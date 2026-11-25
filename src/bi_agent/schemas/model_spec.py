"""Dimensional star-schema contract and relationship DSL parser."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from bi_agent.schemas.requirement import (
    BusinessRequirement,
    DimensionSpec,
    FactSpec,
    KpiDefinition,
    RlsRequirement,
)

RELATIONSHIP_DSL_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_ ]*)\[([^\]]+)\]\s*->\s*"
    r"([A-Za-z_][A-Za-z0-9_ ]*)\[([^\]]+)\]\s*"
    r"\(\s*(\*:1|1:1|\*:\*)\s*,\s*(Single|Both)\s*\)\s*$"
)


class RelationshipSpec(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    cardinality: Literal["*:1", "1:1", "*:*"] = "*:1"
    cross_filter: Literal["Single", "Both"] = "Single"
    is_active: bool = True


def parse_relationship_dsl(expr: str) -> RelationshipSpec:
    """Parse `'FactSales[DateKey] -> DimDate[DateKey] (*:1, Single)'` into RelationshipSpec."""
    match = RELATIONSHIP_DSL_RE.match(expr)
    if not match:
        raise ValueError(
            f"Invalid relationship DSL '{expr}'. "
            "Expected format: 'FromTable[FromCol] -> ToTable[ToCol] (*:1, Single)'"
        )
    return RelationshipSpec(
        from_table=match.group(1).strip(),
        from_column=match.group(2).strip(),
        to_table=match.group(3).strip(),
        to_column=match.group(4).strip(),
        cardinality=match.group(5),  # type: ignore[arg-type]
        cross_filter=match.group(6),  # type: ignore[arg-type]
    )


class DimensionalModelSpec(BaseModel):
    req_id: str
    target_pbip: str
    facts: list[FactSpec] = Field(default_factory=list)
    dimensions: list[DimensionSpec] = Field(default_factory=list)
    relationships: list[RelationshipSpec] = Field(default_factory=list)
    measure_display_table: str = "_Measures"
    kpis: list[KpiDefinition] = Field(default_factory=list)
    rls_roles: list[RlsRequirement] = Field(default_factory=list)

    @classmethod
    def from_requirement(cls, req: BusinessRequirement) -> DimensionalModelSpec:
        rels = [parse_relationship_dsl(item) for item in req.star_schema.relationships]
        return cls(
            req_id=req.id,
            target_pbip=req.target_pbip,
            facts=list(req.star_schema.facts),
            dimensions=list(req.star_schema.dimensions),
            relationships=rels,
            measure_display_table=req.measures.display_table,
            kpis=list(req.measures.kpis),
            rls_roles=list(req.rls_roles),
        )

    def validate_star_schema(self, repo_root: Path | None = None) -> list[str]:
        """Return a list of design violations; empty list means compliant."""
        violations: list[str] = []
        fact_names = {f.name for f in self.facts}
        dim_names = {d.name for d in self.dimensions}
        all_tables = fact_names | dim_names

        if not self.facts:
            violations.append("At least one fact table must be declared.")
        if not self.dimensions:
            violations.append("At least one dimension table must be declared.")

        if not any(d.mark_as_date_table for d in self.dimensions):
            violations.append("At least one dimension must specify mark_as_date_table.")

        for rel in self.relationships:
            if rel.from_table not in all_tables:
                violations.append(
                    f"Relationship source table '{rel.from_table}' is not declared in star_schema."
                )
            if rel.to_table not in all_tables:
                violations.append(
                    f"Relationship target table '{rel.to_table}' is not declared in star_schema."
                )
            if rel.cardinality == "*:*":
                violations.append(
                    f"Many-to-many relationship prohibited in star schema: "
                    f"{rel.from_table}[{rel.from_column}] -> {rel.to_table}[{rel.to_column}]"
                )
            if rel.cross_filter == "Both":
                violations.append(
                    f"Bidirectional cross-filter prohibited in star schema: "
                    f"{rel.from_table}[{rel.from_column}] -> {rel.to_table}[{rel.to_column}]"
                )

        if not self.kpis:
            violations.append("At least one KPI measure must be defined.")
        for kpi in self.kpis:
            if not kpi.format.strip():
                violations.append(f"KPI '{kpi.name}' is missing a format string.")
            if not kpi.folder.strip():
                violations.append(f"KPI '{kpi.name}' is missing a display folder.")
            if kpi.description and len(kpi.description) > 200:
                violations.append(f"KPI '{kpi.name}' description exceeds 200 characters.")

        for role in self.rls_roles:
            if role.table not in all_tables:
                violations.append(
                    f"RLS role '{role.name}' targets undeclared table '{role.table}'."
                )
            if not role.filter.strip():
                violations.append(f"RLS role '{role.name}' has an empty filter expression.")
            if not role.expected_fail_closed:
                violations.append(f"RLS role '{role.name}' must set expected_fail_closed=true.")
            if role.type == "dynamic" and "USERPRINCIPALNAME()" not in role.filter.upper():
                violations.append(
                    f"Dynamic RLS role '{role.name}' must reference USERPRINCIPALNAME()."
                )

        if repo_root is not None:
            for fact in self.facts:
                if not (repo_root / fact.source).is_file():
                    violations.append(f"Fact source file not found: {fact.source}")
            for dim in self.dimensions:
                if not (repo_root / dim.source).is_file():
                    violations.append(f"Dimension source file not found: {dim.source}")

        return violations
