# Package-aware source understanding, 2026-11-04

## Subsequent authorized saved-analysis rebuild

The separate performance/enrichment workstream now provides an explicit versioned
Java-evidence rebuild for existing immutable saved artifacts, rather than claiming
that reuploading the same source hash updates them. Canonical files and historical
review/approval records are preserved. See
[the implementation, actual saved-artifact proof and release handoff](hosted-scoped-read-analysis-rebuild.md).
This is not parent/child ETL conversion or output parity, and it has not been
activated in the user's live state or deployed by that workstream.

## Scoped package migration-context improvement

### Final follow-up verification, 2026-11-04

This record supersedes the open-failure counts in the earlier validation history
below. No deployment, local-server restart, model inference, business Job, source
query or fixture provisioning was performed.

- Corrected the actual proposal selection race: both successful and failed
  server-paged reads recheck the selected workflow before applying a response.
  A changed filter causes one fresh scoped read; a cleared selection cannot
  resurrect the previous candidate. Existing epoch/scenario/ticket guards remain.
- Updated the strict selection fixture to model the current server contract:
  workflow/scenario filtering, default 20-row pages, totals and separate detail
  GETs. Tests check exact read counts, paging, empty selections and late 200/503
  responses. Unexpected requests and page errors still fail teardown.
- Repaired the isolated UI-uplift harness by loading actual production progress
  DOM declarations rather than incomplete handwritten declarations. No production
  loading behavior or isolation gate was relaxed.
- Added Explorer and Proposals checks for stale context successes/errors and
  selection clearing, command-only zero-source fixture presentation, and no
  automatic child metadata reads. Source-only amber assessments display neutral
  “Not assessed”, never “Scoped checks passed”; actual canonical red stays red.
  Real fixture observations still retain their unapproved binding evidence.

**Final consolidated command:** the ten-file offline pytest command in “Scoped
validation command” below, unchanged, with no deselection or skip additions.
Result: **199 passed, 1 failed, 0 teardown errors, 0 skipped, 6 subtests passed,
2 warnings, 478.74 seconds**. Both modified UI scripts also passed node --check;
all four edited Python tests passed AST parsing and editor diagnostics were clean.
All package-context, fixture metadata, source evidence, package graph, redesign
UI, agents, UI uplift, capability and proposal-selection tests passed.

