# ORION hosted release verification

Latest complete app preview:
[Action progression and full ORION release, 2026-11-08 UTC](orion-guidance-release-2026-11-08.md).
Deployment `01f1db1090d31d44978737267dc6042f` is SUCCEEDED/RUNNING.
The detailed record preserves exact tests, assets, UTC timestamps and business gates.

## Cohort and readiness UI preview, 2026-11-07 UTC

The user explicitly authorized a preview of the current application, not a
business-pipeline release. The four independently verified business-workflow
gate remains blocked, with zero qualifying workflows. No business Jobs, model
calls, catalog DDL or business data writes were performed.

- One consolidated regression ran with the existing Python 3.13.12 venv,
	`PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and
	`pytest -c workbench/pytest.ini -q --tb=short --disable-warnings -ra`.
	**844 passed, 1 skipped, 1 warning, 602.31 seconds**. The skip is the Windows
	account's unavailable symlink privilege in `test_spark_codegen_api.py:362`.
	Dependency `pip check` passed. This is a focused regression, not a full suite.
- The consolidated paths were `workbench/tests/test_cohort_review.py`,
	`test_cohort_design_actions.py`, `test_draft_bundle.py`,
	`test_native_job_draft_conditions.py`, `test_target_review.py`,
	`test_delta_runtime.py`, `test_delta_state.py`, `test_hosted_operations.py`,
	`test_hosted.py`, `test_hosted_package.py`, `test_hosted_object_reads.py`,
	`test_hosted_readiness.py`, `test_hosted_registry.py`, `test_hosted_volumes.py`,
	`test_campaign_artifacts.py`, `test_validation_campaigns.py`,
	`test_validation_contracts.py`, `test_readiness.py`, `test_package_context.py`,
	`test_migration_lineage.py`, `test_migration_planning.py`,
	`test_workflow_artifacts.py`, `test_workflow_artifacts_api.py`, and
	`test_hosted_intake_ui.py`, all under `workbench/tests/`; and
	`tests/test_spark_codegen_api.py`, `test_spark_codegen.py`,
	`test_readiness_ui.py`, `test_migration_lineage_ui.py`,
	`test_package_context_visible_ui.py`, `test_next_action_guidance.py`,
	`test_proposal_selection_sync.py`, `test_navigation_prerequisites.py`, and
	`test_hosted_startup.py`, all under `tests/`.
- The existing allowlist staged **111 entries**, including cohort, worker journal,
	draft and architecture skill modules, current static assets and both runtime
	templates. All 65 staged Python files compiled, seven key staged modules
	imported, 18 JavaScript files passed syntax checking, all copied workbench
	sources matched local SHA-256, runtime and bundle environment definitions
	matched, and bundle validation passed. The root README is not in the existing
	runtime allowlist and was not packaged; architecture skill context and review
	instructions were packaged. No allowlist or source code was changed.
- A single `labs` / `dev` bundle deployment uploaded 112 files, deleted none,
	changed one existing app and created no resources. The documented app stop
	and bundle run completed. Release start **2026-11-07T04:28:04.885370Z**,
	completion **2026-11-07T04:35:35.999366Z**. Deployment
	**01f1da6451141193b064d1b739ada11c**, platform creation **04:33:01Z** and
	update **04:35:31Z**, was **SUCCEEDED / RUNNING**.
- Native JSON comparison against both immediately pre-deploy and post-deploy
	app metadata verified the same app ID, run-as and resources values (both null),
	requested and effective scopes, service principal, compute size and one
	minimum/maximum instance. The existing warehouse remained in both staged
	runtime templates. No credential was printed or stored in the release tree.
- Fresh authenticated OBO health returned HTTP 200. All **27 static assets**
	returned HTTP 200 with hashes matching the staged files. Scoped readiness and
	lineage GETs for the existing project and package returned HTTP 200 in
	**41.598s** and **21.217s**, respectively, with matching scope. Readiness
	reported **BLOCKED**, zero verified business workflows and deployment denied.
	These are individual API observations, not browser acceptance or latency
	guarantees. External staging and compilation scratch were removed and their
	absence verified at **2026-11-07T04:36:48.287602Z**.

## Coordinated candidate status

The combined candidate was deployed on **2026-11-05 UTC**, as recorded in the
final section below. The earlier candidate and deployment records remain intact
as historical evidence. Deployment is application hosting only, not full ETL
conversion, output parity or approval to run generated business workloads.

The previously pending scoped object/context projection and versioned saved
Java-analysis rebuild are documented in
[the candidate handoff](hosted-scoped-read-analysis-rebuild.md). Latest UI guidance
is included by allowlisted staging and all-static hash checks. The combined
release includes the separate conversion workstream's negative completeness gate,
not a completed parent/child ETL conversion. Historical deployment IDs and timings
below must not be substituted for the final combined release evidence.

Release authorization: update the existing `orion-migration-workbench` with profile `labs`. No scaffolding, business Job execution, model inference, catalog/table recreation or extension of the four reviewed compiler examples.

## Changes and observed causes

- Fixed the indentation of the existing incomplete-result-chunk rejection in `workbench/hosted_operations.py`; the predicate is unchanged.
- Runtime reproduction proved `_valid_progress('running', _progress('running'))` returned false. Aligned the producer with the canonical initial running record and allowed the existing dispatcher `audit_restore` phase. Strict counters, exact envelope, owner checks, credential filtering and commit readback remain enforced.
- Updated operation oversize regression to exceed the existing 640 KiB chunked-result cap, rather than the smaller per-Delta-record bound. Updated the integration wrapper to forward the existing progress callback. Added strict invalid-progress cases.
- Strengthened the fixture metadata test to construct the real hosted dispatcher, retaining fresh two-user OBO, same-origin rejection, no local CLI fallback and no full Delta/audit flush assertions.
- Inspected route installation order: fixture route is installed before the hosted model installer replaces its identity provider. Project child applications use that same factory. Metadata POST is observational, restores with `initialize=False`, preserves explicit project/package selection, and does not publish a content generation or Delta audit write.

## Regression commands

Environment: existing Python 3.13.12 venv, `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

