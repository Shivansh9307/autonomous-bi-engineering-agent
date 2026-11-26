"""Adapter for `pbir` CLI report validation and Power BI Desktop Bridge commands."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bi_agent.adapters._subprocess import resolve_pbir, run_command
from bi_agent.config import ToolchainConfig

_TRANSIENT_BRIDGE_MARKERS = (
    "host is not ready",
    "is unreachable",
    "still loading",
)


class PbirValidateResult(BaseModel):
    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    findings: list[str] = Field(default_factory=list)


class DesktopListResult(BaseModel):
    ok: bool
    instances: list[dict[str, Any]] = Field(default_factory=list)
    raw_output: str = ""
    error: str = ""


class DesktopRefreshResult(BaseModel):
    ok: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class ScreenshotFile(BaseModel):
    path: str
    size_bytes: int
    page_name: str = ""


class DesktopScreenshotResult(BaseModel):
    ok: bool
    exit_code: int
    screenshots: list[ScreenshotFile] = Field(default_factory=list)
    error: str = ""
    raw_output: str = ""


def validate_report(
    report_dir: Path,
    config: ToolchainConfig,
    all_checks: bool = True,
) -> PbirValidateResult:
    """Run `pbir validate <report_dir> [--all]` and capture findings."""
    pbir_bin = resolve_pbir(config)
    cmd = [pbir_bin, "validate", str(report_dir)]
    if all_checks:
        cmd.append("--all")
    res = run_command(cmd, timeout=config.timeouts_seconds.pbir)
    lines = [ln.strip() for ln in res.combined_output.splitlines() if ln.strip()]
    ok = res.returncode == 0
    return PbirValidateResult(
        ok=ok,
        exit_code=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
        findings=lines,
    )


def desktop_list(config: ToolchainConfig) -> DesktopListResult:
    """List open Power BI Desktop instances via `pbir desktop list --json`."""
    pbir_bin = resolve_pbir(config)
    res = run_command([pbir_bin, "desktop", "list", "--json"], timeout=config.timeouts_seconds.pbir)
    if not res.stdout:
        return DesktopListResult(ok=False, raw_output=res.combined_output, error=res.stderr)
    try:
        parsed = json.loads(res.stdout)
        instances = parsed if isinstance(parsed, list) else parsed.get("instances", [])
        return DesktopListResult(
            ok=res.returncode == 0,
            instances=list(instances),
            raw_output=res.combined_output,
        )
    except json.JSONDecodeError as exc:
        return DesktopListResult(
            ok=False,
            raw_output=res.combined_output,
            error=f"Invalid JSON from pbir desktop list: {exc}",
        )


def desktop_refresh(
    report_dir: Path,
    config: ToolchainConfig,
    include_model: bool = True,
    wait_seconds: float | None = 15.0,
) -> DesktopRefreshResult:
    """Reload report and optionally model definition into the Desktop canvas."""
    pbir_bin = resolve_pbir(config)
    cmd = [pbir_bin, "desktop", "refresh", str(report_dir)]
    if include_model:
        cmd.append("-m")
    if wait_seconds is not None:
        cmd.extend(["--wait-seconds", str(wait_seconds)])
    res = run_command(cmd, timeout=config.timeouts_seconds.pbir)
    return DesktopRefreshResult(
        ok=res.returncode == 0,
        exit_code=res.returncode,
        stdout=res.stdout,
        stderr=res.stderr,
    )


def desktop_screenshot(
    report_dir: Path,
    output_dir: Path,
    config: ToolchainConfig,
    settle_ms: int | None = None,
    min_bytes: int = 1024,
    max_retries: int = 5,
    retry_delay_seconds: float = 2.0,
) -> DesktopScreenshotResult:
    """Capture screenshots for all pages and verify PNG files exist and exceed min_bytes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    pbir_bin = resolve_pbir(config)
    settle = settle_ms if settle_ms is not None else config.gates.desktop_screenshot_settle_ms
    cmd = [
        pbir_bin,
        "desktop",
        "screenshot",
        str(report_dir),
        "--all",
        "--output-dir",
        str(output_dir),
        "--settle",
        str(settle),
        "--json",
    ]
    for attempt in range(max_retries + 1):
        res = run_command(cmd, timeout=config.timeouts_seconds.pbir)
        if res.returncode == 0:
            break
        lower_out = res.combined_output.lower()
        is_transient = any(marker in lower_out for marker in _TRANSIENT_BRIDGE_MARKERS)
        if attempt < max_retries and is_transient:
            time.sleep(retry_delay_seconds)
            continue
        break
    if res.returncode != 0:
        return DesktopScreenshotResult(
            ok=False,
            exit_code=res.returncode,
            error=res.combined_output or f"pbir desktop screenshot exited {res.returncode}",
            raw_output=res.combined_output,
        )

    candidate_paths: list[tuple[Path, str]] = []
    if res.stdout:
        try:
            payload = json.loads(res.stdout)
            items = (
                payload
                if isinstance(payload, list)
                else payload.get("screenshots", payload.get("pages", payload.get("files", [])))
            )
            for item in items:
                if isinstance(item, str):
                    candidate_paths.append((Path(item), Path(item).stem))
                elif isinstance(item, dict):
                    p = item.get("path") or item.get("file") or item.get("outputPath")
                    if p:
                        page_label = str(
                            item.get("page", item.get("displayName", Path(p).stem))
                        )
                        candidate_paths.append((Path(str(p)), page_label))
        except json.JSONDecodeError:
            pass

    if not candidate_paths:
        for png in sorted(output_dir.glob("*.png")):
            candidate_paths.append((png, png.stem))

    screenshots: list[ScreenshotFile] = []
    for path, page_name in candidate_paths:
        resolved = path if path.is_absolute() else (output_dir / path)
        if not resolved.is_file():
            return DesktopScreenshotResult(
                ok=False,
                exit_code=res.returncode,
                screenshots=screenshots,
                error=f"Expected screenshot missing on disk: {resolved}",
                raw_output=res.combined_output,
            )
        size = resolved.stat().st_size
        if size <= min_bytes:
            return DesktopScreenshotResult(
                ok=False,
                exit_code=res.returncode,
                screenshots=screenshots,
                error=f"Screenshot {resolved.name} too small ({size} bytes <= {min_bytes})",
                raw_output=res.combined_output,
            )
        screenshots.append(ScreenshotFile(path=str(resolved), size_bytes=size, page_name=page_name))

    if not screenshots:
        return DesktopScreenshotResult(
            ok=False,
            exit_code=res.returncode,
            error="No screenshot PNG files were produced.",
            raw_output=res.combined_output,
        )

    return DesktopScreenshotResult(
        ok=True,
        exit_code=res.returncode,
        screenshots=screenshots,
        raw_output=res.combined_output,
    )
