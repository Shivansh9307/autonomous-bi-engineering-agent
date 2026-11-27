---
name: bi-data-profiling
description: Gate G0 raw flat-file profiling, defect taxonomy, human-in-the-loop policy sign-off, and raw-to-staged financial reconciliation standards before Kimball semantic modeling.
---

# Gate G0: Raw Data Profiling & Staging Reconciliation

Use this skill whenever onboarding raw CSV/TSV flat files for any business domain before designing or mutating a Power BI semantic model (`G1`–`G7`).

## 1. Preflight Profiling (`bi-agent profile-data`)

Always inspect raw source files before proposing tables, relationships, or Power Query M scripts:

```powershell
uv run bi-agent profile-data <raw_path> [--req-id REQ-XXX] [--output-dir artifacts/runs/REQ-XXX]
```

This emits structured `data-profile.json` and `data-profile.md` artifacts covering schema inference, delimited defects, proposed Kimball `Fact*`/`Dim*` table decomposition, and concrete policy questions for any detected defects.

## 2. Eight Defect Categories & Human-Signed Policy Decisions

Whenever `profile-data` surfaces any of the 8 defect categories, record an explicit human-approved decision under `data_profiling.policy_decisions` in `requirements/<REQ>.yaml` before staging:

1. `duplicate_rows` -> `deduplication_policy` (drop exact duplicates and emit to quarantine)
2. `duplicate_keys` -> `key_uniqueness_policy` (deterministic keep-first/latest + quarantine conflicts)
3. `mixed_date_formats` -> `date_parsing_policy` (explicit locale precedence -> ISO `YYYY-MM-DD`)
4. `stringified_numeric` -> `currency_numeric_policy` (strip currency/`%`/commas, convert accounting `(amt)` to `-amt`, cast to Decimal)
5. `whitespace_casing` -> `whitespace_casing_policy` (trim leading/trailing whitespace and standardize casing)
6. `null_or_orphan_fk` -> `orphan_fk_policy` (route unmapped/null keys to explicit Unknown `-1` dimension member or quarantine)
7. `negative_credit_note` -> `credit_note_sign_policy` (preserve signed negative amounts / split Credit Memo measures)
8. `grain_mismatch` -> `grain_resolution_policy` (enforce atomic line-item grain vs. repeated header totals)

## 3. Staging & Quarantine Accounting Invariants

- **Row Conservation:** `raw_rows == staged_rows + quarantine_rows` (`G0_ROW_RECON_MISMATCH` fails closed otherwise). Never silently drop dirty rows.
- **Financial Conservation:** For every configured amount column in `reconcile_amount_columns`, `abs(sum(raw) - (sum(staged) + sum(quarantine))) <= tolerance` (default `0.01`, `G0_AMOUNT_RECON_MISMATCH`).
- **Staged Cleanliness (`G0_STAGED_DEFECT_REMAINING`):** Zero exact duplicate rows, zero duplicate business keys, zero leading/trailing whitespace, `100%` ISO `YYYY-MM-DD` date columns, zero dirty currency/percent strings, and explicit `-` negative checks unless listed in `allow_negative_columns`.
- **Referential Integrity (`G0_ORPHAN_FK`):** Every staged foreign key must match an active dimension key or the explicitly configured `allow_unknown_key` sentinel (e.g., `"-1"`).

## 4. Gate `G0` Verification

```powershell
uv run bi-agent verify-data requirements/<REQ>.yaml
uv run bi-agent verify-all --req-id <REQ-ID> --requirement requirements/<REQ>.yaml [--offline]
```