```powershell
./.venv/Scripts/python.exe -B -m pip check
./.venv/Scripts/python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_hosted_package.py workbench/tests/test_hosted_registry.py workbench/tests/test_hosted_volumes.py workbench/tests/test_delta_runtime.py workbench/tests/test_source_fixtures.py workbench/tests/test_databricks_models.py tests/test_fixture_metadata.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_presentation_review.py tests/test_connections.py -q --tb=short --disable-warnings
```

Dependency check passed. Final combined batch: **455 passed, 0 failed, 1 existing schema-shadow warning**, 216.07 seconds. Editor diagnostics for the four modified Python files are clean.

Earlier expanded diagnostic batch: **408 passed, 29 failed**, 1 warning. Twenty-three failures were hosted operation/updated-interface blockers investigated above. Six failures were in `tests/test_hosted_startup.py`: two fresh-project cases, multiple-project selection, two saved-selection cases, and scoped-overview activity. Their shared `ready()` helper expects agent invocation enabled without loading persisted agent-run status. Current UI intentionally keeps invocation disabled until `/api/agents/runs` is verified. No production gating change or fixture weakening was made for these unrelated failures. No clean full-suite claim.

## Packaging and deployment checklist

- Existing app ID: `cfada07d-ffa6-4018-9080-293f5e577083`.
- Existing workspace: `https://adb-1866518241053589.9.azuredatabricks.net`.
- Existing warehouse: `e3a0fc2c08db05bb`.
- Preserve single instance and requested scopes: `catalog.catalogs`, `catalog.schemas`, `catalog.tables`, `files`, `model-serving`, `sql`. The two default IAM scopes remain platform-provided. Existing app has no attached resource array to replace.
- Stage only through the existing allowlist in a brand-new external temporary directory. Compile every staged Python module and test isolated staged hosted imports before validation/deployment.
- Use existing bundle validation, bundle deployment and app-resource run workflow with exact `--profile labs -t dev`. No business Jobs exist in this app-only bundle.
- Remove the release-created staging tree, including CLI state and compilation scratch, on success or failure.

## Readiness boundaries

App hosting is not production migration readiness. Compiler support remains WIL_NET_DIS, WIL_NET_INS, WIL_CUS_INST and WIL_CUS_DIS only; arbitrary/batch workflow compilation is unsupported. Fixture observations never approve production bindings, target mappings or data parity. Long requests still face the platform 120-second timeout. Browser-specific OBO UC consent and interaction require an actual signed-in user smoke test; service/proxy health or static delivery alone does not establish UC verification.

Required agentic-blueprint directory/PDF and the parent databricks-core skill are absent in this checkout/environment. The available proposal-first blueprint and installed Apps platform/other-framework guides were consulted.

## Completed deployment

- Allowlisted stage: 76 manifest entries; **37 Python files py_compile passed**, **35 isolated workbench imports passed**. Compilation outputs were removed before upload.
- `databricks bundle validate --profile labs -t dev`: Validation OK.
- `databricks bundle deploy --profile labs -t dev`: 77 files uploaded (including package manifest), 0 resources created, 1 existing app updated, 0 resources deleted.
- `databricks apps stop orion-migration-workbench --profile labs -o json`: stopped before replacement.
- `databricks bundle run workbench --profile labs -t dev`: synchronous completion, traffic switched successfully.
- Overall release: **2026-11-04T01:08:20.9173755Z to 2026-11-04T01:14:04.0926254Z**.
- Deployment ID: `01f1d7ecbedc1cc78350355fb01cc945`.
- Platform deployment create/update times: **2026-11-04T01:12:03Z to 2026-11-04T01:14:04Z**.
- Confirmed **SUCCEEDED / RUNNING**, existing app ID unchanged, one active instance, six requested scopes unchanged, no attached resource array added.
- URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com
- Live labs-user OAuth proxy smoke: `/api/health` 200 (1.67s), `authentication=databricks_obo`; `/api/databricks/volumes/status` 200 (0.17s), `mode=obo`, requesting user ID matched the labs identity. These status routes do not verify live UC reads.
- Both deployed static scripts returned 200 and matched local bytes exactly. UC controls, zoom controls, selection callbacks and notebook task inspector markers verified. An initial smoke assertion used an absent `Open task` label for app.js, corrected to its actual selection callback without changing source.
- `databricks apps logs orion-migration-workbench --profile labs --source SYSTEM --tail-lines 12`: previous process stopped reported at 01:14:06Z. No application credentials or raw evidence logs fetched.
- External release staging and CLI scratch were removed in the deployment finally block and absence rechecked. No transient staging/log directory was created in the checkout. No Git metadata, no git sync or push performed/requested.
- Browser-specific UC metadata observations, consent and actual notebook interactions remain for user testing. No live model inference, business Jobs, fixture creation or Delta state mutation was performed by the smoke checks.

## Consolidated concurrency and progress release, 2026-11-04 03:04 UTC

### Cause and scoped changes

