"""Gate G6: PBIR report validation, page/visual contract checks, and Desktop screenshots."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from bi_agent.adapters import pbir_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.requirement import load_requirement


def _resolve_report_dir(path: Path) -> Path | None:
    if path.name.endswith(".Report") and path.is_dir():
        return path
    for child in sorted(path.glob("*.Report")):
        if child.is_dir():
            return child
    return None


def _norm_page_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _inspect_pbir_pages(report_dir: Path) -> tuple[list[str], dict[str, dict[str, object]]]:
    pages_dir = report_dir / "definition" / "pages"
    pages_meta_path = pages_dir / "pages.json"
    if not pages_meta_path.is_file():
        return [], {}
    try:
        meta = json.loads(pages_meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], {}

    page_order = [str(p) for p in meta.get("pageOrder", []) if str(p).strip()]
    pages_info: dict[str, dict[str, object]] = {}
    for page_id in page_order:
        page_dir = pages_dir / page_id
        page_json_path = page_dir / "page.json"
        display_name = page_id
        page_name = page_id
        if page_json_path.is_file():
            try:
                p_payload = json.loads(page_json_path.read_text(encoding="utf-8"))
                page_name = str(p_payload.get("name") or page_id)
                display_name = str(p_payload.get("displayName") or page_name)
            except (OSError, json.JSONDecodeError):
                pass
        visuals = list(page_dir.glob("visuals/*/visual.json"))
        pages_info[page_id] = {
            "name": page_name,
            "display_name": display_name,
            "visual_count": len(visuals),
        }
    return page_order, pages_info


def run_gate_g6(
    report_or_project_dir: Path,
    config: ToolchainConfig,
    repo_root: Path,
    offline: bool = False,
    output_dir: Path | None = None,
    requirement_path: Path | None = None,
) -> GateResult:
    """Validate PBIR metadata, contracted page/visual structure, and Desktop screenshots."""
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

    page_order, pages_info = _inspect_pbir_pages(report_dir)
    visuals_per_page: dict[str, int] = {
        str(info["display_name"]): int(info["visual_count"])  # type: ignore[arg-type]
        for info in pages_info.values()
    }

    req = load_requirement(requirement_path) if requirement_path is not None else None
    if req is not None:
        rc = req.verification.report_checks
        expected_pages = rc.expected_pages or [p.slug for p in req.report_pages]
        present_labels = set()
        for pid, info in pages_info.items():
            present_labels.add(_norm_page_label(pid))
            present_labels.add(_norm_page_label(str(info["name"])))
            present_labels.add(_norm_page_label(str(info["display_name"])))

        for exp_page in expected_pages:
            if _norm_page_label(exp_page) not in present_labels:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G6_EXPECTED_PAGE_MISSING",
                        message=f"Expected report page '{exp_page}' not found in PBIR pages.",
                        location=str(report_dir / "definition" / "pages" / "pages.json"),
                    )
                )

        for pid, info in pages_info.items():
            v_count = int(info["visual_count"])  # type: ignore[arg-type]
            if v_count < rc.min_visuals_per_page:
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G6_PAGE_VISUAL_COUNT_LOW",
                        message=(
                            f"Page '{info['display_name']}' ({pid}) has {v_count} visual(s) "
                            f"< minimum {rc.min_visuals_per_page}."
                        ),
                        location=str(report_dir / "definition" / "pages" / pid),
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

            if (
                req is not None
                and req.verification.report_checks.require_screenshot_count_match
                and page_order
                and len(screenshots_meta) != len(page_order)
            ):
                findings.append(
                    GateFinding(
                        severity="error",
                        code="G6_SCREENSHOT_COUNT_MISMATCH",
                        message=(
                            f"Captured {len(screenshots_meta)} screenshot(s) != "
                            f"{len(page_order)} page(s) in pages.json."
                        ),
                        location=str(shots_dir),
                    )
                )

    elapsed = int((time.perf_counter() - start) * 1000)
    has_errors = any(f.severity == "error" for f in findings)
    passed = not has_errors and val_res.ok and (offline or (refresh_ok and screenshot_ok))
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
            "page_count": len(page_order),
            "visuals_per_page": visuals_per_page,
            "screenshot_count": len(screenshots_meta),
            "screenshots": screenshots_meta,
        },
        findings=findings,
        artifacts=artifacts,
    )
