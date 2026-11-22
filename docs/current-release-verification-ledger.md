# Current release verification ledger

This ledger records evidence and outstanding checks. It is not, by itself, an
authorization veto. A specific current user deployment instruction may proceed
with checks pending; record those gaps clearly and never present them as passed.
Passing regression tests is not a migration specification or runtime-parity
certificate.

## Plain-language release, 2026-11-22

- Deployment `01f1e6312aa21268976847acaa79e314` was created at
	2026-11-22 04:57:06Z and switched traffic at 05:00:01Z. The app reported
	SUCCEEDED, RUNNING and ACTIVE. Authenticated health returned HTTP 200;
	the deployed HTML and four checked scripts matched the staged bytes.
- The agent diagram is mounted only in the top-left help modal. The modal
	heading names the migration agents. Model setup and suggested-action text
	was revised to explain that connecting or saving a model does not start a
	conversion. The served page and scripts reported `Cache-Control: no-store`.
	Hosted interactive behavior has not been inspected in a browser.
- Eight changed JavaScript files passed `node --check`; three changed Python
	files passed AST parsing; `git diff --check` passed. The staged snapshot had
	182 workbench files matching the workspace by SHA-256, and bundle validation
	passed. No model calls or test suites were run for this release. Existing
	CSS compatibility advisories remain.

## Inventory and modal release, 2026-11-22

- Deployment `01f1e62f70191bd0b01d8587ad454bd8` was created at
	04:44:44Z and switched traffic at 04:47:24Z. The app reported SUCCEEDED,
	RUNNING and ACTIVE; health returned HTTP 200 and four checked assets
	matched the staged snapshot. This deployment is superseded by the release
	above.
- The selected local package preloaded 5,092 version-pinned inventory records;
	the Mappings dialog showed all 434 entries. Record details remain separately
	loaded. The local help modal showed the migration diagram and Agent activity
	had no diagram mount. Hosted interaction remains unverified.

## Agent communication graph deployment, 2026-11-22

- Deployment `01f1e62b8b0f105c83c3177bcbf3698e` was created at
	2026-11-22 04:16:51Z and switched traffic at 04:19:33Z. App metadata
	reported SUCCEEDED, RUNNING and ACTIVE, with one instance and the same six
	user scopes. Authenticated health returned HTTP 200, and the graph script,
	stylesheet, agent arena, main script and HTML matched the staged bytes.
- The external snapshot contained 186 files, including 182 workbench files
	whose SHA-256 hashes matched the workspace. `databricks bundle validate
	--profile labs -t dev` passed. Before deployment, `node --check` passed for
	`agent-team-flow.js`, `agent-arena.js` and `app.js`, and `git diff --check`
	passed. The actual local graph was inspected as described below.
- The consolidated outstanding-work checklist is [ORION-TODO.md](ORION-TODO.md).
	It is a workspace document, not a hosted app asset. The graph illustrates
	configured communication and the writer review loop, not live activity or
	automated scored hill climbing. No new model run or `test_*.py` suite was
	performed. Hosted interactive behavior and conversion quality remain
	unverified; existing CSS compatibility advisories remain.

## Agent communication and skills view, local observations before deployment

- The numbered view now uses a directed graph with animated arrows between
	the workbench, six agents, shared findings, and the conditional writer.
	Optional supervisor delegation is marked separately. The actual local dialog
	showed 16 directional paths and changing arrow dash positions; reduced motion
	stopped the animation. The full graph fits the checked desktop width, and
	the 390 px view has a keyboard-focusable horizontal scroll area.
- Agent activity and the agent-structure dialog now display the six configured
	analyst roles in invocation order, their actual role identifiers and named
	skills, plus the conditional separate Spark writer. The workbench invokes each
	analyst with the findings recorded so far; a supervisor may request focused
	follow-up. This view describes configuration, while the existing run view
	remains the source of recorded call counts and skills.
- The supervisor is labeled Lead migration design coordinator, reflecting its
	ability to submit or correct a logical design. The separate Spark and SQL
	code writer drafts candidate code using the configured artifact-role model.
	The graph now shows static checks, AI review when available, and an optional
	feedback-to-writer revision loop. This is iterative refinement, not a scored
	hill-climbing optimizer; saving a candidate does not establish parity.
