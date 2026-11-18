# Hosted intake redesign: candidate behavior and verification ledger

Updated: **2026-10-30**. Status: **implemented candidate with offline verification, deployment on hold**.

This guide describes inspected code, not a release announcement. The new intake path has **not been deployed or verified through live hosted OBO HTTP end to end**. An existing deployed app and a previously completed XML parse do not prove this candidate works behind the Apps proxy. Not all requested items are fixed.

The [proposal-first implementation plan](proposal-first-deepagents-lakeflow-plan.md) remains the governing design. Read it alongside [hosted operation](databricks-hosted-workbench.md), [Delta authority](hosted-delta-authority.md), and [verification history](workbench-verification.md). The startup-preference agentic-blueprint documents and PDF are absent from this checkout; alignment with those unavailable files cannot be certified.

## Task record and ask ledger

This documentation task adds this guide and its [documentation-index link](README.md) only. It does not change application code, configuration, jobs, model routing, credentials, or deployment. The following ledger preserves implemented components, pending live checks, and blockers separately.

**Status vocabulary:** `Implemented + offline verified` means bounded tests exercise the implementation, not live service acceptance. `Pending live` requires approved hosted verification. `Blocker` identifies missing behavior or an unproven operational requirement, not permission to work around it.

| Ask | Current status | Evidence and remaining boundary |
|---|---|---|
| Project XML visibility from committed metadata, without model hydration | Implemented + offline verified; pending live | [Registry projections](../workbench/hosted_registry.py) serve project list/detail/job metadata directly from a pinned committed Delta generation. They bypass the long-action queue and do not restore XML, canonical models, checkpoints, or an execution cache. Externalized registry-column metadata and the generation manifest can still require Volume reads. |
| Capability gaps independently accessible | Implemented + offline verified; pending live | [Independent implementation panel](../workbench/static/capability-status.js) uses semantic headings, open keyboard-operable details, and an existing Evolution navigation button. It is outside page sections and the overview-owned capability container. Offline Chromium checks cover delayed, failed, and offline overview responses. Static disclosure is not a health probe. |
| Retain existing activity/spinner UI and add real byte bars | Implemented + offline verified; pending live | [Hosted intake UI](../workbench/static/hosted-intake.js) is additive. Existing activity UI is not removed. Separate transport and durable-byte bars, per-file outcomes, and a navigation-independent activity summary are present. The standalone browser test explicitly preserves an existing spinner; that is not proof of all live parent-spinner behavior. |
| Bounded transfer, maximum 4 MiB per chunk | Implemented + offline verified; pending live | [Upload service](../workbench/hosted_uploads.py) and [dispatcher](../workbench/hosted.py) enforce the chunk body bound, exact final remainder, immutable chunk integrity, and durable receipt readback. This is a request-body bound, not a total process-memory bound. |
| Variable XML sizes, 8 GiB default, configurable up to 32 GiB | Implemented + offline verified for limits; capacity blocker | [Intake limits](../workbench/intake_limits.py) enforce integer byte budgets. No 8 GiB or 32 GiB XML has been proven to parse within app memory, disk, duration, or metadata limits. |
| Long actions do not fabricate parsing progress | Implemented + offline verified; pending live | Operation journal states are `queued`, `running`, and terminal outcomes. Successful completion reports progress stage `committed`. Staging and analysis bars remain indeterminate until closure; there is no measured parsing percentage. |
| Initiating OBO worker identity, no service-principal fallback | Implemented + offline verified; pending live | Worker closures retain initiating authorization in memory only. Polling resolves fresh OBO independently. Expired worker authorization is not replaced by the polling user's new token or an app identity. |
| Authentication-required and unconfirmed-completion handling | Implemented + offline verified; pending live | Recognized authentication errors yield `authentication_required` when that terminal write succeeds. A failed journal write can leave the last durable state `queued` or `running`, not a fabricated terminal result. |
| Restart interruption, no automatic replay | Implemented + offline verified; external-worker blocker | A new process observes old nonterminal operations as `interrupted`. Delta records are durable; the worker, event loop, execution arguments, and local working files are not an external durable worker service. |
| Resumable upload and browser reconnect | Implemented + offline verified; bounded UX | Immutable chunks and receipts survive the app process. Automatic browser discovery is limited to IDs recorded in that browser tab's `sessionStorage`. Reconnect observes recorded operations only. Missing IDs are not recovered by a server-side browser-history listing. |
| Explicit same-file resume and hashing | Partial; blocker for verified whole-file identity | User must reselect the identical file and click **Resume selected file**. Name and size are only a match hint. New slices are hashed when browser Web Crypto is available; server hashes and verifies every accepted chunk. Already acknowledged slices are skipped without comparing their hashes to the reselected file. This is not a whole-file identity guarantee. |
| Direct-read audit evidence | Implemented + offline verified; pending live | Registry reads append and independently read back a unique Delta audit event before returning data or a registry 404. This journal is separate from legacy SQLite HTTP-chain logs. |
| Volume selection/import uses redesigned intake | Blocker, legacy path remains | [Volume UI](../workbench/static/workspace-access.js) still posts directly to `/api/databricks/volumes/import`. It does not use chunk receipts or the operation wrapper. Download/staging remains synchronous and retains 80 MiB/file and 160 MiB/batch limits. |
| Activity heartbeat without scheduled-job changes | Implemented + offline verified; opt-in, pending live | [Heartbeat service](../workbench/heartbeat.py) is disabled unless both required environment settings are supplied. Existing-table writes only, no DDL, grants, job creation, or scheduled-job changes. Live permissions, table contract, and operational effect are unverified. |
| Deployment hold and approval gate | Held | Do not deploy the candidate without renewed explicit approval. This documentation task does not lift the hold. |
| Skills-improvement lifecycle | Blocker, next work, not done | Existing role skills are not evidence-linked lessons with human review, regression evaluation, and controlled promotion. The independent panel explicitly marks this unimplemented. |
| Model-driven general conversion | Blocker, not done | Advisory runs, typed-design submission paths, captured-SQL drafts, and the isolated demo do not constitute successful general XML-to-design-to-artifacts conversion or semantic parity. |
| Live E2E and all-asks acceptance | Pending live and remaining blockers | The known offline batch passed, but live OBO HTTP, large-file capacity, Volume redesign, exact resume identity, and general conversion remain unresolved. Do not mark the entire ask list fixed. |

