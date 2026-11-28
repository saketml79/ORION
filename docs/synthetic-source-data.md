# Additive synthetic source fixture population

## Scope and entry points

- [Planner and explicit SDK helper](../workbench/synthetic_source_data.py): `build_plan()`, `create_local_plan()`, `deploy_run()` and `main()`.
- [Separate XML metadata extraction](../workbench/synthetic_source_metadata.py): `extract_metadata()`. Existing `plan_sources()` and its six-part namespace are unchanged.
- [Standalone serverless runtime](../workbench/synthetic_source_runtime.py): `validate_plan()`, `insert_statement()`, `run()` and `run_file()`.
- [Offline regression tests](../workbench/tests/test_synthetic_source_data.py).

The target is exclusively `infotodbxmig_src_dev_ssuman`, original package SHA256
`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`, with
583 SOURCE fixture tables. The CLI rejects a different package or table count.
The deploy helper independently rebuilds the entire plan from the original XML
and compares metadata, namespaces, schemas and generated values before any cloud
operation. The plan includes its canonical JSON hash, and the notebook receives
that hash as a Job parameter.

No agents, app, redesign, Spark codewriter, frontend, fixture provisioning or
existing Job implementation is modified. No cloud deployment/run is part of
local planning, imports or tests.

## CLI usage for a later explicit operator action

Local planning: invoke Python module `workbench.synthetic_source_data` with
`--plan`. Optional planning flags are `--xml`, `--rows-per-table` (1..1000,
default 10), `--anchor-date` (default 2026-01-01) and
`--require-serializable`. No authentication occurs on this path.

Explicit cloud action: invoke the same module with `--deploy-run PLAN`, where
PLAN is the generated plan JSON path. `--xml` must identify the same original
package. `--timeout-minutes` defaults to 180 and is bounded to 1..1440. Generation
and isolation settings are taken from the reviewed plan, not silently overridden
by deployment flags. There is no catalog, alternate profile, credential, cluster
or existing Job option. Do not invoke this action during local implementation.

Each planning invocation creates a unique child of the local output/workbench
synthetic-source-data directory containing the JSON plan and reviewable
conditional INSERT statements. Each explicit deployment creates a separate
unique deployment child containing create/run acknowledgements, a final
deployment receipt and, when available, the population receipt. The helper
raises on failed, partial, unconfirmed or missing-receipt outcomes.

## SDK and serverless contract

SDK 0.157.0 signatures were inspected locally. The cloud helper uses only
`profile_identity("labs")`, verifies `current_user.me()` again, and creates assets
under that user's personal Workspace directory with a fresh UUID.

The API sequence is:

1. `workspace.mkdirs(unique_directory)`.
2. `workspace.upload(..., format=AUTO, overwrite=False)` for the standalone
  runtime Python file and JSON plan. Direct AUTO downloads must match uploaded bytes.
3. `workspace.upload(..., format=SOURCE, language=PYTHON, overwrite=False)`
   for one plain Python notebook SOURCE export. This is not an ipynb document.
4. `jobs.create(...)` with one `NotebookTask(source=WORKSPACE)`, explicit
   `JobRunAs(user_name=current_user.user_name)`, `max_concurrent_runs=1`, task
   `max_retries=0`, scoped tags and `performance_target=STANDARD`.
5. `jobs.run_now(job_id=..., idempotency_token=unique_UUID)` and the SDK waiter's
   `result(timeout=...)`.
6. `workspace.list()` and `workspace.download(..., format=AUTO)` recover the final
   population receipt or latest immutable checkpoint even after a failed run.

`WorkspaceClient.workspace` uses the SDK's `WorkspaceExt` mixin, not just the
lower-level service API. The complete pretty-printed local plan is 14,563,880
bytes. Even compact JSON exceeds the base64 `import_` content limit when encoded.
The verified multipart `upload()` path sends binary content instead, without
dropping metadata or splitting the plan. Headerless runtime Python and JSON use
AUTO file imports; the notebook alone uses SOURCE. Direct `download()` avoids
base64 export limits too. Offline tests exercise the actual installed mixin's
multipart request shape, in addition to deployment protocol doubles.

STANDARD is appropriate for non-time-critical batch generation. There are no
cluster fields, SparkContext/RDD/cache APIs, libraries, DBFS-root paths, mounts,
Spark configuration overrides or serverless Python-script environment guesses.
The notebook uses absolute Workspace paths and stdlib driver file access. No SDK
authentication is constructed inside the notebook: its Spark principal must
match the explicit Job run-as user. Runtime Workspace file writes require
supported serverless Workspace filesystem access and are subject to asynchronous
Workspace flush behavior. The helper verifies exported receipts before reporting
completion. There is no classic-compute or alternate-identity fallback.

