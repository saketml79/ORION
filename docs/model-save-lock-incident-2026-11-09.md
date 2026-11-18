# Model-save ownership lock incident

## Observed root cause

The retained hosted APP trace at 2026-11-10T01:59:50Z reports
`RuntimeError: The current task is not holding this lock`, from AnyIO's
Python 3.11 asyncio backend. The dispatcher acquired the AnyIO task-owned lock
through `asyncio.wait_for(lock.acquire(), timeout=90)`, then released it from
the parent dispatcher task. Python 3.11 schedules that acquisition in a child
task. A failed release also skipped the following queue-counter cleanup.

Fresh retrieval of hosted logs confirmed same-owner model-save waits of
90.146, 90.195 and 90.160 seconds, followed by HTTP 503 at 02:23:30,
02:25:00 and 02:26:30 UTC. Diagnostics identify the old queue-timeout boundary.
Saved status independently reported OpenAI-gpt-4o-mini with six configured
roles. That saved route is not proof that any particular failed Apply succeeded.

Local Python 3.13.14 uses a same-task timeout context inside `wait_for`, so the
literal reproduction releases successfully locally. Explicit child-task
acquisition reproduces the hosted ownership exception and a still-held lock.
Regression coverage therefore emulates Python 3.11 task scheduling rather than
assuming a passing Python 3.13 test rules out the production defect.

## Implementation boundaries

- [Dispatcher](../workbench/hosted.py): `anyio.fail_after` surrounds direct
  acquisition in the releasing task. No thread-lock substitution, lock stealing,
  broad deadlock bypass or timeout polling wrapper. Cleanup remains mandatory
  on cancellation, scope denial, expired authentication and downstream failure.
- Direct competing writes return HTTP 409 promptly with same-actor writer
  metadata. Durable workers have a bounded 90-second wait outside the originating
  proxy request; the per-user pending count is bounded at eight. Owner keys
  include the freshly verified workspace and actor. Observational reads bypass
  the writer. Heartbeat cleanup cannot skip lock release.
- [Operations](../workbench/hosted_operations.py) validates a small model-select
  body, exact expected saved-route version and scope-bound operation fingerprint.
  Durable queued receipt and readback precede HTTP 202. Existing IDs do not replay.
  Worker re-entry obtains a fresh OBO identity; polling obtains its own fresh
  identity. No credentials enter the journal or browser storage.
- [Route writer](../workbench/hosted_model_writes.py) verifies the committed parent,
  scope membership and expected route version, lists fresh endpoint metadata,
  retains every unrelated record, and publishes a complete commit-last Delta
  generation. Exact route/version readback is required. No XML, SQLite, audit
  restoration, six client constructors or model inference is necessary for this
  metadata mutation. This remains a single-replica protocol, not distributed CAS.
- [Browser receipt helper](../workbench/static/model-route-save.js) records a
  nonsecret operation ID before submission, polls that exact receipt, and retains
  failed/uncertain IDs. A proxy error triggers observation, never automatic POST
  replay. New Apply is unavailable while unresolved; reconnect observes the old
  ID. [Picker](../workbench/static/workspace-access.js) distinguishes previously
  saved configuration from an unconfirmed pending operation.
- Pending selected agent-history projection/UI changes are included in the same
  source candidate and regression gate. No inference, Jobs execution or business
  table mutation is authorized by this release.

The saved-preference blueprint directory/PDF is absent in this checkout. The
available proposal-first design and existing durable Delta/OBO boundaries were
retained. A proxy/network failure remains possible; this correction is not a
guarantee that every HTTP request can never return 502 or 503.

## Verification and release

Implementation is complete before the consolidated regression pass. Final
commands, counts, app/deployment identity, UTC timestamps and authenticated
model-save receipt/timings will be recorded here after they are actually observed.
The live check must first inspect saved state and outstanding work, then submit
only the explicitly requested OpenAI-gpt-4o-mini metadata save, without inference.

Consolidated command: selected virtual environment executable, `-B -m pytest -c
workbench/pytest.ini`, the 25 modules below, then `-q --tb=short
--disable-warnings -ra`. The first run completed with 621 passed, one failed
stale guidance assertion and two warnings in 590.48 seconds. The assertion
incorrectly forbade the eight existing package-readiness `inert` sections. Its
replacement still forbids a global browser-shell lock/modal and explicitly
requires the Explorer readiness lock. No production change followed that run.
The identical full module list is rerun, not only the failed test.