## Current request and commit paths

### Project visibility and audit

The [hosted dispatcher](../workbench/hosted.py) resolves trusted proxy OBO authorization before API dispatch. GET `/api/projects`, `/api/projects/{project_id}`, and `/api/projects/{project_id}/jobs/{job_id}` use [registry_response()](../workbench/hosted_registry.py), outside the per-user long-action lock.

The projection verifies the latest receipt, generation manifest, exact registry-scope inventory, envelope/checksum, source keys, and project/package identities. It fails closed on missing, unexpected, duplicate, corrupt, or unauthorized records rather than returning a plausible partial list. It does not initialize missing tables or advance a runtime generation. A legacy HEAD without a Delta receipt requires explicit import, not a local fallback.

Each accepted read, including a registry 404 after lookup, requires its own `audit_events` append and exact readback in a unique `hosted-registry-read-v1:<event>` context. This is **not** legacy HTTP audit-chain replay and need not appear in the existing Logs UI. Audit publication failure prevents the registry result from being acknowledged.

Direct metadata visibility does not make every other view cheap. Overview, graph, model, and action routes can still restore or hydrate package state through the existing execution path. Fresh authorization and SQL/Volume calls still cost time; bypassing the action lock is not a latency SLA.

### Browser upload, staging, and analysis

1. Select an existing saved project and 1 to 10 nonempty XML files. The [parent submit hook](../workbench/static/app.js) first probes hosted identity and delegates hosted upload to the standalone adapter. A failed health probe does not silently fall back to local upload.
2. The browser records an upload ID before sending a manifest to POST `/api/hosted/uploads`. The server validates project membership from committed registry metadata and durably reserves the declared byte count.
3. Files are processed sequentially. The browser slices at most 4 MiB at a time, never calls whole-file `File.arrayBuffer()`, and PUTs `/api/hosted/uploads/{id}/chunks/{index}`. The server verifies exact size, computes SHA-256, writes an immutable Volume object, reads it back, and verifies a Delta chunk receipt before acknowledgement.
4. The transport bar measures bytes sent by XHR, **not persisted bytes**. The durable bar advances only from acknowledged chunks or a subsequent server-status observation. A failed PUT triggers one status observation, not an automatic resend.
5. POST `/api/hosted/uploads/{id}/complete` checks receipt completeness only. `ready_for_finalize` means all chunk receipts exist, not that XML has parsed or project state has committed.
6. The browser submits an operation wrapping POST `/api/hosted/uploads/{id}/finalize`. The worker reconstructs a temporary file using bounded reads, validates each chunk again, computes the assembled file hash, stages via the existing manager, drains action work, and flushes the full Delta generation. Only then is an upload `committed` receipt written.
7. The normal uninterrupted upload flow submits a separate project-analysis operation after staging succeeds, then refreshes project metadata with actual per-file results. Analysis remains independently fallible. A committed operation can contain an action result such as `partial`; inspect that result and every package status, not merely the journal envelope.

