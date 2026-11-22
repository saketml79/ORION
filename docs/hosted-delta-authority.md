# Hosted Delta authority cutover

**Current implementation and reported operational evidence, 2026-10-30.**
Implemented in [delta_runtime.py](../workbench/delta_runtime.py) and injected by
[hosted.py](../workbench/hosted.py). The deployed app is
`orion-migration-workbench`; real forwarded-user OBO health returned HTTP 200.
See the current [hosted operations guide](databricks-hosted-workbench.md).
This documentation update performs no deployment or remote mutation. Reported
live observations below are existing evidence, not fresh tests run for this edit,
and do not establish production readiness or general artifact/deployment authority.

## Authority and action lifecycle

### Committed source cache (current source, not verified or deployed)

After a committed generation and its inventory are verified, restore can retain
scanned source copies in a private process-local cache. Reuse is scoped to the
workspace, user, runtime and exact selected Volume path, checksum and size. Each
reuse calls the Files metadata API with the current identity before and after
copying and rechecks the cached bytes against the committed checksum. Flush uses
the same path only for sources already in the current committed generation.
Newly selected sources retain the existing import and verification behavior.

If usable size or last-modified metadata is unavailable, the original source
download and checksum verification is used instead. Missing or denied metadata
never authorizes a cache hit. The preparation response separately reports
`source_cache_ready` and `source_cache_reason`; working-snapshot readiness is
unchanged. The cache holds at most 32 source entries and may evict them, be lost
on process restart, or fail disk admission. It does not impose an XML byte limit
smaller than the existing `max_xml_bytes` limit. These conditions can cause
subsequent downloads, so uninterrupted no-download behavior is not guaranteed.

Files metadata is not a content hash or immutable version. An external change
that preserves both length and the reported last-modified value can go undetected,
including changes within the timestamp's resolution. The checksum validates the
cached bytes, not the current remote bytes. Pre/post checks reject observable
metadata changes during verification but cannot close the race after the final
metadata check. Initial import is not bracketed by this cache mechanism.

### Scoped endpoint-settings exception (current source, not deployed)

Endpoint Apply now appends one bounded, immutable revision to the existing
`model_routes` table under `hosted-model-settings-v1`, scoped to fresh OBO
workspace/user and the selected project/package. It verifies current committed
membership, the expected saved-route version and fresh endpoint metadata, then
independently reads back the exact row before returning a successful receipt.
It does not initialize tables, restore XML/SQLite/audit, construct model clients,
upload artifacts, copy unrelated records or publish a full runtime generation.
The existing per-user writer, operation deduplication and uncertain-outcome
reconnect protocol remain in force. Single-replica operation is still required.

Saved-status reads prefer this separate settings authority. Only absence of a
journal revision permits reading the legacy committed routing document; invalid
or ambiguous journal data fails closed. Execution overlays the freshly scoped
preference before constructing clients, so a later full runtime generation cannot
erase it. Compatibility copies in full generations are not settings authority.
No credentials are persisted, and no alternate principal is introduced.

The prior historical 156-second model-save observation involved full-generation
publication, not proof of a new XML analysis or agent review. The inactive former
writer remains in source solely for rollback reference. Hosted operation activity
now occupies normal page flow, without a modal, blur, focus trap or shell inert.
Model settings use their existing inline elapsed/progress component with actual
Validate, Save and Confirm server stages, never simulated agent activity.
No hosted latency improvement or deployment is claimed by this source change.

Verification for this focused change: **629 passed, one warning in 573.54s**.
The consolidated command used the selected environment executable with
`-B -m pytest -c workbench/pytest.ini`, the following modules, and
`-q --tb=short --disable-warnings -ra`, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

