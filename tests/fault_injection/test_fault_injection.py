"""Fail-closed fault-injection suite verifying hooks and Gates G1..G7 catch injected defects."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from bi_agent.adapters import adomd_adapter, dax_studio_adapter, git_adapter
from bi_agent.adapters.adomd_adapter import DaxQueryResult
from bi_agent.adapters.dax_studio_adapter import BenchmarkResult
from bi_agent.config import ToolchainConfig, find_repo_root
from bi_agent.gates import (
    run_gate_g1,
    run_gate_g2,
    run_gate_g3,
    run_gate_g4,
    run_gate_g5,
    run_gate_g5_offline,
    run_gate_g6,
)
from bi_agent.hooks.pre_tool_guard import evaluate_pre_tool_use
from bi_agent.schemas import load_ground_truth
from bi_agent.schemas.evidence import GateResult, VerificationEvidenceBundle


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _copy_pbip(repo_root: Path, tmp_path: Path) -> Path:
    dest = tmp_path / "EnterpriseSales"
    shutil.copytree(
        repo_root / "pbi" / "EnterpriseSales",
        dest,
        ignore=shutil.ignore_patterns(".pbi", "cache.abf", "localSettings.json"),
    )
    return dest


def test_fi01_hook_blocks_pbir_json_direct_and_shell_writes() -> None:
    target = "pbi/EnterpriseSales/EnterpriseSales.Report/definition/pages/pages.json"
    for tool_name in ("write_file", "edit_file"):
        res = evaluate_pre_tool_use({"toolCall": {"name": tool_name, "args": {"Path": target}}})
        assert res["decision"] == "deny"

    shell_res = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "run_command",
                "args": {
                    "CommandLine": (
                        "Set-Content -Path "
                        "pbi/EnterpriseSales/EnterpriseSales.Report/definition/report.json "
                        "-Value '{}'"
                    )
                },
            }
        }
    )
    assert shell_res["decision"] == "deny"


def test_fi02_g1_rejects_star_schema_and_rls_antipatterns(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    bad_req = tmp_path / "REQ-FAULT.yaml"
    bad_req.write_text(
        """id: REQ-FAULT
title: Faulty Star Schema
owner: QA
status: draft
target_pbip: pbi/EnterpriseSales
star_schema:
  facts:
    - name: FactSales
      grain: Order line
      source: data/raw/FactSales.csv
  dimensions:
    - name: DimCustomer
      key: CustomerKey
      source: data/raw/DimCustomer.csv
  relationships:
    - "FactSales[CustomerKey] -> DimCustomer[CustomerKey] (*:*, Both)"
measures:
  display_table: _Measures
  kpis:
    - name: Total Revenue
      folder: "01 Revenue"
      format: "$#,##0.00"
rls_roles:
  - name: Broken_Dynamic
    type: dynamic
    table: DimCustomer
    filter: "'DimCustomer'[Region] = \\"NA\\""
    expected_fail_closed: false
