"""PostToolUse audit logger recording tool invocations to artifacts/runs/hook-audit.jsonl."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bi_agent.config import find_repo_root


def record_audit_event(payload: dict[str, Any], repo_root: Path | None = None) -> None:
    root = repo_root
    if root is None:
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and Path(cwd).exists():
            root = find_repo_root(Path(cwd))
        else:
            root = find_repo_root()
    audit_path = root / "artifacts" / "runs" / "hook-audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    tool_call = payload.get("toolCall") or {}
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "hookEventName": payload.get("hookEventName", "PostToolUse"),
        "toolName": tool_call.get("name", ""),
        "stepIdx": payload.get("stepIdx"),
        "error": payload.get("error"),
    }
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def main() -> int:
    raw = sys.stdin.read()
    try:
        if raw.strip():
            payload = json.loads(raw)
            if isinstance(payload, dict):
                record_audit_event(payload)
    except Exception:
        pass
    sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