- The insert-only metadata MERGE had only target/source equalities. It lacked constant target predicates, allowing broad target read dependencies. Independent operation acceptance/progress writers do not use the dispatcher's per-user action lock, so they could overlap other app writers to the same physical jobs table. User/operation locks are insufficient for that shared physical write domain.
- `workbench/delta_state.py`: parameterized constant target predicates for every exact revision in mixed-scope batches, retaining every source equality and insert-only revision identity. Physical-table serialization is shared by all adapter instances and covers Statement API completion; idle lock entries are removed and no clients or credentials are retained. No retries, suppressed errors, table alterations, provisioning, updates or deletes were added. This is explicitly single-replica application serialization, not distributed CAS, a uniqueness constraint, or protection from external writers. Constant predicates constrain the requested target scope; an optimizer's physical read plan is not independently verified here.
- `workbench/static/hosted-intake.js` and `workbench/static/app.css`: responsive soft progress card, separate collapse/expand and close/minimize, active minimize explicitly does not cancel, collapsed operation ID, in-card reconnect, measured progress only when reported. Existing blur/inert blocking is retained, keyboard Tab/Shift+Tab and Escape are trapped, focus restores after the last blocker exits, and background agent-run observations remain nonblocking.
- `workbench/static/app.js`: preserve the existing immediate selection and capability fixes; add explicit selected/loading/loaded/failed states and retry only after real load failure. Source-only unassessed nodes are neutral with a plain-language explanation, not an invented amber score. Recorded colored assessments and canonical error diagnostics remain visible. Four reviewed wrapper compiler support, app OBO authentication, inference unverified and unsupported general conversion/deployment remain separate capability facts.
- Regression edits: `workbench/tests/test_delta_state.py`, `workbench/tests/test_hosted_operations.py`, `workbench/tests/test_hosted_intake_ui.py`, `workbench/tests/test_capability_status.py`, `workbench/tests/test_ui_structure.py`. Added mixed-scope target predicates, table lock/failure release, overlapping object workers preserving all emitted immutable stages, responsive geometry/focus trapping, actual failed-object retry and neutral assessment checks. Structural source assertions updated for the new presenter; no behavioral gate weakened.

### Regression evidence and boundaries

- Dependency check passed. First collection attempt caught patch indentation, corrected before execution. The first executing consolidated batch was interrupted during browser coverage; it is not a completed test count.
- Completed follow-on verification: **543 passed, 40 failed, 14 teardown errors, 1 warning, 740.47s**. All changed Delta, operation, modal, capability and object-loading behavioral regressions passed. Three failures were stale structural assessment-presenter assertions, corrected and targeted rerun **3 passed**. Remaining **37 failures and 14 associated teardown errors** match existing navigation/proposal/startup/package-autoload fixture contracts: hosted health mocks omit OBO authentication, proposal mocks omit the current detail endpoint/paged contract, and readiness helpers expect agent invocation enabled before persisted-run verification. These were included and reported, not hidden or weakened. No clean full-suite claim.
- Additional hosted diagnostic rerun: **32 passed**; one notification punctuation failure was corrected before the completed verification.
- Commands: existing venv `python -B -m pip check`; `python -B -m pytest -c workbench/pytest.ini ... -q --tb=line --disable-warnings`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Completed coverage: delta_state, delta_runtime, hosted_operations, hosted_intake_ui, capability_status, package_graph, presentation_review, requested_ui, ui_structure, redesign_ui, workflow_artifacts, workflow_artifacts_api, fixture_metadata, navigation_prerequisites, proposal_selection_sync, hosted_startup, intake_loading_controls, loading_diagnostics_ui, selected_package_loading, project_package_autoload and ui_spacing_borders. Hosted, hosted_intake_integration, hosted_package, hosted_registry and hosted_volumes also executed in the interrupted initial batch; hosted.py's 32 tests completed on the diagnostic rerun.

### Deployment and actual live evidence

- Existing app only, exact labs executable: `C:/Users/SaketSuman/AppData/Local/Microsoft/WinGet/Packages/Databricks.DatabricksCLI_Microsoft.Winget.Source_8wekyb3d8bbwe/databricks.exe`.
- New external allowlist stage: 76 manifest entries, **37 Python syntax checks and 35 isolated imports passed**. Bundle validate passed, bundle deploy uploaded 77 files, **0 created / 1 existing app changed / 0 deleted**. Existing app stop followed by bundle run workbench completed successfully. All bundle commands used `--profile labs -t dev`.
- Release start **2026-11-04T02:58:10.6533946Z**, end **2026-11-04T03:04:35.4277710Z**. Deployment **01f1d7fc22361c28883c72b52170ed01**, platform create **03:02:13Z**, update **03:04:26Z**, **SUCCEEDED / RUNNING**, one replica, existing ID and six requested scopes unchanged. URL unchanged: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com.
- Actual fresh labs-user OAuth via supported Apps proxy verified OBO and the four compiler capability records. All three changed deployed static assets matched local bytes.
- Authoritative live project **84b03a652f5e4371ba1c72d401acba6b**, completed LumenExample.XML package **08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba**. Inventory lookup returned actual canonical wrapper **wkf_AE2E_DUMMY_CALL_WIL_NET_DIS**, ID **00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38**. Initial smoke incorrectly expected the wrapper's bare suffix as its name; inspecting the already-committed inventory corrected the smoke assertion without replaying it or changing application code.
- Live UC metadata HTTP 200: one source, existing `infotodbxmig_src_dev_ssuman.src_oracle_atldev_ae2e_56bd1c2fb7cc.ae2e_job_dep_chk_count`, metadata_verified_fixture_only, binding fixture_only_unapproved. No fixture creation, data sampling or binding approval.
- Two overlapping scoped OBO object operations **54f75a929710462e849956b26222c6e7** and **4b8d0ae1d6e84ac093649008f1465531** both returned the exact canonical object, committed with actual queued/running/provisioning/metadata/artifact/preparing/responding progress, **160.45s and 157.53s**. No concurrent MERGE failure observed. Direct canonical GET object subsequently **200 in 42.44s**. Live smoke ended **2026-11-04T03:12:53.4928671Z**.
- Distinct-user live concurrency remains **unverified**, only labs identity available. Offline fresh two-user isolation remains covered. No live model inference, business Jobs, new UC resources, data deletion or SP fallback. Multi-process/external-writer guarantees and latency improvement are not claimed.
- Release-created external staging removed in finally and absence verified. External regression report removed after recording counts. No transient repository logs created; no Git metadata, push or sync available.

