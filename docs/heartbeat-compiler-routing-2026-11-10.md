# Local heartbeat and compiler routing correction

## Confirmed roots

- The previous browser page ID was unavailable. A new localhost page opened with no workflow selected and no saved localStorage selection. It cannot confirm the screenshot's selected workflow.
- GET health confirms local loopback authentication (`not_implemented`). The heartbeat asset previously posted on trusted interactions without checking mode or capability. No local heartbeat endpoint was added.
- GET overview identifies VYVX as `wkf_Load_AE2E_VYVX_HIST_PREP_PULL`, canonical ID `9d9f0522b3776b460514ba513ccb4bb4e7698f2fa22a3e374ef2edf2acf73820`.
- Offline compilation of the actual saved canonical package `08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba` originally rejected VYVX with `Unsupported reference outside reviewed scope`. VYVX is not a supported example; support validation now precedes scope traversal.
- All four reviewed wrappers in that saved artifact reject with `Unsupported or changed scoped semantics; review required`. No XML reparse, activation, pin replacement, or guard relaxation was performed.

## Changes

- Heartbeat requires confirmed `databricks_obo` and `heartbeat_enabled: true`. Parent health events are shared; an independent adapter can make one finite GET probe. Disabled/local mode remains cached across click, scroll and visibility changes. Hosted trusted interaction throttling and no-idle-write behavior remain.
- The static example controls use the server's generation capability membership and scenario. Unsupported choices cannot submit, even through a synthetic click. Examples remain under advanced disclosures and are labelled static, not agent-generated.
- HTTP 422 retains its original detail body and adds `X-Workbench-Diagnostic`. Incompatible saved analysis is explicitly labelled and repeated generation is blocked until a fresh context review, without rebuilding or changing pins.
- The primary cohort action reads selected evidence, opens an unchecked explicit-consent dialog, saves a real scope if needed, and starts the existing local or hosted design worker. Confirmed review IDs are remembered under project/package/workflow/scenario/scope session keys. Reconnect reads and validates that exact record; no newest-record inference or fabricated ID is used.
- Agent-page cohort submission carries the selected scenario and opens this same consent-gated flow. Existing model availability, confirmed idle status and consent gates remain.
- Exact outline approval, artifact-model drafting, supported writer task types, native graph/notebook renderer, runtime bindings and independent execution/deployment gates remain separate. This is not general conversion, automatic group consolidation, or verified parity.

## Verification

All edits preceded the consolidated regression. Initial 553 outcomes: 544 passed, one failed, seven fixture setup errors, one Windows symlink skip. Corrections were test-only: advance the installed virtual clock for the one health probe, and close the existing terminal progress dialog before reconnect.

The identical final 16-module batch: **552 passed, 0 failed, 0 errors, 1 Windows symlink skip**, JUnit time **776.557 seconds**, process exit **0**. No exclusions or deselections. Three dependency/existing marker warnings remain.

Command: `.venv/Scripts/python.exe -m pytest workbench/tests/test_heartbeat.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_hosted_workflow_artifacts.py workbench/tests/test_redesign_ui.py workbench/tests/test_proposal_primary_ui.py workbench/tests/test_cohort_review.py workbench/tests/test_cohort_design_actions.py workbench/tests/test_hosted.py workbench/tests/test_hosted_intake_ui.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_native_job_draft_conditions.py workbench/tests/test_review_actual_workflows.py workbench/tests/test_source_analysis.py tests/test_spark_codegen_api.py tests/test_proposal_selection_sync.py -q --junitxml=<TEMP>/orion-heartbeat-routing-final-20261110.xml`.

Python AST checks: seven edited files pass. JavaScript syntax checks: four edited assets pass. Relevant editor diagnostics and shared compiler-response Pylance diagnostics are empty.

No deployment, backend restart, live inference, Databricks Jobs submission, catalog mutation, source reanalysis, or saved-analysis activation. Tests use offline HTTP/model/worker providers. Required agentic-blueprint files and PDF are absent in this checkout; the existing proposal-first design document was consulted.