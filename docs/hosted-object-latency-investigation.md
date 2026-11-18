# Hosted object-read latency: measured root cause

## Subsequent scoped implementation authorization

The user subsequently authorized “fix what is remaining”, including the proposed
Option B committed-generation read projection and explicit saved-source analysis
rebuild. The earlier unapproved/read-only statements below describe the historical
investigation, not the later authorization. See
[the implementation and coordinated release handoff](hosted-scoped-read-analysis-rebuild.md).
No deployment or new hosted-latency measurement is performed by that workstream.

## Decision and scope

Investigated the app following release `01f1d7fc22361c28883c72b52170ed01`, recorded in [the release ledger](orion-hosted-release-2026-11-04.md). The user requested root-cause investigation and intuitive UI. No A/B/C implementation choice has been approved in the parent conversation. No application/UI code, environment flags, services, deployment, model routes, business Jobs or data were changed during this investigation. This document is the only workspace change from this investigation.

The saved agentic-blueprint directory/PDF is still absent. The available [proposal-first plan](proposal-first-deepagents-lakeflow-plan.md) and [Delta authority design](hosted-delta-authority.md) were consulted. Older design documents describe historical audit-publication behavior; current code and observed calls determine this latency diagnosis.

## Successful live canonical requests

Fresh labs-user OAuth through the existing Apps endpoint returned `authentication=databricks_obo`. Credentials stayed in memory and were not printed. Selected committed scope:

