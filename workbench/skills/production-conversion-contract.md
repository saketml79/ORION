# Agent-authored dialect translation

SQL_TRANSLATION_BLOCKED and UNSUPPORTED_MAPPING_IMPLEMENTATION describe the limited
static compiler, not the capabilities of Databricks or permission to stop drafting.
Inspect the original statement, session overrides, ports and target write settings.
The code writer must author the supported PySpark/Databricks SQL implementation;
do not ask the operator to write a "manual equivalent".

For Oracle DELETE FROM table PARTITION(partition_name), Databricks Delta supports
row deletion with a predicate. Recover partition membership from available evidence.
For the explicitly authorized counter demo, declare the assumption that the named
template partition contains exactly rows matching the captured TEMPLATE_ID constant.
After the captured insert, count only those rows and delete those rows when that
count reaches the captured threshold. Do not count the entire table for the reset,
and do not replace the earlier session target-success metric with this later count.
Use the exact session constant and thresholds, never values from sibling workflows.
Generate actual executable notebook code with parameterized, validated UC identifiers.
Oracle COMMIT is not a Spark SQL command: describe the separate Delta commits and
cross-statement concurrency boundary; never claim identical Oracle transactionality.

Unknown runtime parameters require explicit runtime bindings, not invented demo
defaults, production credentials or procedure bodies. Missing source
procedure bodies may prevent parity proof, but do not prevent writing supported task
code. Distinguish generated implementation, declared demo assumptions, external
simulations, static validation and executed parity evidence in the resulting report.

# Shared runtime production conversion requirements

This contract is injected into every review role and the separate controlled writer.
It guides proposals and draft acceptance, not approval, execution or deployment.

## Common evidence-to-deliverable contract

Review roles use the planning tools below. The separate writer uses only its own
fetch_source_task/fetch_sql_phases tools for the frozen design, not review tools.

1. Establish selected-workflow authority first. Read workflow_explanation and batch
  planning overview, tasks, control_links and invocations. Page package_migration_contract
  collections and invocation rows using their disclosed cursors/counts. Follow only
  exact returned caller_workflow_id/candidate_workflow_ids with related_workflow_id.
  Inspect resolution, resolution_reason, wait_mode and repository_binding. Literal
  export matches are candidates, not verified runtime routing. Dynamic names, absent
  children and ambiguous matches stay explicit boundaries; never guess a child ID.
2. Retrieve relevant implementation evidence within the explicitly selected scope.
  In single-workflow mode, an external Informatica workflow call remains a clearly
  identified non-executing placeholder; do not require a child Job ID, demo mode,
  child conversion or cohort selection to generate the selected workflow's code.
  Do not declare a child-job parameter or widget, call the Databricks Jobs API,
  or produce a simulated submission for this placeholder.
  State that the called workflow's notebook is not implemented by the parent.
  Tie a shared-data dependency to exact source/target identity, connector lineage,
  SQL reads/writes and status-table behavior. Shared names, mappings or fingerprints
  alone are not dependency evidence. A -nowait launch does not prove child completion
  or successful data production. Keep invocation, control and data relations distinct.
  Recommend convert-together scope expansion or an explicit external disposition,
  with evidence and tradeoffs. Retrieval permission never expands workflow authority;
  other cohort members need their own consented design/draft run. Missing package
  pages or bodies mean closure is unverified, not silently complete.
3. Inventory each task's behavior: all pre/source/lookup/target/post SQL phases with
  owner, ordinal and session override precedence; expressions, ports, types, nulls,
  writes, transactions, control conditions, failure/skip paths, timers, retry/recovery,
  notifications and commands. For Java inspect structural_source, sql_expressions,
  upstream expressions and parameters together. Enumerate loops, status updates,
  constants, thresholds, timeout equality/bounds, sleep units, outputs and exceptions.
  Preserve observed literals exactly. Masked or truncated values remain unknown;
  never borrow a threshold, task-value key or business rule from the WIL reference.
4. Analysis roles submit typed findings and architecture/supervisor submit a logical
  design only. Honor the injected strict tool argument schema, including nested
  required fields and literals. Every direct original task occurs once in coverage,
  including Start and disabled tasks. Coverage cites its original ID, all citations
  are role-local, and proposed-ID references must agree across every collection.
  Artifact plans are optional, not a prerequisite for a candidate design.
5. Check static syntax, declared types, required fields, DAG endpoints and coverage
  separately from behavior and parity. Return exact field paths and required shapes
  for repairs; do not replace rejected outputs with prose. SQL checks apply to SQL,
  not a requirement to invent SQL for email, waits or commands. Review roles cannot
  execute generated code or certify a static pass before the writer exists.
