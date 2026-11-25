"""Gate G7: Git branch hygiene, staged tree SHA verification, and evidence binding."""

from __future__ import annotations

import time
from pathlib import Path

from bi_agent.adapters import git_adapter
from bi_agent.schemas.evidence import GateFinding, GateResult


def run_gate_g7(
    repo_root: Path,
    evidence_path: Path | None = None,
    expected_tree_sha: str | None = None,
    require_feature_branch: bool = False,
) -> GateResult:
    """Validate Git governance, branch protection, and staged tree SHA binding."""
    start = time.perf_counter()
    branch = git_adapter.current_branch(repo_root)
    staged = git_adapter.staged_files(repo_root)
    tree_sha = git_adapter.staged_tree_sha(repo_root)
    pbi_staged = git_adapter.has_pbi_staged(repo_root)

    findings: list[GateFinding] = []

    if not branch:
        findings.append(
            GateFinding(
                severity="error",
                code="G7_NOT_GIT_REPO",
                message="Unable to resolve active Git branch.",
                location=str(repo_root),
            )
        )
    elif require_feature_branch and pbi_staged and branch in ("main", "master"):
        findings.append(
            GateFinding(
                severity="error",
                code="G7_PROTECTED_BRANCH",
                message=f"PBI changes staged directly on protected branch '{branch}'.",
                location=branch,
            )
        )

    if not tree_sha:
        findings.append(
            GateFinding(
                severity="error",
                code="G7_NO_TREE_SHA",
                message="Failed to compute Git index tree SHA via `git write-tree`.",
            )
        )

    if expected_tree_sha is not None and tree_sha != expected_tree_sha:
        findings.append(
            GateFinding(
                severity="error",
                code="G7_TREE_SHA_MISMATCH",
                message=f"Staged tree SHA '{tree_sha}' != expected '{expected_tree_sha}'.",
            )
        )

    if evidence_path is not None:
        ok, detail = git_adapter.verify_evidence_matches_index(repo_root, evidence_path)
        if not ok:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G7_EVIDENCE_MISMATCH",
                    message=detail,
                    location=str(evidence_path),
                )
            )

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = not any(f.severity == "error" for f in findings)
    return GateResult(
        gate_id="G7",
        name="Git Governance & Tree Binding",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"Branch '{branch}', tree={tree_sha[:10]}, staged={len(staged)} file(s)"
            if passed
            else f"{len(findings)} Git governance error(s)"
        ),
        metrics={
            "branch": branch,
            "staged_tree_sha": tree_sha,
            "staged_file_count": len(staged),
            "pbi_staged": pbi_staged,
        },
        findings=findings,
    )
