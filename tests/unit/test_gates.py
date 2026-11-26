"""Unit tests for verification gates G1, G4, G5, and G7."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bi_agent.adapters import adomd_adapter
from bi_agent.adapters.adomd_adapter import DaxQueryResult
from bi_agent.config import ToolchainConfig, find_repo_root
from bi_agent.gates import run_gate_g1, run_gate_g4, run_gate_g5, run_gate_g5_offline, run_gate_g7
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
    assert pass_res.metrics["future_period_check"]["status"] == "SKIP"
    assert any(f.code == "G4_FUTURE_PERIOD_CHECK_SKIPPED" for f in pass_res.findings)

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
    codes = {f.code for f in fail_res.findings}
    assert {"G4_METRIC_MISMATCH", "G4_ACCOUNTING_IDENTITY_MISMATCH"}.issubset(codes)

    def _mock_guard_fail(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
    ) -> DaxQueryResult:
        res = _mock_exec(port, dax_query, config, repo, database, roles, effective_user_name)
        if "BudgetByProduct" in dax_query:
            res.rows[0]["[BudgetByProduct]"] = 49390714.25
        if "NoActualsBudget" in dax_query:
            res.rows[0]["[NoActualsStatus]"] = "Below Target"
        return res

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_guard_fail)
    guard_fail_res = run_gate_g4(
        gt_path, ToolchainConfig(), repo_root, port=55555, database="DB", skip_benchmark=True
    )
    assert guard_fail_res.status == "FAIL"
    guard_codes = {f.code for f in guard_fail_res.findings}
    assert {"G4_CROSS_GRAIN_BUDGET_LEAK", "G4_NO_ACTUALS_STATUS_MISMATCH"}.issubset(guard_codes)


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
            rows=[
                {
                    "[RowCount]": exp_rep.row_count,
                    "[BudgetRowCount]": 0,
                    "[TotalRevenue]": exp_rep.total_revenue,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_rls)
    pass_res = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB"
    )
    assert pass_res.status == "PASS"
    assert pass_res.metrics["scenarios_tested"] == 5

    # Simulate fail-open leak for unauthorized user (FactSales + FactBudget)
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
                ok=True,
                rows=[{"[RowCount]": 12, "[BudgetRowCount]": 648, "[TotalRevenue]": 100.0}],
                row_count=1,
            )
        return _mock_rls(port, dax_query, config, repo, database, roles, effective_user_name)

    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_leak)
    fail_res = run_gate_g5(
        req_path, gt_path, ToolchainConfig(), repo_root, port=55555, database="DB"
    )
    assert fail_res.status == "FAIL"
    leak_codes = {f.code for f in fail_res.findings}
    assert {"G5_FAIL_OPEN_LEAK", "G5_CROSS_FACT_LEAK"}.issubset(leak_codes)


def test_gate_g5_offline_passes_on_enterprise_sales() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    res = run_gate_g5_offline(
        repo_root / "pbi" / "EnterpriseSales",
        repo_root / "requirements" / "REQ-002-customer-discount-analytics.yaml",
    )
    assert res.status == "SKIP"
    assert res.metrics["roles_audited"] == 4


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