- The local dialog showed 18 directional paths, readable role and writer labels,
	and the check/revision nodes. Reduced motion disabled the graph animation;
	mobile uses a keyboard-focusable horizontal scroll area. Syntax checks passed
	for the three changed JavaScript files. The reported observation of the lead
	writing code was not independently reproduced or attributed to a saved run.
- The actual local app showed both views with all six analyst names and skills,
	plus the writer. Desktop and 390 px mobile layout were inspected; mobile
	showed no overflowing cards. Dark theme and reduced motion were checked.
	`node --check` passed for the new script and `app.js`; `git diff --check`
	passed. Existing CSS compatibility advisories remain. No model run,
	`test_*.py` suite, or hosted deployment was started for this change.

## Named skills and animated agent diagram deployment, 2026-11-22

- The agent-structure diagram animates its connections and role highlights.
	The local browser confirmed animation is running and that reduced-motion
	mode disables it. The diagram remains an illustrative architecture guide.
- The named specialist skills and separate Spark writer skill are included;
	original skill files and historical receipts remain available. No new agent
	run was performed, so output quality and hosted live behavior remain
	unverified. Changed skills alter the current input fingerprint and may cause
	existing designs to be reported stale without rewriting their history.
- JavaScript syntax, Python diagnostics, skill-path checks, the local skill
	catalog, a saved-run view, 181 staged workbench hashes, and bundle validation
	passed. No `test_*.py` files were run. Existing CSS compatibility advisories
	remain.
- Deployment `01f1e626e0a7116798a3db5a02744eef` was created at
	2026-11-22 03:43:27Z and switched traffic at 03:46:08Z. App metadata reported
	RUNNING/ACTIVE with the same six requested scopes. Authenticated health
	returned HTTP 200 and eight hosted UI assets matched staged bytes. Hosted
	animation and a fresh model run were not exercised.

## Agent skills and context, 2026-11-22

- Deployment `01f1e622ef8e1a088cfd728760495049` switched traffic at
	03:17:53Z. The app was RUNNING/ACTIVE, authenticated health returned HTTP
	200, and the eight checked hosted assets matched the staged snapshot. The
	prior project-layout, copy, and skills/context updates were included.
- After that deployment, the local context view was simplified: an accessible
	help control explains the evidence, SHA-256 values are no longer displayed
	in the normal view, and technical source IDs remain optional. The saved
	records were not modified. A historical local run was checked at desktop
	and mobile sizes, including dark mode. These follow-up changes are not in
	the deployment above.
- Anthropic's official public skills repository was downloaded separately at
	`vendor/anthropic-skills`, revision `e4bb5d960d87126ff3b3a8af1a104b7db67560ba`.
	Its contents have not been activated in ORION. The user selected the named
	skill migration with compatibility. Six specialist loaders and the separate
	Spark writer now point to named enhanced skills, while the old skill files
	remain available for historical paths. The current local server was
	restarted while idle and its skill catalog returned all seven named skills.
	A saved run continued to show its actual historical `review` receipts.
	New skill instructions and files change the current skills fingerprint, so
	old designs may be reported stale; their saved records are not rewritten.
	No new model run or runtime-quality evaluation was performed. These changes
	are local only and require deployment before hosted runs can use them.

## Timer and UI deployment, 2026-11-22

- The live agent clock now uses the parent run start instead of switching to
	the candidate-writer start. The earlier ORION sizing, role-column styling,
	operation disclosure, wording, and call-list alignment changes are included.
- JavaScript syntax checks passed for `app.js`, `agent-arena.js`, and
	`hosted-intake.js`. The actual local app showed a saved run's 31m 19s
	duration, six role rows, a 132px ORION block, and aligned desktop call lists.
	No new agent run was invoked, so the active writer transition remains
	unverified in a live run. No `test_*.py` files were run. The existing CSS
	Safari `backdrop-filter` advisory remains.
- The staged 174 workbench files matched their source hashes and the bundle
	validated. Deployment `01f1e61a91451a24b82cb82aa1fb5f76` was accepted at
	2026-11-22 02:15:20Z and switched traffic at 02:18:11Z. App metadata reports
	RUNNING/ACTIVE with the same six requested scopes. The user authorized
	deployment despite the running hosted app. That run's final state and the
	new hosted UI's live timer behavior remain unverified.

## Local typography pass, pending deployment

- Shared page prose, headings, and control weight were adjusted for a calmer,
	more readable hierarchy. Long section headings wrap; the agent cost panel
	distinguishes numeric amounts from longer unavailable messages.
