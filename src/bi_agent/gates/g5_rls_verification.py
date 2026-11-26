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
    "BudgetRowCount", COUNTROWS('FactBudget'),
    "TotalRevenue", COALESCE([Total Revenue], 0),
    "BudgetRevenue", COALESCE([Budget Revenue], 0),
    "GrossMargin", COALESCE([Gross Margin], 0)
)"""

QUOTED_LITERAL_RE = re.compile(r'"([^"]+)"')
ROLE_HEADER_RE = re.compile(r"^\s*role\s+('([^']+)'|([^\s]+))\s*$")
TABLE_PERM_RE = re.compile(r"^\t+tablePermission\s+('([^']+)'|([^\s=]+))\s*=\s*(.*)$")
REL_HEADER_RE = re.compile(r"^\s*relationship\s+")


def _strip_quotes(name: str) -> str:
    s = name.strip()
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    return s


def _normalize_dax(expr: str) -> str:
    return re.sub(r"\s+", "", expr).upper()


def _find_definition_dir(project_dir: Path) -> Path | None:
    if project_dir.name.endswith(".SemanticModel") and (project_dir / "definition").is_dir():
        return project_dir / "definition"
    if project_dir.name == "definition" and (project_dir / "model.tmdl").is_file():
        return project_dir
    for sm in sorted(project_dir.glob("*.SemanticModel")):
        if (sm / "definition").is_dir():
            return sm / "definition"
    return None


def _parse_tmdl_roles(def_dir: Path) -> dict[str, dict[str, str]]:
    roles_dir = def_dir / "roles"
    parsed: dict[str, dict[str, str]] = {}
    if not roles_dir.is_dir():
        return parsed

    for role_file in sorted(roles_dir.glob("*.tmdl")):
        role_name = role_file.stem
        perms: dict[str, str] = {}
        active_table: str | None = None
        active_lines: list[str] = []

        def _flush(target_perms: dict[str, str] = perms) -> None:
            nonlocal active_table, active_lines
            if active_table is not None:
                target_perms[active_table] = "\n".join(active_lines).strip()
                active_table = None
                active_lines = []

        for raw_line in role_file.read_text(encoding="utf-8").splitlines():
            m_role = ROLE_HEADER_RE.match(raw_line)
            if m_role:
                role_name = _strip_quotes(m_role.group(2) or m_role.group(3) or role_name)
                continue

            m_perm = TABLE_PERM_RE.match(raw_line)
            if m_perm:
                _flush()
                active_table = _strip_quotes(m_perm.group(2) or m_perm.group(3) or "")
                first_expr = (m_perm.group(4) or "").strip()
                active_lines = [first_expr] if first_expr else []
                continue

            if active_table is not None:
                if raw_line.startswith("\t\t") or not raw_line.strip():
                    active_lines.append(raw_line.strip())
                else:
                    _flush()

        _flush()
        parsed[role_name] = perms

    return parsed


def _parse_active_relationships(def_dir: Path) -> dict[str, set[str]]:
    rel_file = def_dir / "relationships.tmdl"
    propagates_to: dict[str, set[str]] = {}
    if not rel_file.is_file():
        return propagates_to

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in rel_file.read_text(encoding="utf-8").splitlines():
        if REL_HEADER_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)

    for block in blocks:
        from_table = ""
        to_table = ""
        is_active = True
        both_dirs = False
        for line in block:
            stripped = line.strip()
            if stripped.startswith("fromColumn:"):
                val = stripped.split(":", 1)[1].strip()
                from_table = _strip_quotes(val.split(".", 1)[0])
            elif stripped.startswith("toColumn:"):
                val = stripped.split(":", 1)[1].strip()
                to_table = _strip_quotes(val.split(".", 1)[0])
            elif stripped.startswith("isActive:") and "false" in stripped.lower():
                is_active = False
            elif stripped.startswith("crossFilteringBehavior:") and "both" in stripped.lower():
                both_dirs = True

        if is_active and from_table and to_table:
            propagates_to.setdefault(to_table, set()).add(from_table)
            if both_dirs:
                propagates_to.setdefault(from_table, set()).add(to_table)

    return propagates_to


def run_gate_g5_offline(
    project_dir: Path,
    requirement_path: Path,
) -> GateResult:
    """Audit TMDL RLS role definitions against the requirement contract and star schema offline."""
    start = time.perf_counter()
    req = load_requirement(requirement_path)
    def_dir = _find_definition_dir(project_dir)
    if def_dir is None:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G5",
            name="Live RLS Impersonation",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"SemanticModel/definition not found under {project_dir}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G5_OFFLINE_DEFINITION_MISSING",
                    message=f"Could not locate SemanticModel/definition in {project_dir}",
                    location=str(project_dir),
                )
            ],
        )

    tmdl_roles = _parse_tmdl_roles(def_dir)
    propagates_to = _parse_active_relationships(def_dir)
    fact_names = [f.name for f in req.star_schema.facts]
    findings: list[GateFinding] = []

    for role in req.rls_roles:
        if role.name not in tmdl_roles:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G5_OFFLINE_MISSING_ROLE",
                    message=f"Contracted RLS role '{role.name}' missing from TMDL roles/.",
                    location=str(def_dir / "roles"),
                )
            )
            continue

        actual_perms = tmdl_roles[role.name]
        for perm in role.effective_permissions():
            if perm.table not in actual_perms or not actual_perms[perm.table].strip():
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_OFFLINE_MISSING_PERMISSION",
                        message=(
                            f"Role '{role.name}' is missing tablePermission for "
                            f"'{perm.table}' in TMDL."
                        ),
                        location=str(def_dir / "roles" / f"{role.name}.tmdl"),
                    )
                )
            elif (
                _normalize_dax(perm.filter) in ("FALSE()", "FALSE")
                and _normalize_dax(actual_perms[perm.table]) not in ("FALSE()", "FALSE")
            ):
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_OFFLINE_DENY_MISMATCH",
                        message=(
                            f"Role '{role.name}' tablePermission on '{perm.table}' must be "
                            f"FALSE (), found '{actual_perms[perm.table]}'."
                        ),
                        location=str(def_dir / "roles" / f"{role.name}.tmdl"),
                    )
                )

        covered: set[str] = set()
        queue = list(actual_perms.keys())
        while queue:
            curr = queue.pop()
            if curr in covered:
                continue
            covered.add(curr)
            for nxt in propagates_to.get(curr, ()):
                if nxt not in covered:
                    queue.append(nxt)

        for fact in fact_names:
            if fact not in covered:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_OFFLINE_CROSS_FACT_LEAK",
                        message=(
                            f"Role '{role.name}' in TMDL does not filter or explicitly deny "
                            f"fact table '{fact}' (cross-fact RLS leak)."
                        ),
                        location=str(def_dir / "roles" / f"{role.name}.tmdl"),
                    )
                )

    elapsed = int((time.perf_counter() - start) * 1000)
    if findings:
        return GateResult(
            gate_id="G5",
            name="Live RLS Impersonation",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"Offline RLS contract audit failed ({len(findings)} violation(s))",
            metrics={
                "offline": True,
                "roles_audited": len(req.rls_roles),
                "live_impersonation_skipped": True,
            },
            findings=findings,
        )

    return GateResult(
        gate_id="G5",
        name="Live RLS Impersonation",
        status="SKIP",
        duration_ms=elapsed,
        summary=(
            f"Offline RLS contract audit passed ({len(req.rls_roles)} role(s), "
            "0 cross-fact leaks); live ADOMD impersonation skipped"
        ),
        metrics={
            "offline": True,
            "roles_audited": len(req.rls_roles),
            "live_impersonation_skipped": True,
        },
        findings=[],
    )


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
            match = QUOTED_LITERAL_RE.search(role.primary_filter())
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
            denies_fact_budget = "FactBudget" in role.denied_tables()
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
                            base_budget_rows = int(
                                float(_get_field(base_row, "BudgetRowCount") or 0)
                            )
                            base_rev = float(_get_field(base_row, "TotalRevenue") or 0.0)
                            base_ok = base_rows == 0 and base_budget_rows == 0
                            if base_rows != 0:
                                findings.append(
                                    GateFinding(
                                        severity="error",
                                        code="G5_FAIL_OPEN_LEAK",
                                        message=(
                                            f"Local principal under role '{role.name}' saw "
                                            f"{base_rows} FactSales rows (must fail closed to 0)."
                                        ),
                                    )
                                )
                            if base_budget_rows != 0:
                                findings.append(
                                    GateFinding(
                                        severity="error",
                                        code="G5_CROSS_FACT_LEAK",
                                        message=(
                                            f"Local principal under role '{role.name}' saw "
                                            f"{base_budget_rows} FactBudget rows (must be 0)."
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
                                    "actual_budget_rows": base_budget_rows,
                                    "actual_revenue": base_rev,
                                    "expected_rows": 0,
                                    "expected_budget_rows": 0,
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
                actual_budget_rows = int(float(_get_field(row, "BudgetRowCount") or 0))
                actual_rev = float(_get_field(row, "TotalRevenue") or 0.0)
                expected = gt.by_sales_rep_upn.get(upn)
                expected_budget_rows = (
                    0
                    if (expected is None or expected.row_count == 0 or denies_fact_budget)
                    else expected.budget_row_count
                )

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
                                    f"'{role.name}' saw {actual_rows} FactSales rows "
                                    "(must fail closed to 0)."
                                ),
                            )
                        )
                    if actual_budget_rows != 0:
                        case_ok = False
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G5_CROSS_FACT_LEAK",
                                message=(
                                    f"Unauthorized/unmapped principal '{upn}' under role "
                                    f"'{role.name}' saw {actual_budget_rows} FactBudget rows "
                                    "(must fail closed to 0)."
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
                    if denies_fact_budget and actual_budget_rows != 0:
                        case_ok = False
                        findings.append(
                            GateFinding(
                                severity="error",
                                code="G5_CROSS_FACT_LEAK",
                                message=(
                                    f"Principal '{upn}' under role '{role.name}' saw "
                                    f"{actual_budget_rows} FactBudget rows "
                                    "(expected 0 via explicit FactBudget deny)."
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
                        "actual_budget_rows": actual_budget_rows,
                        "actual_revenue": actual_rev,
                        "expected_rows": expected.row_count if expected else 0,
                        "expected_budget_rows": expected_budget_rows,
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
