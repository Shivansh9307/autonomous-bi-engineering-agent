"""Unit tests for enterprise BPA ruleset formatting and TE2 output parser."""

from __future__ import annotations

import json
from pathlib import Path

from bi_agent.adapters.tabular_editor_adapter import parse_bpa_output
from bi_agent.config import find_repo_root


def test_enterprise_bpa_rules_crlf_and_schema_hygiene() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    rules_path = repo_root / "config" / "bpa" / "enterprise-rules.json"
    raw_bytes = rules_path.read_bytes()

    assert b"\r\n" in raw_bytes
    stripped_crlf = raw_bytes.replace(b"\r\n", b"")
    assert b"\n" not in stripped_crlf

    rules = json.loads(raw_bytes.decode("utf-8"))
    assert isinstance(rules, list)
    assert len(rules) == 15

    ids: set[str] = set()
    allowed_fields = {
        "ID",
        "Name",
        "Category",
        "Description",
        "Severity",
        "Scope",
        "Expression",
        "FixExpression",
        "CompatibilityLevel",
        "Source",
        "Remarks",
    }
    for rule in rules:
        assert "_comment" not in rule
        assert set(rule.keys()) <= allowed_fields
        for val in rule.values():
            assert val is not None
        assert rule["ID"] not in ids
        ids.add(rule["ID"])
        assert rule["Severity"] in (1, 2, 3)


def test_parse_bpa_output_github_and_standard_formats() -> None:
    rules_lookup = {
        "ERR_MEASURE_NO_FORMAT_STRING": {"ID": "ERR_MEASURE_NO_FORMAT_STRING", "Severity": 3},
        "Visible measure missing description": {
            "ID": "META_MEASURE_NO_DESCRIPTION",
            "Severity": 2,
        },
    }
    stdout = "\n".join(
        [
            "::error ::[Total Revenue] (Measure) violates rule "
            "[ERR_MEASURE_NO_FORMAT_STRING] Visible measure missing format string",
            "Rule 'Visible measure missing description' violated by [Gross Margin] (Measure).",
        ]
    )
    violations = parse_bpa_output(stdout, "", rules_by_name=rules_lookup)
    assert len(violations) == 2
    assert violations[0].severity == "error"
    assert violations[0].rule_id == "ERR_MEASURE_NO_FORMAT_STRING"
    assert violations[0].object_name == "[Total Revenue]"
    assert violations[1].severity == "warning"
    assert violations[1].rule_id == "META_MEASURE_NO_DESCRIPTION"
    assert violations[1].object_name == "[Gross Margin]"
