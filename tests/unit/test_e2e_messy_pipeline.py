"""End-to-end messy flat-file profiling, staging, and 8-gate (G0..G7) verification tests."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bi_agent import cli
from bi_agent.adapters import adomd_adapter, dax_studio_adapter, git_adapter
from bi_agent.adapters.adomd_adapter import DaxQueryResult, DiscoveryResult, PbidInstance
from bi_agent.adapters.dax_studio_adapter import BenchmarkResult
from bi_agent.config import ToolchainConfig
from bi_agent.gates import g0_data_profiling, runner
from bi_agent.schemas.evidence import GateResult


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def _write_csv(path: Path, fieldnames: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def test_e2e_messy_procurement_profiling_staging_and_full_g0_to_g7_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise G0 raw profiling + staging reconciliation + full G0..G7 on Procurement domain."""
    # 1. Create messy flat files for a Procurement / AP Spend domain
    raw_dir = tmp_path / "data" / "raw_proc"
    _write_csv(
        raw_dir / "raw_vendors.csv",
        ["VendorKey", "VendorName", "Department"],
        [
            ["V001", "Apex Industrial", "Operations"],
            ["V002", "Northwind Cloud", "IT"],
        ],
    )
    _write_csv(
        raw_dir / "raw_invoices.csv",
        ["InvoiceLineID", "InvoiceID", "PostingDate", "VendorKey", "HeaderTotal", "NetAmount"],
        [
            ["L001", "INV-100", "2026-01-10", "V001", "$1,400.00", "$1,200.50"],
            ["L001", "INV-100", "2026-01-10", "V001", "$1,400.00", "$1,200.50"],  # exact dup
            ["L002", "INV-100", "01/20/2026", "V001", "$1,400.00", "199.50"],  # MM/DD + repeat hdr
            ["L003", "INV-101", "15-Feb-2026", "V999", "  (250.00)", "(250.00)"],  # orphan+credit
            ["L004", "INV-102", "2026-03-01", "V002", "800.00", " 800.00 "],  # whitespace+ok
        ],
    )

    # Raw profile detects exact dup row, mixed dates, stringified currency,
    # orphan FK, negative credit note, whitespace, and grain_mismatch
    profile = g0_data_profiling.profile_path(
        raw_dir, req_id="REQ-PROC-001", output_dir=tmp_path / "artifacts" / "runs" / "REQ-PROC-001"
    )
    assert (tmp_path / "artifacts" / "runs" / "REQ-PROC-001" / "data-profile.json").is_file()
    assert (tmp_path / "artifacts" / "runs" / "REQ-PROC-001" / "data-profile.md").is_file()
    defect_cats = {d.category for d in profile.defects}
    assert {
        "duplicate_rows",
        "mixed_date_formats",
        "stringified_numeric",
        "whitespace_casing",
        "null_or_orphan_fk",
        "negative_credit_note",
        "grain_mismatch",
    }.issubset(defect_cats)
    policy_keys = {q.policy_key for q in profile.policy_questions}
    assert {
        "deduplication_policy",
        "date_parsing_policy",
        "currency_numeric_policy",
        "orphan_fk_policy",
    }.issubset(policy_keys)
    proposed_names = {p.proposed_table_name for p in profile.proposed_decomposition}
    assert any(name.startswith("Fact") for name in proposed_names)
    assert any(name.startswith("Dim") for name in proposed_names)

    # 2. Produce clean staged CSVs + quarantine (conserving rows & Decimal sums)
    # Raw NetAmount sum = 1200.50 + 1200.50 + 199.50 - 250.00 + 800.00 = 3150.50
    # Staged NetAmount sum (4 rows) = 1200.50 + 199.50 - 250.00 + 800.00 = 1950.00
    # Quarantine NetAmount sum (1 duplicate row) = 1200.50 -> diff = 0.00
    staged_dir = tmp_path / "data" / "staged_proc"
    _write_csv(
        staged_dir / "DimVendor.csv",
        ["VendorKey", "VendorName", "Department"],
        [
            ["-1", "Unknown Vendor", "Unassigned"],
            ["V001", "Apex Industrial", "Operations"],
            ["V002", "Northwind Cloud", "IT"],
        ],
    )
    _write_csv(
        staged_dir / "DimCalendar.csv",
        ["DateKey", "Date", "Year"],
        [
            ["20260110", "2026-01-10", "2026"],
            ["20260120", "2026-01-20", "2026"],
            ["20260215", "2026-02-15", "2026"],
            ["20260301", "2026-03-01", "2026"],
        ],
    )
    _write_csv(
        staged_dir / "DimCostCenter.csv",
        ["CostCenterKey", "Department"],
        [["CC-IT", "IT"], ["CC-OPS", "Operations"]],
    )
    _write_csv(
        staged_dir / "FactBudgets.csv",
        ["DateKey", "CostCenterKey", "BudgetAmount"],
        [["20260110", "CC-IT", "1000.00"], ["20260110", "CC-OPS", "1500.00"]],
    )
    _write_csv(
        staged_dir / "FactInvoices.csv",
        ["InvoiceLineID", "InvoiceID", "PostingDate", "VendorKey", "CostCenterKey", "NetSpend"],
        [
            ["L001", "INV-100", "2026-01-10", "V001", "CC-OPS", "1200.50"],
            ["L002", "INV-100", "2026-01-20", "V001", "CC-OPS", "199.50"],
            ["L003", "INV-101", "2026-02-15", "-1", "CC-OPS", "-250.00"],
            ["L004", "INV-102", "2026-03-01", "V002", "CC-IT", "800.00"],
        ],
    )
    _write_csv(
        staged_dir / "quarantine_invoices.csv",
        ["InvoiceLineID", "InvoiceID", "PostingDate", "VendorKey", "NetSpend", "Reason"],
        [["L001", "INV-100", "2026-01-10", "V001", "1200.50", "exact_duplicate"]],
    )

    # 3. Ground truth & PBIP/Report stubs + REQ-PROC-001.yaml
    gt_path = tmp_path / "data" / "gt_proc" / "expected_metrics.json"
    gt_path.parent.mkdir(parents=True)
    gt_payload = {
        "overall": {
            "invoice_rows": 4,
            "gross_spend": 2200.00,
            "credit_notes": 250.00,
            "net_spend": 1950.00,
            "budget_amount": 2500.00,
        },
        "by_department": {
            "Operations": {"invoice_rows": 3, "net_spend": 1150.00, "budget_amount": 1500.00},
            "IT": {"invoice_rows": 1, "net_spend": 800.00, "budget_amount": 1000.00},
        },
        "by_buyer": {
            "buyer.ops@procure.io": {"invoice_rows": 3, "budget_rows": 0, "net_spend": 1150.00},
            "unknown@procure.io": {"invoice_rows": 0, "budget_rows": 0, "net_spend": 0.0},
        },
    }
    gt_path.write_text(json.dumps(gt_payload, indent=2), encoding="utf-8")

    pbip_dir = tmp_path / "clients" / "procurement"
    (pbip_dir / "Procurement.SemanticModel" / "definition" / "roles").mkdir(parents=True)
    (pbip_dir / "Procurement.Report" / "definition" / "pages" / "p_spend" / "visuals" / "v1").mkdir(
        parents=True
    )
    (
        pbip_dir
        / "Procurement.Report"
        / "definition"
        / "pages"
        / "p_spend"
        / "page.json"
    ).write_text(json.dumps({"name": "p_spend", "displayName": "AP Spend Overview"}))
    (
        pbip_dir
        / "Procurement.Report"
        / "definition"
        / "pages"
        / "p_spend"
        / "visuals"
        / "v1"
        / "visual.json"
    ).write_text("{}", encoding="utf-8")
    (pbip_dir / "Procurement.Report" / "definition" / "pages" / "pages.json").write_text(
        json.dumps({"pageOrder": ["p_spend"], "activePageName": "p_spend"}), encoding="utf-8"
    )

    req_path = tmp_path / "requirements" / "REQ-PROC-001.yaml"
    req_path.parent.mkdir(parents=True)
    req_path.write_text(
        """id: REQ-PROC-001
title: Procurement AP Spend Analytics
owner: Finance Procurement
status: approved
target_pbip: clients/procurement
project:
  pbip_path: clients/procurement
  semantic_model_path: clients/procurement/Procurement.SemanticModel
  report_path: clients/procurement/Procurement.Report
  ground_truth_path: data/gt_proc/expected_metrics.json
  raw_data_dir: data/raw_proc
  desktop_report_name: Procurement
data_profiling:
  enabled: true
  raw_dir: data/raw_proc
  staged_dir: data/staged_proc
  quarantine_dir: data/staged_proc
  policy_decisions:
    deduplication_policy: "Drop exact duplicates to quarantine"
    key_uniqueness_policy: "Keep first InvoiceLineID occurrence and quarantine duplicates"
    date_parsing_policy: "Normalize mixed dates to ISO YYYY-MM-DD"
    currency_numeric_policy: "Strip currency and parens to Decimal"
    whitespace_casing_policy: "Trim whitespace"
    orphan_fk_policy: "Map unknown vendor V999 to -1 Unknown Vendor"
    credit_note_sign_policy: "Preserve signed negative amounts on NetSpend"
    grain_resolution_policy: "Drop repeated HeaderTotal and aggregate line NetSpend"
  tables:
    - name: FactInvoices
      raw_file: data/raw_proc/raw_invoices.csv
      staged_file: data/staged_proc/FactInvoices.csv
      quarantine_file: data/staged_proc/quarantine_invoices.csv
      business_keys: [InvoiceLineID]
      date_columns: [PostingDate]
      numeric_columns: [NetSpend]
      reconcile_amount_columns: [NetAmount]
      raw_to_staged_column_map:
        NetAmount: NetSpend
      allow_negative_columns: [NetSpend, NetAmount]
      foreign_keys:
        - column: VendorKey
          target_table: DimVendor
          target_column: VendorKey
          target_file: data/staged_proc/DimVendor.csv
          allow_unknown_key: "-1"
star_schema:
  facts:
    - name: FactInvoices
      grain: Invoice line
      source: data/staged_proc/FactInvoices.csv
    - name: FactBudgets
      grain: Department month
      source: data/staged_proc/FactBudgets.csv
  dimensions:
    - name: DimCalendar
      key: DateKey
      mark_as_date_table: Date
      source: data/staged_proc/DimCalendar.csv
    - name: DimVendor
      key: VendorKey
      source: data/staged_proc/DimVendor.csv
    - name: DimCostCenter
      key: CostCenterKey
      source: data/staged_proc/DimCostCenter.csv
  relationships:
    - "FactInvoices[VendorKey] -> DimVendor[VendorKey] (*:1, Single)"
    - "FactInvoices[CostCenterKey] -> DimCostCenter[CostCenterKey] (*:1, Single)"
    - "FactBudgets[CostCenterKey] -> DimCostCenter[CostCenterKey] (*:1, Single)"
measures:
  display_table: _Measures
  kpis:
    - name: Gross Spend
      folder: "01 Spend"
      format: "$#,##0.00"
    - name: Credit Notes
      folder: "01 Spend"
      format: "$#,##0.00"
    - name: Net Spend
      folder: "01 Spend"
      format: "$#,##0.00"
    - name: Budget Amount
      folder: "02 Budget"
      format: "$#,##0.00"
rls_roles:
  - name: Dept_IT
    type: static
    table: DimCostCenter
    filter: "'DimCostCenter'[Department] = \\"IT\\""
    expected_fail_closed: true
  - name: Dynamic_Buyer
    type: dynamic
    expected_fail_closed: true
    table_permissions:
      - table: DimVendor
        filter: "'DimVendor'[BuyerEmail] = USERPRINCIPALNAME()"
      - table: FactBudgets
        filter: "FALSE ()"
    test_principals:
      - buyer.ops@procure.io
      - unknown@procure.io
report_pages:
  - slug: AP_Spend_Overview
    intent: Spend and credit-memo KPIs by department.
verification:
  dax_checks:
    scalar_queries:
      - name: overall
        scope: overall
        dax: |
          EVALUATE ROW(
              "InvoiceRows", COUNTROWS('FactInvoices'),
              "GrossSpend", [Gross Spend],
              "CreditNotes", [Credit Notes],
              "NetSpend", [Net Spend],
              "BudgetAmount", [Budget Amount]
          )
        assertions:
          - alias: InvoiceRows
            expected_path: overall.invoice_rows
            tolerance: 0.0
          - alias: GrossSpend
            expected_path: overall.gross_spend
          - alias: CreditNotes
            expected_path: overall.credit_notes
          - alias: NetSpend
            expected_path: overall.net_spend
          - alias: BudgetAmount
            expected_path: overall.budget_amount
    grouped_queries:
      - name: by_department
        scope_prefix: dept
        group_column: Department
        group_aliases: ["DimCostCenter[Department]"]
        ground_truth_map_path: by_department
        dax: |
          EVALUATE SUMMARIZECOLUMNS(
              'DimCostCenter'[Department],
              "InvoiceRows", COUNTROWS('FactInvoices'),
              "NetSpend", [Net Spend],
              "BudgetAmount", [Budget Amount]
          )
        metrics:
          - alias: InvoiceRows
            expected_field: invoice_rows
            tolerance: 0.0
          - alias: NetSpend
            expected_field: net_spend
          - alias: BudgetAmount
            expected_field: budget_amount
    accounting_identities:
      - name: NetSpendIdentity
        lhs_alias: NetSpend
        rhs_terms:
          - alias: GrossSpend
            sign: 1
          - alias: CreditNotes
            sign: -1
    benchmark_dax: "EVALUATE ROW(\\"NetSpend\\", [Net Spend])"
  rls_checks:
    static_checks:
      - role: Dept_IT
        scope_column: "'DimCostCenter'[Department]"
        expected_scope_value: IT
        ground_truth_entry_path: by_department.IT
        fact_row_count_fields:
          FactInvoices: invoice_rows
        expected_fact_counts:
          FactBudgets: 1
        kpi_assertions:
          - alias: NetSpend
            dax_expression: "[Net Spend]"
            expected_field: net_spend
    dynamic_checks:
      - role: Dynamic_Buyer
        ground_truth_map_path: by_buyer
        fact_row_count_fields:
          FactInvoices: invoice_rows
          FactBudgets: budget_rows
        kpi_assertions:
          - alias: NetSpend
            dax_expression: "[Net Spend]"
            expected_field: net_spend
        principals:
          - buyer.ops@procure.io
          - unknown@procure.io
  report_checks:
    expected_pages: ["AP Spend Overview"]
    min_visuals_per_page: 1
    require_screenshot_count_match: false
""",
        encoding="utf-8",
    )

    # 4. Verify Gate G0 directly on staged Procurement artifacts
    out_dir = tmp_path / "artifacts" / "runs" / "REQ-PROC-001"
    g0_res = g0_data_profiling.run_gate_g0(req_path, tmp_path, output_dir=out_dir)
    assert g0_res.status == "PASS", g0_res.findings
    assert g0_res.metrics["tables_passed"] == 1
    profile_payload = json.loads((out_dir / "data-profile.json").read_text(encoding="utf-8"))
    rec = profile_payload["reconciliations"][0]
    assert rec["raw_row_count"] == rec["staged_row_count"] + rec["quarantined_row_count"]
    assert rec["amount_reconciliations"]["NetSpend"]["diff"] == "0.00"

    # 5. Exercise full G0..G7 runner across Procurement domain
    def _mock_dax(
        port: int,
        dax_query: str,
        config: ToolchainConfig,
        repo: Path,
        database: str = "",
        roles: str = "",
        effective_user_name: str = "",
        custom_data: str = "",
    ) -> DaxQueryResult:
        if "SUMMARIZECOLUMNS" in dax_query:
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "DimCostCenter[Department]": dept,
                        "[InvoiceRows]": vals["invoice_rows"],
                        "[NetSpend]": vals["net_spend"],
                        "[BudgetAmount]": vals["budget_amount"],
                    }
                    for dept, vals in gt_payload["by_department"].items()
                ],
                row_count=2,
            )
        if roles == "Dept_IT":
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactInvoices]": 1,
                        "[Rows_FactBudgets]": 1,
                        "[ScopeValues]": "IT",
                        "[NetSpend]": 800.00,
                    }
                ],
                row_count=1,
            )
        if roles == "Dynamic_Buyer":
            principal = effective_user_name or custom_data
            if not principal:
                return DaxQueryResult(
                    ok=True,
                    rows=[{"[Rows_FactInvoices]": 0, "[Rows_FactBudgets]": 0, "[NetSpend]": 0.0}],
                    row_count=1,
                )
            entry = gt_payload["by_buyer"][principal]
            return DaxQueryResult(
                ok=True,
                rows=[
                    {
                        "[Rows_FactInvoices]": entry["invoice_rows"],
                        "[Rows_FactBudgets]": entry["budget_rows"],
                        "[NetSpend]": entry["net_spend"],
                    }
                ],
                row_count=1,
            )
        return DaxQueryResult(
            ok=True,
            rows=[
                {
                    "[InvoiceRows]": 4,
                    "[GrossSpend]": 2200.00,
                    "[CreditNotes]": 250.00,
                    "[NetSpend]": 1950.00,
                    "[BudgetAmount]": 2500.00,
                }
            ],
            row_count=1,
        )

    monkeypatch.setattr(
        adomd_adapter,
        "discover_instance",
        lambda *_a, **_kw: DiscoveryResult(
            ok=True,
            selected=PbidInstance(port=54321, database="ProcurementDB", open_target="Procurement"),
        ),
    )
    monkeypatch.setattr(adomd_adapter, "execute_dax", _mock_dax)
    monkeypatch.setattr(
        dax_studio_adapter,
        "run_benchmark",
        lambda *_a, **_kw: BenchmarkResult(ok=True, cold_avg_ms=45.0, warm_avg_ms=12.0),
    )
    monkeypatch.setattr(
        runner,
        "run_gate_g2",
        lambda *_a, **_kw: GateResult(
            gate_id="G2", name="PBIP & TMDL Structure", status="PASS", summary="PBIP clean"
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_gate_g3",
        lambda *_a, **_kw: GateResult(
            gate_id="G3", name="BPA & VertiPaq Audit", status="PASS", summary="BPA errors=0"
        ),
    )
    monkeypatch.setattr(
        runner,
        "run_gate_g6",
        lambda *_a, **_kw: GateResult(
            gate_id="G6", name="Report QA & Desktop Render", status="PASS", summary="pbir pass"
        ),
    )
    monkeypatch.setattr(git_adapter, "current_branch", lambda _r: "feat/req-proc-001")
    monkeypatch.setattr(git_adapter, "staged_tree_sha", lambda _r: "a" * 40)
    monkeypatch.setattr(git_adapter, "staged_files", lambda _r: [])
    monkeypatch.setattr(git_adapter, "has_pbi_staged", lambda _r: False)

    bundle, json_path, md_path = runner.run_all_gates(
        req_id="",
        requirement_path=req_path,
        project_dir=None,
        ground_truth_path=None,
        config=ToolchainConfig(),
        repo_root=tmp_path,
        offline=False,
        port=54321,
        database="ProcurementDB",
        require_feature_branch=True,
        output_dir=out_dir,
    )

    assert bundle.req_id == "REQ-PROC-001"
    gate_debug = [(g.gate_id, g.status, g.summary, g.findings) for g in bundle.gates]
    assert bundle.overall_status == "PASS", gate_debug
    assert [g.gate_id for g in bundle.gates] == ["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"]
    gate_statuses = {g.gate_id: g.status for g in bundle.gates}
    expected_ids = ("G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7")
    assert gate_statuses == {gid: "PASS" for gid in expected_ids}
    assert json_path.is_file()
    md_text = md_path.read_text(encoding="utf-8")
    assert "| Role | Principal | FactInvoices Rows | FactBudgets Rows | Status |" in md_text
    assert "| Dept_IT | (static) | 1 | 1 | `PASS` |" in md_text
    for retail_tok in ("FactSales", "DimStore", "EnterpriseSales"):
        assert retail_tok not in md_text


def test_cli_profile_data_and_verify_data_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke `bi-agent profile-data` and `bi-agent verify-data` CLI commands cleanly."""
    raw_csv = tmp_path / "raw" / "transactions.csv"
    staged_csv = tmp_path / "staged" / "FactTx.csv"
    dim_csv = tmp_path / "staged" / "DimAcct.csv"
    _write_csv(raw_csv, ["TxID", "TxDate", "AcctID", "Amt"], [["1", "2026-01-01", "A1", "$10.00"]])
    _write_csv(
        staged_csv, ["TxID", "TxDate", "AcctID", "Amt"], [["1", "2026-01-01", "A1", "10.00"]]
    )
    _write_csv(dim_csv, ["AcctID", "Name"], [["A1", "Cash"]])

    req_path = tmp_path / "REQ-CLI-01.yaml"
    req_path.write_text(
        """id: REQ-CLI-01
title: CLI G0 Smoke
owner: QA
status: approved
target_pbip: pbi/CliDemo
data_profiling:
  enabled: true
  raw_dir: raw
  staged_dir: staged
  policy_decisions:
    currency_numeric_policy: "Normalize currency strings"
  tables:
    - name: FactTx
      raw_file: raw/transactions.csv
      staged_file: staged/FactTx.csv
      business_keys: [TxID]
      date_columns: [TxDate]
      reconcile_amount_columns: [Amt]
      foreign_keys:
        - column: AcctID
          target_table: DimAcct
          target_column: AcctID
          target_file: staged/DimAcct.csv
star_schema:
  facts:
    - name: FactTx
      grain: Transaction
      source: staged/FactTx.csv
  dimensions:
    - name: DimAcct
      key: AcctID
      source: staged/DimAcct.csv
  relationships:
    - "FactTx[AcctID] -> DimAcct[AcctID] (*:1, Single)"
measures:
  display_table: _Measures
  kpis:
    - name: Total Amt
      folder: "01 Core"
      format: "$#,##0.00"
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "find_repo_root", lambda *_a, **_kw: tmp_path)
    runner_cli = CliRunner()

    prof_dir = tmp_path / "artifacts" / "runs" / "REQ-CLI-01"
    prof_res = runner_cli.invoke(
        cli.app,
        ["profile-data", "raw", "--req-id", "REQ-CLI-01", "--output-dir", str(prof_dir)],
    )
    assert prof_res.exit_code == 0, prof_res.stdout
    assert (prof_dir / "data-profile.json").is_file()
    assert (prof_dir / "data-profile.md").is_file()

    verify_res = runner_cli.invoke(
        cli.app,
        ["verify-data", str(req_path), "--output-dir", str(prof_dir), "--json"],
    )
    assert verify_res.exit_code == 0, verify_res.stdout
    payload = json.loads(verify_res.stdout)
    assert payload["gate_id"] == "G0"
    assert payload["status"] == "PASS"