- Project: `84b03a652f5e4371ba1c72d401acba6b`.
- Package: `08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.
- Object: `00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38`, `wkf_AE2E_DUMMY_CALL_WIL_NET_DIS`.
- Exact route: `GET /api/object/{object_id}`, with explicit project/package headers.

An unpolled request returned **HTTP 200 in 43.584 seconds**, exact selected node ID, **175,169 response bytes**. This independently reproduces the release ledger's 42.44-second observation.

A second successful request used the existing bounded per-user loading diagnostics. It returned **HTTP 200 in 44.462 seconds**, with **43.519 seconds server diagnostic elapsed** and **16/16 files verified**. Phase transitions were recorded from the server's `phase_started_at`, not estimated percentages:

| Actual phase code | Duration to next phase | Meaning |
|---|---:|---|
| `provisioning` | 1.763s | Resolve owned user catalog/schema/Volume |
| `metadata_restore` | 21.642s | Verify receipt/manifest and restore committed Delta records |
| `artifact_files` | 19.740s | Download, verify and reconstruct the active runtime files |
| `preparing_package` | 0.371s | Prepare/serve selected package view, including response buffering/closure |
| `responding` | Terminal boundary | Verified result returned |

The warehouse was observed **RUNNING** during this successful traced request. These are sample measurements, not an SLA or exhaustive concurrency benchmark. Diagnostic sampling made 151 sequential observations, so the unpolled request is the cleaner total-latency measurement. Do not copy that diagnostic sampling frequency into production polling.

An initial diagnostic probe accidentally used the plural `/api/objects/{id}` route and returned 404 after 54.269 seconds. It also observed the warehouse move from STOPPED to RUNNING. It is **not** a successful object-read measurement. The singular route was checked in source and both successful requests above used it. Even the unknown plural route incurred runtime restore, another consequence of dispatching through the general restore boundary before route resolution.

## Dominant root cause: read amplification

[The dispatcher](../workbench/hosted.py) sends ordinary object GETs through `DeltaRuntime.restore(initialize=False)` before the selected package route executes. [The runtime](../workbench/delta_runtime.py) restores the **entire active committed generation**, not just the requested object/package view.

The separate `restore_audit()` tail overlay is already skipped for ordinary object GETs. However, `_records()` still reads the audit rows embedded in the committed content generation, and `restore()` reconstructs their SQLite files. Skipping the tail overlay did not remove this baseline audit cost.

Checksum-verified active inventory established:

| Delta table | Committed rows | Scopes | Record-page SELECTs |
|---|---:|---:|---:|
| `artifact_manifest` | 16 | 3 | 3 |
| `audit_events` | 493 | 2 | 11 |
| `jobs` | 2 | 2 | 2 |
| `model_routes` | 1 | 1 | 1 |
| `packages` | 2 | 2 | 2 |
| `projects` | 1 | 1 | 1 |
| `settings` | 2 | 2 | 2 |
| **Total** | **517** | **13** | **22** |

Eleven of the 22 page requests exist to restore baseline audit history. These counts exclude receipt lookup, generation-count verification and SQL Volume metadata calls. No audit rows were deleted, ignored or bypassed during diagnosis.

The 16-file inventory includes:

- Canonical compressed understanding archive: **121,098,558 bytes**.
- Package explanation document: **20,212,357 bytes**.
- Source XML: **61,141,514 bytes**, referenced both as staged XML and package `source.xml`.
- Registry, review, loop and audit schema artifacts, active selection JSON and model routing.

The replay confirmed **two source read/verify calls**. The baseline restores these unrelated runtime families and both source destinations even when returning one approximately 175 KB object response. Existing immutable index reuse does not avoid this prior Delta/Volume restoration.

## Exact-call replay, distinct from hosted timing

A fresh local process replayed the actual hosted dispatcher against the real user-owned Delta/Volume APIs using the explicit labs OAuth token and OBO resolver. Temporary in-memory wrappers measured existing methods; a SQL guard rejected every first keyword except SELECT/SHOW/DESCRIBE. Provisioning used `initialize=False`. No tracing hooks or scripts were saved to application source or the repository. Private restoration directories were context-managed and removed.

The replay returned HTTP 200 and the exact canonical wrapper in **64.337 seconds**. This includes local-network/Windows/cold-index costs and must not be substituted for hosted phase timing.

| Measured call | Count | Total time |
|---|---:|---:|
| Fresh authorization | 1 | 0.433s |
| Owned-root provisioning | 1 | 8.364s |
| Full restore | 1 | 44.864s |
| Record-page SELECTs | 22 | 17.978s |
| Generation-count UNION | 1 | 1.950s |
| Latest receipt SELECT | 1 | 2.000s |
| Manifest artifact read/verify | 1 | 2.490s |
| Runtime artifact read/verify | 14 | 8.812s |
| Source read/verify | 2 | 7.683s |
| SQLite reconstruction | 7 | 0.143s |
| Canonical index load | 1 | 9.033s |
| Selected-context acquisition | 1 | 10.209s |

Rows are nested method measurements and **must not be added together**. For example, restore contains record/artifact/SQLite work; selected-context acquisition contains canonical loading. SQLite construction is not the dominant cost. The successful hosted package-preparation phase was only 0.371s, consistent with its existing verified index reuse. Authorization/queueing/cold warehouse alone do not explain the steady successful 43-second GET.

## Async envelope versus canonical GET

The canonical GET above is not a POST acceptance response and does not create an async operation. The browser's [hosted intake adapter](../workbench/static/hosted-intake.js) instead submits GET work to POST `/api/hosted/operations`, receives an acceptance record, then polls the recorded operation. This protects the browser from holding one long HTTP response; it does not eliminate restore work.

Read-only inspection of the two existing release-test operation histories (`54f75a929710462e849956b26222c6e7` and `4b8d0ae1d6e84ac093649008f1465531`) found **21 verified rows each**:

- One each: `queued`, `running`, `provisioning`, `metadata_restore`, `preparing_package`, `committed`.
- Five `artifact_files` records.
- Ten `responding` records, nine containing result chunks.

[Operation publication](../workbench/hosted_operations.py) appends and reads back journal records. That is additional sequential remote work beyond the underlying canonical GET. The histories explain the extra work, but do **not** establish exact per-write latency or fully attribute the historical 157.53/160.45-second operation duration. No new async operations were submitted to measure that component.

## UI findings and proposed scope, not implemented

Source inspection established these narrow presentation issues:

1. `activityStatus()` prefixes every nonterminal message with **Running.**, including submitting/queued observations. Waiting and executing should be distinct, based on the actual journal state.
2. `progressText()` exposes phase descriptions and real counts, but the processing-meter caption remains **Hosted processing: progress unknown** even when a measured phase counter is available. Label it as phase-local verified work, not total-operation percent or ETA.
3. Collapse hides details. Active Minimize hides the card but intentionally leaves backdrop/inert blocking and a Show operation progress control. Explain this explicitly. Removing blocking is a separate behavior decision, not a cosmetic close-label fix.
4. Raw operation IDs are already in collapsed details and scrubbed from primary status messages. Keep that behavior.
5. [The object presenter](../workbench/static/app.js) already gives source-only unassessed nodes neutral treatment. Do not relabel an unassessed node as assessed, passed or safe.

Recommended next approved scope is **B**: an isolated exact committed-generation object-read projection, with fresh OBO ownership, selected-project membership, receipt/manifest/envelope/content-hash verification and only the required canonical dependencies. Preserve full runtime restoration for mutations and explicit Logs, current failure-closed behavior, cross-user isolation, Delta authority and separate execution approval. No TTL/ambient authorization cache, skipped integrity verification, audit deletion, service-principal fallback or format migration is proposed. Performance benefit remains unmeasured until implemented and tested.

## Verification and remaining work

- Two successful live exact-scope canonical GETs, including server phase breakdown.
- Read-only real dispatcher replay with remote-write rejection.
- Verified active inventory and existing operation-history checksums/counts.
- No business-data sampling, model inference, new operation submission, business Job execution, deployment or restart.
- No application code changed, so no application regression batch was run or claimed. Documentation-only integrity checks are recorded in the handoff.
- No exact future latency target is claimed. Larger XMLs, distinct-user live concurrency, cold/warm distributions and async per-write timing remain unverified.
- No implementation option has been selected. The proposed scoped read-path architecture change requires approval before editing tested control flow.

## Subsequent authorized local UI correction (2026-11-04)

The preceding investigation describes its original read-only scope. The user subsequently authorized a narrow UI correction, not the proposed scoped-restore architecture and not deployment.

- Routine hosted reads now retain their durable GET operation envelope and polling but never enter the mutation activity panel, acquire blockers, make the workspace inert, move focus, or reopen a minimized mutation notification. Progress is delivered to the requesting view; the main application uses its existing inline top status and region progress.
- Read journal records are labelled `kind: read`. They remain in reconnect storage but do not become a mutation notification on reload or get mixed into intake reconnection. Failures still reject with the operation ID and explicit no-replay wording.
- Main application read progress is owned by the originating page and selected-context epoch. Stale read updates cannot replace current-page progress. Durable backend operations are neither cancelled nor replayed by navigation.
- The workflow evidence presenter, captured connection inventory, recorded agent-loop reads, and hosted status GETs no longer impose arbitrary observational deadlines. Explicit mutations retain existing deadlines and guards. Invalidation clears the prior workflow evidence/error and advances its response ticket even if a transport ignores cancellation.
- Canonical `GET /api/object/{id}`, `GET /api/workflows/{id}/migration-context`, and the hosted `POST /api/hosted/operations` envelope for `method: GET` are observations, not model inference. Explicit model connect/select POST actions remain separate, with their existing no-inference notice.

The 16-file count still means files verified while restoring the active committed runtime generation, not models, inference calls, selected objects, or a request to process 16 user uploads. A representative inventory is the compressed canonical archive, explanation JSON, staged XML and package source XML (two destinations/read-verifications), registry, audit/review/loop databases, selection and model-routing metadata. The manifest accounts for the complete count. No backend restoration, checksum verification, audit history, ownership boundary, Unity Catalog writes or business execution was changed. The measured restore latency above therefore still applies as historical evidence, not a claimed performance improvement.

### Local verification, not hosted deployment

- Verified port 8000 belonged to the existing workspace command `.venv/Scripts/python.exe -u -m workbench --port 8000` (listener PID 39440, launcher 15280). Default/project package jobs and completed-package agent ledgers had no queued/running work. Restarted only that process pair with identical arguments and the existing `output/workbench` root. New listener PID 51092. No port 8001 process was touched.
- After restart, health returned 200, active package remained `08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`, and persisted activation timestamp remained `2026-10-28T19:49:29.371900+00:00`. Exact WIL_NET_DIS migration-context GET now returns 200. OpenAPI is disabled, so its 404 is not a route inventory.
- Live existing browser, Explorer to Proposals, workflow selection, Evidence and back: resource entries included overview, objects, proposals, object detail, redesigns, reviews, documents, connections and migration-context. Successful canonical object and context reads returned 200. Superseded redesign/connection resources showed status 0, not a rendered failure. Final DOM: zero dialogs, zero inert roots, zero hosted activity panels, no `aborted without reason` evidence text. Initial `/api/models/status` was metadata only; no connect/select POST, agent run, UC fixture check or model inference was invoked.
- Final changed-path command: `.venv/Scripts/python.exe -B -m pytest workbench/tests/test_hosted_intake_ui.py workbench/tests/test_redesign_ui.py workbench/tests/test_loop_ui.py workbench/tests/test_ui_structure.py tests/test_connections.py tests/test_loading_diagnostics_ui.py tests/test_proposal_selection_sync.py -o addopts= -q --disable-warnings -ra`. Result: **194 passed, 2 warnings, 223.86s**.
- Navigation-only command: `.venv/Scripts/python.exe -B -m pytest tests/test_navigation_prerequisites.py -o addopts= -q --disable-warnings -ra`. Result: **20 passed, 1 failed**, `test_mismatched_ready_package_does_not_unlock_project_navigation`, immediate hash assertion observes `#agents` while Estate is already visible. The test does not wait for initialization's final navigation. Left explicitly unresolved rather than weakening the assertion after repeated fixture corrections.
- Initial broad command: `.venv/Scripts/python.exe -B -m pytest workbench/tests/test_hosted_intake_ui.py workbench/tests/test_redesign_ui.py workbench/tests/test_ui_structure.py tests/test_connections.py tests/test_loading_diagnostics_ui.py tests/test_navigation_prerequisites.py tests/test_proposal_selection_sync.py workbench/tests/test_package_context.py tests/test_fixture_metadata.py workbench/tests/test_source_evidence.py workbench/tests/test_package_graph.py workbench/tests/test_agents.py workbench/tests/test_migration_planning.py workbench/tests/test_ui_uplift.py workbench/tests/test_capability_status.py -o addopts= -q --disable-warnings -ra`. Initial result: 314 passed, 17 failed, 1 teardown error, 6 subtests passed. Modal expectations and hosted fixture inconsistencies were corrected; package-wide overview progress was preserved across tab navigation. The separate `test_session_mapping_closures_and_connection_is_not_schema` failure (expected unresolved 1, actual 0) remains the previously documented parser-baseline failure. No planning/parser implementation or assertion was changed for this UI task.
- Editor diagnostics found no errors in the five changed frontend scripts or three changed UI test modules. No temporary tracing source files or hooks were created. No deployment, hosted restart, remote mutation or backend restore optimization was performed. No clean full-suite claim is made.