"""Unit tests proving zero domain hardcoding, non-retail E2E gates, and multi-project hooks."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bi_agent.adapters import adomd_adapter, git_adapter, pbir_adapter, tabular_editor_adapter
from bi_agent.adapters.adomd_adapter import DaxQueryResult
from bi_agent.adapters.pbir_adapter import PbirValidateResult
from bi_agent.adapters.tabular_editor_adapter import BpaResult, TmdlCheckResult
from bi_agent.config import ToolchainConfig, find_repo_root
from bi_agent.gates import (
    run_gate_g1,
    run_gate_g4,
    run_gate_g5,
    run_gate_g5_offline,
    run_gate_g6,
)
from bi_agent.gates.runner import run_all_gates
from bi_agent.hooks.pre_tool_guard import evaluate_pre_tool_use
from bi_agent.schemas.evidence import GateResult, VerificationEvidenceBundle

FORBIDDEN_DOMAIN_TOKENS = (
    "EnterpriseSales",
    "FactSales",
    "FactBudget",
    "DimStore",
    "DimProduct",
    "DimCustomer",
    "DimSalesRep",
    "Region_NA",
    "Region_EMEA",
    "Region_APAC",
    "Dynamic_SalesRep",
    "contoso.com",
    "SalesOrderID",
    "Cloud Hardware",
    "ActiveCustomers",
    "NoActualsBudget",
    "REQ-001-regional-sales-margin",
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _write_health_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    data_dir = tmp_path / "data" / "health"
    data_dir.mkdir(parents=True)
    for csv_name in (
        "FactClaims.csv",
        "FactCapitation.csv",
        "DimCalendar.csv",
        "DimClinic.csv",
        "DimProvider.csv",
    ):
        (data_dir / csv_name).write_text("id\n1\n", encoding="utf-8")

    gt_path = data_dir / "expected.json"
    gt_payload = {
        "overall": {
            "claims_count": 1200,
            "capitation_rows": 36,
            "gross_billed": 500000.0,
            "adjustments": 50000.0,
            "net_paid": 450000.0,
            "capitation_budget": 460000.0,
        },
        "by_clinic": {
            "West": {
                "claims_count": 700,
                "net_paid": 260000.0,
                "capitation_budget": 270000.0,
            },
            "East": {
                "claims_count": 500,
                "net_paid": 190000.0,
                "capitation_budget": 190000.0,
            },
        },
        "by_provider": {
            "dr.patel@health.org": {
                "claims_count": 320,
                "capitation_rows": 0,
                "net_paid": 125000.0,
            },
            "unmapped@health.org": {
                "claims_count": 0,
                "capitation_rows": 0,
                "net_paid": 0.0,
            },
        },
        "guards": {
            "empty_status": "No Claims",
        },
    }
    gt_path.write_text(json.dumps(gt_payload, indent=2), encoding="utf-8")

    report_dir = tmp_path / "clients" / "health" / "HealthCare.Report"
    pages_dir = report_dir / "definition" / "pages"
    for pid, title, n_vis in (
        ("p_exec", "Executive Summary", 2),
        ("p_clinic", "Clinic Utilization", 1),
    ):
        pdir = pages_dir / pid
        pdir.mkdir(parents=True)
        (pdir / "page.json").write_text(
            json.dumps({"name": pid, "displayName": title}), encoding="utf-8"
        )
        for idx in range(n_vis):
            vdir = pdir / "visuals" / f"vis{idx + 1}"
            vdir.mkdir(parents=True)
            (vdir / "visual.json").write_text("{}", encoding="utf-8")
    (pages_dir / "pages.json").write_text(
        json.dumps({"pageOrder": ["p_exec", "p_clinic"], "activePageName": "p_exec"}),
        encoding="utf-8",
    )

    req_path = tmp_path / "REQ-HEALTH.yaml"
    req_path.write_text(
        """id: REQ-HEALTH
