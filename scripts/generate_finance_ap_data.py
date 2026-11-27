#!/usr/bin/env python3
"""Generate deterministic messy Corporate Finance / AP Spend datasets for Gate G0 -> G7."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SEED = 20260303
INVOICE_COUNT = 1500
START_DATE = date(2024, 1, 1)
END_DATE = date(2026, 12, 31)

TWO_DP = Decimal("0.01")
FOUR_DP = Decimal("0.0001")

MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_ABBR = (
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


def q2(val: Decimal) -> Decimal:
    return val.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def q4(val: Decimal) -> Decimal:
    return val.quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_dim_date() -> tuple[list[dict[str, object]], list[date], list[date]]:
    rows: list[dict[str, object]] = []
    all_dates: list[date] = []
    month_starts: list[date] = []
    cur = START_DATE
    while cur <= END_DATE:
        date_key = int(cur.strftime("%Y%m%d"))
        quarter_num = ((cur.month - 1) // 3) + 1
        month_start = cur.replace(day=1)
        rows.append(
            {
                "DateKey": date_key,
                "Date": cur.isoformat(),
                "Year": cur.year,
                "Quarter": f"Q{quarter_num}",
                "QuarterNumber": quarter_num,
                "MonthNumber": cur.month,
                "MonthName": MONTH_NAMES[cur.month],
                "YearMonth": f"{cur.year:04d}-{cur.month:02d}",
                "YearMonthSort": cur.year * 100 + cur.month,
                "DayOfWeek": WEEKDAY_NAMES[cur.weekday()],
                "IsWeekend": 1 if cur.weekday() >= 5 else 0,
                "MonthStart": month_start.isoformat(),
            }
        )
        all_dates.append(cur)
        if cur.day == 1:
            month_starts.append(cur)
        cur += timedelta(days=1)
    return rows, all_dates, month_starts


def build_dim_vendor(rng: random.Random) -> list[dict[str, object]]:
    vendors_catalog = [
        ("Apex Cloud Infrastructure", "Cloud Infrastructure", "Net 30", "USA", "Strategic"),
        ("Nordic Fiber Networks", "Cloud Infrastructure", "Net 45", "Sweden", "Preferred"),
        ("Vertex Compute Systems", "Cloud Infrastructure", "Net 30", "USA", "Strategic"),
        ("Pacific Data Centers", "Cloud Infrastructure", "Net 60", "Singapore", "Preferred"),
        ("Helios Cloud Security", "Cloud Infrastructure", "2/10 Net 30", "Germany", "Preferred"),
        ("Kestrel Storage Corp", "Cloud Infrastructure", "Net 30", "UK", "Standard"),
        ("Horizon SaaS Platforms", "Enterprise Software", "Net 30", "USA", "Strategic"),
        ("Boreal Workflow Labs", "Enterprise Software", "Net 45", "Canada", "Preferred"),
        ("Aurelia ERP Extensions", "Enterprise Software", "Net 30", "Netherlands", "Strategic"),
        ("Cobalt BI Connectors", "Enterprise Software", "2/10 Net 30", "USA", "Standard"),
        ("Solstice IAM Suites", "Enterprise Software", "Net 30", "UK", "Preferred"),
        ("Zephyr Observability", "Enterprise Software", "Net 45", "Australia", "Preferred"),
        ("Mercer & Hale LLP", "Advisory & Legal", "Net 30", "USA", "Strategic"),
        ("Kensington Tax Partners", "Advisory & Legal", "Net 45", "UK", "Strategic"),
        ("Rheinland Audit GmbH", "Advisory & Legal", "Net 60", "Germany", "Preferred"),
        ("Tanaka Compliance Advisors", "Advisory & Legal", "Net 30", "Japan", "Preferred"),
        ("Clarion Risk Consulting", "Advisory & Legal", "Net 30", "USA", "Standard"),
        ("Harborline Patent Counsel", "Advisory & Legal", "Net 45", "Singapore", "Standard"),
        ("Ironbridge Facilities Co", "Logistics & Facilities", "Net 30", "USA", "Preferred"),
        ("Thames Corporate Interiors", "Logistics & Facilities", "Net 45", "UK", "Standard"),
        ("Alpine Green Energy", "Logistics & Facilities", "Net 30", "Switzerland", "Preferred"),
        ("Meridian Freight Dispatch", "Logistics & Facilities", "2/10 Net 30", "USA", "Preferred"),
        ("Kyoto Precision Logistics", "Logistics & Facilities", "Net 30", "Japan", "Standard"),
        ("Southern Cross Couriers", "Logistics & Facilities", "Net 15", "Australia", "Standard"),
        ("Northstar Brand Studio", "Marketing Agencies", "Net 30", "USA", "Preferred"),
        ("Seine Creative Collective", "Marketing Agencies", "Net 45", "France", "Standard"),
        ("Catalyst Demand Media", "Marketing Agencies", "2/10 Net 30", "USA", "Preferred"),
        ("Lotus Digital Events", "Marketing Agencies", "Net 30", "Singapore", "Standard"),
        ("Redwood PR Associates", "Marketing Agencies", "Net 30", "Canada", "Standard"),
        ("Metro Outdoor Placements", "Marketing Agencies", "Net 15", "Germany", "Standard"),
        ("Titan Server Hardware", "Hardware & Telecom", "Net 45", "USA", "Strategic"),
        ("Broadlink Telecom Ltd", "Hardware & Telecom", "Net 30", "UK", "Preferred"),
        ("Sakura Optical Gear", "Hardware & Telecom", "Net 60", "Japan", "Preferred"),
        ("Granite Workstation Inc", "Hardware & Telecom", "2/10 Net 30", "USA", "Standard"),
        ("EuroRack Peripherals", "Hardware & Telecom", "Net 30", "Netherlands", "Standard"),
        ("Indus Cloud Links", "Hardware & Telecom", "Net 30", "India", "Standard"),
        ("Silverline Travel Corp", "Marketing Agencies", "Net 15", "USA", "Preferred"),
        ("Vanguard Fleet Leasing", "Logistics & Facilities", "Net 45", "Canada", "Preferred"),
        ("Atlas Payroll Services", "Advisory & Legal", "Net 30", "USA", "Strategic"),
        ("Pulse FinOps Automation", "Enterprise Software", "2/10 Net 30", "Ireland", "Preferred"),
    ]

    rows: list[dict[str, object]] = [
        {
            "VendorKey": -1,
            "VendorCode": "UNK",
            "VendorName": "Unknown Vendor",
            "VendorCategory": "Unassigned",
            "PaymentTerms": "Net 30",
            "Country": "Unknown",
            "VendorTier": "Unassigned",
        }
    ]
    for idx, (name, cat, terms, country, tier) in enumerate(vendors_catalog, start=1):
        _ = rng.random()  # keep RNG state aligned if extended later
        rows.append(
            {
                "VendorKey": idx,
                "VendorCode": f"VND-{idx:04d}",
                "VendorName": name,
                "VendorCategory": cat,
                "PaymentTerms": terms,
                "Country": country,
                "VendorTier": tier,
            }
        )
    return rows


def build_dim_cost_center() -> list[dict[str, object]]:
    specs = [
        ("CC-101", "NA Cloud Platform", "North America", "Engineering", "US01"),
        ("CC-102", "NA Commercial Growth", "North America", "Commercial", "US01"),
        ("CC-103", "NA Controllership", "North America", "Finance & Legal", "US02"),
        ("CC-104", "NA Workplace Ops", "North America", "Operations", "CA01"),
        ("CC-201", "EMEA Core Engineering", "Europe", "Engineering", "UK01"),
        ("CC-202", "EMEA Enterprise Sales", "Europe", "Commercial", "DE01"),
        ("CC-203", "EMEA Statutory Finance", "Europe", "Finance & Legal", "UK01"),
        ("CC-204", "EMEA Logistics Hub", "Europe", "Operations", "NL01"),
        ("CC-301", "APAC Digital Infra", "APAC", "Engineering", "SG01"),
        ("CC-302", "APAC Field Revenue", "APAC", "Commercial", "JP01"),
        ("CC-303", "APAC Regional Treasury", "APAC", "Finance & Legal", "SG01"),
        ("CC-304", "APAC Fulfillment Ops", "APAC", "Operations", "AU01"),
        ("CC-401", "Global Security & SRE", "Shared Services", "Engineering", "US01"),
        ("CC-402", "Global Procurement", "Shared Services", "Operations", "US01"),
        ("CC-403", "Global Tax & Audit", "Shared Services", "Finance & Legal", "UK01"),
        ("CC-404", "Global Brand & Events", "Shared Services", "Commercial", "US01"),
    ]
    rows: list[dict[str, object]] = [
        {
            "CostCenterKey": -1,
            "CostCenterCode": "UNK",
            "CostCenterName": "Unknown Cost Center",
            "BusinessUnit": "Unknown",
            "Department": "Unknown",
            "EntityCode": "UNK",
        }
    ]
    for idx, (code, name, bu, dept, entity) in enumerate(specs, start=1):
        rows.append(
            {
                "CostCenterKey": idx,
                "CostCenterCode": code,
                "CostCenterName": name,
                "BusinessUnit": bu,
                "Department": dept,
                "EntityCode": entity,
            }
        )
    return rows


def build_dim_gl_account() -> list[dict[str, object]]:
    specs = [
        ("510100", "Cloud Hosting & Compute", "Cloud & IT", "Expense"),
        ("510200", "Enterprise SaaS Subscriptions", "Cloud & IT", "Expense"),
        ("510300", "IT Hardware & Networking", "Cloud & IT", "Expense"),
        ("520100", "External Legal Counsel", "Professional Services", "Expense"),
        ("520200", "Audit & Tax Advisory", "Professional Services", "Expense"),
        ("520300", "Management Consulting", "Professional Services", "Expense"),
        ("530100", "Office Leases & Utilities", "Facilities & Ops", "Expense"),
        ("530200", "Freight & Warehousing", "Facilities & Ops", "Expense"),
        ("530300", "Equipment Maintenance", "Facilities & Ops", "Expense"),
        ("540100", "Demand Generation Media", "Marketing & Travel", "Expense"),
        ("540200", "Trade Shows & Conferences", "Marketing & Travel", "Expense"),
        ("540300", "Corporate Travel & Lodging", "Marketing & Travel", "Expense"),
    ]
    rows: list[dict[str, object]] = [
        {
            "GLAccountKey": -1,
            "GLAccountCode": "999999",
            "GLAccountName": "Unknown GL Account",
            "ExpenseGroup": "Unknown",
            "AccountType": "Expense",
        }
    ]
    for idx, (code, name, group, acct_type) in enumerate(specs, start=1):
        rows.append(
            {
                "GLAccountKey": idx,
                "GLAccountCode": code,
                "GLAccountName": name,
                "ExpenseGroup": group,
                "AccountType": acct_type,
            }
        )
    return rows


def build_dim_approver() -> list[dict[str, object]]:
    specs = [
        (
            "APR-001",
            "Claire Bennett",
            "VP Finance NA",
            "North America",
            "claire.na@finco.example",
        ),
        (
            "APR-002",
            "Diego Morales",
            "Regional Controller EMEA",
            "Europe",
            "diego.eu@finco.example",
        ),
        (
            "APR-003",
            "Priya Nair",
            "APAC Finance Director",
            "APAC",
            "priya.apac@finco.example",
        ),
        (
            "APR-004",
            "Hannah Brooks",
            "Procurement Lead",
            "Shared Services",
            "hannah.ss@finco.example",
        ),
        ("APR-005", "Marcus Chen", "AP Manager NA", "North America", "marcus.na@finco.example"),
        ("APR-006", "Elena Weber", "Senior AP Specialist", "Europe", "elena.eu@finco.example"),
        ("APR-007", "Kenji Sato", "AP Lead APAC", "APAC", "kenji.apac@finco.example"),
        (
            "APR-008",
            "Nadia Al-Mansoor",
            "Shared Services Controller",
            "Shared Services",
            "nadia.ss@finco.example",
        ),
    ]
    rows: list[dict[str, object]] = [
        {
            "ApproverKey": -1,
            "ApproverCode": "UNK",
            "ApproverName": "Unassigned Approver",
            "RoleTitle": "Unassigned",
            "BusinessUnit": "Unknown",
            "UserPrincipalName": "",
        }
    ]
    for idx, (code, name, title, bu, upn) in enumerate(specs, start=1):
        rows.append(
            {
                "ApproverKey": idx,
                "ApproverCode": code,
                "ApproverName": name,
                "RoleTitle": title,
                "BusinessUnit": bu,
                "UserPrincipalName": upn,
            }
        )
    return rows


def _terms_days(terms: str) -> int:
    if "15" in terms:
        return 15
    if "45" in terms:
        return 45
    if "60" in terms:
        return 60
    return 30


def _early_discount_for_terms(terms: str) -> Decimal:
    if terms.startswith("2/10"):
        return Decimal("0.0200")
    if terms == "Net 15":
        return Decimal("0.0150")
    if terms == "Net 30":
        return Decimal("0.0100")
    if terms == "Net 45":
        return Decimal("0.0250")
    return Decimal("0.0000")


def _format_comma_decimal(val: Decimal) -> str:
    sign = "-" if val < 0 else ""
    abs_val = abs(val)
    return f"{sign}{abs_val:,.2f}"


def _format_dirty_amount(val: Decimal, style_idx: int) -> str:
    """Apply deterministic stringified currency / accounting-parens representations."""
    abs_comma = f"{abs(val):,.2f}"
    if val < 0:
        mod = style_idx % 4
        if mod == 0:
            return f"(${abs_comma})"
        if mod == 1:
            return f"({abs_comma})"
        if mod == 2:
            return f"USD -{abs(val):.2f}"
        return f"{val:.2f}"
    mod = style_idx % 4
    if mod == 0:
        return f"${abs_comma}"
    if mod == 1:
        return f"USD {abs_comma}"
    if mod == 2:
        return f"+${abs_comma}"
    return f"{val:.2f}"


def _format_dirty_date(d: date, style_idx: int) -> tuple[str, str]:
    """Return (formatted_date, pattern_tag), prioritizing US MM/DD/YYYY when day > 12."""
    if d.day > 12 and style_idx % 3 == 0:
        return f"{d.month:02d}/{d.day:02d}/{d.year:04d}", "US_MM_DD_YYYY_SLASH"
    mod = style_idx % 5
    if mod == 0:
        return d.isoformat(), "ISO_YYYY_MM_DD"
    if mod == 1:
        return f"{d.month:02d}/{d.day:02d}/{d.year:04d}", (
            "US_MM_DD_YYYY_SLASH" if d.day > 12 else "AMBIGUOUS_SLASH_DATE"
        )
    if mod == 2:
        return f"{d.year:04d}/{d.month:02d}/{d.day:02d}", "SLASH_YYYY_MM_DD"
    if mod == 3:
        return f"{d.day:02d}-{MONTH_ABBR[d.month]}-{d.year:04d}", "TEXT_MON_DATE"
    return f"{MONTH_ABBR[d.month]} {d.day}, {d.year:04d}", "TEXT_MON_DATE"


def _drift_whitespace_casing(text: str, variant: int) -> str:
    mod = variant % 4
    if mod == 0:
        return f"  {text} "
    if mod == 1:
        return text.replace(" ", "  ", 1) if " " in text else f" {text}"
    if mod == 2:
        return text.upper()
    return text.lower()


def _sum_invoice_rows(rows: list[dict[str, object]]) -> dict[str, str]:
    line_sum = sum((Decimal(str(r["LineAmount"])) for r in rows), Decimal("0.00"))
    tax_sum = sum((Decimal(str(r["TaxAmount"])) for r in rows), Decimal("0.00"))
    net_sum = sum((Decimal(str(r["NetAmount"])) for r in rows), Decimal("0.00"))
    return {
        "row_count": len(rows),  # type: ignore[dict-item]
        "line_amount": f"{q2(line_sum):.2f}",
        "tax_amount": f"{q2(tax_sum):.2f}",
        "net_amount": f"{q2(net_sum):.2f}",
    }


def generate_finance_ap_dataset(base_dir: Path, seed: int = SEED) -> dict[str, Any]:
    """Generate clean source CSVs, corrupted raw CSVs, and defect_manifest.json."""
    rng = random.Random(seed)
    clean_dir = base_dir / "clean"
    raw_dir = base_dir / "raw"
    gt_dir = base_dir / "ground_truth"

    dim_date, all_dates, month_starts = build_dim_date()
    dim_vendor = build_dim_vendor(rng)
    dim_cc = build_dim_cost_center()
    dim_gl = build_dim_gl_account()
    dim_approver = build_dim_approver()

    vendor_by_key = {int(r["VendorKey"]): r for r in dim_vendor}
    cc_by_key = {int(r["CostCenterKey"]): r for r in dim_cc}
    approvers_by_bu: dict[str, list[int]] = defaultdict(list)
    for row in dim_approver:
        if int(row["ApproverKey"]) > 0:
            approvers_by_bu[str(row["BusinessUnit"])].append(int(row["ApproverKey"]))

    active_vendor_keys = [int(r["VendorKey"]) for r in dim_vendor if int(r["VendorKey"]) > 0]
    active_cc_keys = [int(r["CostCenterKey"]) for r in dim_cc if int(r["CostCenterKey"]) > 0]
    active_gl_keys = [int(r["GLAccountKey"]) for r in dim_gl if int(r["GLAccountKey"]) > 0]

    # Map VendorCategory to natural GL account keys for realistic ERP coding
    cat_to_gl: dict[str, list[int]] = {
        "Cloud Infrastructure": [1, 2, 3],
        "Enterprise Software": [2, 1, 6],
        "Advisory & Legal": [4, 5, 6],
        "Logistics & Facilities": [7, 8, 9],
        "Marketing Agencies": [10, 11, 12],
        "Hardware & Telecom": [3, 1, 9],
    }

    clean_invoices: list[dict[str, object]] = []
    line_counts_cycle = [1, 2, 2, 3, 3, 4]
    inv_header_totals: dict[str, Decimal] = {}

    for inv_idx in range(1, INVOICE_COUNT + 1):
        posting_dt = rng.choice(all_dates)
        date_key = int(posting_dt.strftime("%Y%m%d"))
        inv_id = f"INV-{posting_dt.year}-{inv_idx:05d}"

        vendor_key = rng.choice(active_vendor_keys)
        cc_key = rng.choice(active_cc_keys)
        bu = str(cc_by_key[cc_key]["BusinessUnit"])
        approver_key = rng.choice(approvers_by_bu[bu])

        vendor_row = vendor_by_key[vendor_key]
        terms = str(vendor_row["PaymentTerms"])
        terms_days = _terms_days(terms)
        due_dt = posting_dt + timedelta(days=terms_days)

        is_credit_memo = (inv_idx % 12 == 0)
        doc_type = "Credit Memo" if is_credit_memo else "Invoice"

        if inv_idx % 17 == 0:
            pay_status = "On Hold"
            match_status = "Exception"
            days_to_pay = 0
            is_paid_on_time = 0
        elif posting_dt >= date(2026, 10, 1) and inv_idx % 3 != 0:
            pay_status = "Open" if inv_idx % 2 == 0 else "Overdue"
            match_status = "2-Way Matched" if inv_idx % 5 == 0 else "3-Way Matched"
            days_to_pay = 0
            is_paid_on_time = 0
        else:
            pay_status = "Paid"
            match_status = "3-Way Matched" if rng.random() < 0.82 else "2-Way Matched"
            days_to_pay = rng.randint(max(5, terms_days - 14), terms_days + 18)
            is_paid_on_time = 1 if days_to_pay <= terms_days else 0

        disc_rate = _early_discount_for_terms(terms)
        n_lines = 2 if is_credit_memo else line_counts_cycle[(inv_idx - 1) % len(line_counts_cycle)]
        gl_candidates = cat_to_gl.get(str(vendor_row["VendorCategory"]), active_gl_keys)

        inv_total = Decimal("0.00")
        for line_no in range(1, n_lines + 1):
            gl_key = gl_candidates[(line_no - 1 + inv_idx) % len(gl_candidates)]
            line_vendor = vendor_key
            line_cc = cc_key
            line_gl = gl_key
            line_approver = approver_key

            # Deterministically place ~3% of lines into Unknown (-1) bucket in clean source
            # (never overlapping with exact/conflict dup target indices handled separately below)
            global_seq = len(clean_invoices)
            if global_seq % 40 == 7:
                line_vendor = -1
            elif global_seq % 40 == 17:
                line_cc = -1
            elif global_seq % 40 == 27:
                line_gl = -1
            elif global_seq % 40 == 37:
                line_approver = -1

            qty = rng.randint(1, 20)
            unit_price = Decimal(rng.randint(85, 1850)) + Decimal("0.50")
            raw_line_amt = q2(Decimal(qty) * unit_price)
            tax_rate = Decimal("0.05") if line_no % 2 == 1 else Decimal("0.08")

            if is_credit_memo:
                line_amt = -abs(q2(raw_line_amt * Decimal("0.55")))
                tax_amt = -abs(q2(abs(line_amt) * tax_rate))
            else:
                line_amt = raw_line_amt
                tax_amt = q2(line_amt * tax_rate)

            net_amt = q2(line_amt + tax_amt)
            inv_total = q2(inv_total + net_amt)

            clean_invoices.append(
                {
                    "InvoiceID": inv_id,
                    "LineNumber": line_no,
                    "DateKey": date_key,
                    "PostingDate": posting_dt.isoformat(),
                    "DueDate": due_dt.isoformat(),
                    "VendorKey": line_vendor,
                    "CostCenterKey": line_cc,
                    "GLAccountKey": line_gl,
                    "ApproverKey": line_approver,
                    "DocumentType": doc_type,
                    "PaymentStatus": pay_status,
                    "MatchStatus": match_status,
                    "Quantity": qty,
                    "UnitPrice": f"{unit_price:.2f}",
                    "LineAmount": f"{line_amt:.2f}",
                    "TaxAmount": f"{tax_amt:.2f}",
                    "NetAmount": f"{net_amt:.2f}",
                    "EarlyPayDiscountRate": f"{q4(disc_rate):.4f}",
                    "DaysToPay": days_to_pay,
                    "IsPaidOnTime": is_paid_on_time,
                }
            )
        inv_header_totals[inv_id] = inv_total

    # FactAPBudget: 36 months x 16 active cost centers x 12 active GL accounts
    clean_budget: list[dict[str, object]] = []
    for m_dt in month_starts:
        m_key = int(m_dt.strftime("%Y%m%d"))
        season = Decimal("1.08") if m_dt.month in (11, 12) else Decimal("1.00")
        yr_growth = Decimal("1.00") + Decimal(m_dt.year - 2024) * Decimal("0.06")
        for cc_key in active_cc_keys:
            for gl_key in active_gl_keys:
                base = Decimal(rng.randint(4500, 14500))
                budget_amt = q2(base * season * yr_growth)
                clean_budget.append(
                    {
                        "PostingDate": m_dt.isoformat(),
                        "DateKey": m_key,
                        "CostCenterKey": cc_key,
                        "GLAccountKey": gl_key,
                        "BudgetAmount": f"{budget_amt:.2f}",
                    }
                )

    # ---- Write clean CSVs ----
    dim_date_cols = [
        "DateKey",
        "Date",
        "Year",
        "Quarter",
        "QuarterNumber",
        "MonthNumber",
        "MonthName",
        "YearMonth",
        "YearMonthSort",
        "DayOfWeek",
        "IsWeekend",
        "MonthStart",
    ]
    dim_vendor_cols = [
        "VendorKey",
        "VendorCode",
        "VendorName",
        "VendorCategory",
        "PaymentTerms",
        "Country",
        "VendorTier",
    ]
    dim_cc_cols = [
        "CostCenterKey",
        "CostCenterCode",
        "CostCenterName",
        "BusinessUnit",
        "Department",
        "EntityCode",
    ]
    dim_gl_cols = ["GLAccountKey", "GLAccountCode", "GLAccountName", "ExpenseGroup", "AccountType"]
    dim_app_cols = [
        "ApproverKey",
        "ApproverCode",
        "ApproverName",
        "RoleTitle",
        "BusinessUnit",
        "UserPrincipalName",
    ]
    clean_inv_cols = [
        "InvoiceID",
        "LineNumber",
        "DateKey",
        "PostingDate",
        "DueDate",
        "VendorKey",
        "CostCenterKey",
        "GLAccountKey",
        "ApproverKey",
        "DocumentType",
        "PaymentStatus",
        "MatchStatus",
        "Quantity",
        "UnitPrice",
        "LineAmount",
        "TaxAmount",
        "NetAmount",
        "EarlyPayDiscountRate",
        "DaysToPay",
        "IsPaidOnTime",
    ]
    clean_budget_cols = ["PostingDate", "DateKey", "CostCenterKey", "GLAccountKey", "BudgetAmount"]

    write_csv(clean_dir / "DimDate.csv", dim_date_cols, dim_date)
    write_csv(clean_dir / "DimVendor.csv", dim_vendor_cols, dim_vendor)
    write_csv(clean_dir / "DimCostCenter.csv", dim_cc_cols, dim_cc)
    write_csv(clean_dir / "DimGLAccount.csv", dim_gl_cols, dim_gl)
    write_csv(clean_dir / "DimApprover.csv", dim_app_cols, dim_approver)
    write_csv(clean_dir / "FactAPInvoices.csv", clean_inv_cols, clean_invoices)
    write_csv(clean_dir / "FactAPBudget.csv", clean_budget_cols, clean_budget)

    # ---- Corrupt Dimensions (whitespace / casing drift only; PKs stay unique) ----
    ws_dim_count = 0
    raw_vendor = [dict(r) for r in dim_vendor]
    for i, r in enumerate(raw_vendor):
        if int(r["VendorKey"]) > 0 and i % 4 == 1:
            r["VendorName"] = _drift_whitespace_casing(str(r["VendorName"]), i)
            ws_dim_count += 1

    raw_cc = [dict(r) for r in dim_cc]
    for i, r in enumerate(raw_cc):
        if int(r["CostCenterKey"]) > 0 and i % 4 == 1:
            r["CostCenterName"] = _drift_whitespace_casing(str(r["CostCenterName"]), i + 1)
            ws_dim_count += 1

    raw_gl = [dict(r) for r in dim_gl]
    for i, r in enumerate(raw_gl):
        if int(r["GLAccountKey"]) > 0 and i % 3 == 1:
            r["GLAccountName"] = _drift_whitespace_casing(str(r["GLAccountName"]), i + 2)
            ws_dim_count += 1

    raw_approver = [dict(r) for r in dim_approver]
    for i, r in enumerate(raw_approver):
        if int(r["ApproverKey"]) > 0 and i % 4 == 1:
            r["RoleTitle"] = _drift_whitespace_casing(str(r["RoleTitle"]), i)
            ws_dim_count += 1

    # ---- Corrupt FactAPInvoices (preserve exact clean row order FIRST) ----
    raw_invoices: list[dict[str, object]] = []
    date_pattern_counts: dict[str, int] = defaultdict(int)
    us_day_gt_12_samples: list[str] = []
    currency_samples: list[str] = []
    pct_samples: list[str] = []
    neg_credit_samples: list[str] = []
    ws_fact_count = 0
    null_fk_count = 0
    orphan_fk_count = 0
    null_orphan_samples: list[str] = []
    stringified_amt_count = 0
    stringified_pct_count = 0
    inconsistent_repr_samples: list[str] = []

    null_tokens = ["", "NULL", "TBD"]
    orphan_tokens = ["9999", "8888"]
    fk_corruption_seq = 0

    for idx, clean_row in enumerate(clean_invoices):
        inv_id = str(clean_row["InvoiceID"])
        post_dt = date.fromisoformat(str(clean_row["PostingDate"]))
        due_dt = date.fromisoformat(str(clean_row["DueDate"]))

        post_str, post_tag = _format_dirty_date(post_dt, idx)
        due_str, due_tag = _format_dirty_date(due_dt, idx + 1)
        date_pattern_counts[post_tag] += 1
        date_pattern_counts[due_tag] += 1
        if post_tag == "US_MM_DD_YYYY_SLASH" and len(us_day_gt_12_samples) < 5:
            us_day_gt_12_samples.append(post_str)

        v_key = str(clean_row["VendorKey"])
        cc_key = str(clean_row["CostCenterKey"])
        gl_key = str(clean_row["GLAccountKey"])
        a_key = str(clean_row["ApproverKey"])
        for col_label, cur_val in (
            ("VendorKey", v_key),
            ("CostCenterKey", cc_key),
            ("GLAccountKey", gl_key),
            ("ApproverKey", a_key),
        ):
            if cur_val == "-1":
                if fk_corruption_seq % 2 == 0:
                    rep = null_tokens[(fk_corruption_seq // 2) % len(null_tokens)]
                    null_fk_count += 1
                else:
                    rep = orphan_tokens[(fk_corruption_seq // 2) % len(orphan_tokens)]
                    orphan_fk_count += 1
                fk_corruption_seq += 1
                if len(null_orphan_samples) < 6:
                    null_orphan_samples.append(f"{col_label}={rep or '<BLANK>'}")
                if col_label == "VendorKey":
                    v_key = rep
                elif col_label == "CostCenterKey":
                    cc_key = rep
                elif col_label == "GLAccountKey":
                    gl_key = rep
                else:
                    a_key = rep

        doc_type = str(clean_row["DocumentType"])
        pay_status = str(clean_row["PaymentStatus"])
        match_status = str(clean_row["MatchStatus"])
        if idx % 11 == 0:
            doc_type = _drift_whitespace_casing(doc_type, idx)
            ws_fact_count += 1
        if idx % 9 == 0:
            pay_status = _drift_whitespace_casing(pay_status, idx + 1)
            ws_fact_count += 1
        if idx % 13 == 0:
            match_status = _drift_whitespace_casing(match_status, idx + 2)
            ws_fact_count += 1

        line_dec = Decimal(str(clean_row["LineAmount"]))
        tax_dec = Decimal(str(clean_row["TaxAmount"]))
        net_dec = Decimal(str(clean_row["NetAmount"]))
        hdr_dec = inv_header_totals[inv_id]

        if idx % 3 != 2:
            line_str = _format_dirty_amount(line_dec, idx)
            tax_str = _format_dirty_amount(tax_dec, idx + 1)
            net_str = _format_dirty_amount(net_dec, idx + 2)
            hdr_str = _format_dirty_amount(hdr_dec, idx + 3)
            if line_str != f"{line_dec:.2f}":
                stringified_amt_count += 1
                if len(currency_samples) < 6:
                    currency_samples.append(line_str)
                if len(inconsistent_repr_samples) < 6:
                    inconsistent_repr_samples.append(line_str)
        else:
            line_str = f"{line_dec:.2f}"
            tax_str = f"{tax_dec:.2f}"
            net_str = f"{net_dec:.2f}"
            hdr_str = f"{hdr_dec:.2f}"

        if net_dec < 0 and len(neg_credit_samples) < 6:
            neg_credit_samples.append(net_str)

        disc_dec = Decimal(str(clean_row["EarlyPayDiscountRate"]))
        if idx % 3 == 0:
            disc_str = f"{q2(disc_dec * Decimal('100')):.2f}%"
            stringified_pct_count += 1
            if len(pct_samples) < 5:
                pct_samples.append(disc_str)
        else:
            disc_str = f"{disc_dec:.4f}"

        raw_invoices.append(
            {
                "InvoiceID": inv_id,
                "LineNumber": clean_row["LineNumber"],
                "PostingDate": post_str,
                "DueDate": due_str,
                "VendorKey": v_key,
                "CostCenterKey": cc_key,
                "GLAccountKey": gl_key,
                "ApproverKey": a_key,
                "DocumentType": doc_type,
                "PaymentStatus": pay_status,
                "MatchStatus": match_status,
                "Quantity": clean_row["Quantity"],
                "UnitPrice": clean_row["UnitPrice"],
                "LineAmount": line_str,
                "TaxAmount": tax_str,
                "NetAmount": net_str,
                "InvoiceHeaderTotal": hdr_str,
                "EarlyPayDiscountRate": disc_str,
                "DaysToPay": clean_row["DaysToPay"],
                "IsPaidOnTime": clean_row["IsPaidOnTime"],
            }
        )

    # Inject duplicate_rows (45 exact copies) strictly AFTER original rows
    exact_dup_indices = [i for i in range(5, len(clean_invoices), 75)][:45]
    exact_dup_rows_clean_equiv: list[dict[str, object]] = []
    exact_dup_samples: list[str] = []
    for src_i in exact_dup_indices:
        raw_invoices.append(dict(raw_invoices[src_i]))
        exact_dup_rows_clean_equiv.append(clean_invoices[src_i])
        if len(exact_dup_samples) < 4:
            exact_dup_samples.append(
                f"{clean_invoices[src_i]['InvoiceID']}#{clean_invoices[src_i]['LineNumber']}"
            )

    # Inject duplicate_keys (35 colliding keys with changed status & amounts) AFTER originals
    exact_dup_set = set(exact_dup_indices)
    conflict_indices = [
        i
        for i in range(10, len(clean_invoices), 85)
        if i not in exact_dup_set
        and str(clean_invoices[i]["DocumentType"]) == "Invoice"
        and str(clean_invoices[i]["VendorKey"]) != "-1"
    ][:35]
    conflict_dup_rows_clean_equiv: list[dict[str, object]] = []
    conflict_samples: list[str] = []
    for k_i, src_i in enumerate(conflict_indices):
        orig_clean = clean_invoices[src_i]
        delta_line = Decimal("25.00") if k_i % 2 == 0 else Decimal("-15.00")
        delta_tax = Decimal("2.00") if k_i % 2 == 0 else Decimal("-1.20")
        new_line = q2(Decimal(str(orig_clean["LineAmount"])) + delta_line)
        new_tax = q2(Decimal(str(orig_clean["TaxAmount"])) + delta_tax)
        new_net = q2(new_line + new_tax)

        conflict_raw = dict(raw_invoices[src_i])
        conflict_raw["PaymentStatus"] = "On Hold"
        conflict_raw["LineAmount"] = _format_dirty_amount(new_line, k_i)
        conflict_raw["TaxAmount"] = _format_dirty_amount(new_tax, k_i + 1)
        conflict_raw["NetAmount"] = _format_dirty_amount(new_net, k_i + 2)
        raw_invoices.append(conflict_raw)

        equiv = dict(orig_clean)
        equiv["LineAmount"] = f"{new_line:.2f}"
        equiv["TaxAmount"] = f"{new_tax:.2f}"
        equiv["NetAmount"] = f"{new_net:.2f}"
        conflict_dup_rows_clean_equiv.append(equiv)
        if len(conflict_samples) < 4:
            conflict_samples.append(f"{orig_clean['InvoiceID']}#{orig_clean['LineNumber']}")

    # Corrupt FactAPBudget (mixed dates & stringified currency)
    raw_budget: list[dict[str, object]] = []
    budget_stringified_count = 0
    for idx, b_row in enumerate(clean_budget):
        m_dt = date.fromisoformat(str(b_row["PostingDate"]))
        m_str, m_tag = _format_dirty_date(m_dt, idx)
        date_pattern_counts[m_tag] += 1
        amt_dec = Decimal(str(b_row["BudgetAmount"]))
        if idx % 3 == 0:
            amt_str = _format_dirty_amount(amt_dec, idx)
            if amt_str != f"{amt_dec:.2f}":
                budget_stringified_count += 1
        else:
            amt_str = f"{amt_dec:.2f}"
        raw_budget.append(
            {
                "PostingDate": m_str,
                "CostCenterKey": b_row["CostCenterKey"],
                "GLAccountKey": b_row["GLAccountKey"],
                "BudgetAmount": amt_str,
            }
        )

    raw_inv_cols = [
        "InvoiceID",
        "LineNumber",
        "PostingDate",
        "DueDate",
        "VendorKey",
        "CostCenterKey",
        "GLAccountKey",
        "ApproverKey",
        "DocumentType",
        "PaymentStatus",
        "MatchStatus",
        "Quantity",
        "UnitPrice",
        "LineAmount",
        "TaxAmount",
        "NetAmount",
        "InvoiceHeaderTotal",
        "EarlyPayDiscountRate",
        "DaysToPay",
        "IsPaidOnTime",
    ]
    raw_budget_cols = ["PostingDate", "CostCenterKey", "GLAccountKey", "BudgetAmount"]

    write_csv(raw_dir / "DimDate.csv", dim_date_cols, dim_date)
    write_csv(raw_dir / "DimVendor.csv", dim_vendor_cols, raw_vendor)
    write_csv(raw_dir / "DimCostCenter.csv", dim_cc_cols, raw_cc)
    write_csv(raw_dir / "DimGLAccount.csv", dim_gl_cols, raw_gl)
    write_csv(raw_dir / "DimApprover.csv", dim_app_cols, raw_approver)
    write_csv(raw_dir / "FactAPInvoices.csv", raw_inv_cols, raw_invoices)
    write_csv(raw_dir / "FactAPBudget.csv", raw_budget_cols, raw_budget)

    # Financial checksums & Unknown (-1) bucket breakdown
    clean_sums = _sum_invoice_rows(clean_invoices)
    dup_sums = _sum_invoice_rows(exact_dup_rows_clean_equiv)
    conflict_sums = _sum_invoice_rows(conflict_dup_rows_clean_equiv)
    raw_total_line = (
        Decimal(clean_sums["line_amount"])
        + Decimal(dup_sums["line_amount"])
        + Decimal(conflict_sums["line_amount"])
    )
    raw_total_tax = (
        Decimal(clean_sums["tax_amount"])
        + Decimal(dup_sums["tax_amount"])
        + Decimal(conflict_sums["tax_amount"])
    )
    raw_total_net = (
        Decimal(clean_sums["net_amount"])
        + Decimal(dup_sums["net_amount"])
        + Decimal(conflict_sums["net_amount"])
    )

    credit_rows = [r for r in clean_invoices if str(r["DocumentType"]) == "Credit Memo"]
    invoice_only_rows = [r for r in clean_invoices if str(r["DocumentType"]) == "Invoice"]
    unknown_cc_rows = [r for r in clean_invoices if int(r["CostCenterKey"]) == -1]
    unknown_app_rows = [r for r in clean_invoices if int(r["ApproverKey"]) == -1]
    unknown_vendor_rows = [r for r in clean_invoices if int(r["VendorKey"]) == -1]
    unknown_gl_rows = [r for r in clean_invoices if int(r["GLAccountKey"]) == -1]
    budget_total = sum((Decimal(str(r["BudgetAmount"])) for r in clean_budget), Decimal("0.00"))
    multi_line_invoices = {
        k
        for k, cnt in (
            (inv_id, sum(1 for r in clean_invoices if str(r["InvoiceID"]) == inv_id))
            for inv_id in inv_header_totals
        )
        if cnt > 1
    }

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "domain": "corporate_finance_ap_spend",
        "seed": seed,
        "row_counts": {
            "DimDate": {"clean": len(dim_date), "raw": len(dim_date)},
            "DimVendor": {"clean": len(dim_vendor), "raw": len(raw_vendor)},
            "DimCostCenter": {"clean": len(dim_cc), "raw": len(raw_cc)},
            "DimGLAccount": {"clean": len(dim_gl), "raw": len(raw_gl)},
            "DimApprover": {"clean": len(dim_approver), "raw": len(raw_approver)},
            "FactAPInvoices": {"clean": len(clean_invoices), "raw": len(raw_invoices)},
            "FactAPBudget": {"clean": len(clean_budget), "raw": len(raw_budget)},
        },
        "financial_checksums": {
            "FactAPInvoices_clean": clean_sums,
            "FactAPInvoices_exact_duplicates": dup_sums,
            "FactAPInvoices_key_conflicts": conflict_sums,
            "FactAPInvoices_raw_expected": {
                "row_count": len(raw_invoices),
                "line_amount": f"{q2(raw_total_line):.2f}",
                "tax_amount": f"{q2(raw_total_tax):.2f}",
                "net_amount": f"{q2(raw_total_net):.2f}",
            },
            "FactAPInvoices_gross_invoices": _sum_invoice_rows(invoice_only_rows),
            "FactAPInvoices_credit_memos": _sum_invoice_rows(credit_rows),
            "unknown_minus_one_buckets": {
                "CostCenterKey_-1": _sum_invoice_rows(unknown_cc_rows),
                "ApproverKey_-1": _sum_invoice_rows(unknown_app_rows),
                "VendorKey_-1": _sum_invoice_rows(unknown_vendor_rows),
                "GLAccountKey_-1": _sum_invoice_rows(unknown_gl_rows),
            },
            "FactAPBudget_total": f"{q2(budget_total):.2f}",
        },
        "defects": {
            "duplicate_invoice_lines": {
                "exact_duplicate_rows": len(exact_dup_indices),
                "conflicting_duplicate_keys": len(conflict_indices),
                "total_quarantine_expected": len(exact_dup_indices) + len(conflict_indices),
                "files": ["FactAPInvoices.csv"],
                "samples": exact_dup_samples + conflict_samples,
            },
            "mixed_date_formats": {
                "pattern_counts": dict(sorted(date_pattern_counts.items())),
                "us_mm_dd_day_gt_12_count": date_pattern_counts.get("US_MM_DD_YYYY_SLASH", 0),
                "files": ["FactAPInvoices.csv", "FactAPBudget.csv"],
                "samples": us_day_gt_12_samples,
            },
            "stringified_currency_values": {
                "count": stringified_amt_count + budget_stringified_count,
                "fact_ap_invoices_count": stringified_amt_count,
                "fact_ap_budget_count": budget_stringified_count,
                "files": ["FactAPInvoices.csv", "FactAPBudget.csv"],
                "samples": currency_samples,
            },
            "negative_credit_memos": {
                "count": len(credit_rows),
                "distinct_credit_invoices": len({str(r["InvoiceID"]) for r in credit_rows}),
                "files": ["FactAPInvoices.csv"],
                "samples": neg_credit_samples,
            },
            "whitespace_casing_drift": {
                "count": ws_fact_count + ws_dim_count,
                "fact_cell_count": ws_fact_count,
                "dimension_cell_count": ws_dim_count,
                "files": [
                    "FactAPInvoices.csv",
                    "DimVendor.csv",
                    "DimCostCenter.csv",
                    "DimGLAccount.csv",
                    "DimApprover.csv",
                ],
            },
            "null_or_orphan_foreign_keys": {
                "count": null_fk_count + orphan_fk_count,
                "null_sentinel_count": null_fk_count,
                "orphan_id_count": orphan_fk_count,
                "files": ["FactAPInvoices.csv"],
                "samples": null_orphan_samples,
            },
            "mixed_entity_grain": {
                "count": len(multi_line_invoices),
                "repeated_header_column": "InvoiceHeaderTotal",
                "monthly_budget_rows": len(clean_budget),
                "files": ["FactAPInvoices.csv", "FactAPBudget.csv"],
            },
            "inconsistent_financial_representation": {
                "count": stringified_amt_count + stringified_pct_count,
                "currency_and_accounting_parens_count": stringified_amt_count,
                "percentage_rate_strings_count": stringified_pct_count,
                "files": ["FactAPInvoices.csv", "FactAPBudget.csv"],
                "samples": inconsistent_repr_samples + pct_samples,
            },
        },
    }

    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / "defect_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic messy Finance/AP Spend dataset."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/finance_ap"),
        help="Target base directory for clean/, raw/, and ground_truth/.",
    )
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    manifest = generate_finance_ap_dataset(args.output_dir, seed=args.seed)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "row_counts": manifest["row_counts"],
                "net_clean": manifest["financial_checksums"]["FactAPInvoices_clean"]["net_amount"],
                "net_raw": manifest["financial_checksums"]["FactAPInvoices_raw_expected"][
                    "net_amount"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