6. The explicitly requested candidate path hands the frozen submitted design to the
  separate writer for actual notebook JSON, modules/SQL and authored assertion tests.
  The writer stages files and typed metadata incrementally, then calls
  commit_generation_receipts() last with no arguments, not responsibilities in prose.
  Legacy submit_generation_artifact_set remains compatible, not the primary protocol.
  Supported tasks receive code; unavailable behavior receives task-scoped
  explanatory placeholders, not fake implementation or an external side effect.
  Review-only calls do not authorize code. Candidate consent is not execution,
  deployment, artifact approval or independent parity evidence. Keep actual model,
  tool, source-record and design_origin provenance, including deterministic references.

- Retrieve the package graph, inbound/outbound call graph and field/data graph
  before choosing task boundaries. Distinguish shared definitions from actual
  runtime dependencies. Page canonical evidence; missing evidence stays scoped
  and unverified, never guessed or repaired with a reference example.
- Compare evidence-backed alternatives. Choose appropriate supported native
  Lakeflow task boundaries for ordering, transactions, side effects, lifecycle,
  recovery and async calls. No fixed task count. Where behavior cannot be
  translated, preserve the task with a clearly explained placeholder rather than
  preventing other tasks from being designed or drafted.
  Apply DRY only with semantic proof across expressions, overrides, parameters,
  writes and operational contracts. Shared code need not merge schedules/jobs.
- Preserve exact source semantics: ports, types, nulls, duplicates, predicates,
  enabled state, control outcomes, waits, retry/timeout/exception behavior and
  side effects. Keep source/lookup/target overrides and pre/post SQL distinct,
  ordered and owned. Translate complete Java control and data behavior, not
  merely extracted queries. Inventory procedure bodies and transitive calls;
  unavailable bodies or unsupported behavior require explicit blocked items.
- Translate source-system variable references into explicitly named Databricks
  runtime parameters. Preserve source tokens in evidence or documentation only,
  never use them as fallback values in executable SQL, messages or paths.
  Trace declarations, task scope, explicit overrides and every consuming expression
  into the submitted design's parameter purposes. Distinguish task status and output
  values from user-supplied parameters. Give the writer the original variable and
  chosen runtime parameter relationship without assuming a missing parameter file.
  Captured Email tasks require send-capable code with the source recipient, subject
  and body, configured by runtime mail settings and secret references. Missing mail
  configuration is a runtime input, not a reason to simulate delivery or skip code.
  Generation/review must not send email or claim delivery.
- Every generated notebook begins with a source-grounded business overview and
  each code cell starts with a concise comment describing the business logic it
  implements, including relevant rules and input/output or side effects. These are
  documentation for notebook readers, not invented facts or execution approval.
- Derive the session-specific source SQL, target load mode and ordered pre/post SQL
  from the retrieved source, not a prior design or sibling workflow. Preserve
  row multiplicity and compute branch metrics from the correct source phase.
  A similar source/target table name or an unresolved schema variable does not prove
  identical runtime bindings. Preserve a documented source-proven self-reference
  when established; otherwise expose the uncertainty and do not silently force
  read and write tables to match or to differ. Never write to a source snapshot
  merely because it was offered as an input; explicitly describe intended writes.
- Oracle partition names do not prove a predicate without partition-definition
  evidence. If a candidate must approximate unavailable partition membership,
  label that assumption in the affected code, avoid claiming parity and explain
  the data affected. Preserve statement order and disclose transaction, concurrency,
  failure and recovery differences. For generated modules, do not assume the
  Databricks notebook process working directory equals the uploaded bundle root;
  use explicit deployable dependencies or clearly state unresolved packaging.
- Separate input_data_basis from behavior_basis. Synthetic input tests source
  behavior without necessarily mocking it. Behavior substitutions, stubs,
  receipt-only side effects and missing implementations must be disclosed
  independently. Synthetic data is not production evidence; neither is a
  model's claim of source preservation.
- For the selected Unity Catalog source scenario, treat Informatica source inputs as
  available in Unity Catalog for candidate design. Do not report absent live metadata
  queries, unexecuted analysis SQL, or historical connection redactions as missing
  source data. State that the scenario assumption is not a live schema/parity check.
- Analysis tools inspect SQL and do not execute SQL. This does not mean SQL is missing
  or block a logical design or candidate notebook. Preserve captured SQL phases and
  separately disclose a concrete unsupported translation or runtime contract.
- Record source-record, task and artifact translation coverage, including
  explicit blocked work. Missing coverage remains unverified. A source scan,
  syntax pass, authored test or self-reported coverage is not semantic proof.
- Expected results must come from an independent source/specification or oracle,
  not the translated implementation's output. Check full typed multisets, nulls,
  multiplicities, SQL phase order, Java boundaries/exceptions, procedure effects,
  restart/failure paths and source preservation. State oracle limitations.
- Static checks alone cannot establish production readiness. Independent
  execution and parity evidence, resolved bindings, expert review and explicit
  approvals remain separate gates. Never clear those gates through coverage.