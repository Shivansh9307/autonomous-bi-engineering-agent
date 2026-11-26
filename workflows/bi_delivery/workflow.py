"""Multi-stage Power BI engineering & verification workflow for run_workflow."""

import json
import subprocess
from pathlib import Path

req_id = args["req_id"]
requirement_path = args["requirement_path"]
project_dir = args.get("project_dir", "pbi/EnterpriseSales")
mode = args.get("mode", "audit")
auto_commit = bool(args.get("auto_commit", False))
allow_main = bool(args.get("allow_main", False))
delivery_type = args.get("delivery_agent_type", "self")

if mode not in ("deliver", "audit"):
    raise ValueError(f"Unsupported workflow mode '{mode}'; expected 'deliver' or 'audit'.")

DELIVERY_SCHEMA = {
    "type": "object",
    "required": ["passed", "summary"],
    "properties": {
        "passed": {"type": "boolean"},
        "summary": {"type": "string"},
    },
}

AUDIT_SCHEMA = {
    "type": "object",
    "required": ["auditor", "passed", "findings"],
    "properties": {
        "auditor": {"type": "string"},
        "passed": {"type": "boolean"},
        "findings": {"type": "array", "items": {"type": "string"}},
    },
}

RELEASE_SCHEMA = {
    "type": "object",
    "required": ["passed", "overall_status", "summary"],
    "properties": {
        "passed": {"type": "boolean"},
        "overall_status": {"type": "string"},
        "summary": {"type": "string"},
    },
}

phase("G1 Design Contract")
log(f"Validating {req_id} contract at {requirement_path} (mode={mode})")
g1_proc = subprocess.run(
    [str(Path(".venv/Scripts/bi-agent.exe")), "verify-design", requirement_path, "--json"],
    capture_output=True,
    text=True,
    check=False,
)
if not g1_proc.stdout.strip():
    raise RuntimeError(
        f"bi-agent verify-design produced no output (exit={g1_proc.returncode}): {g1_proc.stderr}"
    )
g1_payload = json.loads(g1_proc.stdout)
g1_res = {
    "passed": g1_payload.get("status") == "PASS",
    "summary": g1_payload.get("summary", ""),
    "kpi_count": int(g1_payload.get("metrics", {}).get("kpi_count", 0)),
    "rls_role_count": int(g1_payload.get("metrics", {}).get("rls_role_count", 0)),
}
log(f"G1 result: {g1_res}")
if not g1_res["passed"]:
    raise RuntimeError(f"Gate G1 failed for {req_id}: {g1_res['summary']}")

results = {
    "req_id": req_id,
    "mode": mode,
    "g1": g1_res,
}

if mode == "deliver":
    phase("Semantic Model & RLS Delivery")
    model_res = await agent(
        (
            f"Deliver semantic model, DAX KPIs, and RLS roles for {req_id} in `{project_dir}` "
            f"matching `{requirement_path}`.\n"
            "Follow AGENTS.md tool cascade strictly (`powerbi-modeling-mcp` first -> TE2 -> TOM/ADOMD -> TMDL last resort).\n"
            "Verify `.venv\\Scripts\\bi-agent.exe verify-model`, `verify-dax`, and "
            f"`verify-rls --requirement \"{requirement_path}\"` pass with zero errors, then call finish."
        ),
        schema=DELIVERY_SCHEMA,
        role="Semantic Model Engineer",
        type_name=delivery_type,
    )
    log(f"Semantic Model result: {model_res}")
    results["semantic_model"] = model_res
    if not model_res["passed"]:
        raise RuntimeError(f"Semantic model delivery failed: {model_res['summary']}")

    phase("PBIR Report Delivery")
    report_res = await agent(
        (
            f"Build or update PBIR report pages for {req_id} under `{project_dir}` "
            f"matching `{requirement_path}`.\n"
            "Use the `pbir` CLI exclusively (NEVER hand-edit `*.Report/definition/**/*.json`).\n"
            "Run `pbir validate --all`, `pbir desktop refresh -m`, and `pbir desktop screenshot --all`, "
            "inspect every PNG screenshot via read_file to confirm zero overlap/clipping, then call finish."
        ),
        schema=DELIVERY_SCHEMA,
        role="PBIR Report Designer",
        type_name=delivery_type,
    )
    log(f"PBIR Report result: {report_res}")
    results["pbir_report"] = report_res
    if not report_res["passed"]:
        raise RuntimeError(f"PBIR report delivery failed: {report_res['summary']}")

