# Enterprise BI Engineering Charter (`AGENTS.md`)

## 1. Verification-First Rule
- Never claim a BI change is complete by merely generating files.
- Every delivery must pass all 7 deterministic verification gates (`bi-agent verify-all`) and produce a fresh `verification-report.json` matching the staged Git tree hash before committing.

## 2. Strict Tool Cascade
- **Semantic Models:** `powerbi-modeling-mcp` first -> `TabularEditor.exe` (`-S` C# TOM / `-A` BPA) -> PowerShell TOM/ADOMD (`connect-pbid`) -> direct `.tmdl` edits (`tmdl` skill) strictly as last resort.
- **Reports (PBIR):** Use `pbir` CLI (`pbir new`, `pbir add`, `pbir set`, `pbir visuals`, `pbir validate --all`, `pbir desktop refresh|screenshot`) **exclusively**. NEVER hand-edit `*.Report/definition/**/*.json`.

## 3. DAX & RLS Standards
- Always reference columns as `'Table'[Column]` (table always quoted) and measures as bare `[Measure]`.
- Use `DIVIDE()` for ratios, `VAR` caching for reused expressions, and `FormatString` + `DisplayFolder` + `<=200`-char `Description` on every measure.
- Model pure `1:*` star schemas; hide surrogate keys and set `SummarizeBy = None` on non-additive numeric columns.
- Security roles (static and dynamic `USERPRINCIPALNAME()`) must fail closed (`0` rows for unknown/unmapped principals).

## 4. Token-Optimal Phase Handoff
- Read `docs/PHASE_STATE.md` first at session start instead of re-scanning historical logs.
- Write verbose tool output (BPA, VPAX, pytest, screenshots) to `artifacts/runs/` and keep `docs/PHASE_STATE.md` concise (`<=60` lines).
