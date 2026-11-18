# Backend-only selected review read handoff

## Status and boundaries

Final local verification completed on 2026-11-10: the exact 16-module proposal
gate passed with **350 passed, zero failures/errors/skips, one warning**, in
**750.64 seconds**. Both application and proposal JavaScript syntax checks
passed. No application or test source changed after that passing gate.
The authorized existing-app deployment and authenticated read-only smoke are
tracked separately below. Offline scripted providers are not live inference.

The original complete JUnit report recorded **344 tests, 332 passed, 12 failed,
zero errors/skips**, not 232 tests. All failures were addressed before the
single full rerun. Six added original-scenario actual-XML cases account for
the final total of 350. No test exclusions, lowered gate, production validator
weakening or model/business execution were used.

The requested agentic-blueprint Markdown files and PDF are absent from this
checkout. Existing approval, consent, session-claim, model-write serialization,
diagnostic relevance, and native Jobs condition validation remain authoritative.

## Source-grounded root facts

- The hosted dispatcher imports the shared `hosted_object_reads.ROUTE` allowlist
  before deciding whether a selected GET can bypass full `DeltaRuntime.restore`.
  Proposal, cohort, target-selection and writer-result GETs were absent there.
- The local proposal presenter calls `_Service.proposals`, then `lite_records`,
  then `Store.save_proposals`. Reusing that presenter as a hosted read would
  register provisional rows and read full review event history. The new hosted
  presenter instead overlays verified proposal-version rows without registration.
- Existing redesign GETs already use the selected committed projection. Their
  design/review/document/lesson freshness path is retained, not replaced.
- Draft-result integrity logic was embedded inside the package application.
  It is now shared as a pure file reader, preserving the local response contract
  and avoiding creation of an application or model runtime merely to read output.

These are source/call-path facts, not measured new hosted latency results.
No assertion is made that every platform 429, 502 or 503 is eliminated.

## Exact newly projected selected GETs

The existing dispatcher requires fresh OBO authorization and explicit selected
project and package headers before using these projections:

- `/api/proposals`, including scenario, workflow filter and pagination.
- `/api/proposals/{proposal_id}`, retaining the structural baseline contract.
- `/api/cohorts/{cohort_id}`. There is no new GET collection route.
- `/api/target-selections/{workflow_id}/{target_id}`.
- `/api/jobs/{job_id}`.
- `/api/jobs/{job_id}/spark-drafts`.
- `/api/jobs/{job_id}/completeness`.

Each uses the fresh committed receipt and inventory, verified registry membership,
whole queried package-family inventories, row hashes and exact source identities.
Active package and canonical manifest checks remain required. Canonical and
source-analysis index hydration is used only for presenters that require it.
Writer status, output and completeness reads do not load the canonical graph.

The new family decoder has explicit row/download byte bounds and does not decode
irrelevant supplementary SQLite payload copies. Writer reads validate manifest
checksum and saved input pins before fetching only that manifest's bounded
artifact inventory. Completeness preserves unknown current-input freshness,
or definite design-version staleness, rather than inventing current approval.

No execution-cache restore, audit restore/publication, source XML read, model
client construction or history rewrite is part of the new selected read path.
Invalid committed evidence fails closed, without fallback restoration.
Stale target/cohort evidence stays visibly stale. Missing cohort membership is
an error, not an apparently completed review. Raw warnings/history are not cleared.

POST consent, design approval, session claim and native bundle checks are unchanged.
Native bundle reads remain outside this extension because they use current
approval checks. SQL draft generation and other mutation routes are not reclassified.
`condition_task: null` and unsupported task kinds still block native YAML creation.

## Exact changed files

Production:

- [../workbench/hosted_object_reads.py](../workbench/hosted_object_reads.py): narrow GET allowlist, manifest check and projection dispatch.
- [../workbench/hosted_review_reads.py](../workbench/hosted_review_reads.py): bounded proposal/cohort/target/writer presenters and source checks.
- [../workbench/draft_artifact_reads.py](../workbench/draft_artifact_reads.py): shared pure bounded writer artifact reader.
- [../workbench/app.py](../workbench/app.py): existing draft-result handler delegates to the shared reader.

Tests, included in the passing consolidated gate:

- [../workbench/tests/test_hosted_review_reads.py](../workbench/tests/test_hosted_review_reads.py): local/hosted JSON parity; two concurrent projection entries with existing completed/running jobs and a held model-save writer; exhausted user slots with the same actor; no 409 from valid reads; fresh actor/project/package authority; row corruption and missing rows; source-key consistency; stale saved input pins; incomplete committed cohorts; output reads without canonical hydration; no publication/models/XML/restoration.
- [../workbench/tests/test_review_actual_workflows.py](../workbench/tests/test_review_actual_workflows.py): fresh canonical Lumen parser, actual WIL_NET_DIS, WIL_NET_INS and VYVX definitions in individual and real 32-member cohort modes; deterministic source context; compiled fake-provider architecture tool pipeline with real retrieval/submission validation and cross-role rejection; no-direct-source master fixture boundary; null conditions and unsupported native tasks remain blocked.