The parent databricks-core skill was absent in this installation. Authentication
facts were instead read from the existing tested `profile_identity()` helper,
and API facts from installed SDK signatures and offline contract tests.

## Evidence and synthetic values

- The authoritative namespace and ordered columns come from original
  `plan_sources()`. No saved/redacted IR namespace or guessed owner substitution
  is used.
- XML SOURCEFIELD key hints are extracted separately. They are hints, not enforced
  constraints.
- Original CONNECTOR endpoints and equality predicates are traced through only
  unambiguous same-port/bare-column passthroughs. Computed expressions, ambiguous
  origins, SQL overrides that change lineage and unresolved shortcuts are
  withheld. Cross-name key aliases are recorded with confidence 0.8.
- Same-name ID/key domains have confidence 0.35. Joined aliases share one small
  bounded numeric domain across compatible numeric/string representations.
  Narrow fractional-only domains use zero; incompatible temporal/binary members
  are reported as unsupported. Repeated keys and synthetic cardinalities are
  possible. These are not established relationships or foreign keys.
- Captured SELECT comparison/IN literals supply fitting status/flag/type/code
  examples. Oversized or incompatible literals are withheld, never truncated
  into a different claimed SQL literal. Parameters and ambiguous columns are not
  assigned status values. Raw SQL, Java and credentials are not retained.
- General values are deterministic, bounded by original character widths and
  mapped decimal precision/scale, with fixed timestamp anchors, bounded integers
  and binary values. Nullable credential-like columns receive NULL; required
  credential-like fields receive only synthetic placeholders.
- WIL counter rows use actual SELECT literal aliases from original session
  overrides, including template IDs 18299, 18300, 18301 and 18302 and counter 1.
  They do not simulate the session's pre/post-SQL or thresholds. In particular,
  its SELECT constant applies to every input counter-table row, not just rows
  matching a template ID. No 34/35 success or parent-child parity is claimed.
- Java dependency settings capture the actual table/column tokens, literal
  wait/sleep settings and unresolved non-secret parameter names. The query's
  ae2e_template and ae2e_template_job_dependency tables are absent from the SOURCE
  fixture inventory. They remain unresolved. No additional tables, dependency
  edges, successful logs or external JDBC bindings are fabricated.

## Empty-only safety and concurrency boundary

The runtime verifies all planned tables before its first INSERT: Delta format,
table ID, fixture marker `orion.fixture=xml_source_schema_only`, exact package
marker, ordered exact column names/types and nullability. It repeats metadata
validation immediately before each write and rejects changed table IDs/isolation.
Any mismatch fails the Job and persists a partial receipt. It never creates,
alters, overwrites, truncates or deletes existing UC objects.

Populated tables are skipped with their observed count preserved. An initially
empty table receives exactly one self-reading `INSERT INTO ... SELECT ... FROM
VALUES ... WHERE NOT EXISTS (SELECT 1 FROM the_same_table)`. Generated SQL
contains validated identifiers, escaped literals and explicit type casts, never
source SQL, Java or post-SQL execution. Failed writes are not retried by this
module. SDK transport behavior is not a distributed exactly-once guarantee.

Existing Delta `delta.isolationLevel=Serializable` is honored using Delta's
optimistic conflict handling. There is no supported session override asserted
here, and no table property is changed. A plan with `--require-serializable`
fails before writing if any existing fixture lacks that property.

Without verified Serializable isolation and controlled external writers, this
implementation cannot prove an atomic global empty-only guarantee. The
conditional INSERT sees a statement snapshot; unrelated writers and metadata
changes may race the preflight or commit. Job concurrency limits serialize only
this Job, not other Jobs or clients. There are no atomic guarantees across 583
tables, no distributed lock and no automatic rollback of successful earlier
inserts. Operators requiring stronger guarantees must independently serialize
all writers or use the strict existing-Serializable gate before invocation.

## Machine-readable receipts

The runtime writes a start receipt, immutable numbered checkpoints before each
insert and after each completed/skipped table, and a final receipt in its unique
Workspace directory. The local helper retrieves them using the labs client.
An invalid JSON plan receives a safe final failure receipt too. If driver/file
access or export fails, available checkpoints and the local deployment receipt
remain evidence, not a successful population claim.

Every planned table has `count_before`, `count_after`, `skipped`, `inserted`,
`status`, `errors`, `commit_uncertain`, and verified table/isolation metadata when
available. Unprocessed counts and unavailable INSERT metrics are JSON null.
`inserted` uses SQL DML affected-row metrics when returned, never a fabricated
count-after minus count-before attribution. Counts are observations and can be
affected by concurrent writers. An acknowledgement/count failure after a write
retains commit uncertainty. Errors are fixed safe codes or exception class names,
not upstream exception strings or secrets.

