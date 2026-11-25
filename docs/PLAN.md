# Autonomous BI Engineering Agent — Master Plan

## Architecture
Hybrid Antigravity multi-agent orchestration (`.agents/` skills, rules, hooks, `run_workflow`) backed by a deterministic Python verification harness (`src/bi_agent/`) and evidence-gated Git workflow.

## 7-Gate Verification Architecture
1. **G1 (Design Contract):** Validate star schema, grain, KPI specs, and RLS rules (`Pydantic`).
2. **G2 (PBIP / TMDL Structure):** `validate_pbip.py` + `TabularEditor.exe` TMDL load check (`0` errors).
3. **G3 (BPA & VertiPaq):** `TabularEditor.exe -A config/bpa/enterprise-rules.json` (`0` Errors) + `dscmd vpax`.
4. **G4 (Live DAX Truth & Perf):** `powerbi-modeling-mcp` / ADOMD.NET DAX execution reconciled within `+-0.01` against `data/ground_truth/expected_metrics.json` + `dscmd benchmark`.
5. **G5 (Live RLS Impersonation):** `Roles` + `EffectiveUserName` DAX queries verifying zero cross-tenant leakage and fail-closed `0` rows for unauthorized UPNs.
6. **G6 (Report QA & Visual Render):** `pbir validate --all` + `pbir desktop refresh -m` + `pbir desktop screenshot --all` + multimodal PNG inspection.
7. **G7 (Git Commit Gate):** Commit permitted only when `verification-report.json` passes and matches the staged Git tree SHA.

## 6 Phases & 3-Window Token Cadence
- **Window A:** Phase 1 (Foundation, `doctor`, Ground-Truth Data) -> `/compact` -> Phase 2 (Verification Engine, BPA, Hooks, Unit Tests)
- **Window B:** Phase 3 (Semantic Model, DAX, RLS + Live G1-G5) -> `/compact` -> Phase 4 (PBIR Report + Desktop PNG G6)
- **Window C:** Phase 5 (`run_workflow` Orchestrator + `REQ-002` E2E + Fault Injection) -> `/compact` -> Phase 6 (CI/CD, ADRs, Docs, Walkthrough)