## Combined scoped-read, UI and parity-gate release, 2026-11-05 UTC

### Authorization, checks and preserved boundaries

The user's explicit combined-release instruction authorizes deployment of the
remaining local UI, source evidence/context, scoped-read and parity-gate changes,
and one versioned analysis rebuild for the current labs user's existing project
84b03a652f5e4371ba1c72d401acba6b and saved package
08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba.
It does not authorize reanalysis of other projects/users, canonical replacement,
review renewal, model inference, business Jobs or source/target business SQL.

- Dependency check: existing venv `python -B -m pip check`, passed.
- Combined narrow integration command, all paths executed without deselection:
	`python -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_object_reads.py workbench/tests/test_source_analysis.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_package.py workbench/tests/test_package_parity.py workbench/tests/test_workflow_artifacts_api.py -q --tb=short --disable-warnings -ra`.
	Result: **361 passed, 1 existing warning, 186.64 seconds**.
- Post-deploy offline UI regression command:
	`python -B -m pytest -c workbench/pytest.ini tests/test_next_action_guidance.py workbench/tests/test_hosted_intake_ui.py -q --tb=short --disable-warnings -ra`.
	Result: **93 passed, 121.71 seconds**. This verifies offline behavior, not a
	signed-in hosted browser session. Both commands used the configured Python
	3.13.12 venv, PYTHONDONTWRITEBYTECODE=1 and
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1.
- New external allowlisted stage: **85 manifest entries, 44 staged Python modules
	py_compile passed, 42 isolated imports passed, all 22 static assets matched local
	SHA-256**. The increase from the preceding 42-module candidate includes
	dependency_compiler.py and package_parity.py. All staged JavaScript passed
	`node --check`. Source XML, output state, tests, secrets and bytecode were not
	packaged. Actual compiled outputs were outside the staged candidate.
- `databricks apps validate --profile labs` explicitly returned unsupported
	project type (looks for package.json). No validation bypass was represented as
	success: actual Python compile/import, JS syntax, regressions and the existing
	`databricks bundle validate --profile labs -t dev` passed instead.
- The documented migration-planning unresolved-reference assertion, expected 1,
	actual 0, remains open and was not rerun or weakened. Prior 354-pass expanded
	results and the conversion workstream's 512-pass/1-skip results remain separate
	evidence, not a clean combined whole-repository suite.

### One existing-app deployment

- Exact executable: installed WinGet Databricks CLI **v1.13.0**, profile `labs`.
- Commands: existing bundle validate, bundle deploy, apps stop
	orion-migration-workbench, bundle run workbench. Bundle commands used `-t dev`.
	Completion was synchronous; no agent-authored sleep, deployment retry or
	repeated deployment was used. CLI internal lifecycle progress is not a second
	deployment. A terminal session/environment switch after completion required
	restoring the external report path for smoke checks, not rerunning deployment.
- Bundle deployment: **86 files uploaded, 0 deleted; 0 resources created,
	1 existing app changed, 0 resources deleted**.
- Release start **2026-11-05T00:47:33.480580Z**, end
	**2026-11-05T00:54:00.208141Z**.
- Deployment **01f1d8b3148815d194f5a475314ec5d3**. Platform create
	**2026-11-05T00:51:48Z**, update **2026-11-05T00:53:56Z**.
	**SUCCEEDED / RUNNING**, traffic switched successfully.
- App ID **cfada07d-ffa6-4018-9080-293f5e577083**, six requested scopes, two
	effective default IAM scopes, no attached-resource array, and one minimum/maximum
	instance preserved. Existing warehouse **e3a0fc2c08db05bb** retained in both
	staged runtime and bundle config. No new resource or permission was introduced.
- URL unchanged: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com.

### Authenticated live read and static evidence

Fresh labs-user OAuth credentials were held only in memory and sent through the
supported Apps HTTP proxy. No raw credential or proxy header was printed.
Health returned 200 with `authentication=databricks_obo`, exactly four reviewed
wrapper compiler capabilities, general generation and deployment gates still
false. No model call was made.

Exact scope: project/package above, canonical wrapper
00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38.

| Observation | Actual result |
|---|---|
| Immediately pre-deploy object GET | 200, **67.926 seconds**, 175,169 bytes |
| First post-deploy cold object GET | 200, **36.275 seconds**, identical bytes/hash |
| Additional post-deploy object GET | 200, **33.337 seconds**, identical bytes/hash |
| Warm object GET | 200, **15.304 seconds**, identical bytes/hash |
| First migration-context GET | 200, **16.686 seconds**, 54,767 bytes |
| Second migration-context GET | 200, **16.677 seconds**, identical bytes/hash |
| Selected source-analysis status GET | 200, **50.284 seconds**, analysis version null, source_reparsed false |
| Live static delivery | **All 22 assets HTTP 200 and exact staged/local SHA-256** |

Object SHA-256, before and after:
aa204679fc4f66e166962165fa2ed9582fee7a88b813a12a0c3f479c38bedc28.
Migration-context SHA-256:
0291cb96e376c1e28279d839f6e6f8b995b492177ddf3fcbf8e235a605f38984.
The first smoke's text-mode Windows decoder incorrectly made identical-byte JSON
look different. Explicit UTF-8 bytes fixed that comparator, with no production
change. These are individual observed samples, not a universal latency guarantee
or evidence that all routes use the scoped projection. The historical 43.584-second
sample remains historical, not the immediate release baseline.