- The actual local app's twelve navigation pages were inspected at desktop and
	mobile widths, and the saved agent view was checked in light and dark themes.
	The desktop model/tool scroll areas remained aligned and the saved timer
	stayed at 31m 19s. This is not exhaustive verification of every dynamically
	loaded state or an active agent run. No model or `test_*.py` run was started.
- On the user's request, a new staged snapshot passed 174 source SHA-256
	comparisons and `databricks bundle validate --profile labs -t dev`. Deployment
	`01f1e61e428910bb8cca1a01de6718c2` was accepted at 2026-11-22 02:41:46Z
	and switched traffic at 02:44:22Z. The app is RUNNING/ACTIVE with the same
	six requested scopes. Authenticated health returned HTTP 200, and all three
	changed stylesheets matched the staged bytes. Hosted appearance remains for
	user review; no hosted browser or new agent run was exercised.

## Local project layout and user-facing copy, pending deployment

- The project summary and XML member details have reserved, keyboard-focusable
	scroll areas. The create-project card provides next steps without changing
	project creation. During actual local project selection, the selection card
	remained 872 px before, during, and after the XML list loaded; at 390 px it
	remained 908.5 px in the earlier mobile check. No project was created.
- Updated prominent page introductions, the Estate review banner, agent
	evaluation and missing-evidence copy, folder errors and progress, and
	proposal guidance to explain user actions and preserve actual limitations.
	The agent instruction prompt and execution behavior were not changed.
- The actual local app's twelve pages were inspected at desktop and mobile
	widths. Visible page headings showed no clipping in the inspected states;
	a saved run retained its elapsed time and aligned model/tool lists. Light
	and dark themes were checked for the project card and Estate banner. Dynamic
	error, active run, and all hosted states were not exhaustively exercised.
- `node --check` passed for the five changed JavaScript files. The editor
	reported no JavaScript or HTML errors and only existing CSS compatibility
	advisories. No `test_*.py` files, agent runs, or deployment were started.

## Requested checks

| Request | Current evidence / outstanding work |
|---|---|
| Single-workflow authority, optional cohort | Existing local UI/source-disposition verification; external call remains informational |
| Amber external-call task, other cards white | Prior local browser verification; retain after changes |
| Correct condition arrows | Prior local browser verification and published upstream-value negative check |
| Preserve workflow selection | Verified on prior refresh/reload; recheck current app |
| Smaller regular-weight saved-design dropdown | Prior local 13px / 400 browser result |
| Actual notebook paths below YAML | Four v25 paths verified, owning-task link works |
| Native Jobs YAML | v27 available; fresh v29 blocked on nested email widget declarations despite AI review_ready, unresolved release defect |
| Source/target identity not assumed equal | Separate runtime bindings and exact source-candidate receipt |
| Destination counter table | Live metadata search: absent in all schemas of the three remembered destination catalogs; not a bound or approved destination |
| Catalog/schema/table in three colors | Local change, current browser verification pending |
| Blueprint header logo | Actual local app: supplied color SVG immediately after Databricks in light mode; supplied transparent white PNG in dark mode. Both load; boxes match Databricks at 132×22 desktop and 99×16.5 mobile, no horizontal overflow. Repeated three desktop reloads and two mobile reloads in both themes. |
| Variable-to-parameter mapping | Mapping guidance and formatted inventory; complete source-to-use correspondence not independently established |
| Notebook top overview and per-cell business logic | Active guidance updated; fresh generated evidence and runtime behavior still require review |
| Notification is secondary | Send-capable code generated in v25; missing SMTP settings are runtime configuration, not code-generation blockers; no mail sent |
| Rejected submissions separate from failures | Actual middleware check passed; updated backend now loaded locally; historical records not rewritten |
| Agent evaluations live | Local scorecards/browser verified on selected historical receipt, not independently measured groundedness or business correctness |
| Latest Agent event layout | Live local browser: event is a separate row after the equal-height activity/evaluation panels; includes role, type, local timestamp, run/workflow/scenario, elapsed offset, endpoint and reconciled call counts |
| Six agent call lines | Live local browser: six distinct cumulative per-role model-call lines from all 44 timestamped model turns; counts supervisor 10, discovery 9, semantics 6, architecture 9, validation 6, artifact 4 |
| Operational KPI graph and reference KPIs | Live local browser: KPI trend chart distinguishes sampled operational snapshots from quality; visible Reference-based quality KPI section correctly says not measured without labeled references |
| Agent scorecard reconciliation | Live local browser: tool acceptance 105/115 = 91%, using terminal start=accepted+rejected reconciliation; model success 44/44, roles 6/6, drafted dispositions 5/5 |
| Missing LangSmith credentials not Failed | Configuration and browser checks passed; no tracing export enabled |
| MLflow mention | Explicit Not integrated; no fabricated trace delivery |
| Databricks notebook attachment | App-managed session code added, no overwrite; live cloud roundtrip explicitly deferred to user |
| Return edited Databricks notebook to existing app artifact | Preview/save/revision/cleanup code added; offline unit checks passed initially, consolidated regression not yet green; live testing deferred to user |
| Per-run memory / multiple model design | Proposed documentation only; no new endpoint selected or expanded mesh activated |
| Hosted authority | Existing hosted path uses committed Delta metadata and Volume artifacts; SQLite is reconstructed ephemeral cache, not hosted authority |
| Hosted loading timeout | Deployment 01f1e614055a1cbd994f87869edd664d is active. The app-proxy timeout remains a separate ~120s platform boundary. Operation 03303804ac0f42e49539b7139d1a8e01 remains unreplayed and unconfirmed. |
| Two full suite passes | Pending; all failures/skips must be reported, not hidden by exclusions |
| Two fresh visible agent conversions | Actual local v27/v29 runs completed and saved four notebooks each. Not two release passes: target not verified; v29 native YAML fails. See visible-run receipt |
| Deployment | Snapshot 01f1e614055a1cbd994f87869edd664d SUCCEEDED from 2026-11-22 01:28:28Z to 01:31:46Z. App RUNNING; hosted agent behavior and regression suite remain unverified. |

