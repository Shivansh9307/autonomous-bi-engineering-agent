"""Gate G5: Contract-driven live ADOMD.NET Row-Level Security impersonation verification."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from bi_agent.adapters import adomd_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.gates.g4_dax_reconciliation import _get_field
from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.requirement import (
    BusinessRequirement,
    DynamicRlsCheckSpec,
    RlsKpiAssertionSpec,
    RlsRequirement,
    StaticRlsCheckSpec,
    load_requirement,
)
from bi_agent.schemas.test_suite import GroundTruthDataset, load_ground_truth, resolve_dot_path

QUOTED_LITERAL_RE = re.compile(r'"([^"]+)"')
COL_LEAF_RE = re.compile(r"\[([^\]]+)\]")
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


def _build_rls_dax_query(
    fact_tables: list[str],
    kpi_assertions: list[RlsKpiAssertionSpec],
    scope_column: str = "",
) -> str:
    row_lines = [f'    "Rows_{fact}", COUNTROWS(\'{fact}\')' for fact in fact_tables]
    if scope_column:
        row_lines.append(
            f'    "ScopeValues", CONCATENATEX(VALUES({scope_column}), {scope_column}, ",")'
        )
    for kpi in kpi_assertions:
        row_lines.append(f'    "{kpi.alias}", {kpi.dax_expression}')
    return "EVALUATE\nROW(\n" + ",\n".join(row_lines) + "\n)"


def _extract_fact_count(row: dict[str, Any], fact_name: str, is_primary: bool) -> int | None:
    stem = fact_name[4:] if fact_name.startswith("Fact") else fact_name
    candidates = [f"Rows_{fact_name}", f"{stem}RowCount", f"{fact_name}RowCount"]
    if is_primary:
        candidates.append("RowCount")
    raw = _get_field(row, *candidates)
    if raw is None:
        return None
    return int(float(raw))


def _extract_scope_values(row: dict[str, Any], scope_column: str) -> str:
    candidates = ["ScopeValues"]
    m = COL_LEAF_RE.search(scope_column)
    if m:
        leaf = m.group(1).strip()
        candidates.extend([leaf, f"{leaf}s"])
    val = _get_field(row, *candidates)
    return str(val or "")


def _resolve_kpi_expected(
    kpi: RlsKpiAssertionSpec,
    gt: GroundTruthDataset,
    entry: Any = None,
) -> float | None:
    if kpi.expected_value is not None:
        return float(kpi.expected_value)
    if kpi.expected_path:
        val = gt.resolve(kpi.expected_path)
        if val is not None:
            return float(val)
    if kpi.expected_field and entry is not None:
        val = resolve_dot_path(entry, kpi.expected_field)
        if val is not None:
            return float(val)
    return None


def _build_role_lookup(req: BusinessRequirement) -> dict[str, RlsRequirement]:
    return {r.name: r for r in req.rls_roles}


def run_gate_g5_offline(
    project_dir: Path,
    requirement_path: Path,
) -> GateResult:
    """Audit TMDL RLS role definitions against the requirement contract and star schema offline."""
    start = time.perf_counter()
    req = load_requirement(requirement_path)
    if not req.rls_roles:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G5",
            name="Live RLS Impersonation",
            status="SKIP",
            duration_ms=elapsed,
            summary="No RLS roles declared in contract (0 roles audited); offline check skipped.",
            metrics={
                "offline": True,
                "roles_audited": 0,
                "live_impersonation_skipped": True,
            },
            findings=[],
        )

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


def _run_static_check(
    check: StaticRlsCheckSpec,
    role_req: RlsRequirement | None,
    fact_tables: list[str],
    gt: GroundTruthDataset,
    target_port: int,
    target_db: str,
    config: ToolchainConfig,
    repo_root: Path,
    tol: float,
    findings: list[GateFinding],
    matrix: list[dict[str, Any]],
) -> None:
    dax_query = _build_rls_dax_query(
        fact_tables, check.kpi_assertions, scope_column=check.scope_column
    )
    res = adomd_adapter.execute_dax(
        target_port, dax_query, config, repo_root, database=target_db, roles=check.role
    )
    if not res.ok or not res.rows:
        findings.append(
            GateFinding(
                severity="error",
                code="G5_STATIC_QUERY_FAILED",
                message=f"Static RLS query failed for role '{check.role}': {res.error}",
            )
        )
        return

    row = res.rows[0]
    fact_counts: dict[str, int | None] = {
        fn: _extract_fact_count(row, fn, is_primary=(idx == 0))
        for idx, fn in enumerate(fact_tables)
    }
    denied = role_req.denied_tables() if role_req is not None else set()
    gt_entry = (
        gt.resolve(check.ground_truth_entry_path) if check.ground_truth_entry_path else None
    )

    expected_scope = check.expected_scope_value
    if not expected_scope and role_req is not None:
        m = QUOTED_LITERAL_RE.search(role_req.primary_filter())
        if m:
            expected_scope = m.group(1)

    actual_scope = (
        _extract_scope_values(row, check.scope_column) if check.scope_column else ""
    )
    case_ok = True
    if expected_scope and actual_scope and actual_scope != expected_scope:
        case_ok = False
        findings.append(
            GateFinding(
                severity="error",
                code="G5_CROSS_REGION_LEAKAGE",
                message=(
                    f"Role '{check.role}' leaked scope values '{actual_scope}' "
                    f"(expected strictly '{expected_scope}')."
                ),
            )
        )

    for fact_name in denied:
        actual_c = fact_counts.get(fact_name)
        if actual_c is not None and actual_c != 0:
            case_ok = False
            findings.append(
                GateFinding(
                    severity="error",
                    code="G5_CROSS_FACT_LEAK",
                    message=(
                        f"Role '{check.role}' saw {actual_c} '{fact_name}' rows "
                        "(expected 0 via explicit deny)."
                    ),
                )
            )

    expected_counts: dict[str, int] = dict(check.expected_fact_counts)
    for fact_name, gt_field in check.fact_row_count_fields.items():
        if gt_entry is not None:
            val = resolve_dot_path(gt_entry, gt_field)
            if val is not None:
                expected_counts[fact_name] = int(val)

    for fact_name, exp_c in expected_counts.items():
        act_c = fact_counts.get(fact_name)
        if act_c is None or act_c != exp_c:
            case_ok = False
            findings.append(
                GateFinding(
                    severity="error",
                    code="G5_STATIC_ROW_MISMATCH",
                    message=(
                        f"Role '{check.role}' '{fact_name}' row count {act_c} != "
                        f"expected {exp_c}"
                    ),
                )
            )

    kpis_out: dict[str, float | None] = {}
    for kpi in check.kpi_assertions:
        act_raw = _get_field(row, kpi.alias)
        act_val = float(act_raw) if act_raw is not None else None
        kpis_out[kpi.alias] = act_val
        exp_val = _resolve_kpi_expected(kpi, gt, gt_entry)
        if exp_val is None:
            continue
        kpi_tol = kpi.tolerance if kpi.tolerance is not None else tol
        if act_val is None or abs(act_val - exp_val) > kpi_tol:
            case_ok = False
            findings.append(
                GateFinding(
                    severity="error",
                    code="G5_STATIC_REVENUE_MISMATCH",
                    message=(
                        f"Role '{check.role}' KPI '{kpi.alias}'={act_val} != "
                        f"expected {exp_val}"
                    ),
                )
            )

    clean_counts = {k: v for k, v in fact_counts.items() if v is not None}
    matrix.append(
        {
            "role": check.role,
            "type": "static",
            "principal": None,
            "fact_counts": clean_counts,
            "kpis": kpis_out,
            "scope_visible": actual_scope,
            "status": "PASS" if case_ok else "FAIL",
        }
    )


def _run_dynamic_check(
    check: DynamicRlsCheckSpec,
    role_req: RlsRequirement | None,
    fact_tables: list[str],
    gt: GroundTruthDataset,
    target_port: int,
    target_db: str,
    config: ToolchainConfig,
    repo_root: Path,
    tol: float,
    findings: list[GateFinding],
    matrix: list[dict[str, Any]],
) -> None:
    gt_map = gt.resolve(check.ground_truth_map_path) if check.ground_truth_map_path else {}
    map_keys = list(gt_map.keys()) if hasattr(gt_map, "keys") else []
    principals = (
        list(check.principals)
        or (list(role_req.test_principals) if role_req is not None else [])
        or map_keys
    )
    denied = role_req.denied_tables() if role_req is not None else set()
    dax_query = _build_rls_dax_query(fact_tables, check.kpi_assertions)
    checked_local_fail_closed = False

    for upn in principals:
        impersonation_mode = "effective_user_name"
        res = adomd_adapter.execute_dax(
            target_port,
            dax_query,
            config,
            repo_root,
            database=target_db,
            roles=check.role,
            effective_user_name=upn,
        )
        if not res.ok and "CreateWithS4U" in (res.error or ""):
            if not checked_local_fail_closed:
                checked_local_fail_closed = True
                base_res = adomd_adapter.execute_dax(
                    target_port,
                    dax_query,
                    config,
                    repo_root,
                    database=target_db,
                    roles=check.role,
                )
                if not base_res.ok or not base_res.rows:
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_DYNAMIC_QUERY_FAILED",
                            message=(
                                f"Dynamic RLS baseline check failed for role "
                                f"'{check.role}': {base_res.error}"
                            ),
                        )
                    )
                else:
                    base_row = base_res.rows[0]
                    base_counts = {
                        fn: _extract_fact_count(base_row, fn, is_primary=(idx == 0))
                        for idx, fn in enumerate(fact_tables)
                    }
                    base_ok = True
                    for idx, fn in enumerate(fact_tables):
                        cnt = base_counts[fn]
                        if cnt is not None and cnt != 0:
                            base_ok = False
                            findings.append(
                                GateFinding(
                                    severity="error",
                                    code="G5_FAIL_OPEN_LEAK" if idx == 0 else "G5_CROSS_FACT_LEAK",
                                    message=(
                                        f"Local principal under role '{check.role}' saw "
                                        f"{cnt} '{fn}' rows (must fail closed to 0)."
                                    ),
                                )
                            )
                    matrix.append(
                        {
                            "role": check.role,
                            "type": "dynamic",
                            "principal": "<local_user>",
                            "impersonation_mode": "local_upn_fail_closed",
                            "fact_counts": {k: v for k, v in base_counts.items() if v is not None},
                            "expected_fact_counts": {fn: 0 for fn in fact_tables},
                            "kpis": {},
                            "status": "PASS" if base_ok else "FAIL",
                        }
                    )

            res = adomd_adapter.execute_dax(
                target_port,
                dax_query,
                config,
                repo_root,
                database=target_db,
                roles=check.role,
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
        fact_counts = {
            fn: _extract_fact_count(row, fn, is_primary=(idx == 0))
            for idx, fn in enumerate(fact_tables)
        }
        expected_entry = (
            gt_map.get(upn)
            if hasattr(gt_map, "get")
            else (
                gt.resolve(f"{check.ground_truth_map_path}.{upn}")
                if check.ground_truth_map_path
                else None
            )
        )

        expected_counts: dict[str, int] = {}
        is_unauthorized = expected_entry is None
        if not is_unauthorized:
            for idx, fn in enumerate(fact_tables):
                if fn in denied:
                    expected_counts[fn] = 0
                elif fn in check.fact_row_count_fields:
                    val = resolve_dot_path(expected_entry, check.fact_row_count_fields[fn])
                    if val is not None:
                        expected_counts[fn] = int(val)
                elif idx == 0:
                    fallback_cnt = resolve_dot_path(expected_entry, "row_count")
                    if fallback_cnt is not None:
                        expected_counts[fn] = int(fallback_cnt)
            if expected_counts and all(v == 0 for v in expected_counts.values()):
                is_unauthorized = True
        else:
            expected_counts = {fn: 0 for fn in fact_tables}

        case_ok = True
        if is_unauthorized:
            for idx, fn in enumerate(fact_tables):
                act_c = fact_counts.get(fn)
                if act_c is not None and act_c != 0:
                    case_ok = False
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_FAIL_OPEN_LEAK" if idx == 0 else "G5_CROSS_FACT_LEAK",
                            message=(
                                f"Unauthorized/unmapped principal '{upn}' under role "
                                f"'{check.role}' saw {act_c} '{fn}' rows "
                                "(must fail closed to 0)."
                            ),
                        )
                    )
        else:
            for fn in denied:
                act_c = fact_counts.get(fn)
                if act_c is not None and act_c != 0:
                    case_ok = False
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_CROSS_FACT_LEAK",
                            message=(
                                f"Principal '{upn}' under role '{check.role}' saw "
                                f"{act_c} '{fn}' rows (expected 0 via explicit deny)."
                            ),
                        )
                    )
            for fn, exp_c in expected_counts.items():
                if fn in denied:
                    continue
                act_c = fact_counts.get(fn)
                if act_c is None or act_c != exp_c:
                    case_ok = False
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G5_DYNAMIC_ROW_MISMATCH",
                            message=(
                                f"Principal '{upn}' '{fn}' row count {act_c} != "
                                f"expected {exp_c}"
                            ),
                        )
                    )

        kpis_out: dict[str, float | None] = {}
        for kpi in check.kpi_assertions:
            act_raw = _get_field(row, kpi.alias)
            act_val = float(act_raw) if act_raw is not None else None
            kpis_out[kpi.alias] = act_val
            if is_unauthorized:
                continue
            exp_val = _resolve_kpi_expected(kpi, gt, expected_entry)
            if exp_val is None:
                continue
            kpi_tol = kpi.tolerance if kpi.tolerance is not None else tol
            if act_val is None or abs(act_val - exp_val) > kpi_tol:
                case_ok = False
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G5_DYNAMIC_REVENUE_MISMATCH",
                        message=(
                            f"Principal '{upn}' KPI '{kpi.alias}'={act_val} != "
                            f"expected {exp_val}"
                        ),
                    )
                )

        matrix.append(
            {
                "role": check.role,
                "type": "dynamic",
                "principal": upn,
                "impersonation_mode": impersonation_mode,
                "fact_counts": {k: v for k, v in fact_counts.items() if v is not None},
                "expected_fact_counts": expected_counts,
                "kpis": kpis_out,
                "status": "PASS" if case_ok else "FAIL",
            }
        )


def run_gate_g5(
    requirement_path: Path,
    ground_truth_path: Path,
    config: ToolchainConfig,
    repo_root: Path,
    port: int | None = None,
    database: str = "",
    tolerance: float | None = None,
    report_name: str = "",
) -> GateResult:
    """Verify contracted static and dynamic RLS roles enforce isolation and fail closed."""
    start = time.perf_counter()
    req = load_requirement(requirement_path)
    gt = load_ground_truth(ground_truth_path)
    fact_tables = [f.name for f in req.star_schema.facts]

    if not req.rls_roles:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G5",
            name="Live RLS Impersonation",
            status="SKIP",
            duration_ms=elapsed,
            summary="No RLS roles declared in contract; live impersonation skipped.",
            metrics={"fact_tables": fact_tables, "scenarios_tested": 0, "matrix": []},
            findings=[],
        )

    rls_spec = req.verification.rls_checks
    if not rls_spec.static_checks and not rls_spec.dynamic_checks:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G5",
            name="Live RLS Impersonation",
            status="FAIL",
            duration_ms=elapsed,
            summary="Contract declares rls_roles but verification.rls_checks is empty.",
            findings=[
                GateFinding(
                    severity="error",
                    code="G5_CONTRACT_MISSING",
                    message=(
                        "Declare verification.rls_checks (static_checks / dynamic_checks) "
                        "for all rls_roles."
                    ),
                    location=str(requirement_path),
                )
            ],
        )

    tol = (
        tolerance
        if tolerance is not None
        else req.verification_acceptance.dax_numeric_tolerance
    )
    target_report_name = (
        report_name or req.resolved_desktop_report_name() or config.project.desktop_report_name
    )

    target_port = port
    target_db = database
    if target_port is None:
        disc = adomd_adapter.discover_instance(
            config, repo_root, report_name=target_report_name
        )
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
    role_lookup = _build_role_lookup(req)

    covered_roles = {c.role for c in rls_spec.static_checks} | {
        c.role for c in rls_spec.dynamic_checks
    }
    for role in req.rls_roles:
        if role.name not in covered_roles:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G5_UNCOVERED_ROLE",
                    message=(
                        f"Contracted RLS role '{role.name}' has no check in "
                        "verification.rls_checks."
                    ),
                    location=str(requirement_path),
                )
            )

    for static_check in rls_spec.static_checks:
        _run_static_check(
            static_check,
            role_lookup.get(static_check.role),
            fact_tables,
            gt,
            target_port,
            target_db,
            config,
            repo_root,
            tol,
            findings,
            matrix,
        )

    for dynamic_check in rls_spec.dynamic_checks:
        _run_dynamic_check(
            dynamic_check,
            role_lookup.get(dynamic_check.role),
            fact_tables,
            gt,
            target_port,
            target_db,
            config,
            repo_root,
            tol,
            findings,
            matrix,
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
        metrics={
            "fact_tables": fact_tables,
            "scenarios_tested": len(matrix),
            "matrix": matrix,
        },
        findings=findings,
    )