Exact static delivery includes nonmodal hosted reads, borderless minus/close
controls without Collapse, next-action JS/CSS and HTML includes, and existing
wrapper-only compile status. Offline UI behavior was exercised; hosted browser
interaction/consent and distinct-user live concurrency remain **unverified**.

### Explicit saved-analysis operation

- Fresh GET established base understanding SHA-256
	dec6cdd4101e311eebf0f4a258e7738bd1c5881a0e79176dc96ce282d6dbb1fa,
	selected package exact and analysis_version null.
- Before submission, independently verified latest committed receipt/generation
	**bbe83d38ce3345dc801fa49e351d2ecd**, sequence **10**, package membership,
	all **10 selected artifact references**, registry and historical inventories.
- Submitted exactly once at **2026-11-05T00:58:30.044952Z** through
	POST /api/hosted/operations, inner POST /api/source-analysis/rebuild, with
	consent:true, exact selected headers, expected base hash and null prior analysis
	version. Same-origin mutation header/Origin were supplied. HTTP **202 queued**.
- Operation **43ccfa4d97c9400cb52687268ae4af1c**. No duplicate POST/reupload or
	direct canonical mutation was used. The observation at 01:02:44 UTC still saw
	generation 10; the durable operation was running/responding. The final state is
	mixed: committed analysis verified below, but operation acknowledgement failed.
- At that observation all 10 canonical references and 16 historical metadata
	families were unchanged. Hosted committed agent-run/review inventories were
	both zero, so no existing live agent/review staleness example can be claimed.
	Offline regressions verify old input pins become stale and are not renewed.

### Final analysis authority, failed acknowledgement and cleanup boundary

At **2026-11-05T01:03:34.969120Z**, the durable operation reported **failed**,
error_type RuntimeError, generic "Hosted operation failed." No success receipt
was fabricated and the POST was **not replayed**. Built-in per-user diagnostic
GET returned no diagnostic records. Bounded APP logs contained startup/shutdown
only, not a useful failure stack. The exact acknowledgement failure cause is
therefore **unresolved**, not attributed to a guessed timeout or parser failure.
No speculative application patch, restart or second deployment was performed.

Independent fresh OBO verification subsequently established:

- Committed generation **650e5b622fbe4d7d8c803296062bb64e**, sequence **11**.
- Active analysis version
	**9c23dc650c74a5a560846bef7d1e5d2f2c15feb97508a03bb5c3ef62ce45d469**.
- Compressed analysis artifact SHA-256
	**ed2421cae5721ab613e90afd7b9f214c9d61bb6531a31784f23747565b0e5c44**.
- Exact selected package/base understanding unchanged. Verified immutable analysis
	archive contains **5 Java-owning canonical IDs, 30 property records**. This is
	evidence enrichment, not Java-to-Spark translation or an ETL parity certificate.
- Java owner IDs: 59638be18dade6258a5dcc17ff524871c3e24be80fae181951f41c4012d26786,
	8cb36d3a3d9a711ffa1cf8cfdc0a60bf270a7c0a4b2066cf1b70272e5a15f895,
	b4b1f33d2cc5fc3bc75aaf600454a90ddf7de2c3ea473119f40582297f30c2b2,
	c1e2f3a7e9fe89a9967013ca70c2d33f7846ef48a737ab29f7b1f02d7580f045,
	c3943ebd3354629c27f853dadb8f1cbb668609b1e326e47eab3dd03d5a049bed.
- All **10 original selected artifact references** (hash, byte length, immutable
	Volume path) remain exact, including canonical source/model/manifest/English
	and selection state. Registry and all 16 historical metadata families remained
	identical. No live agent runs or reviews existed in this committed namespace;
	staleness behavior is offline-tested, not demonstrated on nonexistent live runs.
- Full generation-inventory diff verified **zero removed entries** in every
	family. Only additions: **2 artifact_manifest, 1 settings, 4 audit_events**.
	All non-audit additions have the exact authorized project/package scope.
	Original audit entries are retained, and other project metadata is unchanged.
- Live selected GET /api/source-analysis returned **200 in 41.920s**, matching the
	committed version and original base hash. Live Java-object GET returned **200
	in 15.551s**, same canonical ID and expected embedded-code property sidecars.
- Post-analysis wrapper GET **200 in 15.196s**, still byte-identical to the
	pre-deploy 175,169-byte baseline. Migration context **200 in 17.335s**, still
	byte-identical to its pre-analysis response. App remained RUNNING on the same
	deployment. Final live check **2026-11-05T01:07:10.471415Z**; final complete
	inventory preservation check **2026-11-05T01:07:35.545933Z**.

This establishes a live, authoritative analysis overlay despite a failed durable
operation acknowledgement. It is **not** a clean successful-operation claim.
Inspect committed state before any future repair/retry; do not renew approvals
or replay the mutation merely because the operation is labelled failed.

The temporary verification harness also initially compared in-memory registry
tuples to JSON-restored lists. Canonical JSON comparison corrected that harness
only; all unchanged-file and historical-inventory checks then passed. No
production behavior or integrity gate was weakened.

Selected deployed static fingerprints (all 22 were independently verified):

| Asset | SHA-256 |
|---|---|
| app.js | fb7c2048bfed7f3dbcc74fce298cb8c4329891e1699b0c48cf4d61fa9ae446e7 |
| app.css | 0ab384272a4470f9d5b78ebb10589800680a3363dafe7b413259ce301e7d1850 |
| hosted-intake.js | 4ceec7d7fcd728eb7c0925d90db4b848ca420134592ebd32e48e32af0dd9e9c4 |
| next-action.js | 2a2c1c4cca889a53935d377e9ab9d890130ac5eac9f76d647c00519ac7eead27 |
| next-action.css | e8d19c929656c9a0336b293503e41f412286eb29dbcdbd0cd7cdd8ef8b6b7f98 |
| index.html | 1f9fd454b943e2fa8704c8a4d96178c6a35514c7fccaf32d1d4be0f04deb1f3d |

