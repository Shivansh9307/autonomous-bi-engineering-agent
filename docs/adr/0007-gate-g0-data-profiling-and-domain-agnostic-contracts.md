# ADR-0007: Gate G0 Data Profiling & Domain-Agnostic Declarative Contracts

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

In v1 of the **Autonomous BI Engineering Agent System**, Gates `G4`, `G5`, and `G6` assumed a clean retail star schema (`pbi/EnterpriseSales`, `DimDate`/`DimStore`/`FactSales`/`FactBudget`) and relied on hardcoded DAX queries, ground-truth schemas, and RLS query builders in Python. Onboarding a new domain (for example healthcare claims or procurement AP spend) or starting from messy raw flat files with mixed date formats, stringified currency, duplicate rows, and orphan foreign keys required editing core Python modules and bypassed automated pre-modeling reconciliation.

## Decision

1. **Pre-Modeling Gate `G0` (`bi-agent profile-data` & `bi-agent verify-data`):**
   - Profile raw CSV/TSV files before Kimball modeling to detect 8 defect categories (`duplicate_rows`, `duplicate_keys`, `mixed_date_formats`, `stringified_numeric`, `whitespace_casing`, `null_or_orphan_fk`, `negative_credit_note`, `grain_mismatch`), propose `Fact*`/`Dim*` table decomposition, and emit human-in-the-loop `PolicyQuestion` prompts (`data-profile.{json,md}`).
   - Require signed `data_profiling.policy_decisions` in `requirements/REQ-*.yaml` and enforce fail-closed raw-to-staged conservation (`raw_rows == staged_rows + quarantine_rows`), Decimal financial sum conservation (`±0.01`), ISO `YYYY-MM-DD` dates, zero remaining dirty cells, and foreign-key integrity (`allow_unknown_key` support for explicit `-1` Unknown members).

2. **Contract-Driven Verification (`verification:` + Domain-Agnostic `GroundTruthDataset`):**
   - Replace hardcoded retail DAX/RLS queries with declarative `verification.dax_checks`, `verification.rls_checks`, and `verification.report_checks` inside `requirements/REQ-*.yaml`.
   - Store expected metrics in a schema-free `GroundTruthDataset` navigated via greedy dotted-key lookup (`resolve_dot_path`), supporting keys containing periods (e.g., email UPNs).
   - Render dynamic multi-fact RLS Markdown tables (`fact_tables` / `fact_counts`) in `VerificationEvidenceBundle`.

3. **Multi-Project Path Protection & Fail-Closed Desktop Discovery:**
   - Resolve `project:` (`pbip_path`, `semantic_model_path`, `report_path`, `ground_truth_path`, `desktop_report_name`) from CLI flags -> requirement contract -> `config/toolchain.yaml`, eliminating hardcoded project defaults in `ProjectConfig`.
   - Protect any `*.Report/definition/**/*.json` and `*.SemanticModel/` path across the repository via `PreToolUse` hooks and `git_adapter.is_pbi_path`.
   - Enforce `-ReportName` matching in `scripts/pbid/discover_pbid_port.ps1` (`MainWindowTitle`/`open_target`/`database`) with fail-closed exit when no active Power BI Desktop instance matches.

## Consequences

- **Positive:** Any domain (`Healthcare`, `Procurement`, `Retail`, etc.) can be onboarded strictly by authoring a `requirements/REQ-*.yaml` contract without modifying `src/bi_agent/` or `workflows/bi_delivery/`.
- **Positive:** Silent row drops, financial rounding drift, ambiguous date locales, and wrong-Desktop-instance queries fail closed deterministically.
- **Trade-off:** Requirement YAML contracts are richer because they declare concrete DAX reconciliation queries, expected report pages, and messy-data reconciliation rules alongside modeling specs.

## Verification & References

- Gate `G0` Engine & Schemas: `src/bi_agent/gates/g0_data_profiling.py`, `src/bi_agent/schemas/profiling.py`, `.agents/skills/bi-data-profiling/SKILL.md`
- Contract & Ground Truth Schemas: `src/bi_agent/schemas/requirement.py`, `src/bi_agent/schemas/test_suite.py`
- Contract-Driven Gates: `src/bi_agent/gates/{g1,g3,g4,g5,g6,runner}.py`, `scripts/pbid/discover_pbid_port.ps1`
- Test Suites: `tests/unit/test_g0_profiling.py`, `tests/unit/test_domain_agnostic.py`, `tests/unit/test_e2e_messy_pipeline.py`, `tests/fault_injection/test_fault_injection.py`
