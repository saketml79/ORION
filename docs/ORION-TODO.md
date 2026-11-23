# ORION consolidated TODOs

This is the single working checklist of open work and unverified outcomes, compiled on 2026-11-22 from the current release ledger, source, and earlier investigation receipts. Generated migration code is the deliverable for the user to review and change as needed. Historical incidents are identified as such and should be rechecked before treating them as current defects. Do not start model calls, create cloud resources, execute customer jobs, or deploy without the appropriate explicit authorization.

## 1. Agent improvement and evaluation

- [ ] **Next: investigate run cost and latency, without running it now.** For
	the local `wkf_Load_tblcircuits_temp` run `12a6273b9e004a8f828d1f5f81d49c29`,
	the UI displayed an estimated $54.94 and elapsed 00:31:49 for one drafted
	notebook. Verify the estimate's pricing source and whether it excludes writer
	or compute charges. Trace the actual plan, analysis-agent handoffs, writer and
	validation model calls, tool calls, retries, token usage, durations and source
	retrievals. Assess which calls were justified by evidence, whether the plan
	was reused, and where duplication or cost can be reduced without weakening
	accuracy, approvals or safety. Mark missing telemetry unknown; do not assume
	that one notebook implies a simple workflow or that every call was necessary.
	Do not rerun inference or change routing/budgets as part of this TODO entry.
- [ ] **Automated hill climbing:** Define and implement a bounded code-generation, evaluation, comparison, and selection loop that seeks the best result within the configured time, token, and iteration budget. Preserve run scope, approval boundaries, and a durable trail of versions and reasons. The existing [hill-climb comparison](../workbench/loops.py) compares completed advisory runs; it does not automatically generate and select code versions. The existing writer feedback loop is separate.
- [ ] **Quality measures:** Establish labeled reference cases or calibrated evaluators for groundedness, citation correctness, source-to-target mapping, business behavior, and parity. Operational call counts are not quality scores. See [agent evaluation scorecard](agent-evaluation-scorecard.md).
- [ ] **Named skills:** Evaluate the seven enhanced specialist/writer skills with a fresh, explicitly requested visible run. Verify actual loaded/read receipts and outcomes. Historical generic skill paths remain available; changed skill fingerprints can make earlier designs appear stale. See [release ledger](current-release-verification-ledger.md).
- [ ] **Context and traceability:** Confirm in a fresh run that per-agent guidance, source references, tools, errors, and the separate writer are attributed accurately. Loaded/read records establish access, not whether a model used the information. Improve the writer's own provenance where records do not currently expose it.
- [ ] **Model routing and memory:** Evaluate role-specific model choices and run-scoped memory against workflow-independent cases, including cost, latency, quality, isolation, and governance. Multiple endpoints and additional agents were deferred. See [agent responsibilities and model routing](migration-agent-responsibilities-memory-and-model-routing.md).
- [ ] **MLflow tracing on the current inference route:** Implement payload-minimized MLflow spans and link to a trace only when a real trace ID and experiment are returned. Preserve user-scoped OBO for model access; do not silently substitute shared service-principal permissions for user authority. Resolve the MLflow experiment and supported trace-write authorization before enabling delivery. Current agent tracing is opt-in LangSmith; MLflow tracing is not integrated. No MLflow package, experiment path, or trace-write authorization is configured in this checkout, so no trace link may be shown as live.
- [ ] **Unity Catalog AI Gateway inference migration:** Deferred because it changes the tested model-routing path. Identify the exact Gateway model-service FQN(s), required `ai-gateway` OBO scope and `USE CATALOG`/`USE SCHEMA`/`EXECUTE` grants, then plan migration with no guessed endpoint names or fallback route. The current Serving Endpoint configuration is not an inference test.

## 2. Conversion correctness and deliverables

