# ADR-0004: Evidence-Gated Git Commits Bound to Staged Tree SHA

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

In multi-step AI-assisted development, an agent might run verification gates early, then edit one last `.tmdl` or `.pbir` file, stage it, and commit stale test results. Standard commit hooks that only check whether a `verification-report.json` file exists on disk cannot detect post-verification edits to the staged Git index.

## Decision

Bind every verification evidence bundle to the exact Git index tree hash via `git write-tree`:

1. **Evidence Tree Binding (`src/bi_agent/gates/runner.py`, `src/bi_agent/gates/g7_git_governance.py`):**
   - `run_all_gates` records `git_adapter.staged_tree_sha(repo_root)` (`git write-tree`) into `VerificationEvidenceBundle.git_tree_sha`.
2. **Evidence-Gated Commit Command (`src/bi_agent/adapters/git_adapter.py`, `bi-agent git-commit`):**
   - `commit_verified` enforces:
     1. Conventional Commit message format (`feat|fix|chore|docs|refactor|test|ci|build|perf`).
     2. Protected-branch guard blocking direct `pbi/` commits on `main`/`master` unless `--allow-main` is explicitly supplied.
     3. Passing `verification-report.json` whose `git_tree_sha` equals the current `git write-tree` SHA of the staged index.
3. **Hook Enforcement (`src/bi_agent/hooks/pre_tool_guard.py`):**
   - Intercepts raw `git commit` invocations in `run_command` whenever `pbi/` files are staged and no matching `verification-report.json` exists for the staged tree SHA.

## Consequences

- **Positive:** Any post-verification modification to staged `pbi/` artifacts immediately invalidates the evidence SHA and blocks the commit until `bi-agent verify-all` is re-run.
- **Trade-off:** Contributors must stage intended changes (`git add`) before running `bi-agent verify-all` and must keep untracked `artifacts/` out of the staged tree if re-verifying commits.

## Verification & References

- Git Governance & Adapter: `src/bi_agent/adapters/git_adapter.py`, `src/bi_agent/gates/g7_git_governance.py`
- Pre-Tool Guard: `src/bi_agent/hooks/pre_tool_guard.py`
- Fault Injection Test: `tests/fault_injection/test_fault_injection.py` (`test_fi08_g7_blocks_stale_evidence_sha_and_protected_branch`)