## Hosted workflow classification redeployment, 2026-11-22

- Deployment `01f1e614055a1cbd994f87869edd664d` switched traffic successfully
	at 01:31:46Z. App readback confirmed RUNNING/ACTIVE, six requested scopes and
	one instance. `/healthz` returned HTTP 200; deployed `app.js`,
	`context-picker.js`, and `app.css` matched the verified staged bytes.
- The pre-deployment status check reported no active run for the current
	user/project/package; this was not an all-user idle assertion. No model or
	job was invoked. No `test_*.py` files were run. The hosted workflow-picker
	presentation and hosted live-agent behavior remain browser-unverified.

## Hosted duplicate activity panel, pending deployment

- The purple status ribbon remains across tabs. The existing detailed hosted
	operation disclosure, including its recorded-operation reconnect control,
	has been moved from the shared page header into the Logs page. The change
	does not cancel, replay, or delete an operation, and does not change polling.
- `node --check workbench/static/hosted-intake.js` passed. A read-only fetch
	from the shared local app returned the updated script and confirmed the Logs
	mount and retained status ribbon. No hosted operation was created to test
	the panel and no `test_*.py` file was run. Hosted behavior remains unverified
	until the change is deployed and exercised in the hosted app.

## Model and tool call alignment, pending deployment

- The model and tool call lists were both capped at 480 px but started at
	different positions because only the tool side had explanatory text. In a
	saved local run, their scroll areas initially differed by 55 px vertically.
- The two open call-history panels now share summary, note, and list rows in
	browsers supporting CSS subgrid and `::details-content`; narrow screens retain
	the stacked layout. Local browser verification with saved run
	`b0bbfb4360ff4259806fc10decc6506a` measured identical scroll-area top and
	bottom positions, with independent scrolling and working disclosure controls.
	The fallback in browsers without these layout features retains the previous
	layout and has not been separately validated for alignment.
- The current hosted run was not changed, cancelled, or restarted. No model or
	`test_*.py` run was started. This source change is not deployed to hosted.

## Hosted live-run handoff, 2026-11-22

- The user supplied a hosted audit record showing `POST /api/agents/run` returned
	HTTP 202 after 5742.74 ms at 2026-11-22T00:45:47.389692+00:00. This is the
	inner request acceptance, not evidence that the agent completed or that its
	results were committed. The related browser screenshot showed the outer
	operation stuck on `starting`.
- Source changes expose the exact accepted run ID as a user-scoped, process-local
	observation, labeled not yet committed. The agent status UI can read that run
	while the original hosted operation continues and remains responsible for
	reporting the Delta commit. No additional inference, replay, or automatic
	execution is introduced. Restart or eviction can lose this observation.
