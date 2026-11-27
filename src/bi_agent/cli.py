"""Command-line interface for the Autonomous BI Engineering Agent verification harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bi_agent import __version__
from bi_agent.adapters import git_adapter
from bi_agent.config import ToolchainConfig, find_repo_root, load_toolchain_config, run_doctor
from bi_agent.gates.g0_data_profiling import profile_path, run_gate_g0
from bi_agent.gates.g1_design_contract import run_gate_g1
from bi_agent.gates.g2_pbip_structure import run_gate_g2
from bi_agent.gates.g3_bpa_vpax import run_gate_g3
from bi_agent.gates.g4_dax_reconciliation import run_gate_g4
from bi_agent.gates.g5_rls_verification import run_gate_g5, run_gate_g5_offline
from bi_agent.gates.g6_report_visuals import run_gate_g6
from bi_agent.gates.runner import run_all_gates
from bi_agent.schemas.evidence import GateResult, VerificationEvidenceBundle
from bi_agent.schemas.requirement import BusinessRequirement, load_requirement

app = typer.Typer(
    name="bi-agent",
    help="Verification-first Autonomous BI Engineering Agent CLI.",
    no_args_is_help=True,
)
console = Console()


def _resolve_requirement(
    repo_root: Path,
    req_id: str,
    requirement_path: Path | None,
) -> Path:
    if requirement_path is not None:
        if requirement_path.is_absolute():
            return requirement_path
        return repo_root / requirement_path
    req_dir = repo_root / "requirements"
    if req_dir.is_dir():
        if req_id:
            matches = sorted(req_dir.glob(f"{req_id}*.yaml"))
            if matches:
                return matches[0]
        all_reqs = sorted(req_dir.glob("*.yaml"))
        if all_reqs:
            return all_reqs[0]
    raise typer.BadParameter(
        f"No requirement YAML found for req_id='{req_id}' in {req_dir}."
    )


def _resolve_project_context(
    repo_root: Path,
    cfg: ToolchainConfig,
    requirement_path: Path | None,
    project_dir: Path | None,
    ground_truth: Path | None,
) -> tuple[BusinessRequirement | None, Path, Path | None, str]:
    req: BusinessRequirement | None = None
    if requirement_path is not None and requirement_path.is_file():
        req = load_requirement(requirement_path)

    if project_dir is not None:
        target_project = project_dir if project_dir.is_absolute() else (repo_root / project_dir)
    elif req is not None and req.resolved_pbip_path():
        target_project = repo_root / req.resolved_pbip_path()
    elif cfg.project.pbip_project_dir:
        target_project = repo_root / cfg.project.pbip_project_dir
    else:
        target_project = repo_root

    gt_path: Path | None
    if ground_truth is not None:
        gt_path = ground_truth if ground_truth.is_absolute() else (repo_root / ground_truth)
    elif req is not None and req.resolved_ground_truth_path():
        gt_path = repo_root / req.resolved_ground_truth_path()
    elif cfg.project.ground_truth_path:
        gt_path = repo_root / cfg.project.ground_truth_path
    else:
        gt_path = None

    report_name = (
        (req.resolved_desktop_report_name() if req is not None else "")
        or cfg.project.desktop_report_name
    )
    return req, target_project, gt_path, report_name


def _emit_gate(result: GateResult, json_output: bool) -> None:
    if json_output:
        typer.echo(result.model_dump_json(indent=2))
    else:
        style = (
            "green"
            if result.status == "PASS"
            else ("yellow" if result.status in ("WARN", "SKIP") else "red")
        )
        console.print(
            f"[{style}]{result.gate_id} ({result.name}): "
            f"{result.status}[/{style}] - {result.summary}"
        )
        for finding in result.findings:
            console.print(f"  - [{finding.severity.upper()}] {finding.code}: {finding.message}")
    raise typer.Exit(code=0 if result.status in ("PASS", "WARN", "SKIP") else 1)


def _emit_bundle(
    bundle: VerificationEvidenceBundle,
    json_path: Path,
    md_path: Path,
    json_output: bool,
) -> None:
    if json_output:
        payload = bundle.model_dump()
        payload["report_json"] = str(json_path)
        payload["report_md"] = str(md_path)
        typer.echo(json.dumps(payload, indent=2))
    else:
        table = Table(title=f"Verification Evidence Bundle: {bundle.req_id}")
        table.add_column("Gate", style="cyan", no_wrap=True)
        table.add_column("Name")
        table.add_column("Status", justify="center")
        table.add_column("Duration (ms)", justify="right")
        table.add_column("Summary")
        for gate in bundle.gates:
            badge = {
                "PASS": "[green]PASS[/green]",
                "WARN": "[yellow]WARN[/yellow]",
                "SKIP": "[yellow]SKIP[/yellow]",
            }.get(gate.status, "[red]FAIL[/red]")
            table.add_row(gate.gate_id, gate.name, badge, str(gate.duration_ms), gate.summary)
        console.print(table)
        console.print(f"Evidence JSON: {json_path}")
        console.print(f"Evidence MD:   {md_path}")
        console.print(
            "[green]Overall: PASS[/green]"
            if bundle.overall_status == "PASS"
            else "[red]Overall: FAIL[/red]"
        )
    raise typer.Exit(code=0 if bundle.overall_status == "PASS" else 1)


@app.command()
def doctor(
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
    config: Annotated[
        Path | None, typer.Option("--config", help="Optional path to toolchain.yaml.")
    ] = None,
) -> None:
    """Verify local BI toolchain binaries, DLLs, MCP configuration, and Desktop Bridge."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    results = run_doctor(cfg, repo_root)
    all_required_ok = all(item.status == "ok" for item in results if item.required)

    if json_output:
        payload = {
            "version": __version__,
            "repo_root": str(repo_root),
            "overall_status": "PASS" if all_required_ok else "FAIL",
            "checks": [item.model_dump() for item in results],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        table = Table(title=f"bi-agent doctor v{__version__}")
        table.add_column("Check", style="cyan", no_wrap=True)
        table.add_column("Required", justify="center")
        table.add_column("Status", justify="center")
        table.add_column("Version")
        table.add_column("Path / Detail")

        for item in results:
            status_style = {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]"}.get(
                item.status, "[red]ERROR[/red]"
            )
            loc = item.path or item.detail
            if item.path and item.detail and item.status != "ok":
                loc = f"{item.path} ({item.detail})"
            table.add_row(
                item.name,
                "yes" if item.required else "no",
                status_style,
                item.version or "-",
                loc,
            )
        console.print(table)
        console.print(
            "[green]Overall: PASS[/green]" if all_required_ok else "[red]Overall: FAIL[/red]"
        )

    raise typer.Exit(code=0 if all_required_ok else 1)