Completion requires the final receipt to match the plan/catalog, receipt status
completed and the SDK Job run state SUCCESS. Failed runs remain failed even if a
receipt file exists. If waiting times out, the Job may still be running: do not
submit another Job until the recorded Job/run IDs are inspected by the operator.
No cloud assets or existing rows are automatically cleaned up or deleted.

## Verification

Final consolidated local regression: **125 passed, 0 failures, 0 errors, 0 skips**,
24.775 seconds, process exit 0. The exact invocation was Python module `pytest`
with these arguments:

- `workbench/tests/test_synthetic_source_data.py`
- `workbench/tests/test_source_fixtures.py`
- `tests/test_fixture_metadata.py`
- `workbench/tests/test_databricks_models.py`
- `-q --disable-warnings`
- `--junitxml=output/workbench/synthetic-source-data/8e7033beea3548739f0f413ce52a2962/regression.xml`

Artifacts:
[local plan](../output/workbench/synthetic-source-data/8e7033beea3548739f0f413ce52a2962/plan.json),
[conditional INSERT SQL](../output/workbench/synthetic-source-data/8e7033beea3548739f0f413ce52a2962/conditional-inserts.sql),
[JUnit receipt](../output/workbench/synthetic-source-data/8e7033beea3548739f0f413ce52a2962/regression.xml),
[local implementation receipt](../output/workbench/synthetic-source-data/8e7033beea3548739f0f413ce52a2962/implementation-receipt.json).

The local plan hash is
`53a7880b35a8643d2b9a41b4992c2ec0c508ef230f92ff5973e6063c447a4dc8`.
It contains 583 tables, 136 schemas, 27,908 columns and 5,826 proposed rows (582
tables with 10 rows plus 4 captured counter rows). There are 3 conservatively
connector-traced equality relationships, 1,351 hinted ID domains and 2 captured
Java dependency-setting records. All 583 INSERT statements independently parse
as INSERTs with self-read EXISTS predicates in SQLGlot's Databricks dialect.
Python AST checks pass for all four added Python files. The three production
modules have no Pylance diagnostics; test-only unused-parameter hints remain.

**No cloud authentication, deployment, Job run or table mutation was performed.**
Offline protocol doubles test the guards and SDK shapes; they do not prove Spark
execution, live Delta isolation, UC permissions, Workspace filesystem persistence,
performance or relationship/business parity. This is not a full application-suite
regression or a live table-population receipt.

References:
[serverless Jobs](https://docs.databricks.com/aws/en/jobs/run-serverless-jobs),
[Workspace file interactions](https://docs.databricks.com/aws/en/files/workspace-interact),
[Delta isolation](https://docs.databricks.com/aws/en/optimizations/isolation/isolation-levels).

## GitHub release coverage and issue #52

The source-generation and target-completion scripts were already packaged in the
app release. Packaging did not execute full target provisioning or verify the
separate source-seeding Job. The read-only audit found all 583 canonical source
tables but only 21/20/20 of 307 expected targets in bronze/silver/gold. It also
verified a previously successful source-seeding Job receipt covering all 583
tables and 5,826 observed rows. That receipt is historical, not a current row-count
or business-parity certification.

The [release gate](../deployment/check_fixture_deployment.py) now checks the
entire canonical source/target inventory, ordered schemas, fixture provenance,
managed Delta format and ownership before app activation. It also verifies the
existing source Job's exact definition, owner/Run-as, plan checksum, notebook
generator and runtime bytes against the
[reviewed fingerprint](../deployment/source-seed-deployment-fingerprint.json).
The deployed runtime has CRLF bytes; its exact fingerprint is checked without
normalizing the remote artifact or modifying the working Job.

Missing fixtures or drift fail the release. The gate never creates tables,
loads data, executes/resets/recreates Jobs, changes grants or deploys the app.
Additive target setup uses the existing
[one-attempt owner operator](../workbench/complete_target_fixtures.py), with a
fresh offline `--plan`, its exact plan SHA, and explicit checksum-bound
`CREATE MISSING TARGET FIXTURES <SHA>` consent for `--apply`. A claimed plan
must not be rerun after an uncertain CREATE; inspect its durable journal first.
These remain empty structural fixtures, not approved semantic destination bindings.

Before using the revised GitHub workflow, separately approve the deployment
principal's required **read-only** metadata, source-Job and private Workspace
artifact access. Do not add grants implicitly or grant data-row/write access.
After approval, retain the canonical target plan in an owner-controlled private
Workspace file and configure protected-environment secrets
`ORION_FIXTURE_PLAN_PATH` and `ORION_SOURCE_SEED_JOB_ID`, plus the reviewed
`ORION_FIXTURE_PLAN_SHA` variable. Private plan contents must not enter the public
repository, logs or Actions artifacts. Missing configuration/access holds the
release rather than silently skipping verification. Existing exact-SHA and
active-work-replacement approvals remain mandatory.