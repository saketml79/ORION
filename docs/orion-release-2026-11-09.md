# ORION combined release, 2026-11-09

## Scope and pre-deployment ask ledger

User explicitly authorized test and deployment to the existing ORION app. This
release does not authorize production migration, inference, SMTP or a new demo
execution. All code changes precede one consolidated regression gate. Results
below must be completed from actual commands, not assumed from earlier sessions.

| Ask | Implementation and verification gate |
|---|---|
| Desktop sidebar below header, all 11 controls visible | Desktop-only sticky positioning, no outer border, compact navigation; actual Chromium geometry at 1024/1440 widths, 600/720/768/900 heights, both themes, before/after document scrolling. Mobile remains natural-flow grid at 390/900 widths. |
| Validate icon | Existing checkmark retained, covered by sidebar regression. |
| Agent activity four steps | Existing scoped local changes included. Agent activity remains the page title; Select agentic model endpoint is its model section only. Workspace URL, independent metadata discovery and real indeterminate progress are tested without inference. |
| Capabilities | Existing versioned 15-group inventory and honest assessment date included. Unsupported and configured-but-unverified behavior remain distinct. |
| Reference resolution | Fresh parser fixes 56 nested-task references, verified by real-source regressions. Historical immutable canonical artifacts are not overwritten or claimed activated. No approved versioned canonical reingest is performed by this release. |
| Architecture knowledge | Existing NetworkX/UC-backed retrieval, GA-default filtering, preview opt-in and citation evidence included. Prior authorized UC publication has a separate receipt. No new curation, inference or business validation is implied. |
| Latest lineage trace | Existing canonical trace retained; opt-in VYVX reference now has timestamped historical MOCK proof and a real Job link. Original-task correspondence remains absent rather than guessed. |
| One working end-to-end example | Independently verified existing fresh and resume Job runs, full source checksum preservation and seven empty destination readbacks. No duplicate execution submitted. Synthetic example only, not general agent conversion or Informatica parity. |
| Hosted release | External allowlist stage, exact-byte verification, syntax/import checks, bundle validation, consolidated regression, existing-app deployment, fresh authenticated smoke and static-byte verification required. No extra resources/scopes or identity fallback. |

The requested agentic-blueprint Markdown/PDF files are absent in this checkout.
The existing proposal-first plan preserves durable history, evidence-driven
review and separate policy gates.

## Independently verified VYVX evidence

At **2026-11-09T22:57:34.525706+00:00**, fresh labs-user read-only checks verified:

