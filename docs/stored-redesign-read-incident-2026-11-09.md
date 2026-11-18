# Stored redesign read failure: live evidence and local correction

## Observed, read-only investigation

The explicit `labs` user profile reports user `ssuman@bpcs.com`. App metadata
reports existing app `cfada07d-ffa6-4018-9080-293f5e577083`, RUNNING, with active
deployment `01f1dcb5420d1c428a6885eb3e77663c`. No deployment was requested or run
in this investigation. The saved-preference blueprint directory and PDF are absent
in this checkout; the proposal-first design was consulted instead.

Authenticated `databricks apps logs` confirms the failing route is
**GET /api/redesigns**, not workflow boilerplate generation:

| Request start UTC | Proxy log timestamp UTC | HTTP |
|---|---|---|
| 2026-11-10 06:51:56 | 06:53:29 | 502 |
| 2026-11-10 06:52:56 | 06:54:56 | 502 |
| 2026-11-10 06:56:29 | 06:58:30 | 502 |
| 2026-11-10 07:23:13 | 07:25:14 | 502 |

The last three intervals are approximately 120 to 121 seconds. These access logs
do not retain query parameters, so they do not prove the screenshot's workflow or
scenario. No exception stack for these redesign requests appeared in the retained
3,000-line server log window. Do not equate another route's authentication error
with the redesign failure.

A fresh authenticated **GET /api/hosted/diagnostics** timed out locally at the
helper's 115-second deadline. Server access logs independently show that same
request as HTTP 502, start 07:33:58 UTC, proxy log timestamp 07:35:53 UTC.
No diagnostic response body was received. Therefore exact backend phase timings
and a redesign exception stack remain unconfirmed, not successful live proof.

Read-only export of the exact active deployment source, using supported AUTO
format for file assets, confirmed that its canonical reader has **no redesign
route/projection**. Its SHA-256 is
`0e1538c94b2a9356dd4073e9de74e455d9e16ca599b4bdfa4344a841abc77ebd`.
The deployed dispatcher SHA-256 is
`f84d70bcbd595739a081d676df6fa6dad1e5a9c5ce2ce5adfa9aad03cc270f05`.
Thus the active version cannot use the pending local scoped redesign path.
The observed 502 deadline and confirmed deployed full-restore path support the
read-amplification diagnosis; they do not establish which individual SQL call
consumed the deadline. The initial RAW export was rejected as unsupported; AUTO
exports succeeded. No credentials were printed, no alternate identity was used.

## Local implementation boundaries

- [Canonical reads](../workbench/hosted_object_reads.py) retain fresh OBO identity,
  project/package membership, a pinned committed generation, complete scope
  inventory checks and content/envelope verification. JSON metadata is hydrated
  from verified authoritative Delta rows, not first downloaded from a redundant
  file snapshot. Canonical gzip remains pinned and verified. No XML reparse.
- [Evidence hydration](../workbench/readiness.py) filters selected workflow or
  exact design identity before decoding design artifacts. List/detail use design,
  review, proposal and document evidence, with loop lessons only when needed.
  Original and bronze remain distinct. List pages remain bounded, default 50,
  explicit 20 supported; full executable payloads are not returned in list items.
- [Freshness](../workbench/redesign.py) hashes the same base snapshot and exact
  approved lesson window as the existing hosted restored SQLite path: verified
  Delta row insertion order, last 20 approved workflow lessons, then sort by ID.
  This is deliberately not lesson timestamp or insertion-order approximation.
  Missing lesson evidence fails closed, never falls back to full runtime restore.
  Historical designs/reviews remain immutable and stale approval stays blocked.
- [Dispatcher](../workbench/hosted.py) returns the selected canonical response
  directly, without restoring a second time or constructing an application/model
  runtime. No Jobs, audit tail, all-history or source XML dependency is added.
- [Review browser](../workbench/static/redesign-review.js) uses the existing
  durable hosted read helper for exact list/detail routes, including run handoff.
  It does not replay a failed read as a direct GET. List choices survive a failed
  same-context refresh as explicitly unconfirmed, with old review controls removed.
  Context changes still clear old choices/consent. Direct errors identify their
  method/path/status without trying to parse a proxy HTML error as JSON.

No model invocation, application POST, job execution, business-data write,
schema change, historical record mutation or deployment occurred live. Offline
tests use fake Delta stores and fake HTTP responses only. Existing durable read
receipts are an asynchronous transport boundary, not a latency improvement claim.

## Verification

Initial complete 18-module consolidated run: **475 passed, 2 failed, 1 skipped,
6 subtests passed, 1 warning in 674.53 seconds**. Both failures were new browser
regressions. One exposed the explicit refresh handler still calling invalidation
that cleared choices before the read; it now retains same-context choices while
still invalidating review consent and all context-change results. The other was
Playwright invoking a returned assigned mock function; the fixture now installs
that mock inside a function returning no value. No assertion was weakened.

Final identical 18-module consolidated result: **477 passed, 1 skipped,
6 subtests passed, 1 warning in 605.59 seconds**. Zero failures or deselections.
The skip is the existing Windows symlink permission case. All seven Python AST
checks, JavaScript syntax check and changed-file editor diagnostics passed.
No production/test edits followed the final passing gate.

Exact regression command (run from workspace root with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`):

`.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_object_reads.py workbench/tests/test_readiness.py workbench/tests/test_redesign.py workbench/tests/test_redesign_ui.py workbench/tests/test_run_review_fixes.py workbench/tests/test_hosted.py workbench/tests/test_hosted_agent_reads.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_ui.py workbench/tests/test_hosted_lock_ownership.py workbench/tests/test_hosted_model_reads.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_runtime_planning.py workbench/tests/test_agent_arena.py tests/test_spark_codegen.py tests/test_spark_codegen_api.py -q --tb=short --disable-warnings -ra`

JavaScript syntax command: `node --check workbench/static/redesign-review.js`.
Python syntax used `ast.parse` on the four changed backend modules and three
changed test modules, all seven successful. Terminal completion marker:
`STORED_DESIGN_REGRESSION=PASS; OFFLINE_ONLY; NO_DEPLOYMENT; NO_INFERENCE; NO_TEST_EXCLUSIONS`.

### Ask ledger

- Exact failed route and active deployment: verified from live logs/app metadata.
- Active version lacks pending scoped redesign reader: verified by source export.
- Exact screenshot workflow/scenario and exception/phase: not retained by logs;
  diagnostic GET failed. No invented backend timing or stack is reported.
- Selected original/bronze list/detail, default 50 and explicit 20 paging,
  full-restore/model-construction prohibition: verified offline.
- Loop lesson order/window and fresh lesson revision invalidation: verified offline
  against the existing full-restored SQLite input hash; old design unchanged.
- Fresh cross-user/project/package denial and envelope integrity: verified offline.
- Exact saved-version handoff and durable helper transport, no direct replay on
  helper failure: verified in offline Chromium.
- HTML 502 refresh retains unconfirmed choices, removes approval controls and
  does not claim an empty stored list: verified in offline Chromium.
- Deployment and live corrected-route latency: **pending explicit authorization**.

No new hosted latency improvement is claimed until explicit deployment and live
verification. No temporary source, trace hooks, deployment files or captured log
files were created in the repository by this investigation.