- The selected-workflow note reports only the last confirmed hosted package
	session, not a guarantee that its in-memory state still exists. Guided model
	endpoint selection and the request-details disclosure are source changes too.
- These changes are deployed but not verified in a hosted run. The existing
	operation journal, OBO checks, worker queue, status polling and final Delta
	commit remain, so zero waiting or instantaneous startup is not established.
	The user's previously active hosted invocation was interrupted by the
	explicitly requested app stop; a clean cancellation or final result is not
	established, and the invocation was not replayed.
- An allowlisted 178-file deployment candidate was staged on 2026-11-21 at
	`C:/Users/SAKETS~1/AppData/Local/Temp/orion-hosted-live-activity-20261121-d7ef95fe0b224052885a3e951a38d7ad`.
	The six changed executable files matched the workspace SHA-256 values; four
	staged JavaScript files passed `node --check`, and 108 staged top-level Python
	modules parsed with Python 3.11 grammar. The existing `labs` refresh token was
	invalid, so no remote bundle validation, upload, or deployment was attempted.
- After the user requested CLI refresh, the `labs` profile was refreshed. At
	2026-11-22T01:09:52Z the app was RUNNING, and a scoped status read returned
	`run_status_verified=true`, `active_run_present=true`, with run
	`e2348c4aa3674bd6b71269569d601ff9` running in `agent_analysis`. This status
	applies only to the observed user/project/package, not all app users.
- All 174 staged workbench files matched current source SHA-256 values and the
	staged bundle validated successfully. A new snapshot was uploaded to
	`/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261121-live-activity-d7ef95fe0b224052885a3e951a38d7ad`.
	The app was not switched or redeployed because the reported run was active.
- After the user's explicit stop-and-deploy request, app stop was requested at
	2026-11-22T01:14:18Z and compute was STOPPED at 01:15:28Z. Direct deployment
	on the stopped app was rejected by the platform; the app was started and the
	final snapshot deployed successfully, ID `01f1e612c43d18cfa0bf1a1ed3cf6c42`,
	created at 01:19:30Z and traffic switched at 01:22:16Z. Post-deployment app
	read reported RUNNING/ACTIVE, the same six requested scopes and one instance.
	`/healthz` returned HTTP 200, and the deployed app, intake, guidance, and
	workspace-access scripts matched the final stage SHA-256 hashes. No hosted
	browser or new agent run was used; live progress and semantic parity remain
	unverified. The deployed snapshot precedes any later workspace edits.
- Source syntax checks passed for the affected Python and JavaScript. In the
	actual shared local app, a project and XML were selected and Agent activity
	opened; workflow selection was verified without invoking an agent. The local
	viewport at 1156 px showed 22 px horizontal overflow in existing header/arena
	elements. Hosted live acceptance and cross-user isolation were not tested.
	No `test_*.py` files were edited or run.

## Hosted warm-session follow-up, 2026-11-20

- Deployment `01f1e52d18a51cda9e78973148a772aa` is active and `RUNNING` at the
	existing app URL. Source is the allowlisted 178-file snapshot under
	`/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261120-warm-session-6682018b4bff42069e9b7e6fca12a069`.
- Warm retained requests now reuse the initiating user's retained app, Delta
	storage root, and loaded selected-package index. They skip repeat UC provisioning,
	full manifest/table authority scans, and XML-derived artifact hydration. Each
	request still resolves fresh OBO identity. Mutations still serialize per user,
	commit a new Delta generation, and update the retained generation marker. Cold
	start/session eviction still requires a restore.
- Hosted activity no longer displays `DEBUG ONLY` labels. Worker queue and cold
	restore progress labels are more explicit. Workflow selection remains editable
	while an unrelated settings operation is active; invoking remains blocked until
	that operation finishes.
- Validation performed: `node --check workbench/static/hosted-intake.js`,
	`node --check workbench/static/app.js`, Python AST parse of `workbench/hosted.py`,
	Pylance diagnostics, and staged `databricks bundle validate`. No `test_*.py` files
	were edited or run. No model inference, customer Job, or hosted UI smoke was run.
	Hosted latency and workflow selection behavior require the user's actual app test.

## Hosted snapshot deployment, 2026-11-20