- [ ] **Native Jobs YAML:** Resolve or re-verify the reported v29 nested email-widget declaration failure, and make any resulting diagnostic actionable. The earlier saved run had `review_ready` results but failed native YAML generation. See [local visible agent consistency](local-visible-agent-consistency-2026-11-16.md).
- [ ] **Source and destination bindings:** Obtain and review evidence for exact source and target identities independently. The earlier counter-table investigation did not establish an approved target in the searched catalogs; matching names or schemas are not approval or proof. See [release ledger](current-release-verification-ledger.md).
- [ ] **Workflow semantics:** Independently review and validate SQL phases, expressions, Java/procedures, conditions, nulls and precision, parameter precedence, side effects, transactions, recovery, and asynchronous calls. Track unsupported or missing behavior by affected task, without inventing defaults.
- [ ] **Complete source coverage:** Verify the full selected workflow and relevant dependencies are inspected without silent truncation. Preserve task ownership and evidence references across design, writer, and review.
- [ ] **Parameters and notebook explanations:** Verify source variables map to the correct task-scoped runtime parameters and that generated notebooks explain their business purpose and each cell's logic from actual evidence.
- [ ] **Candidate quality and parity:** Inspect and test generated notebooks and native Jobs YAML against independent expected results. Static validation, agent approval, generated files, and fixture success do not alone prove business or runtime equivalence.
- [ ] **Notifications and external actions:** Verify generated notification behavior with explicitly configured mail settings and authorized test execution; preserve single-workflow external-call boundaries. No email or customer job was executed as part of the cited review.
- [ ] **Legacy compiler gaps:** Review the explicit SQL column-inference placeholder and primary-key metadata hint in [SQL generation](../archived/legacy-compiler/codegen/sqlgen.py) and [mapping IR](../archived/legacy-compiler/ir/mapping_ir.py), and assess their actual use before changing supported paths.

## 3. Hosted execution, durability, and performance

- [ ] **Live hosted run:** Verify a fresh authorized run displays accepted status, agent activity, skill/context receipts, timing, terminal outcome, and final Delta confirmation. Hosted UI/live behavior has not been independently established by local checks.
- [ ] **Durable progress during long runs:** Design and validate consistent background checkpoints or incremental commits if required. The current final snapshot is committed after worker drain; a pending label alone does not prove a Delta write is stalled. Preserve user-scoped OBO, serialized publication, and recovery semantics. See [hosted Delta authority](hosted-delta-authority.md).
- [ ] **Hosted code-generation failure recovery:** Validate the new phase/counter receipts for writer model calls, tools, staged files, validation and final manifest, and confirm a final Delta `PermissionDenied` exposes the exact safe resource and diagnostic ID. A prior pasted 503 showed final Delta commit denied on table metadata verification; no run result should be retried until the committed state is reconciled. The UI does not auto-cancel potentially long provider/tool calls.
- [ ] **Latency:** Measure the actual hosted project/XML, workflow, navigation, and agent-status paths rather than extrapolating from local performance. Identify repeated authorization, metadata, or restore work before changing caching. The Apps proxy's request limit remains separate from application behavior. See [hosted object latency investigation](hosted-object-latency-investigation.md).
- [ ] **Live cost:** Trace the Databricks cost API integration and verify the hosted display for the selected run and endpoint. Do not substitute public list prices for actual workspace cost data.
- [ ] **Reconciliation:** Establish the outcome of prior ambiguous operations before any retry or replay. An accepted in-process run is not proof of final committed results.
- [ ] **Security and isolation:** Verify same-user and cross-user isolation, current OBO authorization, per-request scope, concurrent reads/writes, and behavior after restart. Do not substitute service-principal identity for user authority.

## 4. User experience and operational transparency

- [x] **Support, AI review, local implementation:** One explicit consent and
	confirmation sends bounded recorded findings and analysis activity to the
	already-selected validation route. Available analysis token/list-price
	estimates are labeled as estimates; writer and review-call charges remain
	unknown. Opening Support never invokes a model. The action is not deployed or
	exercised with a live model.
- [ ] **Support, human review, shared access:** The local app has a durable
	request endpoint, per-user Volume staging, and an approver-only queue/decision
	view. Shared submission requires a Unity Catalog group, but the workspace
	`users` and `admins` names are not recognized by the selected UC principal
	API. No grant was applied. A private staging receipt is not a queued request.
	The designated initial approver is
	`ssuman@bpcs.com`; verify their identity from the current authenticated OBO
	principal, not a client-supplied name. The user selected the existing
	`orion_dev_ssuman` catalog. Managed Volume
	`orion_dev_ssuman.workbench.orion_support` now exists (ID
	`807d1ec5-4714-450d-a480-b647571d72cf`), but has no verified sharing grants
	or application sharing. No service-principal fallback is used.
- [ ] **Skill submissions:** The local app now validates bounded UTF-8 Markdown,
	stages it to the requestor's own Volume, supports approver vetting and stores
	promoted skills in a separate read-only-intended registry Volume. Uploaded
	instructions never execute. Shared queue submission and other-user reads are
	blocked until a valid Unity Catalog group is named and granted access.
	`orion_dev_ssuman.workbench.orion_support_submissions` is the pending Volume;
	`orion_dev_ssuman.workbench.orion_support` is the approved library.