title: Healthcare Claims & Capitation Analytics
owner: Clinical Finance
status: approved
target_pbip: clients/health
project:
  pbip_path: clients/health
  report_path: clients/health/HealthCare.Report
  ground_truth_path: data/health/expected.json
  desktop_report_name: HealthCare
star_schema:
  facts:
    - name: FactClaims
      grain: Claim line
      source: data/health/FactClaims.csv
    - name: FactCapitation
      grain: Clinic month
      source: data/health/FactCapitation.csv
  dimensions:
    - name: DimCalendar
      key: DateKey
      mark_as_date_table: Date
      source: data/health/DimCalendar.csv
    - name: DimClinic
      key: ClinicKey
      source: data/health/DimClinic.csv
    - name: DimProvider
      key: ProviderKey
      source: data/health/DimProvider.csv
  relationships:
    - "FactClaims[DateKey] -> DimCalendar[DateKey] (*:1, Single)"
    - "FactClaims[ClinicKey] -> DimClinic[ClinicKey] (*:1, Single)"
    - "FactClaims[ProviderKey] -> DimProvider[ProviderKey] (*:1, Single)"
    - "FactCapitation[DateKey] -> DimCalendar[DateKey] (*:1, Single)"
    - "FactCapitation[ClinicKey] -> DimClinic[ClinicKey] (*:1, Single)"
measures:
  display_table: _Metrics
  kpis:
    - name: Gross Billed
      folder: "01 Claims"
      format: "$#,##0.00"
    - name: Adjustments
      folder: "01 Claims"
      format: "$#,##0.00"
    - name: Net Paid
      folder: "01 Claims"
      format: "$#,##0.00"
    - name: Capitation Budget
      folder: "02 Capitation"
      format: "$#,##0.00"
rls_roles:
  - name: Clinic_West
    type: static
    table: DimClinic
    filter: "'DimClinic'[Region] = \\"West\\""
    expected_fail_closed: true
  - name: Dynamic_Provider
    type: dynamic
    expected_fail_closed: true
    table_permissions:
      - table: DimProvider
        filter: "'DimProvider'[ProviderEmail] = USERPRINCIPALNAME()"
      - table: FactCapitation
        filter: "FALSE ()"
    test_principals:
      - dr.patel@health.org
      - unmapped@health.org
report_pages:
  - slug: Executive_Summary
    intent: Top-level claims KPIs and capitation spend.
  - slug: Clinic_Utilization
    intent: Clinic utilization and provider drilldown.
