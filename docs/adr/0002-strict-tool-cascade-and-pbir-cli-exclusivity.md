# ADR-0002: Strict Modeling Tool Cascade & PBIR CLI Exclusivity

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

Hand-editing Power BI Enhanced Report (`PBIR`) JSON files (`*.Report/definition/**/*.json`) frequently violates schema expectations, breaks `$schema` contracts, or leaves orphaned page/visual references. Similarly, editing semantic model `.tmdl` files directly without TOM/engine validation risks relationship cardinality errors, broken DAX dependencies, or non-standard metadata serialization.

## Decision

Enforce a strict deterministic tool cascade in `AGENTS.md` and `.agents/rules/pbip-tool-cascade.md`, backed by runtime `PreToolUse` hooks:

1. **Semantic Models:** `powerbi-modeling-mcp` first -> `TabularEditor.exe` (`-S` C# TOM scripts, `-A` BPA, `-TMDL` export) -> PowerShell TOM/ADOMD.NET (`scripts/pbid/`) -> direct `.tmdl` edits strictly as a last resort followed immediately by Tabular Editor 2 TMDL load validation (`Gate G2`).
2. **PBIR Reports:** Exclusive use of the `pbir` CLI (`pbir new`, `pbir add`, `pbir set`, `pbir visuals`, `pbir validate --all`, `pbir desktop refresh`, `pbir desktop screenshot`). Direct writes to `*.Report/definition/**/*.json` are strictly prohibited.
3. **Runtime Enforcement:** `.agents/hooks.json` wires `src/bi_agent/hooks/pre_tool_guard.py` (`PreToolUse`) and `src/bi_agent/hooks/post_tool_audit.py` (`PostToolUse`). `evaluate_pre_tool_use` denies file-write tools (`write_file`, `edit_file`, `write_to_file`, `replace_file_content`, `multi_replace_file_content`) targeting `*.Report/definition/**/*.json` as well as shell commands (`run_command`) attempting file redirects or copies into those paths.

## Consequences

- **Positive:** PBIR schemas stay valid across all pages (`44` visuals across `4` pages in `pbi/EnterpriseSales/EnterpriseSales.Report/`) and pass `pbir validate --all` cleanly.
- **Positive:** Guardrails fail closed at tool-invocation time rather than waiting for post-hoc review.
- **Trade-off:** Bulk visual refactoring must be expressed through `pbir` CLI commands rather than JSON text replacement.

## Verification & References

- Governance & Cascade Rules: `AGENTS.md`, `.agents/rules/pbip-tool-cascade.md`, `.agents/hooks.json`
- Hook Implementation: `src/bi_agent/hooks/pre_tool_guard.py`, `src/bi_agent/hooks/post_tool_audit.py`
- Fault Injection & Hook Tests: `tests/unit/test_hooks.py`, `tests/fault_injection/test_fault_injection.py` (`test_fi01_hook_blocks_pbir_json_direct_and_shell_writes`, `test_fi07_g6_real_pbir_validate_catches_corrupt_visual`)
