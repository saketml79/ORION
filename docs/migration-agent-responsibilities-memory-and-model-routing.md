# Migration agent responsibilities, run memory and model routing

Status: proposed incremental design, not an implemented expanded agent mesh or
verified multi-model benchmark. This note records the user-supplied agent hierarchy
and routing brief. No model selection, additional agent, inference, storage cleanup
or deployment is authorized by this document alone.

## 1. Business contract comes first

The captured XML, task-level business behavior and explicit user scope define the
migration. Tests verify integrity and regressions; they do not supply migration
specifications. Complete generation, model review and independently demonstrated
runtime parity are separate results. A higher model capability or more agents does
not by itself prove correctness.

Single-workflow conversion preserves only the selected workflow. Captured external
calls stay informational placeholders and do not require conversion of child Jobs.
Cohort expansion requires explicit member selection. Dependencies, data readiness
and completion order must not be inferred from similar names or shared mappings.

## 2. Responsibilities without immediately adding agents

| Brief responsibility | Current owner / phase | Required evidence-backed deliverable |
|---|---|---|
| Metadata extraction | discovery | Workflow/session/mapping identities; fields, ports, overrides, declarations and source locators |
| Workflow dependencies | discovery + architecture | Literal calls, direct control edges, conditional outcomes and external-scope dispositions |
| Transformation semantics | semantics | Expressions, lookup joins, router paths, aggregation grain, null/type rules and update strategies |
| PySpark generation | separate writer using artifact route | Actual task-owned code and source-variable-to-runtime-parameter mapping |
| Lakeflow generation | architecture + separate writer | Appropriate Jobs or declarative-pipeline boundaries; native conditions and executable resource wiring |
| Validation | validation role + independent code reviewer | Exact source/code comparisons, precise issue locations, assurance and unresolved evidence |
| Optimization | architecture + semantics, later specialist if needed | Evidence-qualified reuse and tuning without changing predicates, ordering or write semantics |
| Test-case design | validation + artifact | Independently derived expected outcomes, edge cases and parity criteria, not an oracle copied from generated code |
| Documentation | artifact + writer | Top notebook business overview, per-cell logic explanations, source/target maps and limitations |
| Coordination | supervisor | Reconciled evidence, explicit conflicts, final scoped design and recorded handoffs |

These functions can remain skill-defined phases under six roles. Only add separate
metadata, dependency, PySpark, Lakeflow, optimization, test and documentation agents
if measurements show that splitting responsibilities improves fidelity or latency.
The separate writer and code reviewer must not be mistaken for extra analysis roles.

## 3. Structured handoffs

Each handoff should contain source package/version pins, selected workflow/scope,
canonical source locators, producing role, task IDs, required behavior, unresolved
questions, and provenance. Proposed improvements:

- Parameter map: original token, declaration scope, usage locations, safe captured
  default, precedence, runtime parameter or task-output key, type and resolution.
- UC map: canonical source or target ID and instance, original qualified identity,
  exact observed catalog.schema.table, columns/types/nullability, observation hash,
  matching rationale, confidence and unresolved candidates. Never copy a source
  binding to a target merely because their names match.
- Business map: rules, phase order, row multiplicity, writes, transactions, restart,
  errors and conditions linked to original evidence and generated artifacts.
- Code review: actual code bytes, structured conditions, upstream metric producer,
  relevant source records and precise findings. Missing evidence stays unknown.

These richer handoffs are proposed, not a claim that the current Design schema
already guarantees complete parameter or semantic coverage. Safe target creation
requires an explicit intended destination and source-derived target schema; creating
an empty source cannot establish that required business data exists.

## 4. Memory layers and lifecycle

### Package context, durable and pinned

Keep the immutable canonical XML package and analysis version as the common source
of truth throughout a run. Every role receives a compact package identity/scope
anchor and retrieves exact records from that pinned context. Keep large XML outside
model message copies; use lossless references and paged records, never lossy summaries
as replacements for consequential source facts.

### Run working memory, ephemeral authority

Create a fresh run ID and run-specific working-memory namespace. Store retrieved
facts, parameter/UC/business maps, agent findings, unresolved items, accepted design
and review feedback with ownership and source provenance. Roles receive relevant
facts for their task rather than the whole accumulated conversation. A handoff does
not automatically grant citation ownership or treat another model's prose as fact.