phase("Parallel Specialist Audit")
audit_specs = [
    {
        "auditor": "Model & DAX Hygiene Auditor",
        "prompt": (
            f"Audit the semantic model in `{project_dir}` against `{requirement_path}` using read_file ONLY.\n"
            f"1. Read `{requirement_path}` to list all required KPI names, folders, formats, and RLS roles.\n"
            f"2. Read `{project_dir}/EnterpriseSales.SemanticModel/definition/tables/_Measures.tmdl` and verify "
            "every required KPI exists, qualifies columns as `'Table'[Column]`, references measures as bare `[Measure]`, "
            "uses `DIVIDE()` (no raw `/` division on columns/measures), sets `formatString` and `displayFolder`, "
            "and has a `///` description of <= 200 chars.\n"
            f"3. Read `{project_dir}/EnterpriseSales.SemanticModel/definition/roles/Region_NA.tmdl`, "
            "`Region_EMEA.tmdl`, `Region_APAC.tmdl`, and `Dynamic_SalesRep.tmdl` and verify every declared RLS "
            "role exists with a non-empty fail-closed filter expression (dynamic role uses USERPRINCIPALNAME()).\n"
            "Call finish immediately with {auditor, passed, findings} (passed=true only if findings is empty)."
        ),
    },
    {
        "auditor": "Report & Governance Auditor",
        "prompt": (
            f"Audit the PBIR report structure and governance rules for {req_id} under `{project_dir}` using read_file ONLY.\n"
            f"1. Read `{requirement_path}` and `{project_dir}/EnterpriseSales.Report/definition/pages/pages.json`.\n"
            "2. Confirm `pageOrder` has at least as many pages as declared in `report_pages` and read each "
            f"`{project_dir}/EnterpriseSales.Report/definition/pages/<id>/page.json` listed in `pageOrder`, "
            "confirming `activePageName` is in `pageOrder` and display names cover all requested intents.\n"
            "3. Read `.agents/rules/bi-verification-gate.md`, `.agents/rules/pbip-tool-cascade.md`, and "
            "`.agents/hooks.json` to confirm governance guardrails are intact.\n"
            "Call finish immediately with {auditor, passed, findings} (passed=true only if findings is empty)."
        ),
    },
]

audit_results = await parallel(
    audit_specs,
    lambda spec: agent(
        spec["prompt"],
        schema=AUDIT_SCHEMA,
        role=spec["auditor"],
        type_name="research",
    ),
)
results["audits"] = audit_results
for item in audit_results:
    log(f"Audit [{item['auditor']}] passed={item['passed']} findings={item['findings']}")
if not all(item["passed"] for item in audit_results):
    failed = [item for item in audit_results if not item["passed"]]
    raise RuntimeError(f"Parallel specialist audit failed: {failed}")

if mode == "deliver":
    phase("G1-G7 Verification & Release Gate")
    commit_instruction = (
        f"Then commit via `.venv\\Scripts\\bi-agent.exe git-commit"
        + (" --allow-main" if allow_main else "")
        + f" -m \"feat({req_id}): deliver verified BI requirement\"`."
        if auto_commit
        else "Do NOT commit changes (auto_commit=false)."
    )
    release_res = await agent(
        (
            f"Run full 7-gate verification for {req_id}.\n"
            "Stage project files with `git add`, then run "
            f"`.venv\\Scripts\\bi-agent.exe verify-all --req-id {req_id} --requirement \"{requirement_path}\" --json`.\n"
            "Confirm overall_status == 'PASS' and all 7 gates G1..G7 pass.\n"
            f"{commit_instruction}\n"
            "Call finish with {passed, overall_status, summary}."
        ),
        schema=RELEASE_SCHEMA,
        role="Release Gatekeeper",
        type_name=delivery_type,
    )
    log(f"Release gate result: {release_res}")
    results["release"] = release_res
    if not release_res["passed"]:
        raise RuntimeError(f"Release gate failed: {release_res['summary']}")

print(json.dumps(results, indent=2))
