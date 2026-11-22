---
name: lakeflow-design
description: "Use when designing Databricks Lakeflow workflows to propose evidence-backed task boundaries, reuse, and destination placement."
---

# Activation
Use for original-source or existing-bronze scenario design review.

## Procedure
0. Apply the injected shared production conversion contract. Retrieve the package,
	call and data graphs before selecting boundaries. Derive and compare alternatives
	from exact source semantics, not a required WIL outline or fixed task count.
	The optional deterministic reference tool retains its explicit design_origin;
	using it does not establish optimized architecture or complete conversion.
1. In one batch read this skill, recall_findings, workflow_planning_contract overview,
	 tasks and control_links. The overview includes the same deterministic XML-to-English
	 explanation as the workbench, including scheduler evidence. This is input, not a design.
2. Read definitions, mapping_fields, mapping_links, properties, connections and parameters
	 pages needed for this workflow. Respect next_offset, has_more and text_truncations.
	 mapping_fields retains exact expression attributes, not just transformation names.
	 sql_role_inventory separates pre, source, lookup, target and post SQL with owner/order.
	 Reconcile session overrides with defaults; do not choose one arbitrary SQL statement.
3. Call package_neighbors and inspect inbound/outbound invocations and field lineage.
	Compare a reusable module with unique-workflow boundaries.
	 A shared fingerprint is not equivalence, cadence compatibility or permission to combine.
4. Author and call submit_redesign, then submit_finding, before final prose. Use a real
	 Lakeflow Jobs logical DAG, not only questions or a restatement of the English report.
	 Missing physical bindings and missing production data do not prevent a logical draft.
	 Normal runs have no elapsed-time or model-call cutoff. Explicit budgets alone
	 bound investigation. Batch independent retrievals and prioritize concrete outputs.

## Logical graph construction contract
- Submit the exact {design: object} tool wrapper shown in the injected authoritative
	schema. Include title, tasks, edges, rationale, object alternatives (label, rationale,
	nonempty tradeoffs and evidence_ids), and coverage. Task fields are id, label,
	implementation_kind, purpose and nonempty evidence_ids. Parameter fields are name,
	purpose, required (boolean) and value_type (exact schema literal). Reuse groups require
	id, proposed_task_ids, rationale and citations. Constraints/blockers are text arrays;
	scoped_unresolved and cadence are typed objects, not prose strings. Omit empty optional
	sections if unnecessary. Never submit status, code, approval or fabricated bindings.
- Retrieve exact caller/child implementation and shared-data evidence under the shared
	contract. Recommend cohort expansion instead of silently adding child authority.
	Before submitting, check every field/type, proposed-ID reference, original coverage row
	and role-local citation. If recorded=false, repair its exact field-level feedback.
- Cover ALL direct original taskinstance IDs, disabled ones included. Start is an
	administrative entry: disposition removed with a rationale and no executable task
	is valid. Do not invent a Start notebook. Never discard an actual side effect as Start.
- Choose notebook_task or sql_task for ordered imperative writes and pre/post phases;
	pipeline_task only for suitable declarative datasets, not arbitrary stored procedures.
	Use condition_task for predicates, retaining exact branch meaning and true/false
	outcomes in edges. Retain failure/skip semantics and enabled dispositions explicitly.
- Informatica task type labels are not Databricks Jobs implementation kinds. Never emit
	`Session`, `Email`, `Command`, `Timer`, `email_task`, `session_task`, or similar source
	labels as `implementation_kind`. Use only the logical design schema's supported
	Databricks task kinds. Implement Email as a send-capable notebook with explicit
	secure mail configuration and secret references, not a rendered-only placeholder;
	a timer is an in-run wait, not a recurring schedule; a `-nowait` command is not a
	blocking `run_job_task`. If the correct adapter cannot be evidenced, preserve the
	behavior as task-scoped unresolved work instead of inventing an implementation.
- A timer is an in-run wait, not a recurring schedule. For an individual workflow,
	show a captured external -nowait workflow call as a non-executing informational
	placeholder, not a child Job or adapter. Mention asynchronous source behavior
	without creating a job-ID parameter, widget, simulated launch or child binding.
