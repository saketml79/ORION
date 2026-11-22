# Databricks hosted workbench

**Current implementation and reported operational evidence, 2026-10-30.**
The deployed app is `orion-migration-workbench`. Real forwarded-user OBO health
was verified with HTTP 200. This is a proposal-first advisory workbench, not a
general executable-artifact generator, release engine, or deployment authority.
Health success is not a production-readiness guarantee or evidence that every
model, user permission, or long-running action has passed live verification.

Hosted authority is committed Delta metadata plus immutable Volume artifacts,
with a separate durable read-audit journal. SQLite is an ephemeral execution
cache reconstructed from Delta source columns, not a hosted snapshot authority.
See [hosted-delta-authority.md](hosted-delta-authority.md) for the exact lifecycle
and [delta-state-migration.md](delta-state-migration.md) for the separately verified
historical archive. Local mode and its loopback-only boundary are unchanged.

## Architecture and trust boundary

- Entrypoint: [workbench/hosted.py](../workbench/hosted.py), started with the Python
  module workbench.hosted. Binds 0.0.0.0 on DATABRICKS_APP_PORT, one process.
- Deploy only behind the Databricks Apps managed proxy. Do not expose this
  entrypoint through another server or trust arbitrary forwarded headers.
  WORKBENCH_TRUST_DATABRICKS_PROXY=1 is an operator assertion, not cryptographic
  proof of proxy provenance.
- WORKBENCH_DATABRICKS_HOST is a fixed HTTPS workspace origin. Startup normalizes
  a strictly validated bare DNS DATABRICKS_HOST to HTTPS before comparing it with
  that configured origin. WORKBENCH_APP_ORIGIN is the fixed public HTTPS origin.
  The trusted proxy's X-Forwarded-Host, or Host when absent, must match its exact
  authority; mutation Origin must match too. Bare injected host and forwarded-host
  corrections were verified. These rules do not make arbitrary proxies trusted.
- Every authenticated request resolves the forwarded x-forwarded-access-token
  with the existing explicit obo_identity function. Missing/invalid authorization
  fails closed. No WorkspaceClient() ambient authentication, CLI profile, PAT
  lookup, or service-principal fallback is used by hosted application code.
  UI/assets still require OBO, but do not provision catalogs or restore state.
  apply even without Content-Length. Forwarded bearer headers are stripped before
  existing application handlers and audit middleware execute.
- A ContextVar holds the initiating action identity. ActionExecutor copies that
  context into existing package/agent workers. Workers drain before durable
  an application-global last-user credential. A later request always constructs
  a fresh OBO client and fresh model clients from nonsecret stored selections.
- WORKBENCH_MODEL is forbidden in hosted mode so provider environment credentials
  cannot silently replace user-selected OBO serving routes. Tracing is disabled

## User-owned durable storage

using the requesting user's client. Catalog/schema/table checks use SDK metadata
APIs. [workbench/hosted_volumes.py](../workbench/hosted_volumes.py) wraps that same
OBO client for Volume metadata: SHOW VOLUMES, DESCRIBE VOLUME and CREATE VOLUME
IF NOT EXISTS run through SQL Statement Execution on the configured warehouse.
bypass. Both `unity-catalog` and `catalog.volumes` scopes were rejected by Apps;
the accepted requested scopes are `catalog.catalogs`, `catalog.schemas`,
`catalog.tables`, `files`, `model-serving` and `sql`, as in the
[bundle template](../deployment/hosted/databricks.yml).

WORKBENCH_CATALOG_PREFIX defaults to orion_dev_. The readable candidate combines
this validated prefix with a bounded safe slug from the resolved username.
Existing resources are reused only when owner exactly equals the resolved user
principal. A catalog owned by anyone else causes a deterministic 16-character
host/user-ID hash suffix. A collision at that second name fails closed. Group
and managed volume artifacts must also be owned by that user.