verification:
  dax_checks:
    scalar_queries:
      - name: overall
        scope: overall
        dax: |
          EVALUATE ROW(
              "ClaimsCount", COUNTROWS('FactClaims'),
              "GrossBilled", [Gross Billed],
              "Adjustments", [Adjustments],
              "NetPaid", [Net Paid],
              "CapitationBudget", [Capitation Budget]
          )
        assertions:
          - alias: ClaimsCount
            expected_path: overall.claims_count
            tolerance: 0.0
          - alias: GrossBilled
            expected_path: overall.gross_billed
          - alias: Adjustments
            expected_path: overall.adjustments
          - alias: NetPaid
            expected_path: overall.net_paid
          - alias: CapitationBudget
            expected_path: overall.capitation_budget
    grouped_queries:
      - name: by_clinic
        scope_prefix: clinic
        group_column: Region
        group_aliases: ["DimClinic[Region]"]
        ground_truth_map_path: by_clinic
        dax: |
          EVALUATE SUMMARIZECOLUMNS(
              'DimClinic'[Region],
              "ClaimsCount", COUNTROWS('FactClaims'),
              "NetPaid", [Net Paid],
              "CapitationBudget", [Capitation Budget]
          )
        metrics:
          - alias: ClaimsCount
            expected_field: claims_count
            tolerance: 0.0
          - alias: NetPaid
            expected_field: net_paid
          - alias: CapitationBudget
            expected_field: capitation_budget
    accounting_identities:
      - name: NetPaidIdentity
        lhs_alias: NetPaid
        rhs_terms:
          - alias: GrossBilled
            sign: 1
          - alias: Adjustments
            sign: -1
    cross_grain_blank_checks:
      - name: cross_grain_capitation
        scope: cross_grain
        dax: |
          EVALUATE ROW(
              "CapByProvider",
              CALCULATE([Capitation Budget], 'DimProvider'[Specialty] = "Cardiology")
          )
        expected_blank_aliases:
          - CapByProvider
    empty_context_guards:
      - name: empty_claims_guard
        scope: no_actuals
        dax: |
          EVALUATE ROW(
              "EmptyStatus",
              CALCULATE([Claims Status], 'FactClaims'[ClaimID] = "__NONE__")
          )
        string_assertions:
          - alias: EmptyStatus
            expected_path: guards.empty_status
            expected_value: "No Claims"
    future_period_check:
      date_table_column: "'DimCalendar'[DateKey]"
      actuals_date_column: "'FactClaims'[DateKey]"
      must_be_blank_measures:
        FutureNetPaidYTD: "[Net Paid YTD]"
      budget_measure: "[Capitation Budget]"
      status_measure: "[Claims Status]"
      expected_future_status: "No Claims"
      require_active: true
  rls_checks:
    static_checks:
      - role: Clinic_West
        scope_column: "'DimClinic'[Region]"
        expected_scope_value: West
        ground_truth_entry_path: by_clinic.West
        fact_row_count_fields:
          FactClaims: claims_count
        kpi_assertions:
          - alias: NetPaid
            dax_expression: "COALESCE([Net Paid], 0)"
            expected_field: net_paid
    dynamic_checks:
      - role: Dynamic_Provider
        ground_truth_map_path: by_provider
        fact_row_count_fields:
          FactClaims: claims_count
          FactCapitation: capitation_rows
        kpi_assertions:
          - alias: NetPaid
            dax_expression: "COALESCE([Net Paid], 0)"
            expected_field: net_paid
  report_checks:
    expected_pages:
      - "Executive Summary"
      - "Clinic Utilization"
    min_visuals_per_page: 1
    require_screenshot_count_match: true
