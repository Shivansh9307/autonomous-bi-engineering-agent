"""Adapter for Tabular Editor 2 CLI (TMDL load, C# scripting, and BPA analysis)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from bi_agent.adapters._subprocess import CommandResult, resolve_tabular_editor, run_command
from bi_agent.config import ToolchainConfig

GHA_ANNOTATION_RE = re.compile(r"^::(error|warning|notice)(?:\s+[^:]*)?::(.+)$", re.IGNORECASE)
VIOLATES_RULE_RE = re.compile(
    r"^(?P<obj>.+?)\s+\((?P<type>[^)]+)\)\s+violates rule\s+\[(?P<id>[^\]]+)\]\s*(?P<name>.*)$",
    re.IGNORECASE,
)
RULE_VIOLATED_BY_RE = re.compile(
    r"^Rule\s+'(?P<name>[^']+)'\s+(?:\((?P<id>[^)]+)\)\s+)?"
    r"violated by\s+(?P<obj>.+?)\s*(?:\((?P<type>[^)]+)\))?\.?$",
    re.IGNORECASE,
)


class BpaViolation(BaseModel):
    rule_id: str = ""
    rule_name: str
    severity: Literal["error", "warning", "info"] = "warning"
    object_name: str = ""
    object_type: str = ""
    message: str


class BpaResult(BaseModel):
    ok: bool
    exit_code: int
    violations: list[BpaViolation] = Field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    raw_output: str = ""


class TmdlCheckResult(BaseModel):
    ok: bool
    exit_code: int
    table_count: int | None = None
    message: str = ""
    raw_output: str = ""


def _severity_from_int(
    value: int | None, fallback: str = "warning"
) -> Literal["error", "warning", "info"]:
    if value == 3:
        return "error"
    if value == 2:
        return "warning"
    if value == 1:
        return "info"
    lowered = fallback.lower()
    if lowered == "error":
        return "error"
    if lowered == "notice" or lowered == "info":
        return "info"
    return "warning"


def _load_rules_index(rules_path: Path) -> dict[str, dict]:
    try:
        items = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    index: dict[str, dict] = {}
    if isinstance(items, list):
        for rule in items:
            if not isinstance(rule, dict):
                continue
            if rule.get("ID"):
                index[str(rule["ID"])] = rule
            if rule.get("Name"):
                index[str(rule["Name"])] = rule
    return index


def parse_bpa_output(
    stdout: str, stderr: str = "", rules_by_name: dict[str, dict] | None = None
) -> list[BpaViolation]:
    """Parse Tabular Editor 2 `-A` (default or `-G`) BPA output into structured violations."""
    lookup = rules_by_name or {}
    violations: list[BpaViolation] = []
    combined_lines = (stdout + "\n" + stderr).splitlines()

    for raw_line in combined_lines:
        line = raw_line.strip()
        if not line:
            continue

        fallback_sev = "warning"
        body = line
        gha_match = GHA_ANNOTATION_RE.match(line)
        if gha_match:
            fallback_sev = gha_match.group(1).lower()
            body = gha_match.group(2).strip()
        elif line.lower().startswith("error:"):
            fallback_sev = "error"
            body = line[6:].strip()
        elif line.lower().startswith("warning:"):
            fallback_sev = "warning"
            body = line[8:].strip()
        elif "violates rule" not in line.lower() and "violated by" not in line.lower():
            continue

        m1 = VIOLATES_RULE_RE.match(body)
        if m1:
            rule_id = m1.group("id").strip()
            rule_name = m1.group("name").strip() or rule_id
            meta = lookup.get(rule_id) or lookup.get(rule_name) or {}
            sev = _severity_from_int(meta.get("Severity"), fallback_sev)
            violations.append(
                BpaViolation(
                    rule_id=rule_id or str(meta.get("ID", "")),
                    rule_name=rule_name,
                    severity=sev,
                    object_name=m1.group("obj").strip(),
                    object_type=m1.group("type").strip(),
                    message=body,
                )
            )
            continue

        m2 = RULE_VIOLATED_BY_RE.match(body)
        if m2:
            rule_name = m2.group("name").strip()
            rule_id = (m2.group("id") or "").strip()
            meta = lookup.get(rule_id) or lookup.get(rule_name) or {}
            sev = _severity_from_int(meta.get("Severity"), fallback_sev)
            violations.append(
                BpaViolation(
                    rule_id=rule_id or str(meta.get("ID", "")),
                    rule_name=rule_name,
                    severity=sev,
                    object_name=m2.group("obj").strip(),
                    object_type=(m2.group("type") or "").strip(),
                    message=body,
                )
            )
            continue

        if gha_match:
            violations.append(
                BpaViolation(
                    rule_id="",
                    rule_name=body,
                    severity=_severity_from_int(None, fallback_sev),
                    message=body,
                )
            )

    return violations


def check_tmdl_load(definition_dir: Path, config: ToolchainConfig) -> TmdlCheckResult:
    """Verify a SemanticModel/definition folder parses cleanly in Tabular Editor 2."""
    te_bin = resolve_tabular_editor(config)
    inline_script = 'Info("TMDL_OK:" + Model.Tables.Count);'
    res = run_command(
        [te_bin, str(definition_dir.resolve()), "-S", inline_script],
        timeout=config.timeouts_seconds.tabular_editor,
    )
    combined = res.combined_output
    match = re.search(r"TMDL_OK:(\d+)", combined)
    table_count = int(match.group(1)) if match else None
    ok = res.returncode == 0 and table_count is not None
    return TmdlCheckResult(
        ok=ok,
        exit_code=res.returncode,
        table_count=table_count,
        message=f"Loaded {table_count} table(s)" if ok else (combined or "TMDL load failed"),
        raw_output=combined,
    )


def run_bpa(
    definition_dir: Path,
    rules_path: Path,
    config: ToolchainConfig,
) -> BpaResult:
    """Execute Tabular Editor 2 Best Practice Analyzer with GitHub-format annotations."""
    te_bin = resolve_tabular_editor(config)
    abs_rules = rules_path.resolve()
    rules_index = _load_rules_index(abs_rules)

    res = run_command(
        [te_bin, str(definition_dir.resolve()), "-A", str(abs_rules), "-G"],
        timeout=config.timeouts_seconds.tabular_editor,
    )
    violations = parse_bpa_output(res.stdout, res.stderr, rules_index)
    error_count = sum(1 for v in violations if v.severity == "error")
    warning_count = sum(1 for v in violations if v.severity == "warning")
    ok = error_count == 0 and res.returncode == 0
    return BpaResult(
        ok=ok,
        exit_code=res.returncode,
        violations=violations,
        error_count=error_count,
        warning_count=warning_count,
        raw_output=res.combined_output,
    )


def run_script(
    definition_dir: Path,
    script_path: Path,
    config: ToolchainConfig,
    save_tmdl_dir: Path | None = None,
) -> CommandResult:
    """Execute a Tabular Editor 2 C# script (.csx) against a TMDL definition folder."""
    te_bin = resolve_tabular_editor(config)
    cmd = [te_bin, str(definition_dir.resolve()), "-S", str(script_path.resolve())]
    if save_tmdl_dir is not None:
        cmd.extend(["-TMDL", str(save_tmdl_dir.resolve())])
    return run_command(cmd, timeout=config.timeouts_seconds.tabular_editor)
