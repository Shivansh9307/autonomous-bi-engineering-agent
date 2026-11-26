"""Gate G3: Tabular Editor 2 Best Practice Analyzer + DAX Studio VPAX telemetry."""

from __future__ import annotations

import time
from pathlib import Path

from bi_agent.adapters import adomd_adapter, dax_studio_adapter, tabular_editor_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult


def _find_definition_dir(project_dir: Path) -> Path | None:
    if project_dir.name.endswith(".SemanticModel") and (project_dir / "definition").is_dir():
        return project_dir / "definition"
    if project_dir.name == "definition" and (project_dir / "model.tmdl").is_file():
        return project_dir
    for sm in sorted(project_dir.glob("*.SemanticModel")):
        if (sm / "definition").is_dir():
            return sm / "definition"
    return None


def run_gate_g3(
    project_dir: Path,
    config: ToolchainConfig,
    repo_root: Path,
    bpa_rules_path: Path | None = None,
    port: int | None = None,
    database: str = "",
    offline: bool = False,
    output_dir: Path | None = None,
    report_name: str = "",
) -> GateResult:
    """Run TE2 BPA on TMDL and optionally export/audit live VPAX metrics."""
    start = time.perf_counter()
    def_dir = _find_definition_dir(project_dir)
    if def_dir is None:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G3",
            name="BPA & VertiPaq Audit",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"SemanticModel/definition not found under {project_dir}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G3_DEFINITION_MISSING",
                    message=f"Could not locate SemanticModel/definition in {project_dir}",
                    location=str(project_dir),
                )
            ],
        )

    rules_file = bpa_rules_path or (repo_root / "config" / "bpa" / "enterprise-rules.json")
    bpa_res = tabular_editor_adapter.run_bpa(def_dir, rules_file, config)

    findings = [
        GateFinding(
            severity=(
                "error"
                if v.severity == "error"
                else ("warn" if v.severity == "warning" else "info")
            ),
            code=v.rule_id or "BPA_VIOLATION",
            message=v.message,
            location=f"{v.object_type}:{v.object_name}" if v.object_name else None,
        )
        for v in bpa_res.violations
    ]

    metrics: dict[str, object] = {
        "bpa_errors": bpa_res.error_count,
        "bpa_warnings": bpa_res.warning_count,
        "bpa_total_violations": len(bpa_res.violations),
        "vpax_skipped": offline,
    }
    artifacts: list[str] = []
    vpax_ok = True

    if not offline:
        target_port = port
        target_db = database
        if target_port is None:
            disc = adomd_adapter.discover_instance(config, repo_root, report_name=report_name)
            if disc.ok and disc.selected:
                target_port = disc.selected.port
                target_db = target_db or disc.selected.database

        if target_port is None or not target_db:
            vpax_ok = False
            findings.append(
                GateFinding(
                    severity="error",
                    code="G3_VPAX_NO_INSTANCE",
                    message="Live VertiPaq VPAX export requires an active Power BI Desktop model.",
                )
            )
        else:
            vpax_dir = output_dir or (repo_root / "artifacts" / "runs" / "ad-hoc")
            vpax_path = vpax_dir / "model.vpax"
            vpax_res = dax_studio_adapter.export_vpax(
                f"localhost:{target_port}", target_db, vpax_path, config
            )
            if vpax_res.ok:
                metrics["vpax"] = vpax_res.metrics
                artifacts.append(str(vpax_path))
            else:
                vpax_ok = False
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G3_VPAX_FAILED",
                        message=vpax_res.error,
                    )
                )

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = bpa_res.ok and vpax_ok
    return GateResult(
        gate_id="G3",
        name="BPA & VertiPaq Audit",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"BPA errors={bpa_res.error_count}, warnings={bpa_res.warning_count}"
            + ("" if offline else f", vpax_ok={vpax_ok}")
        ),
        metrics=metrics,
        findings=findings,
        artifacts=artifacts,
    )
