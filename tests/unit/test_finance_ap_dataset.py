"""Unit tests for the REQ-003 messy Corporate Finance / AP Spend benchmark dataset."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from bi_agent.config import find_repo_root
from bi_agent.gates import run_gate_g0, run_gate_g1
from bi_agent.gates.g0_data_profiling import (
    POLICY_CATALOG,
    detect_date_format,
    parse_numeric_value,
    profile_path,
)

REPO_ROOT = find_repo_root(Path(__file__).resolve())
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_finance_ap_data import SEED, generate_finance_ap_dataset  # noqa: E402
from stage_finance_ap_data import stage_finance_ap_dataset  # noqa: E402


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_finance_ap_generator_is_deterministic(tmp_path: Path) -> None:
    """Fixed-seed generator outputs must be byte-identical across runs and match repo files."""
    run1 = tmp_path / "run1"
    run2 = tmp_path / "run2"
    generate_finance_ap_dataset(run1, seed=SEED)
    generate_finance_ap_dataset(run2, seed=SEED)

    repo_data = REPO_ROOT / "data" / "finance_ap"
    for rel in (
        "clean/DimDate.csv",
        "clean/DimVendor.csv",
        "clean/DimCostCenter.csv",
        "clean/DimGLAccount.csv",
        "clean/DimApprover.csv",
        "clean/FactAPInvoices.csv",
        "clean/FactAPBudget.csv",
        "raw/DimDate.csv",
        "raw/DimVendor.csv",
        "raw/DimCostCenter.csv",
        "raw/DimGLAccount.csv",
        "raw/DimApprover.csv",
        "raw/FactAPInvoices.csv",
        "raw/FactAPBudget.csv",
        "ground_truth/defect_manifest.json",
    ):
        b1 = (run1 / rel).read_bytes()
        b2 = (run2 / rel).read_bytes()
        assert b1 == b2, f"Non-deterministic output in {rel}"
        assert b1 == (repo_data / rel).read_bytes(), f"Checked-in {rel} stale vs seed={SEED}"


def test_finance_ap_defect_manifest_and_guardrails() -> None:
    """Validate defect manifest coverage and all 5 Block C1 user guardrails."""
    base = REPO_ROOT / "data" / "finance_ap"
    manifest = json.loads(
        (base / "ground_truth" / "defect_manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["seed"] == SEED
    defects = manifest["defects"]
    assert set(defects.keys()) == {
        "duplicate_invoice_lines",
        "mixed_date_formats",
        "stringified_currency_values",
        "negative_credit_memos",
        "whitespace_casing_drift",
        "null_or_orphan_foreign_keys",
        "mixed_entity_grain",
        "inconsistent_financial_representation",
    }
    assert defects["duplicate_invoice_lines"]["exact_duplicate_rows"] > 0
    assert defects["duplicate_invoice_lines"]["conflicting_duplicate_keys"] > 0
    assert defects["mixed_date_formats"]["us_mm_dd_day_gt_12_count"] > 0
    assert defects["stringified_currency_values"]["count"] > 0
    assert defects["negative_credit_memos"]["count"] > 0
    assert defects["whitespace_casing_drift"]["count"] > 0
    assert defects["null_or_orphan_foreign_keys"]["null_sentinel_count"] > 0
    assert defects["null_or_orphan_foreign_keys"]["orphan_id_count"] > 0
    assert defects["mixed_entity_grain"]["count"] > 0
    assert defects["inconsistent_financial_representation"]["percentage_rate_strings_count"] > 0

    clean_rows = _read_csv_rows(base / "clean" / "FactAPInvoices.csv")
    raw_rows = _read_csv_rows(base / "raw" / "FactAPInvoices.csv")
    budget_raw_rows = _read_csv_rows(base / "raw" / "FactAPBudget.csv")

    # Guardrail #1: Clean originals always precede duplicate / colliding-key rows
    seen_keys: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(raw_rows):
        key = (row["InvoiceID"], row["LineNumber"])
        if key not in seen_keys:
            seen_keys[key] = idx
            assert idx < len(clean_rows), f"Non-duplicate key {key} appeared after original block"
        else:
            assert idx >= len(clean_rows), f"Duplicate key {key} appeared inside original block"
    assert len(seen_keys) == len(clean_rows)

    # Guardrail #2: US MM/DD/YYYY with day > 12 present, and clean DueDate >= PostingDate
    raw_post_fmts = {detect_date_format(r["PostingDate"]) for r in raw_rows}
    assert "US_MM_DD_YYYY_SLASH" in raw_post_fmts
    assert "ISO_YYYY_MM_DD" in raw_post_fmts
    assert "SLASH_YYYY_MM_DD" in raw_post_fmts
    assert "TEXT_MON_DATE" in raw_post_fmts
    for r in clean_rows:
        assert date.fromisoformat(r["DueDate"]) >= date.fromisoformat(r["PostingDate"])

    # Guardrail #3: '%' strings only in EarlyPayDiscountRate, never in reconciled amount columns
    for r in raw_rows:
        for col in ("LineAmount", "TaxAmount", "NetAmount", "InvoiceHeaderTotal"):
            assert "%" not in r[col], f"Unexpected '%' in {col}: {r[col]}"
    for r in budget_raw_rows:
        assert "%" not in r["BudgetAmount"]
    assert any("%" in r["EarlyPayDiscountRate"] for r in raw_rows)
    for r in clean_rows:
        rate_val, is_dirty = parse_numeric_value(r["EarlyPayDiscountRate"])
        assert rate_val is not None and not is_dirty

    # Guardrail #4: Credit memos consistently negative across LineAmount, TaxAmount, NetAmount
    for r in clean_rows:
        line_dec = Decimal(r["LineAmount"])
        tax_dec = Decimal(r["TaxAmount"])
        net_dec = Decimal(r["NetAmount"])
        assert line_dec + tax_dec == net_dec
        if r["DocumentType"] == "Credit Memo":
            assert line_dec < 0
            assert tax_dec < 0
            assert net_dec < 0
        else:
            assert line_dec > 0
            assert tax_dec > 0
            assert net_dec > 0

    # Guardrail #5: Explicit -1 Unknown members exist & Unknown bucket reconciles cleanly
    for dim_file, pk_col in (
        ("DimVendor.csv", "VendorKey"),
        ("DimCostCenter.csv", "CostCenterKey"),
        ("DimGLAccount.csv", "GLAccountKey"),
        ("DimApprover.csv", "ApproverKey"),
    ):
        dim_rows = _read_csv_rows(base / "clean" / dim_file)
        neg_one = [r for r in dim_rows if r[pk_col] == "-1"]
        assert len(neg_one) == 1, f"{dim_file} missing explicit -1 Unknown row"
        if dim_file == "DimApprover.csv":
            assert neg_one[0]["UserPrincipalName"] == ""

    cc_unknown_net = sum(
        (Decimal(r["NetAmount"]) for r in clean_rows if r["CostCenterKey"] == "-1"),
        Decimal("0.00"),
    )
    assert (
        f"{cc_unknown_net:.2f}"
        == manifest["financial_checksums"]["unknown_minus_one_buckets"]["CostCenterKey_-1"][
            "net_amount"
        ]
    )


def test_g0_profile_data_detects_all_eight_defect_classes_on_finance_ap(tmp_path: Path) -> None:
    """bi-agent profile_path must surface all 8 G0 defect categories and conserve Decimal sums."""
    base = REPO_ROOT / "data" / "finance_ap"
    raw_dir = base / "raw"
    manifest = json.loads(
        (base / "ground_truth" / "defect_manifest.json").read_text(encoding="utf-8")
    )

    # Standalone preflight profiling (7 keyless categories)
    standalone_report = profile_path(raw_dir, req_id="REQ-003", output_dir=tmp_path / "standalone")
    standalone_cats = {d.category for d in standalone_report.defects}
    assert standalone_cats == {
        "duplicate_rows",
        "mixed_date_formats",
        "stringified_numeric",
        "whitespace_casing",
        "null_or_orphan_fk",
        "negative_credit_note",
        "grain_mismatch",
    }

    # Keyed profiling (all 8 categories + all 8 PolicyQuestions)
    keyed_report = profile_path(
        raw_dir,
        req_id="REQ-003",
        output_dir=tmp_path / "keyed",
        business_keys_by_file={"FactAPInvoices.csv": ["InvoiceID", "LineNumber"]},
    )
    keyed_cats = {d.category for d in keyed_report.defects}
    assert keyed_cats == set(POLICY_CATALOG.keys())
    assert {q.policy_key for q in keyed_report.policy_questions} == {
        spec[0] for spec in POLICY_CATALOG.values()
    }

    # Exact raw Decimal conservation against defect_manifest.json
    raw_inv_rows = _read_csv_rows(raw_dir / "FactAPInvoices.csv")
    raw_totals = {
        "LineAmount": Decimal("0.00"),
        "TaxAmount": Decimal("0.00"),
        "NetAmount": Decimal("0.00"),
    }
    for row in raw_inv_rows:
        for col in raw_totals:
            val, _ = parse_numeric_value(row[col])
            assert val is not None, f"Unparseable raw cell in {col}: {row[col]!r}"
            raw_totals[col] += val

    expected_raw = manifest["financial_checksums"]["FactAPInvoices_raw_expected"]
    assert f"{raw_totals['LineAmount']:.2f}" == expected_raw["line_amount"]
    assert f"{raw_totals['TaxAmount']:.2f}" == expected_raw["tax_amount"]
    assert f"{raw_totals['NetAmount']:.2f}" == expected_raw["net_amount"]

    budget_total = Decimal("0.00")
    for row in _read_csv_rows(raw_dir / "FactAPBudget.csv"):
        val, _ = parse_numeric_value(row["BudgetAmount"])
        assert val is not None
        budget_total += val
    assert f"{budget_total:.2f}" == manifest["financial_checksums"]["FactAPBudget_total"]


def test_stage_finance_ap_reconciles_to_clean_and_quarantine(tmp_path: Path) -> None:
    """Staging must recover exact clean rows, preserve Unknown (-1), and conserve Decimal sums."""
    base = REPO_ROOT / "data" / "finance_ap"
    sandbox = tmp_path / "finance_ap"
    shutil.copytree(base / "raw", sandbox / "raw")
    stage_finance_ap_dataset(sandbox)

    clean_rows = _read_csv_rows(base / "clean" / "FactAPInvoices.csv")
    staged_rows = _read_csv_rows(base / "staged" / "FactAPInvoices.csv")
    raw_rows = _read_csv_rows(base / "raw" / "FactAPInvoices.csv")
    quarantine_rows = _read_csv_rows(base / "quarantine" / "FactAPInvoices_quarantine.csv")

    assert len(raw_rows) == 3580
    assert len(staged_rows) == 3500
    assert len(quarantine_rows) == 80
    assert (sandbox / "staged" / "FactAPInvoices.csv").read_bytes() == (
        base / "staged" / "FactAPInvoices.csv"
    ).read_bytes()
    assert (sandbox / "quarantine" / "FactAPInvoices_quarantine.csv").read_bytes() == (
        base / "quarantine" / "FactAPInvoices_quarantine.csv"
    ).read_bytes()

    q_net = sum((parse_numeric_value(r["NetAmount"])[0] or Decimal("0")) for r in quarantine_rows)
    assert q_net == Decimal("725654.28")
    for col in ("LineAmount", "TaxAmount", "NetAmount"):
        raw_sum = sum((parse_numeric_value(r[col])[0] or Decimal("0")) for r in raw_rows)
        staged_sum = sum((Decimal(r[col]) for r in staged_rows), Decimal("0.00"))
        quar_sum = sum((parse_numeric_value(r[col])[0] or Decimal("0")) for r in quarantine_rows)
        assert raw_sum - (staged_sum + quar_sum) == Decimal("0.00")

    for c_row, s_row in zip(clean_rows, staged_rows, strict=True):
        assert s_row["InvoiceID"] == c_row["InvoiceID"]
        assert s_row["LineNumber"] == c_row["LineNumber"]
        assert s_row["PostingDateKey"] == c_row["DateKey"]
        for field in (
            "PostingDate",
            "DueDate",
            "VendorKey",
            "CostCenterKey",
            "GLAccountKey",
            "ApproverKey",
            "DocumentType",
            "PaymentStatus",
            "MatchStatus",
            "LineAmount",
            "TaxAmount",
            "NetAmount",
            "EarlyPayDiscountRate",
        ):
            assert s_row[field] == c_row[field]

    for dim_file, key_col, expected_name in (
        ("DimVendor.csv", "VendorKey", "Unknown Vendor"),
        ("DimCostCenter.csv", "CostCenterKey", "Unknown Cost Center"),
        ("DimGLAccount.csv", "GLAccountKey", "Unknown GL Account"),
        ("DimApprover.csv", "ApproverKey", "Unassigned Approver"),
    ):
        s_dim = _read_csv_rows(base / "staged" / dim_file)
        neg_one = [r for r in s_dim if r[key_col] == "-1"]
        assert len(neg_one) == 1
        assert expected_name in neg_one[0].values()

    for rel_path in (
        "scripts/generate_finance_ap_data.py",
        "scripts/stage_finance_ap_data.py",
        "requirements/REQ-003-corporate-ap-spend.yaml",
    ):
        assert "InvoiceNumber" not in (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_req003_expected_metrics_and_gates_g0_g1_pass(tmp_path: Path) -> None:
    """REQ-003 expected metrics, Unknown BU accounting, and Gates G0 + G1 must pass."""
    expected = json.loads(
        (REPO_ROOT / "data" / "finance_ap" / "ground_truth" / "expected_metrics.json").read_text(
            encoding="utf-8"
        )
    )
    ov = expected["overall"]
    assert ov["net_ap_spend"] == 33507890.84
    assert round(ov["gross_invoice_spend"] + ov["credit_memo_amount"], 2) == ov["net_ap_spend"]
    assert round(ov["pre_tax_spend"] + ov["tax_spend"], 2) == ov["net_ap_spend"]

    by_bu = expected["by_business_unit"]
    assert "Unknown" in by_bu
    static_bu_sum = round(
        by_bu["North America"]["net_ap_spend"]
        + by_bu["Europe"]["net_ap_spend"]
        + by_bu["APAC"]["net_ap_spend"],
        2,
    )
    assert static_bu_sum < ov["net_ap_spend"]

    req_path = REPO_ROOT / "requirements" / "REQ-003-corporate-ap-spend.yaml"
    g0 = run_gate_g0(req_path, REPO_ROOT, output_dir=tmp_path)
    assert g0.status == "PASS", g0.findings
    assert g0.metrics["tables_passed"] == 7

    g1 = run_gate_g1(req_path, REPO_ROOT)
    assert g1.status == "PASS", g1.findings


def test_corporate_finance_ap_tmdl_has_non_empty_datafolder_and_m_partitions_only() -> None:
    """Exported TMDL must define non-empty DataFolder expression and clean M partitions only."""
    def_dir = (
        REPO_ROOT
        / "pbi"
        / "CorporateFinanceAP"
        / "CorporateFinanceAP.SemanticModel"
        / "definition"
    )
    expr_text = (def_dir / "expressions.tmdl").read_text(encoding="utf-8")
    assert "expression DataFolder =" in expr_text
    assert "finance_ap" in expr_text
    assert not (def_dir / "dataSources.tmdl").exists()

    for tmdl_path in sorted((def_dir / "tables").glob("*.tmdl")):
        body = tmdl_path.read_text(encoding="utf-8")
        assert "= m" in body, f"{tmdl_path.name} missing M partition"
        assert "= query" not in body, f"{tmdl_path.name} has legacy query partition"
        assert "New Provider Data Source" not in body


