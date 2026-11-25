# Autonomous BI Engineering Agent System

A verification-first Autonomous BI Engineering Agent system for Power BI (`PBIP` / `TMDL` / `PBIR`).
Given a business requirement, it designs a Kimball star schema, builds or updates the semantic model, authors DAX and Row-Level Security (RLS), builds PBIR report pages, verifies every layer empirically (BPA, VPAX, live DAX ground truth, live RLS impersonation, `pbir validate --all`, and Desktop screenshots), and commits changes through an evidence-gated Git workflow.

## Quickstart

```powershell
uv sync
uv run bi-agent doctor
uv run python scripts/generate_sample_data.py
```

See [docs/PLAN.md](docs/PLAN.md) and [docs/PHASE_STATE.md](docs/PHASE_STATE.md) for the phased roadmap and current checkpoint.
