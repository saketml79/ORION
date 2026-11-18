# Agent activity: explicit model setup and four-step review

Current follow-up: the [model-save ownership incident](model-save-lock-incident-2026-11-09.md)
supersedes the historical synchronous Apply/full-runtime path described below.
It records the actual Python 3.11 task-ownership failure, same-task lock fix,
durable asynchronous Apply receipt, scoped complete-generation metadata save,
and the separately reported regression/deployment evidence. Earlier test counts
in this document remain evidence for their own revisions, not the current release.

Local implementation only. The original implementation and 341-test follow-up below were offline. The later measured-latency follow-up records explicitly authorized live metadata reads. No deployment, inference, route application, Jobs or business-data changes were performed.

## Root cause and boundaries

- Agent activity navigation previously required loaded package understanding. The model picker also rejected a project without a package.
- Hosted model status and discovery previously fell through to full committed Delta/runtime restoration, package app construction and runtime setup. Discovery could save a default route.
- Model setup is now accessible independently. Discovery verifies fresh requesting-user OBO and lists endpoint metadata, without storage provisioning, XML loading, route writes, client construction or inference. Missing/failed endpoint discovery never enables selection.
- Saved-model status reads the pinned committed generation, verifies project/package membership when supplied, and validates complete queried artifact/model-route scope inventories. Only the matching model routing blob is downloaded. Checksums, route payload ownership and READY chat revalidation remain required. No local or legacy authority fallback was added.
- Project plus package uses its existing package-context route. Project without package uses the existing user/root route after project membership verification. There is no implicit root-route inheritance into a package.
- Explicit Apply retains the existing serialized full-runtime restore and commit-last durable save. It may still report package restoration. This change does not claim that durable route writes are optimized or that all package reads are fast.
- Missing existing hosted storage can still reject the optional saved-settings read. Discovery remains available, and no tables are initialized by a read.
- Package fingerprint, recorded-run readiness, runtime availability and egress consent remain required for assessment. Loop history and tracing-settings reads no longer trigger package restoration merely because model setup is visible.

## UI ask ledger

1. STEP 1 MODEL: exact heading “Select model endpoint from workspace”. Metadata-only explanation, explicit blank selection, tool-call declaration and Apply. Saved settings do not block the endpoint inventory.
2. STEP 2 SOURCE & SCOPE: existing workflow, source scenario and instruction controls, with deterministic source/package context explained. No new cohort-conversion capability is claimed.
3. STEP 3 ASSESSMENT: existing explicit run button and data-egress consent, with separate design/code/execution approval boundaries preserved.
4. STEP 4 RESULTS & HUMAN REVIEW: existing recorded runs, evidence and six-role arena. The arena is smaller, and missing findings stay unknown.
5. Existing control IDs are preserved, including workspace-model-panel, workspace-model-heading, connect-workspace-models, agent-endpoint, endpoint-tool-support, apply-workspace-model, workspace-model-status, agent-form, agent-workflow, agent-scenario, agent-prompt, agent-consent, agent-submit, agent-arena, refresh-runs, runs-list and run-detail. New heading IDs are agent-assessment-heading and agent-results-heading.
6. Package activity names the selected package and separates request target from measured stage; duplicate banner text is removed. No invented percentages or modal read lock was added.

## Validation scope

One consolidated relevant batch covers workspace picker, scoped hosted model reads, hosted authorization/persistence, Databricks model clients, scoped canonical reads, staging, requested UI, UI structure, arena, loop UI, navigation, startup, loading diagnostics and next-action guidance. Browser tests are offline intercepted Chromium, not hosted live verification. Responsive layout checks include 1440px and 390px. Actual screenshot matching is not claimed because no screenshot attachment was available in this request.

