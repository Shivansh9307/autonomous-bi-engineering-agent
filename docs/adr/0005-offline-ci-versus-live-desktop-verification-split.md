# ADR-0005: Offline CI vs. Live Power BI Desktop Verification Split

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Hosted CI runners (GitHub-hosted `ubuntu-latest` and `windows-latest`) do not run an interactive Power BI Desktop GUI session, a local Analysis Services (`msmdsrv.exe`) engine loaded with `.pbix`/`.pbip` data, or the Desktop Bridge local API. However, PBIP/TMDL structure validation, Tabular Editor 2 Best Practice Analyzer (`15` rules), `pbir validate`, Pydantic design contracts, and fail-closed fault-injection tests can all run headlessly without Power BI Desktop.

## Decision

Formalize a two-tier verification topology via the `--offline` flag in `bi-agent verify-all`, `verify-model`, and `verify-report`:

1. **Hosted CI Tier (`.github/workflows/bi-ci.yml`):**
   - `python-lint-and-unit` (`ubuntu-latest`): `ruff check`, `pytest tests/unit`, and `bi-agent verify-design` (`Gate G1`) for `REQ-001` and `REQ-002`.
   - `bi-offline-gates` (`windows-latest`): Installs `pbir-cli` and Tabular Editor 2 Portable, then runs `bi-agent verify-all --offline` for `REQ-001` and `REQ-002` (executing `G1` Design Contract, `G2` PBIP & TE2 TMDL load, `G3` TE2 BPA static audit, `G6` `pbir validate`, `G7` Git Governance while explicitly recording `G4` and `G5` as `SKIP`) plus `pytest tests/fault_injection`.
2. **Live Desktop Tier (Local / Self-Hosted Windows Workstation):**
   - Full `bi-agent verify-all` (without `--offline`) executes all 7 gates end to end: `G3` live `dscmd vpax` export, `G4` live ADOMD.NET DAX ground-truth reconciliation + `dscmd benchmark`, `G5` live ADOMD.NET RLS impersonation matrix, and `G6` `pbir validate --all` + `pbir desktop refresh -m` + `pbir desktop screenshot --all` PNG capture.

## Consequences

- **Positive:** Every pull request and push to `main` gets deterministic static, structural, BPA, PBIR, and fault-injection coverage on standard GitHub-hosted runners without requiring Desktop licenses or GUI sessions.
- **Positive:** Offline runs clearly mark `G4`/`G5` as `SKIP` rather than falsely claiming live DAX or RLS execution.
- **Trade-off:** Live DAX, RLS, VPAX, and Desktop PNG rendering checks remain gated on local workstation or self-hosted Windows runner execution prior to merging model/report changes.

## Verification & References

- CI Workflow: `.github/workflows/bi-ci.yml`
- Gate Runner & Offline Support: `src/bi_agent/gates/runner.py`, `src/bi_agent/gates/g3_bpa_vpax.py`, `src/bi_agent/gates/g6_report_visuals.py`
- CI & Docs Tests: `tests/unit/test_ci_and_docs.py`
