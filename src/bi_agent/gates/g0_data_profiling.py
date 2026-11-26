"""Gate G0: Messy flat-file profiling, defect detection, and raw-to-staged reconciliation."""

from __future__ import annotations

import csv
import re
import time
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bi_agent.schemas.evidence import GateFinding, GateResult
from bi_agent.schemas.profiling import (
    ColumnProfile,
    DataProfileReport,
    DefectCategory,
    DefectFinding,
    FileProfile,
    PolicyQuestion,
    TableDecompositionProposal,
    TableReconciliationResult,
)
from bi_agent.schemas.requirement import (
    BusinessRequirement,
    StagingReconciliationRule,
    load_requirement,
)

CURRENCY_TOKEN_RE = re.compile(r"([$€£¥]|\b(?:USD|EUR|GBP|INR|JPY|CAD|AUD)\b)", re.IGNORECASE)
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")
SLASH_YMD_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
SLASH_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
DASH_DMY_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
TEXT_MON_DMY_RE = re.compile(
    r"^(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{2,4})$",
    re.IGNORECASE,
)
TEXT_MON_MDY_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s*(\d{4})$",
    re.IGNORECASE,
)
NULL_SENTINELS = {"", "null", "none", "nan", "n/a", "na", "tbd", "unknown"}

POLICY_CATALOG: dict[DefectCategory, tuple[str, str, str]] = {
    "duplicate_rows": (
        "deduplication_policy",
        "Exact duplicate rows were detected. "
        "Should staging drop exact duplicates and log them to quarantine?",
        "Drop exact duplicate rows and emit them to quarantine so raw == staged + quarantine.",
    ),
    "duplicate_keys": (
        "key_uniqueness_policy",
        "Duplicate business keys were detected. "
        "Keep first, keep latest, or quarantine colliding rows?",
        "Keep deterministic first occurrence and quarantine conflicting duplicates.",
    ),
    "mixed_date_formats": (
        "date_parsing_policy",
        "Mixed or ambiguous date formats (e.g. MM/DD/YYYY vs DD/MM/YYYY) exist. "
        "Which locale takes precedence?",
        "Parse explicit ISO/text dates first; disambiguate slash dates using contract locale "
        "and output ISO YYYY-MM-DD.",
    ),
    "stringified_numeric": (
        "currency_numeric_policy",
        "Stringified currency/percent/accounting-parentheses values exist. "
        "Normalize to Decimal with accounting parens negative?",
        "Strip currency symbols and commas, convert (amount) to -amount, and cast to Decimal.",
    ),
    "whitespace_casing": (
        "whitespace_casing_policy",
        "Leading/trailing whitespace or inconsistent casing exists in attributes. "
        "Trim and standardize casing?",
        "Trim leading/trailing whitespace, collapse internal runs, "
        "and normalize canonical Title/Upper case.",
    ),
    "null_or_orphan_fk": (
        "orphan_fk_policy",
        "Null or unmapped foreign keys were detected. "
        "Map to an explicit Unknown (-1) dimension member or quarantine?",
        "Route unmapped/null dimension references to an explicit Unknown (-1) surrogate member.",
    ),
    "negative_credit_note": (
        "credit_note_sign_policy",
        "Negative values / credit memos were detected in amount columns. "
        "Preserve signed amounts or split into Credit Memo measures?",
        "Preserve signed negative amounts and expose dedicated Credit Memo / Net Spend measures.",
    ),
    "grain_mismatch": (
        "grain_resolution_policy",
        "Potential grain mismatch (repeated header totals or mixed date grain) was detected. "
        "Confirm target fact grain.",
        "Enforce atomic line-item grain for transactional facts "
        "and first-of-month DateKey for budget facts.",
    ),
}


def parse_numeric_value(raw: str) -> tuple[Decimal | None, bool]:
    """Parse plain or dirty stringified numeric values, returning (Decimal, has_dirty_format)."""
    if raw is None:
        return None, False
    stripped = raw.strip()
    if not stripped:
        return None, False

    has_dirty_format = False
    negative = False
    work = stripped

    if work.startswith("(") and work.endswith(")") and len(work) > 2:
        negative = True
        has_dirty_format = True
        work = work[1:-1].strip()

    if CURRENCY_TOKEN_RE.search(work):
        has_dirty_format = True
        work = CURRENCY_TOKEN_RE.sub("", work).strip()

    if work.endswith("%"):
        has_dirty_format = True
        work = work[:-1].strip()

    if "," in work:
        has_dirty_format = True
        work = work.replace(",", "")

    if work.startswith("+"):
        work = work[1:].strip()

    if not work:
        return None, False

    try:
        val = Decimal(work)
    except InvalidOperation:
        return None, False

    if not val.is_finite():
        return None, False

    if negative:
        val = -abs(val)
    return val, has_dirty_format


