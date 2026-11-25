"""Unit tests for tool adapters (`dax_studio`, `tabular_editor`, `adomd`, `pbir`, `git`)."""

from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from bi_agent.adapters import (
    adomd_adapter,
    dax_studio_adapter,
    git_adapter,
    pbir_adapter,
    tabular_editor_adapter,
)
from bi_agent.adapters._subprocess import CommandResult
from bi_agent.config import ToolchainConfig
from bi_agent.schemas.evidence import GateResult, VerificationEvidenceBundle


def test_parse_vpax_metrics_reads_zip_archive(tmp_path: Path) -> None:
    vpax_path = tmp_path / "model.vpax"
    payload = {
        "Tables": [
            {"Name": "FactSales", "RowsCount": 25000, "TableSize": 2048000},
            {"Name": "DimDate", "RowsCount": 1096, "TableSize": 102400},
        ],
        "Columns": [
            {
                "TableName": "FactSales",
                "ColumnName": "SalesOrderID",
                "TotalSize": 1500000,
                "ColumnCardinality": 12500,
            },
            {
                "TableName": "FactSales",
                "ColumnName": "NetRevenue",
                "TotalSize": 400000,
                "ColumnCardinality": 9800,
            },
        ],
    }
    with zipfile.ZipFile(vpax_path, "w") as zf:
        zf.writestr("DaxVpaView.json", json.dumps(payload))

    metrics = dax_studio_adapter.parse_vpax_metrics(vpax_path)
    assert metrics["table_count"] == 2
    assert metrics["column_count"] == 2
    assert metrics["total_rows"] == 26096
    assert metrics["total_size_bytes"] == 2150400
    assert metrics["top_columns"][0]["column"] == "SalesOrderID"


def test_tabular_editor_check_tmdl_and_bpa_mocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def_dir = tmp_path / "definition"
    def_dir.mkdir()
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps(
            [
                {
                    "ID": "ERR_TEST",
                    "Name": "Test Rule",
                    "Severity": 3,
                    "Scope": "Measure",
                    "Expression": "true",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        tabular_editor_adapter, "resolve_tabular_editor", lambda _cfg: "TabularEditor.exe"
    )
    monkeypatch.setattr(
        tabular_editor_adapter,
        "run_command",
        lambda *_a, **_kw: CommandResult(returncode=0, stdout="TMDL_OK:7"),
    )
    tmdl_res = tabular_editor_adapter.check_tmdl_load(def_dir, ToolchainConfig())
    assert tmdl_res.ok is True
    assert tmdl_res.table_count == 7

    monkeypatch.setattr(
        tabular_editor_adapter,
        "run_command",
        lambda *_a, **_kw: CommandResult(
            returncode=1,
            stdout="::error ::[Sales] (Measure) violates rule [ERR_TEST] Test Rule",
        ),
    )
    bpa_res = tabular_editor_adapter.run_bpa(def_dir, rules_path, ToolchainConfig())
    assert bpa_res.ok is False
    assert bpa_res.error_count == 1


def test_adomd_and_pbir_adapters_mocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts" / "pbid").mkdir(parents=True)
    (repo / "scripts" / "pbid" / "discover_pbid_port.ps1").write_text("# ps1", encoding="utf-8")
    (repo / "scripts" / "pbid" / "run_dax_impersonated.ps1").write_text("# ps1", encoding="utf-8")

    monkeypatch.setattr(adomd_adapter, "resolve_pwsh", lambda: "pwsh")
    monkeypatch.setattr(adomd_adapter, "resolve_adomd_dir", lambda _cfg: str(tmp_path))
    monkeypatch.setattr(adomd_adapter, "resolve_pbir", lambda _cfg: "pbir")
    monkeypatch.setattr(
        adomd_adapter,
        "run_command",
        lambda args, **_kw: CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "instances": [{"port": 51234, "database": "EnterpriseSales"}],
                    "selected": {"port": 51234, "database": "EnterpriseSales"},
                }
            )
            if "discover_pbid_port" in " ".join(args)
            else json.dumps(
                {
                    "ok": True,
                    "duration_ms": 18,
                    "columns": ["[TotalRevenue]"],
                    "rows": [{"[TotalRevenue]": 48744150.82}],
                    "row_count": 1,
                }
            ),
        ),
    )

    disc = adomd_adapter.discover_instance(ToolchainConfig(), repo)
    assert disc.ok is True
    assert disc.selected is not None
    assert disc.selected.port == 51234

    qres = adomd_adapter.execute_dax(
        51234, "EVALUATE ROW(\"TotalRevenue\", [Total Revenue])", ToolchainConfig(), repo
    )
    assert qres.ok is True
    assert qres.rows[0]["[TotalRevenue]"] == 48744150.82

    shots_dir = tmp_path / "shots"
    shots_dir.mkdir()
    shot_file = shots_dir / "Executive_Overview.png"
    shot_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)

    monkeypatch.setattr(pbir_adapter, "resolve_pbir", lambda _cfg: "pbir")
    monkeypatch.setattr(
        pbir_adapter,
        "run_command",
        lambda *_a, **_kw: CommandResult(
            returncode=0,
            stdout=json.dumps(
                {"screenshots": [{"path": str(shot_file), "page": "Executive_Overview"}]}
            ),
        ),
    )
    sres = pbir_adapter.desktop_screenshot(tmp_path / "Rep.Report", shots_dir, ToolchainConfig())
    assert sres.ok is True
    assert len(sres.screenshots) == 1
    assert sres.screenshots[0].size_bytes > 1024


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def test_git_adapter_tree_sha_and_commit_gating(tmp_path: Path) -> None:
    repo = tmp_path / "git_repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@local.dev")

    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "chore(init): initial commit")

    _git(repo, "checkout", "-b", "feat/bi-REQ-001")
    pbi_dir = repo / "pbi" / "EnterpriseSales"
    pbi_dir.mkdir(parents=True)
    pbip_file = pbi_dir / "EnterpriseSales.pbip"
    pbip_file.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "pbi/EnterpriseSales/EnterpriseSales.pbip")

    # Commit without evidence must fail
    no_ev_res = git_adapter.commit_verified(repo, "feat(REQ-001): add pbip")
    assert no_ev_res.returncode != 0
    assert "verification-report.json" in no_ev_res.stderr

    # Write evidence with wrong tree SHA -> must fail
    ev_path = repo / "artifacts" / "runs" / "REQ-001" / "verification-report.json"
    bad_bundle = VerificationEvidenceBundle(
        req_id="REQ-001",
        git_branch="feat/bi-REQ-001",
        git_tree_sha="0000000000000000000000000000000000000000",
        overall_status="PASS",
        gates=[GateResult(gate_id="G1", name="Design", status="PASS")],
    )
    bad_bundle.write_json(ev_path)
    stale_res = git_adapter.commit_verified(repo, "feat(REQ-001): add pbip", evidence_path=ev_path)
    assert stale_res.returncode != 0
    assert "Stale evidence" in stale_res.stderr

    # Write evidence matching staged tree SHA -> must succeed
    good_sha = git_adapter.staged_tree_sha(repo)
    good_bundle = VerificationEvidenceBundle(
        req_id="REQ-001",
        git_branch="feat/bi-REQ-001",
        git_tree_sha=good_sha,
        overall_status="PASS",
        gates=[GateResult(gate_id="G1", name="Design", status="PASS")],
    )
    good_bundle.write_json(ev_path)
    ok_res = git_adapter.commit_verified(repo, "feat(REQ-001): add pbip", evidence_path=ev_path)
    assert ok_res.returncode == 0
