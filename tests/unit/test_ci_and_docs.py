"""Unit tests validating GitHub Actions CI workflow, ADRs, portfolio docs, and relative links."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from bi_agent.config import find_repo_root

VENDORED_SKILLS = (
    "connect-pbid",
    "create-pbi-report",
    "dax",
    "lineage-analysis",
    "modifying-theme-json",
    "organize-semantic-model",
    "pbi-report-design",
    "pbip",
    "pbir-cli",
    "pbir-format",
    "power-query",
    "refresh-semantic-model",
    "review-report",
    "semantic-model",
    "tmdl",
)

AUTHORED_SKILLS = (
    "bi-dimensional-modeling",
    "bi-dax-and-rls-engineering",
)


def test_github_actions_ci_workflow_is_valid() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    workflow_path = repo_root / ".github" / "workflows" / "bi-ci.yml"
    assert workflow_path.is_file()

    raw_text = workflow_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw_text)
    assert isinstance(data, dict)
    assert "on" in data or True in data

    jobs = data.get("jobs", {})
    assert "python-lint-and-unit" in jobs
    assert "bi-offline-gates" in jobs
    assert jobs["python-lint-and-unit"]["runs-on"] == "ubuntu-latest"
    assert jobs["bi-offline-gates"]["runs-on"] == "windows-latest"

    for expected_cmd in (
        "ruff check",
        "pytest tests/unit",
        "pytest tests/fault_injection",
        "REQ-001-regional-sales-margin.yaml --offline",
        "REQ-002-customer-discount-analytics.yaml --offline",
    ):
        assert expected_cmd in raw_text


def test_adrs_readme_and_walkthrough_exist_and_cross_link() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    readme_text = (repo_root / "README.md").read_text(encoding="utf-8")
    ops_path = repo_root / "docs" / "OPERATIONS_AND_CLI.md"
    blueprint_path = repo_root / "docs" / "PRODUCTIONISATION_BLUEPRINT.md"
    license_path = repo_root / "LICENSE"
    gitattributes_text = (repo_root / ".gitattributes").read_text(encoding="utf-8")
    walkthrough_text = (repo_root / "docs" / "WALKTHROUGH.md").read_text(encoding="utf-8")
    adr_index = (repo_root / "docs" / "adr" / "README.md").read_text(encoding="utf-8")

    expected_badge = (
        "https://img.shields.io/github/actions/workflow/status/"
        "Shivansh9307/autonomous-bi-engineering-agent/bi-ci.yml?branch=main&label=BI%20CI"
    )
    assert expected_badge in readme_text
    for expected_ref in (
        "docs/PRODUCTIONISATION_BLUEPRINT.md",
        "docs/OPERATIONS_AND_CLI.md",
        "docs/WALKTHROUGH.md",
        "docs/adr/README.md",
        "docs/PLAN.md",
        "docs/PHASE_STATE.md",
        "docs/images/executive-overview.png",
        "docs/images/regional-margin-analysis.png",
        "docs/images/product-detail.png",
        "docs/images/customer-discounts.png",
        "$48.74M",
        "33.80%",
        "-$646.56K",
        "```mermaid",
    ):
        assert expected_ref in readme_text

    assert ops_path.is_file()
    ops_text = ops_path.read_text(encoding="utf-8")
    assert "7-Gate Verification Matrix" in ops_text
    assert "Authored IP vs. Vendored Skills" in ops_text
    assert "Finance Modeling Safeguards" in ops_text

    assert blueprint_path.is_file()
    blueprint_text = blueprint_path.read_text(encoding="utf-8")
    for expected_token in (
        "```mermaid",
        "DataFolder",
        "EffectiveUserName",
        "ADR-0006",
        "USERPRINCIPALNAME",
    ):
        assert expected_token in blueprint_text

    assert license_path.is_file()
    license_text = license_path.read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Shivansh Chauhan" in license_text

    for skill in VENDORED_SKILLS:
        assert f"/.agents/skills/{skill}/** linguist-vendored" in gitattributes_text
    for authored_skill in AUTHORED_SKILLS:
        assert authored_skill not in gitattributes_text

    assert "REQ-002" in walkthrough_text

    adr_files = [
        "0001-hybrid-agent-and-deterministic-verification-harness.md",
        "0002-strict-tool-cascade-and-pbir-cli-exclusivity.md",
        "0003-empirical-dax-reconciliation-and-fail-closed-rls.md",
        "0004-evidence-gated-git-commits-via-staged-tree-sha.md",
        "0005-offline-ci-versus-live-desktop-verification-split.md",
        "0006-customdata-rls-testing-shim-and-service-hardening.md",
    ]
    for filename in adr_files:
        adr_path = repo_root / "docs" / "adr" / filename
        assert adr_path.is_file(), f"Missing ADR: {adr_path}"
        content = adr_path.read_text(encoding="utf-8")
        assert "## Context" in content
        assert "## Decision" in content
        assert "## Consequences" in content
        assert filename in adr_index


def test_offline_relative_markdown_links_resolve() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    md_files = [repo_root / "README.md", *sorted((repo_root / "docs").rglob("*.md"))]
    link_pattern = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
    fenced_code_pattern = re.compile(r"```.*?```", flags=re.DOTALL)

    broken: list[str] = []
    for md_path in md_files:
        raw_text = md_path.read_text(encoding="utf-8")
        cleaned = fenced_code_pattern.sub("", raw_text)
        for match in link_pattern.finditer(cleaned):
            target = match.group(1).strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            rel_target = target.split("#", 1)[0].strip()
            if not rel_target:
                continue
            resolved = (md_path.parent / rel_target).resolve()
            if not resolved.exists():
                broken.append(f"{md_path.relative_to(repo_root)} -> {target}")

    assert not broken, "Broken relative Markdown link(s):\n" + "\n".join(broken)
