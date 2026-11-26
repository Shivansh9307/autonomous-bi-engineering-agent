"""Gate G0 schemas for raw flat-file profiling, defect detection, and staging reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

DefectCategory = Literal[
    "duplicate_rows",
    "duplicate_keys",
    "mixed_date_formats",
    "stringified_numeric",
    "whitespace_casing",
    "null_or_orphan_fk",
    "negative_credit_note",
    "grain_mismatch",
]


class ColumnProfile(BaseModel):
    name: str
    inferred_type: Literal["integer", "decimal", "date", "string", "boolean", "empty"] = "string"
    non_null_count: int = 0
    null_count: int = 0
    distinct_count: int = 0
    sample_values: list[str] = Field(default_factory=list)
    detected_date_formats: list[str] = Field(default_factory=list)
    stringified_numeric_count: int = 0
    whitespace_defect_count: int = 0
    casing_variant_count: int = 0
    negative_value_count: int = 0


class DefectFinding(BaseModel):
    category: DefectCategory
    file: str
    column: str | None = None
    count: int = 0
    severity: Literal["error", "warn", "info"] = "warn"
    summary: str
    samples: list[str] = Field(default_factory=list)


class PolicyQuestion(BaseModel):
    policy_key: str
    category: DefectCategory
    question: str
    affected_files: list[str] = Field(default_factory=list)
    recommended_default: str = ""


class TableDecompositionProposal(BaseModel):
    source_file: str
    proposed_role: Literal["fact", "dimension", "date_dimension"]
    proposed_table_name: str
    candidate_keys: list[str] = Field(default_factory=list)
    candidate_measures: list[str] = Field(default_factory=list)
    candidate_date_columns: list[str] = Field(default_factory=list)
    candidate_attributes: list[str] = Field(default_factory=list)


class FileProfile(BaseModel):
    file_name: str
    path: str
    delimiter: str = ","
    row_count: int = 0
    column_count: int = 0
    exact_duplicate_row_count: int = 0
    columns: list[ColumnProfile] = Field(default_factory=list)
    defects: list[DefectFinding] = Field(default_factory=list)


class TableReconciliationResult(BaseModel):
    table_name: str
    raw_file: str
    staged_file: str
    quarantine_file: str | None = None
    raw_row_count: int = 0
    staged_row_count: int = 0
    quarantined_row_count: int = 0
    deduplicated_row_count: int = 0
    row_reconciled: bool = False
    amount_reconciliations: dict[str, dict[str, Any]] = Field(default_factory=dict)
    staged_defects: list[str] = Field(default_factory=list)
    orphan_fk_counts: dict[str, int] = Field(default_factory=dict)
    passed: bool = False


class DataProfileReport(BaseModel):
    schema_version: str = "1.0"
    req_id: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_path: str
    files: list[FileProfile] = Field(default_factory=list)
    defects: list[DefectFinding] = Field(default_factory=list)
    proposed_decomposition: list[TableDecompositionProposal] = Field(default_factory=list)
    policy_questions: list[PolicyQuestion] = Field(default_factory=list)
    reconciliations: list[TableReconciliationResult] = Field(default_factory=list)

    def write_json(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def write_markdown(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        title_suffix = f" ({self.req_id})" if self.req_id else ""
        lines: list[str] = [
            f"# Raw Data Profile & Reconciliation Report{title_suffix}",
            "",
            f"- **Source Path:** `{self.source_path}`",
            f"- **Generated At:** `{self.generated_at}`",
            f"- **Files Profiled:** `{len(self.files)}`",
            f"- **Defects Detected:** `{len(self.defects)}`",
            "",
            "## 1. Schema Summary",
            "",
            "| File | Delimiter | Rows | Columns | Exact Duplicate Rows | Defects |",
            "| :--- | :---: | ---: | ---: | ---: | ---: |",
        ]
        for fp in self.files:
            delim_label = "TAB" if fp.delimiter == "\t" else fp.delimiter
            lines.append(
                f"| `{fp.file_name}` | `{delim_label}` | {fp.row_count:,} | "
                f"{fp.column_count} | {fp.exact_duplicate_row_count:,} | {len(fp.defects)} |"
            )

        if self.defects:
            lines.extend(
                [
                    "",
                    "## 2. Detected Data Quality Defects",
                    "",
                    "| Severity | Category | File | Column | Count | Summary |",
                    "| :---: | :--- | :--- | :--- | ---: | :--- |",
                ]
            )
            for defect in self.defects:
                col_str = f"`{defect.column}`" if defect.column else "-"
                lines.append(
                    f"| `{defect.severity.upper()}` | `{defect.category}` | "
                    f"`{defect.file}` | {col_str} | {defect.count:,} | {defect.summary} |"
                )

        if self.proposed_decomposition:
            lines.extend(
                [
                    "",
                    "## 3. Proposed Kimball Star-Schema Decomposition",
                    "",
                    "| Source File | Proposed Role | Table Name "
                    "| Candidate Keys | Measures | Dates |",
                    "| :--- | :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for prop in self.proposed_decomposition:
                keys_str = ", ".join(prop.candidate_keys) or "-"
                meas_str = ", ".join(prop.candidate_measures) or "-"
                date_str = ", ".join(prop.candidate_date_columns) or "-"
                lines.append(
                    f"| `{prop.source_file}` | `{prop.proposed_role}` | "
                    f"`{prop.proposed_table_name}` | {keys_str} | {meas_str} | {date_str} |"
                )

        if self.policy_questions:
            lines.extend(
                [
                    "",
                    "## 4. Human-in-the-Loop Policy Questions",
                    "",
                    "| Policy Key | Category | Affected Files | Question | Recommended Default |",
                    "| :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for q in self.policy_questions:
                files_str = ", ".join(f"`{f}`" for f in q.affected_files) or "-"
                lines.append(
                    f"| `{q.policy_key}` | `{q.category}` | {files_str} | "
                    f"{q.question} | {q.recommended_default} |"
                )

        if self.reconciliations:
            lines.extend(
                [
                    "",
                    "## 5. Raw-to-Staged Reconciliation",
                    "",
                    "| Table | Raw Rows | Staged Rows | Quarantine Rows | Row Recon | Status |",
                    "| :--- | ---: | ---: | ---: | :---: | :---: |",
                ]
            )
            for rec in self.reconciliations:
                status_badge = "`PASS`" if rec.passed else "`FAIL`"
                row_badge = "`OK`" if rec.row_reconciled else "`MISMATCH`"
                lines.append(
                    f"| `{rec.table_name}` | {rec.raw_row_count:,} | "
                    f"{rec.staged_row_count:,} | {rec.quarantined_row_count:,} | "
                    f"{row_badge} | {status_badge} |"
                )
            for rec in self.reconciliations:
                if rec.amount_reconciliations:
                    lines.append("")
                    lines.append(f"### Financial Reconciliation: `{rec.table_name}`")
                    lines.append("")
                    lines.append(
                        "| Column | Raw Total | Staged Total | Quarantine Total | Diff | Status |"
                    )
                    lines.append("| :--- | ---: | ---: | ---: | ---: | :---: |")
                    for col_name, info in rec.amount_reconciliations.items():
                        col_status = "`PASS`" if info.get("reconciled") else "`FAIL`"
                        lines.append(
                            f"| `{col_name}` | {info.get('raw_total')} | "
                            f"{info.get('staged_total')} | {info.get('quarantine_total')} | "
                            f"{info.get('diff')} | {col_status} |"
                        )

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
