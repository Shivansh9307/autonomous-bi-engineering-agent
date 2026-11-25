"""Unit tests for Pydantic v2 BI contract, model spec, ground-truth, and evidence schemas."""

from __future__ import annotations

from pathlib import Path

import pytest

from bi_agent.config import find_repo_root
from bi_agent.schemas import (
    DimensionalModelSpec,
    GateResult,
    VerificationEvidenceBundle,
    load_ground_truth,
    load_requirement,
    parse_relationship_dsl,
)


def test_load_req001_and_validate_star_schema() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req = load_requirement(repo_root / "requirements" / "REQ-001-regional-sales-margin.yaml")
    assert req.id == "REQ-001"
    assert len(req.measures.kpis) == 16
    assert len(req.rls_roles) == 3

    spec = DimensionalModelSpec.from_requirement(req)
    violations = spec.validate_star_schema(repo_root=repo_root)
    assert violations == []


def test_star_schema_rejects_m2m_bidir_and_non_fail_closed_rls() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req = load_requirement(repo_root / "requirements" / "REQ-001-regional-sales-margin.yaml")
    req.star_schema.relationships.append(
        "FactSales[ProductKey] -> DimProduct[ProductKey] (*:*, Both)"
    )
    req.rls_roles[2].expected_fail_closed = False
    req.rls_roles[2].filter = "'DimSalesRep'[Region] = \"NA\""

    spec = DimensionalModelSpec.from_requirement(req)
    violations = spec.validate_star_schema()
    assert any("Many-to-many" in v for v in violations)
    assert any("Bidirectional" in v for v in violations)
    assert any("expected_fail_closed" in v for v in violations)
    assert any("USERPRINCIPALNAME()" in v for v in violations)


def test_page_slug_rejects_spaces_and_dots() -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    req = load_requirement(repo_root / "requirements" / "REQ-001-regional-sales-margin.yaml")
    payload = req.model_dump()
    payload["report_pages"][0]["slug"] = "Executive Overview.1"
    with pytest.raises(ValueError, match="silently ignored"):
        req.__class__.model_validate(payload)


def test_parse_relationship_dsl_invalid() -> None:
    with pytest.raises(ValueError, match="Invalid relationship DSL"):
        parse_relationship_dsl("FactSales.DateKey = DimDate.DateKey")


def test_load_ground_truth_and_evidence_roundtrip(tmp_path: Path) -> None:
    repo_root = find_repo_root(Path(__file__).resolve())
    gt = load_ground_truth(repo_root / "data" / "ground_truth" / "expected_metrics.json")
    assert gt.overall.sales_row_count == 25000
    assert gt.overall.budget_row_count == 648
    assert gt.by_sales_rep_upn["unauthorized.user@contoso.com"].row_count == 0
    assert gt.reconciliation_status(48744150.82, gt.overall.total_revenue, 0.01) == "PASS"
    assert gt.reconciliation_status(48744150.90, gt.overall.total_revenue, 0.01) == "FAIL"

    bundle = VerificationEvidenceBundle(
        req_id="REQ-001",
        git_branch="feat/bi-REQ-001",
        git_tree_sha="abc123",
        overall_status="PASS",
        gates=[
            GateResult(
                gate_id="G1",
                name="Design Contract",
                status="PASS",
                duration_ms=12,
                summary="All checks green",
            )
        ],
    )
    json_path = bundle.write_json(tmp_path / "verification-report.json")
    md_path = bundle.write_markdown(tmp_path / "verification-report.md")

    loaded = VerificationEvidenceBundle.load_json(json_path)
    assert loaded.is_passing() is True
    assert loaded.git_tree_sha == "abc123"
    md_text = md_path.read_text(encoding="utf-8")
    assert "| G1 | Design Contract | `PASS` |" in md_text
