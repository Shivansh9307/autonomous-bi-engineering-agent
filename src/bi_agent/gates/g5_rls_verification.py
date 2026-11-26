"""Gate G5: Live ADOMD.NET Row-Level Security impersonation matrix verification."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from bi_agent.adapters import adomd_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.gates.g4_dax_reconciliation import _get_field
from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.requirement import load_requirement
from bi_agent.schemas.test_suite import load_ground_truth

STATIC_RLS_QUERY = """EVALUATE
ROW(
    "RowCount", COUNTROWS('FactSales'),
    "TotalRevenue", COALESCE([Total Revenue], 0),
    "Regions", CONCATENATEX(VALUES('DimStore'[Region]), 'DimStore'[Region], ",")
)"""

DYNAMIC_RLS_QUERY = """EVALUATE
ROW(
    "RowCount", COUNTROWS('FactSales'),
    "TotalRevenue", COALESCE([Total Revenue], 0),
    "GrossMargin", COALESCE([Gross Margin], 0)
)"""

QUOTED_LITERAL_RE = re.compile(r'"([^"]+)"')


def run_gate_g5(
    requirement_path: Path,
    ground_truth_path: Path,
    config: ToolchainConfig,
    repo_root: Path,
    port: int | None = None,
    database: str = "",
    tolerance: float | None = None,
) -> GateResult:
    """Verify static and dynamic RLS roles enforce isolation and fail closed."""
    start = time.perf_counter()
    tol = tolerance if tolerance is not None else config.gates.dax_numeric_tolerance
    req = load_requirement(requirement_path)
    gt = load_ground_truth(ground_truth_path)

    target_port = port
    target_db = database
    if target_port is None:
        disc = adomd_adapter.discover_instance(config, repo_root)
        if not disc.ok or not disc.selected:
            elapsed = int((time.perf_counter() - start) * 1000)
            return GateResult(
                gate_id="G5",
                name="Live RLS Impersonation",
                status="FAIL",
                duration_ms=elapsed,
                summary=disc.error or "No active Power BI Desktop engine found.",
                findings=[
                    GateFinding(
                        severity="error",
                        code="G5_NO_DESKTOP_INSTANCE",
                        message=disc.error or "No active Power BI Desktop engine found.",
                    )
                ],
            )
        target_port = disc.selected.port
        target_db = target_db or disc.selected.database

    findings: list[GateFinding] = []
    matrix: list[dict[str, Any]] = []

    for role in req.rls_roles:
        if role.type == "static":
            match = QUOTED_LITERAL_RE.search(role.filter)
            expected_region = match.group(1) if match else role.name.replace("Region_", "")
            res = adomd_adapter.execute_dax(
                target_port,
                STATIC_RLS_QUERY,
                config,
                repo_root,
                database=target_db,
                roles=role.name,
            )
            if not res.ok or not res.rows:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_STATIC_QUERY_FAILED",
                        message=f"Static RLS query failed for role '{role.name}': {res.error}",
                    )
                )
                continue

            row = res.rows[0]
            actual_rows = int(float(_get_field(row, "RowCount") or 0))
            actual_rev = float(_get_field(row, "TotalRevenue") or 0.0)
            actual_regions = str(_get_field(row, "Regions") or "")

            expected = gt.by_region.get(expected_region)
            case_ok = True
            if actual_regions and actual_regions != expected_region:
                case_ok = False
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_CROSS_REGION_LEAKAGE",
                        message=(
                            f"Role '{role.name}' leaked regions '{actual_regions}' "
                            f"(expected strictly '{expected_region}')."
                        ),
                    )
                )
            if expected is not None:
                if actual_rows != expected.row_count:
                    case_ok = False
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_STATIC_ROW_MISMATCH",
                            message=(
                                f"Role '{role.name}' row count {actual_rows} != "
                                f"expected {expected.row_count}"
                            ),
                        )
                    )
                if abs(actual_rev - expected.total_revenue) > tol:
                    case_ok = False
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_STATIC_REVENUE_MISMATCH",
                            message=(
                                f"Role '{role.name}' revenue {actual_rev} != "
                                f"expected {expected.total_revenue}"
                            ),
                        )
                    )

            matrix.append(
                {
                    "role": role.name,
                    "type": "static",
                    "principal": None,
                    "actual_rows": actual_rows,
                    "actual_revenue": actual_rev,
                    "regions_visible": actual_regions,
                    "status": "PASS" if case_ok else "FAIL",
                }
            )

        elif role.type == "dynamic":
            principals = role.test_principals or list(gt.by_sales_rep_upn.keys())
            checked_local_upn_fail_closed = False
            for upn in principals:
                impersonation_mode = "effective_user_name"
                res = adomd_adapter.execute_dax(
                    target_port,
                    DYNAMIC_RLS_QUERY,
                    config,
                    repo_root,
                    database=target_db,
                    roles=role.name,
                    effective_user_name=upn,
                )
                if not res.ok and "CreateWithS4U" in (res.error or ""):
                    if not checked_local_upn_fail_closed:
                        checked_local_upn_fail_closed = True
                        base_res = adomd_adapter.execute_dax(
                            target_port,
                            DYNAMIC_RLS_QUERY,
                            config,
                            repo_root,
                            database=target_db,
                            roles=role.name,
                        )
                        if not base_res.ok or not base_res.rows:
                            findings.append(
                                GateFinding(
                                    severity="error",
                                    code="G5_DYNAMIC_QUERY_FAILED",
                                    message=(
                                        f"Dynamic RLS baseline check failed for role "
                                        f"'{role.name}': {base_res.error}"
                                    ),
                                )
                            )
                        else:
                            base_row = base_res.rows[0]
                            base_rows = int(float(_get_field(base_row, "RowCount") or 0))
                            base_rev = float(_get_field(base_row, "TotalRevenue") or 0.0)
                            base_ok = base_rows == 0
                            if not base_ok:
                                findings.append(
                                    GateFinding(
                                        severity="error",
                                        code="G5_FAIL_OPEN_LEAK",
                                        message=(
                                            f"Local principal under role '{role.name}' saw "
                                            f"{base_rows} rows (must fail closed to 0)."
                                        ),
                                    )
                                )
                            matrix.append(
                                {
                                    "role": role.name,
                                    "type": "dynamic",
                                    "principal": "<local_user>",
                                    "impersonation_mode": "local_upn_fail_closed",
                                    "actual_rows": base_rows,
                                    "actual_revenue": base_rev,
                                    "expected_rows": 0,
                                    "status": "PASS" if base_ok else "FAIL",
                                }
                            )
                    res = adomd_adapter.execute_dax(
                        target_port,
                        DYNAMIC_RLS_QUERY,
                        config,
                        repo_root,
                        database=target_db,
                        roles=role.name,
                        custom_data=upn,
                    )
                    impersonation_mode = "custom_data_fallback"

                if not res.ok or not res.rows:
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_DYNAMIC_QUERY_FAILED",
                            message=f"Dynamic RLS query failed for '{upn}': {res.error}",
                        )
                    )
                    continue

                row = res.rows[0]
                actual_rows = int(float(_get_field(row, "RowCount") or 0))
                actual_rev = float(_get_field(row, "TotalRevenue") or 0.0)
                expected = gt.by_sales_rep_upn.get(upn)

                case_ok = True
                if expected is None or expected.row_count == 0:
                    if actual_rows != 0:
                        case_ok = False
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G5_FAIL_OPEN_LEAK",
                                message=(
                                    f"Unauthorized/unmapped principal '{upn}' under role "
                                    f"'{role.name}' saw {actual_rows} rows (must fail closed to 0)."
                                ),
                            )
                        )
                else:
                    if actual_rows != expected.row_count:
                        case_ok = False
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G5_DYNAMIC_ROW_MISMATCH",
                                message=(
                                    f"Principal '{upn}' row count {actual_rows} != "
                                    f"expected {expected.row_count}"
                                ),
                            )
                        )
                    if abs(actual_rev - expected.total_revenue) > tol:
                        case_ok = False
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G5_DYNAMIC_REVENUE_MISMATCH",
                                message=(
                                    f"Principal '{upn}' revenue {actual_rev} != "
                                    f"expected {expected.total_revenue}"
                                ),
                            )
                        )

                matrix.append(
                    {
                        "role": role.name,
                        "type": "dynamic",
                        "principal": upn,
                        "impersonation_mode": impersonation_mode,
                        "actual_rows": actual_rows,
                        "actual_revenue": actual_rev,
                        "expected_rows": expected.row_count if expected else 0,
                        "status": "PASS" if case_ok else "FAIL",
                    }
                )

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = len(findings) == 0 and len(matrix) > 0
    return GateResult(
        gate_id="G5",
        name="Live RLS Impersonation",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"Verified {len(matrix)} RLS impersonation scenario(s) with zero leakage"
            if passed
            else f"{len(findings)} RLS security verification failure(s)"
        ),
        metrics={"scenarios_tested": len(matrix), "matrix": matrix},
        findings=findings,
    )
