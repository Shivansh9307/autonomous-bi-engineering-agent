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

CUSTOMER_DISCOUNT_DAX_QUERY = """EVALUATE
ROW(
    "GrossRevenue", [Gross Revenue],
    "DiscountRatePct", [Discount Rate %],
    "ActiveCustomers", [Active Customers],
    "RevenuePerCustomer", [Revenue per Customer]
)"""

CROSS_GRAIN_BUDGET_QUERY = """EVALUATE
ROW(
    "BudgetByProduct", CALCULATE([Budget Revenue], 'DimProduct'[Category] = "Cloud Hardware"),
    "BudgetByCustomer", CALCULATE([Budget Revenue], 'DimCustomer'[Segment] = "Enterprise"),
    "BudgetBySalesRep", CALCULATE([Budget Revenue], 'DimSalesRep'[Region] = "EMEA"),
    "VarianceByProduct", CALCULATE([Budget Variance], 'DimProduct'[Category] = "Cloud Hardware")
)"""

NO_ACTUALS_GUARD_QUERY = """EVALUATE
ROW(
    "NoActualsBudget",
        CALCULATE([Budget Revenue], 'FactSales'[SalesOrderID] = "__NO_ACTUALS__"),
    "NoActualsVariance",
        CALCULATE([Budget Variance], 'FactSales'[SalesOrderID] = "__NO_ACTUALS__"),
    "NoActualsStatus",
        CALCULATE([Budget Attainment Status], 'FactSales'[SalesOrderID] = "__NO_ACTUALS__"),
    "NoBudgetStatus",
        CALCULATE([Budget Attainment Status], 'DimProduct'[Category] = "Cloud Hardware")
)"""