Staged XML and parsed/ready XML are different states. Transfer at 100% is not parsing at 100%. The activity summary is not a substitute for project membership and committed operation evidence.

### Long-action operation contract

[HostedOperations](../workbench/hosted_operations.py) accepts a bounded, allowlisted POST request through `/api/hosted/operations`: upload finalize, project analyze, agent run, or loop run. Arbitrary paths are not a general background execution API. The browser parent routes agent and loop submissions through this wrapper; project analysis is used by the intake adapter. Project creation, Volume import, and all other mutations are not automatically converted to asynchronous operations.

The pool has two process-local workers and eight accepted-work slots. Same-user actions still enter the existing dispatcher lock and configured active-user capacity boundary. Status and upload endpoints bypass that long-action lock, but still perform fresh authorization and storage verification. Operation IDs are scoped by workspace/user and request fingerprint; a conflicting request cannot reuse the same ID.

| Observation | Meaning |
|---|---|
| `queued` | The acceptance record was appended and verified. Work is not proven complete. |
| `running` | A verified running record precedes action execution. The worker may still be waiting on the action boundary or remote services. |
| `completed`, progress stage `committed` | The actual action returned success through the existing commit boundary and the terminal result was journaled and read back. Inspect nested action and per-file outcomes. |
| `failed` | A safe failure record was committed. Do not infer that all remote side effects rolled back. |
| `authentication_required` | Recognized user-authorization failure was recorded. Reauthenticate, inspect committed state, and explicitly decide whether to retry. |
| `interrupted` | A different process ID owns the last nonterminal record. This is a read-time observation, not automatic recovery or a new journal transition. |

There is no parsing numerator/denominator, ETA, or synthetic percent. `queued` and `running` progress uses an unknown total. Browser polls are sequential with a three-second delay, not overlapping fixed-interval requests. Each candidate intake request has a 110-second client timeout; the proxy and remote calls can still time out. Legacy parent mutations retain their separate 30-second timeout.

If the running append fails, execution does not start. If execution succeeds but the completion append fails, GET may continue to report the last `running` record without a result. The code deliberately does not convert an unverified write into completion. In the same process, such a record can remain nonterminal; a process restart makes it observable as interrupted. Inspect actual committed project state before any new operation.

## Identity, durability, and recovery boundaries

- **Initiating authorization:** The worker uses the initiating OBO client and forwarded authorization retained by in-memory closures. Credentials and action arguments are not persisted for worker replay. The dispatcher re-enters its authentication and commit boundary with the initiating forwarded token. There is no background token renewal or service-principal fallback.
- **Fresh polling authorization:** Each HTTP poll resolves current OBO credentials and verifies namespace/privileges. A refreshed poll can observe earlier durable results, but it does not refresh or replace the worker's initiating token. Token expiry can prevent both execution and a terminal journal write.
- **Durable journal, ephemeral worker:** Delta acceptance/status/upload receipts and immutable Volume chunks survive a process loss. Threads, the server event loop, temporary assembled XML, and the ability to continue execution do not. This is not an external durable queue, distributed lock, job worker, or restart-replay system. Exactly one app replica/process is required.
- **Lifecycle limitation:** The operations class exposes `close()` to drain accepted work, and integration tests explicitly call it while the server loop remains available. The inspected hosted app factory does not install a lifespan shutdown hook to drain this independent executor. Graceful app shutdown must not be claimed verified.
- **Commit boundary:** Full-state mutations retain commit-last generation visibility, not a multi-table transaction. Generation publication, upload commit receipt, and operation completion are separate writes. Lost acknowledgements may leave genuine committed state with incomplete later receipts. Never claim automatic rollback or blindly retry.
- **Same tab, recorded IDs:** The browser stores only whitelisted reconnect metadata in `sessionStorage` under `orion.hosted-intake.v1`: upload/project IDs, file name/size, and staging/analysis operation IDs. It stores no file bodies, request bodies, auth headers, or credentials. Refresh/reconnect depends on those IDs remaining available in that browser session. A new browser/profile or lost storage is not covered by automatic recovery.
- **No replay on reconnect:** **Reconnect to recorded operation** performs status reads. It neither creates a missing finalize operation nor submits missing analysis. Changing project/package selection stops the old UI flow from refreshing the new selection or submitting its next action. An already accepted server worker is not cancelled by navigation.
- **Resume is explicit:** Reselect the exact original bytes and choose **Resume selected file**. Same name/size does not prove identity. Already acknowledged chunks are skipped, so substituting a same-size different file can assemble old and new content. The server's final hash describes the assembled bytes, not proof they match the newly selected whole file.
- **Analysis recovery UI gap:** The adapter's reconnect message recommends an explicit project Analyze action. The inspected parent scripts do not expose a dedicated project Analyze button. The allowlisted operation/API exists, but do not advertise a complete click-through recovery journey for the missing-analysis case.
- **No assumed completion:** On timeout, authentication failure, interruption, or a lost response, inspect upload receipts, operation ID, project metadata, and per-file outcomes. Reauthenticate when required. Reconnect before creating any new action. Escalate unresolved state rather than treating a stopped spinner as success.