- [Lock ownership](../workbench/tests/test_hosted_lock_ownership.py)
- [Scoped model reads and actual asynchronous saves](../workbench/tests/test_hosted_model_reads.py)
- [Browser save receipts](../workbench/tests/test_model_route_save_ui.py)
- [Hosted authorization and mutations](../workbench/tests/test_hosted.py)
- [Operations](../workbench/tests/test_hosted_operations.py)
- [Intake integration](../workbench/tests/test_hosted_intake_integration.py)
- [Intake browser](../workbench/tests/test_hosted_intake_ui.py)
- [Scoped agent-history reads](../workbench/tests/test_hosted_agent_reads.py)
- [Delta runtime](../workbench/tests/test_delta_runtime.py)
- [Delta state](../workbench/tests/test_delta_state.py)
- [Deployment package](../workbench/tests/test_hosted_package.py)
- [Registry](../workbench/tests/test_hosted_registry.py)
- [Scoped objects](../workbench/tests/test_hosted_object_reads.py)
- [Workspace picker](../workbench/tests/test_workspace_access.py)
- [Model clients](../workbench/tests/test_databricks_models.py)
- [Workspace metadata](../workbench/tests/test_model_workspace.py)
- [Loop UI](../workbench/tests/test_loop_ui.py)
- [Arena](../workbench/tests/test_agent_arena.py)
- [UI structure](../workbench/tests/test_ui_structure.py)
- [Scoped history browser](../tests/test_agent_history_scoped_ui.py)
- [Navigation](../tests/test_navigation_prerequisites.py)
- [Hosted startup](../tests/test_hosted_startup.py)
- [Intake controls](../tests/test_intake_loading_controls.py)
- [Loading diagnostics](../tests/test_loading_diagnostics_ui.py)
- [Guidance](../tests/test_next_action_guidance.py)

Initial syntax gate: 90 Python AST parses and every static JavaScript file passed.
Editor diagnostics on the corrected dispatcher, journal, writer, lock tests and
picker reported no errors. These checks do not replace authenticated live proof.

Final identical 25-module consolidated gate: **622 passed, two warnings in
544.35 seconds**, no failures, skips or deselections. Terminal reported
`CONSOLIDATED_REGRESSION=PASS; NO_INFERENCE`. All implementation preceded the
first gate; only the documented stale test assertion changed between runs.
No production/test edits followed the final passing gate.

Release preflight completed at **2026-11-10T03:14:00.627084+00:00**:
122 allowlisted package entries; staged Python AST, every JavaScript syntax check,
seven isolated hosted module imports and `databricks bundle validate --profile
labs -t dev` passed. Stage hashes are checked again against the current source
before deployment. The existing app baseline is
`cfada07d-ffa6-4018-9080-293f5e577083`, previous deployment
`01f1dca476e11faba3db9b229207ab34`. Six requested OBO scopes, effective scopes,
no attached resources, MEDIUM compute and minimum/maximum one instance are pinned.

The single deployment attempt started **2026-11-10T03:14:14.718771+00:00**.
It runs bundle deploy, existing-app stop, then bundle run for the same app.
The command chains authenticated live verification only after deployment passes.
No deployment success or live-save completion is claimed until recorded below.

Authenticated live model-select submission returned **HTTP 202 in 15.184
seconds**, with operation `85f2b4d5acb7450e8f07395cad7c1737` and process
`18cf5ffb7af74e4194acc2cce3e97b24`. The accepted receipt was queued; subsequent
GET observations confirmed running/provisioning. Only one POST was sent.
This establishes acceptance below the proxy deadline for this sample, not yet
completion or a general latency guarantee. The saved baseline remained
OpenAI-gpt-4o-mini with six configured roles, READY metadata and runtime/tool-call
verification false. Final committed receipt and unrelated-state comparison follow.

### Completed release and live proof

**Deployment SUCCEEDED, app RUNNING.** Single deployment
`01f1dcb5420d1c428a6885eb3e77663c`; existing app ID
`cfada07d-ffa6-4018-9080-293f5e577083` preserved. Release interval:
**2026-11-10T03:14:14.718771+00:00 to 03:20:21.320522+00:00**.
Platform deployment create/update: **03:17:28Z / 03:20:14Z**.
Requested/effective OBO scopes, no attached resources, MEDIUM compute and one
instance were compared before/after and unchanged. All **29 deployed static
assets** matched the staged byte SHA-256 values.

