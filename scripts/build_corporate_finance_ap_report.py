#!/usr/bin/env python3
"""Reproducibly construct CorporateFinanceAP.Report exclusively via pbir CLI."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = ROOT / "pbi" / "CorporateFinanceAP"
REPORT_DIR = PROJECT_DIR / "CorporateFinanceAP.Report"
PBIP_FILE = PROJECT_DIR / "CorporateFinanceAP.pbip"
GITIGNORE_CONTENT = "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n"


def _run_pbir(
    args: list[str],
    *,
    cwd: Path | None = None,
    stdin_text: str | None = None,
    check: bool = True,
) -> str:
    proc = subprocess.run(
        ["pbir", *args],
        cwd=str(cwd or ROOT),
        input=stdin_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 and check:
        if proc.stdout:
            print(proc.stdout.strip())
        if proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        raise SystemExit(proc.returncode)
    return proc.stdout


def _discover_title_visual(page_path: str) -> str:
    out = _run_pbir(["ls", page_path])
    for line in out.splitlines():
        match = re.search(r"([A-Za-z0-9_]+\.Visual)\b", line)
        if match:
            return match.group(1)
    raise RuntimeError(f"Could not find title visual on {page_path}:\n{out}")


def build_report() -> None:
    # Disconnect active pbir context safely (answer 'n' to never delete local files)
    _run_pbir(["connect", "--clear"], stdin_text="n\n", check=False)

    if REPORT_DIR.exists():
        shutil.rmtree(REPORT_DIR)
    if PBIP_FILE.exists():
        PBIP_FILE.unlink()

    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    _run_pbir(["new", "report", "CorporateFinanceAP.Report", "--thick"], cwd=PROJECT_DIR)
    _run_pbir(
        ["rebind", "CorporateFinanceAP.Report", "--local", "../CorporateFinanceAP.SemanticModel"],
        cwd=PROJECT_DIR,
    )

    gitignore = PROJECT_DIR / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_CONTENT, encoding="utf-8")

    _run_pbir(["connect", "pbi/CorporateFinanceAP/CorporateFinanceAP.Report"])
    _run_pbir(
        [
            "set",
            "CorporateFinanceAP.Report/Page 1.Page.displayName",
            "--value",
            "AP Executive Overview",
        ]
    )
    _run_pbir(
        [
            "add",
            "page",
            "CorporateFinanceAP.Report/Vendor_And_Credits.Page",
            "-n",
            "Vendor & Credit Analysis",
        ]
    )
    _run_pbir(
        [
            "add",
            "page",
            "CorporateFinanceAP.Report/Cost_Center_Controls.Page",
            "-n",
            "Cost Center & Controls",
        ]
    )
    _run_pbir(["filters", "pane-collapse", "CorporateFinanceAP.Report"])
    _run_pbir(["filters", "pane-hide", "CorporateFinanceAP.Report"])
    _run_pbir(["pages", "active-page", "CorporateFinanceAP.Report", "AP Executive Overview"])

    p1 = "CorporateFinanceAP.Report/AP Executive Overview.Page"
    p2 = "CorporateFinanceAP.Report/Vendor & Credit Analysis.Page"
    p3 = "CorporateFinanceAP.Report/Cost Center & Controls.Page"

    p1_title = _discover_title_visual(p1)
    title_payload = json.dumps(
        [
            {
                "textRuns": [
                    {
                        "value": "AP Executive Overview",
                        "textStyle": {
                            "fontFamily": (
                                '"Segoe UI", wf_segoe-ui_normal, helvetica, arial, sans-serif'
                            ),
                            "fontSize": "24pt",
                            "fontWeight": "600",
                        },
                    }
                ]
            }
        ]
    )
    _run_pbir(["set", f"{p1}/{p1_title}.general.paragraphs", "--json", title_payload])
    for title_ref in (f"{p1}/{p1_title}", f"{p2}/Title.Visual", f"{p3}/Title.Visual"):
        _run_pbir(
            [
                "visuals",
                "position",
                title_ref,
                "--x",
                "20",
                "--y",
                "16",
                "--width",
                "1240",
                "--height",
                "74",
            ]
        )

    visuals_spec: list[tuple[str, str, str, str, int, int, int, int, list[str]]] = [
        # Page 1: AP Executive Overview
        (
            "card",
            p1,
            "KpiNetSpend",
            "Net AP Spend",
            20,
            105,
            298,
            140,
            ["Values:_Measures.Net AP Spend"],
        ),
        (
            "card",
            p1,
            "KpiBudgetSpend",
            "Budget Spend",
            334,
            105,
            298,
            140,
            ["Values:_Measures.Budget Spend"],
        ),
        (
            "card",
            p1,
            "KpiBudgetVarPct",
            "Budget Variance %",
            648,
            105,
            298,
            140,
            ["Values:_Measures.Budget Variance %"],
        ),
        (
            "card",
            p1,
            "KpiOnTimePct",
            "On-Time Payment %",
            962,
            105,
            298,
            140,
            ["Values:_Measures.On-Time Payment %"],
        ),
        (
            "lineChart",
            p1,
            "TrendSpendVsBudget",
            "Monthly Net AP Spend vs Budget",
            20,
            252,
            720,
            205,
            [
                "Category:DimDate.MonthStart",
                "Y:_Measures.Net AP Spend",
                "Y:_Measures.Budget Spend",
            ],
        ),
        (
            "clusteredBarChart",
            p1,
            "SpendByBusinessUnit",
            "Net AP Spend by Business Unit",
            756,
            252,
            504,
            205,
            ["Category:DimCostCenter.BusinessUnit", "Y:_Measures.Net AP Spend"],
        ),
        (
            "tableEx",
            p1,
            "ExpenseGroupSummary",
            "Expense Group Financial Summary",
            20,
            466,
            1240,
            238,
            [
                "Values:DimGLAccount.ExpenseGroup",
                "Values:_Measures.Gross Invoice Spend",
                "Values:_Measures.Credit Memo Amount",
                "Values:_Measures.Net AP Spend",
                "Values:_Measures.Budget Spend",
                "Values:_Measures.Budget Variance",
            ],
        ),
        # Page 2: Vendor & Credit Analysis
        (
            "card",
            p2,
            "KpiGrossSpend",
            "Gross Invoice Spend",
            20,
            105,
            298,
            140,
            ["Values:_Measures.Gross Invoice Spend"],
        ),
        (
            "card",
            p2,
            "KpiCreditMemo",
            "Credit Memo Amount",
            334,
            105,
            298,
            140,
            ["Values:_Measures.Credit Memo Amount"],
        ),
        (
            "card",
            p2,
            "KpiCreditRate",
            "Credit Memo Rate %",
            648,
            105,
            298,
            140,
            ["Values:_Measures.Credit Memo Rate %"],
        ),
        (
            "card",
            p2,
            "KpiInvoiceCount",
            "Invoice Count",
            962,
            105,
            298,
            140,
            ["Values:_Measures.Invoice Count"],
        ),
        (
            "clusteredBarChart",
            p2,
            "SpendByVendorCat",
            "Net AP Spend by Vendor Category",
            20,
            260,
            612,
            210,
            ["Category:DimVendor.VendorCategory", "Y:_Measures.Net AP Spend"],
        ),
        (
            "clusteredBarChart",
            p2,
            "CreditsByVendorCat",
            "Credit Memo Amount by Vendor Category",
            648,
            260,
            612,
            210,
            ["Category:DimVendor.VendorCategory", "Y:_Measures.Credit Memo Amount"],
        ),
        (
            "tableEx",
            p2,
            "VendorLedger",
            "Vendor Spend & Credit Ledger",
            20,
            485,
            1240,
            215,
            [
                "Values:DimVendor.VendorName",
                "Values:DimVendor.VendorCategory",
                "Values:DimVendor.PaymentTerms",
                "Values:_Measures.Invoice Count",
                "Values:_Measures.Gross Invoice Spend",
                "Values:_Measures.Credit Memo Amount",
                "Values:_Measures.Net AP Spend",
            ],
        ),
        # Page 3: Cost Center & Controls
        (
            "card",
            p3,
            "KpiThreeWayPct",
            "Three-Way Match %",
            20,
            105,
            298,
            140,
            ["Values:_Measures.Three-Way Match %"],
        ),
        (
            "card",
            p3,
            "KpiOverdue",
            "Overdue Exposure",
            334,
            105,
            298,
            140,
            ["Values:_Measures.Overdue Exposure"],
        ),
        (
            "card",
            p3,
            "KpiExceptionPct",
            "Exception Rate %",
            648,
            105,
            298,
            140,
            ["Values:_Measures.Exception Rate %"],
        ),
        (
            "card",
            p3,
            "KpiBudgetStatus",
            "Budget Utilization Status",
            962,
            105,
            298,
            140,
            ["Values:_Measures.Budget Utilization Status"],
        ),
        (
            "clusteredBarChart",
            p3,
            "DeptSpendVsBudget",
            "Department Net AP Spend vs Budget",
            20,
            260,
            612,
            210,
            [
                "Category:DimCostCenter.Department",
                "Y:_Measures.Net AP Spend",
                "Y:_Measures.Budget Spend",
            ],
        ),
        (
            "clusteredColumnChart",
            p3,
            "InvoicesByMatchStatus",
            "Invoice Volume by Match Status",
            648,
            260,
            612,
            210,
            ["Category:FactAPInvoices.MatchStatus", "Y:_Measures.Invoice Count"],
        ),
        (
            "tableEx",
            p3,
            "CostCenterControlsTable",
            "Cost Center Controls & Variance",
            20,
            485,
            1240,
            215,
            [
                "Values:DimCostCenter.BusinessUnit",
                "Values:DimCostCenter.CostCenterName",
                "Values:_Measures.Net AP Spend",
                "Values:_Measures.Budget Spend",
                "Values:_Measures.Budget Variance",
                "Values:_Measures.Overdue Exposure",
                "Values:_Measures.Three-Way Match %",
            ],
        ),
    ]

    for vtype, page, name, title, x, y, w, h, bindings in visuals_spec:
        cmd = [
            "add",
            "visual",
            vtype,
            page,
            "-n",
            name,
            "-t",
            title,
            "-x",
            str(x),
            "-y",
            str(y),
            "-w",
            str(w),
            "-h",
            str(h),
        ]
        for binding in bindings:
            cmd.extend(["-d", binding])
        _run_pbir(cmd)
        if vtype == "card":
            _run_pbir(["set", f"{page}/{name}.Visual.categoryLabels.show", "--value", "false"])

    # Polish overrides & requested micro-tweaks
    _run_pbir(
        [
            "set",
            f"{p1}/SpendByBusinessUnit.Visual.categoryAxis.preferredCategoryWidth",
            "--value",
            "20",
        ]
    )
    _run_pbir(
        [
            "set",
            f"{p1}/SpendByBusinessUnit.Visual.categoryAxis.innerPadding",
            "--value",
            "12",
        ]
    )
    _run_pbir(
        [
            "set",
            f"{p1}/SpendByBusinessUnit.Visual.categoryAxis.showAxisTitle",
            "--value",
            "false",
        ]
    )
    _run_pbir(
        [
            "visuals",
            "table-density",
            f"{p1}/ExpenseGroupSummary.Visual",
            "--preset",
            "compact",
            "--row-padding",
            "2",
        ]
    )
    _run_pbir(
        [
            "visuals",
            "sort",
            f"{p2}/CreditsByVendorCat.Visual",
            "--field",
            "_Measures.Credit Memo Amount",
            "--direction",
            "Ascending",
        ]
    )
    _run_pbir(["set", f"{p3}/KpiBudgetStatus.Visual.labels.fontSize", "--value", "26"])
    _run_pbir(["set", f"{p3}/InvoicesByMatchStatus.Visual.labels.show", "--value", "true"])
    _run_pbir(
        [
            "set",
            f"{p3}/InvoicesByMatchStatus.Visual.labels.labelDisplayUnits",
            "--value",
            "1",
        ]
    )
    _run_pbir(["set", f"{p3}/InvoicesByMatchStatus.Visual.labels.labelPrecision", "--value", "0"])
    _run_pbir(
        [
            "set",
            f"{p3}/InvoicesByMatchStatus.Visual.valueAxis.labelDisplayUnits",
            "--value",
            "1",
        ]
    )
    _run_pbir(
        [
            "set",
            f"{p3}/InvoicesByMatchStatus.Visual.valueAxis.labelPrecision",
            "--value",
            "0",
        ]
    )

    print(_run_pbir(["validate", "CorporateFinanceAP.Report", "--all"]))


if __name__ == "__main__":
    build_report()
