"""Gate G6: PBIR report validation and live Power BI Desktop screenshot verification."""

from __future__ import annotations

import time
from pathlib import Path

from bi_agent.adapters import pbir_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult


def _resolve_report_dir(path: Path) -> Path | None:
    if path.name.endswith(".Report") and path.is_dir():
        return path
    for child in sorted(path.glob("*.Report")):
        if child.is_dir():
            return child
    return None


def run_gate_g6(
    report_or_project_dir: Path,
    config: ToolchainConfig,
    repo_root: Path,
    offline: bool = False,
    output_dir: Path | None = None,
) -> GateResult:
    """Validate PBIR metadata and capture live Desktop page screenshots when online."""
    start = time.perf_counter()
    report_dir = _resolve_report_dir(report_or_project_dir)
    if report_dir is None:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G6",
            name="Report QA & Desktop Render",
            status="FAIL",
            duration_ms=elapsed,
            summary=f".Report directory not found under {report_or_project_dir}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G6_REPORT_MISSING",
                    message=f"Could not find a .Report folder in {report_or_project_dir}",
                    location=str(report_or_project_dir),
                )
            ],
        )

    findings: list[GateFinding] = []
    val_res = pbir_adapter.validate_report(report_dir, config, all_checks=not offline)
    if not val_res.ok:
        findings.append(
            GateFinding(
                severity="error",
                code="G6_PBIR_VALIDATE_FAILED",
                message=val_res.stderr or "\n".join(val_res.findings) or "pbir validate failed",
                location=str(report_dir),
            )
        )

    artifacts: list[str] = []
    screenshots_meta: list[dict[str, object]] = []
    refresh_ok = True
    screenshot_ok = True

    if not offline:
        ref_res = pbir_adapter.desktop_refresh(report_dir, config, include_model=True)
        refresh_ok = ref_res.ok
        if not ref_res.ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G6_DESKTOP_REFRESH_FAILED",
                    message=ref_res.stderr or ref_res.stdout or "pbir desktop refresh failed",
                    location=str(report_dir),
                )
            )
        else:
            shots_dir = (
                (output_dir / "screenshots")
                if output_dir
                else (repo_root / "artifacts" / "runs" / "ad-hoc" / "screenshots")
            )
            shot_res = pbir_adapter.desktop_screenshot(report_dir, shots_dir, config)
            screenshot_ok = shot_res.ok
            if not shot_res.ok:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G6_SCREENSHOT_FAILED",
                        message=shot_res.error,
                        location=str(shots_dir),
                    )
                )
            for item in shot_res.screenshots:
                artifacts.append(item.path)
                screenshots_meta.append(item.model_dump())

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = val_res.ok and (offline or (refresh_ok and screenshot_ok))
    return GateResult(
        gate_id="G6",
        name="Report QA & Desktop Render",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"pbir validate passed ({len(screenshots_meta)} screenshot(s))"
            if passed
            else f"Report validation/render failed ({len(findings)} error(s))"
        ),
        metrics={
            "pbir_exit_code": val_res.exit_code,
            "offline": offline,
            "screenshot_count": len(screenshots_meta),
            "screenshots": screenshots_meta,
        },
        findings=findings,
        artifacts=artifacts,
    )