FUTURE_PERIOD_CHECK_QUERY = """EVALUATE
ROW(
    "MaxDateKey", CALCULATE(MAX('DimDate'[DateKey]), REMOVEFILTERS()),
    "MaxSalesDateKey", CALCULATE(MAX('FactSales'[DateKey]), REMOVEFILTERS())
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

    if (
        gt.overall.gross_revenue is not None
        and gt.overall.active_customers is not None
        and gt.overall.revenue_per_customer is not None
    ):
        cd_res = adomd_adapter.execute_dax(
            target_port, CUSTOMER_DISCOUNT_DAX_QUERY, config, repo_root, database=target_db
        )
        if not cd_res.ok or not cd_res.rows:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_CUSTOMER_DISCOUNT_QUERY_FAILED",
                    message=cd_res.error or "Customer & discount DAX query failed.",
                )
            )
        else:
            cd_row = cd_res.rows[0]
            cd_pairs = [
                ("GrossRevenue", gt.overall.gross_revenue, tol),
                ("DiscountRatePct", gt.overall.discount_rate_pct, 0.0005),
                ("ActiveCustomers", float(gt.overall.active_customers), 0.0),
                ("RevenuePerCustomer", gt.overall.revenue_per_customer, tol),
            ]
            for field_name, expected_val, field_tol in cd_pairs:
                _compare_numeric(
                    "overall",
                    field_name,
                    _get_field(cd_row, field_name),
                    expected_val,
                    field_tol,
                    findings,
                    checks,
                )

            if overall_res.ok and overall_res.rows:
                act_rev_raw = _get_field(overall_res.rows[0], "TotalRevenue")
                act_disc_raw = _get_field(overall_res.rows[0], "TotalDiscount")
                act_gross_raw = _get_field(cd_row, "GrossRevenue")
                if (
                    act_rev_raw is not None
                    and act_disc_raw is not None
                    and act_gross_raw is not None
                ):
                    act_rev = float(act_rev_raw)
                    act_gross_minus_disc = float(act_gross_raw) - float(act_disc_raw)
                    id_diff = abs(act_rev - act_gross_minus_disc)
                    id_ok = id_diff <= tol
                    checks.append(
                        {
                            "scope": "audit",
                            "metric": "AccountingIdentity_NetEqGrossMinusDiscount",
                            "expected": round(act_gross_minus_disc, 6),
                            "actual": round(act_rev, 6),
                            "diff": round(id_diff, 6),
                            "status": "PASS" if id_ok else "FAIL",
                        }
                    )
                    if not id_ok:
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G4_ACCOUNTING_IDENTITY_MISMATCH",
                                message=(
                                    f"[audit] Accounting identity failed: TotalRevenue={act_rev} "
                                    f"!= GrossRevenue-TotalDiscount={act_gross_minus_disc:.2f} "
                                    f"(diff={id_diff:.4f} > tolerance={tol})"
                                ),
                            )
                        )

    cg_res = adomd_adapter.execute_dax(
        target_port, CROSS_GRAIN_BUDGET_QUERY, config, repo_root, database=target_db
    )
    if not cg_res.ok or not cg_res.rows:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_CROSS_GRAIN_QUERY_FAILED",
                message=cg_res.error or "Cross-grain budget guard DAX query failed.",
            )
        )
    else:
        cg_row = cg_res.rows[0]
        for field_name in (
            "BudgetByProduct",
            "BudgetByCustomer",
            "BudgetBySalesRep",
            "VarianceByProduct",
        ):
            val = _get_field(cg_row, field_name)
            is_blank = val is None
            checks.append(
                {
                    "scope": "cross_grain",
                    "metric": field_name,
                    "expected": None,
                    "actual": val,
                    "diff": 0.0 if is_blank else None,
                    "status": "PASS" if is_blank else "FAIL",
                }
            )
            if not is_blank:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_CROSS_GRAIN_BUDGET_LEAK",
                        message=(
                            f"[cross_grain] '{field_name}' returned {val} instead of BLANK() "
                            "when sliced by non-conforming dimension."
                        ),
                    )
                )

    na_res = adomd_adapter.execute_dax(
        target_port, NO_ACTUALS_GUARD_QUERY, config, repo_root, database=target_db
    )
    if not na_res.ok or not na_res.rows:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_NO_ACTUALS_QUERY_FAILED",
                message=na_res.error or "No-actuals guard DAX query failed.",
            )
        )
    else:
        na_row = na_res.rows[0]
        _compare_numeric(
            "no_actuals",
            "NoActualsBudget",
            _get_field(na_row, "NoActualsBudget"),
            gt.overall.total_budget,
            tol,
            findings,
            checks,
        )
        na_var = _get_field(na_row, "NoActualsVariance")
        na_var_ok = na_var is None
        checks.append(
            {
                "scope": "no_actuals",
                "metric": "NoActualsVarianceIsBlank",
                "expected": None,
                "actual": na_var,
                "diff": 0.0 if na_var_ok else None,
                "status": "PASS" if na_var_ok else "FAIL",
            }
        )
        if not na_var_ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_NO_ACTUALS_VARIANCE_NOT_BLANK",
                    message=(
                        f"[no_actuals] Budget Variance returned {na_var} instead of BLANK() "
                        "when FactSales has no rows."
                    ),
                )
            )

        expected_no_act = (
            gt.finance_guards.expected_no_actuals_status
            if gt.finance_guards is not None
            else "No Actuals"
        )
        na_status = _get_field(na_row, "NoActualsStatus")
        na_status_ok = na_status == expected_no_act
        checks.append(
            {
                "scope": "no_actuals",
                "metric": "NoActualsStatus",
                "expected": expected_no_act,
                "actual": na_status,
                "diff": 0.0 if na_status_ok else None,
                "status": "PASS" if na_status_ok else "FAIL",
            }
        )
        if not na_status_ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_NO_ACTUALS_STATUS_MISMATCH",
                    message=(
                        f"[no_actuals] Budget Attainment Status returned '{na_status}' "
                        f"(expected '{expected_no_act}')."
                    ),
                )
            )

        expected_no_bud = (
            gt.finance_guards.expected_no_budget_status
            if gt.finance_guards is not None
            else "No Budget"
        )
        nb_status = _get_field(na_row, "NoBudgetStatus")
        nb_status_ok = nb_status == expected_no_bud
        checks.append(
            {
                "scope": "cross_grain",
                "metric": "NoBudgetStatus",
                "expected": expected_no_bud,
                "actual": nb_status,
                "diff": 0.0 if nb_status_ok else None,
                "status": "PASS" if nb_status_ok else "FAIL",
            }
        )
        if not nb_status_ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_NO_BUDGET_STATUS_MISMATCH",
                    message=(
                        f"[cross_grain] Budget Attainment Status returned '{nb_status}' "
                        f"(expected '{expected_no_bud}')."
                    ),
                )
            )

    future_period_meta: dict[str, Any] = {"status": "SKIP"}
    fp_res = adomd_adapter.execute_dax(
        target_port, FUTURE_PERIOD_CHECK_QUERY, config, repo_root, database=target_db
    )
    if not fp_res.ok or not fp_res.rows:
        findings.append(
            GateFinding(
                severity="error",
                code="G4_FUTURE_PERIOD_QUERY_FAILED",
                message=fp_res.error or "Future-period date range query failed.",
            )
        )
        future_period_meta = {"status": "FAIL", "error": fp_res.error}
    else:
        fp_row = fp_res.rows[0]
        max_dk_raw = _get_field(fp_row, "MaxDateKey")
        max_sales_raw = _get_field(fp_row, "MaxSalesDateKey")
        max_dk = int(float(max_dk_raw)) if max_dk_raw is not None else 0
        max_sales_dk = int(float(max_sales_raw)) if max_sales_raw is not None else 0
        if max_dk <= max_sales_dk:
            reason = (
                f"DimDate max DateKey ({max_dk}) has no dates beyond MAX('FactSales'[DateKey]) "
                f"({max_sales_dk}); future-month budget/time-intelligence check skipped (N/A)."
            )
            future_period_meta = {
                "status": "SKIP",
                "max_date_key": max_dk,
                "max_sales_date_key": max_sales_dk,
                "reason": reason,
            }
            findings.append(
                GateFinding(
                    severity="info",
                    code="G4_FUTURE_PERIOD_CHECK_SKIPPED",
                    message=reason,
                )
            )
            checks.append(
                {
                    "scope": "future_period",
                    "metric": "FuturePeriodBudgetAndTimeIntel",
                    "expected": "N/A",
                    "actual": None,
                    "diff": None,
                    "status": "SKIP",
                }
            )
        else:
            future_vals_query = (
                "EVALUATE\n"
                "ROW(\n"
                '    "FutureBudget", CALCULATE([Budget Revenue], '
                f"'DimDate'[DateKey] > {max_sales_dk}),\n"
                '    "FutureVariance", CALCULATE([Budget Variance], '
                f"'DimDate'[DateKey] > {max_sales_dk}),\n"
                '    "FutureStatus", CALCULATE([Budget Attainment Status], '
                f"'DimDate'[DateKey] > {max_sales_dk}),\n"
                '    "FutureYTD", CALCULATE([Revenue YTD], '
                f"'DimDate'[DateKey] > {max_sales_dk}),\n"
                '    "FutureRolling3M", CALCULATE([Revenue Rolling 3M], '
                f"'DimDate'[DateKey] > {max_sales_dk})\n"
                ")"
            )
            fv_res = adomd_adapter.execute_dax(
                target_port, future_vals_query, config, repo_root, database=target_db
            )
            if not fv_res.ok or not fv_res.rows:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_FUTURE_PERIOD_QUERY_FAILED",
                        message=fv_res.error or "Future-period values query failed.",
                    )
                )
                future_period_meta = {"status": "FAIL", "error": fv_res.error}
            else:
                fv_row = fv_res.rows[0]
                future_period_meta = {
                    "status": "PASS",
                    "max_date_key": max_dk,
                    "max_sales_date_key": max_sales_dk,
                }
                for blank_metric in ("FutureVariance", "FutureYTD", "FutureRolling3M"):
                    f_val = _get_field(fv_row, blank_metric)
                    f_ok = f_val is None
                    checks.append(
                        {
                            "scope": "future_period",
                            "metric": blank_metric,
                            "expected": None,
                            "actual": f_val,
                            "diff": 0.0 if f_ok else None,
                            "status": "PASS" if f_ok else "FAIL",
                        }
                    )
                    if not f_ok:
                        future_period_meta["status"] = "FAIL"
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G4_FUTURE_PERIOD_NOT_BLANK",
                                message=(
                                    f"[future_period] '{blank_metric}' returned {f_val} "
                                    "beyond MAX('FactSales'[DateKey])."
                                ),
                            )
                        )
                if _get_field(fv_row, "FutureBudget") is not None:
                    fs_val = _get_field(fv_row, "FutureStatus")
                    fs_ok = fs_val == "No Actuals"
                    checks.append(
                        {
                            "scope": "future_period",
                            "metric": "FutureStatus",
                            "expected": "No Actuals",
                            "actual": fs_val,
                            "diff": 0.0 if fs_ok else None,
                            "status": "PASS" if fs_ok else "FAIL",
                        }
                    )
                    if not fs_ok:
                        future_period_meta["status"] = "FAIL"
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G4_NO_ACTUALS_STATUS_MISMATCH",
                                message=(
                                    f"[future_period] Budget Attainment Status returned "
                                    f"'{fs_val}' (expected 'No Actuals')."
                                ),
                            )
                        )

    assertions_checked = sum(1 for c in checks if c.get("status") in ("PASS", "FAIL"))

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
            f"Reconciled {assertions_checked} DAX assertion(s) within +/-{tol}"
            if passed
            else f"{sum(1 for f in findings if f.severity == 'error')} DAX/perf failure(s)"
        ),
        metrics={
            "assertions_checked": assertions_checked,
            "checks": checks,
            "future_period_check": future_period_meta,
            "performance": perf_metrics,
        },
        findings=findings,
        artifacts=artifacts,
    )
