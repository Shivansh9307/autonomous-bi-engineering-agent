"""Unit tests for Gate G0 raw flat-file profiling and raw-to-staged reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

from bi_agent.config import find_repo_root
from bi_agent.gates.g0_data_profiling import (
    detect_date_format,
    parse_numeric_value,
    profile_path,
    run_gate_g0,
)


def test_parse_numeric_value_and_detect_date_format() -> None:
    assert parse_numeric_value("$1,250.00") == (parse_numeric_value("1250.00")[0], True)
    assert parse_numeric_value(" (450.00) ") == (parse_numeric_value("-450.00")[0], True)
    assert parse_numeric_value("8.5%") == (parse_numeric_value("8.5")[0], True)
    assert parse_numeric_value("125.50") == (parse_numeric_value("125.50")[0], False)
    assert parse_numeric_value("not-a-number") == (None, False)

    assert detect_date_format("2025-03-14") == "ISO_YYYY_MM_DD"
    assert detect_date_format("25/03/2025") == "UK_DD_MM_YYYY_SLASH"
    assert detect_date_format("03/25/2025") == "US_MM_DD_YYYY_SLASH"
    assert detect_date_format("04/05/2025") == "AMBIGUOUS_SLASH_DATE"
    assert detect_date_format("14-Mar-2025") == "TEXT_MON_DATE"


def test_profile_path_detects_all_seven_defect_classes_and_writes_artifacts(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    out_dir = tmp_path / "out"

    (raw_dir / "DimVendor.csv").write_text(
        "\n".join(
            [
                "VendorKey,VendorName,Status",
                "1,Acme Supplies,Active",
                "2,Beacon Logistics,active",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (raw_dir / "FactInvoices.csv").write_text(
        "\n".join(
            [
                "InvoiceID,LineNum,InvoiceDate,VendorKey,HeaderTotalAmount,LineAmount",
                'INV-100,1,2025-01-15,1,"$1,200.00","$400.00"',
                'INV-100,2,15/01/2025,1,"$1,200.00","$800.00"',
                'INV-101,1,02/20/2025,99,"$500.00","(150.00)"',
                'INV-101,1,02/20/2025,99,"$500.00","(150.00)"',
                'INV-102,1,14-Mar-2025,,  "$300.00" ,"$300.00"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = profile_path(
        raw_dir,
        req_id="REQ-TEST",
        output_dir=out_dir,
        business_keys_by_file={"FactInvoices.csv": ["InvoiceID", "LineNum"]},
    )

    categories = {d.category for d in report.defects}
    assert {
        "duplicate_rows",
        "duplicate_keys",
        "mixed_date_formats",
        "stringified_numeric",
        "whitespace_casing",
        "null_or_orphan_fk",
        "negative_credit_note",
        "grain_mismatch",
    }.issubset(categories)

    assert len(report.proposed_decomposition) == 2
    assert len(report.policy_questions) >= 6

    json_file = out_dir / "data-profile.json"
    md_file = out_dir / "data-profile.md"
    assert json_file.is_file()
    assert md_file.is_file()
    payload = json.loads(json_file.read_text(encoding="utf-8"))
    assert payload["req_id"] == "REQ-TEST"
    md_text = md_file.read_text(encoding="utf-8")
    assert "## 4. Human-in-the-Loop Policy Questions" in md_text


def test_run_gate_g0_skips_when_not_configured() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    for req_name in (
        "REQ-001-regional-sales-margin.yaml",
        "REQ-002-customer-discount-analytics.yaml",
    ):
        res = run_gate_g0(repo_root / "requirements" / req_name, repo_root)
        assert res.gate_id == "G0"
        assert res.status == "SKIP"


def _write_g0_fixture(base: Path) -> Path:
    (base / "raw").mkdir(parents=True)
    (base / "staged").mkdir(parents=True)
    (base / "quarantine").mkdir(parents=True)

    (base / "raw" / "DimVendor.csv").write_text(
        "VendorKey,VendorName\n-1,Unmapped Vendor\n1,Acme Corp\n",
        encoding="utf-8",
    )
    (base / "staged" / "DimVendor.csv").write_text(
        "VendorKey,VendorName\n-1,Unmapped Vendor\n1,Acme Corp\n",
        encoding="utf-8",
    )
    (base / "raw" / "FactInvoices.csv").write_text(
        "\n".join(
            [
                "InvoiceID,LineNum,InvoiceDate,VendorKey,LineAmount",
                'INV-1,1,2025-01-15,1,"$1,000.00"',
                'INV-1,1,2025-01-15,1,"$1,000.00"',
                'INV-2,1,20/02/2025,999,"(250.00)"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (base / "staged" / "FactInvoices.csv").write_text(
        "\n".join(
            [
                "InvoiceID,LineNum,InvoiceDate,VendorKey,LineAmount",
                "INV-1,1,2025-01-15,1,1000.00",
                "INV-2,1,2025-02-20,-1,-250.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (base / "quarantine" / "FactInvoices_quarantine.csv").write_text(
        "\n".join(
            [
                "InvoiceID,LineNum,InvoiceDate,VendorKey,LineAmount",
                "INV-1,1,2025-01-15,1,1000.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    req_path = base / "REQ-G0.yaml"
    req_path.write_text(
        """id: REQ-G0
