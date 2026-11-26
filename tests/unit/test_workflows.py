"""Unit tests validating the run_workflow BI delivery orchestrator script & manifest."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from bi_agent.config import find_repo_root


def test_bi_delivery_workflow_manifest_and_ast() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    wf_dir = repo_root / "workflows" / "bi_delivery"
    manifest_path = wf_dir / "workflow.json"
    script_path = wf_dir / "workflow.py"

    assert manifest_path.is_file()
    assert script_path.is_file()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "bi-delivery"
    assert manifest["entry"] == "workflow.py"
    props = manifest["args"]["properties"]
    assert set(["req_id", "requirement_path", "project_dir", "mode"]).issubset(props.keys())
    assert props["mode"]["enum"] == ["deliver", "audit"]

    source = script_path.read_text(encoding="utf-8")
    tree = compile(source, str(script_path), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    assert tree is not None

    for primitive in ("phase(", "agent(", "parallel(", "log(", "args"):
        assert primitive in source