- [x] **Official documentation search, local implementation:** The Support
	page can, after
	explicit per-search consent, match URL-path tokens in official sitemaps and
	read at most two matched pages. This is not full-text search, and the current
	Databricks source is AWS documentation. Informatica was unavailable in the
	local check. A separate unchecked per-run consent now exposes it to analysis
	agents and the writer, with a two-page/three-query run budget, source-identifier
	filters, URL/hash receipts and untrusted-content disclosure. This integration
	has not been exercised by a model run; do not infer use from availability.
- [ ] **Application log analytics:** The local tab shows current-process CPU,
	memory and I/O samples, a 50-row audit sample and analysis-model calls for a
	selected saved run; the app/unattributed filter is also available locally.
	Hosted behavior is unverified. Complete genuine app-wide endpoint and writer
	metrics, durable retention and fleet visibility with explicit access controls.
	Do not present the sampled view as enterprise-grade or invent missing values.
- [ ] **Writer limits and complete evidence:** The local writer can now retrieve
	oversized source records in ordered chunks and the per-file content limit is
	aligned to the existing aggregate byte limit. The aggregate artifact cap and
	other text, identifier and model-context bounds remain unchanged. Establish
	measured, end-to-end safe limits and prove large-workflow behavior before
	claiming those remaining limits have been increased or are justified.
- [ ] **Hosted user review:** Review the deployed agent communication graph, labels, animated arrows, skills, context panel, typography, and disclosures with real hosted data. The graph illustrates configured flow; recorded activity must remain distinguishable.
- [ ] **Live and historical states:** Check loading, empty, stale, permission, failure, cancellation, and partial-result views. Verify the elapsed timer through the transition from analysis to candidate writing.
- [ ] **Project selection:** Confirm the XML list and neighboring project card remain stable during hosted loading, and verify project creation feedback without altering existing project data unnecessarily.
- [ ] **Responsive and accessibility:** Check keyboard navigation, focus, reduced motion, narrow/mobile layouts, light/dark themes, labels, wrapping, graph scroll/zoom, and browser compatibility. The local view has existing CSS compatibility advisories.
- [ ] **Notebook controls and downloads:** Verify selected task descriptions, exact notebook paths, download contents, and saved-code/review/run controls in the actual app. Confirm below-YAML paths where applicable.

## 5. Notebook editing and delivery

- [ ] **Edit-return workflow:** Complete the consolidated regression for app-managed notebook edit, preview, durable save, revision history, and cleanup. Live hosted OBO roundtrip and cleanup were deferred to the user. Preserve originals and reconcile uncertain cleanup; see [notebook edit and return](notebook-edit-return.md).
- [ ] **Delivery automation:** Finish and independently validate the intended GitHub/CI/CD and protected environment promotion path. Keep code generation, human review, deployment, scheduling, and execution as separately authorized decisions.
- [ ] **Runtime dependencies and connections:** Verify source access, authentication, required parameters, network connectivity, and Databricks bindings in the target environment. XML connection inventory alone is not a live connection test.

## 6. Verification and governance

- [ ] **Regression pass:** Complete the requested comprehensive regression passes and record exact commands, failures, skips, and environment limitations. The user currently requested avoiding `test_*.py` files; do not run them unless that instruction changes.
- [ ] **Fresh visible conversions:** Run and inspect authorized, workflow-independent end-to-end examples in the actual shared app when requested. Preserve receipts; do not change a running prompt or infer parity from prior drafts.
- [x] **Architecture references:** The repository contains design and agent
	responsibility documents. They are context, not a prerequisite for the Support
	features requested here.
- [ ] **External skills:** Finish and verify task-specific, on-demand access to
	the downloaded Anthropic and installed Databricks skill catalogs, including
	actual per-run read receipts, attribution, packaging and documented boundaries.
	Skills do not gain shell, credential or deployment authority. Existing local
	Informatica migration skills remain separate. Do not equate available, loaded,
	read and reasoned-with; older runs cannot acquire new historical receipts.
- [ ] **Review this checklist:** Reconcile items against new evidence, owners, and user priorities after each substantial change. Keep completed items distinguishable from unmeasured outcomes.

## Sources and interpretation

- [Current release verification ledger](current-release-verification-ledger.md), [hosted workbench](databricks-hosted-workbench.md), [agent engineering review](agent-engineering-eight-source-review-2026-11-17.md), and [local visible agent consistency](local-visible-agent-consistency-2026-11-16.md).
- Earlier reports can describe superseded deployments or local-only fixes. Verify current source and deployment before using an older issue as evidence of a current defect.
