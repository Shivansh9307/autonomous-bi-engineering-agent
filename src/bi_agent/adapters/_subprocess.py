"""Shared subprocess execution and toolchain binary resolution helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field

from bi_agent.config import ToolchainConfig


class CommandResult(BaseModel):
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    command: list[str] = Field(default_factory=list)

    @property
    def combined_output(self) -> str:
        return "\n".join(part for part in (self.stdout, self.stderr) if part).strip()


def run_command(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 60,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Execute a subprocess safely without invoking a shell."""
    merged_env = None
    if env is not None:
        merged_env = dict(os.environ)
        merged_env.update(env)

    start = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=merged_env,
            check=False,
        )
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            duration_ms=elapsed_ms,
            command=list(args),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return CommandResult(
            returncode=-1,
            stdout="",
            stderr=str(exc),
            duration_ms=elapsed_ms,
            command=list(args),
        )


def _first_existing(candidates: list[Path | None]) -> Path | None:
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def resolve_pbir(config: ToolchainConfig) -> str:
    override = Path(config.paths.pbir) if config.paths.pbir else None
    which_pbir = shutil.which("pbir")
    fallback = Path.home() / ".local" / "bin" / "pbir.exe"
    resolved = _first_existing([override, Path(which_pbir) if which_pbir else None, fallback])
    if not resolved:
        raise FileNotFoundError("pbir CLI executable not found.")
    return str(resolved)


def resolve_tabular_editor(config: ToolchainConfig) -> str:
    override = Path(config.paths.tabular_editor) if config.paths.tabular_editor else None
    which_te = shutil.which("TabularEditor")
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    winget_hits = sorted(
        (local_app / "Microsoft" / "WinGet" / "Packages").glob(
            "TabularEditor.TabularEditor.2*/TabularEditor.exe"
        )
    )
    resolved = _first_existing(
        [
            override,
            Path(which_te) if which_te else None,
            *winget_hits,
            Path(r"C:\Program Files (x86)\Tabular Editor\TabularEditor.exe"),
            local_app / "TabularEditor" / "TabularEditor.exe",
        ]
    )
    if not resolved:
        raise FileNotFoundError("TabularEditor.exe not found.")
    return str(resolved)


def resolve_dscmd(config: ToolchainConfig) -> str:
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
        raise FileNotFoundError("dscmd.exe not found.")
    return str(resolved)


def resolve_adomd_dir(config: ToolchainConfig) -> str:
    dll_name = "Microsoft.AnalysisServices.AdomdClient.dll"
    override = Path(config.paths.adomd_dir) if config.paths.adomd_dir else None
    env_dir = Path(os.environ["PBIR_ADOMD_DIR"]) if os.environ.get("PBIR_ADOMD_DIR") else None
    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    resolved = _first_existing(
        [
            (override / dll_name) if override else None,
            (env_dir / dll_name) if env_dir else None,
            local_app / "Programs" / "DAX Studio" / "bin" / dll_name,
            Path(r"C:\Program Files\DAX Studio\bin") / dll_name,
        ]
    )
    if not resolved:
        raise FileNotFoundError(f"{dll_name} not found.")
    return str(resolved.parent)


def resolve_pwsh() -> str:
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
        res = run_command(
            [candidate, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
            timeout=10,
        )
        if res.returncode == 0:
            return candidate
    raise FileNotFoundError("Neither pwsh nor powershell could be executed.")