/Volumes/{catalog}/workbench/artifacts/users/{host-user-hash}. Content-addressed
Delta artifacts use the separate delta-state/users namespace described in
[delta-state-migration.md](delta-state-migration.md#artifacts-and-validation).
The user hash protects application namespaces, even if a username changes. UC
that owners/administrators cannot inspect user-owned storage.

- Browser-uploaded XML is uploaded to a hash-addressed Volume object before
- An existing Volume XML selection records its exact validated path and checksum.
  A temporary download is permitted for the existing parser. The source bytes
  are not copied into another persistent upload/blob. Changes/deletion/access
  revocation at the original path explicitly block later restoration rather than
  silently reading a different package. Keep referenced source objects immutable.
- SQLite files live only in an ephemeral local directory. Connections never open
  /Volumes paths. The runtime rebuilds tables from DDL-only artifacts and exact
  Direct Delta changes made outside this app process are not reflected by warm reads
  until a cold restore; a later mutation checks its parent generation and fails
  rather than publishing over a changed parent.
  unpickling. JSON metadata also comes from Delta records. SQLite backup bytes
  and Volume HEAD snapshots are not hosted restore or publication paths.
- Mutations upload immutable artifacts and a complete active manifest, append
  Delta records, independently verify inventory and parent, then publish and
  Missing/corrupt state fails closed, never falling back to a local backup.
- GET/HEAD/OPTIONS reconstruct existing authority but publish only exact new
  independent readback verify persisted audit columns/hash chains before the
  response. They do not upload artifacts or rewrite the whole content generation.
- The forwarded token is checked against every newly persisted byte stream.
  Identities, client configuration and model credential callbacks are never
  serialized. Raw user evidence remains sensitive and can contain other secrets:
  this is not a DLP guarantee. Do not upload secrets.

## Explicit operational limits and design deviation

The current implementation uses SQL-backed Delta operational state and Volume
visibility protocol is not a cross-table transaction or durable worker service.

- Exactly one app replica/process is required. The bundle sets min/max instances
  to 1. Rolling deployments must not run overlapping writers. Stop the old writer
  before starting a replacement. No distributed compare-and-swap lease exists.
  per-user queue. Routine overlapping polls wait, not return a dispatcher 409.
  Domain-level conflicts can still return 409. Two users can execute concurrently
  with the default configuration; capacity excess returns 429. Waiting in the
  queue still consumes the caller's request/proxy time budget.
- Working-state cap: 1 GiB aggregate and 20,000 files; generation inventory at most
  64 MiB, inline metadata payload at most 48 KiB before externalization. Existing
  upload limits remain ten files, 80 MiB/file and 160 MiB/batch. Cached XML may
  appear multiple times locally and counts against this aggregate cap.
- The first request for a user in a process restores the committed Delta generation
  into a bounded, process-local session and loads selected package indexes. Later
  requests for the same OBO-resolved user reuse that session and selected package
  index; they do not re-provision UC, re-read the full generation manifest, or
  reload XML-derived package artifacts before each agent action. The user identity
  and model clients remain request-scoped. Mutations serialize per user, flush a
  new committed generation, and update the retained session. A process restart,
  session eviction, cache miss, or storage-root change requires a cold restore.
  Direct Delta edits made outside this app process are not reflected by warm reads
  until a cold restore; a later mutation checks its parent generation and fails
  rather than publishing over a changed parent.
  Cold restore and fresh project-registry projection can still perform multiple
  remote calls; neither is a sub-second SLA. Observational reads that use the
  retained action path no longer publish a Delta audit tail on every request.
  Responses are spooled as needed. The historical 76.7-second full-overview
  measurement predates this warm-session path and is not a current latency result.
- The main UI request helper budgets **110 seconds for GET reads** and **30 seconds
  for mutations**. These are browser budgets, not server execution guarantees;
  other UI helpers can retain their own shorter budgets.
- Existing routes still return their normal 202 job record contract, but hosted
  responses are withheld until workers finish and durable flush succeeds. This
  is intentional bounded action completion, not a claim of background durability.
- Databricks Apps has a 120-second proxy timeout and a short SIGTERM grace period.
  Full XML analysis or six-role agents may exceed these limits. Cooperative
  provider timeouts cannot kill an in-flight call. The action is shielded from
  request cancellation while draining; process termination can still discard the
  uncommitted action. Browser timeout is not proof of failure or success: inspect
  committed state before retrying. Do not deploy large/long-running workloads
  without accepting these limits or approving a user-authorized worker design.
  A full XML analysis hit the proxy timeout but its committed result was verified
  later. Check job and committed project state before retrying an ambiguous action.
- Persistence failures return sanitized 503, not false success. Orphan
  immutable blobs can remain after an interrupted action. Garbage collection and
  retention policy are not implemented. Read-audit journal history grows storage,
  but reads do not create whole-state snapshots. A network failure during final
  receipt publication can have an ambiguous commit outcome:
  inspect restored state before retrying rather than assuming rollback.
- Existing local payload labels such as local_only, actor_authenticated:false and
  The hosted /api/health and /api/hosted/status report actual hosted authentication.
  Review attribution schema uplift is separate from authentication enforcement.
  Legacy record labels do not change the live request's resolved OBO identity.
  type from a CLI profile label. Model selection alone is not verified inference.

## Deployment configuration and packaging

Templates: [deployment/hosted/app.yaml](../deployment/hosted/app.yaml) and
[deployment/hosted/databricks.yml](../deployment/hosted/databricks.yml).
workbench Python modules, static assets, skill sources, exact-pinned requirements
and these two configuration templates into a NEW directory outside the checkout.
It never invokes a deployment command. It rejects symlinks, existing destinations
and files exceeding the Apps 10 MiB source-file cap. A package manifest supports
parent review. No repository-wide upload/sync should be used.

The stage function accepts destination, explicit workspace host, exact app origin,
and optional catalog prefix/warehouse. Runtime placeholders are replaced only in
the staged copy. Source templates cannot start without reviewed real values.
Neither .env, .venv, output, input, test data nor local credentials are packaged.
The root pipeline requirements are unchanged. Apps receives the pinned workbench
requirements as its top-level dependency declaration, including databricks-sdk
0.157.0 and langchain-openai 1.7.3. These are exact direct-dependency pins, not a
transitive lockfile. Historical installed-environment pip check passed; that alone
does not establish the health of every current or future clean dependency build.

The bundle defaults to app `orion-migration-workbench`, one replica, and the six
accepted scopes above. A SQL warehouse is required for Delta state and SQL Volume
metadata. The engine requires explicit WORKBENCH_SQL_WAREHOUSE_ID and has no
warehouse default. The staging helper defaults to the existing approved
`e3a0fc2c08db05bb`; its warehouse option can select another reviewed existing
warehouse. Neither helper nor adapter creates a warehouse. Users still need
CAN_USE and exact UC ownership/permissions in addition to OAuth consent.

Historical external staging validation passed, but was structural validation,
not evidence of live scope acceptance. That earlier no-deployment review is
deployment, remote mutation, or fresh live authorization test.
## Active XML versus historical archive

- Hosted project `84b03a652f5e4371ba1c72d401acba6b` uses a fresh XML upload and
  analysis, not old generated output. Reported parsed counts: 243 workflows,
  434 mappings, 592 sessions, 583 sources, 307 targets, 165,013 nodes and 529,237
  edges. Parse completion does not prove semantic parity or release readiness.
- Historical local-state import is a separately VERIFIED archive, reported as 5,764 metadata
  rows before its receipt, 1,404 objects, 100 files and 300,651,539 object bytes.
  The supplied detailed counts have an unresolved two-row discrepancy against
  that overall total, explicitly recorded in
  [the migration inventory](delta-state-migration.md#verified-historical-import-2026-10-30).
  reported 583 empty source tables across 136 schemas (27,908 columns), not

## Remaining verification and authority boundaries

1. Review the small injection seams in app, projects and volume intake modules.
   No CSS, SVG, branding tests or explanation logic were edited.
2. Run the hosted/offline tests plus the full existing workbench suite. Inspect
   the existing project-document timeout separately, without relaxing tests.
3. Generate an external allowlisted staging package and inspect its manifest.
4. Validate the bundle against the target CLI schema, including single-instance
  controls and accepted scopes. Review the exact app URL and workspace host.
5. Test clean dependency installation on the Apps Linux Python runtime.
6. Further deployments require explicit approval. Expand live evidence for
  missing-token rejection, owner-safe reuse, source reference behavior, model
  inference, two-user isolation, restart recovery and SIGTERM behavior. Existing
  OBO health success does not establish this entire matrix. Resolve request
  latency and long-action timeout risk before claiming production readiness.
7. Leave generation/deployment/publication gates closed. This work does not build
   the missing migration generator or authorize any user release deployment.

## Historical verification, 2026-10-29, not current full-suite clearance

Commands were run from the checkout using its configured Python 3.13 venv with
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 for pytest. No tests were removed or weakened.

- Python -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted.py
  workbench/tests/test_hosted_package.py -q --tb=short: **23 passed**.
- Python -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted.py
  workbench/tests/test_volume_intake.py workbench/tests/test_projects.py -q
  --tb=short: **98 passed, 1 failed**. The failing existing test is
  test_stage_then_explicit_analyze_partial_statuses_snapshots_and_documents,
  which times out waiting 30 seconds for a batch Future. It reproduces in
  isolation. A diagnostic-only faulthandler run also captured unchanged
  domain._redact processing the fixture's 70KB document. This is not sufficient
  evidence to assign the worker timeout's root cause, and no unrelated domain
  patch was made.
- Python -m pip check: **No broken requirements found**.
- Python -B -m pytest -c workbench/pytest.ini workbench/tests -q --tb=short:
  full-suite run initiated separately. Do not treat the hosted result as full
  regression clearance; a final full-suite summary was not yet available at the
  time this verification note was written.
- Databricks bundle validate --profile labs -t dev on the temporary allowlisted
  stage: **Validation OK**, exit 0. That validation did not deploy the app.
- Pylance diagnostics for hosted entrypoint: no error diagnostics after fixing
  reportArgumentType through a correctly typed BaseChatModel dictionary copy.

These are historical scoped results, not a claim that the current entire
regression suite is green. Later full-suite runs recorded failures; see the
[authority verification history](hosted-delta-authority.md#historical-regression-results-2026-10-29).