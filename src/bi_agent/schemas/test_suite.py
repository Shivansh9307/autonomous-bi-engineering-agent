"""DAX assertion, RLS impersonation, and ground-truth dataset schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class DaxAssertion(BaseModel):
    name: str
    dax_query: str
    expected_value: float
    tolerance: float = 0.01
    scope_label: str = "overall"


class RlsImpersonationCase(BaseModel):
    role_name: str
    effective_user_name: str | None = None
    expected_row_count: int
    expected_revenue: float | None = None
    must_be_empty: bool = False


class PerformanceBudget(BaseModel):
    cold_ms: int = 1500
    warm_ms: int = 500


class OverallMetrics(BaseModel):
    sales_row_count: int
    budget_row_count: int
    distinct_orders: int
    total_revenue: float
    total_cogs: float
    gross_margin: float
    gross_margin_pct: float
    total_units: int
    total_discount: float
    discount_rate_pct: float
    gross_revenue: float | None = None
    active_customers: int | None = None
    revenue_per_customer: float | None = None
    average_order_value: float
    total_budget: float
    budget_variance: float
    budget_variance_pct: float


class YearMetrics(BaseModel):
    total_revenue: float
    gross_margin: float
    total_budget: float
    revenue_py: float | None = None
    revenue_yoy: float | None = None
    revenue_yoy_pct: float | None = None


class RegionMetrics(BaseModel):
    row_count: int
    total_revenue: float
    gross_margin: float
    gross_margin_pct: float
    total_budget: float


class SalesRepMetrics(BaseModel):
    row_count: int
    budget_row_count: int = 0
    total_revenue: float
    gross_margin: float


class FinanceGuardExpectations(BaseModel):
    accounting_identity_diff: float = 0.0
    cross_grain_budget_dimensions: list[str] = Field(default_factory=list)
    max_sales_date_key: int
    max_date_key: int
    has_future_budget_periods: bool = False
    expected_no_actuals_status: str = "No Actuals"
    expected_no_budget_status: str = "No Budget"


class GroundTruthDataset(BaseModel):
    seed: int
    dataset_sha256: dict[str, str] = Field(default_factory=dict)
    overall: OverallMetrics
    finance_guards: FinanceGuardExpectations | None = None
    by_year: dict[str, YearMetrics] = Field(default_factory=dict)
    by_region: dict[str, RegionMetrics] = Field(default_factory=dict)
    by_sales_rep_upn: dict[str, SalesRepMetrics] = Field(default_factory=dict)

    def reconciliation_status(
        self, actual: float, expected: float, tolerance: float = 0.01
    ) -> Literal["PASS", "FAIL"]:
        return "PASS" if abs(actual - expected) <= tolerance else "FAIL"


def load_ground_truth(path: Path) -> GroundTruthDataset:
    """Load and validate expected_metrics.json."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return GroundTruthDataset.model_validate(raw)
