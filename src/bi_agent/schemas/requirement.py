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


class BusinessRequirement(BaseModel):
    id: str
    title: str
    owner: str
    status: str
    target_pbip: str
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    star_schema: StarSchemaSpec
    measures: MeasuresSpec
    rls_roles: list[RlsRequirement] = Field(default_factory=list)
    report_pages: list[PageRequirement] = Field(default_factory=list)
    verification_acceptance: VerificationAcceptance = Field(
        default_factory=VerificationAcceptance
    )


def load_requirement(path: Path) -> BusinessRequirement:
    """Load and validate a BusinessRequirement YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Requirement YAML at {path} must deserialize to a mapping.")
    return BusinessRequirement.model_validate(raw)
