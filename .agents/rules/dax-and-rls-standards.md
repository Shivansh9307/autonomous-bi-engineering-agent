# DAX & Row-Level Security Standards

- **Reference Syntax:** Always qualify column references as `'Table'[Column]` (single quotes around table name) and keep measure references unqualified as `[Measure]`.
- **Safe Division & Variables:** Always use `DIVIDE(numerator, denominator)` instead of `/` for dynamic denominators; cache reused expressions with `VAR ... RETURN`.
- **Measure Hygiene:** Every visible measure must define `FormatString`, `DisplayFolder`, and a concise `Description` (`<= 200` characters).
- **Dimensional Hygiene:** Pure `1:*` (`Single` cross-filter) star schemas only. Hide foreign keys (`IsHidden = true`) and set `SummarizeBy = None` on non-additive numeric columns. Avoid `Double` for currency/financial metrics; use `Decimal`.
- **Fail-Closed RLS:** Static and dynamic (`USERPRINCIPALNAME()`) roles must never leave empty `FilterExpression` strings and must return `0` rows for unauthorized or unmapped principals.
