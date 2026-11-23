# ORION documentation

Updated: **2026-11-22**.

Start with the [open work checklist](ORION-TODO.md) and the
[current release ledger](current-release-verification-ledger.md). This index
lists the maintained design and operational references. Completed, one-off
dated fix reports were consolidated into the release ledger and removed; active
blockers and durable technical guidance remain separate.

## Reading order

1. [Architecture and implementation notes](proposal-first-deepagents-lakeflow-plan.md):
  background design intent and implementation boundaries. This is reference
  material, not a prerequisite to make requested UI or support changes.
2. [Hosted app](databricks-hosted-workbench.md) and
   [Delta authority](hosted-delta-authority.md): OBO identity, data ownership,
   runtime storage, authorization and operational limitations. See also the
   [intake guide](hosted-intake-redesign.md) for user-scoped uploads and
   operation boundaries.
3. [Agent responsibilities and memory](migration-agent-responsibilities-memory-and-model-routing.md),
   [evaluation scorecard](agent-evaluation-scorecard.md), and
   [engineering review](agent-engineering-eight-source-review-2026-11-17.md):
   scoped roles, evidence, evaluation and known limitations.
4. [ORION verification](current-release-verification-ledger.md),
   [workbench verification](workbench-verification.md), and
   [conversion acceptance](conversion-acceptance-2026-11-10.md): current evidence
   and explicit unverified boundaries.
5. [Demo runbook](wil-net-dis-demo-runbook.md),
   [workflow shortlist](demo-workflow-shortlist.md), and
   [parent/child parity assessment](wil-parent-child-parity.md): bounded example
   evidence, not a general conversion claim.
6. [Diagram companion](modernization-review-diagrams.html): design presentation,
   not an execution trace.

## Active investigations and specialized references

- [Agent and deployment blockers](ORION-TODO.md), including support review,
  official search, skill vetting and shared Volume access.
- [Hosted intake](hosted-intake-redesign.md),
  [hosted object latency](hosted-object-latency-investigation.md),
  [deployment blocker](orion-deployment-blocker-2026-11-12.md), and
  [model-save incident](model-save-lock-incident-2026-11-09.md).
- [Package/source evidence](package-source-understanding-investigation.md),
  [ten-workflow lineage](ten-workflow-lineage.md),
  [target fixtures](target-fixture-completion-2026-11-16.md), and
  [writer limits](writer-metadata-staging-2026-11-12.md).
- Keep the remaining feature-specific reports only when linked from this index,
  the TODO list or the current release ledger. Do not treat their dated test
  totals as current whole-project certification.

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