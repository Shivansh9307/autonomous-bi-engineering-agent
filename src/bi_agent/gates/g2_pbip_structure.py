"""Gate G2: Validate PBIP folder layout and Tabular Editor 2 TMDL load check."""

from __future__ import annotations

import time
from pathlib import Path

from bi_agent.adapters import pbip_adapter, tabular_editor_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult


def _find_semantic_model_definition(project_dir: Path, discovered: str | None) -> Path | None:
    if discovered:
        cand = Path(discovered) / "definition"
        if cand.is_dir():
            return cand
    if project_dir.name.endswith(".SemanticModel") and (project_dir / "definition").is_dir():
        return project_dir / "definition"
    for sm in sorted(project_dir.glob("*.SemanticModel")):
        if (sm / "definition").is_dir():
            return sm / "definition"
    return None


def run_gate_g2(
    project_dir: Path,
    config: ToolchainConfig,
    repo_root: Path,
) -> GateResult:
    """Validate PBIP cross-cutting rules and parse TMDL in Tabular Editor 2."""
    start = time.perf_counter()
    if not project_dir.exists():
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G2",
            name="PBIP & TMDL Structure",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"PBIP project directory does not exist: {project_dir}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G2_PROJECT_MISSING",
                    message=f"Project directory not found: {project_dir}",
                    location=str(project_dir),
                )
            ],
        )

    pbip_res = pbip_adapter.validate_pbip(
        project_dir,
        repo_root=repo_root,
        timeout=config.timeouts_seconds.pbir,
        no_pbir_cli=True,
    )
    findings = [
        GateFinding(
            severity="error" if f.level == "error" else ("warn" if f.level == "warn" else "info"),
            code=f.code,
            message=f.message,
            location=f.path,
        )
        for f in pbip_res.findings
        if f.level in ("error", "warn")
    ]

    def_dir = _find_semantic_model_definition(project_dir, pbip_res.semantic_model_dir)
    tmdl_ok = False
    table_count: int | None = None
    if def_dir is None:
        findings.append(
            GateFinding(
                severity="error",
                code="G2_TMDL_MISSING",
                message="SemanticModel/definition folder not found.",
                location=str(project_dir),
            )
        )
    else:
        tmdl_res = tabular_editor_adapter.check_tmdl_load(def_dir, config)
        tmdl_ok = tmdl_res.ok
        table_count = tmdl_res.table_count
        if not tmdl_res.ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G2_TMDL_LOAD_FAILED",
                    message=tmdl_res.message,
                    location=str(def_dir),
                )
            )

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = pbip_res.ok and tmdl_ok
    return GateResult(
        gate_id="G2",
        name="PBIP & TMDL Structure",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"PBIP clean and TE2 loaded {table_count} table(s)"
            if passed
            else f"PBIP errors={pbip_res.error_count}, tmdl_ok={tmdl_ok}"
        ),
        metrics={
            "pbip_errors": pbip_res.error_count,
            "pbip_warnings": pbip_res.warning_count,
            "tmdl_ok": tmdl_ok,
            "table_count": table_count,
        },
        findings=findings,
    )
