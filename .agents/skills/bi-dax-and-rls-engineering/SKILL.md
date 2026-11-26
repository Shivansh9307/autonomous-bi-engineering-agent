---
name: bi-dax-and-rls-engineering
description: Enterprise DAX measure authoring and fail-closed Row-Level Security (RLS) standards with empirical ADOMD.NET / MCP reconciliation against ground truth (Gates G4 & G5).
---

# Enterprise DAX & Fail-Closed RLS Engineering

Use this skill when authoring measures, time-intelligence calculations, KPIs, and security roles in Power BI semantic models.

## 1. DAX Authoring & Measure Hygiene
- **Dedicated Measure Table:** Place all business KPIs in `_Measures` (`discourageImplicitMeasures = true` on the model) with a single hidden `_Placeholder` column (`isHidden = true`, `isAvailableInMDX = false`).
- **Qualification Standard:**
  - Always qualify columns with single-quoted table names: `'FactSales'[NetRevenue]`, `'DimDate'[Date]` (`ERR_DAX_COLUMNS_FULLY_QUALIFIED`).
  - Always reference measures bare without a table prefix: `[Total Revenue]`, `[Gross Margin]` (`ERR_DAX_MEASURES_UNQUALIFIED`).
- **Safe Division & Variables:**
  - Always use `DIVIDE(numerator, denominator)` instead of `/` (`ERR_DAX_DIVISION_OPERATOR`).
  - Cache reused expressions in `VAR ... RETURN` blocks.
  - Never use `IFERROR(...)` (`DAX_AVOID_IFERROR`) or `FILTER(ALL(...))` (`DAX_AVOID_FILTER_ALL`).
- **Mandatory Metadata:** Every visible measure must define:
  - `FormatString` (e.g., `$#,##0.00`, `0.00%`, `#,##0`, `@`)
  - `DisplayFolder` (e.g., `01 Revenue & Margin`, `02 Time Intelligence`, `03 Budget & Variance`, `04 KPI & Formatting`)
  - `Description` (`<= 200` characters, explaining business definition)

## 2. Time Intelligence Patterns
- Always target the marked date table column `'DimDate'[Date]`:
  - `Revenue PY`: `CALCULATE ( [Total Revenue], SAMEPERIODLASTYEAR ( 'DimDate'[Date] ) )`
  - `Revenue YoY`: `VAR _Current = [Total Revenue] VAR _Prior = [Revenue PY] RETURN IF ( NOT ISBLANK ( _Prior ), _Current - _Prior )`
  - `Revenue YoY %`: `VAR _Prior = [Revenue PY] RETURN DIVIDE ( [Revenue YoY], _Prior )`
  - `Revenue YTD`: `CALCULATE ( [Total Revenue], DATESYTD ( 'DimDate'[Date] ) )`
  - `Revenue Rolling 3M`: `CALCULATE ( [Total Revenue], DATESINPERIOD ( 'DimDate'[Date], MAX ( 'DimDate'[Date] ), -3, MONTH ) )`

## 3. Fail-Closed Row-Level Security (RLS)
- Every `ModelRole` must use `modelPermission = Read` and non-empty `FilterExpression` strings (`ERR_RLS_EMPTY_FILTER`).
- **Static Role Example:** `'DimStore'[Region] = "NA"`
- **Dynamic Role Example:** `'DimSalesRep'[UserPrincipalName] = USERPRINCIPALNAME()`
- Because relationships are `1:*` `Single` (`DimStore -> FactSales/FactBudget`, `DimSalesRep -> FactSales`), filtering the dimension propagates cleanly to facts without bidirectional cross-filtering.
- Unauthorized or unmapped UPNs (e.g., `unauthorized.user@contoso.com`) must return **0 rows** (fail closed).

## 4. Empirical Verification (Gates G4 & G5)
- `bi-agent verify-dax --ground-truth data/ground_truth/expected_metrics.json` -> reconciles overall, by-year, and by-region metrics within `+-0.01` (`+-0.0005` for ratios) and enforces `dscmd benchmark` cold/warm latency budgets.
- `bi-agent verify-rls --requirement requirements/REQ-001-regional-sales-margin.yaml --ground-truth data/ground_truth/expected_metrics.json` -> verifies static and dynamic `Roles` + `EffectiveUserName` isolation via `scripts/pbid/run_dax_impersonated.ps1`.
