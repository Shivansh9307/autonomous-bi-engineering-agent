# ADR-0001: Hybrid Multi-Agent Orchestration & Deterministic Verification Harness

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Power BI engineering spans multiple domains—dimensional modeling, DAX calculation authoring, Row-Level Security (RLS), and PBIR report layout—each with brittle metadata schemas and subtle runtime failure modes. Relying purely on LLM reasoning to self-certify BI artifacts leads to silent regressions (e.g., implicit `Double` columns, raw `/` division, fail-open RLS predicates, or malformed PBIR JSON). Conversely, purely imperative scripts cannot interpret ambiguous business requirements (`requirements/*.yaml`) or inspect visual page renders for overlapping labels.

## Decision

Split the system into two complementary layers:

1. **Probabilistic Multi-Agent Orchestration (`.agents/` & `workflows/bi_delivery/`):**
   - Domain rules in `.agents/rules/` (`bi-verification-gate.md`, `dax-and-rls-standards.md`, `pbip-tool-cascade.md`), specialized skills in `.agents/skills/` (including `bi-dimensional-modeling` and `bi-dax-and-rls-engineering`), and a `run_workflow` orchestrator (`workflows/bi_delivery/workflow.json`, `workflows/bi_delivery/workflow.py`) supporting both `audit` and `deliver` modes with parallel specialist `research` auditors.
2. **Deterministic Python Verification Harness (`src/bi_agent/`):**
   - Strict Pydantic v2 contracts (`src/bi_agent/schemas/`), 6 toolchain adapters (`src/bi_agent/adapters/`), and 7 deterministic gates (`G1`–`G7` in `src/bi_agent/gates/`) orchestrated by `run_all_gates` (`src/bi_agent/gates/runner.py`) and exposed via the `bi-agent` Typer CLI (`src/bi_agent/cli.py`).
   - Every `bi-agent verify-all` run persists a machine-readable `verification-report.json` and human-readable `verification-report.md` (`VerificationEvidenceBundle`) under `artifacts/runs/<REQ-ID>/`.

## Consequences

- **Positive:** Agents retain flexibility for modeling, DAX authoring, and visual layout critique while hard correctness, security, performance, and structural invariants are enforced deterministically outside the LLM context.
- **Positive:** All verification outputs are structured, repeatable, and testable in unit and fault-injection suites (`tests/unit/`, `tests/fault_injection/`).
- **Trade-off:** Maintaining synchronized requirement schemas (`src/bi_agent/schemas/requirement.py`) and gate adapters adds engineering overhead whenever new metric families or tool flags are introduced.

## Verification & References

- Harness CLI & Gate Runner: `src/bi_agent/cli.py`, `src/bi_agent/gates/runner.py`
- Multi-Agent Workflow: `workflows/bi_delivery/workflow.json`, `workflows/bi_delivery/workflow.py`
- Unit & Workflow Tests: `tests/unit/test_schemas.py`, `tests/unit/test_gates.py`, `tests/unit/test_workflows.py`
