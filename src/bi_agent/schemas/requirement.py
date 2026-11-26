"""Requirement YAML contract schemas."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

PAGE_SLUG_RE = re.compile(r"^[\w-]+$")


class BusinessContext(BaseModel):
    audience: list[str] = Field(default_factory=list)
    decision_questions: list[str] = Field(default_factory=list)


class FactSpec(BaseModel):
    name: str
    grain: str
    source: str


class HierarchySpec(BaseModel):
    name: str
    levels: list[str]


class DimensionSpec(BaseModel):
    name: str
    key: str
    source: str
    mark_as_date_table: str | None = None
    hierarchies: list[HierarchySpec] = Field(default_factory=list)


class StarSchemaSpec(BaseModel):
    facts: list[FactSpec] = Field(default_factory=list)
    dimensions: list[DimensionSpec] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class KpiDefinition(BaseModel):
    name: str
    folder: str
    format: str
    description: str = ""


class MeasuresSpec(BaseModel):
    display_table: str = "_Measures"
    kpis: list[KpiDefinition] = Field(default_factory=list)


class TablePermissionSpec(BaseModel):
    table: str
    filter: str


class RlsRequirement(BaseModel):
    name: str
    type: Literal["static", "dynamic"]
    table: str = ""
    filter: str = ""
    table_permissions: list[TablePermissionSpec] = Field(default_factory=list)
    expected_fail_closed: bool = True
    test_principals: list[str] = Field(default_factory=list)

    def effective_permissions(self) -> list[TablePermissionSpec]:
        """Return all declared table permissions, supporting legacy single-table fields."""
        perms = list(self.table_permissions)
        seen = {p.table for p in perms}
        if self.table.strip() and self.table not in seen:
            perms.append(TablePermissionSpec(table=self.table, filter=self.filter))
        return perms

    def denied_tables(self) -> set[str]:
        """Return table names whose permission filter explicitly evaluates to FALSE."""
        denied: set[str] = set()
        for perm in self.effective_permissions():
            compact = re.sub(r"\s+", "", perm.filter).upper()
            if compact in ("FALSE()", "FALSE"):
                denied.add(perm.table)
        return denied

    def primary_filter(self) -> str:
        """Return the primary filter expression for legacy single-table inspection."""
        if self.filter.strip():
            return self.filter
        for perm in self.effective_permissions():
            if perm.filter.strip():
                return perm.filter
        return ""


class PageRequirement(BaseModel):
    slug: str
    intent: str

    @field_validator("slug")
    @classmethod
    def _validate_slug(cls, value: str) -> str:
        if not PAGE_SLUG_RE.match(value):
            raise ValueError(
                f"Page slug '{value}' must match ^[\\w-]+$ "
                "(spaces/dots are silently ignored by Desktop)"
            )
        return value


class VerificationAcceptance(BaseModel):
    bpa_max_errors: int = 0
    dax_numeric_tolerance: float = 0.01
    perf_budget_cold_ms: int = 1500
    perf_budget_warm_ms: int = 500
    rls_unauthorized_must_return_zero_rows: bool = True
    require_desktop_screenshots: bool = True


class ProjectPathsSpec(BaseModel):
    pbip_path: str = ""
    semantic_model_path: str = ""
    report_path: str = ""
    ground_truth_path: str = ""
    raw_data_dir: str = ""
    staged_data_dir: str = ""
    desktop_report_name: str = ""


class ForeignKeyRule(BaseModel):
    column: str
    target_table: str
    target_column: str
    target_file: str = ""
    allow_unknown_key: str | int | None = None


class StagingReconciliationRule(BaseModel):
    name: str
    raw_file: str
    staged_file: str
    quarantine_file: str | None = None
    business_keys: list[str] = Field(default_factory=list)
    date_columns: list[str] = Field(default_factory=list)
    numeric_columns: list[str] = Field(default_factory=list)
    reconcile_amount_columns: list[str] = Field(default_factory=list)
    raw_to_staged_column_map: dict[str, str] = Field(default_factory=dict)
    foreign_keys: list[ForeignKeyRule] = Field(default_factory=list)
    expected_grain: str = ""
    tolerance: float = 0.01
    allow_negative_columns: list[str] = Field(default_factory=list)


class DataProfilingSpec(BaseModel):
    enabled: bool = True
    raw_dir: str = ""
    staged_dir: str = ""
    quarantine_dir: str = ""
    policy_decisions: dict[str, str] = Field(default_factory=dict)
    tables: list[StagingReconciliationRule] = Field(default_factory=list)


class MetricAssertionSpec(BaseModel):
    alias: str
    expected_path: str | None = None
    expected_value: float | None = None
    tolerance: float | None = None
    optional: bool = False


class ScalarQueryCheckSpec(BaseModel):
    name: str
    scope: str = "overall"
    dax: str
    assertions: list[MetricAssertionSpec] = Field(default_factory=list)


class GroupedMetricAssertionSpec(BaseModel):
    alias: str
    expected_field: str
    tolerance: float | None = None
    optional: bool = False


class GroupedQueryCheckSpec(BaseModel):
    name: str
    scope_prefix: str
    dax: str
    group_column: str
    group_aliases: list[str] = Field(default_factory=list)
    ground_truth_map_path: str
    metrics: list[GroupedMetricAssertionSpec] = Field(default_factory=list)


class IdentityTermSpec(BaseModel):
    alias: str
    sign: Literal[1, -1] = 1


class AccountingIdentitySpec(BaseModel):
    name: str
    lhs_alias: str
    rhs_terms: list[IdentityTermSpec] = Field(default_factory=list)
    tolerance: float | None = None


class BlankGuardCheckSpec(BaseModel):
    name: str
    scope: str = "cross_grain"
    dax: str
    expected_blank_aliases: list[str] = Field(default_factory=list)


class StringAssertionSpec(BaseModel):
    alias: str
    expected_value: str | None = None
    expected_path: str | None = None
    scope: str = "no_actuals"


class EmptyContextGuardSpec(BaseModel):
    name: str
    scope: str = "no_actuals"
    dax: str
    numeric_assertions: list[MetricAssertionSpec] = Field(default_factory=list)
    expected_blank_aliases: list[str] = Field(default_factory=list)
    string_assertions: list[StringAssertionSpec] = Field(default_factory=list)


class FuturePeriodCheckSpec(BaseModel):
    date_table_column: str
    actuals_date_column: str
    must_be_blank_measures: dict[str, str] = Field(default_factory=dict)
    budget_measure: str = ""
    status_measure: str = ""
    expected_future_status: str = "No Actuals"
    require_active: bool = False


class DaxVerificationSpec(BaseModel):
    scalar_queries: list[ScalarQueryCheckSpec] = Field(default_factory=list)
    grouped_queries: list[GroupedQueryCheckSpec] = Field(default_factory=list)
    accounting_identities: list[AccountingIdentitySpec] = Field(default_factory=list)
    cross_grain_blank_checks: list[BlankGuardCheckSpec] = Field(default_factory=list)
    empty_context_guards: list[EmptyContextGuardSpec] = Field(default_factory=list)
    future_period_check: FuturePeriodCheckSpec | None = None
    benchmark_dax: str = ""


class RlsKpiAssertionSpec(BaseModel):
    alias: str
    dax_expression: str
    expected_field: str | None = None
    expected_path: str | None = None
    expected_value: float | None = None
    tolerance: float | None = None


class StaticRlsCheckSpec(BaseModel):
    role: str
    scope_column: str = ""
    expected_scope_value: str = ""
    ground_truth_entry_path: str = ""
    fact_row_count_fields: dict[str, str] = Field(default_factory=dict)
    expected_fact_counts: dict[str, int] = Field(default_factory=dict)
    kpi_assertions: list[RlsKpiAssertionSpec] = Field(default_factory=list)


class DynamicRlsCheckSpec(BaseModel):
    role: str
    ground_truth_map_path: str = ""
    fact_row_count_fields: dict[str, str] = Field(default_factory=dict)
    kpi_assertions: list[RlsKpiAssertionSpec] = Field(default_factory=list)
    principals: list[str] = Field(default_factory=list)


class RlsVerificationSpec(BaseModel):
    static_checks: list[StaticRlsCheckSpec] = Field(default_factory=list)
    dynamic_checks: list[DynamicRlsCheckSpec] = Field(default_factory=list)


class ReportVerificationSpec(BaseModel):
    expected_pages: list[str] = Field(default_factory=list)
    min_visuals_per_page: int = 1
    require_screenshot_count_match: bool = True


class VerificationContractSpec(BaseModel):
    dax_checks: DaxVerificationSpec = Field(default_factory=DaxVerificationSpec)
    rls_checks: RlsVerificationSpec = Field(default_factory=RlsVerificationSpec)
    report_checks: ReportVerificationSpec = Field(default_factory=ReportVerificationSpec)


class BusinessRequirement(BaseModel):
    id: str
    title: str
    owner: str
    status: str
    target_pbip: str
    project: ProjectPathsSpec = Field(default_factory=ProjectPathsSpec)
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    data_profiling: DataProfilingSpec | None = None
    star_schema: StarSchemaSpec
    measures: MeasuresSpec
    rls_roles: list[RlsRequirement] = Field(default_factory=list)
    report_pages: list[PageRequirement] = Field(default_factory=list)
    verification_acceptance: VerificationAcceptance = Field(
        default_factory=VerificationAcceptance
    )
    verification: VerificationContractSpec = Field(default_factory=VerificationContractSpec)

    def resolved_pbip_path(self) -> str:
        return self.project.pbip_path or self.target_pbip

    def resolved_ground_truth_path(self) -> str:
        return self.project.ground_truth_path

    def resolved_desktop_report_name(self) -> str:
        if self.project.desktop_report_name:
            return self.project.desktop_report_name
        pbip = self.resolved_pbip_path()
        return Path(pbip).name if pbip else ""


def load_requirement(path: Path) -> BusinessRequirement:
    """Load and validate a BusinessRequirement YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Requirement YAML at {path} must deserialize to a mapping.")
    return BusinessRequirement.model_validate(raw)