- workbench/tests/test_hosted_lock_ownership.py
- workbench/tests/test_hosted_model_reads.py
- workbench/tests/test_model_route_save_ui.py
- workbench/tests/test_hosted.py
- workbench/tests/test_hosted_operations.py
- workbench/tests/test_hosted_intake_integration.py
- workbench/tests/test_hosted_intake_ui.py
- workbench/tests/test_hosted_agent_reads.py
- workbench/tests/test_delta_runtime.py
- workbench/tests/test_delta_state.py
- workbench/tests/test_hosted_package.py
- workbench/tests/test_hosted_registry.py
- workbench/tests/test_hosted_object_reads.py
- workbench/tests/test_workspace_access.py
- workbench/tests/test_databricks_models.py
- workbench/tests/test_model_workspace.py
- workbench/tests/test_loop_ui.py
- workbench/tests/test_agent_arena.py
- workbench/tests/test_ui_structure.py
- tests/test_agent_history_scoped_ui.py
- tests/test_navigation_prerequisites.py
- tests/test_hosted_startup.py
- tests/test_intake_loading_controls.py
- tests/test_loading_diagnostics_ui.py
- tests/test_next_action_guidance.py

Python AST parsing and Node syntax checks for the three changed browser scripts
also passed. These are offline fake-backend and real Chromium checks, not live
Databricks proof. The first consolidated run had 610 passed and 15 failures;
traced fixture/expectation corrections preceded the complete passing rerun.
No deployment, model inference, remote UC mutation or Jobs execution was run.

1. Resolve fresh forwarded user authorization using the existing OBO resolver.
   Enforce the fixed workspace/origin, per-user asynchronous queue, and bounded
   capacity. The strict bare injected DATABRICKS_HOST is normalized to HTTPS;
   trusted X-Forwarded-Host (Host if absent) must match the configured app origin.
   Both proxy-host corrections were verified. No arbitrary proxy trust is implied.
2. Verify user ownership of catalog, schema and Volume. Mutations initialize/check
   all 21 Delta tables; GET/HEAD/OPTIONS use restore(initialize=False), verifying
   only tables actually used, without initializing the full metadata schema.
   Query the latest committed hosted generation receipt in that exact
   workspace/user/runtime scope. Sequence determines ordering, not timestamps.
3. Verify its immutable manifest, namespace, inventory, paged Delta rows and
   content hashes. Reconstruct SQLite from schema-only artifacts plus exact raw
   source columns in committed Delta records. Supplementary decoded fields never
   override original source columns. Restore binary checkpoint cells without
   unpickling. Recreate indexes/views/triggers after inserting rows.
4. Reconstruct JSON metadata from committed Delta records, including ordered
   proposal arrays and the user-scoped model routing document. File artifacts
   are checked for integrity but are not used to replace JSON record values.
   Restore ordinary binary artifacts and exact active XML source references
   from the committed artifact_manifest set. Verify size/checksum of every object.
5. Execute the existing application against the disposable private cache. Model
   clients are recreated for the initiating user on every action. There is no
   ambient credential, service-principal, CLI-profile or local-state fallback.
6. Buffer the response, drain package and agent workers and close checkpoint
   connections. For mutations, read every supported SQLite table and file through
   the existing build_records contract. Unknown tables fail closed. Capture schema DDL only
   for SQLite artifacts, never SQLite backup bytes or row snapshots.
7. Preflight secrets and bounds before uploading generation artifacts. Upload
   immutable artifacts and the complete active inventory, append Delta records
   under a fresh generation ID, independently read back the exact inventory,
   check the parent generation, and append one final committed receipt.
8. For mutations, read back the committed receipt before releasing the buffered
   response. Dispose of the cache and request identity. Steps 6-8's full-content
   publication is not performed for observational GET/HEAD/OPTIONS requests.

### Observational reads and durable audit

After content restoration, restore_audit overlays verified tails from the separate
Delta `audit_events` journal scoped to `hosted-http-audit-v1`. After serving the
request and draining workers, flush_audit appends only new exact SQLite audit
columns: sequence, payload, previous_hash and event_hash. It independently replays
the persisted tail against the verified prefix and compares the resulting rows
and hash chain before releasing the response. A failed or ambiguous journal write
fails closed, not a successful unverified acknowledgement.

Reads do **not** upload artifacts, append application content rows, or publish a
new full-generation receipt. Disposable read-context initialization is not made
content authority. A later mutation can include these exact audit prefixes in its
content generation. For an empty initialized user, only receipts and audit tables
need verification; missing tables on a read fail closed rather than triggering
schema initialization. This does not mean every nonempty estate needs only two
tables. Dispatcher health, status and static fast paths bypass content hydration
and this audit-publication cycle.

