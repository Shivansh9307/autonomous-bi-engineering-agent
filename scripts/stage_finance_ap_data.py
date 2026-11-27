#!/usr/bin/env python3
"""Stage messy Finance / AP Spend flat files using signed Gate G0 policies."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from bi_agent.gates.g0_data_profiling import NULL_SENTINELS, parse_numeric_value

TWO_DP = Decimal("0.01")
FOUR_DP = Decimal("0.0001")

SPECIAL_TOKENS: dict[str, str] = {
    "na": "NA",
    "emea": "EMEA",
    "apac": "APAC",
    "it": "IT",
    "saas": "SaaS",
    "llp": "LLP",
    "gmbh": "GmbH",
    "sre": "SRE",
    "ap": "AP",
    "vp": "VP",
    "pr": "PR",
    "bi": "BI",
    "iam": "IAM",
    "erp": "ERP",
    "gl": "GL",
    "finops": "FinOps",
    "eurorack": "EuroRack",
    "uk": "UK",
    "usa": "USA",
    "co": "Co",
    "inc": "Inc",
    "ltd": "Ltd",
    "unk": "UNK",
}

MONTH_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
SLASH_YMD_RE = re.compile(r"^(\d{4})/(\d{2})/(\d{2})$")
SLASH_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
TEXT_DMY_RE = re.compile(
    r"^(\d{1,2})-(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-(\d{4})$",
    re.IGNORECASE,
)
TEXT_MDY_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s*(\d{4})$",
    re.IGNORECASE,
)


def q2(val: Decimal) -> Decimal:
    return val.quantize(TWO_DP, rounding=ROUND_HALF_UP)


def q4(val: Decimal) -> Decimal:
    return val.quantize(FOUR_DP, rounding=ROUND_HALF_UP)


def _clean_spaces(val: str) -> str:
    return " ".join(val.strip().split())


def _canonicalize_hyphen_word(part: str) -> str:
    low = part.lower()
    if low in SPECIAL_TOKENS:
        return SPECIAL_TOKENS[low]
    if not part:
        return part
    return part[0].upper() + part[1:].lower()


def _canonicalize_label(val: str) -> str:
    cleaned = _clean_spaces(val)
    if not cleaned:
        return ""
    tokens: list[str] = []
    for word in cleaned.split(" "):
        if word == "&":
            tokens.append("&")
            continue
        if "-" in word:
            tokens.append("-".join(_canonicalize_hyphen_word(p) for p in word.split("-")))
        else:
            tokens.append(_canonicalize_hyphen_word(word))
    return " ".join(tokens)


def _parse_date_us_precedence(raw: str) -> date:
    s = _clean_spaces(raw)
    if m := ISO_RE.match(s):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := SLASH_YMD_RE.match(s):
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := SLASH_MDY_RE.match(s):
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    if m := TEXT_DMY_RE.match(s):
        return date(int(m.group(3)), MONTH_MAP[m.group(2).lower()], int(m.group(1)))
    if m := TEXT_MDY_RE.match(s):
        return date(int(m.group(3)), MONTH_MAP[m.group(1).lower()], int(m.group(2)))
    return datetime.fromisoformat(s).date()


def _parse_decimal_2dp(raw: str) -> Decimal:
    val, _ = parse_numeric_value(raw)
    if val is None:
        raise ValueError(f"Unable to parse numeric value: {raw!r}")
    return q2(val)


def _parse_rate_4dp(raw: str) -> Decimal:
    cleaned = _clean_spaces(raw)
    if cleaned.endswith("%"):
        val, _ = parse_numeric_value(cleaned[:-1])
        if val is None:
            raise ValueError(f"Unable to parse percentage rate: {raw!r}")
        return q4(val / Decimal("100"))
    val, _ = parse_numeric_value(cleaned)
    if val is None:
        raise ValueError(f"Unable to parse rate value: {raw!r}")
    return q4(val)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        headers = [h for h in (reader.fieldnames or []) if h is not None]
        rows = [{h: (row.get(h) or "") for h in headers} for row in reader]
    return headers, rows


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _normalize_fk(raw_key: str, valid_keys: set[str]) -> str:
    cleaned = _clean_spaces(raw_key)
    if cleaned.lower() in NULL_SENTINELS or cleaned not in valid_keys:
        return "-1"
    return cleaned


def _stage_dimensions(raw_dir: Path, staged_dir: Path) -> dict[str, set[str]]:
    # 1. DimDate
    date_headers, date_rows = _read_csv(raw_dir / "DimDate.csv")
    staged_date: list[dict[str, object]] = []
    valid_date_keys: set[str] = set()
    for row in date_rows:
        dt_iso = _parse_date_us_precedence(row["Date"]).isoformat()
        ms_iso = _parse_date_us_precedence(row["MonthStart"]).isoformat()
        dk = _clean_spaces(row["DateKey"])
        valid_date_keys.add(dk)
        staged_date.append(
            {
                "DateKey": dk,
                "Date": dt_iso,
                "Year": _clean_spaces(row["Year"]),
                "Quarter": _clean_spaces(row["Quarter"]),
                "QuarterNumber": _clean_spaces(row["QuarterNumber"]),
                "MonthNumber": _clean_spaces(row["MonthNumber"]),
                "MonthName": _clean_spaces(row["MonthName"]),
                "YearMonth": _clean_spaces(row["YearMonth"]),
                "YearMonthSort": _clean_spaces(row["YearMonthSort"]),
                "DayOfWeek": _clean_spaces(row["DayOfWeek"]),
                "IsWeekend": _clean_spaces(row["IsWeekend"]),
                "MonthStart": ms_iso,
            }
        )
    _write_csv(staged_dir / "DimDate.csv", date_headers, staged_date)

    # 2. DimVendor
    vendor_headers, vendor_rows = _read_csv(raw_dir / "DimVendor.csv")
    staged_vendor: list[dict[str, object]] = []
    valid_vendor_keys: set[str] = set()
    for row in vendor_rows:
        vk = _clean_spaces(row["VendorKey"])
        valid_vendor_keys.add(vk)
        staged_vendor.append(
            {
                "VendorKey": vk,
                "VendorCode": _clean_spaces(row["VendorCode"]),
                "VendorName": _canonicalize_label(row["VendorName"]),
                "VendorCategory": _canonicalize_label(row["VendorCategory"]),
                "PaymentTerms": _clean_spaces(row["PaymentTerms"]),
                "Country": _clean_spaces(row["Country"]),
                "VendorTier": _canonicalize_label(row["VendorTier"]),
            }
        )
    _write_csv(staged_dir / "DimVendor.csv", vendor_headers, staged_vendor)

    # 3. DimCostCenter
    cc_headers, cc_rows = _read_csv(raw_dir / "DimCostCenter.csv")
    staged_cc: list[dict[str, object]] = []
    valid_cc_keys: set[str] = set()
    for row in cc_rows:
        cck = _clean_spaces(row["CostCenterKey"])
        valid_cc_keys.add(cck)
        staged_cc.append(
            {
                "CostCenterKey": cck,
                "CostCenterCode": _clean_spaces(row["CostCenterCode"]),
                "CostCenterName": _canonicalize_label(row["CostCenterName"]),
                "BusinessUnit": _clean_spaces(row["BusinessUnit"]),
                "Department": _canonicalize_label(row["Department"]),
                "EntityCode": _clean_spaces(row["EntityCode"]),
            }
        )
    _write_csv(staged_dir / "DimCostCenter.csv", cc_headers, staged_cc)

    # 4. DimGLAccount
    gl_headers, gl_rows = _read_csv(raw_dir / "DimGLAccount.csv")
    staged_gl: list[dict[str, object]] = []
    valid_gl_keys: set[str] = set()
    for row in gl_rows:
        glk = _clean_spaces(row["GLAccountKey"])
        valid_gl_keys.add(glk)
        staged_gl.append(
            {
                "GLAccountKey": glk,
                "GLAccountCode": _clean_spaces(row["GLAccountCode"]),
                "GLAccountName": _canonicalize_label(row["GLAccountName"]),
                "ExpenseGroup": _canonicalize_label(row["ExpenseGroup"]),
                "AccountType": _canonicalize_label(row["AccountType"]),
            }
        )
    _write_csv(staged_dir / "DimGLAccount.csv", gl_headers, staged_gl)

    # 5. DimApprover
    app_headers, app_rows = _read_csv(raw_dir / "DimApprover.csv")
    staged_app: list[dict[str, object]] = []
    valid_app_keys: set[str] = set()
    for row in app_rows:
        ak = _clean_spaces(row["ApproverKey"])
        valid_app_keys.add(ak)
        staged_app.append(
            {
                "ApproverKey": ak,
                "ApproverCode": _clean_spaces(row["ApproverCode"]),
                "ApproverName": _canonicalize_label(row["ApproverName"]),
                "RoleTitle": _canonicalize_label(row["RoleTitle"]),
                "BusinessUnit": _clean_spaces(row["BusinessUnit"]),
                "UserPrincipalName": _clean_spaces(row["UserPrincipalName"]).lower(),
            }
        )
    _write_csv(staged_dir / "DimApprover.csv", app_headers, staged_app)

    return {
        "DimDate": valid_date_keys,
        "DimVendor": valid_vendor_keys,
        "DimCostCenter": valid_cc_keys,
        "DimGLAccount": valid_gl_keys,
        "DimApprover": valid_app_keys,
    }


def _compute_expected_metrics(staged_dir: Path) -> dict[str, Any]:
    """Compute independent Decimal ground truth strictly from staged/*.csv files."""
    _, date_rows = _read_csv(staged_dir / "DimDate.csv")
    _, cc_rows = _read_csv(staged_dir / "DimCostCenter.csv")
    _, app_rows = _read_csv(staged_dir / "DimApprover.csv")
    _, inv_rows = _read_csv(staged_dir / "FactAPInvoices.csv")
    _, budget_rows = _read_csv(staged_dir / "FactAPBudget.csv")

    year_by_date_key = {r["DateKey"]: r["Year"] for r in date_rows}
    bu_by_cc_key = {r["CostCenterKey"]: r["BusinessUnit"] for r in cc_rows}
    upn_by_app_key = {r["ApproverKey"]: r["UserPrincipalName"] for r in app_rows}

    total_net = Decimal("0.00")
    total_line = Decimal("0.00")
    total_tax = Decimal("0.00")
    gross_invoice_spend = Decimal("0.00")
    credit_memo_amount = Decimal("0.00")
    overdue_exposure = Decimal("0.00")
    paid_count = 0
    paid_on_time_count = 0
    three_way_count = 0
    exception_count = 0
    distinct_invoices: set[str] = set()

    by_year_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "row_count": 0,
            "net_ap_spend": Decimal("0.00"),
            "gross_invoice_spend": Decimal("0.00"),
            "credit_memo_amount": Decimal("0.00"),
            "budget_spend": Decimal("0.00"),
        }
    )
    by_bu_acc: dict[str, dict[str, Any]] = {
        bu: {
            "row_count": 0,
            "budget_row_count": 0,
            "net_ap_spend": Decimal("0.00"),
            "gross_invoice_spend": Decimal("0.00"),
            "credit_memo_amount": Decimal("0.00"),
            "budget_spend": Decimal("0.00"),
        }
        for bu in sorted({r["BusinessUnit"] for r in cc_rows})
    }
    by_upn_acc: dict[str, dict[str, Any]] = {
        r["UserPrincipalName"]: {
            "row_count": 0,
            "budget_row_count": 0,
            "net_ap_spend": Decimal("0.00"),
        }
        for r in app_rows
        if r["UserPrincipalName"]
    }
    by_upn_acc["unauthorized.user@finco.example"] = {
        "row_count": 0,
        "budget_row_count": 0,
        "net_ap_spend": Decimal("0.00"),
    }

    unknown_buckets: dict[str, dict[str, Any]] = {
        "CostCenterKey_-1": {"row_count": 0, "net_ap_spend": Decimal("0.00")},
        "ApproverKey_-1": {"row_count": 0, "net_ap_spend": Decimal("0.00")},
        "VendorKey_-1": {"row_count": 0, "net_ap_spend": Decimal("0.00")},
        "GLAccountKey_-1": {"row_count": 0, "net_ap_spend": Decimal("0.00")},
    }

    for row in inv_rows:
        distinct_invoices.add(row["InvoiceID"])
        line_dec = Decimal(row["LineAmount"])
        tax_dec = Decimal(row["TaxAmount"])
        net_dec = Decimal(row["NetAmount"])

        total_line += line_dec
        total_tax += tax_dec
        total_net += net_dec

        is_credit = row["DocumentType"] == "Credit Memo"
        if is_credit:
            credit_memo_amount += net_dec
        else:
            gross_invoice_spend += net_dec

        if row["PaymentStatus"] == "Overdue":
            overdue_exposure += net_dec
        if row["PaymentStatus"] == "Paid":
            paid_count += 1
            if int(row["IsPaidOnTime"]) == 1:
                paid_on_time_count += 1
        if row["MatchStatus"] == "3-Way Matched":
            three_way_count += 1
        elif row["MatchStatus"] == "Exception":
            exception_count += 1

        yr = year_by_date_key[row["PostingDateKey"]]
        by_year_acc[yr]["row_count"] += 1
        by_year_acc[yr]["net_ap_spend"] += net_dec
        if is_credit:
            by_year_acc[yr]["credit_memo_amount"] += net_dec
        else:
            by_year_acc[yr]["gross_invoice_spend"] += net_dec

        bu = bu_by_cc_key[row["CostCenterKey"]]
        by_bu_acc[bu]["row_count"] += 1
        by_bu_acc[bu]["net_ap_spend"] += net_dec
        if is_credit:
            by_bu_acc[bu]["credit_memo_amount"] += net_dec
        else:
            by_bu_acc[bu]["gross_invoice_spend"] += net_dec

        upn = upn_by_app_key[row["ApproverKey"]]
        if upn:
            by_upn_acc[upn]["row_count"] += 1
            by_upn_acc[upn]["net_ap_spend"] += net_dec

        if row["CostCenterKey"] == "-1":
            unknown_buckets["CostCenterKey_-1"]["row_count"] += 1
            unknown_buckets["CostCenterKey_-1"]["net_ap_spend"] += net_dec
        if row["ApproverKey"] == "-1":
            unknown_buckets["ApproverKey_-1"]["row_count"] += 1
            unknown_buckets["ApproverKey_-1"]["net_ap_spend"] += net_dec
        if row["VendorKey"] == "-1":
            unknown_buckets["VendorKey_-1"]["row_count"] += 1
            unknown_buckets["VendorKey_-1"]["net_ap_spend"] += net_dec
        if row["GLAccountKey"] == "-1":
            unknown_buckets["GLAccountKey_-1"]["row_count"] += 1
            unknown_buckets["GLAccountKey_-1"]["net_ap_spend"] += net_dec

    total_budget = Decimal("0.00")
    for brow in budget_rows:
        b_dec = Decimal(brow["BudgetAmount"])
        total_budget += b_dec
        yr = year_by_date_key[brow["PostingDateKey"]]
        by_year_acc[yr]["budget_spend"] += b_dec
        bu = bu_by_cc_key[brow["CostCenterKey"]]
        by_bu_acc[bu]["budget_row_count"] += 1
        by_bu_acc[bu]["budget_spend"] += b_dec

    total_rows = len(inv_rows)
    distinct_inv_count = len(distinct_invoices)
    budget_variance = total_budget - total_net

    overall = {
        "invoice_line_count": total_rows,
        "budget_row_count": len(budget_rows),
        "distinct_invoices": distinct_inv_count,
        "gross_invoice_spend": float(q2(gross_invoice_spend)),
        "credit_memo_amount": float(q2(credit_memo_amount)),
        "net_ap_spend": float(q2(total_net)),
        "pre_tax_spend": float(q2(total_line)),
        "tax_spend": float(q2(total_tax)),
        "average_invoice_value": float(q2(total_net / Decimal(distinct_inv_count))),
        "credit_memo_rate_pct": round(float(abs(credit_memo_amount) / gross_invoice_spend), 6),
        "budget_spend": float(q2(total_budget)),
        "budget_variance": float(q2(budget_variance)),
        "budget_variance_pct": round(float(budget_variance / total_budget), 6),
        "on_time_payment_pct": round(paid_on_time_count / paid_count, 6),
        "overdue_exposure": float(q2(overdue_exposure)),
        "three_way_match_pct": round(three_way_count / total_rows, 6),
        "exception_rate_pct": round(exception_count / total_rows, 6),
    }

    by_year_out: dict[str, dict[str, Any]] = {}
    sorted_years = sorted(by_year_acc.keys())
    for i, yr in enumerate(sorted_years):
        entry = by_year_acc[yr]
        rec: dict[str, Any] = {
            "row_count": entry["row_count"],
            "net_ap_spend": float(q2(entry["net_ap_spend"])),
            "gross_invoice_spend": float(q2(entry["gross_invoice_spend"])),
            "credit_memo_amount": float(q2(entry["credit_memo_amount"])),
            "budget_spend": float(q2(entry["budget_spend"])),
        }
        if i > 0:
            prev_spend = by_year_acc[sorted_years[i - 1]]["net_ap_spend"]
            rec["net_spend_py"] = float(q2(prev_spend))
            rec["net_spend_yoy"] = float(q2(entry["net_ap_spend"] - prev_spend))
        by_year_out[yr] = rec

    by_bu_out: dict[str, dict[str, Any]] = {}
    for bu, entry in sorted(by_bu_acc.items()):
        bu_var = entry["budget_spend"] - entry["net_ap_spend"]
        by_bu_out[bu] = {
            "row_count": entry["row_count"],
            "budget_row_count": entry["budget_row_count"],
            "net_ap_spend": float(q2(entry["net_ap_spend"])),
            "gross_invoice_spend": float(q2(entry["gross_invoice_spend"])),
            "credit_memo_amount": float(q2(entry["credit_memo_amount"])),
            "budget_spend": float(q2(entry["budget_spend"])),
            "budget_variance": float(q2(bu_var)),
        }

    by_upn_out = {
        upn: {
            "row_count": entry["row_count"],
            "budget_row_count": entry["budget_row_count"],
            "net_ap_spend": float(q2(entry["net_ap_spend"])),
        }
        for upn, entry in sorted(by_upn_acc.items())
    }

    unknown_out = {
        key: {
            "row_count": entry["row_count"],
            "net_ap_spend": float(q2(entry["net_ap_spend"])),
        }
        for key, entry in unknown_buckets.items()
    }

    return {
        "schema_version": "2.0",
        "domain": "corporate_finance_ap_spend",
        "overall": overall,
        "by_year": by_year_out,
        "by_business_unit": by_bu_out,
        "by_approver_upn": by_upn_out,
        "unknown_buckets": unknown_out,
        "finance_guards": {
            "expected_no_actuals_status": "No Actuals",
            "expected_no_budget_status": "No Budget",
        },
    }


def stage_finance_ap_dataset(base_dir: Path) -> dict[str, Any]:
    """Stage messy Finance/AP raw files cleanly and compute Decimal expected_metrics.json."""
    raw_dir = base_dir / "raw"
    staged_dir = base_dir / "staged"
    quarantine_dir = base_dir / "quarantine"
    gt_dir = base_dir / "ground_truth"

    valid_keys = _stage_dimensions(raw_dir, staged_dir)

    raw_inv_headers, raw_inv_rows = _read_csv(raw_dir / "FactAPInvoices.csv")
    seen_full: set[tuple[str, ...]] = set()
    seen_bkeys: set[tuple[str, str]] = set()
    staged_invoices: list[dict[str, object]] = []
    quarantine_invoices: list[dict[str, object]] = []

    for row in raw_inv_rows:
        row_tuple = tuple(row[col] for col in raw_inv_headers)
        inv_id = _clean_spaces(row["InvoiceID"])
        line_no = _clean_spaces(row["LineNumber"])
        bkey = (inv_id, line_no)

        if row_tuple in seen_full:
            q_row: dict[str, object] = dict(row)
            q_row["QuarantineReason"] = "exact_duplicate_row"
            quarantine_invoices.append(q_row)
            continue
        if bkey in seen_bkeys:
            q_row = dict(row)
            q_row["QuarantineReason"] = "duplicate_business_key"
            quarantine_invoices.append(q_row)
            continue

        seen_full.add(row_tuple)
        seen_bkeys.add(bkey)

        post_dt = _parse_date_us_precedence(row["PostingDate"])
        due_dt = _parse_date_us_precedence(row["DueDate"])
        if due_dt < post_dt:
            raise ValueError(f"DueDate {due_dt} precedes PostingDate {post_dt} on {bkey}")
        post_key = post_dt.strftime("%Y%m%d")
        if post_key not in valid_keys["DimDate"]:
            raise ValueError(f"PostingDateKey {post_key} missing from DimDate")

        line_dec = _parse_decimal_2dp(row["LineAmount"])
        tax_dec = _parse_decimal_2dp(row["TaxAmount"])
        net_dec = _parse_decimal_2dp(row["NetAmount"])
        unit_price_dec = _parse_decimal_2dp(row["UnitPrice"])
        disc_rate_dec = _parse_rate_4dp(row["EarlyPayDiscountRate"])

        staged_invoices.append(
            {
                "InvoiceID": inv_id,
                "LineNumber": int(line_no),
                "PostingDateKey": int(post_key),
                "PostingDate": post_dt.isoformat(),
                "DueDate": due_dt.isoformat(),
                "VendorKey": _normalize_fk(row["VendorKey"], valid_keys["DimVendor"]),
                "CostCenterKey": _normalize_fk(row["CostCenterKey"], valid_keys["DimCostCenter"]),
                "GLAccountKey": _normalize_fk(row["GLAccountKey"], valid_keys["DimGLAccount"]),
                "ApproverKey": _normalize_fk(row["ApproverKey"], valid_keys["DimApprover"]),
                "DocumentType": _canonicalize_label(row["DocumentType"]),
                "PaymentStatus": _canonicalize_label(row["PaymentStatus"]),
                "MatchStatus": _canonicalize_label(row["MatchStatus"]),
                "Quantity": int(_clean_spaces(row["Quantity"])),
                "UnitPrice": f"{unit_price_dec:.2f}",
                "LineAmount": f"{line_dec:.2f}",
                "TaxAmount": f"{tax_dec:.2f}",
                "NetAmount": f"{net_dec:.2f}",
                "EarlyPayDiscountRate": f"{disc_rate_dec:.4f}",
                "DaysToPay": int(_clean_spaces(row["DaysToPay"])),
                "IsPaidOnTime": int(_clean_spaces(row["IsPaidOnTime"])),
            }
        )

    staged_inv_cols = [
        "InvoiceID",
        "LineNumber",
        "PostingDateKey",
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
    _write_csv(staged_dir / "FactAPInvoices.csv", staged_inv_cols, staged_invoices)
    _write_csv(
        quarantine_dir / "FactAPInvoices_quarantine.csv",
        [*raw_inv_headers, "QuarantineReason"],
        quarantine_invoices,
    )

    _, raw_budget_rows = _read_csv(raw_dir / "FactAPBudget.csv")
    staged_budget: list[dict[str, object]] = []
    for brow in raw_budget_rows:
        post_dt = _parse_date_us_precedence(brow["PostingDate"])
        post_key = post_dt.strftime("%Y%m%d")
        amt_dec = _parse_decimal_2dp(brow["BudgetAmount"])
        staged_budget.append(
            {
                "PostingDate": post_dt.isoformat(),
                "PostingDateKey": int(post_key),
                "CostCenterKey": _normalize_fk(brow["CostCenterKey"], valid_keys["DimCostCenter"]),
                "GLAccountKey": _normalize_fk(brow["GLAccountKey"], valid_keys["DimGLAccount"]),
                "BudgetAmount": f"{amt_dec:.2f}",
            }
        )
    _write_csv(
        staged_dir / "FactAPBudget.csv",
        ["PostingDate", "PostingDateKey", "CostCenterKey", "GLAccountKey", "BudgetAmount"],
        staged_budget,
    )

    expected = _compute_expected_metrics(staged_dir)
    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / "expected_metrics.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stage messy Finance/AP files and generate Decimal expected_metrics.json."
    )
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("data/finance_ap"),
        help="Base dataset folder containing raw/.",
    )
    args = parser.parse_args()
    metrics = stage_finance_ap_dataset(args.base_dir)
    print(
        json.dumps(
            {
                "base_dir": str(args.base_dir),
                "overall": metrics["overall"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
