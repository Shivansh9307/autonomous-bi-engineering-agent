"""Unit tests validating Phase 6 GitHub Actions CI workflow, ADRs, and documentation."""

from __future__ import annotations

from pathlib import Path

import yaml

from bi_agent.config import find_repo_root


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
    walkthrough_text = (repo_root / "docs" / "WALKTHROUGH.md").read_text(encoding="utf-8")
    adr_index = (repo_root / "docs" / "adr" / "README.md").read_text(encoding="utf-8")

    assert "7-Gate Verification Matrix" in readme_text
    assert "docs/WALKTHROUGH.md" in readme_text
    assert "docs/adr/README.md" in readme_text
    assert "REQ-002" in walkthrough_text

    adr_files = [
        "0001-hybrid-agent-and-deterministic-verification-harness.md",
        "0002-strict-tool-cascade-and-pbir-cli-exclusivity.md",
        "0003-empirical-dax-reconciliation-and-fail-closed-rls.md",
        "0004-evidence-gated-git-commits-via-staged-tree-sha.md",
        "0005-offline-ci-versus-live-desktop-verification-split.md",
    ]
    for filename in adr_files:
        adr_path = repo_root / "docs" / "adr" / filename
        assert adr_path.is_file(), f"Missing ADR: {adr_path}"
        content = adr_path.read_text(encoding="utf-8")
        assert "## Context" in content
        assert "## Decision" in content
        assert "## Consequences" in content
        assert filename in adr_index
