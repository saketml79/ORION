# Selected proposal loading and shared read ownership

## Confirmed facts and live boundary

- The reported text came from `loadProposalDetail`, the deterministic structural
  baseline, not the saved agent design. `loadPage('proposals')` unconditionally
  loaded candidates. A selected workflow then automatically loaded its baseline
  detail, even while the baseline disclosure was closed.
- Request wrappers in different scripts did not share an in-flight owner.
  Hosted `read()` allocated another operation ID and durable read envelope for
  every caller. Repeated baseline rendering also issued an original-object read.
- Hosted immutable index reuse applied only to redesign routes. Object,
  migration-context, proposal, readiness and lineage presenters materialized
  the same compressed canonical graph before `PackageIndex.load` could reuse it.
- The prior release receipt is verified, not pending: deployment
  01f1dd5e1fc51cc786ab0a642e11e46b, release started
  2026-11-10T23:22:29.484850+00:00 and finished
  2026-11-10T23:29:20.278873+00:00. Prior smoke finished
  2026-11-10T23:31:05.360520+00:00. This does not deploy this correction.
- One new authenticated hosted selected WIL bronze redesign-list GET used a
  25-second observation bound. It timed out after 25.175 seconds with no body.
  There was no retry. This confirms an unconfirmed slow read, not a measured
  15-minute server phase or proof that this candidate has improved hosted timing.
- A separate fast-path diagnostic GET returned HTTP 200 in 0.657 seconds with
  no retained error events. It does not expose a successful slow request's phase
  and does not establish a root cause for the reported full 15-minute duration.
- The shared local page has a different selected project from hosted. Local
  observations must not be substituted for hosted saved-design results.
- Fresh read-only app metadata at 2026-11-11T02:37:01.022874+00:00 still reports
  that same deployment ID, SUCCEEDED and RUNNING. This candidate is not deployed.
- The requested agentic-blueprint files and PDF are absent from this checkout.
  Existing durable authority, consent, version and execution gates are retained.

## Correction and ask ledger

- Baseline remains fully available, but its list/detail/object/review reads begin
  only on explicit disclosure expansion. Saved design loading is independent.
  Scenario/workflow changes invalidate old baseline tickets without silently
  starting a hidden baseline. Release-page gate reads remain available.
- Re-selecting a pending or loaded baseline does not render/fetch it again.
  Failed detail selection is cleared so a deliberate retry remains possible.
  Exact saved-design version presentation has one in-flight owner.
- [The shared coordinator](../workbench/static/scoped-reads.js) is page-memory
  only and explicitly used by application, redesign, hosted intake, readiness,
  lineage, connections, loops, workspace/model settings, tracing, validation and
  conversion clients. It does not replace global `fetch`.
- Identity includes current invalidation revision, transport, full same-origin
  URL with stable query-key ordering, captured headers and fetch credential,
  mode, redirect, integrity, cache and referrer policy. Only the read-only
  mutation-marker/diagnostic-ID headers are excluded. Repeated query-value order
  is preserved. Project, package, scenario, design version, exact query and any
  authority-bearing headers remain distinct. Response bodies/JSON are cloned.
- No completed response or permission verdict is cached. Completed/error reads
  are evicted. Status/history/job/model/health observations are never joined to
  an earlier poll. Mutations are never coalesced or replayed. Mutations invalidate
  ownership on submission and response; observational envelopes/heartbeat are
  not misclassified as writes. Explicit refresh/context/terminal-run events
  prevent joining stale reads.
- Stalled reads terminate the UI wait after 120 seconds without confirmed
  progress. Actual stage/count advancement extends a healthy durable read;
  timestamps and identical polls do not. Failure retains the recorded operation
  ID and explicitly says completion is unconfirmed. There is no automatic retry.
  Large explicit exports are not buffered by this coordinator.
