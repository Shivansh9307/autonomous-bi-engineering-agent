# ADR-0006: CUSTOMDATA() RLS Testing Shim & Power BI Service Hardening

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

In local Power BI Desktop development on non-domain-joined Windows workstations, Analysis Services rejects ADOMD.NET `EffectiveUserName` impersonation with a Windows `CreateWithS4U` security error. To preserve automated, empirical Row-Level Security (RLS) verification in Gate `G5` (`bi-agent verify-rls`) across multiple sales-rep principals (`alice.emea@contoso.com`, `bob.na@contoso.com`, `unauthorized.user@contoso.com`), role `Dynamic_SalesRep` evaluates a `CUSTOMDATA()` fallback before `USERPRINCIPALNAME()`:

```dax
VAR _Custom = CUSTOMDATA ()
VAR _Identity = IF ( _Custom <> "", _Custom, USERPRINCIPALNAME () )
RETURN
    'DimSalesRep'[UserPrincipalName] = _Identity
```

While safe on a local single-user Desktop workstation (where the local Windows identity itself fails closed to `0` rows when `CUSTOMDATA()` is empty), leaving `CUSTOMDATA()` precedence active in a published Power BI Service or Fabric semantic model introduces an **identity spoofing vulnerability**: any user with Read/Build permission connecting via XMLA endpoint or **Analyze in Excel** can pass `CustomData=alice.emea@contoso.com` in their connection string and assume another sales representative's identity.

## Decision

1. **Mandatory Pre-Publish Hardening Rule:**
   - Treat `CUSTOMDATA()` in `Dynamic_SalesRep` strictly as a **local Desktop/ADOMD.NET automated testing shim**.
   - Before publishing `pbi/EnterpriseSales` to Power BI Service or Microsoft Fabric (enforced in the release/deployment pipeline prior to XMLA/PBIX publish), strip `CUSTOMDATA()` precedence from `Dynamic_SalesRep`'s `DimSalesRep` table permission so the production expression evaluates **solely**:
     ```dax
     'DimSalesRep'[UserPrincipalName] = USERPRINCIPALNAME ()
     ```
2. **Cross-Fact Deny Invariant:**
   - Retain `tablePermission FactBudget = FALSE ()` on `Dynamic_SalesRep` across both local and Service deployments so sales representatives and unauthorized principals can never read store-level budget targets (`FactBudget`).
3. **Empirical Verification Split:**
   - Local Gate `G5` tests `<local_user>` (empty `CUSTOMDATA()`) fail-closed to `0` rows on both `FactSales` and `FactBudget`, then exercises per-UPN isolation via `CustomData` fallback.
   - Offline Gate `G1` + `G5` (`--offline`) enforce multi-fact RLS coverage so neither `FactSales` nor `FactBudget` can be left unguarded.

## Consequences

- **Positive:** Automated local CI/Desktop testing can empirically verify multi-principal dynamic RLS isolation without requiring an Active Directory domain or Service workspace.
- **Positive:** Explicit pre-publish hardening prevents XMLA / Analyze in Excel `CustomData` UPN spoofing in production.
- **Trade-off:** Production deployment pipelines must execute the `Dynamic_SalesRep` expression rewrite (or maintain a Service deployment parameter/script) and validate the published model against Entra ID test accounts.

## Verification & References

- Role Definition: `pbi/EnterpriseSales/EnterpriseSales.SemanticModel/definition/roles/Dynamic_SalesRep.tmdl`
- Gate `G5` & ADOMD Wrapper: `src/bi_agent/gates/g5_rls_verification.py`, `scripts/pbid/run_dax_impersonated.ps1`
- Related ADR: [ADR-0003](0003-empirical-dax-reconciliation-and-fail-closed-rls.md)