@app.command("profile-data")
def profile_data(
    raw_path: Annotated[
        Path, typer.Argument(help="Raw CSV/TSV file or directory of messy flat files.")
    ],
    output_dir: Annotated[
        Path | None, typer.Option("--output-dir", help="Directory for data-profile.{json,md}.")
    ] = None,
    req_id: Annotated[str, typer.Option("--req-id")] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Profile raw flat files, detect defects, and emit policy questions (Gate G0 preflight)."""
    repo_root = find_repo_root()
    target = raw_path if raw_path.is_absolute() else (repo_root / raw_path)
    out = (
        (output_dir if output_dir.is_absolute() else (repo_root / output_dir))
        if output_dir is not None
        else (repo_root / "artifacts" / "runs" / (req_id or "profile"))
    )
    report = profile_path(target, req_id=req_id, output_dir=out)
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
    else:
        console.print(
            f"[cyan]G0 Profile:[/cyan] {len(report.files)} file(s), "
            f"{len(report.defects)} defect finding(s), "
            f"{len(report.policy_questions)} policy question(s)"
        )
        console.print(f"Profile JSON: {out / 'data-profile.json'}")
        console.print(f"Profile MD:   {out / 'data-profile.md'}")


@app.command("verify-data")
def verify_data(
    requirement_path: Annotated[Path, typer.Argument(help="Requirement YAML path.")],
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Enforce messy-data policy sign-offs and raw-to-staged reconciliation (Gate G0)."""
    repo_root = find_repo_root()
    req = requirement_path if requirement_path.is_absolute() else (repo_root / requirement_path)
    out = (
        (output_dir if output_dir.is_absolute() else (repo_root / output_dir))
        if output_dir is not None
        else None
    )
    result = run_gate_g0(req, repo_root, output_dir=out)
    _emit_gate(result, json_output)


@app.command("verify-design")
def verify_design(
    requirement_path: Annotated[Path, typer.Argument(help="Requirement YAML path.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON output.")
    ] = False,
) -> None:
    """Validate dimensional design contract against a business requirement (Gate G1)."""
    repo_root = find_repo_root()
    req = requirement_path if requirement_path.is_absolute() else (repo_root / requirement_path)
    result = run_gate_g1(req, repo_root)
    _emit_gate(result, json_output)


@app.command("verify-model")
def verify_model(
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    requirement: Annotated[Path | None, typer.Option("--requirement")] = None,
    bpa_rules: Annotated[Path | None, typer.Option("--bpa-rules")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    database: Annotated[str, typer.Option("--database")] = "",
    offline: Annotated[
        bool, typer.Option("--offline", help="Run static PBIP/TMDL/BPA checks only (skip VPAX).")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run PBIP structure, TMDL syntax, TE2 BPA, and VPAX gates (Gates G2 & G3)."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    req_path = (
        (requirement if requirement.is_absolute() else (repo_root / requirement))
        if requirement is not None
        else None
    )
    _, target_project, _, report_name = _resolve_project_context(
        repo_root, cfg, req_path, project_dir, None
    )
    g2 = run_gate_g2(target_project, cfg, repo_root)
    g3 = run_gate_g3(
        target_project,
        cfg,
        repo_root,
        bpa_rules_path=bpa_rules,
        port=port,
        database=database,
        offline=offline,
        report_name=report_name,
    )
    passed = g2.status == "PASS" and g3.status == "PASS"
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "overall_status": "PASS" if passed else "FAIL",
                    "gates": [g2.model_dump(), g3.model_dump()],
                },
                indent=2,
            )
        )
    else:
        for gate in (g2, g3):
            color = "green" if gate.status == "PASS" else "red"
            console.print(
                f"[{color}]{gate.gate_id} ({gate.name}): "
                f"{gate.status}[/{color}] - {gate.summary}"
            )
            for finding in gate.findings:
                console.print(f"  - [{finding.severity.upper()}] {finding.code}: {finding.message}")
    raise typer.Exit(code=0 if passed else 1)