- [The hosted graph owner](../workbench/hosted_object_reads.py) now applies across
  all existing selected canonical presenters. A single Future coalesces cold
  hydration for the exact verified content/actor/namespace/project/package key.
  One graph remains retained, with a request-local directory facade. A failed
  hydration is not cached. Source-analysis identity changes invalidate reuse.
- Every network request still obtains fresh OBO authority and verifies the
  current committed manifest, membership, complete queried inventories and
  owned Volume authorization before graph reuse. Mutable evidence and approval
  are still read afresh. No TTL identity cache, local authority fallback,
  permission inference, model invocation or business Job is introduced.
- Cold corrupt artifact materialization still fails closed. A previously verified
  warm immutable graph is not replaced with subsequently corrupt remote bytes.
  Corruption regression explicitly evicts the warm data to test materialization;
  warm Volume-permission revocation is separately tested.

## Verification

### Selection ownership follow-up

- Recovered the original active regression terminal from the session transcript:
  `179cb103-fc7d-49b6-b444-609f28563c00`. Its first XML was the older
  collection-error receipt, not the running batch's result. No second batch was
  started while that process remained active.
- Initial-selection race fixtures must explicitly expand the baseline before
  expecting a candidate read. This corrects the test setup to the lazy contract;
  it does not weaken scope, selection or failure assertions.
- Rapid top-selection changes now share the pending candidate-list owner for
  the same epoch/scenario. Intermediate choices clear the old detail immediately.
  Completion reconciles the final filter, making one additional read only when
  that filter differs. Scenario/package epochs remain independent and old
  successful or failed responses cannot publish into them.
- Explicit global refresh retires the baseline presenter ticket. Saved-version
  ownership includes its presentation epoch and is cleared by invalidation, so
  refreshing the identical scope/version cannot join a superseded detail read.
  The new production-viewer regression deliberately defeats AbortController.
- Explicit same-context refresh retains the actual verified graph DOM, including
  its SVG, task plan and recorded version, with an unconfirmed notice. It does
  not retain review forms, code actions, consent or download authority. The old
  tab labels are removed and its notebook button is disabled. HTTP
  402/409/429/503 remain genuine errors. An exact successful reread replaces
  that historical graph and creates unchecked consent. Package/scenario/workflow
  invalidation still clears the graph rather than showing another context.
- Project-scoped intake-job and hosted-upload status GETs are now explicitly
  live observations, alongside agent/job/loop/operation/model status. Added
  coordinator cases retain 402/409/429/5xx responses as real failures and prove
  post-completion write invalidation and ignored-abort late-result rejection.
- Hosted read ownership no longer depends on the originating subscriber's
  `isCurrent` predicate. Polling belongs to the shared owner's signal, while
  each subscriber retains its own cancellation, status and completion guards.
  Captured scope headers cannot be mutated after key construction. The added
  actual-helper test cancels the originating view, changes its caller-owned
  header object and verifies the second view still receives the one recorded
  operation's result in the original captured package scope.
- Artifact POST regression budgets now assert one canonical archive body plus
  two fresh committed manifests across two pure compilations, not a bare change
  from four downloads to three. Mutation/CSRF/active-writer/per-user/no-publication
  checks remain intact. Corruption fixtures explicitly evict the warm graph to
  exercise fail-closed cold materialization rather than claiming that a verified
  immutable in-memory graph must download the same bytes again.
- The existing real hosted analysis-rebuild regression now warms the old graph
  before rebuilding. It checks that the committed analysis version changes the
  cache identity and the cached model version, then compares the subsequent
  projected response. This is an offline regression, not a live rebuild.
- Local navigation exercised Projects, Estate, Explorer, Agent activity,
  Proposals, Evidence, Validate, Readiness, CI/CD, Logs and Evolution, returning
  to Proposals. Final Resource Timing recorded one new request each for projects,
  input packages, selected project, object inventory, agent history, tracing,
  loops, documents, readiness, settings, baseline list and logs. The baseline
  list was explicitly required by the CI/CD gate presenter, not by opening the
  collapsed Proposals disclosure. A superseded connections read was aborted by
  navigation. No model invocation, generation, save, business Job or deployment
  action was selected. Temporary browser listeners were removed in finally.