The first batch reported 294 passed and 11 failed. It exposed a tracing-settings coupling, which was corrected; obsolete navigation/order/label expectations and a pre-existing unsupported task-preservation assertion were aligned to actual boundaries. The second identical batch reported 303 passed and two remaining navigation fixture visibility expectations. Those assertions were corrected to test package progress state on the hidden Estate panel and model setup remaining visible across project switches. Final batch results are reported with the completion response.

The requested agentic-blueprint directory and PDF are absent from this checkout. The proposal-first design was read, and separate human approval, durable history and fresh-user authorization boundaries were retained.

### Regression invocation

The consolidated command uses the selected workspace virtual environment, `python -B -m pytest -c workbench/pytest.ini`, all of the following modules, and `-q --tb=short --disable-warnings -ra`:

- [workspace picker](../workbench/tests/test_workspace_access.py)
- [scoped hosted model reads](../workbench/tests/test_hosted_model_reads.py)
- [hosted authorization and persistence](../workbench/tests/test_hosted.py)
- [model clients](../workbench/tests/test_databricks_models.py)
- [canonical hosted reads](../workbench/tests/test_hosted_object_reads.py)
- [staged package compilation](../workbench/tests/test_hosted_package.py)
- [requested UI](../workbench/tests/test_requested_ui.py)
- [UI structure](../workbench/tests/test_ui_structure.py)
- [six-role arena](../workbench/tests/test_agent_arena.py)
- [loop UI](../workbench/tests/test_loop_ui.py)
- [navigation prerequisites](../tests/test_navigation_prerequisites.py)
- [hosted startup](../tests/test_hosted_startup.py)
- [loading diagnostics](../tests/test_loading_diagnostics_ui.py)
- [next-action guidance](../tests/test_next_action_guidance.py)

Syntax checks use `ast.parse` on 80 Python files and `node --check` on the parent app, workspace access, agent loops, arena and tracing-status scripts.

## Follow-up: configured workspace target and scoped progress

- `GET /api/models/workspace` returns only the service-configured workspace origin and authorization mode. Local mode reads the explicit labs profile host without constructing an SDK client, resolving credentials, using the CLI, or inheriting DEFAULT/ambient hosts. Missing or unsafe configuration returns an unknown URL.
- Hosted dispatch returns the already freshly verified OBO identity host before provisioning, Delta reads, SQLite/package restoration, inventory discovery, or the mutation queue. The app/browser origin is never substituted for the workspace host. Existing status payloads also expose the workspace as an additive field.
- The model picker immediately shows “Resolving configured workspace…”, then “Workspace https://…” with a local labs versus hosted OBO label. Target metadata and optional saved-settings requests run independently. Neither discovers endpoints nor executes inference automatically.
- Connect and Apply display separate real request stages, an accessible spinner and an indeterminate progress bar, never a fabricated percentage. Controls are restored on completion/error. Scope changes reset target/progress/selection immediately; old responses and old finalizers cannot alter the new scope or clear its busy state. A pending Apply is not canceled server-side by navigation; its late confirmation is simply excluded from the new UI context.
- Offline regressions cover unknown/unsafe hosts, delayed saved settings, fresh hosted identity, no Delta or runtime access for target metadata, discovery/Apply progress, failures, and stale workspace/Connect/Apply responses. The discovery-state screenshot is generated locally from intercepted fixtures, not a live Databricks connection, at output/workbench/workspace-model-picker-local.png.
- The consolidated regression invocation above is expanded with workbench/tests/test_model_workspace.py and tests/test_intake_loading_controls.py. No deployment, shared-browser interaction, service restart, live authentication, model call, or business-data write is part of this verification.

### Follow-up changed files

- Backend: [host normalization/profile reader](../workbench/databricks_models.py), [local model API](../workbench/model_access.py), [local dispatch](../workbench/projects.py), [hosted dispatch/API](../workbench/hosted.py), [hosted saved-model projection](../workbench/hosted_model_reads.py).
- UI: [workspace picker](../workbench/static/workspace-access.js), [scoped styles](../workbench/static/app.css).
- Tests: [local workspace context](../workbench/tests/test_model_workspace.py), [picker browser tests](../workbench/tests/test_workspace_access.py), [hosted context tests](../workbench/tests/test_hosted_model_reads.py), [navigation fixtures](../tests/test_navigation_prerequisites.py), [intake fixtures](../tests/test_intake_loading_controls.py).
- Documentation: this setup note. Screenshot: locally generated fixture artifact, not a production source change.

