"""Gate G4: Contract-driven live DAX ground-truth reconciliation and performance benchmarking."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from bi_agent.adapters import adomd_adapter, dax_studio_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.requirement import (
    BusinessRequirement,
    DaxVerificationSpec,
    load_requirement,
)
from bi_agent.schemas.test_suite import GroundTruthNode, load_ground_truth, resolve_dot_path

ISO_DATE_MARKER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$")


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
) -> float | None:
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
        return None

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
    return actual


def _dax_spec_has_checks(spec: DaxVerificationSpec) -> bool:
    return bool(
        spec.scalar_queries
        or spec.grouped_queries
        or spec.accounting_identities
        or spec.cross_grain_blank_checks
        or spec.empty_context_guards
        or spec.future_period_check is not None
    )


def _dax_spec_richness(spec: DaxVerificationSpec) -> int:
    return (
        sum(len(q.assertions) for q in spec.scalar_queries)
        + sum(len(q.metrics) for q in spec.grouped_queries)
        + len(spec.accounting_identities)
        + len(spec.cross_grain_blank_checks)
        + len(spec.empty_context_guards)
        + (1 if spec.future_period_check is not None else 0)
    )


def _discover_requirement_for_ground_truth(
    repo_root: Path,
    ground_truth_path: Path,
) -> BusinessRequirement | None:
    req_dir = repo_root / "requirements"
    if not req_dir.is_dir():
        return None
    target_gt = ground_truth_path.resolve()
    candidates: list[tuple[int, str, BusinessRequirement]] = []
    for yaml_path in sorted(req_dir.glob("*.yaml")):
        try:
            req = load_requirement(yaml_path)
        except Exception:
            continue
        gt_rel = req.resolved_ground_truth_path()
        if not gt_rel:
            continue
        if (repo_root / gt_rel).resolve() != target_gt:
            continue
        richness = _dax_spec_richness(req.verification.dax_checks)
        if richness > 0:
            candidates.append((richness, yaml_path.name, req))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[-1][2]


def _parse_comparable_date_marker(raw_val: Any) -> tuple[Any, str]:
    if raw_val is None:
        return 0, "0"
    if isinstance(raw_val, (int, float)):
        ival = int(raw_val)
        return ival, str(ival)
    text = str(raw_val).strip()
    m = ISO_DATE_MARKER_RE.match(text)
    if m:
        yr, mo, dy = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (yr, mo, dy), f"DATE({yr}, {mo}, {dy})"
    try:
        ival = int(float(text))
        return ival, str(ival)
    except ValueError:
        return text, f'"{text}"'


def _extract_secondary_date_marker(row: dict[str, Any]) -> Any:
    direct = _get_field(row, "MaxActualsDateKey")
    if direct is not None:
        return direct
    for key, val in row.items():
        clean = str(key).strip()
        if "[" in clean and clean.endswith("]"):
            clean = clean[clean.index("[") + 1 : -1]
        if clean.lower() != "maxdatekey":
            return val
    return None


def _resolve_benchmark_dax(spec: DaxVerificationSpec) -> str:
    if spec.benchmark_dax.strip():
        return spec.benchmark_dax.strip()
    if spec.grouped_queries:
        return spec.grouped_queries[0].dax.strip()
    if spec.scalar_queries:
        return spec.scalar_queries[0].dax.strip()
    return ""


def run_gate_g4(
    ground_truth_path: Path,
    config: ToolchainConfig,
    repo_root: Path,
    port: int | None = None,
    database: str = "",
    tolerance: float | None = None,
    skip_benchmark: bool = False,
    output_dir: Path | None = None,
    requirement_path: Path | None = None,
    dax_spec: DaxVerificationSpec | None = None,
    report_name: str = "",
) -> GateResult:
    """Reconcile live DAX measures against a declarative contract and enforce latency budgets."""
    start = time.perf_counter()
    gt = load_ground_truth(ground_truth_path)

    loaded_req: BusinessRequirement | None = None
    if requirement_path is not None:
        loaded_req = load_requirement(requirement_path)
    elif dax_spec is None:
        loaded_req = _discover_requirement_for_ground_truth(repo_root, ground_truth_path)

    effective_spec = (
        dax_spec
        if dax_spec is not None
        else (loaded_req.verification.dax_checks if loaded_req is not None else None)
    )
    if effective_spec is None or not _dax_spec_has_checks(effective_spec):
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G4",
            name="Live DAX Reconciliation & Perf",
            status="FAIL",
            duration_ms=elapsed,
            summary="No declarative verification.dax_checks contract found for Gate G4.",
            findings=[
                GateFinding(
                    severity="error",
                    code="G4_CONTRACT_MISSING",
                    message=(
                        "Provide --requirement with verification.dax_checks or a matching "
                        "requirement YAML referencing this ground_truth_path."
                    ),
                    location=str(requirement_path or ground_truth_path),
                )
            ],
        )

    tol = (
        tolerance
        if tolerance is not None
        else (
            loaded_req.verification_acceptance.dax_numeric_tolerance
            if loaded_req is not None
            else config.gates.dax_numeric_tolerance
        )
    )
    cold_budget_ms = (
        loaded_req.verification_acceptance.perf_budget_cold_ms
        if loaded_req is not None
        else config.gates.perf_budget_cold_ms
    )
    warm_budget_ms = (
        loaded_req.verification_acceptance.perf_budget_warm_ms
        if loaded_req is not None
        else config.gates.perf_budget_warm_ms
    )
    target_report_name = (
        report_name
        or (loaded_req.resolved_desktop_report_name() if loaded_req is not None else "")
        or config.project.desktop_report_name
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
    scalar_actuals: dict[str, float] = {}

    for sq in effective_spec.scalar_queries:
        res = adomd_adapter.execute_dax(
            target_port, sq.dax, config, repo_root, database=target_db
        )
        if not res.ok or not res.rows:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_SCALAR_QUERY_FAILED",
                    message=res.error or f"Scalar DAX query '{sq.name}' returned no rows.",
                )
            )
            continue

        row = res.rows[0]
        for assertion in sq.assertions:
            expected_raw: Any = assertion.expected_value
            if expected_raw is None and assertion.expected_path:
                expected_raw = gt.resolve(assertion.expected_path)
            if expected_raw is None:
                if assertion.optional:
                    continue
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_MISSING_EXPECTED",
                        message=(
                            f"[{sq.scope}] Missing expected ground-truth value for "
                            f"'{assertion.alias}' (path={assertion.expected_path})."
                        ),
                    )
                )
                continue

            field_tol = assertion.tolerance if assertion.tolerance is not None else tol
            actual_val = _compare_numeric(
                sq.scope,
                assertion.alias,
                _get_field(row, assertion.alias),
                float(expected_raw),
                field_tol,
                findings,
                checks,
            )
            if actual_val is not None:
                scalar_actuals[assertion.alias] = actual_val
                scalar_actuals[f"{sq.name}.{assertion.alias}"] = actual_val

    for gq in effective_spec.grouped_queries:
        res = adomd_adapter.execute_dax(
            target_port, gq.dax, config, repo_root, database=target_db
        )
        if not res.ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_GROUPED_QUERY_FAILED",
                    message=res.error or f"Grouped DAX query '{gq.name}' failed.",
                )
            )
            continue

        gt_map = gt.resolve(gq.ground_truth_map_path)
        if not isinstance(gt_map, (dict, GroundTruthNode)):
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_MISSING_EXPECTED",
                    message=(
                        f"[{gq.scope_prefix}] Ground-truth map '{gq.ground_truth_map_path}' "
                        "not found or not a mapping."
                    ),
                )
            )
            continue

        group_cols = [gq.group_column, *gq.group_aliases]
        rows_by_group: dict[str, dict[str, Any]] = {}
        for row in res.rows:
            gval = _get_field(row, *group_cols)
            if gval is not None:
                rows_by_group[str(gval)] = row

        for group_key, expected_entry in gt_map.items():
            actual_row = rows_by_group.get(str(group_key))
            if actual_row is None:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_MISSING_GROUP",
                        message=(
                            f"[{gq.scope_prefix}] Group '{group_key}' missing from "
                            f"SUMMARIZECOLUMNS output for '{gq.name}'."
                        ),
                    )
                )
                continue

            for metric in gq.metrics:
                exp_val = resolve_dot_path(expected_entry, metric.expected_field)
                if exp_val is None:
                    if metric.optional:
                        continue
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G4_MISSING_EXPECTED",
                            message=(
                                f"[{gq.scope_prefix}:{group_key}] Missing expected field "
                                f"'{metric.expected_field}' for '{metric.alias}'."
                            ),
                        )
                    )
                    continue

                field_tol = metric.tolerance if metric.tolerance is not None else tol
                _compare_numeric(
                    f"{gq.scope_prefix}:{group_key}",
                    metric.alias,
                    _get_field(actual_row, metric.alias),
                    float(exp_val),
                    field_tol,
                    findings,
                    checks,
                )

    for ident in effective_spec.accounting_identities:
        lhs_val = scalar_actuals.get(ident.lhs_alias)
        if lhs_val is None or any(t.alias not in scalar_actuals for t in ident.rhs_terms):
            continue
        rhs_val = sum(t.sign * scalar_actuals[t.alias] for t in ident.rhs_terms)
        id_tol = ident.tolerance if ident.tolerance is not None else tol
        id_diff = abs(lhs_val - rhs_val)
        id_ok = id_diff <= id_tol
        checks.append(
            {
                "scope": "audit",
                "metric": ident.name,
                "expected": round(rhs_val, 6),
                "actual": round(lhs_val, 6),
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
                        f"[audit] Accounting identity '{ident.name}' failed: "
                        f"{ident.lhs_alias}={lhs_val} != rhs={rhs_val:.2f} "
                        f"(diff={id_diff:.4f} > tolerance={id_tol})"
                    ),
                )
            )

    for cg in effective_spec.cross_grain_blank_checks:
        cg_res = adomd_adapter.execute_dax(
            target_port, cg.dax, config, repo_root, database=target_db
        )
        if not cg_res.ok or not cg_res.rows:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_CROSS_GRAIN_QUERY_FAILED",
                    message=cg_res.error or f"Cross-grain blank check '{cg.name}' failed.",
                )
            )
            continue
        cg_row = cg_res.rows[0]
        for alias in cg.expected_blank_aliases:
            val = _get_field(cg_row, alias)
            is_blank = val is None
            checks.append(
                {
                    "scope": cg.scope,
                    "metric": alias,
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
                            f"[{cg.scope}] '{alias}' returned {val} instead of BLANK() "
                            "when sliced by non-conforming dimension."
                        ),
                    )
                )

    for eg in effective_spec.empty_context_guards:
        eg_res = adomd_adapter.execute_dax(
            target_port, eg.dax, config, repo_root, database=target_db
        )
        if not eg_res.ok or not eg_res.rows:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G4_EMPTY_CONTEXT_QUERY_FAILED",
                    message=eg_res.error or f"Empty-context guard '{eg.name}' failed.",
                )
            )
            continue
        eg_row = eg_res.rows[0]

        for num_assert in eg.numeric_assertions:
            expected_raw = num_assert.expected_value
            if expected_raw is None and num_assert.expected_path:
                expected_raw = gt.resolve(num_assert.expected_path)
            if expected_raw is None:
                if num_assert.optional:
                    continue
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_MISSING_EXPECTED",
                        message=(
                            f"[{eg.scope}] Missing expected ground-truth value for "
                            f"'{num_assert.alias}'."
                        ),
                    )
                )
                continue
            field_tol = num_assert.tolerance if num_assert.tolerance is not None else tol
            _compare_numeric(
                eg.scope,
                num_assert.alias,
                _get_field(eg_row, num_assert.alias),
                float(expected_raw),
                field_tol,
                findings,
                checks,
            )

        for blank_alias in eg.expected_blank_aliases:
            b_val = _get_field(eg_row, blank_alias)
            b_ok = b_val is None
            checks.append(
                {
                    "scope": eg.scope,
                    "metric": f"{blank_alias}IsBlank",
                    "expected": None,
                    "actual": b_val,
                    "diff": 0.0 if b_ok else None,
                    "status": "PASS" if b_ok else "FAIL",
                }
            )
            if not b_ok:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_EMPTY_CONTEXT_NOT_BLANK",
                        message=(
                            f"[{eg.scope}] '{blank_alias}' returned {b_val} instead of BLANK() "
                            "in empty actuals context."
                        ),
                    )
                )

        for str_assert in eg.string_assertions:
            expected_str: Any = None
            if str_assert.expected_path:
                expected_str = gt.resolve(str_assert.expected_path)
            if expected_str is None:
                expected_str = str_assert.expected_value
            actual_str = _get_field(eg_row, str_assert.alias)
            str_ok = actual_str == expected_str
            checks.append(
                {
                    "scope": str_assert.scope or eg.scope,
                    "metric": str_assert.alias,
                    "expected": expected_str,
                    "actual": actual_str,
                    "diff": 0.0 if str_ok else None,
                    "status": "PASS" if str_ok else "FAIL",
                }
            )
            if not str_ok:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_NO_ACTUALS_STATUS_MISMATCH",
                        message=(
                            f"[{str_assert.scope or eg.scope}] '{str_assert.alias}' returned "
                            f"'{actual_str}' (expected '{expected_str}')."
                        ),
                    )
                )

    future_period_meta: dict[str, Any] = {"status": "SKIP"}
    fp_spec = effective_spec.future_period_check
    if fp_spec is not None:
        fp_probe_query = (
            "EVALUATE\n"
            "ROW(\n"
            '    "MaxDateKey", '
            f"CALCULATE(MAX({fp_spec.date_table_column}), REMOVEFILTERS()),\n"
            '    "MaxActualsDateKey", '
            f"CALCULATE(MAX({fp_spec.actuals_date_column}), REMOVEFILTERS())\n"
            ")"
        )
        fp_res = adomd_adapter.execute_dax(
            target_port, fp_probe_query, config, repo_root, database=target_db
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
            max_actuals_raw = _extract_secondary_date_marker(fp_row)
            max_dk_comp, _ = _parse_comparable_date_marker(max_dk_raw)
            max_actuals_comp, filter_lit = _parse_comparable_date_marker(max_actuals_raw)
            if max_dk_comp <= max_actuals_comp:
                reason = (
                    f"{fp_spec.date_table_column} max ({max_dk_raw}) has no dates beyond "
                    f"MAX({fp_spec.actuals_date_column}) ({max_actuals_raw}); "
                    "future-period check skipped (N/A)."
                )
                future_period_meta = {
                    "status": "FAIL" if fp_spec.require_active else "SKIP",
                    "max_date_marker": max_dk_raw,
                    "max_actuals_marker": max_actuals_raw,
                    "reason": reason,
                }
                if fp_spec.require_active:
                    findings.append(
                        GateFinding(
                            severity="error",
                            code="G4_FUTURE_PERIOD_REQUIRED",
                            message=reason,
                        )
                    )
                else:
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
                        "metric": "FuturePeriodCheck",
                        "expected": "Active" if fp_spec.require_active else "N/A",
                        "actual": None,
                        "diff": None,
                        "status": "FAIL" if fp_spec.require_active else "SKIP",
                    }
                )
            else:
                row_items: list[str] = []
                for alias, expr in fp_spec.must_be_blank_measures.items():
                    row_items.append(
                        f'    "{alias}", '
                        f"CALCULATE({expr}, {fp_spec.date_table_column} > {filter_lit})"
                    )
                if fp_spec.budget_measure:
                    row_items.append(
                        f'    "FutureBudget", CALCULATE({fp_spec.budget_measure}, '
                        f"{fp_spec.date_table_column} > {filter_lit})"
                    )
                if fp_spec.status_measure:
                    row_items.append(
                        f'    "FutureStatus", CALCULATE({fp_spec.status_measure}, '
                        f"{fp_spec.date_table_column} > {filter_lit})"
                    )
                if row_items:
                    future_vals_query = "EVALUATE\nROW(\n" + ",\n".join(row_items) + "\n)"
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
                            "max_date_marker": max_dk_raw,
                            "max_actuals_marker": max_actuals_raw,
                        }
                        for blank_alias in fp_spec.must_be_blank_measures:
                            f_val = _get_field(fv_row, blank_alias)
                            f_ok = f_val is None
                            checks.append(
                                {
                                    "scope": "future_period",
                                    "metric": blank_alias,
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
                                            f"[future_period] '{blank_alias}' returned {f_val} "
                                            f"beyond MAX({fp_spec.actuals_date_column})."
                                        ),
                                    )
                                )
                        if (
                            fp_spec.budget_measure
                            and fp_spec.status_measure
                            and _get_field(fv_row, "FutureBudget") is not None
                        ):
                            fs_val = _get_field(fv_row, "FutureStatus")
                            fs_ok = fs_val == fp_spec.expected_future_status
                            checks.append(
                                {
                                    "scope": "future_period",
                                    "metric": "FutureStatus",
                                    "expected": fp_spec.expected_future_status,
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
                                            f"[future_period] '{fp_spec.status_measure}' "
                                            f"returned '{fs_val}' "
                                            f"(expected '{fp_spec.expected_future_status}')."
                                        ),
                                    )
                                )

    assertions_checked = sum(1 for c in checks if c.get("status") in ("PASS", "FAIL"))

    benchmark_dax = _resolve_benchmark_dax(effective_spec)
    perf_metrics: dict[str, Any] = {
        "benchmark_skipped": skip_benchmark or not benchmark_dax,
        "cold_budget_ms": cold_budget_ms,
        "warm_budget_ms": warm_budget_ms,
    }
    artifacts: list[str] = []
    if not skip_benchmark and target_db and benchmark_dax:
        run_dir = output_dir or (repo_root / "artifacts" / "runs" / "ad-hoc")
        run_dir.mkdir(parents=True, exist_ok=True)
        dax_file = run_dir / "benchmark_query.dax"
        csv_file = run_dir / "benchmark_results.csv"
        dax_file.write_text(benchmark_dax, encoding="utf-8")
        bench = dax_studio_adapter.run_benchmark(
            f"localhost:{target_port}", target_db, dax_file, csv_file, config
        )
        perf_metrics["cold_avg_ms"] = bench.cold_avg_ms
        perf_metrics["warm_avg_ms"] = bench.warm_avg_ms
        perf_metrics["cold_max_ms"] = bench.cold_max_ms
        perf_metrics["warm_max_ms"] = bench.warm_max_ms
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
            if bench.cold_avg_ms is not None and bench.cold_avg_ms > cold_budget_ms:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_COLD_BUDGET_EXCEEDED",
                        message=(
                            f"Cold cache avg {bench.cold_avg_ms:.1f}ms > "
                            f"budget {cold_budget_ms}ms"
                        ),
                    )
                )
            if bench.warm_avg_ms is not None and bench.warm_avg_ms > warm_budget_ms:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G4_WARM_BUDGET_EXCEEDED",
                        message=(
                            f"Warm cache avg {bench.warm_avg_ms:.1f}ms > "
                            f"budget {warm_budget_ms}ms"
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