Release-created external staging, CLI state, baseline captures and analysis
verification reports were removed and their absence verified at
**2026-11-05T01:08:20.917916Z**. No previous temporary directories or repository
history were deleted. Final documentation links and ledger-to-receipt consistency
checks passed. All 44 staged Python modules and 22 static assets still matched the
workspace at cleanup, with no source drift or temporary production instrumentation.
Only this durable release ledger is retained; no transient report/log capture was
created in the checkout. No Git metadata, push or sync was available/requested.

Full ETL translation remains unfinished. Four child loads, distinct log-check
uses, dependency-session writes/error recovery, workflow branches, external
procedure behavior, parameter resolution and runtime bindings need implementation
and owner evidence. No output-parity or production-conversion claim is made; see
[the exact implementation and owner boundaries](wil-parent-child-parity.md).

## Acknowledgement investigation and separate reconciliation, 2026-11-05 UTC

### Proven failure boundary and remaining historical diagnostic limitation

Fresh OBO reads confirmed the active original deployment was exactly
`01f1d8b3148815d194f5a475314ec5d3`, not an older release. Exported immutable deployed
bytes for hosted dispatcher, operations, runtime and source-analysis modules all
matched the checkout before editing. The original operation
`43ccfa4d97c9400cb52687268ae4af1c` has exactly **13 immutable stages**: queued,
running, restore/index phases, responding, then failed/RuntimeError. No result
chunks were published. The original terminal payload has only the generic safe
error category, no throwing frame, action status or failure phase.

The verified generation-11 audit independently records inner
`POST /api/source-analysis/rebuild returned HTTP 200`, timestamp
**2026-11-05T01:00:37.373525+00:00**, duration 33,320.3 ms. Generation 11 is the
direct committed child of generation 10. Thus analysis committed and its inner
handler returned 200; the durable acknowledgement failed afterward. This is not
an analysis/parser failure or evidence of remote rollback.

The original result was reconstructed from exact committed pins and the unchanged
producer. Its JSON is **686 bytes**, below both the inline 24 KiB and total
640 KiB gates. The actual result envelope, boolean generation/deployment gates,
completed progress and Delta record/readback validator all passed in an inert
in-memory reproduction using the live request fingerprint. Normal real hosted
offline rebuild results also completed. There is no `_validate_payload` function
in this checkout/deployed source path. These facts reject the proposed size or
tested result/progress-schema explanation; they do not prove which historical
RuntimeError site threw.

The exact historical RuntimeError site is **not recoverable from the retained
evidence**. Correct `/api/hosted/logs?limit=30` returned zero diagnostics; bounded
APP logs returned only ten startup/lifecycle lines. One initial diagnostic probe
used a nonexistent endpoint and returned 404, not successful diagnostic evidence.
The old operation serializer discarded the distinction between action failure
and acknowledgement readback failure. A synthetic responding-journal readback
failure reproduces the observed commit-plus-failed shape, but it is **not proof**
that this was the original failure. The original-frame investigation remains
blocked by absent retained diagnostic evidence, not by missing business SQL,
owner procedures, or authorization to replay. No speculative exact root-cause
claim is made, and no rebuild was replayed to obtain a replacement traceback.

### Precise patch and status semantics

- [hosted_operations.py](../workbench/hosted_operations.py): freeze caller-owned
	history/payload JSON before acknowledgement publication/comparison; validate
	progress before publication; preserve bounded future failure phases and actual
	non-2xx action status without raw payload/error text. Older terminal history
	remains valid. Failed operations may expose a separately verified observation;
	their execution `status` remains failed and duplicate IDs never reexecute.
- [operation_reconciliation.py](../workbench/operation_reconciliation.py): new
	explicit same-user reconciliation endpoint under the existing operation API,
	`POST /api/hosted/operations/{id}/reconcile`. It requires consent, the exact
	original request fingerprint, selected project/package headers, parent/current
	direct-child commit, original expected base/prior-analysis version, overlay
	hash, semantic analyzer hashes and exact scope. It verifies both complete
	generation inventories/envelopes, registry membership, preserved canonical
	references, pointer artifact versus settings authority, archive version/hash
	and every joined canonical owner/property provenance. Canonical/review/config
	changes outside the authorized pointer/archive are blockers, never repaired.
	Only additions to audit are allowed. A final fresh receipt/history check is
	required before appending one separate immutable jobs observation. No full
	runtime restore/flush, XML parse, source upload or executable workload occurs.
- [source_analysis.py](../workbench/source_analysis.py): GET now verifies saved
	archive/version and analyzer fingerprints and reports `analysis_state` as
	current, stale_analyzer or not_rebuilt. It no longer falsely demands a rebuild
	when the current saved overlay is already active. GET never reparses source.
- [hosted-intake.js](../workbench/static/hosted-intake.js): explicit verified
	commit/failed-acknowledgement wording. A failed operation still rejects rather
	than resolving as completed; it explicitly says not to replay the rebuild.

This is not a blanket failure-to-success conversion. The original execution is
failed, and an independently proved content commit is a separate fact. Failed
pre-commit operations and mismatched/stale inputs cannot gain an observation.

### Regression and exact candidate evidence