The actual-workflow tests do not infer 32 rows by cloning workflows and do not
claim autonomous cohort execution. They submit offline logical outlines through
compiled tools. They are not live-model quality, remote runtime or parity proof.

## Final fixes and verification ledger

- WIL_NET_DIS fake-provider submissions use the actual scenario-specific
  `build_wil_net_dis_candidate`, retrieve every cited canonical evidence ID,
  and retain rejection before retrieval and across roles. All three source
  workflows now run in both original/bronze and workflow/cohort contexts.
- Workflow draft and archive stale-context tests retain disabled busy choices
  and change workflow through the external agent selection context.
- Hiding Proposals during an active draft/download invalidates its epoch,
  aborts requests, clears output and consent, and prevents late responses from
  restoring actionable state. Completed read-only saved views remain retained.
- Hosted fixture tests evaluate actual read classification rather than an old
  source substring. The fake application accepts the actual execution-model
  keyword and asserts it is false. Per-user diagnostic buffers are included in
  failure evidence. The observed 503 was a fixture TypeError at app construction,
  not proof of an authentication failure.
- Agent-history tests explicitly select a workflow before expecting Invoke to
  enable. Exact loaded package hash, pending/error blocking, navigation re-entry
  and absence of overview reloads remain asserted. No model is manually run.

Exact regression command, run once after all fixes, with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

    node --check workbench/static/app.js
    node --check workbench/static/redesign-review.js
    .\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_proposal_primary_ui.py workbench/tests/test_hosted_review_reads.py workbench/tests/test_review_actual_workflows.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_hosted_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_redesign_ui.py workbench/tests/test_run_review_fixes.py workbench/tests/test_hosted_agent_reads.py workbench/tests/test_hosted_model_reads.py workbench/tests/test_hosted_lock_ownership.py workbench/tests/test_cohort_review.py workbench/tests/test_cohort_design_actions.py tests/test_fixture_metadata.py tests/test_proposal_selection_sync.py tests/test_agent_history_scoped_ui.py -q --tb=short --disable-warnings -ra --junitxml="$env:TEMP/orion-proposal-complete-20261110.xml"

## Release status

External allowlist staging, staged syntax/import checks and bundle validation
passed: **127 files**. Deployment was started once after the complete green gate.
Deployment ID, exact times and live read outcomes must come from the release
receipt before this section can claim deployment or smoke success.

## Suggested backend union for the main agent's consolidated regression

Do not run a separate partial batch first. Union these modules with the UI
agent's complete requested regression set, then run one consolidated batch.
No test exclusions or changes to approval flags are proposed.

- [../workbench/tests/test_hosted_review_reads.py](../workbench/tests/test_hosted_review_reads.py)
- [../workbench/tests/test_review_actual_workflows.py](../workbench/tests/test_review_actual_workflows.py)
- [../workbench/tests/test_hosted_object_reads.py](../workbench/tests/test_hosted_object_reads.py)
- [../workbench/tests/test_hosted_workflow_artifacts.py](../workbench/tests/test_hosted_workflow_artifacts.py)
- [../workbench/tests/test_workflow_artifacts_api.py](../workbench/tests/test_workflow_artifacts_api.py)
- [../workbench/tests/test_hosted_agent_reads.py](../workbench/tests/test_hosted_agent_reads.py)
- [../workbench/tests/test_hosted_model_reads.py](../workbench/tests/test_hosted_model_reads.py)
- [../workbench/tests/test_hosted_lock_ownership.py](../workbench/tests/test_hosted_lock_ownership.py)
- [../workbench/tests/test_hosted_operations.py](../workbench/tests/test_hosted_operations.py)
- [../workbench/tests/test_hosted.py](../workbench/tests/test_hosted.py)
- [../workbench/tests/test_cohort_review.py](../workbench/tests/test_cohort_review.py)
- [../workbench/tests/test_cohort_design_actions.py](../workbench/tests/test_cohort_design_actions.py)
- [../workbench/tests/test_target_review.py](../workbench/tests/test_target_review.py)
- [../workbench/tests/test_redesign.py](../workbench/tests/test_redesign.py)
- [../workbench/tests/test_readiness.py](../workbench/tests/test_readiness.py)
- [../workbench/tests/test_agents.py](../workbench/tests/test_agents.py)
- [../workbench/tests/test_runtime_planning.py](../workbench/tests/test_runtime_planning.py)
- [../workbench/tests/test_package_context.py](../workbench/tests/test_package_context.py)
- [../tests/test_fixture_metadata.py](../tests/test_fixture_metadata.py)
- [../tests/test_spark_codegen.py](../tests/test_spark_codegen.py)
- [../tests/test_spark_codegen_api.py](../tests/test_spark_codegen_api.py)

Use the existing workbench pytest configuration. All tests are offline, including
the scripted fake-provider compiled graph. Actual XML parsing and canonical
artifact creation make the real-workflow cases heavier than toy fixtures.
Main must decide separately whether any live inference cost is authorized.