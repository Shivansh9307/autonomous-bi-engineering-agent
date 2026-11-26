---
name: bi-dimensional-modeling
description: Enterprise Kimball star-schema modeling standards for Power BI PBIP semantic models (`REQ-*.yaml`), enforcing pure 1:* single-direction relationships, Decimal financial types, hidden surrogate/FK keys, date table marking, SortByColumn, and the strict modeling tool cascade.
---

# Enterprise Dimensional Modeling Standard

Use this skill when designing, generating, or auditing Power BI semantic models against `requirements/REQ-*.yaml` and `config/bpa/enterprise-rules.json`.

## 1. Strict Tool Cascade
1. **`powerbi-modeling-mcp` first** (offline database creation, live Desktop edits, TMDL export).
2. **`TabularEditor.exe`** (`-S` C# TOM scripts, `-A` BPA analysis, `-TMDL` export).
3. **PowerShell TOM / ADOMD.NET** (`scripts/pbid/`, `connect-pbid` skill).
4. **Direct `.tmdl` edits strictly as last resort** (`tmdl` skill), followed immediately by TE2 load validation.

## 2. Kimball Star-Schema Rules (Gate G1 + BPA)
- **Pure `1:*` `Single` relationships:** Never create `*:*` or `Both` cross-filter relationships. Every fact foreign key joins to a unique dimension key (`FromCardinality = Many`, `ToCardinality = One`, `CrossFilteringBehavior = OneDirection`).
- **Hide surrogate & foreign keys:** Set `isHidden = true` on all `Dim*[...Key]` and `Fact*[...Key]` columns, plus raw additive/rate columns on fact tables when measures replace implicit aggregation.
- **`SummarizeBy = None`:** Set `summarizeBy = None` on all numeric columns (including visible integers like `Year` or `IsWeekend`, and hidden keys/fact columns) to prevent implicit aggregation (`ERR_NON_ADDITIVE_SUMMARIZE_NONE`).
- **No floating-point (`Double`) columns:** Use `Decimal` in TOM (`Currency.Type` in Power Query M) for all monetary, unit price/cost, discount, and rate columns (`ERR_AVOID_FLOAT_COLUMNS`).
- **VertiPaq MDX hygiene:** Set `isAvailableInMDX = false` on hidden columns that are NOT used in hierarchies, variations, or `SortByColumn` (`PERF_DISABLE_MDX_ON_HIDDEN_COLUMNS`). Keep `isAvailableInMDX = true` on `SortByColumn` helper columns (`QuarterNumber`, `MonthNumber`, `YearMonthSort`).

## 3. Date Table & Power Query M Hygiene
- **Mark as Date Table:** Call `MarkAsDateTable` on `DimDate` targeting `Date` (`DataCategory = Time`, `isKey = true` on `Date`). Keep integer `DateKey` (`YYYYMMDD`) hidden (`isKey = false`) for fact joins.
- **`SortByColumn`:** Bind `Quarter -> QuarterNumber`, `MonthName -> MonthNumber`, and `YearMonth -> YearMonthSort`.
- **No M-vs-Table Collisions:** Name the shared folder parameter `DataFolder` (never the same name as any model table).
- **Locale-safe CSV Parsing:** Always use `Csv.Document(File.Contents(DataFolder & "\\<File>.csv"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])` and pass explicit `"en-US"` culture to `Table.TransformColumnTypes(..., {...}, "en-US")`.

## 4. Verification Checklist
- `bi-agent verify-design requirements/<REQ>.yaml` -> Gate G1 PASS
- `bi-agent verify-model --project <PBIP_DIR> --offline` -> Gate G2 PASS & Gate G3 BPA `0` errors / `0` warnings
- `TabularEditor.exe <definition_dir> -S scripts/tom/audit_model_hygiene.csx` -> `bidirectional_relationships == 0`, `unhidden_fk_columns == 0`
