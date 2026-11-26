#!/usr/bin/env python3
"""Generate deterministic synthetic Enterprise Retail & Regional Margin CSVs and ground truth."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 42
SALES_ROWS = 25_000
START_DATE = date(2024, 1, 1)
END_DATE = date(2026, 12, 31)

TWO_DP = Decimal("0.01")
FOUR_DP = Decimal("0.0001")


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


def build_dim_date() -> tuple[list[dict[str, object]], dict[int, list[int]]]:
    rows: list[dict[str, object]] = []
    dates_by_year: dict[int, list[int]] = defaultdict(list)
    cur = START_DATE
    while cur <= END_DATE:
        date_key = int(cur.strftime("%Y%m%d"))
        quarter_num = ((cur.month - 1) // 3) + 1
        rows.append(
            {
                "DateKey": date_key,
                "Date": cur.isoformat(),
                "Year": cur.year,
                "Quarter": f"Q{quarter_num}",
                "QuarterNumber": quarter_num,
                "MonthNumber": cur.month,
                "MonthName": cur.strftime("%B"),
                "YearMonth": cur.strftime("%Y-%m"),
                "YearMonthSort": cur.year * 100 + cur.month,
                "DayOfWeek": cur.strftime("%A"),
                "IsWeekend": 1 if cur.weekday() >= 5 else 0,
            }
        )
        dates_by_year[cur.year].append(date_key)
        cur += timedelta(days=1)
    return rows, dates_by_year


def build_dim_product(rng: random.Random) -> list[dict[str, object]]:
    catalog = [
        ("Cloud Hardware", "Edge Gateways"),
        ("Cloud Hardware", "Smart POS"),
        ("Enterprise Software", "Analytics Suites"),
        ("Enterprise Software", "Security Licenses"),
        ("Connected Devices", "Wearables"),
        ("Connected Devices", "Tablets"),
        ("Accessories", "Docks & Cables"),
        ("Accessories", "Audio Peripherals"),
        ("Managed Services", "Support Plans"),
        ("Managed Services", "Onboarding Packs"),
    ]
    rows: list[dict[str, object]] = []
    for idx in range(1, 51):
        cat, subcat = catalog[(idx - 1) % len(catalog)]
        base_price = Decimal(rng.randint(35, 850)) + Decimal("0.99")
        margin_ratio = Decimal(str(round(rng.uniform(0.52, 0.72), 2)))
        unit_cost = q2(base_price * margin_ratio)
        rows.append(
            {
                "ProductKey": idx,
                "ProductSKU": f"SKU-{idx:04d}",
                "ProductName": f"{subcat} Model {idx:02d}",
                "Category": cat,
                "Subcategory": subcat,
                "UnitPrice": f"{base_price:.2f}",
                "UnitCost": f"{unit_cost:.2f}",
            }
        )
    return rows


def build_dim_store() -> list[dict[str, object]]:
    store_specs = [
        ("NA", "USA", "Flagship", "New York Midtown"),
        ("NA", "USA", "Flagship", "San Francisco Embarcadero"),
        ("NA", "USA", "Metro", "Chicago Loop"),
        ("NA", "USA", "Metro", "Austin Domain"),
        ("NA", "Canada", "Metro", "Toronto Eaton"),
        ("NA", "USA", "Digital", "NA Enterprise Direct"),
        ("EMEA", "UK", "Flagship", "London Regent"),
        ("EMEA", "Germany", "Flagship", "Berlin Mitte"),
        ("EMEA", "France", "Metro", "Paris Opera"),
        ("EMEA", "Netherlands", "Metro", "Amsterdam Zuid"),
        ("EMEA", "UAE", "Flagship", "Dubai DIFC"),
        ("EMEA", "UK", "Digital", "EMEA Enterprise Direct"),
        ("APAC", "Singapore", "Flagship", "Singapore Marina"),
        ("APAC", "Japan", "Flagship", "Tokyo Ginza"),
        ("APAC", "Australia", "Metro", "Sydney George St"),
        ("APAC", "India", "Metro", "Bengaluru Indiranagar"),
        ("APAC", "South Korea", "Metro", "Seoul Gangnam"),
        ("APAC", "Singapore", "Digital", "APAC Enterprise Direct"),
    ]
    rows: list[dict[str, object]] = []
    for idx, (region, country, channel, name) in enumerate(store_specs, start=1):
        rows.append(
            {
                "StoreKey": idx,
                "StoreCode": f"STR-{region}-{idx:02d}",
                "StoreName": name,
                "Region": region,
                "Country": country,
                "Channel": channel,
            }
        )
    return rows


def build_dim_customer(rng: random.Random) -> list[dict[str, object]]:
    segments = ["Enterprise", "Mid-Market", "SMB"]
    regions = ["NA", "EMEA", "APAC"]
    rows: list[dict[str, object]] = []
    for idx in range(1, 301):
        rows.append(
            {
                "CustomerKey": idx,
                "CustomerCode": f"CUST-{idx:04d}",
                "CustomerName": f"Contoso Account {idx:03d}",
                "Segment": segments[(idx - 1) % len(segments)],
                "Region": regions[rng.randrange(len(regions))],
            }
        )
    return rows


def build_dim_sales_rep() -> list[dict[str, object]]:
    reps = [
        ("Alice Martin", "EMEA", "alice.emea@contoso.com", "vp.emea@contoso.com"),
        ("Lukas Weber", "EMEA", "lukas.emea@contoso.com", "vp.emea@contoso.com"),
        ("Chloe Dubois", "EMEA", "chloe.emea@contoso.com", "vp.emea@contoso.com"),
        ("Tariq Al-Mansoor", "EMEA", "tariq.emea@contoso.com", "vp.emea@contoso.com"),
        ("Bob Johnson", "NA", "bob.na@contoso.com", "vp.na@contoso.com"),
        ("Maya Patel", "NA", "maya.na@contoso.com", "vp.na@contoso.com"),
        ("Liam O'Connor", "NA", "liam.na@contoso.com", "vp.na@contoso.com"),
        ("Sofia Reyes", "NA", "sofia.na@contoso.com", "vp.na@contoso.com"),
        ("Carol Tan", "APAC", "carol.apac@contoso.com", "vp.apac@contoso.com"),
        ("Kenji Sato", "APAC", "kenji.apac@contoso.com", "vp.apac@contoso.com"),
        ("Aarav Sharma", "APAC", "aarav.apac@contoso.com", "vp.apac@contoso.com"),
        ("Min-Ji Park", "APAC", "minji.apac@contoso.com", "vp.apac@contoso.com"),
    ]
    rows: list[dict[str, object]] = []
    for idx, (name, region, upn, mgr) in enumerate(reps, start=1):
        rows.append(
            {
                "SalesRepKey": idx,
                "RepName": name,
                "Region": region,
                "UserPrincipalName": upn,
                "ManagerUPN": mgr,
            }
        )
    return rows


def main() -> None:
    rng = random.Random(SEED)
    repo_root = Path(__file__).resolve().parents[1]
    raw_dir = repo_root / "data" / "raw"
    gt_path = repo_root / "data" / "ground_truth" / "expected_metrics.json"
    gt_path.parent.mkdir(parents=True, exist_ok=True)

    dim_date, dates_by_year = build_dim_date()
    dim_product = build_dim_product(rng)
    dim_store = build_dim_store()
    dim_customer = build_dim_customer(rng)
    dim_sales_rep = build_dim_sales_rep()

    store_region = {int(r["StoreKey"]): str(r["Region"]) for r in dim_store}
    reps_by_region: dict[str, list[dict[str, object]]] = defaultdict(list)
    rep_upn_by_key: dict[int, str] = {}
    for rep in dim_sales_rep:
        reps_by_region[str(rep["Region"])].append(rep)
        rep_upn_by_key[int(rep["SalesRepKey"])] = str(rep["UserPrincipalName"])

    product_by_key = {
        int(p["ProductKey"]): (Decimal(str(p["UnitPrice"])), Decimal(str(p["UnitCost"])))
        for p in dim_product
    }
    customers_by_region: dict[str, list[int]] = defaultdict(list)
    for cust in dim_customer:
        customers_by_region[str(cust["Region"])].append(int(cust["CustomerKey"]))

    year_weights = [(2024, 7_000), (2025, 8_500), (2026, 9_500)]
    discount_choices = [
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.05"),
        Decimal("0.10"),
        Decimal("0.15"),
        Decimal("0.20"),
    ]

    fact_sales: list[dict[str, object]] = []
    # Independent accumulators for ground truth
    overall_rev = Decimal("0.00")
    overall_cogs = Decimal("0.00")
    overall_disc = Decimal("0.00")
    overall_gross = Decimal("0.00")
    overall_units = 0
    order_ids: set[str] = set()
    customer_keys: set[int] = set()

    year_stats: dict[str, dict[str, Decimal | int]] = {
        str(y): {"revenue": Decimal("0.00"), "cogs": Decimal("0.00")} for y in (2024, 2025, 2026)
    }
    region_stats: dict[str, dict[str, Decimal | int]] = {
        r: {"rows": 0, "revenue": Decimal("0.00"), "cogs": Decimal("0.00")}
        for r in ("NA", "EMEA", "APAC")
    }
    rep_stats: dict[str, dict[str, Decimal | int]] = {
        str(rep["UserPrincipalName"]): {
            "rows": 0,
            "revenue": Decimal("0.00"),
            "cogs": Decimal("0.00"),
        }
        for rep in dim_sales_rep
    }

    row_counter = 0
    for year, count in year_weights:
        year_dates = dates_by_year[year]
        for _ in range(count):
            row_counter += 1
            date_key = rng.choice(year_dates)
            store = rng.choice(dim_store)
            store_key = int(store["StoreKey"])
            region = store_region[store_key]
            rep = rng.choice(reps_by_region[region])
            rep_key = int(rep["SalesRepKey"])
            rep_upn = rep_upn_by_key[rep_key]
            product_key = rng.randint(1, len(dim_product))
            unit_price, unit_cost = product_by_key[product_key]
            cust_pool = customers_by_region[region] or [1]
            customer_key = rng.choice(cust_pool)

            qty = rng.randint(1, 8)
            disc_rate = rng.choice(discount_choices)
            gross_amt = q2(unit_price * qty)
            disc_amt = q2(gross_amt * disc_rate)
            net_rev = q2(gross_amt - disc_amt)
            cogs = q2(unit_cost * qty)
            order_id = f"SO-{year}-{(row_counter + 1) // 2:06d}"
            line_num = 1 if (row_counter % 2 == 1) else 2
            order_ids.add(order_id)
            customer_keys.add(customer_key)

            fact_sales.append(
                {
                    "SalesOrderID": order_id,
                    "OrderLineNumber": line_num,
                    "DateKey": date_key,
                    "ProductKey": product_key,
                    "StoreKey": store_key,
                    "CustomerKey": customer_key,
                    "SalesRepKey": rep_key,
                    "Quantity": qty,
                    "UnitPrice": f"{unit_price:.2f}",
                    "UnitCost": f"{unit_cost:.2f}",
                    "GrossAmount": f"{gross_amt:.2f}",
                    "DiscountRate": f"{disc_rate:.2f}",
                    "DiscountAmount": f"{disc_amt:.2f}",
                    "NetRevenue": f"{net_rev:.2f}",
                    "COGS": f"{cogs:.2f}",
                }
            )

            overall_rev += net_rev
            overall_cogs += cogs
            overall_disc += disc_amt
            overall_gross += gross_amt
            overall_units += qty

            y_acc = year_stats[str(year)]
            y_acc["revenue"] = Decimal(y_acc["revenue"]) + net_rev
            y_acc["cogs"] = Decimal(y_acc["cogs"]) + cogs

            r_acc = region_stats[region]
            r_acc["rows"] = int(r_acc["rows"]) + 1
            r_acc["revenue"] = Decimal(r_acc["revenue"]) + net_rev
            r_acc["cogs"] = Decimal(r_acc["cogs"]) + cogs

            u_acc = rep_stats[rep_upn]
            u_acc["rows"] = int(u_acc["rows"]) + 1
            u_acc["revenue"] = Decimal(u_acc["revenue"]) + net_rev
            u_acc["cogs"] = Decimal(u_acc["cogs"]) + cogs

    # FactBudget at pure monthly StoreKey grain (36 months * 18 stores = 648 rows)
    actual_store_month: dict[tuple[int, int], tuple[Decimal, Decimal]] = defaultdict(
        lambda: (Decimal("0.00"), Decimal("0.00"))
    )
    for row in fact_sales:
        dk = int(row["DateKey"])
        month_dk = (dk // 100) * 100 + 1
        sk = int(row["StoreKey"])
        prev_r, prev_c = actual_store_month[(month_dk, sk)]
        actual_store_month[(month_dk, sk)] = (
            prev_r + Decimal(str(row["NetRevenue"])),
            prev_c + Decimal(str(row["COGS"])),
        )

    fact_budget: list[dict[str, object]] = []
    total_budget = Decimal("0.00")
    budget_by_year: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    budget_by_region: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

    for year in (2024, 2025, 2026):
        for month in range(1, 13):
            month_dk = year * 10000 + month * 100 + 1
            for store in dim_store:
                sk = int(store["StoreKey"])
                region = store_region[sk]
                act_rev, act_cogs = actual_store_month[(month_dk, sk)]
                factor = Decimal(str(round(rng.uniform(0.95, 1.08), 4)))
                b_rev = q2((act_rev if act_rev > 0 else Decimal("25000.00")) * factor)
                b_gm = q2(b_rev - (act_cogs if act_cogs > 0 else Decimal("15000.00")))
                fact_budget.append(
                    {
                        "DateKey": month_dk,
                        "StoreKey": sk,
                        "BudgetRevenue": f"{b_rev:.2f}",
                        "BudgetGrossMargin": f"{b_gm:.2f}",
                    }
                )
                total_budget += b_rev
                budget_by_year[str(year)] += b_rev
                budget_by_region[region] += b_rev

    checksums = {
        "DimDate.csv": write_csv(
            raw_dir / "DimDate.csv", list(dim_date[0].keys()), dim_date
        ),
        "DimProduct.csv": write_csv(
            raw_dir / "DimProduct.csv", list(dim_product[0].keys()), dim_product
        ),
        "DimStore.csv": write_csv(
            raw_dir / "DimStore.csv", list(dim_store[0].keys()), dim_store
        ),
        "DimCustomer.csv": write_csv(
            raw_dir / "DimCustomer.csv", list(dim_customer[0].keys()), dim_customer
        ),
        "DimSalesRep.csv": write_csv(
            raw_dir / "DimSalesRep.csv", list(dim_sales_rep[0].keys()), dim_sales_rep
        ),
        "FactSales.csv": write_csv(
            raw_dir / "FactSales.csv", list(fact_sales[0].keys()), fact_sales
        ),
        "FactBudget.csv": write_csv(
            raw_dir / "FactBudget.csv", list(fact_budget[0].keys()), fact_budget
        ),
    }

    overall_gm = overall_rev - overall_cogs
    budget_var = overall_rev - total_budget

    by_year_out: dict[str, dict[str, float]] = {}
    for y_int in (2024, 2025, 2026):
        y_key = str(y_int)
        rev = Decimal(year_stats[y_key]["revenue"])
        cogs = Decimal(year_stats[y_key]["cogs"])
        entry: dict[str, float] = {
            "total_revenue": float(q2(rev)),
            "gross_margin": float(q2(rev - cogs)),
            "total_budget": float(q2(budget_by_year[y_key])),
        }
        if y_int > 2024:
            py_rev = Decimal(year_stats[str(y_int - 1)]["revenue"])
            yoy = rev - py_rev
            entry["revenue_py"] = float(q2(py_rev))
            entry["revenue_yoy"] = float(q2(yoy))
            entry["revenue_yoy_pct"] = float(q4(yoy / py_rev))
        by_year_out[y_key] = entry

    by_region_out: dict[str, dict[str, float | int]] = {}
    for region in ("NA", "EMEA", "APAC"):
        r_rev = Decimal(region_stats[region]["revenue"])
        r_cogs = Decimal(region_stats[region]["cogs"])
        r_gm = r_rev - r_cogs
        by_region_out[region] = {
            "row_count": int(region_stats[region]["rows"]),
            "total_revenue": float(q2(r_rev)),
            "gross_margin": float(q2(r_gm)),
            "gross_margin_pct": float(q4(r_gm / r_rev)),
            "total_budget": float(q2(budget_by_region[region])),
        }

    by_upn_out: dict[str, dict[str, float | int]] = {}
    for upn, stats in rep_stats.items():
        u_rev = Decimal(stats["revenue"])
        u_cogs = Decimal(stats["cogs"])
        by_upn_out[upn] = {
            "row_count": int(stats["rows"]),
            "total_revenue": float(q2(u_rev)),
            "gross_margin": float(q2(u_rev - u_cogs)),
        }
    by_upn_out["unauthorized.user@contoso.com"] = {
        "row_count": 0,
        "total_revenue": 0.0,
        "gross_margin": 0.0,
    }

    ground_truth = {
        "seed": SEED,
        "dataset_sha256": checksums,
        "overall": {
            "sales_row_count": len(fact_sales),
            "budget_row_count": len(fact_budget),
            "distinct_orders": len(order_ids),
            "total_revenue": float(q2(overall_rev)),
            "total_cogs": float(q2(overall_cogs)),
            "gross_margin": float(q2(overall_gm)),
            "gross_margin_pct": float(q4(overall_gm / overall_rev)),
            "total_units": overall_units,
            "total_discount": float(q2(overall_disc)),
            "discount_rate_pct": float(q4(overall_disc / overall_gross)),
            "gross_revenue": float(q2(overall_gross)),
            "active_customers": len(customer_keys),
            "revenue_per_customer": float(q2(overall_rev / len(customer_keys))),
            "average_order_value": float(q2(overall_rev / len(order_ids))),
            "total_budget": float(q2(total_budget)),
            "budget_variance": float(q2(budget_var)),
            "budget_variance_pct": float(q4(budget_var / total_budget)),
        },
        "by_year": by_year_out,
        "by_region": by_region_out,
        "by_sales_rep_upn": by_upn_out,
    }

    gt_path.write_text(json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8")
    print(f"Generated 7 CSVs ({len(fact_sales)} sales rows, {len(fact_budget)} budget rows)")
    print(f"Ground truth written to {gt_path}")


if __name__ == "__main__":
    main()
