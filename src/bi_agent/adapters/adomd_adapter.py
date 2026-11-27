"""Adapter for PowerShell TOM/ADOMD.NET scripts (`discover_pbid_port`, `run_dax_impersonated`)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bi_agent.adapters._subprocess import (
    resolve_adomd_dir,
    resolve_pbir,
    resolve_pwsh,
    run_command,
)
from bi_agent.config import ToolchainConfig


class PbidInstance(BaseModel):
    port: int
    msmdsrv_pid: int | None = None
    pbi_desktop_pid: int | None = None
    database: str = ""
    database_count: int = 1
    open_target: str | None = None
    window_title: str | None = None


class DiscoveryResult(BaseModel):
    ok: bool
    instances: list[PbidInstance] = Field(default_factory=list)
    selected: PbidInstance | None = None
    error: str | None = None


class DaxQueryResult(BaseModel):
    ok: bool
    duration_ms: int = 0
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    error: str | None = None


def discover_instance(
    config: ToolchainConfig,
    repo_root: Path,
    report_name: str = "",
) -> DiscoveryResult:
    """Discover active Power BI Desktop local Analysis Services instances."""
    pwsh_bin = resolve_pwsh()
    script_path = repo_root / "scripts" / "pbid" / "discover_pbid_port.ps1"
    if not script_path.is_file():
        return DiscoveryResult(ok=False, error=f"Script not found: {script_path}")

    args = [
        pwsh_bin,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ]
    if report_name:
        args.extend(["-ReportName", report_name])
    with contextlib.suppress(FileNotFoundError):
        args.extend(["-AdomdDir", resolve_adomd_dir(config)])
    with contextlib.suppress(FileNotFoundError):
        args.extend(["-PbirBin", resolve_pbir(config)])

    res = run_command(args, cwd=repo_root, timeout=config.timeouts_seconds.adomd)
    if not res.stdout:
        return DiscoveryResult(
            ok=False,
            error=res.stderr or f"discover_pbid_port.ps1 exited with {res.returncode}",
        )
    try:
        payload = json.loads(res.stdout)
        return DiscoveryResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        return DiscoveryResult(ok=False, error=f"Invalid JSON from port discovery: {exc}")


def execute_dax(
    port: int,
    dax_query: str,
    config: ToolchainConfig,
    repo_root: Path,
    database: str = "",
    roles: str = "",
    effective_user_name: str = "",
    custom_data: str = "",
) -> DaxQueryResult:
    """Execute a DAX query via ADOMD.NET with optional RLS Roles & EffectiveUserName."""
    pwsh_bin = resolve_pwsh()
    script_path = repo_root / "scripts" / "pbid" / "run_dax_impersonated.ps1"
    if not script_path.is_file():
        return DaxQueryResult(ok=False, error=f"Script not found: {script_path}")

    args = [
        pwsh_bin,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-Port",
        str(port),
        "-DaxQuery",
        dax_query,
        "-CommandTimeoutSeconds",
        str(config.timeouts_seconds.adomd),
    ]
    if database:
        args.extend(["-Database", database])
    if roles:
        args.extend(["-Roles", roles])
    if effective_user_name:
        args.extend(["-EffectiveUserName", effective_user_name])
    if custom_data:
        args.extend(["-CustomData", custom_data])
    with contextlib.suppress(FileNotFoundError):
        args.extend(["-AdomdDir", resolve_adomd_dir(config)])

    res = run_command(args, cwd=repo_root, timeout=config.timeouts_seconds.adomd)
    if not res.stdout:
        return DaxQueryResult(
            ok=False,
            error=res.stderr or f"run_dax_impersonated.ps1 exited with {res.returncode}",
        )
    try:
        payload = json.loads(res.stdout)
        return DaxQueryResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        return DaxQueryResult(ok=False, error=f"Invalid JSON from DAX execution: {exc}")