- [MOCK Job](https://adb-1866518241053589.9.azuredatabricks.net/jobs/878190924157330), fresh run **133215706652645**, resume run **936913853370412**, both terminal SUCCESS.
- All eleven fresh tasks succeeded. Both resume prep-load tasks were EXCLUDED.
- Seven source relations still match original typed fixtures and cleanup source
  snapshots. BTP has nine rows and 53 columns; VBUS has ten rows and 39 columns.
- All seven destination/log/receipt tables currently contain zero rows.
- Historical proof before cleanup contains five silver BTP and three gold VBUS
  output rows, checked independently as complete typed multisets.
- Fresh proof SHA256: 7550105526d6faff7651e7a88e67e23f7c34363e89fac36b28afd11e3b9ef4c2.
- Resume proof SHA256: 206d7fc6b039947c825c010b19408d3e5f30148c37727330e9975e92977b4a91.

The current check issued metadata/SELECT reads only. It did not resubmit, recreate,
truncate, seed, grant access or modify any source/destination table.

## Where to see and run the example

In ORION, select a ready package, open **Readiness**, choose **Load selected
workflow**, then **Trace source → Databricks** and **Illustrative mock example**.
The text alternative includes a runnable MOCK Job link, last verification time,
proof hashes and explicit rerun instructions. This is separate from the selected
user/package's saved designs and runtime status.

The Job's **Run now** uses the pinned **ack=MOCK_ONLY**. Its fixture date remains
2026-11-07. Manual reruns retain outputs for inspection; another run exercises
completed-log resume. Only orion_bronze_test_ssuman, orion_silver_test_ssuman and
orion_gold_test_ssuman, each under vyvx_mock_demo, are part of this demo. Email is
a receipt, not SMTP. This document does not claim general automatic conversion.

## Release results

### Consolidated regression and staging

- Final unchanged 50-module batch: **1,294 passed, six subtests passed, one
  existing ImportBody schema-shadow warning, zero failures/errors/skips**, in
  **1403.10 seconds**. Final batch ran after all application code edits.
- Initial batch: 1,289 passed, five stale assertion failures. Four expected the
  old package-locked Agent activity navigation; one expected old unknown example
  status. Assertions were aligned to the approved changes and strengthened to
  retain disabled assessment and false-parity gates. No production change.
- Second identical batch: 1,293 passed, one 180-second sample-ingestion fixture
  setup timeout. Neither timeout nor fixture/application logic was changed.
  The third identical full batch passed. This is not a claim that the first or
  second invocation was green.
- External staged package: **119 entries**, **72 Python AST checks**, **18
  JavaScript syntax checks**, **nine isolated imports**, all passed. Every staged
  workbench file matched source bytes. `databricks bundle validate -t dev
  --profile labs --output json` passed.
- Editor checks: changed Python/JavaScript files clean. Existing CSS browser
  compatibility advisories remain, no new sidebar diagnostic.

Exact test invocation uses the workspace venv, `-B -m pytest -c
workbench/pytest.ini`, the following 50 module paths, and `-q --tb=short
--disable-warnings -ra` (plus an external temporary JUnit report):

    workbench/tests/test_capability_status.py workbench/tests/test_reference_diagnostics.py
    workbench/tests/test_app.py workbench/tests/test_understanding.py workbench/tests/test_qualified_resolution.py
    workbench/tests/test_explanations.py workbench/tests/test_package_context.py workbench/tests/test_migration_planning.py
    workbench/tests/test_source_evidence.py workbench/tests/test_source_analysis.py
    workbench/tests/test_hosted.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_hosted_package.py
    workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py
    workbench/tests/test_package_parity.py workbench/tests/test_demo_conversion.py
    workbench/tests/test_validation_contracts.py workbench/tests/test_validation_campaigns.py workbench/tests/test_campaign_artifacts.py
    workbench/tests/test_architecture_knowledge.py workbench/tests/test_knowledge_store.py
    workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_runtime_planning.py
    workbench/tests/test_readiness.py workbench/tests/test_hosted_readiness.py
    workbench/tests/test_delta_runtime.py workbench/tests/test_hosted_operations.py
    workbench/tests/test_workspace_access.py workbench/tests/test_hosted_model_reads.py
    workbench/tests/test_databricks_models.py workbench/tests/test_model_workspace.py
    workbench/tests/test_requested_ui.py workbench/tests/test_ui_structure.py workbench/tests/test_agent_arena.py workbench/tests/test_loop_ui.py
    workbench/tests/test_migration_lineage.py
    tests/test_fixture_metadata.py tests/test_package_context_visible_ui.py tests/test_explorer_graph_ui.py
    tests/test_readiness_ui.py tests/test_vyvx_mock_demo.py tests/test_migration_lineage_ui.py
    tests/test_navigation_prerequisites.py tests/test_hosted_startup.py tests/test_loading_diagnostics_ui.py
    tests/test_next_action_guidance.py tests/test_intake_loading_controls.py tests/test_sidebar_layout.py

### Deployment and live smoke

Recovered original background execution UUID:
**337c3120-ff0c-4ac6-a5c2-bf5c5e1189da**. No duplicate deployment or regression
was launched during recovery.

- Existing app: **orion-migration-workbench**, app ID
  **cfada07d-ffa6-4018-9080-293f5e577083**.
- Deployment: **01f1dc9cb79911cfa2599500ac71e337**, **SUCCEEDED**;
  app **RUNNING**, one running instance.
- Release command window (UTC): **2026-11-10T00:16:54.796905+00:00** to
  **2026-11-10T00:24:42.794567+00:00**.
- Platform deployment window (UTC): **2026-11-10T00:21:47Z** to
  **2026-11-10T00:24:33Z**. The Nov 9 release completed after midnight UTC.
- Original helper verified unchanged app ID, six requested user API scopes,
  minimum/maximum instance counts of one, and no attached app resources.
- Actual sequence, using the existing external allowlist stage and labs profile:
  `databricks bundle deploy -t dev --force-lock --profile labs --output json`,
  `databricks apps stop orion-migration-workbench --profile labs --output json`,
  `databricks bundle run -t dev workbench --profile labs --output json`, then
  read-only `databricks apps get orion-migration-workbench --profile labs --output json`.

### Recovered authenticated HTTP smoke: PASS

The original command completed with **LIVE_SMOKE=PASS** at
**2026-11-10T00:26:10.618880+00:00**. Its saved JSON evidence confirms **34 HTTP
requests, all 200**, including **28 static assets byte-for-byte identical** to
the staged release. No smoke, test or deployment was rerun during recovery.

App URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com

| Verified GET route | HTTP | Recorded seconds |
|---|---|---|
| /api/health | 200 | 1.522 |
| / | 200 | 0.214 |
| /api/models/workspace | 200 | 0.157 |
| /api/models/status | 200 | 15.680 |
| /api/readiness | 200 | 36.528 |
| /api/readiness/lineage?example=true&family=trace&limit=1 | 200 | 24.903 |

Health reports **status=ok**, **local_only=false**, and
**authentication=databricks_obo**. Capability inventory version is
**capability-inventory-v1**, assessed at
**2026-11-10T00:24:49.530653+00:00**. Its 15 scoped implementation groups do not
certify general conversion, model inference or production execution.

Workspace metadata reports
**https://adb-1866518241053589.9.azuredatabricks.net**, **mode=obo**,
**profile=null**, **auth_check=verified**, **connected=false**. Model status
reports no active endpoint, no configured roles, **inference_ready=false**,
**tool_calling_verified=false**, and **runtime_verified=false**. Saved routes
and explicit-click endpoint discovery are not inference evidence.

Readiness statistics, observed at **2026-11-10T00:25:47.011557+00:00**, are
**243 total workflows, 243 structurally analyzed, zero agent-assessed, zero
logical drafts and zero generated-draft receipts**. Executable, runtime-validated
and output-parity-approved counts remain **null**, not zero or verified success.
The business release gate remains **BLOCKED, 0/4 verified**, with
**production_ready=false**, **execution_allowed=false** and
**deployment_allowed=false**. These counts came from /api/readiness; no separate
statistics endpoint was called or verified.

Readiness and lineage requests used project
**84b03a652f5e4371ba1c72d401acba6b** and package
**08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba**.
The illustrative example is **historical_mock_success**; the selected workflow's
runtime remains **unknown**. Neither is a new Job execution or parity approval.

Integrated browser access redirected to Databricks sign-in. Signed-in browser
interaction is therefore not claimed. The previously observed browser redirect
was at **2026-11-10T00:26:51Z**, separate from the successful authenticated HTTP
smoke. Offline Chromium tests verify interactive layout/trace behavior;
authenticated HTTP checks verify deployed routes/assets.

### Owned temporary cleanup: PASS

After confirming the original command had finished and both saved regression
and smoke gates had passed, the existing helper's **cleanup** mode ran once,
synchronously. It reported **OWNED_EXTERNAL_TEMP_CLEANUP=PASS**. Independent
absence checks reported **CLEANUP_ABSENCE_VERIFIED=PASS** at
**2026-11-10T00:28:35.2236834Z** for its external staged directory, helper,
state JSON and temporary JUnit report. Evidence was transcribed above before
cleanup. No repository artifacts or deployed resources were deleted.

No tests, deployments, smoke retries, model inference, UC business writes or Job
reruns were initiated during this completion step. No application source was
changed during release recovery.

## Capability implementation palette deployment, 2026-11-10 UTC

The user explicitly authorized a single deployment of the current capability
palette changes to the existing ORION app. Scope was limited to:

- `workbench/static/app.css`
- `workbench/static/app.js`
- `workbench/tests/test_capability_status.py` (verification only; tests are not
  included in the deployed package)

The UI now colors implementation status separately from runtime validation. It
does not imply that every capability is complete or that runtime validation,
general conversion, model inference, production readiness, or execution is
approved.

### Regression and staging gates

- The already completed focused changed-path suite had **19 passed** and JavaScript
  syntax passed. Per the user's instruction, no tests were rerun during this
  deployment.
- The external allowlist stage contained **119 manifest entries**. Every staged
  workbench source/static file matched its local source bytes; the test module was
  excluded. Generated app configuration had no unresolved placeholders.
- Staged checks passed: **72 Python syntax checks**, **18 JavaScript syntax
  checks**, **nine isolated imports**, and `databricks bundle validate -t dev
  --profile labs --output json`.
- Capability implementation labels and distinct runtime-status style markers were
  present in the staged assets before upload.

### Existing-app deployment

- App: **orion-migration-workbench**, stable app ID
  **cfada07d-ffa6-4018-9080-293f5e577083**.
- Deployment ID: **01f1dca1b40510b6a9bc72ab3cd65c23**,
  **SUCCEEDED**; app **RUNNING** with one instance.
- Release command window (UTC): **2026-11-10T00:53:36.8698720+00:00** to
  **2026-11-10T01:00:21.6737894+00:00**.
- Platform deployment window (UTC): **2026-11-10T00:57:29Z** to
  **2026-11-10T01:00:25Z**.
- Bundle upload: **120 files uploaded, zero deleted; zero resources created, one
  existing app changed, zero resources deleted**.
- The six requested user API scopes, platform-provided IAM scopes, no attached
  resource array, and minimum/maximum instance counts of one were preserved.
- Workflow was the existing allowlisted external stage, bundle validation,
  `databricks bundle deploy --profile labs -t dev --force-lock --output json`,
  `databricks apps stop orion-migration-workbench --profile labs --output json`,
  and `databricks bundle run -t dev workbench --profile labs --output json`.
- URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com

### Authenticated, read-only smoke

The labs-profile access token remained in process memory and was not emitted.
Three authenticated GETs passed: `/api/health`, `/static/app.js`, and
`/static/app.css`. The health response reported `status=ok`, `local_only=false`,
`authentication=databricks_obo`, and inventory version
`capability-inventory-v1` with **15 groups: 14 implemented_scoped and one
configured_unverified**. This is not a runtime or overall-completion claim.

Both deployed static files were byte-identical to the staged and local files.
SHA-256:

- app.js: `91b67912871185fb40d273b75828a8cf87978a61f52a8f71b774b1f6d02c6982`
- app.css: `0a4b5a266ec018cbc237fa545896f4a9776585ff216b2982604c7fe4722bdb0d`

The JavaScript response contained the separate “Implemented, scoped”,
“Configured, unverified” and “Runtime validation is separate” markers. The CSS
response contained the matching implementation, verified-runtime and local-only
status classes. No browser interaction, model inference, readiness/private-data
request, Job, catalog, schema, or business-data operation was performed.

Owned external stage, syntax scratch, and temporary verifier scripts were removed
after recording evidence. No repository-local temporary files were created or
deleted, and no existing docs or user files were removed. No Git push or sync was
performed.

## No-tests endpoint-apply deployment, 2026-11-10 UTC

The user explicitly requested deployment without running tests. The previously
completed focused regression result was **124 passed**; it was not rerun here.
No pytest or other test suite was run during this deployment.

### Scope and pre-deploy checks

- The requested frontend update was limited to the endpoint-Apply flow in
  `workbench/static/app.js` and `workbench/static/workspace-access.js`. The
  allowlisted stage included the complete app source, but no test files or other
  workspace data. All staged workbench files matched their local source bytes.
- External stage manifest: **119 entries**, with **zero test/cache files**.
  Staged validation passed: **72 Python compile checks**, **18 JavaScript
  syntax checks**, and `databricks bundle validate -t dev --profile labs
  --output json`.
- Immediately before deployment, the existing app ID and active deployment
  matched the requested baseline. The six requested OAuth scopes, eight
  effective scopes, empty resource array, and one minimum/maximum instance were
  verified. The same properties were rechecked after upload and after deploy.

### Existing-app deployment

- App: **orion-migration-workbench**, ID
  **cfada07d-ffa6-4018-9080-293f5e577083**.
- New deployment ID: **01f1dca476e11faba3db9b229207ab34**, **SUCCEEDED**;
  app **RUNNING** with one instance.
- Bundle upload window (UTC): **2026-11-10T01:13:19.6152932Z** to
  **2026-11-10T01:13:35.3955103Z**.
- Stop/run command window (UTC): **2026-11-10T01:13:46.1158797Z** to
  **2026-11-10T01:20:18.8178113Z**. Platform deployment window (UTC):
  **2026-11-10T01:17:15Z** to **2026-11-10T01:20:19Z**.
- Bundle reported **120 files uploaded, zero deleted; zero resources created,
  one existing app changed, zero resources deleted**. The requested/effective
  scopes, empty resource array, and single-instance settings were preserved.
- The one deployment used the existing external allowlist stage, bundle
  validation, `databricks bundle deploy -t dev --force-lock --profile labs
  --output json`, `databricks apps stop orion-migration-workbench --profile
  labs --output json`, and `databricks bundle run -t dev workbench --profile
  labs --output json`.
- URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com

### Authenticated, read-only smoke

The cached labs-profile OAuth token remained in process memory and was not
printed. Authenticated GET `/api/health` returned **200** with
`status=ok`, `local_only=false`, and `authentication=databricks_obo`. Both
changed JavaScript assets returned **200** and were byte-identical to the
staged files:

- `app.js`: SHA-256
  `8fe3f38c527152e039ca5e615ee430766db24847255f23f19a3cf3e8d309607f`.
- `workspace-access.js`: SHA-256
  `88a41f3a47d584b0cb513d88aba23364165d4a20f210ff38959353664b0bead5`.

Deployed asset inspection confirmed the `orionModelRouteConfigured` callback
and the single intended `/api/models/select` POST in the Apply handler, with no
global-refresh click in that handler. The smoke did not click Apply or issue any
POST, model inference, Job, private-data, catalog, schema, or business-data
request. Owned external staging and syntax scratch directories were removed
and their absence verified. No Git push or sync was performed.