Initial focused run: 79 passed, two new strict-verifier fixture failures. The
fixture's first-ever runtime restore changed JSON key order in preexisting
manifest/job/activation artifacts, which the strict byte-preservation verifier
correctly rejected. The fake baseline was stabilized **before** the tested
analysis commit, matching the existing live baseline. No production canonical
gate was weakened. Follow-up focused run: **81 passed**.

Final staged test invocation used the existing Python 3.13.12 venv,
PYTHONDONTWRITEBYTECODE=1 and PYTEST_DISABLE_PLUGIN_AUTOLOAD=1:

`python -B -m pytest -c workbench/pytest.ini workbench/tests/test_operation_reconciliation.py workbench/tests/test_source_analysis.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_delta_runtime.py workbench/tests/test_hosted_intake_ui.py -q --tb=short --disable-warnings -ra`

The external harness imported **staged production modules**, extended the test
package path only for repository test fixtures and asserted every loaded
production module came from the candidate. **188 passed, 1 existing schema-shadow
warning, 187.05 seconds**. Earlier staged iteration: 186 passed, before adding the
opaque-credential and browser observation tests. No huge/full-suite claim.

Coverage includes commit-then-lost-response, responding readback failure after
commit, pre-commit error remains failed, no replay/flush/parse/source overwrite,
original history retained, duplicate reconciliation/operation IDs idempotent,
wrong selection/request/parent/commit/overlay/analyzer pins and corrupted source
references rejected, opaque initiating credentials rejected, actual hosted HTTP
reconciliation, current/stale analyzer status, normal typed result completion,
and browser rejection/no-duplicate/no-false-completion behavior. Changed Python
and JS editor diagnostics are clean. `python -B -m pip check` passed.

Brand-new final external allowlist stage: **86 manifest entries**, **45 Python
syntax checks**, **43 isolated imports**, all staged JavaScript syntax checks
passed. Every preexisting staged path was compared against the immutable active
deployment export. Exactly **three existing production paths changed**, plus
the new reconciliation module, no unrelated source drift. Bundle validation
passed. No tests, XML, output state, secrets or bytecode were packaged.

### One patch deployment

- Commands: existing `bundle validate`, `bundle deploy`, `apps stop`, then
	`bundle run workbench`, exact labs profile and dev target for bundle commands.
	One deployment, no retry. Terminal backgrounding while the CLI waited did not
	trigger a second command or second deployment.
- **87 files uploaded, zero deleted; zero resources created, one existing app
	changed, zero resources deleted**.
- Release start **2026-11-05T01:34:10.113055+00:00**; end
	**2026-11-05T01:40:16.511177+00:00**.
- Deployment **01f1d8b9846a1dc887b72c58b05a03de**.
	Platform create **2026-11-05T01:37:52Z**, update
	**2026-11-05T01:40:07Z**, **SUCCEEDED / RUNNING**.
- Existing app ID, six requested scopes, two effective default IAM scopes,
	no attached-resource array, existing warehouse and one min/max instance retained.

### Durable reconciliation, original failure retained

Read-only live preflight reached the exact observation-publication boundary at
**2026-11-05T01:24:27.631962+00:00**, with the write intercepted locally and
**zero remote writes**. It verified all requested pins, full direct-parent/current
inventories, canonical references and actual joined overlay before deployment.

After deployment, exactly one POST to the separate reconciliation endpoint
returned **HTTP 200** with execution `status: failed`,
`observation: committed_with_ack_failure`, `original_history_preserved: true`,
`mutation_replayed: false`. Observation timestamp
**2026-11-05T01:41:54.055323+00:00**. Fresh independent OBO Delta and HTTP readback
completed at **2026-11-05T01:43:17.080483+00:00**:

| Verified fact | Exact durable pin |
|---|---|
| Original operation | 43ccfa4d97c9400cb52687268ae4af1c |
| Original request fingerprint | 1c475a5d61841e16fabe5863bb264b1afc2564d1e1116455917dbf668eaa0340 |
| Original 13-stage terminal-history SHA-256 | bea19e593898fd3557355edcb2fff2e0429a50cedef982f17f3c04294ad76b7d |
| Direct parent generation | bbe83d38ce3345dc801fa49e351d2ecd |
| Unchanged content generation, sequence 11 | 650e5b622fbe4d7d8c803296062bb64e |
| Active analysis version | 9c23dc650c74a5a560846bef7d1e5d2f2c15feb97508a03bb5c3ef62ce45d469 |
| Compressed overlay SHA-256 | ed2421cae5721ab613e90afd7b9f214c9d61bb6531a31784f23747565b0e5c44 |
| Original base understanding SHA-256 | dec6cdd4101e311eebf0f4a258e7738bd1c5881a0e79176dc96ce282d6dbb1fa |
| understanding.py analyzer SHA-256 | 8a349c04ee44a362c9b588cb5b9599f3d3c109d26d51479145bcdb8272d72bd3 |
| source_evidence.py analyzer SHA-256 | deceaa8c2b535ca09dc9fa1eed5aea73037f8e5a102ff5f6e1e4669495158e13 |
| Committed manifest SHA-256 | 7b9dd1b432126c20b505ed1b93f065742e959910f154b6ea74bf7461f3325ecc |
| Preserved parent inventory SHA-256 | 1c36ac7a87bd5b0f751b211e94aefc3250a8b3dff4cde3c3445a86b017b8fb3d |
| Preserved canonical/source file references SHA-256 | 740bcce4bece15d5b415cb99e690589b0c65a445ea13450aaa6c6903439626ee |
| New reconciliation proof SHA-256 | 4aec87aca72789a57c3bce162bfe87e3856fce58c098349d7600af3040a45226 |

Exact workspace/user/project/package pins are included in the durable receipt,
not inferred from a UI selection. The observation is one additional insert-only
row in jobs under its own `hosted-reconciliation:{operation}` context, outside
the original terminal operation journal and outside content-generation authority.
The original 13 stages remained byte/value-exact. No generation 12 was published.