def detect_date_format(raw: str) -> str | None:
    """Detect common flat-file date representation patterns."""
    if raw is None:
        return None
    val = raw.strip()
    if not val:
        return None

    m_iso = ISO_DATE_RE.match(val)
    if m_iso:
        month, day = int(m_iso.group(2)), int(m_iso.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return "ISO_YYYY_MM_DD"
        return None

    m_symd = SLASH_YMD_RE.match(val)
    if m_symd:
        month, day = int(m_symd.group(2)), int(m_symd.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return "SLASH_YYYY_MM_DD"
        return None

    for pattern, fmt_prefix in ((SLASH_DMY_RE, "SLASH"), (DASH_DMY_RE, "DASH")):
        m = pattern.match(val)
        if m:
            first, second = int(m.group(1)), int(m.group(2))
            if first > 12 and 1 <= second <= 12 and first <= 31:
                return f"UK_DD_MM_YYYY_{fmt_prefix}"
            if second > 12 and 1 <= first <= 12 and second <= 31:
                return f"US_MM_DD_YYYY_{fmt_prefix}"
            if 1 <= first <= 12 and 1 <= second <= 12:
                return f"AMBIGUOUS_{fmt_prefix}_DATE"
            return None

    if TEXT_MON_DMY_RE.match(val) or TEXT_MON_MDY_RE.match(val):
        return "TEXT_MON_DATE"

    return None


def _detect_delimiter(file_path: Path) -> str:
    with file_path.open("r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            if line.strip():
                return "\t" if line.count("\t") > line.count(",") else ","
    return ","


def _read_delimited_rows(file_path: Path) -> tuple[list[str], list[dict[str, str]], str]:
    delimiter = _detect_delimiter(file_path)
    with file_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        headers = [h for h in (reader.fieldnames or []) if h is not None]
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            rows.append({col: (raw_row.get(col) or "") for col in headers})
    return headers, rows, delimiter


def _is_boolean_literal(val: str) -> bool:
    return val.strip().lower() in {"true", "false", "0", "1", "yes", "no"}


def _looks_like_date_column(col_name: str) -> bool:
    low = col_name.lower()
    return any(tok in low for tok in ("date", "dt", "period", "month")) and "key" not in low


def _looks_like_key_or_fk(col_name: str) -> bool:
    low = col_name.lower()
    return low.endswith(("key", "id", "code", "upn"))


def _looks_like_amount_or_qty(col_name: str) -> bool:
    low = col_name.lower()
    return any(
        tok in low
        for tok in (
            "amount",
            "amt",
            "total",
            "spend",
            "revenue",
            "price",
            "cost",
            "budget",
            "discount",
            "tax",
            "qty",
            "quantity",
            "units",
            "balance",
        )
    )


def _profile_single_file(
    file_path: Path,
    business_keys: list[str] | None = None,
) -> tuple[FileProfile, list[dict[str, str]], list[str]]:
    headers, rows, delimiter = _read_delimited_rows(file_path)
    row_tuples = [tuple(row[col] for col in headers) for row in rows]
    dup_row_count = len(row_tuples) - len(set(row_tuples))

    defects: list[DefectFinding] = []
    if dup_row_count > 0:
        counts = Counter(row_tuples)
        samples = [
            ", ".join(f"{h}={v}" for h, v in zip(headers, tup, strict=False))
            for tup, cnt in counts.items()
            if cnt > 1
        ][:3]
        defects.append(
            DefectFinding(
                category="duplicate_rows",
                file=file_path.name,
                count=dup_row_count,
                severity="warn",
                summary=f"{dup_row_count} exact duplicate row(s) detected.",
                samples=samples,
            )
        )

    if business_keys and all(k in headers for k in business_keys):
        seen_keys: set[tuple[str, ...]] = set()
        dup_key_samples: list[str] = []
        dup_key_count = 0
        for row in rows:
            key_tup = tuple(row[k].strip() for k in business_keys)
            if key_tup in seen_keys:
                dup_key_count += 1
                if len(dup_key_samples) < 3:
                    dup_key_samples.append("|".join(key_tup))
            else:
                seen_keys.add(key_tup)
        if dup_key_count > 0:
            defects.append(
                DefectFinding(
                    category="duplicate_keys",
                    file=file_path.name,
                    column="+".join(business_keys),
                    count=dup_key_count,
                    severity="warn",
                    summary=(
                        f"{dup_key_count} duplicate business key(s) on "
                        f"({', '.join(business_keys)})."
                    ),
                    samples=dup_key_samples,
                )
            )

    col_profiles: list[ColumnProfile] = []
    date_cols_detected: list[str] = []
    numeric_cols_detected: list[str] = []
    id_cols_detected: list[str] = []

    for col in headers:
        raw_vals = [row[col] for row in rows]
        non_empty = [v for v in raw_vals if v.strip().lower() not in NULL_SENTINELS]
        null_count = len(raw_vals) - len(non_empty)
        distinct_vals = {v.strip() for v in non_empty}
        samples = list(dict.fromkeys(v.strip() for v in non_empty))[:5]

        ws_defects = [v for v in raw_vals if v != v.strip() or "  " in v.strip()]
        ws_count = len(ws_defects)

        date_formats: Counter[str] = Counter()
        unparsed_date_samples: list[str] = []
        parsed_nums: list[tuple[Decimal, bool, str]] = []
        bool_count = 0

        for val in non_empty:
            fmt = detect_date_format(val)
            if fmt:
                date_formats[fmt] += 1
            elif _looks_like_date_column(col) and len(unparsed_date_samples) < 3:
                unparsed_date_samples.append(val)

            num_val, is_dirty = parse_numeric_value(val)
            if num_val is not None:
                parsed_nums.append((num_val, is_dirty, val))
            if _is_boolean_literal(val):
                bool_count += 1

        n_non_empty = len(non_empty)
        date_ratio = sum(date_formats.values()) / n_non_empty if n_non_empty else 0.0
        num_ratio = len(parsed_nums) / n_non_empty if n_non_empty else 0.0
        bool_ratio = bool_count / n_non_empty if n_non_empty else 0.0

        if n_non_empty == 0:
            inferred_type = "empty"
        elif date_ratio >= 0.5 or (_looks_like_date_column(col) and sum(date_formats.values()) > 0):
            inferred_type = "date"
            date_cols_detected.append(col)
        elif bool_ratio == 1.0 and set(v.strip().lower() for v in non_empty).issubset(
            {"true", "false", "yes", "no"}
        ):
            inferred_type = "boolean"
        elif num_ratio >= 0.6:
            all_int = all(num == num.to_integral_value() for num, _, _ in parsed_nums)
            inferred_type = (
                "integer" if all_int and not _looks_like_amount_or_qty(col) else "decimal"
            )
            numeric_cols_detected.append(col)
        else:
            inferred_type = "string"

        if _looks_like_key_or_fk(col):
            id_cols_detected.append(col)

        dirty_num_vals = [raw_v for _, is_dirty, raw_v in parsed_nums if is_dirty]
        neg_vals = [raw_v for num, _, raw_v in parsed_nums if num < 0]

        casing_variants = 0
        if inferred_type == "string" and n_non_empty > 0:
            lowered_distinct = {v.strip().lower() for v in non_empty}
            if len(lowered_distinct) < len(distinct_vals):
                casing_variants = len(distinct_vals) - len(lowered_distinct)

        if ws_count > 0 or casing_variants > 0:
            defects.append(
                DefectFinding(
                    category="whitespace_casing",
                    file=file_path.name,
                    column=col,
                    count=ws_count + casing_variants,
                    severity="warn",
                    summary=(
                        f"Column '{col}' has {ws_count} whitespace defect(s) and "
                        f"{casing_variants} casing variant(s)."
                    ),
                    samples=(ws_defects[:3] or samples[:3]),
                )
            )

        if len(date_formats) > 1 or (inferred_type == "date" and unparsed_date_samples):
            defects.append(
                DefectFinding(
                    category="mixed_date_formats",
                    file=file_path.name,
                    column=col,
                    count=sum(date_formats.values()) + len(unparsed_date_samples),
                    severity="warn",
                    summary=(
                        f"Column '{col}' contains mixed/unparsed date formats: "
                        f"{sorted(date_formats.keys())}"
                    ),
                    samples=samples[:3],
                )
            )

        if dirty_num_vals and num_ratio >= 0.5:
            defects.append(
                DefectFinding(
                    category="stringified_numeric",
                    file=file_path.name,
                    column=col,
                    count=len(dirty_num_vals),
                    severity="warn",
                    summary=(
                        f"Column '{col}' contains {len(dirty_num_vals)} stringified numeric/"
                        "currency value(s)."
                    ),
                    samples=dirty_num_vals[:3],
                )
            )

        if neg_vals and inferred_type in ("decimal", "integer") and _looks_like_amount_or_qty(col):
            defects.append(
                DefectFinding(
                    category="negative_credit_note",
                    file=file_path.name,
                    column=col,
                    count=len(neg_vals),
                    severity="info",
                    summary=(
                        f"Column '{col}' contains {len(neg_vals)} negative/credit-note value(s)."
                    ),
                    samples=neg_vals[:3],
                )
            )

        if _looks_like_key_or_fk(col) and null_count > 0:
            defects.append(
                DefectFinding(
                    category="null_or_orphan_fk",
                    file=file_path.name,
                    column=col,
                    count=null_count,
                    severity="warn",
                    summary=f"Key/FK column '{col}' has {null_count} null/blank value(s).",
                    samples=["<NULL>"],
                )
            )

        col_profiles.append(
            ColumnProfile(
                name=col,
                inferred_type=inferred_type,  # type: ignore[arg-type]
                non_null_count=n_non_empty,
                null_count=null_count,
                distinct_count=len(distinct_vals),
                sample_values=samples,
                detected_date_formats=sorted(date_formats.keys()),
                stringified_numeric_count=len(dirty_num_vals),
                whitespace_defect_count=ws_count,
                casing_variant_count=casing_variants,
                negative_value_count=len(neg_vals),
            )
        )

    # Heuristic grain mismatch check: repeated header amount across multi-line ID groups
    if len(rows) > 1 and id_cols_detected and numeric_cols_detected:
        header_id_col = id_cols_detected[0]
        grouped_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            key_val = row[header_id_col].strip()
            if key_val:
                grouped_rows[key_val].append(row)
        multi_groups = [grp for grp in grouped_rows.values() if len(grp) > 1]
        if multi_groups:
            for num_col in numeric_cols_detected:
                if "total" in num_col.lower() or "header" in num_col.lower():
                    repeated_groups = 0
                    for grp in multi_groups:
                        vals = {
                            parse_numeric_value(r[num_col])[0]
                            for r in grp
                            if parse_numeric_value(r[num_col])[0] is not None
                        }
                        if len(vals) == 1 and next(iter(vals)) != Decimal("0"):
                            repeated_groups += 1
                    if repeated_groups >= max(1, len(multi_groups) // 2):
                        defects.append(
                            DefectFinding(
                                category="grain_mismatch",
                                file=file_path.name,
                                column=num_col,
                                count=repeated_groups,
                                severity="warn",
                                summary=(
                                    f"Column '{num_col}' repeats identical values across "
                                    f"{repeated_groups} multi-line '{header_id_col}' group(s) "
                                    "(potential header-vs-line grain mismatch)."
                                ),
                                samples=[r[num_col] for r in multi_groups[0][:2]],
                            )
                        )

    return (
        FileProfile(
            file_name=file_path.name,
            path=str(file_path),
            delimiter=delimiter,
            row_count=len(rows),
            column_count=len(headers),
            exact_duplicate_row_count=dup_row_count,
            columns=col_profiles,
            defects=defects,
        ),
        rows,
        headers,
    )


def _propose_decomposition(fp: FileProfile) -> TableDecompositionProposal:
    stem = Path(fp.file_name).stem
    low_name = stem.lower()
    col_names = [c.name for c in fp.columns]
    date_cols = [c.name for c in fp.columns if c.inferred_type == "date"]
    measure_cols = [
        c.name
        for c in fp.columns
        if c.inferred_type in ("decimal", "integer") and _looks_like_amount_or_qty(c.name)
    ]
    key_cols = [c.name for c in fp.columns if _looks_like_key_or_fk(c.name)]
    attr_cols = [
        c.name
        for c in fp.columns
        if c.name not in set(date_cols + measure_cols + key_cols)
    ]

    if "date" in low_name or "calendar" in low_name:
        role = "date_dimension"
        table_name = stem if stem.startswith("Dim") else f"Dim{stem}"
    elif low_name.startswith("dim") or (not measure_cols and not date_cols and col_names):
        role = "dimension"
        table_name = stem if stem.startswith("Dim") else f"Dim{stem}"
    else:
        role = "fact"
        table_name = stem if stem.startswith("Fact") else f"Fact{stem}"

    return TableDecompositionProposal(
        source_file=fp.file_name,
        proposed_role=role,  # type: ignore[arg-type]
        proposed_table_name=table_name,
        candidate_keys=key_cols,
        candidate_measures=measure_cols,
        candidate_date_columns=date_cols,
        candidate_attributes=attr_cols,
    )


def _build_policy_questions(defects: list[DefectFinding]) -> list[PolicyQuestion]:
    files_by_cat: dict[DefectCategory, list[str]] = defaultdict(list)
    for defect in defects:
        if defect.file not in files_by_cat[defect.category]:
            files_by_cat[defect.category].append(defect.file)

    questions: list[PolicyQuestion] = []
    for cat, files in files_by_cat.items():
        policy_key, question, default_rec = POLICY_CATALOG[cat]
        questions.append(
            PolicyQuestion(
                policy_key=policy_key,
                category=cat,
                question=question,
                affected_files=files,
                recommended_default=default_rec,
            )
        )
    return questions


def profile_path(
    raw_path: Path,
    req_id: str = "",
    output_dir: Path | None = None,
    business_keys_by_file: dict[str, list[str]] | None = None,
) -> DataProfileReport:
    """Profile one CSV/TSV file or a directory of flat files and emit JSON/Markdown summaries."""
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw data path does not exist: {raw_path}")

    if raw_path.is_file():
        files = [raw_path]
    else:
        files = sorted(
            p
            for p in raw_path.iterdir()
            if p.is_file() and p.suffix.lower() in (".csv", ".tsv", ".txt")
        )
        if not files:
            raise FileNotFoundError(f"No CSV/TSV files found in directory: {raw_path}")

    key_map = business_keys_by_file or {}
    file_profiles: list[FileProfile] = []
    all_defects: list[DefectFinding] = []
    proposals: list[TableDecompositionProposal] = []
    loaded_tables: dict[str, tuple[list[str], list[dict[str, str]]]] = {}

    for fpath in files:
        bkeys = key_map.get(fpath.name)
        fp, rows, headers = _profile_single_file(fpath, business_keys=bkeys)
        file_profiles.append(fp)
        all_defects.extend(fp.defects)
        proposals.append(_propose_decomposition(fp))
        loaded_tables[fpath.name] = (headers, rows)

    # Cross-file orphan FK scan when profiling a multi-file directory
    if len(file_profiles) > 1:
        dim_key_sets: dict[str, set[str]] = {}
        for fp in file_profiles:
            headers, rows = loaded_tables[fp.file_name]
            if not headers:
                continue
            for col in headers:
                if _looks_like_key_or_fk(col):
                    vals = {
                        r[col].strip()
                        for r in rows
                        if r[col].strip().lower() not in NULL_SENTINELS
                    }
                    if len(vals) == len(rows) and len(vals) > 0:
                        dim_key_sets.setdefault(col.lower(), set()).update(vals)

        for fp in file_profiles:
            headers, rows = loaded_tables[fp.file_name]
            is_dim_file = fp.file_name.lower().startswith("dim")
            if is_dim_file:
                continue
            for col in headers:
                col_low = col.lower()
                if col_low in dim_key_sets and _looks_like_key_or_fk(col):
                    known = dim_key_sets[col_low]
                    orphans = [
                        r[col].strip()
                        for r in rows
                        if r[col].strip().lower() not in NULL_SENTINELS
                        and r[col].strip() not in known
                    ]
                    if orphans:
                        defect = DefectFinding(
                            category="null_or_orphan_fk",
                            file=fp.file_name,
                            column=col,
                            count=len(orphans),
                            severity="warn",
                            summary=(
                                f"Column '{col}' has {len(orphans)} value(s) missing from "
                                "candidate dimension keys."
                            ),
                            samples=list(dict.fromkeys(orphans))[:3],
                        )
                        fp.defects.append(defect)
                        all_defects.append(defect)

    questions = _build_policy_questions(all_defects)
    report = DataProfileReport(
        req_id=req_id,
        source_path=str(raw_path),
        files=file_profiles,
        defects=all_defects,
        proposed_decomposition=proposals,
        policy_questions=questions,
    )

    if output_dir is not None:
        report.write_json(output_dir / "data-profile.json")
        report.write_markdown(output_dir / "data-profile.md")

    return report


def _resolve_target_file(
    req: BusinessRequirement,
    target_table: str,
    explicit_target_file: str,
    repo_root: Path,
) -> Path | None:
    if explicit_target_file:
        return repo_root / explicit_target_file
    if req.data_profiling:
        for rule in req.data_profiling.tables:
            if rule.name == target_table:
                return repo_root / rule.staged_file
    for dim in req.star_schema.dimensions:
        if dim.name == target_table:
            return repo_root / dim.source
    for fact in req.star_schema.facts:
        if fact.name == target_table:
            return repo_root / fact.source
    return None


def _reconcile_table(
    rule: StagingReconciliationRule,
    req: BusinessRequirement,
    repo_root: Path,
    findings: list[GateFinding],
) -> TableReconciliationResult:
    raw_path = repo_root / rule.raw_file
    staged_path = repo_root / rule.staged_file
    quarantine_path = (repo_root / rule.quarantine_file) if rule.quarantine_file else None

    if not raw_path.is_file():
        findings.append(
            GateFinding(
                severity="error",
                code="G0_FILE_MISSING",
                message=f"[{rule.name}] Raw source file not found: {rule.raw_file}",
                location=str(raw_path),
            )
        )
        return TableReconciliationResult(
            table_name=rule.name,
            raw_file=rule.raw_file,
            staged_file=rule.staged_file,
            quarantine_file=rule.quarantine_file,
            passed=False,
        )

    if not staged_path.is_file():
        findings.append(
            GateFinding(
                severity="error",
                code="G0_FILE_MISSING",
                message=f"[{rule.name}] Staged file not found: {rule.staged_file}",
                location=str(staged_path),
            )
        )
        return TableReconciliationResult(
            table_name=rule.name,
            raw_file=rule.raw_file,
            staged_file=rule.staged_file,
            quarantine_file=rule.quarantine_file,
            passed=False,
        )

    raw_headers, raw_rows, _ = _read_delimited_rows(raw_path)
    staged_headers, staged_rows, _ = _read_delimited_rows(staged_path)
    quarantine_headers: list[str] = []
    quarantine_rows: list[dict[str, str]] = []
    if quarantine_path is not None and quarantine_path.is_file():
        quarantine_headers, quarantine_rows, _ = _read_delimited_rows(quarantine_path)

    raw_count = len(raw_rows)
    staged_count = len(staged_rows)
    quarantine_count = len(quarantine_rows)
    row_reconciled = raw_count == (staged_count + quarantine_count)
    if not row_reconciled:
        findings.append(
            GateFinding(
                severity="error",
                code="G0_ROW_RECON_MISMATCH",
                message=(
                    f"[{rule.name}] Row count mismatch: raw={raw_count} != "
                    f"staged ({staged_count}) + quarantine ({quarantine_count})"
                ),
                location=rule.staged_file,
            )
        )

    tol = Decimal(str(rule.tolerance))
    amount_recons: dict[str, dict[str, object]] = {}
    amounts_ok = True
    for raw_col in rule.reconcile_amount_columns:
        staged_col = rule.raw_to_staged_column_map.get(raw_col, raw_col)
        raw_total = Decimal("0")
        for row in raw_rows:
            val, _ = parse_numeric_value(row.get(raw_col, ""))
            if val is not None:
                raw_total += val

        staged_total = Decimal("0")
        for row in staged_rows:
            val, _ = parse_numeric_value(row.get(staged_col, ""))
            if val is not None:
                staged_total += val

        quarantine_total = Decimal("0")
        q_col = staged_col if staged_col in quarantine_headers else raw_col
        for row in quarantine_rows:
            val, _ = parse_numeric_value(row.get(q_col, ""))
            if val is not None:
                quarantine_total += val

        diff = abs(raw_total - (staged_total + quarantine_total))
        col_ok = diff <= tol
        if not col_ok:
            amounts_ok = False
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_AMOUNT_RECON_MISMATCH",
                    message=(
                        f"[{rule.name}] Financial mismatch on '{raw_col}' -> '{staged_col}': "
                        f"raw={raw_total}, staged={staged_total}, quarantine={quarantine_total}, "
                        f"diff={diff} > tol={tol}"
                    ),
                    location=rule.staged_file,
                )
            )
        amount_recons[staged_col] = {
            "raw_column": raw_col,
            "raw_total": str(raw_total),
            "staged_total": str(staged_total),
            "quarantine_total": str(quarantine_total),
            "diff": str(diff),
            "reconciled": col_ok,
        }

    # Staged dataset hygiene checks
    staged_defects: list[str] = []
    staged_tuples = [tuple(r.get(h, "") for h in staged_headers) for r in staged_rows]
    dup_staged = len(staged_tuples) - len(set(staged_tuples))
    if dup_staged > 0:
        staged_defects.append(f"{dup_staged} exact duplicate row(s) in staged output")

    if rule.business_keys:
        missing_keys = [k for k in rule.business_keys if k not in staged_headers]
        if missing_keys:
            staged_defects.append(f"Missing business key column(s) in staged: {missing_keys}")
        else:
            seen_bkeys: set[tuple[str, ...]] = set()
            dup_bkeys = 0
            for row in staged_rows:
                kt = tuple(row[k] for k in rule.business_keys)
                if kt in seen_bkeys:
                    dup_bkeys += 1
                else:
                    seen_bkeys.add(kt)
            if dup_bkeys > 0:
                staged_defects.append(
                    f"{dup_bkeys} duplicate business key(s) on {rule.business_keys}"
                )

    for col in staged_headers:
        ws_bad = sum(1 for r in staged_rows if r[col] != r[col].strip())
        if ws_bad > 0:
            staged_defects.append(f"Column '{col}' has {ws_bad} row(s) with un-trimmed whitespace")

    for d_col in rule.date_columns:
        if d_col not in staged_headers:
            staged_defects.append(f"Configured date column '{d_col}' missing from staged file")
            continue
        bad_dates = [
            r[d_col]
            for r in staged_rows
            if r[d_col].strip() and detect_date_format(r[d_col]) != "ISO_YYYY_MM_DD"
        ]
        if bad_dates:
            staged_defects.append(
                f"Date column '{d_col}' has {len(bad_dates)} non-ISO date value(s)"
            )

    clean_num_cols = list(dict.fromkeys(rule.numeric_columns + rule.reconcile_amount_columns))
    for n_col_raw in clean_num_cols:
        n_col = rule.raw_to_staged_column_map.get(n_col_raw, n_col_raw)
        if n_col not in staged_headers:
            staged_defects.append(f"Configured numeric column '{n_col}' missing from staged file")
            continue
        for row in staged_rows:
            raw_cell = row[n_col]
            if not raw_cell.strip():
                continue
            parsed_num, is_dirty = parse_numeric_value(raw_cell)
            if parsed_num is None or is_dirty:
                staged_defects.append(
                    f"Numeric column '{n_col}' contains dirty/unparseable value '{raw_cell}'"
                )
                break
            if (
                parsed_num < 0
                and n_col not in rule.allow_negative_columns
                and n_col_raw not in rule.allow_negative_columns
            ):
                staged_defects.append(
                    f"Numeric column '{n_col}' contains disallowed negative value '{raw_cell}'"
                )
                break

    for msg in staged_defects:
        findings.append(
            GateFinding(
                severity="error",
                code="G0_STAGED_DEFECT_REMAINING",
                message=f"[{rule.name}] {msg}",
                location=rule.staged_file,
            )
        )

    # Foreign key integrity checks
    orphan_counts: dict[str, int] = {}
    for fk in rule.foreign_keys:
        if fk.column not in staged_headers:
            orphan_counts[fk.column] = staged_count
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_ORPHAN_FK",
                    message=(
                        f"[{rule.name}] Foreign key column '{fk.column}' missing in staged file."
                    ),
                    location=rule.staged_file,
                )
            )
            continue
        target_path = _resolve_target_file(req, fk.target_table, fk.target_file, repo_root)
        if target_path is None or not target_path.is_file():
            orphan_counts[fk.column] = staged_count
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_ORPHAN_FK",
                    message=(
                        f"[{rule.name}] Target dimension file for '{fk.target_table}' not found."
                    ),
                    location=rule.staged_file,
                )
            )
            continue
        t_headers, t_rows, _ = _read_delimited_rows(target_path)
        if fk.target_column not in t_headers:
            orphan_counts[fk.column] = staged_count
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_ORPHAN_FK",
                    message=(
                        f"[{rule.name}] Target column '{fk.target_column}' missing in "
                        f"{target_path.name}."
                    ),
                    location=rule.staged_file,
                )
            )
            continue
        valid_keys = {r[fk.target_column].strip() for r in t_rows if r[fk.target_column].strip()}
        allow_sentinel = (
            str(fk.allow_unknown_key).strip() if fk.allow_unknown_key is not None else None
        )
        orphans = 0
        for row in staged_rows:
            fk_val = row[fk.column].strip()
            if not fk_val or (fk_val not in valid_keys and fk_val != allow_sentinel):
                orphans += 1
        orphan_counts[fk.column] = orphans
        if orphans > 0:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_ORPHAN_FK",
                    message=(
                        f"[{rule.name}] Column '{fk.column}' has {orphans} orphan/null FK(s) "
                        f"not present in '{fk.target_table}[{fk.target_column}]'."
                    ),
                    location=rule.staged_file,
                )
            )

    passed = (
        row_reconciled
        and amounts_ok
        and len(staged_defects) == 0
        and all(cnt == 0 for cnt in orphan_counts.values())
    )
    return TableReconciliationResult(
        table_name=rule.name,
        raw_file=rule.raw_file,
        staged_file=rule.staged_file,
        quarantine_file=rule.quarantine_file,
        raw_row_count=raw_count,
        staged_row_count=staged_count,
        quarantined_row_count=quarantine_count,
        row_reconciled=row_reconciled,
        amount_reconciliations=amount_recons,
        staged_defects=staged_defects,
        orphan_fk_counts=orphan_counts,
        passed=passed,
    )


