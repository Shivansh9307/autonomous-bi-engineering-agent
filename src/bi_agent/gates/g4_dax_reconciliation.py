"""Gate G4: Live DAX ground-truth reconciliation and performance benchmarking."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from bi_agent.adapters import adomd_adapter, dax_studio_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.test_suite import load_ground_truth

OVERALL_DAX_QUERY = """EVALUATE
ROW(
    "SalesRows", COUNTROWS('FactSales'),
    "BudgetRows", COUNTROWS('FactBudget'),
    "DistinctOrders", DISTINCTCOUNT('FactSales'[SalesOrderID]),
    "TotalRevenue", [Total Revenue],
    "TotalCOGS", [Total COGS],
    "GrossMargin", [Gross Margin],
    "GrossMarginPct", [Gross Margin %],
    "TotalUnits", [Total Units],
    "TotalDiscount", [Total Discount],
    "AverageOrderValue", [Average Order Value],
    "BudgetRevenue", [Budget Revenue],
    "BudgetVariance", [Budget Variance],
    "BudgetVariancePct", [Budget Variance %]
)"""

BY_YEAR_DAX_QUERY = """EVALUATE
SUMMARIZECOLUMNS(
    'DimDate'[Year],
    "TotalRevenue", [Total Revenue],
    "GrossMargin", [Gross Margin],
    "BudgetRevenue", [Budget Revenue],
    "RevenuePY", [Revenue PY],
    "RevenueYoY", [Revenue YoY],
    "RevenueYoYPct", [Revenue YoY %]
)"""

BY_REGION_DAX_QUERY = """EVALUATE
SUMMARIZECOLUMNS(
    'DimStore'[Region],
    "RowCount", COUNTROWS('FactSales'),
    "TotalRevenue", [Total Revenue],
    "GrossMargin", [Gross Margin],
    "GrossMarginPct", [Gross Margin %],
    "BudgetRevenue", [Budget Revenue]
)"""

BENCHMARK_DAX = """EVALUATE
SUMMARIZECOLUMNS(
    'DimStore'[Region],
    'DimDate'[Year],
    "TotalRevenue", [Total Revenue],
    "GrossMarginPct", [Gross Margin %],
    "BudgetVariancePct", [Budget Variance %]
)"""


def _get_field(row: dict[str, Any], *candidates: str) -> Any:
    """Resolve column values across plain or ADOMD-qualified (`[Col]`, `Table[Col]`) keys."""
    normalized: dict[str, Any] = {}
    for key, val in row.items():
        k_str = str(key)
        normalized[k_str.lower()] = val
        if "[" in k_str and k_str.endswith("]"):
            inner = k_str[k_str.index("[") + 1 : -1]
            normalized[inner.lower()] = val
    for cand in candidates:
        c_low = cand.lower()
        if c_low in normalized:
            return normalized[c_low]
    return None


def _compare_numeric(
    scope: str,
    metric_name: str,
    actual_raw: Any,
    expected: float,
    tolerance: float,
    findings: list[GateFinding],
    checks: list[dict[str, Any]],
) -> None:
    if actual_raw is None:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_MISSING_METRIC",
                message=(
                    f"[{scope}] Metric '{metric_name}' returned NULL/missing "
                    f"(expected {expected})."
                ),
            )
        )
        checks.append(
            {
                "scope": scope,
                "metric": metric_name,
                "expected": expected,
                "actual": None,
                "diff": None,
                "status": "FAIL",
            }
        )
        return

    actual = float(actual_raw)
    diff = abs(actual - expected)
    ok = diff <= tolerance
    checks.append(
        {
            "scope": scope,
            "metric": metric_name,
            "expected": expected,
            "actual": actual,
            "diff": round(diff, 6),
            "status": "PASS" if ok else "FAIL",
        }
    )
    if not ok:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_METRIC_MISMATCH",
                message=(
                    f"[{scope}] '{metric_name}' drift: actual={actual}, expected={expected}, "
                    f"diff={diff:.4f} > tolerance={tolerance}"
                ),
            )
        )


def run_gate_g4(
    ground_truth_path: Path,
    config: ToolchainConfig,
    repo_root: Path,
    port: int | None = None,
    database: str = "",
    tolerance: float | None = None,
    skip_benchmark: bool = False,
    output_dir: Path | None = None,
) -> GateResult:
    """Reconcile live DAX measures against ground truth and enforce query latency budgets."""
    start = time.perf_counter()
    tol = tolerance if tolerance is not None else config.gates.dax_numeric_tolerance
    gt = load_ground_truth(ground_truth_path)

    target_port = port
    target_db = database
    if target_port is None:
        disc = adomd_adapter.discover_instance(config, repo_root)
        if not disc.ok or not disc.selected:
            elapsed = int((time.perf_counter() - start) * 1000)
            return GateResult(
                gate_id="G4",
                name="Live DAX Reconciliation & Perf",
                status="FAIL",
                duration_ms=elapsed,
                summary=disc.error or "No active Power BI Desktop instance discovered.",
                findings=[
                    GateFinding(
                        severity="error",
                        code="G4_NO_DESKTOP_INSTANCE",
                        message=disc.error or "No active Power BI Desktop engine found.",
                    )
                ],
            )
        target_port = disc.selected.port
        target_db = target_db or disc.selected.database

    findings: list[GateFinding] = []
    checks: list[dict[str, Any]] = []

    overall_res = adomd_adapter.execute_dax(
        target_port, OVERALL_DAX_QUERY, config, repo_root, database=target_db
    )
    if not overall_res.ok or not overall_res.rows:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_OVERALL_QUERY_FAILED",
                message=overall_res.error or "Overall DAX query returned no rows.",
            )
        )
    else:
        row = overall_res.rows[0]
        overall_pairs = [
            ("SalesRows", float(gt.overall.sales_row_count), 0.0),
            ("BudgetRows", float(gt.overall.budget_row_count), 0.0),
            ("DistinctOrders", float(gt.overall.distinct_orders), 0.0),
            ("TotalRevenue", gt.overall.total_revenue, tol),
            ("TotalCOGS", gt.overall.total_cogs, tol),
            ("GrossMargin", gt.overall.gross_margin, tol),
            ("GrossMarginPct", gt.overall.gross_margin_pct, 0.0005),
            ("TotalUnits", float(gt.overall.total_units), 0.0),
            ("TotalDiscount", gt.overall.total_discount, tol),
            ("AverageOrderValue", gt.overall.average_order_value, tol),
            ("BudgetRevenue", gt.overall.total_budget, tol),
            ("BudgetVariance", gt.overall.budget_variance, tol),
            ("BudgetVariancePct", gt.overall.budget_variance_pct, 0.0005),
        ]
        for field_name, expected_val, field_tol in overall_pairs:
            _compare_numeric(
                "overall",
                field_name,
                _get_field(row, field_name),
                expected_val,
                field_tol,
                findings,
                checks,
            )

    year_res = adomd_adapter.execute_dax(
        target_port, BY_YEAR_DAX_QUERY, config, repo_root, database=target_db
    )
    if not year_res.ok:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_YEAR_QUERY_FAILED",
                message=year_res.error or "By-year DAX query failed.",
            )
        )
    else:
        year_rows = {
            str(_get_field(r, "Year", "DimDate[Year]")): r
            for r in year_res.rows
            if _get_field(r, "Year", "DimDate[Year]") is not None
        }
        for yr, expected_yr in gt.by_year.items():
            actual_yr = year_rows.get(yr)
            if actual_yr is None:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_MISSING_YEAR",
                        message=f"Year '{yr}' missing from SUMMARIZECOLUMNS output.",
                    )
                )
                continue
            _compare_numeric(
                f"year:{yr}",
                "TotalRevenue",
                _get_field(actual_yr, "TotalRevenue"),
                expected_yr.total_revenue,
                tol,
                findings,
                checks,
            )
            _compare_numeric(
                f"year:{yr}",
                "GrossMargin",
                _get_field(actual_yr, "GrossMargin"),
                expected_yr.gross_margin,
                tol,
                findings,
                checks,
            )
            _compare_numeric(
                f"year:{yr}",
                "BudgetRevenue",
                _get_field(actual_yr, "BudgetRevenue"),
                expected_yr.total_budget,
                tol,
                findings,
                checks,
            )
            if expected_yr.revenue_py is not None:
                _compare_numeric(
                    f"year:{yr}",
                    "RevenuePY",
                    _get_field(actual_yr, "RevenuePY"),
                    expected_yr.revenue_py,
                    tol,
                    findings,
                    checks,
                )
            if expected_yr.revenue_yoy is not None:
                _compare_numeric(
                    f"year:{yr}",
                    "RevenueYoY",
                    _get_field(actual_yr, "RevenueYoY"),
                    expected_yr.revenue_yoy,
                    tol,
                    findings,
                    checks,
                )

    region_res = adomd_adapter.execute_dax(
        target_port, BY_REGION_DAX_QUERY, config, repo_root, database=target_db
    )
    if not region_res.ok:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_REGION_QUERY_FAILED",
                message=region_res.error or "By-region DAX query failed.",
            )
        )
    else:
        region_rows = {
            str(_get_field(r, "Region", "DimStore[Region]")): r
            for r in region_res.rows
            if _get_field(r, "Region", "DimStore[Region]") is not None
        }
        for reg, expected_reg in gt.by_region.items():
            actual_reg = region_rows.get(reg)
            if actual_reg is None:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_MISSING_REGION",
                        message=f"Region '{reg}' missing from SUMMARIZECOLUMNS output.",
                    )
                )
                continue
            _compare_numeric(
                f"region:{reg}",
                "RowCount",
                _get_field(actual_reg, "RowCount"),
                float(expected_reg.row_count),
                0.0,
                findings,
                checks,
            )
            _compare_numeric(
                f"region:{reg}",
                "TotalRevenue",
                _get_field(actual_reg, "TotalRevenue"),
                expected_reg.total_revenue,
                tol,
                findings,
                checks,
            )
            _compare_numeric(
                f"region:{reg}",
                "GrossMargin",
                _get_field(actual_reg, "GrossMargin"),
                expected_reg.gross_margin,
                tol,
                findings,
                checks,
            )
            _compare_numeric(
                f"region:{reg}",
                "BudgetRevenue",
                _get_field(actual_reg, "BudgetRevenue"),
                expected_reg.total_budget,
                tol,
                findings,
                checks,
            )

    perf_metrics: dict[str, Any] = {"benchmark_skipped": skip_benchmark}
    artifacts: list[str] = []
    if not skip_benchmark and target_db:
        run_dir = output_dir or (repo_root / "artifacts" / "runs" / "ad-hoc")
        run_dir.mkdir(parents=True, exist_ok=True)
        dax_file = run_dir / "benchmark_query.dax"
        csv_file = run_dir / "benchmark_results.csv"
        dax_file.write_text(BENCHMARK_DAX, encoding="utf-8")
        bench = dax_studio_adapter.run_benchmark(
            f"localhost:{target_port}", target_db, dax_file, csv_file, config
        )
        perf_metrics["cold_avg_ms"] = bench.cold_avg_ms
        perf_metrics["warm_avg_ms"] = bench.warm_avg_ms
        if csv_file.is_file():
            artifacts.append(str(csv_file))
        if not bench.ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_BENCHMARK_FAILED",
                    message=bench.error,
                )
            )
        else:
            if (
                bench.cold_avg_ms is not None
                and bench.cold_avg_ms > config.gates.perf_budget_cold_ms
            ):
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_COLD_BUDGET_EXCEEDED",
                        message=(
                            f"Cold cache avg {bench.cold_avg_ms:.1f}ms > "
                            f"budget {config.gates.perf_budget_cold_ms}ms"
                        ),
                    )
                )
            if (
                bench.warm_avg_ms is not None
                and bench.warm_avg_ms > config.gates.perf_budget_warm_ms
            ):
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_WARM_BUDGET_EXCEEDED",
                        message=(
                            f"Warm cache avg {bench.warm_avg_ms:.1f}ms > "
                            f"budget {config.gates.perf_budget_warm_ms}ms"
                        ),
                    )
                )

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = not any(f.severity == "error" for f in findings)
    return GateResult(
        gate_id="G4",
        name="Live DAX Reconciliation & Perf",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"Reconciled {len(checks)} DAX assertion(s) within +/-{tol}"
            if passed
            else f"{sum(1 for f in findings if f.severity == 'error')} DAX/perf failure(s)"
        ),
        metrics={"assertions_checked": len(checks), "checks": checks, "performance": perf_metrics},
        findings=findings,
        artifacts=artifacts,
    )
