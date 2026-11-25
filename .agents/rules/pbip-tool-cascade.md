# PBIP & Semantic Model Tool Cascade Rule

Always follow the deterministic tool precedence:

- **Semantic Models:**
  1. `powerbi-modeling-mcp` (preferred for live PBI Desktop model authoring, DAX execution with impersonation, and security roles)
  2. `TabularEditor.exe` (`-S` C# TOM scripts, `-A` BPA analysis, `-TMDL` export)
  3. PowerShell TOM / ADOMD.NET (`scripts/pbid/` & `connect-pbid` skill)
  4. Direct `.tmdl` edits strictly as a last resort (`tmdl` skill), followed immediately by TE2 TMDL load validation.
- **Reports (PBIR):**
  - Use `pbir` CLI (`pbir new`, `pbir add`, `pbir set`, `pbir visuals`, `pbir validate --all`, `pbir desktop refresh|screenshot`) **exclusively**.
  - **NEVER** hand-edit or overwrite `*.Report/definition/**/*.json` directly.