def run_gate_g0(
    requirement_path: Path,
    repo_root: Path,
    output_dir: Path | None = None,
) -> GateResult:
    """Profile raw flat files and enforce raw-to-staged reconciliation + signed policy rules."""
    start = time.perf_counter()
    try:
        req = load_requirement(requirement_path)
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G0",
            name="Raw Data Profiling & Staging Reconciliation",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"Failed to parse requirement YAML: {exc}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G0_CONTRACT_ERROR",
                    message=str(exc),
                    location=str(requirement_path),
                )
            ],
        )

    if req.data_profiling is None or not req.data_profiling.enabled:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G0",
            name="Raw Data Profiling & Staging Reconciliation",
            status="SKIP",
            duration_ms=elapsed,
            summary="No messy-data staging contract configured; Gate G0 skipped.",
        )

    raw_dir_rel = req.data_profiling.raw_dir or req.project.raw_data_dir
    if not raw_dir_rel:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G0",
            name="Raw Data Profiling & Staging Reconciliation",
            status="FAIL",
            duration_ms=elapsed,
            summary="data_profiling.enabled=true but raw_dir is not configured.",
            findings=[
                GateFinding(
                    severity="error",
                    code="G0_RAW_DIR_MISSING",
                    message="Configure data_profiling.raw_dir or project.raw_data_dir.",
                    location=str(requirement_path),
                )
            ],
        )

    raw_dir = repo_root / raw_dir_rel
    run_dir = output_dir or (repo_root / "artifacts" / "runs" / req.id)
    run_dir.mkdir(parents=True, exist_ok=True)

    bkeys_by_file = {
        Path(rule.raw_file).name: rule.business_keys
        for rule in req.data_profiling.tables
        if rule.business_keys
    }

    try:
        report = profile_path(
            raw_dir,
            req_id=req.id,
            business_keys_by_file=bkeys_by_file,
        )
    except Exception as exc:
        elapsed = int((time.perf_counter() - start) * 1000)
        return GateResult(
            gate_id="G0",
            name="Raw Data Profiling & Staging Reconciliation",
            status="FAIL",
            duration_ms=elapsed,
            summary=f"Raw data profiling failed: {exc}",
            findings=[
                GateFinding(
                    severity="error",
                    code="G0_PROFILE_FAILED",
                    message=str(exc),
                    location=raw_dir_rel,
                )
            ],
        )

    findings: list[GateFinding] = []
    for question in report.policy_questions:
        decision = req.data_profiling.policy_decisions.get(question.policy_key, "").strip()
        if not decision:
            findings.append(
                GateFinding(
                    severity="error",
                    code="G0_UNRESOLVED_POLICY",
                    message=(
                        f"Missing human-signed policy decision for '{question.policy_key}' "
                        f"({question.category} in {', '.join(question.affected_files)})."
                    ),
                    location=str(requirement_path),
                )
            )

    reconciliations: list[TableReconciliationResult] = []
    for rule in req.data_profiling.tables:
        reconciliations.append(_reconcile_table(rule, req, repo_root, findings))
    report.reconciliations = reconciliations

    json_path = report.write_json(run_dir / "data-profile.json")
    md_path = report.write_markdown(run_dir / "data-profile.md")

    elapsed = int((time.perf_counter() - start) * 1000)
    passed = not any(f.severity == "error" for f in findings)
    return GateResult(
        gate_id="G0",
        name="Raw Data Profiling & Staging Reconciliation",
        status="PASS" if passed else "FAIL",
        duration_ms=elapsed,
        summary=(
            f"Profiled {len(report.files)} raw file(s), reconciled "
            f"{len(reconciliations)} staged table(s) with 0 unresolved defects"
            if passed
            else (
                f"{sum(1 for f in findings if f.severity == 'error')} "
                "G0 profiling/reconciliation error(s)"
            )
        ),
        metrics={
            "files_profiled": len(report.files),
            "raw_defects_detected": len(report.defects),
            "policy_questions_count": len(report.policy_questions),
            "tables_reconciled": len(reconciliations),
            "tables_passed": sum(1 for r in reconciliations if r.passed),
        },
        findings=findings,
        artifacts=[str(json_path), str(md_path)],
    )
