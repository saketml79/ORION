# Page and action-scoped next-action guidance

Current deployment and final 639-pass regression evidence:
The current release and verification state is maintained in
[current-release-verification-ledger.md](current-release-verification-ledger.md).

Presentation only. One eligible existing action receives a slow 3.2-second shadow
halo and a non-live “Suggested next step” description. Reduced motion keeps a
static halo and the same description. Nothing clicks, focuses, submits, checks
consent, reads an API, or invokes inference on behalf of the user.

## Explicit priorities, using current navigation labels

| Page | Candidate priority and prerequisite |
| --- | --- |
| New or existing project | Refresh projects to check saved membership, then Refresh packages if the first action is unavailable. The hint asks for a project and completed XML, not another upload. Creation and uploads are not recommendation candidates. |
| Estate | Explore pipelines, then Show package graph, only with loaded understanding and the actual enabled Pipeline explorer navigation gate. Otherwise Choose or create project / Manage project & packages, except while understanding is loading. |
| Readiness | Refresh current evidence, load canonical workflow choices, then trace saved source/destination correspondence and review proposals. Initial workflow-option population does not rearm completed refreshes. |
| Pipeline explorer | Inventory object while no object is loaded. Graph leads to English Description, English leads to Fields & evidence, Fields leads back to Graph. A graph-card selection uses the actual object/node callback to enter a new field context. No technical-download suggestion or repeated completed tab cycle. |
| Agentic Migration Proposals | Inspect selected source targets and destination scope, read an exact saved cohort review, review a candidate or saved redesign. Spark drafting needs both fresh consents plus the existing exact-version enabled gate. SQL/workflow drafting needs its own checked consent and existing enabled gate. Review approval, cohort agent execution, publication and deployment are never guidance candidates. |
| Legacy Data Integration Artifacts | Refresh connections, then Refresh stored documents. No evidence writes or attestations. |
| Agent activity | Connect workspace models if the overview has not confirmed an available route. Otherwise Invoke agents only if the existing enabled run gate, selected valid workflow, valid instruction and existing checked egress consent all permit it. Otherwise Refresh recorded runs. |
| Validate | Load exact context first. Create controlled local fixture evidence only with valid form inputs, existing consent and the enabled parent gate. Read an exact saved campaign or download its report. Catalog provisioning is never a suggestion. |
| CI CD (DAB + Github) | Export compressed JSON, then Export English, with package readiness. No save, publication or deployment recommendation. |
| Logs | Refresh logs, then Download CSV. |
| Evolution roadmap | Evidence-to-decision design, then Agent mesh design. These remain explanatory dialogs opened only by explicit clicks. |

Hidden, disabled (including disabled fieldsets), aria-disabled, busy, inert,
transparent or invisible controls are excluded. Candidates remain inside the
current page. Open modal dialogs suspend suggestions. No eligible action means
no halo and no hint; readiness is never fabricated just to show a suggestion.

The app publishes `orion:navigate` only after prerequisite resolution. Actual
Explorer object/node/tab callbacks also publish `orion:explorer-state`. One explicit
page/action map chooses the next existing eligible control. A trusted candidate
click dismisses that action identity until a meaningful selected context changes,
not the entire page visit. Busy-to-enabled completion and DOM replacement cannot
reinstruct the completed action. Tab entry advances within the same object context;
already inspected English/Fields tabs are not recommended again. A new graph node,
workflow, scenario, package or page starts the relevant new context.

Typing removes the moving cue until 800 milliseconds of input inactivity or blur.
Unrelated clicks and keyboard traversal do not disable guidance forever. Scoped
gate/mounted-control observers handle asynchronous completion without DOM polling,
requests, inferred telemetry or observing the guidance's own class changes. The
same target retains its animation identity across routine updates. Document
backgrounding pauses the animation; leaving a page removes its halo. Existing
accessible descriptions are retained. Open explicit dialogs suspend guidance and
their close reveals the next eligible action, never a replay of the clicked action.