Principal/table and Volume-owner verification are memoized only inside one fresh
action. No application, client, identity, authorization verdict or cached
cross-user credential is retained as a last-user fallback across requests.

The content receipt is an atomic **visibility record**, not a multi-table transaction.
Unreceipted rows and artifacts are invisible to application reconstruction.
Each content generation is a complete active set, not a union with historical
files or rows. The HTTP-audit tail is the explicit separate journal described
above, not an automatic merge of historical content. Deleted content files/rows/
sources do not reappear after restart. Missing/corrupt
committed records or artifacts fail with sanitized 503, not an older generation
or local backup. An ambiguous network failure while publishing a receipt can
still mean the action committed. Inspect committed state before retrying.

SQLite exists only as an execution compatibility cache. No hosted SQLite image
is uploaded, downloaded, or restored. The historical VolumeSnapshots class and
offline delta_state migration/export utilities remain unchanged in authority:
they are legacy compatibility tools, not hosted restore paths.

## Initialization and existing estates

An empty runtime scope with no historical HEAD starts a new empty user estate.
A first mutation can initialize missing metadata tables; a cold read cannot.
A historical HEAD without a committed runtime receipt fails closed and requires
an explicitly reviewed import. The code only checks for its existence, never
loads a HEAD-selected SQLite backup. Existing offline migration receipts are not
runtime generation receipts. This cutover does not automatically adopt an old
snapshot or an offline migration as live application state.

