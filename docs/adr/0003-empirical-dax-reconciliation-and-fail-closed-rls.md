# ADR-0003: Empirical DAX Ground-Truth Reconciliation & Fail-Closed RLS

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Static BPA checks (`Gate G3`) verify DAX syntax patterns and metadata hygiene, but cannot detect semantic calculation bugs (wrong filter context, incorrect `SAMEPERIODLASTYEAR` alignment, or double-counted budget grain) or Row-Level Security (RLS) leaks. Furthermore, on standalone non-domain Windows workstations running Power BI Desktop's local Analysis Services engine, ADOMD.NET `EffectiveUserName` impersonation raises a Windows `CreateWithS4U` error when attempting to impersonate arbitrary UPNs such as `alice.emea@contoso.com`.

## Decision

1. **Deterministic Ground Truth (`data/ground_truth/expected_metrics.json`):**
   - `scripts/generate_sample_data.py` generates reproducible synthetic retail CSVs (`seed: 42`) and computes exact expected aggregates overall (`25,000` sales rows, `648` budget rows, `$48,744,150.82` net revenue), by fiscal year (`2024`–`2026`), by region (`NA`, `EMEA`, `APAC`), and per `DimSalesRep` UPN (`12` valid reps + `unauthorized.user@contoso.com` = `0` rows).
2. **Gate G4 (`src/bi_agent/gates/g4_dax_reconciliation.py`):**
   - Executes live DAX queries against the local Power BI Desktop engine and compares `42` assertions within `±0.01` numeric tolerance (`0.0005` for ratios, `0.0` for exact row/customer counts), alongside `dscmd benchmark` latency budgets (`<= 1500 ms` cold, `<= 500 ms` warm).
3. **Gate G5 & Fail-Closed RLS (`src/bi_agent/gates/g5_rls_verification.py`):**
   - Tests every static role (`Region_NA`, `Region_EMEA`, `Region_APAC`) for exact row counts, revenue, and zero cross-region leakage.
   - Dynamic role `Dynamic_SalesRep` evaluates:
     `VAR _Custom = CUSTOMDATA() VAR _Identity = IF ( _Custom <> "", _Custom, USERPRINCIPALNAME() ) RETURN 'DimSalesRep'[UserPrincipalName] = _Identity`
   - When ADOMD `EffectiveUserName` hits `CreateWithS4U` on local non-domain Windows, Gate `G5` first asserts that the un-impersonated local Windows principal (`<local_user>`) fails closed to `0` rows, and then tests per-UPN isolation (`alice.emea@contoso.com`, `bob.na@contoso.com`, `unauthorized.user@contoso.com`) via `CustomData` fallback.

## Consequences

- **Positive:** Both calculation logic and security boundaries are verified empirically against the live VertiPaq engine (`42` DAX assertions + `7` RLS scenarios passing in `artifacts/runs/REQ-002/verification-report.json`).
- **Positive:** Fail-closed RLS is enforced for both unknown UPNs and unmapped local Windows users (`0` rows).
- **Trade-off:** Adding new KPIs requires extending `scripts/generate_sample_data.py` / `expected_metrics.json` and `g4_dax_reconciliation.py` in tandem.

## Verification & References

- Ground Truth & Generator: `scripts/generate_sample_data.py`, `data/ground_truth/expected_metrics.json`
- Gates & ADOMD Scripts: `src/bi_agent/gates/g4_dax_reconciliation.py`, `src/bi_agent/gates/g5_rls_verification.py`, `scripts/pbid/run_dax_impersonated.ps1`
- Fault Injection Tests: `tests/fault_injection/test_fault_injection.py` (`test_fi05_g4_catches_dax_drift_and_perf_budget_breach`, `test_fi06_g5_catches_cross_region_leak_and_fail_open_rls`)
