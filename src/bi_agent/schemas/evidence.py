"""Verification evidence bundle and gate result schemas."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

GateStatus = Literal["PASS", "FAIL", "SKIP", "WARN"]


def to_repo_relative_posix(raw_path: str, repo_root: Path | None = None) -> str:
    """Normalize a local filesystem path into a repo-relative POSIX path when possible."""
    if not raw_path:
        return raw_path
    norm_raw = raw_path.replace("\\", "/")
    if repo_root is not None:
        norm_root = str(repo_root).replace("\\", "/").rstrip("/")
        if norm_raw.lower().startswith(norm_root.lower() + "/"):
            return norm_raw[len(norm_root) + 1 :]
        if norm_raw.lower() == norm_root.lower():
            return "."
        try:
            return Path(raw_path).resolve().relative_to(repo_root.resolve()).as_posix()
        except (ValueError, OSError):
            pass
    return norm_raw


def _fmt_ms(val: Any) -> str:
    return f"{float(val):.1f}ms" if val is not None else "N/A"


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

    def write_markdown(self, path: Path, repo_root: Path | None = None) -> Path:
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
            perf = gate.metrics.get("performance") if gate.gate_id == "G4" else None
            has_g4_perf = (
                isinstance(perf, dict)
                and not perf.get("benchmark_skipped", False)
                and any(
                    perf.get(k) is not None
                    for k in ("cold_avg_ms", "warm_avg_ms", "cold_max_ms", "warm_max_ms")
                )
            )
            matrix = gate.metrics.get("matrix") if gate.gate_id == "G5" else None
            has_g5_matrix = isinstance(matrix, list) and len(matrix) > 0

            if gate.findings or gate.artifacts or has_g4_perf or has_g5_matrix:
                lines.append("")
                lines.append(f"## {gate.gate_id}: {gate.name}")
                if has_g4_perf and isinstance(perf, dict):
                    cold_budget = perf.get("cold_budget_ms", 1500)
                    warm_budget = perf.get("warm_budget_ms", 500)
                    lines.append(
                        f"- **DAX Benchmark Latency vs. SLA:** "
                        f"Cold `avg={_fmt_ms(perf.get('cold_avg_ms'))}`, "
                        f"`max_cold_ms={_fmt_ms(perf.get('cold_max_ms'))}` "
                        f"(SLA `<= {cold_budget}ms`) | "
                        f"Warm `avg={_fmt_ms(perf.get('warm_avg_ms'))}`, "
                        f"`max_warm_ms={_fmt_ms(perf.get('warm_max_ms'))}` "
                        f"(SLA `<= {warm_budget}ms`)"
                    )
                if has_g5_matrix and isinstance(matrix, list):
                    fact_columns: list[str] = []
                    raw_fact_tables = gate.metrics.get("fact_tables")
                    if isinstance(raw_fact_tables, list) and raw_fact_tables:
                        fact_columns = [str(item) for item in raw_fact_tables]
                    else:
                        seen_facts: set[str] = set()
                        for entry in matrix:
                            if not isinstance(entry, dict):
                                continue
                            fc = entry.get("fact_counts")
                            if isinstance(fc, dict):
                                for fact_name in fc:
                                    s_name = str(fact_name)
                                    if s_name not in seen_facts:
                                        seen_facts.add(s_name)
                                        fact_columns.append(s_name)

                    lines.append("")
                    if fact_columns:
                        header_cols = " | ".join(f"{fc} Rows" for fc in fact_columns)
                        sep_cols = " | ".join("---:" for _ in fact_columns)
                        lines.append(f"| Role | Principal | {header_cols} | Status |")
                        lines.append(f"| :--- | :--- | {sep_cols} | :---: |")
                        for entry in matrix:
                            if not isinstance(entry, dict):
                                continue
                            role = entry.get("role", "")
                            principal = entry.get("principal") or "(static)"
                            fc = entry.get("fact_counts")
                            fc_map = fc if isinstance(fc, dict) else {}
                            row_cells: list[str] = []
                            for col_name in fact_columns:
                                val = fc_map.get(col_name)
                                row_cells.append(f"{int(val):,}" if val is not None else "N/A")
                            status = entry.get("status", "")
                            lines.append(
                                f"| {role} | {principal} | {' | '.join(row_cells)} | `{status}` |"
                            )
                    else:
                        lines.append(
                            "| Role | Principal | Primary Fact Rows "
                            "| Secondary Fact Rows | Status |"
                        )
                        lines.append("| :--- | :--- | ---: | ---: | :---: |")
                        for entry in matrix:
                            if not isinstance(entry, dict):
                                continue
                            role = entry.get("role", "")
                            principal = entry.get("principal") or "(static)"
                            primary_val = entry.get("actual_rows")
                            primary_str = (
                                f"{int(primary_val):,}" if primary_val is not None else "N/A"
                            )
                            secondary_val = entry.get("actual_budget_rows")
                            secondary_str = (
                                f"{int(secondary_val):,}" if secondary_val is not None else "N/A"
                            )
                            status = entry.get("status", "")
                            lines.append(
                                f"| {role} | {principal} | {primary_str} | "
                                f"{secondary_str} | `{status}` |"
                            )
                for finding in gate.findings:
                    loc_str = (
                        to_repo_relative_posix(finding.location, repo_root)
                        if finding.location
                        else ""
                    )
                    loc = f" (`{loc_str}`)" if loc_str else ""
                    lines.append(
                        f"- **[{finding.severity.upper()}]** `{finding.code}`: "
                        f"{finding.message}{loc}"
                    )
                if gate.artifacts:
                    rel_arts = [
                        to_repo_relative_posix(art, repo_root) for art in gate.artifacts
                    ]
                    lines.append(
                        "- **Artifacts:** " + ", ".join(f"`{art}`" for art in rel_arts)
                    )

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    @classmethod
    def load_json(cls, path: Path) -> VerificationEvidenceBundle:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.model_validate(raw)