## Readiness and locks, unchanged

- Startup confirms hosted/local mode and restores the selected project/package.
  Without a completed selected XML, dependent navigation is guarded. Projects,
  Logs and Evolution roadmap remain accessible.
- A completed selected XML can open Estate while its understanding loads.
  Pipeline explorer, proposals, evidence, agents and delivery stay guarded until
  understanding is ready for that exact selected package hash. A mismatch does
  not unlock them. Persisted run status must also be loaded before agent submit.
- Project upload controls remain disabled during queued/running ingestion,
  selected-package hydration or another project submission. Guidance changes no
  existing upload or model prerequisites.
- Routine page reads use the existing nonmodal read path. They do not make all
  tabs inert or open operation dialogs. Package readiness invalidation can still
  legitimately guard package-dependent pages while understanding is reverified.
- Explicit blocking mutations retain the existing inert workspace, focus trap
  and progress/minimize behavior. Guidance does not weaken or own that lock.

## Existing navigation test correction

The navigation fixture previously waited only for the redesign script to mount.
That does not await asynchronous startup. The default Estate section could be
observed while the URL was still the requested agents deep link and saved
selection verification had not completed. The fixture now waits for the app's
confirmed current-page marker. Production prerequisite decisions are unchanged.

Offline Chromium coverage uses the real index, scripts, CSS, navigation and
hosted read envelopes. It checks all nine labels, trusted dismissal, async
eligibility, exact package readiness, hidden/disabled/inert/busy candidates,
consent/routing, reduced motion, background pause, unchanged animation identity
on routine updates and no automatic business mutations.

## Historical verification, 2026-11-04

The interaction-dismissal behavior in this historical test record is superseded
by the action progression above, explicitly requested for the current release.

Final consolidated result: **169 passed, 1 warning, 0 failures, 201.50 seconds**.
This includes **31 new guidance cases** and **all 21 navigation prerequisite
cases**. The previously recorded startup hash assertion now passes, without a
production prerequisite change. The full backend/model suite was not run; the
separately recorded migration-planning connection-count failure was not changed
or rerun by this frontend task.

The final batch ran these seven modules together:

- [New guidance](../tests/test_next_action_guidance.py)
- [Navigation prerequisites](../tests/test_navigation_prerequisites.py)
- [Selected package loading](../tests/test_selected_package_loading.py)
- [Intake loading controls](../tests/test_intake_loading_controls.py)
- [Loading diagnostics](../tests/test_loading_diagnostics_ui.py)
- [UI structure](../workbench/tests/test_ui_structure.py)
- [Hosted intake UI and mutation locking](../workbench/tests/test_hosted_intake_ui.py)

Exact regression command:

`.\.venv\Scripts\python.exe -B -m pytest tests/test_next_action_guidance.py tests/test_navigation_prerequisites.py tests/test_selected_package_loading.py tests/test_intake_loading_controls.py tests/test_loading_diagnostics_ui.py workbench/tests/test_ui_structure.py workbench/tests/test_hosted_intake_ui.py -o addopts= -q --disable-warnings -ra`

JavaScript syntax commands, both passed:

- `node --check workbench/static/app.js`
- `node --check workbench/static/next-action.js`

The first batch had 158 passes and 11 failures in the new test harness. Nine
assertions incorrectly counted the existing trusted-interaction heartbeat POST
as a business mutation; two intercepted a path-plus-query instead of the real
separate pathname/query fields. Only those test assumptions were corrected, then
the same complete scoped batch passed. No application behavior was weakened to
satisfy them. Editor diagnostics for all six source/test assets were clean.

Static-only browser GETs on the existing local port 8000 returned HTTP 200 and
matched on-disk SHA-256 for the root HTML, app script, guidance script and
guidance stylesheet. No app UI was navigated live and no business APIs, model
inference, UC operations, backend restart or deployment were performed. The
static verification tab was returned to a blank page. Reload an already open
workbench tab to load the added module.