- Add typed cadence with captured launch mode and scheduler evidence. Retain unknown
	timezone, scheduler activation and external triggers as unverified. Never invent cron.
- Preserve per-session parameter-file/override scope in task descriptions. In the
	Unity Catalog source scenario, redacted historical Oracle connection aliases are
	non-blocking legacy context; source tables are already in UC. Bronze, silver and
	gold are output layers, and only use those indicated by actual workflow behavior.
- Artifact plans are optional for candidate designs. If supplied, use relative
	notebook/module paths, proposed_task_ids, mapping_ids and retrieved evidence_ids.
	Descriptions specify the responsibility in English, not code. Do not postpone
	the design solely to select file paths; the writer can choose them.
- Commands and notifications do not require SQL counterparts. Translate the captured
	purpose where possible; otherwise retain an explained task-specific placeholder.
	A placeholder is not proof that the original behavior was implemented.
- Add scoped_unresolved only for a real missing behavior or binding in the selected
	workflow. Do not mark historical Oracle connection redactions as human review when
	the UC source scenario is selected. Missing email settings are notification notes,
	not a global reason to refuse the design. Generate best-effort candidate code for
	supported tasks and state assumptions; do not claim independent correctness.
- The UC source scenario supplies the input-availability assumption. Do not block a
	design because this analysis did not query Unity Catalog or execute captured SQL.
	Preserve captured SQL phases as ordered candidate task behavior; distinguish analysis
	from later candidate notebook drafting and execution.
- Every proposed task implementation_kind must be a supported native Databricks Jobs
	task type. Preserve the approved type through artifact generation; never substitute a
	generic task or silently downgrade pipeline_task, sql_task, or other native kinds.
- Every edge must use task IDs from the proposed task array and at least one exact,
	retrieved canonical control-link evidence ID. A control-link's source/target are the
	original task identities; translate those endpoints to the matching proposed task IDs.
	Never paste original IDs directly as proposed endpoints or leave edge evidence empty.
- Compare at least one evidence-backed alternative, explain optimality under the captured
	constraints, preserve coverage and do not claim globally optimal or runtime-proven.

## Required evidence and recommendations
For ordinary single-workflow conversion, preserve a captured external workflow call
as an informational placeholder. The called workflow is not implemented by the parent
conversion. Do not require a child Job ID, demo mode, or cohort selection; related
workflows may be reviewed and selected separately. Do not infer completion from -nowait.
Do not include child-job or demo parameters for an out-of-scope call. Preserve
source query, transformation and partition semantics from the actual evidence;
mark any genuinely unknown behavior as unverified, not as invented code.
Author submit_redesign yourself. Do not substitute compile_demo/reference output
for model-authored design, invent procedure bodies, or claim seven-mapping parity.

Original sources need ingestion contracts unless injected metadata tools establish
an observed UC candidate. Read uc_metadata_state and inspect exact qualified tables
using XML database/owner/column/query evidence, never equal names as a binding rule.
Record only role-authorized canonical source/target IDs with owned evidence and a
same-run uc_get_table observation ID/hash. This is a metadata-verified candidate,
not semantic proof or human approval. Permission errors remain unknown; qualified
absence is scoped to that lookup. Non-table tasks require no binding. Without a
provider retain unverified scenario assumptions. The UC-source scenario expects source
data in Unity Catalog; verify its identity and availability without assuming it is
synthetic. Suggest bronze/silver/gold destination placement
only when required by the captured workflow outputs. Lakeflow Connect availability and
serverless compatibility remain unverified. Prefer serverless-compatible logical
tasks. No resource spec, executable contents, SQL rendering or production-data access.

## Validation and counterexample
Check ordering, transaction, ownership and side effects before proposing reuse.
Example: retain boundaries pending lifecycle evidence. Counterexample: matching
metadata fingerprints do not justify merging workflows or deleting an external write.

## Evidence traceability
Connect each task, edge, boundary and alternative to the exact role-retrieved source
evidence and coverage it represents. Identify unresolved assumptions and scope limits
in the design rather than treating an inferred boundary as established behavior.