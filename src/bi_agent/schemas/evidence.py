"""Verification evidence bundle and gate result schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

GateStatus = Literal["PASS", "FAIL", "SKIP", "WARN"]


class GateFinding(BaseModel):
    severity: Literal["error", "warn", "info"] = "info"
    code: str
    message: str
    location: str | None = None


class GateResult(BaseModel):
    gate_id: str
    name: str
    status: GateStatus
    duration_ms: int = 0
    summary: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    findings: list[GateFinding] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)


class VerificationEvidenceBundle(BaseModel):
    schema_version: str = "1.0"
    req_id: str
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    git_branch: str = ""
    git_tree_sha: str = ""
    overall_status: Literal["PASS", "FAIL"] = "FAIL"
    gates: list[GateResult] = Field(default_factory=list)

    def is_passing(self) -> bool:
        return self.overall_status == "PASS" and all(
            g.status in ("PASS", "WARN", "SKIP") for g in self.gates
        )

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def write_markdown(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# Verification Evidence Report ({self.req_id})",
            "",
            f"- **Overall Status:** `{self.overall_status}`",
            f"- **Generated At:** `{self.generated_at}`",
            f"- **Git Branch:** `{self.git_branch or 'unknown'}`",
            f"- **Staged Tree SHA:** `{self.git_tree_sha or 'none'}`",
            "",
            "| Gate | Name | Status | Duration (ms) | Summary |",
            "| :--- | :--- | :---: | ---: | :--- |",
        ]
        for gate in self.gates:
            lines.append(
                f"| {gate.gate_id} | {gate.name} | `{gate.status}` | "
                f"{gate.duration_ms} | {gate.summary} |"
            )

        for gate in self.gates:
            if gate.findings or gate.artifacts:
                lines.append("")
                lines.append(f"## {gate.gate_id}: {gate.name}")
                for finding in gate.findings:
                    loc = f" (`{finding.location}`)" if finding.location else ""
                    lines.append(
                        f"- **[{finding.severity.upper()}]** `{finding.code}`: "
                        f"{finding.message}{loc}"
                    )
                if gate.artifacts:
                    lines.append(
                        "- **Artifacts:** " + ", ".join(f"`{art}`" for art in gate.artifacts)
                    )

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load_json(cls, path: Path) -> VerificationEvidenceBundle:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
