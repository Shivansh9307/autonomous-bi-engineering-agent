"""Adapter wrapping the local PBIP validation script."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from bi_agent.adapters._subprocess import run_command


class PbipFinding(BaseModel):
    level: str
    code: str
    message: str
    path: str | None = None


class PbipValidationResult(BaseModel):
    ok: bool
    exit_code: int
    error_count: int = 0
    warning_count: int = 0
    findings: list[PbipFinding] = Field(default_factory=list)
    report_dir: str | None = None
    semantic_model_dir: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def validate_pbip(
    project_dir: Path,
    repo_root: Path,
    timeout: int = 60,
    no_pbir_cli: bool = True,
) -> PbipValidationResult:
    """Run .agents/skills/pbip/scripts/validate_pbip.py --json on a PBIP directory."""
    validator = repo_root / ".agents" / "skills" / "pbip" / "scripts" / "validate_pbip.py"
    if not validator.is_file():
        return PbipValidationResult(
            ok=False,
            exit_code=-1,
            error_count=1,
            findings=[
                PbipFinding(
                    level="error",
                    code="validator_missing",
                    message=f"PBIP validator script not found at {validator}",
                    path=str(validator),
                )
            ],
        )

    cmd = [sys.executable, str(validator), str(project_dir), "--json"]
    if no_pbir_cli:
        cmd.append("--no-pbir-cli")

    res = run_command(cmd, cwd=repo_root, timeout=timeout)
    if not res.stdout:
        return PbipValidationResult(
            ok=False,
            exit_code=res.returncode,
            error_count=1,
            findings=[
                PbipFinding(
                    level="error",
                    code="pbip_validator_failed",
                    message=res.stderr or f"validate_pbip.py exited with {res.returncode}",
                    path=str(project_dir),
                )
            ],
        )

    try:
        payload = json.loads(res.stdout)
    except json.JSONDecodeError as exc:
        return PbipValidationResult(
            ok=False,
            exit_code=res.returncode,
            error_count=1,
            findings=[
                PbipFinding(
                    level="error",
                    code="pbip_invalid_json",
                    message=f"Failed to parse validate_pbip.py output: {exc}",
                    path=str(project_dir),
                )
            ],
        )

    findings = [PbipFinding.model_validate(item) for item in payload.get("findings", [])]
    error_count = int(payload.get("error_count", 0))
    warning_count = int(payload.get("warning_count", 0))
    ok = res.returncode in (0, 1) and error_count == 0
    return PbipValidationResult(
        ok=ok,
        exit_code=res.returncode,
        error_count=error_count,
        warning_count=warning_count,
        findings=findings,
        report_dir=payload.get("report_dir"),
        semantic_model_dir=payload.get("semantic_model_dir"),
        raw=payload,
    )