Live operation GET reports failed **plus** the independently verified committed
fact and no-replay guidance. Live selected source-analysis GET reports the same
version, `analysis_state: current`, `explicit_rebuild_required: false`,
`source_reparsed: false`. Health is fresh OBO, and changed JS delivery matched
the staged SHA-256. These are HTTP observations plus offline browser tests, not
a signed-in browser interaction claim. Warm 15-second object samples in the
preceding release remain historical; no new object-latency guarantee is claimed.

No model inference, business Jobs, source/target business SQL, canonical rewrite,
review renewal or missing-procedure substitution occurred. Only app metadata
SQL and the separate authorized observation write were used. The exact original
throwing frame remains unavailable, so the historical root-cause investigation
must not be described as fully resolved.

Final production-source hashes still matched the deployed candidate, Python AST
and ledger links passed. Both release-created external candidate trees, CLI
state, compilation scratch, verification report and harness were removed; the
release scratch root's absence was verified at
**2026-11-05T01:44:41.876798+00:00**. No transient log/report file was created in
the checkout. Only the durable ledger, application patch and regression tests
remain. No Git metadata, push or sync was available/requested.

## Authorized hosted UI preview, 2026-11-06 UTC

The user explicitly authorized a hosted UI preview of the current candidate.
This authorization does not approve pipeline execution or production release and
does not supersede the four independent business-workflow verification gate.
The preview includes Readiness, migration lineage, immutable campaign report
support, guided UI and the current fixture and UI-test fixes. The independent
fixture provisioning documented in the reconciliation ledger is not part of
this app deployment.

### Consolidated predeployment checks

One fresh combined regression was run after the latest fixture changes with the
workspace Python 3.13.12 environment, `PYTHONDONTWRITEBYTECODE=1` and
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:

`python -B -m pytest workbench/tests/test_campaign_artifacts.py workbench/tests/test_validation_campaigns.py workbench/tests/test_validation_contracts.py workbench/tests/test_ten_workflow_fixtures.py workbench/tests/test_source_fixtures.py workbench/tests/test_readiness.py workbench/tests/test_hosted_readiness.py workbench/tests/test_migration_lineage.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_package.py workbench/tests/test_delta_state.py workbench/tests/test_delta_runtime.py tests/test_readiness_ui.py tests/test_migration_lineage_ui.py tests/test_navigation_prerequisites.py tests/test_next_action_guidance.py tests/test_hosted_startup.py tests/test_project_package_autoload.py tests/test_proposal_selection_sync.py -o addopts= -q --disable-warnings -ra`

Result: **396 passed, 2 warnings, 349.55 seconds**, no failures or skips. This
is a focused batch, not a full-suite or business-execution result. A reported
657-test result was not located in the available release records and is not
used to represent this candidate's test coverage.

The new external allowlisted stage contained **106 manifest entries**, 60 Python
modules compiled successfully, all 27 static assets matched local SHA-256, 18
JavaScript files passed syntax checking and the staged hosted/readiness/lineage/
campaign modules imported successfully. Bundle validation passed with explicit
`labs` profile and `dev` target. The first stage attempt stopped before bundle
validation or deployment due to Windows quoting of the import-check print
statement; imports had succeeded, both temporary stages were removed and no
application source or tests were changed in response.

### Deployment and post-release smoke

- Fresh labs identity: `ssuman@bpcs.com`. Existing app ID
	`cfada07d-ffa6-4018-9080-293f5e577083`; predeployment active ID
	`01f1d8b9846a1dc887b72c58b05a03de`. Rechecked before upload and after upload,
	with no detected concurrent deployment drift.
- One existing-app bundle deploy, stop and bundle run. **107 files uploaded,
	zero deleted; zero resources created, one existing app changed, zero deleted.**
	No catalog, SQL business data, Jobs, model invocation or permissions changed.
- Release start **2026-11-06T23:33:48.2768236Z**, completion
	**2026-11-06T23:41:18.6582156Z**. Deployment
	**01f1da3b33011400b2050ac87753b587**, platform create
	**2026-11-06T23:38:41Z**, update **2026-11-06T23:41:08Z**, status
	**SUCCEEDED / RUNNING**. Final readback at **2026-11-06T23:43:26.2217223Z**
	confirmed the same app and deployment IDs. Six requested OBO scopes, no
	attached resources and one minimum/maximum instance were preserved.
- Fresh authenticated proxy health: HTTP 200 in **4.446 seconds**,
	`databricks_obo`, `deployment_allowed=false`. HTML, readiness JS/CSS, lineage
	JS/CSS, next-action JS and app CSS each returned 200 and matched current local
	bytes and SHA-256.
- Selected project `84b03a652f5e4371ba1c72d401acba6b`, package
	`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`:
	`/api/readiness?limit=1` returned 200 in **48.657 seconds**, exact selected
	scope, 243 structural workflows, zero current assessed/draft/receipt counts,
	unknown executable/runtime/parity counts and a BLOCKED release gate with
	zero verified business workflows. `/api/readiness/lineage?limit=1` returned
	200 in **23.227 seconds**, exact selected scope, complete canonical inventory
	but semantic lineage and physical bindings unverified. Both explicitly deny
	execution and deployment. These are individual samples, not latency guarantees.
- No signed-in browser interaction was verified; proxy/API and asset checks do
	not establish visual acceptance or browser OBO consent behavior. Offline tests
	do not prove live business execution, independent output parity or production
	readiness. The prior failed acknowledgement and reconciliation history above
	remain intact and were not replayed. External staging and compile scratch were
	removed. No Git metadata or push was available or requested.