- Existing app: `orion-migration-workbench`, ID `cfada07d-ffa6-4018-9080-293f5e577083`.
- Deployment ID: `01f1e529b8db1b09a797e7055b94022c`; CLI reported `SUCCEEDED` and `Traffic switched successfully`.
- Deployment interval: 2026-11-20T21:31:18Z to 2026-11-20T21:33:53Z.
- Source: `/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261120-fde924d15cde475cad18b12a7bae6024`, an allowlisted 178-file snapshot. Staged bundle validation passed before sync/deploy.
- Post-deploy `apps get` confirmed `RUNNING` / `ACTIVE`, the new deployment ID and source path, the six existing requested scopes, and min/max instances of 1. App URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com.
- No `test_*.py` files were edited or run. No model inference, customer Job, fixture DDL, or hosted browser smoke was run. The user will perform hosted UI testing. Deployment success is not a regression, latency, or workflow-parity result.

## Proactive UI integrity checks

The maintained release checklist covers these categories on every tab, not only
items a user reports. Each category requires observed evidence; unchecked is not
assumed passing. This is a release gate, not a promise that zero bugs exist.

| Category | Required checks | Current evidence |
|---|---|---|
| Navigation | All twelve tabs, correct title, active-page state, preserved project/package/workflow, keyboard access | Live desktop tab walk completed, no page-level horizontal overflow |
| Text | Exact readable sentences, inline emphasis, no unnecessary width caps or forced word breaks, long identifier wrapping | Early Agent introduction and guidance width caps reproduced; two scoped CSS rules corrected; responsive regression pending |
| Visible references | Nonempty links, available images, precise target/version, no stale task destinations | Live twelve-tab walk found no empty visible anchors or broken images |
| Notebook paths | Saved path list, task-to-file ownership, all files accessible after refresh, no false absence messages | Current live v29 task grid exposes all four files; explicit below-YAML saved-path section was not observed before reload, needs recheck |
| Downloads | Exactly one selected-task control, correct filename and complete original UTF-8 bytes, no transformed/sanitized code | All four actual v29 download buttons produced Blob bytes whose SHA-256 equals each saved reviewer artifact hash; integrated browser download-event delivery unverified |
| Notebook controls | Review, download, native editor attachment, logical-condition exception, no duplicate controls or task descriptions | Each actual v29 notebook task has one Download and one Open in Databricks control, expected cells 2/6/2/4. Cloud opening not invoked |
| Graphs | Correct edges/outcomes, readable labels, zoom/focus, one task inspector, external-call distinction | Actual five-task v29 graph inspected; keyboard/mobile/zoom checks require regression evidence |
| Agent run graphs | Per-role call counts use actual timestamped model-turn receipts; operational KPI snapshots are labeled as sampled snapshots; reference-based quality is not inferred | Selected v29 actual saved run: exactly six SVG lines, 44 complete turns, six role totals verified against live status receipt; visible graph reviewed |
| Loading and errors | Busy versus idle, optional/unavailable versus failed, actionable retry, no stale responses, no client initial-load cutoff | Actual two visible runs progressed through analysis/writer to terminal; v29 YAML error still too generic |
| Consent and safety | No navigation-triggered model/Job/business writes, selected scope, disabled unsafe actions, source/target separation | Live navigation/download audit issued no generation or execution actions; no deployment |
| Responsive/themes | Desktop/mobile in both themes, no clipped or overlapping controls/text, focus and reduced-motion intact | Desktop live walk done; current four-theme/viewport wrapping regression pending |
| Data integrity | Original manifests/notebooks/history unchanged by viewing/downloading, hash-bound receipts | Actual v29 downloaded Blob hashes match saved artifacts; full regression and hosted confirmation remain pending |

The older offline browser processes started November 14 were identified by exact
test command/PIDs and stopped as stale. Their unfinished run is not counted as a
passing regression. The local app was not stopped.

## Blueprint header logo, 2026-11-16

Integrated the provided `blueprint-logo.svg` for light mode and transparent white
`blueprint-logo.png` for dark mode as static app assets. Actual local browser
verification across three desktop reloads and two mobile reloads, each in both
themes: image `complete` and natural width > 0, correct active assets/order, equal
Databricks and Blueprint image boxes (132×22 desktop, 99×16.5 at 390px mobile),
and no document horizontal overflow. Header screenshots were inspected in both
themes at mobile and desktop. No model run, Databricks action or deployment was
performed.

Visible local run results: [v27/v29 consistency receipt](local-visible-agent-consistency-2026-11-16.md).
Edit return: [app-managed notebook revisions and cleanup](notebook-edit-return.md).