Actual authenticated HTTP checks, not static assertions:

| Check | Result | Seconds |
|---|---|---:|
| Scoped agent-run list | HTTP 200, no active run | 9.523 |
| Scoped loop history | HTTP 200 | 11.672 |
| Scoped tracing status | HTTP 200 | 5.803 |
| Model-select POST | HTTP 202, verified durable queued receipt | 15.184 |
| Submission to observed terminal receipt | Completed, underlying action HTTP 200 | 156.407 |
| Final saved-route GET | HTTP 200, exact receipt route version | 7.767 |
| Final hosted diagnostic GET | HTTP 200, no failure entries | 0.231 |

The single live POST began **03:22:02.610119 UTC**. The completed receipt was
observed at **03:24:39.016640 UTC**. Operation ID remains
`85f2b4d5acb7450e8f07395cad7c1737`; result is OpenAI-gpt-4o-mini for project
`84b03a652f5e4371ba1c72d401acba6b`, package
`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.
Previous and committed route version both equal
`0f794cfa9df2a4e9b47cc3d05ba96da40a2352df974bf4b9c95d289826d95184`,
so this explicitly authorized save retained the already selected endpoint.
No second POST, automatic retry, model inference, Jobs execution or business-data
write occurred. No old failed operation/history was deleted or relabeled.

The parent generation `4888a55197e84cf081e0ce568490a7c5`, sequence 14, advanced
exactly once to `f3fad889206d4044afa9e5a86f363ae8`, sequence 15. Fresh independently
authorized readback verified **538 unrelated records across all 20 table
families**, comparing every retained record ID/content hash after excluding
only the selected model-routing source path. Canonical comparison SHA-256:
`80cf3d8075747f2d48c1772f3c9d1e9db49ff406d77cfa8bd5a1827a7fb045a2`.
The exact parent/sequence relation also confirms no intervening committed writer
was silently overwritten during this check. Route metadata, not actual inference,
is ready; runtime and tool-call verification remain false.

Final verification finished **2026-11-10T03:25:13.048726+00:00**, with
`LIVE_SMOKE=PASS`, unrelated state preserved and inference invocations zero.
The owned external staging directory was removed after success. No production
or test changes occurred after the final 622-test gate; subsequent edits were
release documentation and the external observational release helper only.

**Remaining performance boundary:** completing the full durable generation still
took approximately 156 seconds in this sample. The originating request no longer
holds that work behind the 120-second proxy limit; it returns its durable receipt
in approximately 15 seconds and the UI observes completion. This is not a claim
of instantaneous persistence or a guarantee against every future gateway failure.

### Source and test manifest

This incident directly changes [dispatcher](../workbench/hosted.py),
[operation request validation](../workbench/hosted_operations.py),
[picker pending-state copy](../workbench/static/workspace-access.js),
[new ownership/cancellation tests](../workbench/tests/test_hosted_lock_ownership.py),
[model-save tests](../workbench/tests/test_hosted_model_reads.py),
[direct-write contention integration](../workbench/tests/test_hosted_intake_integration.py),
and the [guidance assertion](../tests/test_next_action_guidance.py).

The candidate also retains the already present local
[metadata writer](../workbench/hosted_model_writes.py),
[versioned saved-route projection](../workbench/hosted_model_reads.py),
[browser receipt helper](../workbench/static/model-route-save.js),
[receipt browser regressions](../workbench/tests/test_model_route_save_ui.py),
[HTTP save fixture](../workbench/tests/test_hosted.py),
and [script inclusion](../workbench/static/index.html). Pending history work is
staged together: [history projection](../workbench/hosted_agent_reads.py),
[main UI](../workbench/static/app.js), [loop UI](../workbench/static/agent-loops.js),
[tracing UI](../workbench/static/tracing-status.js),
[backend history regressions](../workbench/tests/test_hosted_agent_reads.py), and
[browser history regressions](../tests/test_agent_history_scoped_ui.py).
The unchanged allowlisted [stager](../deployment/stage_hosted.py) copies the full
reviewed workbench source, not a partial replacement of those pending changes.
The [historical setup note](agent-model-setup-2026-11-09.md) now points here.