## Limits, integrity checks, and capacity caveats

| Boundary | Inspected implementation |
|---|---|
| XML file budget | `ORION_MAX_XML_BYTES`: 8 GiB default, positive integer bytes, maximum 32 GiB. |
| Outstanding upload byte budget | `ORION_MAX_BATCH_BYTES`: 32 GiB default and maximum; must be at least the file budget. Uncommitted manifests reserve capacity across restarts. Completing transfer alone does not release a reservation. |
| Chunk and manifest | At most 4 MiB/chunk, at most 8,192 chunks/file, 16 KiB upload metadata bound. The final chunk must match its exact remainder. |
| Upload histories | Reservation/history scans cap at 4,096 uploads and fail closed rather than truncating. No automatic abandoned-reservation cleanup or user-facing cancel/release route is implemented here. A new manifest at the history cap is rejected even if older uploads committed. |
| Operation metadata | 32 KiB request bound, at most 20 immutable stages, terminal record subject to the 48 KiB Delta payload limit. Oversized results are failures, not silently truncated success. |
| Source versus metadata | Source XML is streamed separately. The existing 1 GiB non-source hosted-state budget and other runtime manifest/file-count limits still apply. Raising an XML budget does not raise those limits. |
| Legacy entrypoints | Local/multipart project upload and Volume import retain 80 MiB/file and 160 MiB/batch behavior. The new configurable source path does not uniformly replace every intake route. |

**Read and marker checks:** [HostedUploads._stream()](../workbench/hosted_uploads.py) requests at most 1 MiB per read and at most the remaining expected size plus one byte, detecting overrun as well as truncation. It rejects non-byte or oversized read returns, hashes the stream, and closes it. `_checked()` calls the [Delta credential-shape checker](../workbench/delta_state.py) and rejects exact initiating credential byte strings. Materialization carries a suffix of at least 8,192 bytes, or the longest forbidden credential, across reads and chunks. These checks cover bounded overlap and exact-token matches, not a complete arbitrary-length secret detector.

Do not conflate that checker with [VolumeSnapshots._scan()](../workbench/hosted_storage.py). The source publication/verification scanner also reads 1 MiB blocks, retains ordinary suffix overlap, and carries compact partial Bearer/JWT markers for patterns whose whitespace/segments can exceed a fixed suffix. Its `_checked()` rejects the exact forwarded token; it is not itself a general invocation of all Delta credential-shape rules. Later runtime publication also has its own checked metadata boundaries. Different check functions are not interchangeable, and their presence does not establish universal XML secret detection. Source ownership, classification, and safe-content review remain required.

**Parser memory:** [Canonical understanding](../workbench/understanding.py) uses hardened incremental XML parsing, but accumulates nodes, edges, fields, properties, indexes, connectors, and pending references into the full canonical model. Subsequent analysis and serialization also consume memory. Bounded transfer and source streaming do not make the entire pipeline constant-memory. [Delta flush](../workbench/delta_runtime.py) avoids whole-source reads for XML, but still reads/encodes non-source state and externalized metadata. Local disk space, canonical model size, metadata budgets, token lifetime, proxy behavior, and runtime duration can block files far below the configured maximum.

The 8,192-chunk regression synthesizes receipts and metadata to exercise pagination. It does **not** upload or parse 32 GiB. The browser's large-file test uses generated slices and mocked network responses, not a live giant XML parse. Existing successful approximately 61 MB parsing is the relevant observed real-source scale, not a capacity guarantee.

