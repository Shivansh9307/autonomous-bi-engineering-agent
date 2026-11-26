# Autonomous BI Engineering Agent System

A verification-first Autonomous BI Engineering Agent system for Power BI (`PBIP` / `TMDL` / `PBIR`). Given a structured business requirement (`requirements/*.yaml`), it validates the Kimball star-schema contract, authors or updates the semantic model and DAX/RLS definitions through a strict tool cascade, builds PBIR report pages exclusively via the `pbir` CLI, verifies every layer empirically across **7 deterministic gates (`G1`–`G7`)**, and permits commits only when a fresh `VerificationEvidenceBundle` matches the staged Git index tree SHA.

## Architecture

```text
requirements/REQ-*.yaml
        │
        ▼
workflows/bi_delivery (audit | deliver)
   ├─► Gate G1: Pydantic Design Contract
   ├─► Semantic Model Cascade:
   │      powerbi-modeling-mcp ─► TabularEditor.exe ─► PowerShell TOM/ADOMD ─► .tmdl (last resort)
   ├─► PBIR Report Authoring:
   │      pbir CLI exclusively (PreToolUse hook blocks direct *.Report/definition/**/*.json writes)
   ├─► Parallel Specialist Auditors (Model & DAX Hygiene + Report & Governance)
   └─► 7-Gate Verification Harness (`bi-agent verify-all`)
          └─► artifacts/runs/<REQ-ID>/verification-report.{json,md} (bound to `git write-tree`)
                 └─► `bi-agent git-commit` (evidence-gated & Conventional Commits)
```

## 7-Gate Verification Matrix

| Gate | Name | Tooling & Checks | Pass Criteria | Mode |
| :--- | :--- | :--- | :--- | :--- |
| **G1** | Design Contract | `bi_agent.schemas` (Pydantic v2) | Valid `1:*` `Single` star schema, grain, KPI specs, and fail-closed RLS contracts | CI & Local |
| **G2** | PBIP & TMDL Structure | `validate_pbip.py` + `TabularEditor.exe` TMDL load | `0` PBIP errors and clean TE2 TMDL parse | CI & Local |
| **G3** | BPA & VertiPaq Audit | `TabularEditor.exe -A config/bpa/enterprise-rules.json -G` + `dscmd vpax` | `0` BPA Error violations (`15` enterprise rules); VPAX telemetry exported when online | Offline BPA in CI; +VPAX Live |
| **G4** | Live DAX Reconciliation & Perf | ADOMD.NET (`scripts/pbid/run_dax_impersonated.ps1`) + `dscmd benchmark` | `42` DAX assertions within `±0.01` of `expected_metrics.json`; cold `<=1500ms`, warm `<=500ms` | Live Desktop (`SKIP` in `--offline`) |
| **G5** | Live RLS Impersonation | ADOMD.NET `Roles` + `EffectiveUserName` / `CustomData` | Exact region/rep isolation; `0` rows for unauthorized & unmapped local principals | Live Desktop (`SKIP` in `--offline`) |
| **G6** | Report QA & Desktop Render | `pbir validate [--all]` + `pbir desktop refresh -m` + `pbir desktop screenshot --all` | Exit `0` validation; Desktop refresh & non-empty page PNGs inspected when online | Offline `pbir validate` in CI; +PNGs Live |
| **G7** | Git Governance & Tree Binding | `git write-tree` + `bi_agent.adapters.git_adapter` | Evidence `git_tree_sha` matches staged index SHA; Conventional Commit & branch guard | CI & Local |

## Delivered Showcase (`pbi/EnterpriseSales`)

- **Semantic Model (`pbi/EnterpriseSales/EnterpriseSales.SemanticModel/`):**
  - `8` tables (`DimDate`, `DimProduct`, `DimStore`, `DimCustomer`, `DimSalesRep`, `FactSales`, `FactBudget`, `_Measures`)
  - `7` `1:*` `Single`-direction relationships (`FactSales` 5-dim star + `FactBudget` 2-dim conformance)
  - `3` hierarchies (`Calendar Hierarchy`, `Product Hierarchy`, `Geography Hierarchy`) and `DimDate` marked as date table (`Date`)
  - `20` documented KPIs across `5` display folders (`01 Revenue & Margin`, `02 Time Intelligence`, `03 Budget & Variance`, `04 KPI & Formatting`, `05 Customer Analytics`)
  - `4` fail-closed RLS roles (`Region_NA`, `Region_EMEA`, `Region_APAC`, `Dynamic_SalesRep`)
