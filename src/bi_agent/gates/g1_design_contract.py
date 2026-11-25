"""Gate G1: Validate dimensional star-schema design contract from requirement YAML."""

from __future__ import annotations

import time
from pathlib import Path

from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.model_spec import DimensionalModelSpec
from bi_agent.schemas.requirement import load_requirement


def run_gate_g1(requirement_path: Path, repo_root: Path) -> GateResult:
    """Validate requirement YAML contract and Kimball star-schema rules."""
    start = time.perf_counter()
    try:
        req = load_requirement(requirement_path)
        spec = DimensionalModelSpec.from_requirement(req)
        violations = spec.validate_star_schema(repo_root=repo_root)
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G1",
            name="Design Contract",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"Failed to parse/validate design contract: {exc}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G1_SCHEMA_ERROR",
                    message=str(exc),
                    location=str(requirement_path),
                )
            ],
        )

    elapsed = int((time.perf_counter() - start) * 1000)
    findings = [
        GateFinding(
            severity="error",
            code="G1_DESIGN_VIOLATION",
            message=msg,
            location=str(requirement_path),
        )
        for msg in violations
    ]
    passed = len(findings) == 0
    return GateResult(
        gate_id="G1",
        name="Design Contract",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"Validated {len(spec.facts)} facts, {len(spec.dimensions)} dims, "
            f"{len(spec.kpis)} KPIs, {len(spec.rls_roles)} RLS roles"
            if passed
            else f"{len(findings)} design contract violation(s)"
        ),
        metrics={
            "req_id": req.id,
            "fact_count": len(spec.facts),
            "dimension_count": len(spec.dimensions),
            "relationship_count": len(spec.relationships),
            "kpi_count": len(spec.kpis),
            "rls_role_count": len(spec.rls_roles),
        },
        findings=findings,
    )