### Completed follow-up verification

- Final identical 16-module consolidated batch: **341 passed, 1 warning in 365.47 seconds**. Terminal outcome: `CONSOLIDATED_REGRESSION=PASS; NO_DEPLOYMENT; NO_LIVE_INFERENCE`.
- Python AST validation: **86 files passed**. JavaScript syntax validation: **5 scripts passed**, using the script list above.
- The first follow-up batch reported 340 passed and one static security-guard failure: the literal word “Authorization” in a display label. The label now says “Authentication mode not confirmed”; the security guard was not weakened. The entire batch was rerun, not only the failed test. The prior navigation regression and all new context/progress tests passed.
- The local fixture discovery screenshot was inspected. No production source edits followed the final passing regression. No deployment, service restart, live model discovery/inference, live authentication, or business-data writes were performed.

## Measured-latency and concise-copy follow-up, 2026-11-09

This section supersedes earlier wording and implementation details for the current
local source, not for the deployed app. The earlier **341 passed** result belongs
to the previous local revision, not this follow-up.

### Actual observations

Fresh labs user authentication was obtained with the existing explicit-profile
CLI helper, with credentials held in memory and never printed. Read-only HTTP
requests used individual bounded network timeouts. No service-principal fallback,
inference, endpoint invocation, route application or business-data query occurred.

| Observation | Measured seconds | Result |
|---|---:|---|
| Fresh CLI labs token resolution | 0.938 | Authenticated |
| Direct current-user metadata | 0.476 | HTTP 200 |
| Direct serving endpoint list | 0.396 | HTTP 200, 52 endpoints |
| Hosted health | 1.108 | HTTP 200 |
| Hosted root HTML | 0.305 | Old `Agent activity` heading present |
| Hosted saved model status, observed from 22:29:39 UTC | 60.852 | HTTP 200, inference readiness false, no configured roles confirmed |
| SDK current-user request, fresh explicit labs credentials | 0.721 | Successful |
| SDK serving endpoint list | 0.430 | 52 endpoints |

These are individual samples, not performance guarantees. Raw list metadata
contained explicit task, state and config fields. It reported 39 READY and 13
NOT_READY endpoints; task counts were 28 `llm/v1/chat`, four embeddings, four
agent/v2/chat, eight agent/v1/responses, one systemone and seven unknown. These
task counts are not all eligible endpoints. Unknown/unsupported tasks remain
disabled; names are never treated as chat-capability evidence.

Installed SDK `ServingEndpointsAPI.list()` issues **one** GET to the collection
API. It does not enumerate endpoint details or MLflow models. Current
`WorkspaceModels.discover()` also issues no individual endpoint GET. Therefore
the measured delay cannot be attributed to an observed N+1 inventory loop. No
per-endpoint calls were needed or sent. The old hosted status total is observed;
its internal restore/client subphase timings were not measured in that request.
Earlier source investigation established full runtime restoration in the old
hosted model path. The current local fast path bypasses that restoration for
Connect. This follow-up additionally removes six serial model-client
constructors from the scoped saved-status path, retaining fresh route metadata
validation. No claim is made that the 60.852-second sample is entirely client
construction time or that SDK retry/authentication caused that delay.

At **22:32:56 UTC**, read-only app metadata confirmed active deployment
`01f1db1090d31d44978737267dc6042f`, created **2026-11-08T01:06:02Z**, updated
**01:08:33Z**, SUCCEEDED. The screenshot's old page is consistent with the served
old HTML, not proof that the current local source failed to render.
The running local port 8000 returned health 200 but configured-workspace 404,
so it also has not activated the current package-independent model setup.
Neither running service was restarted.

