"""Unit tests for verification gates G1, G4, G5, and G7."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bi_agent.adapters import adomd_adapter
from bi_agent.adapters.adomd_adapter import DaxQueryResult
from bi_agent.config import ToolchainConfig, find_repo_root
from bi_agent.gates import run_gate_g1, run_gate_g4, run_gate_g5, run_gate_g7
from bi_agent.schemas import load_ground_truth


def test_gate_g1_passes_on_req001() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    res = run_gate_g1(repo_root / "requirements" / "REQ-001-regional-sales-margin.yaml", repo_root)
    assert res.status == "PASS"
    assert res.metrics["fact_count"] == 2
    assert res.metrics["dimension_count"] == 5


def test_gate_g4_dax_reconciliation_pass_and_drift_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt = load_ground_truth(gt_path)

    def _mock_exec(
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
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[SalesRows]": gt.overall.sales_row_count,
                    "[BudgetRows]": gt.overall.budget_row_count,
                    "[DistinctOrders]": gt.overall.distinct_orders,
                    "[TotalRevenue]": gt.overall.total_revenue,
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

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_exec)
    pass_res = run_gate_g4(
        gt_path, ToolchainConfig(), repo_root, port=55555, database="DB", skip_benchmark=True
    )
    assert pass_res.status == "PASS"

    def _mock_drift(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
    ) -> DaxQueryResult:
        res = _mock_exec(port, dax_query, config, repo, database, roles, effective_user_name)
        if res.rows and "[TotalRevenue]" in res.rows[0] and "DimDate[Year]" not in res.rows[0]:
            res.rows[0]["[TotalRevenue]"] = gt.overall.total_revenue + 5.00
        return res

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_drift)
    fail_res = run_gate_g4(
        gt_path, ToolchainConfig(), repo_root, port=55555, database="DB", skip_benchmark=True
    )
    assert fail_res.status == "FAIL"
    assert any(f.code == "G4_METRIC_MISMATCH" for f in fail_res.findings)


def test_gate_g5_rls_fail_closed_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req_path = repo_root / "requirements" / "REQ-001-regional-sales-margin.yaml"
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt = load_ground_truth(gt_path)

    def _mock_rls(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
    ) -> DaxQueryResult:
        if roles == "Region_NA":
            exp = gt.by_region["NA"]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[RowCount]": exp.row_count,
                        "[TotalRevenue]": exp.total_revenue,
                        "[Regions]": "NA",
                    }
                ],
                row_count=1,
            )
        if roles == "Region_EMEA":
            exp = gt.by_region["EMEA"]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[RowCount]": exp.row_count,
                        "[TotalRevenue]": exp.total_revenue,
                        "[Regions]": "EMEA",
                    }
                ],
                row_count=1,
            )
        exp_rep = gt.by_sales_rep_upn[effective_user_name]
        return DaxQueryResult(
            ok=True,
            rows=[{"[RowCount]": exp_rep.row_count, "[TotalRevenue]": exp_rep.total_revenue}],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_rls)
    pass_res = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB"
    )
    assert pass_res.status == "PASS"
    assert pass_res.metrics["scenarios_tested"] == 5

    # Simulate fail-open leak for unauthorized user
    def _mock_leak(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
    ) -> DaxQueryResult:
        if effective_user_name == "unauthorized.user@contoso.com":
            return DaxQueryResult(
                ok=True, rows=[{"[RowCount]": 12, "[TotalRevenue]": 100.0}], row_count=1
            )
        return _mock_rls(port, dax_query, config, repo, database, roles, effective_user_name)

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_leak)
    fail_res = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB"
    )
    assert fail_res.status == "FAIL"
    assert any(f.code == "G5_FAIL_OPEN_LEAK" for f in fail_res.findings)


def test_gate_g7_reports_branch_and_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=str(repo), check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@local.dev"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: init"], cwd=str(repo), check=True, capture_output=True
    )

    res = run_gate_g7(repo)
    assert res.status == "PASS"
    assert res.metrics["branch"] == "main"
    assert res.metrics["staged_tree_sha"]