title: G0 Reconciliation Test
owner: Finance
status: approved
target_pbip: pbi/Demo
data_profiling:
  enabled: true
  raw_dir: raw
  staged_dir: staged
  quarantine_dir: quarantine
  policy_decisions:
    deduplication_policy: Drop exact duplicates to quarantine.
    key_uniqueness_policy: Keep first unique InvoiceID+LineNum.
    date_parsing_policy: Parse UK/ISO dates to ISO YYYY-MM-DD.
    currency_numeric_policy: Strip currency symbols and parse parens as negative Decimal.
    orphan_fk_policy: Route unmapped vendors to VendorKey=-1.
    credit_note_sign_policy: Preserve signed negative amounts.
  tables:
    - name: FactInvoices
      raw_file: raw/FactInvoices.csv
      staged_file: staged/FactInvoices.csv
      quarantine_file: quarantine/FactInvoices_quarantine.csv
      business_keys: [InvoiceID, LineNum]
      date_columns: [InvoiceDate]
      numeric_columns: [LineAmount]
      reconcile_amount_columns: [LineAmount]
      allow_negative_columns: [LineAmount]
      foreign_keys:
        - column: VendorKey
          target_table: DimVendor
          target_column: VendorKey
          target_file: staged/DimVendor.csv
          allow_unknown_key: -1
star_schema:
  facts:
    - name: FactInvoices
      grain: Invoice line
      source: staged/FactInvoices.csv
  dimensions:
    - name: DimVendor
      key: VendorKey
      source: staged/DimVendor.csv
      mark_as_date_table: Date
  relationships:
    - "FactInvoices[VendorKey] -> DimVendor[VendorKey] (*:1, Single)"
measures:
  display_table: _Measures
  kpis:
    - name: Total Spend
      folder: "01 Spend"
      format: "$#,##0.00"
""",
        encoding="utf-8",
    )
    return req_path


def test_run_gate_g0_passes_and_catches_all_failure_modes(tmp_path: Path) -> None:
    req_path = _write_g0_fixture(tmp_path)
    out_dir = tmp_path / "artifacts"

    pass_res = run_gate_g0(req_path, tmp_path, output_dir=out_dir)
    assert pass_res.status == "PASS"
    assert pass_res.metrics["tables_passed"] == 1
    assert (out_dir / "data-profile.json").is_file()
    assert (out_dir / "data-profile.md").is_file()

    # 1. Missing policy decision -> G0_UNRESOLVED_POLICY
    req_no_policy = tmp_path / "REQ-NO-POLICY.yaml"
    req_no_policy.write_text(
        req_path.read_text(encoding="utf-8").replace(
            "    deduplication_policy: Drop exact duplicates to quarantine.\n", ""
        ),
        encoding="utf-8",
    )
    res_policy = run_gate_g0(req_no_policy, tmp_path, output_dir=out_dir)
    assert res_policy.status == "FAIL"
    assert any(f.code == "G0_UNRESOLVED_POLICY" for f in res_policy.findings)

    # 2. Row + amount drop in staged -> G0_ROW_RECON_MISMATCH & G0_AMOUNT_RECON_MISMATCH
    staged_file = tmp_path / "staged" / "FactInvoices.csv"
    orig_staged = staged_file.read_text(encoding="utf-8")
    staged_file.write_text(
        "InvoiceID,LineNum,InvoiceDate,VendorKey,LineAmount\nINV-1,1,2025-01-15,1,1000.00\n",
        encoding="utf-8",
    )
    res_drop = run_gate_g0(req_path, tmp_path, output_dir=out_dir)
    assert res_drop.status == "FAIL"
    drop_codes = {f.code for f in res_drop.findings}
    assert {"G0_ROW_RECON_MISMATCH", "G0_AMOUNT_RECON_MISMATCH"}.issubset(drop_codes)

    # 3. Dirty currency / non-ISO date / orphan FK in staged ->
    #    G0_STAGED_DEFECT_REMAINING & G0_ORPHAN_FK
    staged_file.write_text(
        "\n".join(
            [
                "InvoiceID,LineNum,InvoiceDate,VendorKey,LineAmount",
                'INV-1,1,01/15/2025,1,"$1,000.00"',
                "INV-2,1,2025-02-20,777,-250.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    res_dirty = run_gate_g0(req_path, tmp_path, output_dir=out_dir)
    assert res_dirty.status == "FAIL"
    dirty_codes = {f.code for f in res_dirty.findings}
    assert {"G0_STAGED_DEFECT_REMAINING", "G0_ORPHAN_FK"}.issubset(dirty_codes)
    staged_file.write_text(orig_staged, encoding="utf-8")
