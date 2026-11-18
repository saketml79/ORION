# Current release verification ledger

Deployment is conditional on the reported requirements and actual checks. Passing
regression tests is not a migration specification or runtime-parity certificate.

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
| Hosted loading timeout | Active Databricks deployment 01f1e14c095715a89139602d5fd11ae7 (2026-11-15) still serves old `hosted-intake.js` with REQUEST_MS=110000 for every non-GET request, including the POST wrapper for logical GET reads; its `scoped-reads.js` also retains READ_MS=120000 for non-progressing reads. Current local assets have removed both app deadlines and their hashes differ. Databricks Apps platform proxy remains a separate ~120s limit. Operation 03303804ac0f42e49539b7139d1a8e01 was not replayed; its completion remains unconfirmed. |
| Two full suite passes | Pending; all failures/skips must be reported, not hidden by exclusions |
| Two fresh visible agent conversions | Actual local v27/v29 runs completed and saved four notebooks each. Not two release passes: target not verified; v29 native YAML fails. See visible-run receipt |
| Deployment | Held until conditional requirements are satisfied; no redeployment authorized by a passing syntax check alone |

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