""",
        encoding="utf-8",
    )
    return req_path, gt_path, report_dir


def test_core_harness_has_zero_domain_hardcoding() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    files_to_scan = [
        *sorted((repo_root / "src" / "bi_agent").rglob("*.py")),
        repo_root / "workflows" / "bi_delivery" / "workflow.py",
        repo_root / "workflows" / "bi_delivery" / "workflow.json",
    ]
    violations: list[str] = []
    for path in files_to_scan:
        content = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_DOMAIN_TOKENS:
            if token in content:
                violations.append(f"{path.relative_to(repo_root)} contains '{token}'")
    assert not violations, "Domain hardcoding detected:\n" + "\n".join(violations)


def test_non_retail_domain_g1_g4_g5_g6_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    req_path, gt_path, report_dir = _write_health_fixture(tmp_path)

    # 1. Gate G1 passes and rejects missing deny or unknown verification role
    g1_ok = run_gate_g1(req_path, tmp_path)
    assert g1_ok.status == "PASS"
    assert g1_ok.metrics["measure_display_table"] == "_Metrics"
    assert g1_ok.metrics["desktop_report_name"] == "HealthCare"

    bad_req_text = req_path.read_text(encoding="utf-8").replace(
        '      - table: FactCapitation\n        filter: "FALSE ()"\n', ""
    )
    bad_req = tmp_path / "REQ-HEALTH-BAD.yaml"
    bad_req.write_text(
        bad_req_text.replace("role: Clinic_West", "role: Ghost_Role"), encoding="utf-8"
    )
    g1_fail = run_gate_g1(bad_req, tmp_path)
    assert g1_fail.status == "FAIL"
    assert any("FactCapitation" in f.message for f in g1_fail.findings)
    assert any("Ghost_Role" in f.message for f in g1_fail.findings)

    # 2. Gate G4 executes declarative healthcare queries including active future_period_check
    def _mock_health_dax(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if "SUMMARIZECOLUMNS" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "DimClinic[Region]": "West",
                        "[ClaimsCount]": 700,
                        "[NetPaid]": 260000.0,
                        "[CapitationBudget]": 270000.0,
                    },
                    {
                        "DimClinic[Region]": "East",
                        "[ClaimsCount]": 500,
                        "[NetPaid]": 190000.0,
                        "[CapitationBudget]": 190000.0,
                    },
                ],
                row_count=2,
            )
        if "CapByProvider" in dax_query:
            return DaxQueryResult(ok=True, rows=[{"[CapByProvider]": None}], row_count=1)
        if "EmptyStatus" in dax_query:
            return DaxQueryResult(ok=True, rows=[{"[EmptyStatus]": "No Claims"}], row_count=1)
        if "MaxDateKey" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[{"[MaxDateKey]": 20271231, "[MaxActualsDateKey]": 20261231}],
                row_count=1,
            )
        if "FutureNetPaidYTD" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[FutureNetPaidYTD]": None,
                        "[FutureBudget]": 25000.0,
                        "[FutureStatus]": "No Claims",
                    }
                ],
                row_count=1,
            )
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[ClaimsCount]": 1200,
                    "[GrossBilled]": 500000.0,
                    "[Adjustments]": 50000.0,
                    "[NetPaid]": 450000.0,
                    "[CapitationBudget]": 460000.0,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_health_dax)
    g4_ok = run_gate_g4(
        gt_path,
        ToolchainConfig(),
        tmp_path,
        port=55555,
        database="HealthDB",
        skip_benchmark=True,
        requirement_path=req_path,
    )
    assert g4_ok.status == "PASS"
    assert g4_ok.metrics["future_period_check"]["status"] == "PASS"

    def _mock_health_fail(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        res = _mock_health_dax(
            port, dax_query, config, repo, database, roles, effective_user_name, custom_data
        )
        if "ClaimsCount" in dax_query and "SUMMARIZECOLUMNS" not in dax_query:
            res.rows[0]["[NetPaid]"] = 450500.0
        if "CapByProvider" in dax_query:
            res.rows[0]["[CapByProvider]"] = 1234.0
        if "EmptyStatus" in dax_query:
            res.rows[0]["[EmptyStatus]"] = "Over Budget"
        if "FutureNetPaidYTD" in dax_query:
            res.rows[0]["[FutureNetPaidYTD]"] = 999.0
        return res

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_health_fail)
    g4_fail = run_gate_g4(
        gt_path,
        ToolchainConfig(),
        tmp_path,
        port=55555,
        database="HealthDB",
        skip_benchmark=True,
        requirement_path=req_path,
    )
    assert g4_fail.status == "FAIL"
    assert {
        "G4_METRIC_MISMATCH",
        "G4_ACCOUNTING_IDENTITY_MISMATCH",
        "G4_CROSS_GRAIN_BUDGET_LEAK",
        "G4_NO_ACTUALS_STATUS_MISMATCH",
        "G4_FUTURE_PERIOD_NOT_BLANK",
    }.issubset({f.code for f in g4_fail.findings})

    # 3. Gate G5 verifies multi-fact healthcare RLS and dynamic Markdown matrix rendering
    def _mock_health_rls(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if roles == "Clinic_West":
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactClaims]": 700,
                        "[Rows_FactCapitation]": 18,
                        "[ScopeValues]": "West",
                        "[NetPaid]": 260000.0,
                    }
                ],
                row_count=1,
            )
        if effective_user_name == "dr.patel@health.org":
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactClaims]": 320,
                        "[Rows_FactCapitation]": 0,
                        "[NetPaid]": 125000.0,
                    }
                ],
                row_count=1,
            )
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[Rows_FactClaims]": 0,
                    "[Rows_FactCapitation]": 0,
                    "[NetPaid]": 0.0,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_health_rls)
    g5_ok = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), tmp_path, port=55555, database="HealthDB"
    )
    assert g5_ok.status == "PASS"
    assert g5_ok.metrics["fact_tables"] == ["FactClaims", "FactCapitation"]
    assert g5_ok.metrics["scenarios_tested"] == 3

    md_path = VerificationEvidenceBundle(
        req_id="REQ-HEALTH", overall_status="PASS", gates=[g5_ok]
    ).write_markdown(tmp_path / "health-report.md", repo_root=tmp_path)
    md_text = md_path.read_text(encoding="utf-8")
    assert "| Role | Principal | FactClaims Rows | FactCapitation Rows | Status |" in md_text
    assert "| Clinic_West | (static) | 700 | 18 | `PASS` |" in md_text

    def _mock_health_rls_leak(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if roles == "Clinic_West":
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactClaims]": 700,
                        "[Rows_FactCapitation]": 18,
                        "[ScopeValues]": "West,East",
                        "[NetPaid]": 260000.0,
                    }
                ],
                row_count=1,
            )
        if effective_user_name == "dr.patel@health.org":
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactClaims]": 320,
                        "[Rows_FactCapitation]": 36,
                        "[NetPaid]": 125000.0,
                    }
                ],
                row_count=1,
            )
        return DaxQueryResult(
            ok=True,
            rows=[{"[Rows_FactClaims]": 15, "[Rows_FactCapitation]": 0, "[NetPaid]": 100.0}],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_health_rls_leak)
    g5_fail = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), tmp_path, port=55555, database="HealthDB"
    )
    assert g5_fail.status == "FAIL"
    assert {"G5_CROSS_REGION_LEAKAGE", "G5_CROSS_FACT_LEAK", "G5_FAIL_OPEN_LEAK"}.issubset(
        {f.code for f in g5_fail.findings}
    )

    # Empty rls_roles skips cleanly in both offline and live paths
    no_rls_req = tmp_path / "REQ-HEALTH-NO-RLS.yaml"
    no_rls_yaml = req_path.read_text(encoding="utf-8")
    no_rls_yaml = (
        no_rls_yaml.split("rls_roles:", 1)[0]
        + "rls_roles: []\nreport_pages:\n"
        + "  - slug: Executive_Summary\n    intent: Summary\n"
    )
    no_rls_req.write_text(no_rls_yaml, encoding="utf-8")
    assert run_gate_g5_offline(tmp_path / "clients" / "health", no_rls_req).status == "SKIP"
    assert (
        run_gate_g5(
            no_rls_req, gt_path, ToolchainConfig(), tmp_path, port=55555, database="HealthDB"
        ).status
        == "SKIP"
    )

    # 4. Gate G6 verifies contracted pages and visual counts
    monkeypatch.setattr(
        pbir_adapter,
        "validate_report",
        lambda *_a, **_kw: PbirValidateResult(ok=True, exit_code=0),
    )
    g6_ok = run_gate_g6(
        report_dir, ToolchainConfig(), tmp_path, offline=True, requirement_path=req_path
    )
    assert g6_ok.status == "PASS"
    assert g6_ok.metrics["page_count"] == 2

    # Remove visuals on p_clinic and drop p_clinic from pages.json
    for vf in (report_dir / "definition" / "pages" / "p_clinic" / "visuals").rglob("visual.json"):
        vf.unlink()
    g6_low = run_gate_g6(
        report_dir, ToolchainConfig(), tmp_path, offline=True, requirement_path=req_path
    )
    assert g6_low.status == "FAIL"
    assert any(f.code == "G6_PAGE_VISUAL_COUNT_LOW" for f in g6_low.findings)

    (report_dir / "definition" / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["p_exec"], "activePageName": "p_exec"}), encoding="utf-8"
    )
    g6_missing = run_gate_g6(
        report_dir, ToolchainConfig(), tmp_path, offline=True, requirement_path=req_path
    )
    assert g6_missing.status == "FAIL"
    assert any(f.code == "G6_EXPECTED_PAGE_MISSING" for f in g6_missing.findings)


def test_multi_project_paths_hooks_and_port_discovery(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    outside_report_json = "clients/health/HealthCare.Report/definition/pages/pages.json"
    outside_model_tmdl = "clients/health/HealthCare.SemanticModel/definition/model.tmdl"
    assert git_adapter.is_pbi_path(outside_report_json) is True
    assert git_adapter.is_pbi_path(outside_model_tmdl) is True

    hook_pbir = evaluate_pre_tool_use(
        {"toolCall": {"name": "edit_file", "args": {"Path": outside_report_json}}}
    )
    assert hook_pbir["decision"] == "deny"

    repo = tmp_path / "multi_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@local.dev")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: init")

    sm_file = repo / outside_model_tmdl
    sm_file.parent.mkdir(parents=True)
    sm_file.write_text("model Model\n", encoding="utf-8")
    _git(repo, "add", outside_model_tmdl)

    blocked = evaluate_pre_tool_use(
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "git commit -m 'feat: x'"}}},
        repo_root=repo,
    )
    assert blocked["decision"] == "deny"

    tree_sha = git_adapter.staged_tree_sha(repo)
    VerificationEvidenceBundle(
        req_id="REQ-HEALTH",
        git_branch="main",
        git_tree_sha=tree_sha,
        overall_status="PASS",
        gates=[GateResult(gate_id="G1", name="Design", status="PASS")],
    ).write_json(repo / "artifacts" / "runs" / "REQ-HEALTH" / "verification-report.json")
    assert git_adapter.find_matching_evidence(repo, req_id="REQ-HEALTH") is not None

    allowed = evaluate_pre_tool_use(
        {"toolCall": {"name": "run_command", "args": {"CommandLine": "git commit -m 'feat: x'"}}},
        repo_root=repo,
    )
    assert allowed["decision"] == "allow"

    ps_text = (repo_root / "scripts" / "pbid" / "discover_pbid_port.ps1").read_text(
        encoding="utf-8"
    )
    assert "MainWindowTitle" in ps_text
    assert "window_title" in ps_text
    assert "currentFilePath" in ps_text
    assert "reportDir" in ps_text
    assert "No active Power BI Desktop instance matched ReportName" in ps_text


def test_workflows_manifest_v2_and_runner_includes_g0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    manifest = json.loads(
        (repo_root / "workflows" / "bi_delivery" / "workflow.json").read_text(encoding="utf-8")
    )
    props = manifest["args"]["properties"]
    assert props["project_dir"]["default"] == ""
    assert "raw_data_dir" in props
    assert "offline" in props

    monkeypatch.setattr(
        tabular_editor_adapter,
        "check_tmdl_load",
        lambda *_a, **_kw: TmdlCheckResult(ok=True, exit_code=0, table_count=8),
    )
    monkeypatch.setattr(
        tabular_editor_adapter,
        "run_bpa",
        lambda *_a, **_kw: BpaResult(ok=True, exit_code=0, error_count=0, warning_count=0),
    )
    monkeypatch.setattr(
        pbir_adapter,
        "validate_report",
        lambda *_a, **_kw: PbirValidateResult(ok=True, exit_code=0),
    )

    for req_id, req_rel in (
        ("REQ-001", "requirements/REQ-001-regional-sales-margin.yaml"),
        ("REQ-002", "requirements/REQ-002-customer-discount-analytics.yaml"),
    ):
        bundle, _, _ = run_all_gates(
            req_id=req_id,
            requirement_path=repo_root / req_rel,
            project_dir=None,
            ground_truth_path=None,
            config=ToolchainConfig(),
            repo_root=repo_root,
            offline=True,
            output_dir=tmp_path / req_id,
        )
        assert bundle.overall_status == "PASS"
        assert [g.gate_id for g in bundle.gates] == [
            "G0",
            "G1",
            "G2",
            "G3",
            "G4",
            "G5",
            "G6",
            "G7",
        ]
        assert bundle.gates[0].status == "SKIP"