- **PBIR Report (`pbi/EnterpriseSales/EnterpriseSales.Report/`):**
  - `4` pages (`Executive Overview`, `Regional Margin Analysis`, `Product Detail`, `Customer & Discounts`) with `44` visuals built exclusively via `pbir` CLI

## Prerequisites & Quickstart

- **OS:** Windows 11 (for Power BI Desktop / ADOMD / `dscmd` live gates `G3` VPAX, `G4`, `G5`, `G6` screenshots; offline CI gates run on `ubuntu-latest` + `windows-latest`)
- **Toolchain:** Python `>=3.12`, [`uv`](https://docs.astral.sh/uv/), Git, [`pbir-cli`](.agents/skills/pbir-cli/SKILL.md), Tabular Editor 2 (`TabularEditor.exe`), DAX Studio (`dscmd.exe` + `Microsoft.AnalysisServices.AdomdClient.dll`), Power BI Desktop + `powerbi-modeling-mcp`

```powershell
uv sync --group dev
uv run bi-agent doctor
uv run python scripts/generate_sample_data.py
```

## CLI & Workflow Reference

```powershell
# Toolchain diagnostics
uv run bi-agent doctor [--json]

# Individual gates
uv run bi-agent verify-design requirements/REQ-002-customer-discount-analytics.yaml
uv run bi-agent verify-model [--offline]
uv run bi-agent verify-dax
uv run bi-agent verify-rls --requirement requirements/REQ-002-customer-discount-analytics.yaml
uv run bi-agent verify-report [--offline]

# Full 7-gate evidence bundle (live Desktop or --offline for CI/headless)
uv run bi-agent verify-all --req-id REQ-002 --requirement requirements/REQ-002-customer-discount-analytics.yaml [--offline]

# Evidence-gated commit (requires staged tree SHA match in verification-report.json)
uv run bi-agent git-commit --evidence artifacts/runs/REQ-002/verification-report.json -m "feat(REQ-002): deliver verified BI requirement"
```

Multi-agent orchestration lives in [`workflows/bi_delivery/`](workflows/bi_delivery/workflow.py) (`workflow.json` + `workflow.py`) with two modes:
- `audit` (default): Runs Gate `G1` plus parallel specialist `research` auditors (`Model & DAX Hygiene Auditor` and `Report & Governance Auditor`) without mutating files.
- `deliver`: Runs `G1`, model/RLS delivery, PBIR report delivery, parallel specialist audits, `G1`–`G7` verification, and optional `bi-agent git-commit`.

## Testing, Guardrails & CI

```powershell
uv run ruff check --no-cache src tests scripts
uv run pytest tests/unit -p no:cacheprovider -v
uv run pytest tests/fault_injection -p no:cacheprovider -v
```

- **Runtime Guardrails:** [`.agents/hooks.json`](.agents/hooks.json) enforces [`pre_tool_guard.py`](src/bi_agent/hooks/pre_tool_guard.py) (blocking direct writes to `*.Report/definition/**/*.json` and unverified `git commit` calls) and [`post_tool_audit.py`](src/bi_agent/hooks/post_tool_audit.py).
- **GitHub Actions CI:** [`.github/workflows/bi-ci.yml`](.github/workflows/bi-ci.yml) runs `ruff`, `pytest tests/unit`, and `G1` on `ubuntu-latest`, plus `pbir-cli`, Tabular Editor 2 Portable, `bi-agent verify-all --offline` (`REQ-001` & `REQ-002`), and `pytest tests/fault_injection` on `windows-latest`.

## Documentation

- [Master Plan](docs/PLAN.md)
- [Phase State Checkpoint](docs/PHASE_STATE.md)
- [End-to-End Delivery Walkthrough](docs/WALKTHROUGH.md)
- [Architecture Decision Records (ADRs)](docs/adr/README.md)