**Hosted Connect POST was deliberately not sent:** the old handler can save a
default route, and the fresh status did not establish existing routes. A
metadata-only authorization does not authorize an automatic route write. The
local Connect POST was also not sent to the older running process. Consequently
there is no live running-service Connect timing or live UI completion claim.

A subsequent **isolated current-source local handler** was exercised through
FastAPI TestClient with the actual fresh labs identity and real endpoint metadata.
Its service root was a managed temporary directory, with no business runtime or
route configuration. POST `/api/models/connect` returned **200 in 1.814 seconds**,
52 endpoints, measured authentication **1.175 seconds** and endpoint list
**0.629 seconds**. There were **zero model-routing files**, and the temporary
directory was removed automatically. This verifies the current metadata handler,
not activation in the older port-8000 service or hosted deployment.

### Focused changes

- Page heading, navigation label and page-name map: **Select agentic model endpoint**.
	Intro: “Choose a workspace endpoint, then review a workflow.” Four steps remain
	Model, Source & scope, Assessment, Results & human review. Step 4 is **Agent
	activity and results**. The full source-grounded instruction and egress consent
	remain intact. Safety text beside Invoke: “Review only. Nothing runs or deploys
	automatically.”
- Connect retains one inventory list and no client construction or route save.
	Local and hosted metadata-only handlers return actual authentication and list
	durations. No timeout or retry policy was shortened to hide latency.
- Pending Connect/Apply shows the real request target and elapsed seconds/minutes,
	plus an indeterminate bar. A long wait says it is waiting for the server response,
	not that an invented subphase is progressing. On completion, actual returned
	auth/list measurements are shown separately from browser request duration.
- Optional saved settings have their own status, busy-independent controls and
	scope revision. Connect no longer repeats that read. Late status cannot replace
	a discovery failure, a newer Apply result or another package's state.
- Scoped saved status uses `validated_routes()` without building six ChatOpenAI
	clients. The same fresh inventory, READY/type validation and evidence rules are
	retained; Apply and actual runtime client construction retain their existing path.
- Next-action Invoke guidance requires fresh picker readiness as well as existing
	overview, workflow, consent and enabled-run gates. A pending/error status, failed
	discovery or unapplied different selection cannot inherit an old success cue.
	No button, model selection, consent or route is invoked automatically.

### Consolidated verification

All production source changes preceded the consolidated pytest invocation.
Python final AST preflight: **80 files passed**. JavaScript syntax: **six scripts passed**,
the previous five plus next-action. The 16-module invocation listed above is used,
with the current new tests for measured stages, client-free saved status, no
per-endpoint GETs, independent optional failures, fresh readiness, elapsed time,
word-count hierarchy and light/dark 1440/390px layout. Browser proof remains
offline intercepted Chromium; the regenerated local discovery screenshot was
inspected, including target, actual elapsed text and indeterminate progress.

First consolidated batch: **345 passed, one failed, one warning, 405.98 seconds**.
The existing token-freshness assertion expected a status GET to construct clients.
That test was corrected to assert fresh-token endpoint metadata validation and
**zero additional model-client constructions**, preserving authentication checks.
There was no production patch after the first batch. The identical entire
16-module batch was rerun: **346 passed, two warnings in 339.75 seconds**, zero
failures, errors or skips. Terminal outcome:
`CONSOLIDATED_REGRESSION=PASS; NO_DEPLOYMENT; NO_LIVE_INFERENCE`.
No production or test edits followed this final passing batch. Editor checks on
changed Python/JS/HTML paths reported no errors. No hosted Connect POST, inference,
Apply, service restart or deployment occurred.

Additional changed paths relative to the previous manifest: local model API,
client validation helper, scoped hosted model status, hosted timing fields,
index/page-name map, scoped CSS, guidance and their focused regression modules.