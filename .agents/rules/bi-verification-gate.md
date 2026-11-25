# BI Verification Gate Rule

Never declare a BI modeling, DAX, RLS, or PBIR task complete until empirical verification passes:

1. Design changes must pass `bi-agent verify-design <requirement.yaml>` (Gate G1).
2. Semantic model changes must pass `bi-agent verify-model` (Gates G2 PBIP/TMDL + G3 TE2 BPA `0` Error violations + VPAX).
3. Business metrics must pass `bi-agent verify-dax` (Gate G4 live DAX reconciliation within `+-0.01` + performance budget).
4. Security roles must pass `bi-agent verify-rls` (Gate G5 live ADOMD impersonation proving isolation and `0` rows for unauthorized UPNs).
5. Report changes must pass `bi-agent verify-report` (Gate G6 `pbir validate --all`, `pbir desktop refresh -m`, and `pbir desktop screenshot --all` visual check).
6. Before committing `pbi/` artifacts, stage files and run `bi-agent verify-all` to produce a matching `verification-report.json`, then commit via `bi-agent git-commit -m "<type>(<scope>): <summary>"`.
