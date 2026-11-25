"""Command-line interface for the Autonomous BI Engineering Agent verification harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from bi_agent import __version__
from bi_agent.config import find_repo_root, load_toolchain_config, run_doctor

app = typer.Typer(
    name="bi-agent",
    help="Verification-first Autonomous BI Engineering Agent CLI.",
    no_args_is_help=True,
)
console = Console()


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


def _not_implemented(cmd_name: str) -> None:
    typer.echo(f"{cmd_name}: Not yet implemented (scheduled for Phase 2)", err=True)
    raise typer.Exit(code=2)


@app.command("verify-design")
def verify_design(
    requirement_path: Annotated[Path, typer.Argument(help="Requirement YAML path.")],
) -> None:
    """Validate dimensional design contract against a business requirement."""
    _ = requirement_path
    _not_implemented("verify-design")


@app.command("verify-model")
def verify_model(
    project_dir: Annotated[Path | None, typer.Option("--project")] = None,
) -> None:
    """Run PBIP structure, TMDL syntax, TE2 BPA, and VPAX gates."""
    _ = project_dir
    _not_implemented("verify-model")


@app.command("verify-dax")
def verify_dax(
    ground_truth: Annotated[Path | None, typer.Option("--ground-truth")] = None,
) -> None:
    """Reconcile live DAX measures against ground truth and run benchmarks."""
    _ = ground_truth
    _not_implemented("verify-dax")


@app.command("verify-rls")
def verify_rls(
    ground_truth: Annotated[Path | None, typer.Option("--ground-truth")] = None,
) -> None:
    """Run live ADOMD/MCP RLS impersonation matrix."""
    _ = ground_truth
    _not_implemented("verify-rls")


@app.command("verify-report")
def verify_report(
    report_dir: Annotated[Path | None, typer.Option("--report")] = None,
) -> None:
    """Run pbir validate --all and Desktop screenshot capture."""
    _ = report_dir
    _not_implemented("verify-report")


@app.command("verify-all")
def verify_all(
    req_id: Annotated[str, typer.Option("--req-id")] = "REQ-001",
) -> None:
    """Execute all verification gates and emit Verification Evidence Bundle."""
    _ = req_id
    _not_implemented("verify-all")


@app.command("git-commit")
def git_commit(
    message: Annotated[str, typer.Option("--message", "-m")],
) -> None:
    """Create a conventional Git commit gated on Verification Evidence Bundle."""
    _ = message
    _not_implemented("git-commit")


if __name__ == "__main__":
    app()
