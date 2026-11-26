# BI Verification Gate Rule

Never declare a BI modeling, DAX, RLS, or PBIR task complete until empirical verification passes:

1. Raw flat-file ingestion/staging changes must pass `bi-agent profile-data` and `bi-agent verify-data <requirement.yaml>` (Gate G0).
2. Design changes must pass `bi-agent verify-design <requirement.yaml>` (Gate G1).
3. Semantic model changes must pass `bi-agent verify-model --requirement <requirement.yaml>` (Gates G2 PBIP/TMDL + G3 TE2 BPA `0` Error violations + VPAX).
4. Business metrics must pass `bi-agent verify-dax --requirement <requirement.yaml>` (Gate G4 live DAX reconciliation within `+-0.01` + performance budget).
5. Security roles must pass `bi-agent verify-rls --requirement <requirement.yaml>` (Gate G5 live ADOMD impersonation proving isolation and `0` rows for unauthorized UPNs).
6. Report changes must pass `bi-agent verify-report --requirement <requirement.yaml>` (Gate G6 `pbir validate --all`, contracted page/visual checks, `pbir desktop refresh -m`, and `pbir desktop screenshot --all` visual check).
7. Before committing `pbi/` or any `*.SemanticModel` / `*.Report` artifacts, stage files and run `bi-agent verify-all --requirement <requirement.yaml>` to produce a matching `verification-report.json`, then commit via `bi-agent git-commit -m "<type>(<scope>): <summary>"`.