On terminal completion, freeze audit/artifact receipts, release in-memory objects,
and retire run working memory as an execution authority. On a new run, do not reuse
the previous conversation or temporary conclusions. Reuse the same immutable package
context if its pins still match. Retain historical receipts for audit, not as active
instructions. Ending run memory does not mean deleting tested or historical code.

### Curated reusable knowledge, separate and opt-in

Reusable conversion patterns need evidence, scope, version and review. Current
role memory is namespace-filtered and can be recalled across identical-input runs;
that is not the same as the requested strictly run-scoped working memory. A future
change should distinguish curated knowledge from disposable run facts. Namespace
filtering is an isolation mechanism, not proof that contamination is impossible.

## 5. Model routing proposal from the brief

| Phase | Proposed family from user brief | Evidence required before adoption |
|---|---|---|
| Metadata + documentation + Lakeflow syntax | Claude Sonnet 5.5 | Available service, user permission, tool/structured-output support and measured task results |
| Dependencies + semantics + PySpark + optimization + test design | GPT-6.1 Terra | Same checks plus correctness on held-out transformation and parameter-mapping cases |
| Independent semantic review | Claude Opus 5.5 | Independent route, calibrated rubric and task-specific source/code coverage |
| Alternative comparison | Gemini 4 families | Actual supported interface and task benchmark, not model-name inference |

The official Databricks model overview lists these families, but explicitly warns
that globally listed models may not be available in a particular workspace. The
current local UI selects one endpoint for six roles. The existing backend accepts
explicit role routes; Model Serving endpoint names and Unity Gateway three-part
service names are different interfaces and must not be substituted blindly.

Freeze exact route/model configuration at run submission. Do not change an active
run, automatically downgrade, or retry via a different provider. Multiple endpoints
may diversify behavior or capacity, but do not guarantee independent quotas, tool
compatibility, fewer rejections or higher semantic fidelity. Keep user-scoped
Databricks authorization; no new credential or service-principal fallback.

## 6. Performance priorities for the existing implementation

Before expanding the mesh, measure source retrieval volume, useful versus duplicate
context, handoff sufficiency, input tokens, call durations and correction loops.
Batch independent metadata reads, retain exact invocation-local observations, avoid
rebuilding package indexes for each call, and preserve scope/identity/freshness.
Use concise summaries and typed handoffs so models do not spend output on repeated
rationale. Rejected invalid submissions are correction events, distinct from tool
execution failures. Do not weaken validators to improve a headline success rate.

Current normal app runs use no explicit investigation budget; constants for optional
budgets must not be described as active universal limits. Verify actual per-request
context/output caps and provider limits before changing them.

## 7. Evaluation before adoption

Use the same source cases and frozen rubric for single-model and multi-model runs.
Include both simple orchestration and complex joins, lookups, aggregators, routers,
update strategy, null/precision, source-target name collisions and scoped parameter
precedence. Do not seed test prompts with workflow answers or silently discard
unresolved branches, watermarks or status updates as an optimization.

Measure structural coverage separately from semantic accuracy, parameter use,
UC source/target linking and execution parity. Publish denominators, unavailable
metrics, model/tool latency, input/output tokens, rejected corrections and failures.
A reference-free AI review is advisory; trusted expected outputs and runtime evidence
remain necessary for parity. Migration evaluation must show the exact task/variable
inventory and actual reviewer results, not a universal green check.

## 8. Incremental adoption and approval

1. Finish source-grounded behavior and reporting in the current six-role path.
2. Add explicit, task-owned parameter and UC mapping handoffs with compatibility checks.
3. Pilot isolated per-role routes with the same workflow-independent request.
4. Measure results on fixed cases; expand agents only when supported by evidence.
5. Keep notebook edit roundtrip as a separate consented, revisioned feature; it is
   not implemented by attachment or by this document.

Existing saved notebooks/designs remain immutable history. This note does not
change active skill prompts, user configuration, execution or deployment gates.

References:
- [Databricks-hosted inference overview](https://docs.databricks.com/aws/en/machine-learning/model-serving/foundation-model-overview), retrieved for this design discussion.
- [Databricks model specifications](https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models), interface and regional details require verification.
- [Migration evaluation scorecard](agent-evaluation-scorecard.md).
- [Current v25 coverage assessment](migration-coverage-v25.md).
- [Single-workflow guidance](single-workflow-conversion-guidance.md).