## Agent activity presentation pass, 2026-11-16

Performed in the user's shared local app page, not an offline browser:

- JavaScript syntax check: `node --check workbench/static/app.js` passed.
- The live DOM showed equal-height activity and evaluation columns within one
	672 px (42 rem) observation viewport. Content flows naturally inside the columns;
	a single parent scrollbar handles tall content. The columns have no outer border
	or internal scrollbar. Count cards and active-call entries use spacing rather
	than separator lines; there was no horizontal overflow.
- Reference-based quality KPIs are visible, not inside a collapsed details element.
	Precision/recall, citation correctness, groundedness, behavior parity, and mapping
	authorization remain explicitly not measured without a labeled evaluator.
- Latest event is structurally after the two-panel section, with the selected
	run/workflow/scenario and its timestamp/context.
- The visible six-line chart is derived from the exact complete 44-turn receipt;
	each legend count reconciles to the per-role counters. No model run was started.
- The 91% tool acceptance score is based on 105 accepted / 115 terminal tool
	submissions, with 10 recorded rejections and zero failures reconciled because
	all 115 started calls are accounted for and the completed receipt has no in-flight
	calls. Missing failure fields on incomplete/legacy records remain unknown.
- User-requested validation run a28092ec1a484674b31490bc48137bd8 was explicitly
	stopped from its visible Stop control and reached terminal `cancelled` status at
	2026-11-16T04:23:56.314084Z. It never entered candidate writing; no new notebook
	artifact or native YAML result is claimed from that stopped run.
- Reference KPI typography was reduced in the live local UI from 13 px list text
	to 10 px with 13.5 px line-height. The first CSS rule lost to the later, more
	specific shared prose-format selector; the final scoped `#run-detail` selector
	now wins. The rendered reference block reduced from 295 px to 162 px and all
	four not-measured KPI lines fit visibly. No run was active during reload.

## Destination metadata snapshot

Read-only authenticated `labs` metadata checked each catalog and all listed schemas:

- orion_bronze_test_ssuman: 56 tables listed, no ae2e_job_dep_chk_count match.
- orion_silver_test_ssuman: 50 tables listed, no ae2e_job_dep_chk_count match.
- orion_gold_test_ssuman: 55 tables listed, no ae2e_job_dep_chk_count match.

Exact lookup of catalog.tgt_oracle_220c515707fa.ae2e_job_dep_chk_count in each
catalog returned TABLE_DOES_NOT_EXIST. Current absence does not prove whether a
past table was omitted, removed or never created. No CREATE or business write was
performed. Choosing a new intended destination layer must not be confused with
schema-compatible fixture discovery.

Agent activity design: [responsibilities, memory and routing](migration-agent-responsibilities-memory-and-model-routing.md).
Evaluation definitions: [scorecard](agent-evaluation-scorecard.md).
Source assessment: [v25 coverage](migration-coverage-v25.md).

## Hosted timeout trace, 2026-11-16

Read-only `databricks apps get/list-deployments` confirmed the active Databricks
app is RUNNING deployment 01f1e14c095715a89139602d5fd11ae7. Exported only its
public static request scripts and compared hashes with local files:

- Deployed `hosted-intake.js`: SHA-256
	`6EBEDE5466EC17C5F3510E81E69DCD1FE768152CC16792A4AC282D0B5A811844`; it defines
	`REQUEST_MS = 110000` and aborts `request()` whenever the outer HTTP method is
	not GET. A hosted logical read is sent as `POST /api/hosted/operations`, so this
	abort applies even when the operation envelope's inner method is GET.
- Local `workbench/static/hosted-intake.js`: SHA-256
	`9E90A318B4042DA088F3BA83C5F9861AA79D6196B5A48C340D2082A71A719D6F`; its
	corresponding `request()` has no elapsed-time abort.
- Deployed `scoped-reads.js` defines `READ_MS = 120000` for reads without reported
	progress. Local `workbench/static/scoped-reads.js` explicitly has no app elapsed
	deadline. These deployed/local hashes differ.
- Databricks Apps' proxy timeout is a separate platform boundary and is not
	removed by browser code.

The hosted browser session redirected to Databricks sign-in, so operation
03303804ac0f42e49539b7139d1a8e01's per-user Delta status could not be verified
here. No retry or replay was made. The changed local request handling has not been
deployed. Release remains blocked by the separately recorded target-mapping and
native Job parameter/code-generation validation findings.
