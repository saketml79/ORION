# ORION documentation

Updated: **2026-10-30**.

## Reading order

1. [Architecture and implementation matrix](proposal-first-deepagents-lakeflow-plan.md):
   authoritative design intent and dated partial-implementation status.
2. [Hosted app](databricks-hosted-workbench.md) and
   [Delta authority](hosted-delta-authority.md): OBO identity, data ownership,
   runtime storage, authorization and operational limitations.
   [Hosted intake redesign guide](hosted-intake-redesign.md): candidate upload and
   operation behavior, ask ledger, offline evidence, pending live checks and the
   explicit deployment hold. Journal durability is not external-worker durability.
3. [Historical metadata import](delta-state-migration.md): verified archive,
   record counts, recovery and distinction from the active hosted generation.
4. [Verification report](workbench-verification.md): observed results and
   explicitly historical test milestones.
5. [Demo runbook](wil-net-dis-demo-runbook.md) and
   [workflow shortlist](demo-workflow-shortlist.md): what can actually be shown,
   successful bounded tests and remaining gaps.
6. [Diagram companion](modernization-review-diagrams.html): presentation of the
   design, not an execution trace or proof that every stage is implemented.

## Non-negotiable distinctions

- Deployed application is not the same as a complete migration engine.
- Original XML, historical snapshots and fresh analysis versions are separate.
- Structural candidates, agent-submitted redesigns and executable artifacts are separate.
- SQL syntax drafts are not full non-SQL mapping implementations or semantic parity.
- Missing bindings block affected execution, not all logical design work.
- The WIL_NET_DIS example is a deterministic scoped test conversion, not universal
  AI generation. Its mock email and test child are explicitly disclosed.
- Logical outline approval does not grant artifact execution or deployment approval.
- GitHub publishing, generic CI/CD promotion, OpenRouter and dbt integration are
  not implemented merely because the UI describes them.
- No documented historical test total is a claim of a clean current full regression.

The hosted app is [ORION Migration Workbench](https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com).
Workspace/user/catalog identifiers in runbooks are actual environment examples,
not requirements for future packages or users.