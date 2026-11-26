"""Unit tests for Pydantic v2 BI contract, model spec, ground-truth, and evidence schemas."""

from __future__ import annotations

from pathlib import Path

import pytest

from bi_agent.config import find_repo_root
from bi_agent.schemas import (
    BusinessRequirement,
    DimensionalModelSpec,
    GateFinding,
    GateResult,
    GroundTruthDataset,
    RlsRequirement,
    TablePermissionSpec,
    VerificationEvidenceBundle,
    load_ground_truth,
    load_requirement,
    parse_relationship_dsl,
    resolve_dot_path,
    to_repo_relative_posix,
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
    req.rls_roles[2].table_permissions = [
        TablePermissionSpec(table="DimSalesRep", filter="'DimSalesRep'[Region] = \"NA\"")
    ]

    spec = DimensionalModelSpec.from_requirement(req)
    violations = spec.validate_star_schema()
    assert any("Many-to-many" in v for v in violations)
    assert any("Bidirectional" in v for v in violations)
    assert any("expected_fail_closed" in v for v in violations)
    assert any("USERPRINCIPALNAME()" in v for v in violations)
    assert any("FactBudget" in v for v in violations)


def test_rls_requirement_table_permissions_helpers() -> None:
    role = RlsRequirement(
        name="Dynamic_SalesRep",
        type="dynamic",
        table_permissions=[
            TablePermissionSpec(
                table="DimSalesRep",
                filter="'DimSalesRep'[UserPrincipalName] = USERPRINCIPALNAME()",
            ),
            TablePermissionSpec(table="FactBudget", filter="FALSE ()"),
        ],
    )
    assert len(role.effective_permissions()) == 2
    assert role.denied_tables() == {"FactBudget"}


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
    assert gt.resolve("overall.total_revenue") == gt.overall.total_revenue
    assert (
        gt.resolve("by_sales_rep_upn.alice.emea@contoso.com.row_count")
        == gt.by_sales_rep_upn["alice.emea@contoso.com"].row_count
    )
    assert resolve_dot_path({"a": {"b.c": {"d": 42}}}, "a.b.c.d") == 42

    generic_gt = GroundTruthDataset.model_validate(
        {"overall": {"spend": 1200.5}, "by_mgr": {"mgr.one@corp.com": {"rows": 14}}}
    )
    assert generic_gt.resolve("overall.spend") == 1200.5
    assert generic_gt.resolve("by_mgr.mgr.one@corp.com.rows") == 14

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


def test_evidence_markdown_sanitizes_paths_and_renders_g4_g5(tmp_path: Path) -> None:
    fake_root = Path(r"C:\Users\TestUser\repo")
    abs_vpax = r"C:\Users\TestUser\repo\artifacts\runs\REQ-002\model.vpax"
    abs_loc = r"C:\Users\TestUser\repo\pbi\EnterpriseSales\EnterpriseSales.Report"
    assert to_repo_relative_posix(abs_vpax, fake_root) == "artifacts/runs/REQ-002/model.vpax"

    bundle = VerificationEvidenceBundle(
        req_id="REQ-002",
        git_branch="docs/portfolio-executive-polish",
        git_tree_sha="deadbeef01",
        overall_status="PASS",
        gates=[
            GateResult(
                gate_id="G3",
                name="BPA & VertiPaq Audit",
                status="PASS",
                duration_ms=500,
                summary="BPA errors=0",
                artifacts=[abs_vpax],
            ),
            GateResult(
                gate_id="G4",
                name="Live DAX Reconciliation & Perf",
                status="PASS",
                duration_ms=1200,
                summary="Reconciled 51 DAX assertion(s)",
                metrics={
                    "performance": {
                        "benchmark_skipped": False,
                        "cold_avg_ms": 6.0,
                        "warm_avg_ms": 6.0,
                        "cold_max_ms": 8.0,
                        "warm_max_ms": 7.0,
                        "cold_budget_ms": 1500,
                        "warm_budget_ms": 500,
                    }
                },
                artifacts=[r"C:\Users\TestUser\repo\artifacts\runs\REQ-002\benchmark_results.csv"],
            ),
            GateResult(
                gate_id="G5",
                name="Live RLS Impersonation",
                status="PASS",
                duration_ms=800,
                summary="Verified 7 RLS impersonation scenario(s)",
                metrics={
                    "fact_tables": ["FactSales", "FactBudget"],
                    "matrix": [
                        {
                            "role": "Region_NA",
                            "type": "static",
                            "principal": None,
                            "fact_counts": {"FactSales": 8333},
                            "status": "PASS",
                        },
                        {
                            "role": "Dynamic_SalesRep",
                            "type": "dynamic",
                            "principal": "alice.emea@contoso.com",
                            "fact_counts": {"FactSales": 3125, "FactBudget": 0},
                            "status": "PASS",
                        },
                        {
                            "role": "Dynamic_SalesRep",
                            "type": "dynamic",
                            "principal": "unauthorized.user@contoso.com",
                            "fact_counts": {"FactSales": 0, "FactBudget": 0},
                            "status": "PASS",
                        },
                    ],
                },
            ),
            GateResult(
                gate_id="G6",
                name="Report QA & Desktop Render",
                status="PASS",
                duration_ms=1000,
                summary="pbir validate passed",
                findings=[
                    GateFinding(
                        severity="info",
                        code="G6_INFO",
                        message="Checked report",
                        location=abs_loc,
                    )
                ],
                artifacts=[r"C:\Users\TestUser\repo\artifacts\runs\REQ-002\screenshots\Exec.png"],
            ),
        ],
    )

    md_path = bundle.write_markdown(tmp_path / "verification-report.md", repo_root=fake_root)
    md_text = md_path.read_text(encoding="utf-8")

    assert "C:\\Users" not in md_text
    assert "C:/Users" not in md_text
    assert "`artifacts/runs/REQ-002/model.vpax`" in md_text
    assert "`pbi/EnterpriseSales/EnterpriseSales.Report`" in md_text
    assert "max_cold_ms=8.0ms" in md_text
    assert "max_warm_ms=7.0ms" in md_text
    assert "<= 1500ms" in md_text
    assert "<= 500ms" in md_text
    assert "| Role | Principal | FactSales Rows | FactBudget Rows | Status |" in md_text
    assert "| Region_NA | (static) | 8,333 | N/A | `PASS` |" in md_text
    assert "| Dynamic_SalesRep | alice.emea@contoso.com | 3,125 | 0 | `PASS` |" in md_text
    assert (
        "| Dynamic_SalesRep | unauthorized.user@contoso.com | 0 | 0 | `PASS` |" in md_text
    )

    legacy_bundle = VerificationEvidenceBundle(
        req_id="REQ-LEGACY",
        overall_status="PASS",
        gates=[
            GateResult(
                gate_id="G5",
                name="Live RLS Impersonation",
                status="PASS",
                metrics={
                    "matrix": [
                        {
                            "role": "Scope_A",
                            "principal": None,
                            "actual_rows": 100,
                            "actual_budget_rows": 12,
                            "status": "PASS",
                        }
                    ]
                },
            )
        ],
    )
    legacy_md = legacy_bundle.write_markdown(tmp_path / "legacy-report.md").read_text(
        encoding="utf-8"
    )
    assert "| Role | Principal | Primary Fact Rows | Secondary Fact Rows | Status |" in legacy_md
    assert "| Scope_A | (static) | 100 | 12 | `PASS` |" in legacy_md


def test_v2_business_requirement_contract_fields() -> None:
    req = BusinessRequirement.model_validate(
        {
            "id": "REQ-V2",
            "title": "Generic Domain Contract",
            "owner": "Finance",
            "status": "approved",
            "target_pbip": "pbi/Generic",
            "project": {
                "pbip_path": "pbi/Generic",
                "ground_truth_path": "data/generic/expected.json",
                "desktop_report_name": "Generic",
            },
            "star_schema": {
                "facts": [{"name": "FactLedger", "grain": "Line", "source": "data/fact.csv"}],
                "dimensions": [
                    {
                        "name": "DimDate",
                        "key": "DateKey",
                        "source": "data/date.csv",
                        "mark_as_date_table": "Date",
                    }
                ],
                "relationships": ["FactLedger[DateKey] -> DimDate[DateKey] (*:1, Single)"],
            },
            "measures": {
                "kpis": [
                    {"name": "Total Spend", "folder": "01 Spend", "format": "$#,##0.00"}
                ]
            },
            "verification": {
                "report_checks": {"expected_pages": ["Overview"], "min_visuals_per_page": 2}
            },
        }
    )
    assert req.resolved_pbip_path() == "pbi/Generic"
    assert req.resolved_ground_truth_path() == "data/generic/expected.json"
    assert req.resolved_desktop_report_name() == "Generic"
    assert req.verification.report_checks.expected_pages == ["Overview"]
