"""PreToolUse hook enforcing PBIR CLI-only report edits and evidence-gated Git commits."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from bi_agent.config import find_repo_root

WRITE_TOOLS = {
    "write_file",
    "edit_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
}

PBIR_DEF_JSON_RE = re.compile(r"(^|/)[^/]+\.report/definition/.*\.json$", re.IGNORECASE)
SHELL_PBIR_WRITE_RE = re.compile(
    r"(>|>>|out-file|set-content|add-content|write_text|write_bytes|open\(|cp\s|mv\s|copy-item|move-item)"
    r".*\.report[\\/]+definition[\\/].*\.json",
    re.IGNORECASE,
)
RAW_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|\s])git\s+(?:-[A-Za-z]\s+\S+\s+)*commit\b", re.IGNORECASE)


def _extract_path_arg(args: dict[str, Any]) -> str:
    for key in ("Path", "path", "TargetFile", "target_file", "filePath", "file_path"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _extract_command_arg(args: dict[str, Any]) -> str:
    for key in ("CommandLine", "commandLine", "command", "Command"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _resolve_repo_root(payload: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        p = Path(cwd)
        if p.exists():
            return find_repo_root(p)
    workspaces = payload.get("workspacePaths")
    if isinstance(workspaces, list) and workspaces:
        p = Path(str(workspaces[0]))
        if p.exists():
            return find_repo_root(p)
    return find_repo_root()


def is_pbir_definition_json(path_str: str) -> bool:
    normalized = path_str.replace("\\", "/")
    return bool(PBIR_DEF_JSON_RE.search(normalized))


def evaluate_pre_tool_use(
    payload: dict[str, Any],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Evaluate a PreToolUse JSON payload and return {'decision': 'allow'|'deny', ...}."""
    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name", "")).lower()
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        args = {}

    if tool_name in WRITE_TOOLS:
        target = _extract_path_arg(args)
        if target and is_pbir_definition_json(target):
            return {
                "decision": "deny",
                "reason": (
                    f"Direct edits to PBIR definition JSON '{target}' are prohibited. "
                    "Use the `pbir` CLI (`pbir new`, `pbir add`, `pbir set`) exclusively."
                ),
            }

    if tool_name == "run_command":
        cmd = _extract_command_arg(args)
        if cmd:
            stripped = cmd.strip()
            is_pbir_cmd = stripped.lower().startswith("pbir ")
            if not is_pbir_cmd and SHELL_PBIR_WRITE_RE.search(stripped):
                return {
                    "decision": "deny",
                    "reason": (
                        "Shell writes directly into *.Report/definition/*.json are prohibited. "
                        "Use the `pbir` CLI exclusively."
                    ),
                }

            if RAW_GIT_COMMIT_RE.search(stripped) and "bi-agent git-commit" not in stripped.lower():
                from bi_agent.adapters import git_adapter

                root = _resolve_repo_root(payload, repo_root)
                if git_adapter.has_pbi_staged(root):
                    evidence = git_adapter.find_matching_evidence(root)
                    if evidence is None:
                        return {
                            "decision": "deny",
                            "reason": (
                                "Raw `git commit` blocked: staged pbi/ artifacts do not have a "
                                "passing `artifacts/runs/*/verification-report.json` matching the "
                                "staged Git tree SHA. Run `bi-agent verify-all` and commit via "
                                "`bi-agent git-commit`."
                            ),
                        }

    return {"decision": "allow"}


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stdout.write(json.dumps({"decision": "allow"}))
        return 0
    try:
        payload = json.loads(raw)
        decision = evaluate_pre_tool_use(payload if isinstance(payload, dict) else {})
    except Exception as exc:
        decision = {"decision": "allow", "reason": f"Hook evaluation skipped on error: {exc}"}
    sys.stdout.write(json.dumps(decision))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
