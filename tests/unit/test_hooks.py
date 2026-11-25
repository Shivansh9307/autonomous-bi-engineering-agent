"""Unit tests for Antigravity PreToolUse guardrail hooks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bi_agent.adapters import git_adapter
from bi_agent.hooks.pre_tool_guard import evaluate_pre_tool_use
from bi_agent.schemas.evidence import GateResult, VerificationEvidenceBundle


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def test_pre_tool_guard_blocks_direct_pbir_json_edits() -> None:
    pbir_visual = (
        r"C:\repo\pbi\EnterpriseSales\EnterpriseSales.Report"
        r"\definition\pages\Overview\visuals\v1\visual.json"
    )
    deny_write = evaluate_pre_tool_use(
        {"toolCall": {"name": "write_file", "args": {"Path": pbir_visual}}}
    )
    assert deny_write["decision"] == "deny"

    deny_edit = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": "pbi/Sales.Report/definition/report.json"},
            }
        }
    )
    assert deny_edit["decision"] == "deny"

    allow_python = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "write_file",
                "args": {"Path": "src/bi_agent/cli.py"},
            }
        }
    )
    assert allow_python["decision"] == "allow"

    tmdl_path = (
        "pbi/EnterpriseSales/EnterpriseSales.SemanticModel/definition/model.tmdl"
    )
    allow_tmdl = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "edit_file",
                "args": {"Path": tmdl_path},
            }
        }
    )
    assert allow_tmdl["decision"] == "allow"


def test_pre_tool_guard_blocks_unverified_pbi_git_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@local.dev")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore: init")

    # Non-PBI staged commit should be allowed cleanly
    (repo / "notes.txt").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    non_pbi = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'docs: notes'"},
            }
        },
        repo_root=repo,
    )
    assert non_pbi["decision"] == "allow"

    # Stage pbi/ change without evidence -> raw git commit must be denied
    pbi_dir = repo / "pbi" / "Demo"
    pbi_dir.mkdir(parents=True)
    (pbi_dir / "Demo.pbip").write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "pbi/Demo/Demo.pbip")

    unverified = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: demo'"},
            }
        },
        repo_root=repo,
    )
    assert unverified["decision"] == "deny"

    # Add matching VerificationEvidenceBundle -> raw git commit now allowed
    tree_sha = git_adapter.staged_tree_sha(repo)
    bundle = VerificationEvidenceBundle(
        req_id="REQ-001",
        git_branch="main",
        git_tree_sha=tree_sha,
        overall_status="PASS",
        gates=[GateResult(gate_id="G1", name="Design", status="PASS")],
    )
    bundle.write_json(repo / "artifacts" / "runs" / "REQ-001" / "verification-report.json")

    verified = evaluate_pre_tool_use(
        {
            "toolCall": {
                "name": "run_command",
                "args": {"CommandLine": "git commit -m 'feat: demo'"},
            }
        },
        repo_root=repo,
    )
    assert verified["decision"] == "allow"