- After the selection/refresh corrections, one further local reload loaded the
  current static scripts without a backend restart. Saved-design list: one HTTP
  200, 0.230 seconds. Exact saved v2: one HTTP 200, 0.098 seconds. Two logical graph
  nodes rendered; baseline remained closed with zero proposal/object/review GETs,
  zero open modal dialogs and zero inert application roots. Stale-input and
  blocked-drafting status remained visible. Repeated model workspace/status GETs
  were metadata observations, not endpoint selection or model invocation.
- Final local browser check with the retention/security-policy changes loaded:
  initial list/detail were 0.250/0.125 seconds, one each. One explicit refresh
  held only the outgoing saved-design list to inspect the pending DOM, then
  continued the real GET. The exact same SVG remained mounted, version 2 was
  labelled unconfirmed, review forms and drafting actions were absent and the
  notebook button disabled. The baseline stayed closed. Refresh list/detail
  returned HTTP 200 in 0.218/0.136 seconds, one each; the SVG was replaced only
  after exact saved-version confirmation, consent remained false and the
  historical stale-input gate remained blocked. The temporary route was removed
  and element handle disposed in finally. No fake response or model invocation
  was used for this local refresh check.

The shared local page was reloaded once with the new scripts, without restarting
the backend. Real browser Resource Timing recorded saved-design list 1.822 seconds
and exact saved-version detail 0.098 seconds. The actual saved v2 design rendered
two logical graph nodes. The baseline remained closed with zero proposal, object
or review reads and no visible operation dialog. Existing stale-input status and
blocked drafting approval were preserved. These are local observations, not
hosted before/after timings or runtime/parity proof. The first browser wait used
a list-state text matcher, but the page had already progressed to the exact saved
version status. Inspection confirmed successful rendering; no reload was repeated.

All changes were collected before the consolidated regression. An initial command
used a nonexistent loop-test path and ran zero tests. Correcting that path exposed
an indentation error in the new helper test during collection. Its indentation
was corrected and all five changed Python files passed AST parsing before the
full 33-module batch started. That batch is still running; no green claim yet.

No new deployment or local restart has been performed. Previous 575-test
source/readiness verification remains a separate historical receipt, not
verification of this change.

Consolidated command, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`; JavaScript syntax
was checked for every actual static script using `node --check` first:

  .\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini tests/test_scoped_reads_ui.py workbench/tests/test_immutable_read_ownership.py tests/test_proposal_selection_sync.py tests/test_original_workflow_integration.py tests/test_readiness_ui.py tests/test_explorer_graph_ui.py tests/test_navigation_prerequisites.py tests/test_project_package_autoload.py tests/test_migration_lineage_ui.py tests/test_next_action_guidance.py tests/test_fixture_metadata.py workbench/tests/test_proposal_primary_ui.py workbench/tests/test_redesign_ui.py workbench/tests/test_native_task_view_ui.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_hosted_workflow_artifacts.py workbench/tests/test_review_actual_workflows.py workbench/tests/test_readiness.py workbench/tests/test_hosted_readiness.py workbench/tests/test_hosted_review_reads.py workbench/tests/test_capability_status.py workbench/tests/test_ui_structure.py workbench/tests/test_requested_ui.py tests/test_hosted_startup.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_ui.py tests/test_connections.py workbench/tests/test_loop_ui.py workbench/tests/test_hosted_agent_reads.py workbench/tests/test_hosted_model_reads.py workbench/tests/test_hosted_package.py -q --tb=short --disable-warnings -ra --junitxml="$env:TEMP/orion-read-ownership-20261110.xml"

No tests are excluded. The final XML must independently confirm totals, failures,
errors and skips before any green claim. Temporary browser request listeners and
the trace global were removed after the local observation. No temporary source
instrumentation was introduced.