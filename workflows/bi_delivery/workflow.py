"""Domain-agnostic multi-stage Power BI engineering & verification workflow (G0..G7)."""

import json
import subprocess
from pathlib import Path

req_id = args["req_id"]
requirement_path = args["requirement_path"]
project_dir_override = args.get("project_dir", "")
raw_data_dir = args.get("raw_data_dir", "")
offline = bool(args.get("offline", False))
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

BI_AGENT_BIN = str(Path(".venv/Scripts/bi-agent.exe"))

phase("G0 Data Profiling & Reconciliation")
if raw_data_dir:
    log(f"Running G0 preflight profile on {raw_data_dir} for {req_id}")
    subprocess.run(
        [BI_AGENT_BIN, "profile-data", raw_data_dir, "--req-id", req_id, "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

g0_proc = subprocess.run(
    [BI_AGENT_BIN, "verify-data", requirement_path, "--json"],
    capture_output=True,
    text=True,
    check=False,
)
if not g0_proc.stdout.strip():
    raise RuntimeError(
        f"bi-agent verify-data produced no output (exit={g0_proc.returncode}): {g0_proc.stderr}"
    )
g0_payload = json.loads(g0_proc.stdout)
g0_status = g0_payload.get("status", "FAIL")
log(f"G0 result: status={g0_status}, summary={g0_payload.get('summary', '')}")

if g0_status == "FAIL":
    if mode == "deliver":
        data_res = await agent(
            (
                f"Remediate Gate G0 messy-data profiling/reconciliation failures for {req_id} "
                f"using `{requirement_path}`.\n"
                "1. Inspect `artifacts/runs/{req_id}/data-profile.md` (or run "
                "`bi-agent profile-data`) to review detected defects and policy questions.\n"
                "2. Stage clean CSVs and quarantine logs matching `data_profiling.tables`, "
                "ensuring raw == staged + quarantine row counts and financial sums.\n"
                "3. Re-run `.venv\\Scripts\\bi-agent.exe verify-data \"{requirement_path}\" --json` "
                "and confirm status is PASS, then call finish."
            ),
            schema=DELIVERY_SCHEMA,
            role="Data Ingestion & Profiling Engineer",
            type_name=delivery_type,
        )
        log(f"G0 remediation result: {data_res}")
        if not data_res["passed"]:
            raise RuntimeError(f"Gate G0 remediation failed: {data_res['summary']}")
    else:
        raise RuntimeError(f"Gate G0 failed for {req_id}: {g0_payload.get('summary', '')}")

phase("G1 Design Contract")
log(f"Validating {req_id} contract at {requirement_path} (mode={mode})")
g1_proc = subprocess.run(
    [BI_AGENT_BIN, "verify-design", requirement_path, "--json"],
    capture_output=True,
    text=True,
    check=False,
)
if not g1_proc.stdout.strip():
    raise RuntimeError(
        f"bi-agent verify-design produced no output (exit={g1_proc.returncode}): {g1_proc.stderr}"
    )
g1_payload = json.loads(g1_proc.stdout)
g1_metrics = g1_payload.get("metrics", {})
g1_res = {
    "passed": g1_payload.get("status") == "PASS",
    "summary": g1_payload.get("summary", ""),
    "kpi_count": int(g1_metrics.get("kpi_count", 0)),
    "rls_role_count": int(g1_metrics.get("rls_role_count", 0)),
}
log(f"G1 result: {g1_res}")
if not g1_res["passed"]:
    raise RuntimeError(f"Gate G1 failed for {req_id}: {g1_res['summary']}")

project_dir = project_dir_override or g1_metrics.get("pbip_path") or ""
if not project_dir:
    raise RuntimeError(
        f"No project_dir resolved for {req_id}; set project.pbip_path/target_pbip or pass project_dir."
    )

pbip_stem = Path(project_dir).name
semantic_model_dir = (
    g1_metrics.get("semantic_model_path") or f"{project_dir}/{pbip_stem}.SemanticModel"
)
report_dir = g1_metrics.get("report_path") or f"{project_dir}/{pbip_stem}.Report"
measure_display_table = g1_metrics.get("measure_display_table") or "_Measures"
rls_role_names = list(g1_metrics.get("rls_role_names") or [])

results = {
    "req_id": req_id,
    "mode": mode,
    "project_dir": project_dir,
    "g0": {"status": g0_status, "summary": g0_payload.get("summary", "")},
    "g1": g1_res,
}

if mode == "deliver":
    phase("Semantic Model & RLS Delivery")
    model_res = await agent(
        (
            f"Deliver semantic model, DAX KPIs, and RLS roles for {req_id} in `{project_dir}` "
            f"matching `{requirement_path}`.\n"
            "Follow AGENTS.md tool cascade strictly (`powerbi-modeling-mcp` first -> TE2 -> TOM/ADOMD -> TMDL last resort).\n"
            f"Verify `.venv\\Scripts\\bi-agent.exe verify-model --project \"{project_dir}\" --requirement \"{requirement_path}\"`, "
            f"`verify-dax --requirement \"{requirement_path}\"`, and "
            f"`verify-rls --project \"{project_dir}\" --requirement \"{requirement_path}\"` pass with zero errors, then call finish."
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
            f"verify `.venv\\Scripts\\bi-agent.exe verify-report --report \"{report_dir}\" --requirement \"{requirement_path}\"`, "
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
if rls_role_names:
    role_files = ", ".join(
        f"`{semantic_model_dir}/definition/roles/{role}.tmdl`" for role in rls_role_names
    )
    rls_audit_instruction = (
        f"3. Read {role_files} and verify every declared RLS role exists with a non-empty "
        "fail-closed filter expression (dynamic roles reference USERPRINCIPALNAME() and "
        "deny non-filtered fact tables via FALSE ()).\n"
    )
else:
    rls_audit_instruction = (
        "3. Confirm `rls_roles` in the requirement contract is empty so no RLS role files are required.\n"
    )

audit_specs = [
    {
        "auditor": "Model & DAX Hygiene Auditor",
        "prompt": (
            f"Audit the semantic model in `{semantic_model_dir}` against `{requirement_path}` using read_file ONLY.\n"
            f"1. Read `{requirement_path}` to list all required KPI names, folders, formats, and RLS roles.\n"
            f"2. Read `{semantic_model_dir}/definition/tables/{measure_display_table}.tmdl` and verify "
            "every required KPI exists, qualifies columns as `'Table'[Column]`, references measures as bare `[Measure]`, "
            "uses `DIVIDE()` (no raw `/` division on columns/measures), sets `formatString` and `displayFolder`, "
            "and has a `///` description of <= 200 chars.\n"
            f"{rls_audit_instruction}"
            "Call finish immediately with {auditor, passed, findings} (passed=true only if findings is empty)."
        ),
    },
    {
        "auditor": "Report & Governance Auditor",
        "prompt": (
            f"Audit the PBIR report structure and governance rules for {req_id} under `{report_dir}` using read_file ONLY.\n"
            f"1. Read `{requirement_path}` and `{report_dir}/definition/pages/pages.json`.\n"
            "2. Confirm `pageOrder` has at least as many pages as declared in `report_pages` and read each "
            f"`{report_dir}/definition/pages/<id>/page.json` listed in `pageOrder`, "
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
    phase("G0-G7 Verification & Release Gate")
    commit_instruction = (
        f"Then commit via `.venv\\Scripts\\bi-agent.exe git-commit"
        + (" --allow-main" if allow_main else "")
        + f" -m \"feat({req_id}): deliver verified BI requirement\"`."
        if auto_commit
        else "Do NOT commit changes (auto_commit=false)."
    )
    offline_flag = " --offline" if offline else ""
    release_res = await agent(
        (
            f"Run full 8-gate verification for {req_id}.\n"
            "Stage project files with `git add`, then run "
            f"`.venv\\Scripts\\bi-agent.exe verify-all --req-id {req_id} --requirement \"{requirement_path}\"{offline_flag} --json`.\n"
            "Confirm overall_status == 'PASS' and all 8 gates G0..G7 pass (or expected SKIP when offline).\n"
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