""",
        encoding="utf-8",
    )
    res = run_gate_g1(bad_req, repo_root)
    assert res.status == "FAIL"
    assert len(res.findings) >= 4
    assert all(f.code == "G1_DESIGN_VIOLATION" for f in res.findings)


def test_fi03_g2_rejects_broken_pbip_page_order(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    proj = _copy_pbip(repo_root, tmp_path)
    pages_json = proj / "EnterpriseSales.Report" / "definition" / "pages" / "pages.json"
    payload = json.loads(pages_json.read_text(encoding="utf-8"))
    payload["pageOrder"].append("missing_ghost_page")
    pages_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    res = run_gate_g2(proj, ToolchainConfig(), repo_root)
    assert res.status == "FAIL"
    assert any(f.severity == "error" for f in res.findings)


def test_fi04_g3_real_te2_bpa_catches_injected_defects(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    proj = _copy_pbip(repo_root, tmp_path)
    def_dir = proj / "EnterpriseSales.SemanticModel" / "definition"

    rel_path = def_dir / "relationships.tmdl"
    rel_text = rel_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "relationship FactSales_StoreKey_DimStore" in rel_text
    rel_text = rel_text.replace(
        "relationship FactSales_StoreKey_DimStore\n"
        "\tfromColumn: FactSales.StoreKey\n"
        "\ttoColumn: DimStore.StoreKey",
        "relationship FactSales_StoreKey_DimStore\n"
        "\tcrossFilteringBehavior: bothDirections\n"
        "\tfromColumn: FactSales.StoreKey\n"
        "\ttoColumn: DimStore.StoreKey",
    )
    rel_path.write_text(rel_text, encoding="utf-8")

    fact_path = def_dir / "tables" / "FactSales.tmdl"
    fact_text = fact_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "\tcolumn StoreKey\n\t\tdataType: int64\n\t\tisHidden\n" in fact_text
    fact_text = fact_text.replace(
        "\tcolumn StoreKey\n\t\tdataType: int64\n\t\tisHidden\n",
        "\tcolumn StoreKey\n\t\tdataType: int64\n",
    )
    fact_path.write_text(fact_text, encoding="utf-8")

    meas_path = def_dir / "tables" / "_Measures.tmdl"
    meas_text = meas_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "\tcolumn _Placeholder" in meas_text
    bad_measure = (
        "\tmeasure 'Fault Ratio' = IFERROR ( [Gross Margin] / [Total Revenue], 0 )\n"
        "\t\tlineageTag: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n\n"
    )
    meas_text = meas_text.replace("\tcolumn _Placeholder", f"{bad_measure}\tcolumn _Placeholder")
    meas_path.write_text(meas_text, encoding="utf-8")

    res = run_gate_g3(proj, ToolchainConfig(), repo_root, offline=True)
    assert res.status == "FAIL"
    codes = {f.code for f in res.findings}
    assert {
        "ERR_DAX_DIVISION_OPERATOR",
        "ERR_MEASURE_NO_FORMAT_STRING",
        "ERR_AVOID_BIDIR_RELATIONSHIPS",
        "ERR_HIDE_FK_COLUMNS",
        "DAX_AVOID_IFERROR",
    }.issubset(codes)


def test_fi05_g4_catches_dax_drift_and_perf_budget_breach(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt = load_ground_truth(gt_path)

    def _mock_drift(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
    ) -> DaxQueryResult:
        if "SUMMARIZECOLUMNS(\n    'DimDate'[Year]" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "DimDate[Year]": int(yr),
                        "[TotalRevenue]": item.total_revenue,
                        "[GrossMargin]": item.gross_margin,
                        "[BudgetRevenue]": item.total_budget,
                        "[RevenuePY]": item.revenue_py,
                        "[RevenueYoY]": item.revenue_yoy,
                    }
                    for yr, item in gt.by_year.items()
                ],
                row_count=len(gt.by_year),
            )
        if "SUMMARIZECOLUMNS(\n    'DimStore'[Region]" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "DimStore[Region]": reg,
                        "[RowCount]": item.row_count,
                        "[TotalRevenue]": item.total_revenue,
                        "[GrossMargin]": item.gross_margin,
                        "[BudgetRevenue]": item.total_budget,
                    }
                    for reg, item in gt.by_region.items()
                ],
                row_count=len(gt.by_region),
            )
        if "ActiveCustomers" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[GrossRevenue]": gt.overall.gross_revenue,
                        "[DiscountRatePct]": gt.overall.discount_rate_pct,
                        "[ActiveCustomers]": gt.overall.active_customers,
                        "[RevenuePerCustomer]": gt.overall.revenue_per_customer,
                    }
                ],
                row_count=1,
            )
        if "BudgetByProduct" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[BudgetByProduct]": None,
                        "[BudgetByCustomer]": None,
                        "[BudgetBySalesRep]": None,
                        "[VarianceByProduct]": None,
                    }
                ],
                row_count=1,
            )
        if "NoActualsBudget" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[NoActualsBudget]": gt.overall.total_budget,
                        "[NoActualsVariance]": None,
                        "[NoActualsStatus]": "No Actuals",
                        "[NoBudgetStatus]": "No Budget",
                    }
                ],
                row_count=1,
            )
        if "MaxDateKey" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[{"[MaxDateKey]": 20261231, "[MaxSalesDateKey]": 20261231}],
                row_count=1,
            )
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[SalesRows]": gt.overall.sales_row_count,
                    "[BudgetRows]": gt.overall.budget_row_count,
                    "[DistinctOrders]": gt.overall.distinct_orders,
                    "[TotalRevenue]": gt.overall.total_revenue + 5.00,
                    "[TotalCOGS]": gt.overall.total_cogs,
                    "[GrossMargin]": gt.overall.gross_margin,
                    "[GrossMarginPct]": gt.overall.gross_margin_pct,
                    "[TotalUnits]": gt.overall.total_units,
                    "[TotalDiscount]": gt.overall.total_discount,
                    "[AverageOrderValue]": gt.overall.average_order_value,
                    "[BudgetRevenue]": gt.overall.total_budget,
                    "[BudgetVariance]": gt.overall.budget_variance,
                    "[BudgetVariancePct]": gt.overall.budget_variance_pct,
                }
            ],
            row_count=1,
        )

    def _mock_slow_bench(
        server: str,
        database: str,
        dax_file: Path,
        output_csv: Path,
        config: ToolchainConfig,
        cold_runs: int = 2,
        warm_runs: int = 3,
    ) -> BenchmarkResult:
        return BenchmarkResult(ok=True, cold_avg_ms=2500.0, warm_avg_ms=120.0)

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_drift)
    monkeypatch.setattr(dax_studio_adapter, "run_benchmark", _mock_slow_bench)

    res = run_gate_g4(
        gt_path,
        ToolchainConfig(),
        repo_root,
        port=55555,
        database="DB",
        skip_benchmark=False,
        output_dir=tmp_path,
    )
    assert res.status == "FAIL"
    codes = {f.code for f in res.findings}
    assert {"G4_METRIC_MISMATCH", "G4_COLD_BUDGET_EXCEEDED"}.issubset(codes)


def test_fi06_g5_catches_cross_region_leak_and_fail_open_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req_path = repo_root / "requirements" / "REQ-002-customer-discount-analytics.yaml"
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt = load_ground_truth(gt_path)

    def _mock_rls_breach(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if roles == "Region_APAC":
            exp = gt.by_region["APAC"]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[RowCount]": exp.row_count,
                        "[TotalRevenue]": exp.total_revenue,
                        "[Regions]": "APAC,EMEA",
                    }
                ],
                row_count=1,
            )
        if roles in ("Region_NA", "Region_EMEA"):
            reg = roles.replace("Region_", "")
            exp = gt.by_region[reg]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[RowCount]": exp.row_count,
                        "[TotalRevenue]": exp.total_revenue,
                        "[Regions]": reg,
                    }
                ],
                row_count=1,
            )
        if effective_user_name == "unauthorized.user@contoso.com":
            return DaxQueryResult(
                ok=True,
                rows=[{"[RowCount]": 15, "[BudgetRowCount]": 648, "[TotalRevenue]": 999.0}],
                row_count=1,
            )
        exp_rep = gt.by_sales_rep_upn[effective_user_name]
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[RowCount]": exp_rep.row_count,
                    "[BudgetRowCount]": 0,
                    "[TotalRevenue]": exp_rep.total_revenue,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_rls_breach)
    res = run_gate_g5(req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB")
    assert res.status == "FAIL"
    codes = {f.code for f in res.findings}
    assert {"G5_CROSS_REGION_LEAKAGE", "G5_FAIL_OPEN_LEAK"}.issubset(codes)


def test_fi07_g6_real_pbir_validate_catches_corrupt_visual(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    proj = _copy_pbip(repo_root, tmp_path)
    report_dir = proj / "EnterpriseSales.Report"
    visual_files = sorted(report_dir.glob("definition/pages/*/visuals/*/visual.json"))
    assert visual_files
    visual_files[0].write_text("{ invalid json !!!", encoding="utf-8")

    res = run_gate_g6(proj, ToolchainConfig(), repo_root, offline=True)
    assert res.status == "FAIL"
    assert any(f.code == "G6_PBIR_VALIDATE_FAILED" for f in res.findings)


def test_fi08_g7_blocks_stale_evidence_sha_and_protected_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@local.dev")
    (repo / "README.md").write_text("# Init\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: init")

    pbip_file = repo / "pbi" / "Demo" / "Demo.pbip"
    pbip_file.parent.mkdir(parents=True)
    pbip_file.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "pbi/Demo/Demo.pbip")

    old_tree = git_adapter.staged_tree_sha(repo)
    ev_path = repo / "artifacts" / "runs" / "REQ-002" / "verification-report.json"
    VerificationEvidenceBundle(
        req_id="REQ-002",
        git_branch="main",
        git_tree_sha=old_tree,
        overall_status="PASS",
        gates=[GateResult(gate_id="G1", name="Design", status="PASS")],
    ).write_json(ev_path)

    # Blocked on main without --allow-main
    blocked_main = git_adapter.commit_verified(
        repo, "feat(REQ-002): add demo", evidence_path=ev_path, allow_main=False
    )
    assert blocked_main.returncode != 0
    assert "protected branch" in blocked_main.stderr.lower()

    # Mutate & re-stage pbi/ file so tree SHA drifts -> stale evidence must fail
    pbip_file.write_text('{"version":"2"}\n', encoding="utf-8")
    _git(repo, "add", "pbi/Demo/Demo.pbip")

    stale = git_adapter.commit_verified(
        repo, "feat(REQ-002): add demo", evidence_path=ev_path, allow_main=True
    )
    assert stale.returncode != 0
    assert "Stale evidence" in stale.stderr

    hook_deny = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat(REQ-002): bypass'"},
            }
        },
        repo_root=repo,
    )
    assert hook_deny["decision"] == "deny"


def test_fi09_rls_cross_fact_budget_leak_caught_offline_and_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req_path = repo_root / "requirements" / "REQ-002-customer-discount-analytics.yaml"
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt = load_ground_truth(gt_path)

    # 1. Offline model gate catches stripped FactBudget deny permission on Dynamic_SalesRep.tmdl
    proj = _copy_pbip(repo_root, tmp_path)
    role_file = (
        proj
        / "EnterpriseSales.SemanticModel"
        / "definition"
        / "roles"
        / "Dynamic_SalesRep.tmdl"
    )
    role_text = role_file.read_text(encoding="utf-8")
    assert "tablePermission FactBudget = FALSE ()" in role_text
    role_file.write_text(
        role_text.replace("\ttablePermission FactBudget = FALSE ()\n", "").replace(
            "tablePermission FactBudget = FALSE ()", ""
        ),
        encoding="utf-8",
    )

    offline_res = run_gate_g5_offline(proj, req_path)
    assert offline_res.status == "FAIL"
    offline_codes = {f.code for f in offline_res.findings}
    assert {"G5_OFFLINE_MISSING_PERMISSION", "G5_OFFLINE_CROSS_FACT_LEAK"}.issubset(
        offline_codes
    )

    # 2. Offline G1 contract gate catches missing FactBudget deny in temporary REQ YAML
    bad_req = tmp_path / "REQ-002-MISSING-BUDGET-DENY.yaml"
    req_yaml = req_path.read_text(encoding="utf-8")
    stripped_yaml = req_yaml.replace(
        '      - table: FactBudget\n        filter: "FALSE ()"\n', ""
    )
    assert stripped_yaml != req_yaml
    bad_req.write_text(stripped_yaml, encoding="utf-8")

    g1_res = run_gate_g1(bad_req, repo_root)
    assert g1_res.status == "FAIL"
    assert any("FactBudget" in f.message for f in g1_res.findings)

    # 3. Live G5 catches FactBudget leak for valid & unauthorized Dynamic_SalesRep principals
    def _mock_budget_leak(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if roles in ("Region_NA", "Region_EMEA", "Region_APAC"):
            reg = roles.replace("Region_", "")
            exp = gt.by_region[reg]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[RowCount]": exp.row_count,
                        "[TotalRevenue]": exp.total_revenue,
                        "[Regions]": reg,
                    }
                ],
                row_count=1,
            )
        exp_rep = gt.by_sales_rep_upn[effective_user_name]
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[RowCount]": exp_rep.row_count,
                    "[BudgetRowCount]": 648,
                    "[TotalRevenue]": exp_rep.total_revenue,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_budget_leak)
    live_res = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB"
    )
    assert live_res.status == "FAIL"
    assert any(f.code == "G5_CROSS_FACT_LEAK" for f in live_res.findings)