@app.command("verify-dax")
def verify_dax(
    requirement: Annotated[Path | None, typer.Option("--requirement")] = None,
    ground_truth: Annotated[Path | None, typer.Option("--ground-truth")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    database: Annotated[str, typer.Option("--database")] = "",
    tolerance: Annotated[float | None, typer.Option("--tolerance")] = None,
    skip_benchmark: Annotated[bool, typer.Option("--skip-benchmark")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Reconcile live DAX measures against ground truth and run benchmarks (Gate G4)."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    req_path = (
        (requirement if requirement.is_absolute() else (repo_root / requirement))
        if requirement is not None
        else None
    )
    _, _, gt_path, report_name = _resolve_project_context(
        repo_root, cfg, req_path, None, ground_truth
    )
    if gt_path is None:
        raise typer.BadParameter(
            "Unable to resolve ground-truth JSON path. Pass --ground-truth or --requirement."
        )
    result = run_gate_g4(
        gt_path,
        cfg,
        repo_root,
        port=port,
        database=database,
        tolerance=tolerance,
        skip_benchmark=skip_benchmark,
        requirement_path=req_path,
        report_name=report_name,
    )
    _emit_gate(result, json_output)


@app.command("verify-rls")
def verify_rls(
    requirement: Annotated[Path | None, typer.Option("--requirement")] = None,
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    ground_truth: Annotated[Path | None, typer.Option("--ground-truth")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    database: Annotated[str, typer.Option("--database")] = "",
    tolerance: Annotated[float | None, typer.Option("--tolerance")] = None,
    offline: Annotated[
        bool, typer.Option("--offline", help="Run offline TMDL RLS contract audit only.")
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run offline TMDL RLS contract audit or live ADOMD.NET RLS impersonation (Gate G5)."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    req_path = _resolve_requirement(repo_root, "", requirement)
    _, target_project, gt_path, report_name = _resolve_project_context(
        repo_root, cfg, req_path, project_dir, ground_truth
    )
    if offline:
        result = run_gate_g5_offline(target_project, req_path)
    else:
        if gt_path is None:
            raise typer.BadParameter(
                "Unable to resolve ground-truth JSON path. Pass --ground-truth or --requirement."
            )
        result = run_gate_g5(
            req_path,
            gt_path,
            cfg,
            repo_root,
            port=port,
            database=database,
            tolerance=tolerance,
            report_name=report_name,
        )
    _emit_gate(result, json_output)


@app.command("verify-report")
def verify_report(
    report_dir: Annotated[Path | None, typer.Option("--report")] = None,
    requirement: Annotated[Path | None, typer.Option("--requirement")] = None,
    offline: Annotated[bool, typer.Option("--offline")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Run pbir validate --all and Desktop screenshot capture (Gate G6)."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    req_path = (
        (requirement if requirement.is_absolute() else (repo_root / requirement))
        if requirement is not None
        else None
    )
    _, target, _, _ = _resolve_project_context(repo_root, cfg, req_path, report_dir, None)
    result = run_gate_g6(target, cfg, repo_root, offline=offline, requirement_path=req_path)
    _emit_gate(result, json_output)


@app.command("verify-all")
def verify_all(
    req_id: Annotated[str, typer.Option("--req-id")] = "",
    requirement: Annotated[Path | None, typer.Option("--requirement")] = None,
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
    ground_truth: Annotated[Path | None, typer.Option("--ground-truth")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    database: Annotated[str, typer.Option("--database")] = "",
    offline: Annotated[bool, typer.Option("--offline")] = False,
    skip_benchmark: Annotated[bool, typer.Option("--skip-benchmark")] = False,
    require_feature_branch: Annotated[bool, typer.Option("--require-feature-branch")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    config: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Execute all 8 verification gates (G0-G7) and emit Verification Evidence Bundle."""
    repo_root = find_repo_root()
    cfg = load_toolchain_config(config_path=config, repo_root=repo_root)
    req_path = _resolve_requirement(repo_root, req_id, requirement)
    req_obj, target_project, gt_path, _ = _resolve_project_context(
        repo_root, cfg, req_path, project_dir, ground_truth
    )
    effective_req_id = req_id or (req_obj.id if req_obj is not None else req_path.stem)
    bundle, json_path, md_path = run_all_gates(
        req_id=effective_req_id,
        requirement_path=req_path,
        project_dir=target_project,
        ground_truth_path=gt_path,
        config=cfg,
        repo_root=repo_root,
        offline=offline,
        port=port,
        database=database,
        skip_benchmark=skip_benchmark,
        require_feature_branch=require_feature_branch,
    )
    _emit_bundle(bundle, json_path, md_path, json_output)


@app.command("git-commit")
def git_commit(
    message: Annotated[str, typer.Option("--message", "-m")],
    evidence: Annotated[Path | None, typer.Option("--evidence")] = None,
    allow_main: Annotated[bool, typer.Option("--allow-main")] = False,
) -> None:
    """Create a conventional Git commit gated on Verification Evidence Bundle."""
    repo_root = find_repo_root()
    res = git_adapter.commit_verified(
        repo_root=repo_root,
        message=message,
        evidence_path=evidence,
        allow_main=allow_main,
    )
    if res.stdout:
        typer.echo(res.stdout)
    if res.stderr:
        typer.echo(res.stderr, err=True)
    raise typer.Exit(code=res.returncode)


if __name__ == "__main__":
    app()