The separately VERIFIED historical import has receipt
`3fbacd46df986b9075212553c3792fe9d059ee98f4522fa5d599d954be3e66c5`,
reported 5,764 rows before the receipt, 1,404 objects, 100 files and 300,651,539 object
bytes. It is an archive, not the current runtime generation. The corrected
redesign database mappings are included in that import. See
[the migration inventory](delta-state-migration.md#verified-historical-import-2026-10-30),
including the unresolved two-row discrepancy between the supplied overall and
per-table figures. No fresh count certification is made here.

Active hosted project `84b03a652f5e4371ba1c72d401acba6b` was populated from a
fresh XML upload/analysis, not the old generated output. The separate source
fixture catalog `infotodbxmig_src_dev_ssuman` has the reported 583 empty tables
across 136 schemas, not the workbench's operational metadata or populated data.

Direct authorized Delta edits must be published as a new complete generation
with the matching inventory hashes and final receipt. Editing a committed row
in place without updating integrity metadata is detected as corruption, not
silently accepted. Tests exercise a newly committed generation with altered raw
SQLite, JSON and checkpoint values while the original artifacts remain stale.

## Configuration and authorization

WORKBENCH_SQL_WAREHOUSE_ID is mandatory in HostedConfig.environment. The engine
contains no default warehouse. [stage_hosted.py](../deployment/stage_hosted.py)
puts the previously approved existing warehouse e3a0fc2c08db05bb into staged
configuration by default; its warehouse option can explicitly select another
reviewed existing resource. No warehouse is created by the adapter or staging.

The [bundle](../deployment/hosted/databricks.yml) requests the accepted scopes
`catalog.catalogs`, `catalog.schemas`, `catalog.tables`, `files`, `model-serving`
and `sql`. Apps rejected both `unity-catalog` and `catalog.volumes`; neither is
requested now. sql is the current SQL scope, not sql.statement-execution.
[hosted_volumes.py](../workbench/hosted_volumes.py) supplies SHOW VOLUMES,
DESCRIBE VOLUME and CREATE VOLUME IF NOT EXISTS through the same user's SQL
Statement Execution client. File bytes still use Files. Thus both operational
Delta state and Volume metadata depend on the configured warehouse. There is no
all-apis expansion, cached credential, alternate principal or SP workaround.
Each user needs warehouse CAN_USE and actual UC ownership/permissions in addition
to OAuth consent. Missing SQL/UC permissions fail closed. Forwarded authorization
is OAuth OBO, not a PAT; CLI profile names do not prove a credential type.

## Limits and remaining platform blockers

- Exactly one process/replica, no overlapping deployments or external writers.
  The parent check detects obvious overlap but is not distributed CAS. Two app
  processes could still race. The single-writer deployment restriction is required.
- One active stateful action per user, with overlapping requests queued rather
   than routine dispatcher 409 responses; domain conflicts can still return 409.
   Default capacity is two executing users, with exhaustion returning 429. Queue
   waiting consumes request time. Existing local entrypoints/paths are unchanged.
- 1 GiB working-state bound, 20,000 files, 64 MiB generation inventory, 48 KiB
  inline record payload with immutable artifact externalization. Existing Delta
  Statement API page and inline-response bounds still apply, failing closed.
- Full-state reconstruction remains expensive even though GET avoids content
   republication. The latest reported full overview took **76.7 seconds**. The
   main UI helper budgets **110 seconds for GET reads**, **30 seconds for mutations**;
   these are browser abort budgets, not server guarantees or all-helper defaults.
   The Apps proxy's 120-second timeout remains a long-action risk. Full XML analysis
   timed out at the proxy, but the committed result was verified later. A timeout
   is an ambiguous outcome: inspect committed jobs/project state before retrying.
   A 202 is withheld until workers and persistence complete, not proof of an
   independent durable background worker. Cold-start and multi-user performance
   are not comprehensively established by the overview measurement.
- No mid-action crash recovery or durable worker resume. All checkpoint rows
  survive a completed action, but an interrupted uncommitted action is discarded.
- No automatic legacy estate promotion, orphan artifact cleanup or retention.
- Real OBO health and hosted fresh-XML completion are reported verified, and the
   accepted scope/SQL Volume path supersedes the earlier scope blocker. This does
   not verify every user's SQL/Files/model permissions, model inference, clean
   dependency builds, shutdown timing, or real two-user isolation/performance.
   No production-readiness guarantee or general generation/deployment permission
   follows. Hosted health/status keep generation_allowed and deployment_allowed
   false. No deployment is authorized or executed by this documentation update.

## Offline verification

The authority tests use real SQLite files, real DeltaStateStore serialization,
SDK Statement API parameter/response contracts, and an in-memory remote backend.
They cover every SQLite persistence family and JSON family captured by
build_records, binary checkpoint/writes cells, Delta edit authority, restart,
active deletions, secret preflight, hash failure, unavailable SQL, no local or
HEAD restore fallback, failed appends and commit ordering. Hosted HTTP tests
exercise user isolation, restart, fresh tokens/models, source revalidation,
sanitized failure and response withholding. Final command results are reported
with the implementation handoff, not represented here as live cloud validation.

### Historical regression results, 2026-10-29

All pytest commands used the configured workspace Python 3.13 venv with -B,
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1, -c workbench/pytest.ini, -q, --tb=short and
-p no:cacheprovider. No guards, timeouts or unrelated implementation were changed.

- Full workbench/tests: **648 passed, 7 skipped, 6 subtests passed, 3 failed**
   in 1169.04 seconds. This run preceded three additional configuration/HTTP
   tests, which passed in the final focused run below.
- Final focused test_delta_runtime.py, test_hosted.py, test_hosted_package.py,
   test_delta_state.py: **59 passed** in 93.08 seconds, exit 0.
- Isolated rerun of the three full-suite failures: **1 passed, 2 failed**
   in 69.59 seconds. The concurrent model-routing test passed on rerun; its
   full-suite Windows os.replace PermissionError is intermittent, not resolved.
- Reproduced failures outside edited runtime code:
   test_mock_http_sync_and_async_use_fresh_user_authorization blocks the socketpair
   connection needed to create Windows asyncio's ProactorEventLoop;
   test_stage_then_explicit_analyze_partial_statuses_snapshots_and_documents
   exceeds its existing Future.result(timeout=30).
- Editor diagnostics reported no errors in edited Python implementation/tests.

These scoped historical results verify the cutover at that time, not the entire
current implementation. That full suite was not green; no current all-green
claim is made. The original implementation handoff did not deploy. The later
deployed app/evidence above supersedes its no-deployment status, without turning
historical tests into current production clearance.