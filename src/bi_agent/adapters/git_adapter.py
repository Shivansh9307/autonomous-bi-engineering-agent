"""Adapter for Git tree-SHA evidence binding and conventional commits."""

from __future__ import annotations

import re
from pathlib import Path

from bi_agent.adapters._subprocess import CommandResult, run_command
from bi_agent.schemas.evidence import VerificationEvidenceBundle

CONVENTIONAL_COMMIT_RE = re.compile(
    r"^(feat|fix|chore|docs|refactor|test|ci|build|perf)(\([a-zA-Z0-9_.\-]+\))?: .+"
)


def current_branch(repo_root: Path) -> str:
    res = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, timeout=15)
    return res.stdout.strip() if res.returncode == 0 else ""


def staged_files(repo_root: Path) -> list[str]:
    res = run_command(["git", "diff", "--cached", "--name-only"], cwd=repo_root, timeout=15)
    if res.returncode != 0 or not res.stdout:
        return []
    return [line.strip().replace("\\", "/") for line in res.stdout.splitlines() if line.strip()]


def staged_tree_sha(repo_root: Path) -> str:
    res = run_command(["git", "write-tree"], cwd=repo_root, timeout=15)
    return res.stdout.strip() if res.returncode == 0 else ""


def is_pbi_path(path_str: str) -> bool:
    norm = path_str.replace("\\", "/").lstrip("./")
    lower = norm.lower()
    if lower.startswith("pbi/") or "/pbi/" in lower:
        return True
    return Path(lower).suffix in {".pbip", ".tmdl", ".pbir", ".pbism"}


def has_pbi_staged(repo_root: Path) -> bool:
    return any(is_pbi_path(p) for p in staged_files(repo_root))


def is_conventional_commit(message: str) -> bool:
    first_line = message.splitlines()[0].strip() if message else ""
    return bool(CONVENTIONAL_COMMIT_RE.match(first_line))


def verify_evidence_matches_index(repo_root: Path, evidence_path: Path) -> tuple[bool, str]:
    """Ensure evidence bundle exists, passed all gates, and matches the staged Git tree SHA."""
    if not evidence_path.is_file():
        return False, f"Verification evidence bundle not found: {evidence_path}"
    try:
        bundle = VerificationEvidenceBundle.load_json(evidence_path)
    except Exception as exc:
        return False, f"Failed to parse verification evidence bundle: {exc}"

    if not bundle.is_passing():
        return False, f"Evidence bundle overall_status is '{bundle.overall_status}' (must be PASS)."

    tree_sha = staged_tree_sha(repo_root)
    if not tree_sha:
        return False, "Unable to compute staged Git tree SHA (git write-tree failed)."

    if bundle.git_tree_sha != tree_sha:
        return (
            False,
            f"Stale evidence: bundle tree SHA '{bundle.git_tree_sha}' != "
            f"staged tree SHA '{tree_sha}'. "
            "Re-run `bi-agent verify-all` after staging all changes.",
        )

    return True, f"Verified evidence matches staged tree {tree_sha}"


def find_matching_evidence(repo_root: Path) -> Path | None:
    """Find any passing verification-report.json matching the current staged Git tree SHA."""
    runs_dir = repo_root / "artifacts" / "runs"
    if not runs_dir.is_dir():
        return None
    tree_sha = staged_tree_sha(repo_root)
    if not tree_sha:
        return None
    for report in sorted(runs_dir.glob("*/verification-report.json")):
        try:
            bundle = VerificationEvidenceBundle.load_json(report)
        except Exception:
            continue
        if bundle.is_passing() and bundle.git_tree_sha == tree_sha:
            return report
    return None


def commit_verified(
    repo_root: Path,
    message: str,
    evidence_path: Path | None = None,
    allow_main: bool = False,
) -> CommandResult:
    """Create a conventional Git commit after enforcing branch & evidence-gate policies."""
    if not is_conventional_commit(message):
        return CommandResult(
            returncode=1,
            stderr=(
                "Commit message must follow Conventional Commits: "
                "<type>(<scope>): <summary> (e.g., feat(REQ-001): add regional margin model)"
            ),
        )

    staged = staged_files(repo_root)
    if not staged:
        return CommandResult(returncode=1, stderr="No staged changes to commit.")

    branch = current_branch(repo_root)
    pbi_staged = any(is_pbi_path(item) for item in staged)
    if pbi_staged and branch in ("main", "master") and not allow_main:
        return CommandResult(
            returncode=1,
            stderr=(
                f"Direct commits touching pbi/ artifacts on protected branch "
                f"'{branch}' are blocked. Create a feature branch (e.g., "
                "feat/bi-REQ-001) or pass --allow-main if explicitly intended."
            ),
        )

    if pbi_staged or evidence_path is not None:
        target_evidence = evidence_path or find_matching_evidence(repo_root)
        if target_evidence is None:
            return CommandResult(
                returncode=1,
                stderr=(
                    "No passing verification-report.json matches the current staged Git tree SHA. "
                    "Stage files and run `bi-agent verify-all` before committing."
                ),
            )
        ok, detail = verify_evidence_matches_index(repo_root, target_evidence)
        if not ok:
            return CommandResult(returncode=1, stderr=detail)

    return run_command(["git", "commit", "-m", message], cwd=repo_root, timeout=30)