## Volume import and heartbeat: separate boundaries

### Volume import remains legacy

[workspace-access.js](../workbench/static/workspace-access.js) uses a direct fetch for `/api/databricks/volumes/import`, explicit consent, and per-card busy state. Its fetch helper has no explicit client abort timeout. [VolumeIntake.import_xml()](../workbench/volume_intake.py) synchronously downloads selected files in 1 MiB reads, checks content length and aggregate limits, then stages them. It does not publish per-chunk durable progress, resumable transfer receipts, or a separate operation ID. The hosted wrapper still holds that request through action closure and generation flush. Proxy timeout risk therefore remains.

The script also retains local-profile disclosure text even though the hosted service overrides its backend identity to OBO. That text is not proof the hosted import uses CLI credentials. The UI copy and redesigned Volume-operation integration remain gaps, not completed work in this documentation task.

### Heartbeat is opt-in activity evidence, not a keepalive guarantee

The [browser adapter](../workbench/static/heartbeat.js) sends no startup pulse. Only fresh trusted interactions in a visible document can schedule activity; returning to a tab alone is not activity. It uses a five-minute attempt interval and does not run an idle retry loop.

Server writes are disabled unless both `WORKBENCH_HEARTBEAT_TABLE` and `WORKBENCH_HEARTBEAT_APP` are explicitly set. The table must already satisfy the three-part-name and `(app STRING, ts TIMESTAMP, user STRING)` contract, and the initiating OBO user must have required access through the configured warehouse. The service performs no DDL, grants, ownership provisioning, scheduled-job creation, or job changes. No configuration is changed by this guide.

When enabled, upload mutations can call `touch(..., "active_work")`; the dispatcher also runs a five-minute pulse during wrapped action execution. The service revalidates the supplied OBO principal on every pulse, but the worker still supplies the initiating identity, not a magically renewed token. It can expire. Only successful INSERT plus matching timestamped readback yields `written=True`. Pending/error/busy/throttled is not a verified write. Cooldowns are process-local, and unconfirmed INSERTs can later produce duplicates. Activity writes do not prove durable-worker completion, external liveness, prevention of app suspension, or any change to an existing keepalive job.

## Evidence ledger: what has and has not run

### Known offline regression

The supplied prior regression result for the redesign batch is **368 passed, 1 warning**, with exit code 0. This is a known historical batch result from the parent task, not a newly executed regression or a clean-full-suite claim by this documentation task. The supplied terminal context does not include that warning's text; do not infer a new warning diagnosis from the count alone.

Relevant existing coverage includes [HTTP integration](../workbench/tests/test_hosted_intake_integration.py), [standalone browser intake](../workbench/tests/test_hosted_intake_ui.py), [operation journal](../workbench/tests/test_hosted_operations.py), [upload integrity](../workbench/tests/test_hosted_uploads.py), [registry](../workbench/tests/test_hosted_registry.py), [capability accessibility](../workbench/tests/test_capability_status.py), [heartbeat](../workbench/tests/test_heartbeat.py), and [source streaming](../tests/test_large_source_streaming.py). Remote services are faked in offline HTTP tests; production dispatcher, worker, parser, staging, and commit paths are exercised for bounded fixtures. Browser interception is not live Databricks acceptance.

Exact relevant commands supplied in the parent terminal context follow. These are an evidence record, not instructions to deploy. No command below was rerun merely by adding this guide.

**Redesign batch, known 368-pass result:**

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; try { ./.venv/Scripts/python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_intake_integration.py workbench/tests/test_hosted_intake_ui.py workbench/tests/test_capability_status.py workbench/tests/test_heartbeat.py workbench/tests/test_hosted_registry.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_uploads.py workbench/tests/test_loop_ui.py workbench/tests/test_requested_ui.py workbench/tests/test_ui_structure.py workbench/tests/test_hosted_package.py workbench/tests/test_delta_runtime.py workbench/tests/test_delta_state.py tests/test_large_source_streaming.py -q --tb=short -p no:cacheprovider; $code=$LASTEXITCODE } finally { Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD }; Write-Output "REDESIGN_REGRESSION_EXIT=$code"
```

**Earlier tracing/agent/loop integration batch, supplied exit code 0, no aggregate count asserted here:**

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; try { ./.venv/Scripts/python.exe -B -m pytest -c workbench/pytest.ini tests/test_tracing.py workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_loops.py workbench/tests/test_app.py -q --tb=short -p no:cacheprovider; $code=$LASTEXITCODE } finally { Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD }; Write-Output "TRACING_AND_INTEGRATION_REGRESSION_EXIT=$code"; if ($code -ne 0) { exit $code }
```

