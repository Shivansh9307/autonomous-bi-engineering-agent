"""Full 8-gate verification orchestrator (G0..G7) producing VerificationEvidenceBundle."""

from __future__ import annotations

from pathlib import Path

from bi_agent.adapters import git_adapter
from bi_agent.config import ToolchainConfig
from bi_agent.gates.g0_data_profiling import run_gate_g0
from bi_agent.gates.g1_design_contract import run_gate_g1
from bi_agent.gates.g2_pbip_structure import run_gate_g2
from bi_agent.gates.g3_bpa_vpax import run_gate_g3
from bi_agent.gates.g4_dax_reconciliation import run_gate_g4
from bi_agent.gates.g5_rls_verification import run_gate_g5, run_gate_g5_offline
from bi_agent.gates.g6_report_visuals import run_gate_g6
from bi_agent.gates.g7_git_governance import run_gate_g7
from bi_agent.schemas.evidence import (
    GateResult,
    VerificationEvidenceBundle,
    to_repo_relative_posix,
)
from bi_agent.schemas.requirement import load_requirement


def run_all_gates(
    req_id: str,
    requirement_path: Path,
    project_dir: Path | None,
    ground_truth_path: Path | None,
    config: ToolchainConfig,
    repo_root: Path,
    offline: bool = False,
    port: int | None = None,
    database: str = "",
    skip_benchmark: bool = False,
    require_feature_branch: bool = False,
    output_dir: Path | None = None,
) -> tuple[VerificationEvidenceBundle, Path, Path]:
    """Execute Gates G0..G7 and persist JSON + Markdown verification evidence reports."""
    resolved_req_id = req_id
    resolved_project_dir = project_dir
    resolved_gt_path = ground_truth_path
    report_name = config.project.desktop_report_name

    try:
        req = load_requirement(requirement_path)
        if not resolved_req_id:
            resolved_req_id = req.id
        if resolved_project_dir is None and req.resolved_pbip_path():
            resolved_project_dir = repo_root / req.resolved_pbip_path()
        if resolved_gt_path is None and req.resolved_ground_truth_path():
            resolved_gt_path = repo_root / req.resolved_ground_truth_path()
        if req.resolved_desktop_report_name():
            report_name = req.resolved_desktop_report_name()
    except Exception:
        pass

    if not resolved_req_id:
        resolved_req_id = requirement_path.stem
    if resolved_project_dir is None:
        resolved_project_dir = (
            repo_root / config.project.pbip_project_dir
            if config.project.pbip_project_dir
            else repo_root
        )
    if resolved_gt_path is None:
        resolved_gt_path = (
            repo_root / config.project.ground_truth_path
            if config.project.ground_truth_path
            else repo_root
        )

    run_dir = output_dir or (repo_root / "artifacts" / "runs" / resolved_req_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    gates: list[GateResult] = [
        run_gate_g0(requirement_path, repo_root, output_dir=run_dir),
        run_gate_g1(requirement_path, repo_root),
        run_gate_g2(resolved_project_dir, config, repo_root),
        run_gate_g3(
            resolved_project_dir,
            config,
            repo_root,
            port=port,
            database=database,
            offline=offline,
            output_dir=run_dir,
            report_name=report_name,
        ),
    ]

    if offline:
        gates.append(
            GateResult(
                gate_id="G4",
                name="Live DAX Reconciliation & Perf",
                status="SKIP",
                summary="Skipped in --offline mode (requires live Power BI Desktop engine)",
            )
        )
        gates.append(run_gate_g5_offline(resolved_project_dir, requirement_path))
    else:
        gates.append(
            run_gate_g4(
                resolved_gt_path,
                config,
                repo_root,
                port=port,
                database=database,
                skip_benchmark=skip_benchmark,
                output_dir=run_dir,
                requirement_path=requirement_path,
                report_name=report_name,
            )
        )
        gates.append(
            run_gate_g5(
                requirement_path,
                resolved_gt_path,
                config,
                repo_root,
                port=port,
                database=database,
                report_name=report_name,
            )
        )

    gates.append(
        run_gate_g6(
            resolved_project_dir,
            config,
            repo_root,
            offline=offline,
            output_dir=run_dir,
            requirement_path=requirement_path,
        )
    )
    gates.append(
        run_gate_g7(
            repo_root,
            require_feature_branch=require_feature_branch,
        )
    )

    for gate in gates:
        gate.artifacts = [to_repo_relative_posix(art, repo_root) for art in gate.artifacts]
        for finding in gate.findings:
            if finding.location:
                finding.location = to_repo_relative_posix(finding.location, repo_root)
        if gate.gate_id == "G6":
            shots = gate.metrics.get("screenshots")
            if isinstance(shots, list):
                for shot in shots:
                    if isinstance(shot, dict) and isinstance(shot.get("path"), str):
                        shot["path"] = to_repo_relative_posix(shot["path"], repo_root)

    branch = git_adapter.current_branch(repo_root)
    tree_sha = git_adapter.staged_tree_sha(repo_root)
    overall_pass = all(g.status in ("PASS", "WARN", "SKIP") for g in gates)

    bundle = VerificationEvidenceBundle(
        req_id=resolved_req_id,
        git_branch=branch,
        git_tree_sha=tree_sha,
        overall_status="PASS" if overall_pass else "FAIL",
        gates=gates,
    )

    json_path = bundle.write_json(run_dir / "verification-report.json")
    md_path = bundle.write_markdown(run_dir / "verification-report.md", repo_root=repo_root)
    return bundle, json_path, md_path
