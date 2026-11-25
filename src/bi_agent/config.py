"""Toolchain configuration and local BI environment diagnostics."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class PathsConfig(BaseModel):
    pbir: str = ""
    tabular_editor: str = ""
    dscmd: str = ""
    adomd_dir: str = ""
    powerbi_desktop: str = ""
    mcp_config: str = ""


class TimeoutsConfig(BaseModel):
    pbir: int = 60
    tabular_editor: int = 120
    dscmd: int = 120
    adomd: int = 60


class GatesConfig(BaseModel):
    bpa_fail_severity: str = "Error"
    dax_numeric_tolerance: float = 0.01
    perf_budget_cold_ms: int = 1500
    perf_budget_warm_ms: int = 500
    desktop_screenshot_settle_ms: int = 750


class ProjectConfig(BaseModel):
    pbip_project_dir: str = "pbi/EnterpriseSales"
    ground_truth_path: str = "data/ground_truth/expected_metrics.json"


class ToolchainConfig(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    timeouts_seconds: TimeoutsConfig = Field(default_factory=TimeoutsConfig)
    gates: GatesConfig = Field(default_factory=GatesConfig)
    project: ProjectConfig = Field(default_factory=ProjectConfig)


class ToolCheckResult(BaseModel):
    name: str
    required: bool
    found: bool
    path: str | None = None
    version: str | None = None
    status: Literal["ok", "warn", "error"]
    detail: str = ""


def find_repo_root(start: Path | None = None) -> Path:
    """Locate repository root via pyproject.toml or .git marker."""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists():
            return candidate
    return current


def load_toolchain_config(
    config_path: Path | None = None, repo_root: Path | None = None
) -> ToolchainConfig:
    """Load ToolchainConfig from YAML, falling back to defaults if absent."""
    root = repo_root or find_repo_root()
    target = config_path or (root / "config" / "toolchain.yaml")
    if not target.is_file():
        return ToolchainConfig()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return ToolchainConfig.model_validate(raw)


def _run_cmd(args: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, "", str(exc)


def _first_existing(candidates: list[Path | None]) -> Path | None:
    for item in candidates:
        if item and item.exists():
            return item
    return None


def _resolve_pwsh() -> str | None:
    candidates = [
        shutil.which("pwsh"),
        shutil.which("powershell"),
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        rc, _, _ = _run_cmd(
            [candidate, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]
        )
        if rc == 0:
            return candidate
    return None


def _ps_file_version(pwsh_bin: str | None, file_path: Path) -> str | None:
    if not pwsh_bin:
        return None
    escaped = str(file_path).replace("'", "''")
    rc, out, _ = _run_cmd(
        [
            pwsh_bin,
            "-NoProfile",
            "-Command",
            f"(Get-Item -LiteralPath '{escaped}').VersionInfo.ProductVersion",
        ]
    )
    return out.splitlines()[0].strip() if rc == 0 and out else None


def _check_git() -> ToolCheckResult:
    git_bin = shutil.which("git")
    if not git_bin:
        return ToolCheckResult(
            name="git", required=True, found=False, status="error", detail="git not found on PATH"
        )
    rc, out, err = _run_cmd([git_bin, "--version"])
    ver = out.splitlines()[0] if rc == 0 and out else err
    return ToolCheckResult(
        name="git",
        required=True,
        found=True,
        path=git_bin,
        version=ver,
        status="ok" if rc == 0 else "error",
        detail="" if rc == 0 else err,
    )


def _check_python() -> ToolCheckResult:
    return ToolCheckResult(
        name="python",
        required=True,
        found=True,
        path=sys.executable,
        version=platform.python_version(),
        status="ok",
        detail="Active Python runtime",
    )


def _check_uv() -> ToolCheckResult:
    uv_bin = shutil.which("uv")
    if not uv_bin:
        return ToolCheckResult(
            name="uv", required=True, found=False, status="error", detail="uv not found on PATH"
        )
    rc, out, err = _run_cmd([uv_bin, "--version"])
    return ToolCheckResult(
        name="uv",
        required=True,
        found=True,
        path=uv_bin,
        version=out.splitlines()[0] if rc == 0 and out else None,
        status="ok" if rc == 0 else "error",
        detail="" if rc == 0 else err,
    )


def _check_pwsh(pwsh_bin: str | None) -> ToolCheckResult:
    if not pwsh_bin:
        return ToolCheckResult(
            name="pwsh",
            required=True,
            found=False,
            status="error",
            detail="Neither pwsh nor powershell found on PATH",
        )
    rc, out, err = _run_cmd(
        [pwsh_bin, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"]
    )
    return ToolCheckResult(
        name="pwsh",
        required=True,
        found=True,
        path=pwsh_bin,
        version=out.splitlines()[0] if rc == 0 and out else None,
        status="ok" if rc == 0 else "error",
        detail="" if rc == 0 else err,
    )


def _check_pbir(config: ToolchainConfig) -> tuple[ToolCheckResult, str | None]:
    override = Path(config.paths.pbir) if config.paths.pbir else None
    which_pbir = shutil.which("pbir")
    fallback = Path.home() / ".local" / "bin" / "pbir.exe"
    resolved = _first_existing([override, Path(which_pbir) if which_pbir else None, fallback])
    if not resolved:
        return (
            ToolCheckResult(
                name="pbir",
                required=True,
                found=False,
                status="error",
                detail="pbir CLI not found (install via uv tool install pbir-cli)",
            ),
            None,
        )
    rc, out, err = _run_cmd([str(resolved), "--version"])
    return (
        ToolCheckResult(
            name="pbir",
            required=True,
            found=True,
            path=str(resolved),
            version=out.splitlines()[0] if rc == 0 and out else None,
            status="ok" if rc == 0 else "error",
            detail="" if rc == 0 else err,
        ),
        str(resolved),
    )


def _check_tabular_editor_2(config: ToolchainConfig, pwsh_bin: str | None) -> ToolCheckResult:
    override = Path(config.paths.tabular_editor) if config.paths.tabular_editor else None
    which_te = shutil.which("TabularEditor")
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    winget_hits = sorted(
        (local_app / "Microsoft" / "WinGet" / "Packages").glob(
            "TabularEditor.TabularEditor.2*/TabularEditor.exe"
        )
    )
    candidates: list[Path | None] = [
        override,
        Path(which_te) if which_te else None,
        *winget_hits,
        Path(r"C:\Program Files (x86)\Tabular Editor\TabularEditor.exe"),
        local_app / "TabularEditor" / "TabularEditor.exe",
    ]
    resolved = _first_existing(candidates)
    if not resolved:
        return ToolCheckResult(
            name="tabular_editor_2",
            required=True,
            found=False,
            status="error",
            detail="TabularEditor.exe not found",
        )
    ver = _ps_file_version(pwsh_bin, resolved)
    return ToolCheckResult(
        name="tabular_editor_2",
        required=True,
        found=True,
        path=str(resolved),
        version=ver or "unknown",
        status="ok",
        detail="Metadata version inspected safely (GUI not launched)",
    )


def _check_dscmd(config: ToolchainConfig, pwsh_bin: str | None) -> ToolCheckResult:
    override = Path(config.paths.dscmd) if config.paths.dscmd else None
    which_ds = shutil.which("dscmd")
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    resolved = _first_existing(
        [
            override,
            Path(which_ds) if which_ds else None,
            local_app / "Programs" / "DAX Studio" / "dscmd.exe",
            Path(r"C:\Program Files\DAX Studio\dscmd.exe"),
        ]
    )
    if not resolved:
        return ToolCheckResult(
            name="dax_studio_dscmd",
            required=True,
            found=False,
            status="error",
            detail="dscmd.exe not found",
        )
    rc, out, err = _run_cmd([str(resolved), "--help"])
    ver = None
    if rc == 0 and out:
        match = re.search(r"DSCMD\s+v?([^\s]+)", out.splitlines()[0], re.IGNORECASE)
        ver = match.group(1) if match else out.splitlines()[0]
        return ToolCheckResult(
            name="dax_studio_dscmd",
            required=True,
            found=True,
            path=str(resolved),
            version=ver,
            status="ok",
        )
    if "SecurityException" in err:
        meta_ver = _ps_file_version(pwsh_bin, resolved)
        return ToolCheckResult(
            name="dax_studio_dscmd",
            required=True,
            found=True,
            path=str(resolved),
            version=meta_ver or "unknown",
            status="ok",
            detail="Registry access restricted in sandbox; binary version read via metadata",
        )
    return ToolCheckResult(
        name="dax_studio_dscmd",
        required=True,
        found=True,
        path=str(resolved),
        version=None,
        status="error",
        detail=err,
    )


def _check_adomd_dll(config: ToolchainConfig, pwsh_bin: str | None) -> ToolCheckResult:
    dll_name = "Microsoft.AnalysisServices.AdomdClient.dll"
    override_dir = Path(config.paths.adomd_dir) if config.paths.adomd_dir else None
    env_dir = Path(os.environ["PBIR_ADOMD_DIR"]) if os.environ.get("PBIR_ADOMD_DIR") else None
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    resolved = _first_existing(
        [
            (override_dir / dll_name) if override_dir else None,
            (env_dir / dll_name) if env_dir else None,
            local_app / "Programs" / "DAX Studio" / "bin" / dll_name,
            Path(r"C:\Program Files\DAX Studio\bin") / dll_name,
        ]
    )
    if not resolved:
        return ToolCheckResult(
            name="adomd_dll",
            required=True,
            found=False,
            status="error",
            detail=f"{dll_name} not found",
        )
    ver = _ps_file_version(pwsh_bin, resolved)
    return ToolCheckResult(
        name="adomd_dll",
        required=True,
        found=True,
        path=str(resolved),
        version=ver,
        status="ok",
    )


def _check_powerbi_desktop(config: ToolchainConfig, pwsh_bin: str | None) -> ToolCheckResult:
    if config.paths.powerbi_desktop:
        p = Path(config.paths.powerbi_desktop)
        if p.exists():
            return ToolCheckResult(
                name="powerbi_desktop",
                required=True,
                found=True,
                path=str(p),
                version=_ps_file_version(pwsh_bin, p),
                status="ok",
            )

    if pwsh_bin:
        cmd = (
            "Get-AppxPackage *PowerBI* | "
            "Select-Object -First 1 Version,InstallLocation | ConvertTo-Json -Compress"
        )
        rc, out, _ = _run_cmd([pwsh_bin, "-NoProfile", "-Command", cmd])
        if rc == 0 and out:
            try:
                data = json.loads(out)
                if isinstance(data, dict) and data.get("InstallLocation"):
                    return ToolCheckResult(
                        name="powerbi_desktop",
                        required=True,
                        found=True,
                        path=str(data["InstallLocation"]),
                        version=str(data.get("Version", "")),
                        status="ok",
                        detail="Microsoft Store Appx package",
                    )
            except json.JSONDecodeError:
                pass

    exe_fallback = Path(r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe")
    if exe_fallback.exists():
        return ToolCheckResult(
            name="powerbi_desktop",
            required=True,
            found=True,
            path=str(exe_fallback),
            version=_ps_file_version(pwsh_bin, exe_fallback),
            status="ok",
        )
    return ToolCheckResult(
        name="powerbi_desktop",
        required=True,
        found=False,
        status="error",
        detail="Power BI Desktop installation not detected",
    )


def _check_mcp(config: ToolchainConfig) -> ToolCheckResult:
    override = Path(config.paths.mcp_config) if config.paths.mcp_config else None
    default_cfg = Path.home() / ".gemini" / "config" / "mcp_config.json"
    cfg_path = _first_existing([override, default_cfg])
    if not cfg_path:
        return ToolCheckResult(
            name="powerbi_modeling_mcp",
            required=True,
            found=False,
            status="error",
            detail="mcp_config.json not found",
        )
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
        servers = payload.get("mcpServers", {})
        if "powerbi-modeling-mcp" in servers:
            return ToolCheckResult(
                name="powerbi_modeling_mcp",
                required=True,
                found=True,
                path=str(cfg_path),
                version="configured",
                status="ok",
                detail="powerbi-modeling-mcp entry present",
            )
    except (OSError, json.JSONDecodeError) as exc:
        return ToolCheckResult(
            name="powerbi_modeling_mcp",
            required=True,
            found=False,
            path=str(cfg_path),
            status="error",
            detail=f"Failed to parse mcp_config.json: {exc}",
        )
    return ToolCheckResult(
        name="powerbi_modeling_mcp",
        required=True,
        found=False,
        path=str(cfg_path),
        status="error",
        detail="powerbi-modeling-mcp missing from mcpServers",
    )


def _check_pbip_validator(repo_root: Path) -> ToolCheckResult:
    script = repo_root / ".agents" / "skills" / "pbip" / "scripts" / "validate_pbip.py"
    if script.is_file():
        return ToolCheckResult(
            name="pbip_validator",
            required=True,
            found=True,
            path=str(script),
            version="local-skill",
            status="ok",
        )
    return ToolCheckResult(
        name="pbip_validator",
        required=True,
        found=False,
        path=str(script),
        status="error",
        detail="validate_pbip.py not found in .agents/skills/pbip/scripts/",
    )


def _check_desktop_bridge(pbir_bin: str | None) -> ToolCheckResult:
    hint = (
        "No active Desktop Bridge instance detected. When opening Power BI Desktop in Phases 3-4, "
        "ensure Preview Feature 'Enable external tool access to Power BI Desktop through secure "
        "local APIs' is enabled."
    )
    if not pbir_bin:
        return ToolCheckResult(
            name="desktop_bridge",
            required=False,
            found=False,
            status="warn",
            detail=f"pbir CLI unavailable; {hint}",
        )
    rc, out, err = _run_cmd([pbir_bin, "desktop", "list", "--json"])
    combined = f"{out}\n{err}".strip()
    if rc == 0 and out:
        try:
            parsed = json.loads(out)
            instances = parsed if isinstance(parsed, list) else parsed.get("instances", [])
            if instances:
                return ToolCheckResult(
                    name="desktop_bridge",
                    required=False,
                    found=True,
                    path=pbir_bin,
                    version=f"{len(instances)} instance(s)",
                    status="ok",
                    detail="Desktop Bridge active",
                )
        except json.JSONDecodeError:
            pass
    return ToolCheckResult(
        name="desktop_bridge",
        required=False,
        found=False,
        path=pbir_bin,
        status="warn",
        detail=f"{hint} ({combined})" if combined else hint,
    )


def run_doctor(config: ToolchainConfig, repo_root: Path) -> list[ToolCheckResult]:
    """Run all local BI toolchain checks."""
    pwsh_bin = _resolve_pwsh()
    pbir_res, pbir_bin = _check_pbir(config)
    return [
        _check_git(),
        _check_python(),
        _check_uv(),
        _check_pwsh(pwsh_bin),
        pbir_res,
        _check_tabular_editor_2(config, pwsh_bin),
        _check_dscmd(config, pwsh_bin),
        _check_adomd_dll(config, pwsh_bin),
        _check_powerbi_desktop(config, pwsh_bin),
        _check_mcp(config),
        _check_pbip_validator(repo_root),
        _check_desktop_bridge(pbir_bin),
    ]