### Real candidate registry reads, not hosted HTTP E2E

Previously recorded direct candidate reads using the real `labs` user took **27.24 seconds cold for projects** and **8.48 seconds for project detail**. These measurements exercised actual Delta/Volume registry reads. They are **not** OBO requests through the deployed browser/proxy, warm-cache promises, or sub-ten-second guarantees for every screen. The hosted browser reached login; the candidate's live OBO HTTP behavior remains unverified.

That observation pinned generation `01da287328214a66a5cafa8d56679f82`, sequence 8, with exactly one project: `84b03a652f5e4371ba1c72d401acba6b`, **Lumen Informatica Modernization**. It contained one completed Lumen XML package of **61,141,514 bytes**, and completed 1/1 batch `005d193ac8fa47e39c7998af867a793b`. This is the last supplied committed observation, not a new live query or an assertion that the remote generation cannot have changed.

The local registry observation had **three projects, five package memberships, and four jobs**, a different history. Historical imports, local output directories, and the active hosted project must not be combined into a fabricated current inventory.

### Model and delivery truth

The prior isolated runtime-planning run made 24 actual model calls but produced zero valid designs and ended `needs_information`; subsequent guidance fixes did not have a second live verification. It is not evidence that general conversion is complete. The [constrained demo runbook](wil-net-dis-demo-runbook.md) documents a different bounded compiler path with explicit test replacements. Neither that demo nor the intake redesign establishes general conversion, original child-workflow completion, semantic parity, approved general artifacts, GitHub CI/CD, or a skills-improvement lifecycle.

## Deployment preflight, approval required

**Deployment remains on hold. Do not execute a hosted deployment, change app environment settings, restart the hosted app, provision monitoring infrastructure, or modify existing jobs without explicit approval for that action.** Offline tests and this checklist do not grant approval.

Before requesting deployment approval:

- [ ] Review this ask ledger with the owner. Distinguish offline-verified paths, pending-live checks, and unresolved blockers. Do not state that all requested items are fixed.
- [ ] Confirm the exact candidate source/package and intended hosted app. Record that the new operation path is not yet a live-verified release.
- [ ] Confirm single-process/single-replica operation, process-local worker lifecycle, shutdown-drain limitations, and no restart replay. Decide whether those constraints meet the intended workload.
- [ ] Validate the existing warehouse, trusted Apps proxy/origin, accepted OAuth scopes, fresh user identity, namespace ownership, Delta schema/readback, and Volume access without exposing tokens or substituting another identity.
- [ ] Choose XML/batch budgets within the configured caps, verify temporary disk and non-source-state budgets, and approve a bounded capacity test plan. Do not equate 32 GiB configuration with parser capacity.
- [ ] Decide whether to leave heartbeat disabled. Any activation requires separately approved existing table/app settings, verified OBO write/readback, and confirmation that no scheduled jobs are changed.
- [ ] Explicitly accept or resolve legacy Volume import, missing-analysis recovery UI, incomplete same-file verification, and executor shutdown gaps. No silent fallback or functional reduction is authorized.
- [ ] Run and record the relevant offline regressions against the exact release candidate, including command, pass/fail, warning details, and any known failures. Historical 368-pass evidence alone is not a new release gate.
- [ ] Obtain explicit deployment approval before staging/deploying or restarting the hosted application. Record the approved scope and rollback/recovery plan.

After an approved deployment, before claiming live acceptance:

- [ ] Verify project XML metadata visibility with actual browser OBO HTTP, including a slow/failed overview while the independent capability panel remains accessible.
- [ ] Verify real durable-byte acknowledgements versus transport bytes, exact chunk remainder, staging and analysis results, user isolation, same-user contention, and status polling responsiveness behind the proxy.
- [ ] Exercise lost responses, explicit same-file resume, refreshed polling authorization, initiating-token expiry, journal-write failure, and process interruption without automatic replay. Inspect committed state before retrying.
- [ ] Verify actual audit rows in the independent Delta registry journal, separately from legacy Logs UI. Verify heartbeat only if activation was approved.
- [ ] Record precise observed results and unresolved blockers. Reconcile this guide with the deployed version before marking any pending-live ask accepted.

**Next work, not completed:** evidence-linked skills improvement and model-driven general conversion require their own scoped implementation, review, regressions, and live verification. Intake responsiveness is not approval for either artifact execution or deployment.