The sole remaining exact test is
[test_session_mapping_closures_and_connection_is_not_schema](../workbench/tests/test_migration_planning.py#L107):
its unresolved_references assertion expects 1 and receives 0. This same failure
was reproduced once using the immediate pre-change canonical parser snapshot
from VS Code history (jVgW.py, 2026-11-04T21:32:41.230Z), loaded exclusively into
an in-memory module, against the unchanged current test. That snapshot already
preserves literal connection aliases. An older October staged parser passed,
so this is specifically pre-existing relative to this package-context work,
not proof that all earlier revisions failed. No parser, connection behavior,
assertion or security gate was modified to hide it. The consolidated suite is
therefore not fully green and this is not a shipment-readiness claim.

**Live local endpoint:** a bounded read of the MASTER_PREP migration-context URL
on the existing port 8000 backend returned HTTP 404. The new route is verified
by actual TestClient tests, not confirmed live. OpenAPI inspection was unavailable.
Main must restart the local backend and reload the browser before verifying the
new endpoint; no process was stopped here. Hosted deployment remains untouched.

This completes the scoped deterministic package-understanding follow-up, not a
general compiler, validated physical binding, package equivalence, or output
data parity implementation. Those limits and separate approval gates remain.

The product objective is migration of the ETL package while preserving output data,
not conversion of an unrelated single workflow. This change adds a deterministic,
read-only migration contract and presentation, not a general compiler.

- [Package context](../workbench/package_context.py) derives exact invocation
  candidates from [canonical command evidence](../workbench/source_evidence.py).
  The contract version is package-migration-context-v1. Canonical schema, IDs,
  source data, saved artifact hashes, and parser version remain unchanged.
- The selected-workflow direct source/target scope, outward transitive child
  scopes, and connected caller/child/sibling migration cohort are separate.
  The cohort means **review these invocation boundaries together**, not merge
  everything into one Job, execute every workflow, or declare them converted.
  Shared mappings/names never establish cohort membership or source readiness.
- Traversal uses all captured unique literal candidates, not the 100-node graph
  page. Responses page every collection (default 40, maximum 200), expose total,
  offset, next_offset and truncation, and report cycles over the entire captured
  cohort. These are invocation cycles, not an enforced completion DAG.
- Missing literal children, ambiguous candidates, parameterized commands and
  unsupported shell forms remain external review items. Ambiguous inward callers
  are reported but do not connect multiple candidate workflows into a fake cohort.
  Runtime Integration Service routing remains unverified even for exact candidates.
- Exact complete exported source/target identities can report **potential** data
  dependencies and shared writes within the cohort. Physical binding, ordering,
  source readiness, SQL/Java/procedure effects and external writers remain unknown.
  These observations never become canonical dependency edges.
- [Explorer](../workbench/static/app.js) and
  [Proposals](../workbench/static/redesign-review.js) share one renderer for
  Convert together, Keep external explicitly, blockers and human questions.
  Readable member/call lists and technical evidence are collapsed. Package risk
  remains not assessed. Source-only graph assessments are neutral, not safe/green
  and not an amber risk assessment; actual scoped canonical errors remain red.
- [Fixture observations](../workbench/fixture_metadata.py) exclude called workflow
  boundaries. Empty direct scope reports no_direct_sources; unresolved scope with
  no source definitions reports no_resolved_sources. Neither implies verified
  fixtures. Child/group counts are XML metadata only; no child UC/source data is
  automatically queried. A child must be explicitly selected for its own bounded
  fixture metadata request. Identity, raw statuses and full columns remain in
  collapsed technical observation evidence rather than the primary result.
- [Agent tools](../workbench/agents.py) expose package_migration_contract and
  instruct every role to page closure and external dispositions before making
  completeness claims. This tool does not authorize unrelated implementation
  retrieval; existing role-local invocation retrieval still controls child access.
  Successful workflow draft submission explicitly reports package_candidate_complete
  false. This is a prompt/tool boundary, not proof that a live model will comply
  or a new automatic package-equivalence validator. Existing approval gates remain.

### Measured actual case, existing immutable artifact

Read-only inspection of the live local Explorer selected
wkf_LOAD_AE2E_MASTER_PREP, canonical ID
ac197951c36607afcda14ce4b2adfa4c7dbc789461304980c0e6996a4095e0e1.
This browser session initially had no object selected, so this is a deliberate
inspection of the reported master case, not a claim to have recovered another
browser tab's private selection. The old object API reported 52 direct task
instances, 60 control links, zero mapping references and zero source/target
definitions. The missing sources are explained by the direct closure boundary,
not by verified missing UC tables.

The new contract was measured against the existing saved Lumen artifact, without
reparsing or overwriting it:

| Scope | Captured result |
|---|---|
| Master direct definitions | 0 sources, 0 targets, 0 mappings |
| Master literal outward calls | 19, all nowait |
| Connected invocation cohort | 32 workflows, 31 launch sites, 0 invocation cycles |
| Cohort definition uses | 175 source uses, 198 target uses, shared definitions may repeat |
| WIL_NET_DIS wrapper direct scope | 1 source, 1 target, 1 mapping |
| WIL network-disconnect child direct scope | 4 sources, 8 targets, 6 mappings |
| Literal callers of that child | wrapper and master, both nowait |

Saved-model load plus context construction and three contract evaluations took
6.738 seconds in this local run. This is not an HTTP latency SLA. The existing
artifact also contains unresolved canonical references, for example 54 in the
child's definition closure; this count is not a claim that 54 sources are missing.
Zero unresolved literal child calls in this cohort does not establish source
bindings, external scheduler coverage, Java behavior, or output equivalence.

### Databricks completion-trigger research

Official documentation checked on 2026-11-04:
[Trigger jobs when upstream jobs complete](https://docs.databricks.com/aws/en/jobs/job-completion-triggers)
and [Job schedules and triggers](https://docs.databricks.com/aws/en/jobs/triggers),
both marked updated 2026-10-29.

- Job Completion Triggers are Beta and must be enabled in every participating
  workspace. Workspaces must share a Unity Catalog metastore. Configuring the
  downstream requires CAN MANAGE; its Run as identity needs upstream workspace
  access and CAN VIEW or higher on monitored jobs.
- One trigger monitors 1 to 10 upstream jobs using one result filter. With
  multiple jobs, **all** must contribute a new matching completion; repeated
  completions from one job contribute its latest matching completion.
- Completions are not matched by business date or batch. Historical completions
  do not initialize runs. Ready completions are retained at max concurrency.
  Succeeded includes SUCCESS_WITH_FAILURES, which needs explicit business review.
- Any-of behavior requires separate triggers and the Multiple Triggers Beta.
  Bundle configuration requires a CLI schema supporting the feature. Availability
  and permissions in the user's workspace were not queried or changed.

This may be an architecture option for an **evidenced completion** boundary. It
does not preserve an asynchronous launch by itself, and does not replace the
Java query's dependency-table membership, run-date predicate, end_time test,
timeout/error contract, or the wrapper's row-count branching. No trigger schema,
Job configuration, schedule or deployment was generated by this change.

### Scoped validation command

Application changes were completed before the consolidated regression batch.
JavaScript syntax checks used `node --check` for both modified UI scripts.
The consolidated offline command is recorded here for reproducibility:

```powershell
.\.venv\Scripts\python.exe -B -m pytest workbench/tests/test_package_context.py tests/test_fixture_metadata.py workbench/tests/test_source_evidence.py workbench/tests/test_package_graph.py workbench/tests/test_redesign_ui.py workbench/tests/test_agents.py workbench/tests/test_migration_planning.py workbench/tests/test_ui_uplift.py workbench/tests/test_capability_status.py tests/test_proposal_selection_sync.py -o addopts= -q --disable-warnings -ra
```

An initial attempt stopped during collection because the newly extended offline
renderer harness had an over-indented block. That test-only indentation was
corrected before the executable batch. Strict existing UI mocks also require an
explicit response for the newly added read-only migration-context endpoint;
selection/capability fixtures intentionally return 404 for this unavailable
contract rather than manufacturing canonical package evidence.

The new module's Pylance document diagnostics are empty. Pylance reports existing
type issues outside the changed agent tool blocks: reportGeneralTypeIssues in
dictionary schema unpacking and reportArgumentType in schema construction and
message-content JSON loading. No project-wide type-clean claim is made.

**Executed results:** JavaScript syntax checks passed. The consolidated batch
finished with **165 passed, 21 failed, 18 teardown errors, 6 subtests passed,
1 warning, 770.24 seconds**. All new package-context tests, fixture metadata,
source-evidence, package-graph, redesign-UI and agent tests passed in this batch.
It is not a clean regression-suite result.

Failure classification:

- The already documented migration-planning connection assertion still expects
  unresolved_references=1 but receives 0. No connection-resolution behavior was
  changed to satisfy it.
- Five existing UI-uplift harness tests fail with ReferenceError for
  overviewProgress, which their extracted render function depends on.
- Two capability teardowns and sixteen selection-sync teardowns rejected the
  newly added migration-context GET. Strict mocks now return an explicit 404 for
  unavailable canonical context. This fixes test contract coverage, not product
  capabilities or evidence.
- The selection fixture also omitted the existing selected-proposal detail GET;
  an exact candidate response was added without changing production control flow.

Only the two corrected UI fixtures were rerun, not the broad suite:

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/test_proposal_selection_sync.py workbench/tests/test_capability_status.py -o addopts= -q --disable-warnings -ra
```

Result: **21 passed, 8 failed, 0 teardown errors, 1 warning, 94.97 seconds**.
All capability tests passed, including neutral source-only graph selection,
keyboard behavior and failed-selection retry. The remaining eight selection-sync
assertions concern pagination/empty-selection expectations, including a fixture
that returns all 65 candidates without the server's paged total/offset/limit
contract while expecting 50 cards. Product selection/paging behavior was not
rewritten to work around that mismatch. These failures remain open, alongside
the connection assertion and five UI-uplift harness failures. No clean full-suite
or deployment-readiness claim is made. Application code was unchanged after the
consolidated batch; subsequent edits were test mocks and this evidence record only.

The running local backend was inspected before the change, not restarted or
replaced. New UI/backend behavior is verified through offline Chromium and actual
TestClient routes, not claimed as deployed or live-server verified. Reloading only
the browser against an old backend can show the explicit unavailable-context
message until that backend is restarted. No source/child metadata or model call
is used as a fallback.

## Scope and evidence boundaries

Read-only inspection of the local export followed by additive source-evidence fixes.
No deployment, business Job, source query, DDL, or provider inference was run.
The saved-preference agentic-blueprint documents/PDF are absent from this checkout.
The available [proposal-first design](proposal-first-deepagents-lakeflow-plan.md)
requires evidence-driven, scoped unknowns and separate approval gates.

## First load and charts are different paths

- [Service._ingest](../workbench/app.py#L784) hashes/parses the source, persists
  canonical understanding and structural English, and activates the package.
  It does not invoke agents or generate a semantically translated execution plan.
  If the same hash already exists, it loads the immutable stored artifact rather
  than replacing it with the freshly parsed model. These fixes do not rewrite history.
- [analyze_package](../workbench/understanding.py#L101) retains containment,
  task-definition, session-mapping, instance/shortcut references, exact
  WORKFLOWLINK control conditions, data flows and field flows. Resolution is
  case-sensitive and scoped. It does not parse pmcmd into canonical dependency edges.
- [package_graph](../workbench/package_graph.py#L66) is a paged, same-kind
  structural view, at most 100 nodes and 2,000 edges. It shows direct canonical
  same-kind relations, exact definition-reference paths and undirected shared
  mappings. Shared definitions are not runtime dependencies. Cycle results are
  page-scoped, or unknown outside the page, not proof of an estate-wide DAG.
- [cycle_report](../workbench/understanding.py#L81) measures SCCs of captured
  data_flow/workflow_control edges only. A Java loop is not a WORKFLOWLINK cycle.
- The separate legacy [build_cross_workflow_dependencies](../orchestration/run.py#L150)
  scans command labels/attribute strings for case-insensitive wkf_ names with a
  global name map. Its [master generator](../orchestration/run.py#L187) turns called
  names into caller depends_on entries. This is inference, not exact pmcmd parsing,
  qualified repository resolution, or preservation of asynchronous launch semantics.
  Legacy estate diagrams also show shared-session relations. Do not equate those
  charts or old output artifacts with the canonical workbench graph or a valid Job.

## Exact WIL_NET_DIS paths

Repository PCREPO_PRD1_01, folder B_AE2E:

1. Wrapper [wkf_AE2E_DUMMY_CALL_WIL_NET_DIS](../input/LumenExample.XML#L255991):
   Start -> five-minute relative timer -> s_m_AE2E_JOB_CHECK_COUNTER.
   The [four exact links](../input/LumenExample.XML#L256074-L256077) branch on
   TgtSuccessRows >=35 to eml_on_SUCCESS and <35 to the command task.
   The [timer](../input/LumenExample.XML#L256005-L256009) is one wait per invocation.
   There is no exported back edge in this wrapper. Its description does not
   establish repeated polling or why five minutes is operationally necessary.
2. The [command](../input/LumenExample.XML#L256003) literally invokes
   wkf_Load_AE2E_WILTEL_NETWORK_DISCO with -f B_AE2E and -nowait. Authentication
   variable names are not credential values. A successful command does not prove
   child completion. The Integration Service's runtime repository routing is external.
3. There is also a direct upstream caller
   [wkf_LOAD_AE2E_MASTER_PREP](../input/LumenExample.XML#L270332), via
   dec_UNITY_NET_DIS_AOB_PREP -> tmr_UNITY_NET_DIS -> cmd_WILTEL_NETWORK_DISCO.
   The [control links](../input/LumenExample.XML#L270609) and
   [timer-to-command link](../input/LumenExample.XML#L270641) are actual XML edges.
   Its [command](../input/LumenExample.XML#L270490-L270493) also launches the same
   child with -nowait, not the dummy wrapper. No pmcmd caller of the dummy wrapper
   was found in the export. External schedulers/manual callers remain unknown.
4. The child [workflow](../input/LumenExample.XML#L243606) has 15 direct task
   instances, including Start and four decisions. Its
   [Start -> s_m_Check_Dependency_Status_WIL_ND](../input/LumenExample.XML#L243660)
   references reusable session s_m_Check_Dependency_Status, which references
   [m_Check_Dependency_Status](../input/LumenExample.XML#L76340).
   The [successful-status link](../input/LumenExample.XML#L243649) gates ONM log
   checking. Subsequent ONM -> OPENCI -> ODW -> BILLTRACK stages use log-check
   TgtSuccessRows branches and decisions, not an unconditional list of data loads.

The new invocation evidence reports both literal callers without adding canonical
edges. Matching uses the caller's export repository and exact literal folder/name.
Ambiguous, missing, parameterized and unsupported shell forms remain unresolved.
These are scoped candidates, not verified remote execution bindings.

## Embedded Java: actual query and complete active control block

The [Java transformation](../input/LumenExample.XML#L76234) is exported as
TYPE="Custom Transformation", TEMPLATENAME="Java Transformation". Its method lives
in [OnInputRow_Method_Snippet](../input/LumenExample.XML#L76277), not a Sql Query
property. The XML attribute encodes the entire method on one source line.

The following is the complete active method body after its introductory example
comments. Connection/identity setup is intentionally omitted, not recoverable
from this report. All remaining query/control/cleanup behavior is shown:

```java
OracleDataSource ods = null;
// [Connection declaration omitted]
Statement stmt = null;
ResultSet rs = null;
try {
    // [Connection/identity initialization omitted]
    stmt = conn.createStatement();
    String sqlStr = null;
    sqlStr = "select count(1) from (select depends_on_template_id from " + SCHEMA + ".ae2e_template_job_dependency where template_id = (SELECT template_id FROM " + SCHEMA + ".ae2e_template where template_name = '" + TEMPLATE_NAME + "') minus select template_id from " + SCHEMA + ".ae2e_unix_job_log where run_date = NVL (TO_DATE('" + JOB_RUN_DATE + "','MMDDYYYY'), TRUNC(SYSDATE)) and end_time is not null)";
    System.out.println("My sql: " + sqlStr);
    int total = 0, timeoutcnt = 0;
    do {
        rs = stmt.executeQuery(sqlStr);
        while (rs.next()) {
            // get total
            total = rs.getInt(1);
            System.out.println("Total record: " + total);
        }
        // timeout after looping MAX_WAIT_COUNT
        if (total > 0) {
            System.out.println("Sleep 5 mins");
            Thread.sleep(300000);
            timeoutcnt++;
            if (timeoutcnt == MAX_WAIT_COUNT) {
                break;
            }
        }
    } while (total > 0);
    out_VALUE = total;
    rs.close();
    stmt.close();
} catch (Exception e){
    e.printStackTrace();
    out_VALUE = 1;
} finally {
    // cleanup
}
```

Facts, not the stale transformation description:

- The query counts configured dependencies minus templates with a non-null
  end_time for the selected run date. It does not test a success-status column.
  Template selection, dependency rows and parameter-file values are external data.
- [exp_Define_Param.MAX_WAIT_COUNT](../input/LumenExample.XML#L76285) is the
  literal 54, wired to Java by [this connector](../input/LumenExample.XML#L76319).
  The description says 30 trials, which disagrees with the captured expression.
- It queries immediately. If total >0, it sleeps 300,000 ms, increments timeoutcnt,
  and breaks on equality with MAX_WAIT_COUNT. Persistent nonzero total therefore
  gives 54 queries and 54 sleeps, totaling 270 minutes of intentional sleep, plus
  unbounded database/connect overhead. There is no final query after the last sleep.
  This is not a hard wall-clock timeout. For a hypothetical zero/negative bound,
  equality would not produce the intended bounded loop, but the inspected binding is 54.
- Zero total returns success without sleeping. Timeout returns the remaining count,
  not necessarily 1. Exceptions set out_VALUE=1. The finally block contains only a
  comment; the connection is not explicitly closed there. Earlier ResultSets are
  not explicitly closed per iteration. No corrective runtime behavior is invented.
- [exp_CheckJavaReturn](../input/LumenExample.XML#L76229-L76231) evaluates
  IIF(in_VALUE > 0, ERROR('Max Wait Time Exceeded'), 'Good'). The
  [connector](../input/LumenExample.XML#L76317) supplies the Java output.
  The [session](../input/LumenExample.XML#L76340) has Stop on errors=1.

## SQL, overrides and procedure boundaries

- The dependency mapping's source qualifier is SELECT '1' FROM DUAL. That alone
  is not its implementation. The Java query reads ae2e_template_job_dependency,
  ae2e_template and ae2e_unix_job_log under the supplied SCHEMA expression.
- Counter [mapping](../input/LumenExample.XML#L253295) defaults to a DUAL query
  with template_id=18537. The wrapper's session
  [query override](../input/LumenExample.XML#L256019) instead selects the constant
  18300,1 from AE2E.AE2E_JOB_DEP_CHK_COUNT, without a WHERE clause. TgtSuccessRows
  is therefore not a SELECT COUNT scalar, nor necessarily a template-filtered count.
- Its [Post SQL](../input/LumenExample.XML#L256015) inserts (18300,1), deletes
  partition AE2E_JOB_DEP_CHK_COUNT_18300 when its count is >=60, then commits.
  The branch uses the session's loaded-row count, not a post-SQL recount. This
  Oracle partition/transaction logic is not equivalent to a generic Spark append.
- Child mappings contain source and lookup queries, session-level log Post SQL,
  sequence use and Source Pre Load references to AE2E_COMMON.TRUNCATE_TABLE.
  Procedure names/type/ports are exported; a procedure implementation was not
  found in the inspected mapping evidence. Do not manufacture a TRUNCATE replacement,
  grants, exception contract or complete schema DDL from the procedure name.
- [compile_sql_artifacts](../workbench/sql_artifacts.py#L360) inventories both
  session and mapping properties by SQL role, retains overrides separately, and
  transpiles only accepted statements through SQLGlot. It explicitly blocks full
  mapping/control implementation. Java string assignments are not SQL property
  phases. Parameterized, procedural, sequence, partition and transaction behavior
  still needs explicit review; syntax success is not semantic parity.
- [Spark writer evidence](../workbench/spark_codegen.py#L265) follows approved
  task closures, excludes other workflow boundaries, and pages actual fields,
  properties and edges. Its SQL list is property-name/role driven. Its `_sql`
  function statically checks generated SQL, it is not a Java-to-Spark translator.
  Supplemental Java is now available through source-property retrieval, not
  falsely promoted into a standalone executable SQL phase.

## Root gap and implemented additions

1. The canonical parser already retained METADATAEXTENSION, unlike the separate
   legacy scanner. Its inline JDBC guard redacted the entire OnInputRow VALUE,
   losing the surrounding polling/query evidence. Existing VALUE behavior and
   canonical object IDs are preserved. Fresh parsing now adds embedded_code with
   a structural Java view, original hash/size/line counts and separately qualified
   dynamic SQL assignment expressions. Comments/literals and connection-sensitive
   statements are removed from the structural view. Unsupported lexical forms fail
   closed. It is not compilable Java or a generic Java converter.
2. Planning behavior retrieval formerly filtered by property names such as SQL,
   query, wait and condition. OnInputRow_Method_Snippet did not match. New
   embedded_code pages and behavior inclusion expose this evidence explicitly.
3. Existing package_neighbors measures shared definitions/fingerprints, not inbound
   callers or pmcmd targets. New invocations pages inspect strict literal command
   syntax and report candidates without changing graph closure or chart semantics.
4. Per-role related_workflow_id access requires that role to retrieve invocations
   first. Only the exact returned caller/candidate workflows can then be paged.
   Evidence becomes citable only after retrieval by that role. Arbitrary unrelated
   workflow access and cross-role citation laundering remain rejected.
5. Semantics and Spark writer procedures explicitly require Java/control review.
   The SQL compiler reports EMBEDDED_JAVA_REQUIRES_REVIEW for affected owners,
   rather than pretending that the surrounding mapping's small SQL query is enough.

This verifies availability through offline tools/tests, not that a live model will
choose every required tool, produce a correct plan, or translate the Java. No live
agent run was performed. Existing persisted packages must be deliberately rebuilt
under an approved versioning workflow to gain the new parser sidecar. Re-uploading
the same hash alone is not proof of re-analysis, because immutable artifacts win.

## Regression verification

Final offline command, executed after all application patches:

```powershell
.\.venv\Scripts\python.exe -B -m pytest workbench/tests/test_source_evidence.py workbench/tests/test_understanding.py workbench/tests/test_qualified_resolution.py workbench/tests/test_migration_planning.py workbench/tests/test_package_graph.py workbench/tests/test_runtime_planning.py workbench/tests/test_agents.py workbench/tests/test_explanations.py workbench/tests/test_demo_conversion.py tests/test_sql_artifacts.py tests/test_spark_codegen.py -k "not test_session_mapping_closures_and_connection_is_not_schema" -o addopts= -q --disable-warnings -ra
```

Result: **222 passed, 1 deselected, 3 warnings, 6 subtests passed**, 401.11 seconds.
This includes the real Lumen parse, scoped caller resolution, Java evidence,
role-local tool access, security fixtures, SQL blockers, writer source retrieval,
unchanged canonical graph tests and the pinned isolated-demo scope fingerprint.

The initial batch included the deselected test and failed its assertion that
unresolved_references must equal 1, actual 0. Reconstructing the original parser
without the new Java-sidecar block in memory reproduced the same failure.
The fixture's external connection is not an unresolved node under existing parser
behavior. That unrelated test and connection behavior were left unchanged.
This is targeted verification, not a clean full-workspace-suite claim. Editor
diagnostics for the changed Python files reported no errors. Dynamic decorated
agent tool compatibility is verified by offline tool invocation tests, not by
Pylance call-site enumeration, which found no statically registered callers.