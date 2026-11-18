# Delta metadata adapter and explicit migration

**Current implementation and reported migration evidence, 2026-10-30.**

## Scope and status

The additive adapter in [workbench/delta_state.py](../workbench/delta_state.py)
stores actual metadata rows in 21 user-owned UC Delta tables. It does not merely
publish SQLite snapshots. SQLite backups are separate recovery artifacts.

The historical local-state migration has now been reported **VERIFIED** with
the receipt and inventory below. Its captured SQLite backups are archive/recovery
artifacts, not the live hosted state. Separately, the deployed
`orion-migration-workbench` integrates this adapter through
[delta_runtime.py](../workbench/delta_runtime.py): hosted SQLite is ephemeral and
rebuilt from committed Delta source columns plus schema-only artifacts, not
restored from archive backups. See [hosted-delta-authority.md](hosted-delta-authority.md).

An offline migration receipt is not a committed hosted-generation receipt.
The archive is not automatically adopted, promoted, or merged into the active
hosted estate. No review approval, release/deployment authority, distributed
concurrency control, or standalone remote LangGraph checkpointer is implied.
This documentation update performs no migration, deployment or remote mutation.

## Verified historical import, 2026-10-30

Reported receipt:
`3fbacd46df986b9075212553c3792fe9d059ee98f4522fa5d599d954be3e66c5`.
The recorded overall result is **5,764 metadata rows, 1,404 immutable objects,
100 captured files and 300,651,539 object bytes**, with one separately appended
verified migration receipt. These are historical archive metrics, not counts of
the current active hosted generation or fresh XML analysis.

The supplied per-table inventory is retained explicitly:

| Delta table | Reported imported rows |
| --- | ---: |
| `projects` | 3 |
| `packages` | 12 |
| `jobs` | 10 |
| `proposal_versions` | 1,980 |
| `review_events` | 1,040 |
| `documents` | 16 |
| `settings` | 14 |
| `agent_runs` | 7 |
| `agent_findings` | 4 |
| `agent_usage` | 500 |
| `agent_checkpoints` | 1,425 |
| `audit_events` | 650 |
| `artifact_manifest` | 100 |
| `model_routes` | 1 |
| `agent_memory` | 0 |
| `loop_runs` | 0 |
| `loop_attempts` | 0 |
| `loop_events` | 0 |
| `loop_lessons` | 0 |
| `loop_comparisons` | 0 |
| `migration_receipts` | 1, appended after validation |

**Count reconciliation caveat:** the supplied non-receipt per-table figures sum
to 5,762, two fewer than the reported overall 5,764. Including the receipt gives
5,763. This documentation-only review has not independently reconciled that
reporting discrepancy against the stored plan/receipt; neither missing rows nor
a different table allocation should be invented. The VERIFIED result is retained
as reported evidence, not a fresh count certification.

The formerly missing redesign database mapping is implemented:
`redesign_versions` maps to `proposal_versions` and `redesign_reviews` to
`review_events` in [the adapter](../workbench/delta_state.py). Raw source paths,
table names and keys preserve provenance; do not treat these as a separate
unrecorded redesign family or silently discard them.

The active hosted project `84b03a652f5e4371ba1c72d401acba6b` comes from a fresh
XML upload, not the old generated output or an automatically merged archive.
Its full analysis hit a proxy timeout, with committed completion verified later.
The separate fixture catalog `infotodbxmig_src_dev_ssuman` contains the reported
583 empty source tables across 136 schemas (27,908 columns), not archive rows,
populated source data or demonstrated live source connectivity.

## Preconditions

- For an explicit archive migration, use a verified human OAuth identity through
  the configured CLI/SDK profile (historically `labs`). A profile name alone does
  not establish credential type. Hosted requests instead resolve fresh forwarded
  OAuth OBO credentials, not PATs or an ambient profile/SP fallback.
  Credentials stay in the SDK/CLI authentication mechanism, never command flags,
  output, migration plans, or stored client configuration.
- The authenticated user's catalog, `workbench` schema, and `artifacts` Volume
  must already exist and be owned by that exact human principal. This CLI does
  not create them or change ownership. Group ownership is deliberately rejected.
- The SQL warehouse must grant that user permission to execute statements. The
  existing approved example is `e3a0fc2c08db05bb`, catalog `orion_dev_ssuman`.
  The adapter/CLI require explicit warehouse and catalog arguments, with no
  engine warehouse default. Only the hosted staging helper supplies that existing
  warehouse as a configurable default; it does not create one.
- Stop the local and hosted workbench workers while migrating. SQLite backup is
  transactionally consistent per database, not an atomic estate-wide snapshot.
  Active WAL contents are included through SQLite's backup API. WAL/SHM/lock
  files are not copied as standalone artifacts.
- Review legacy input for secrets. Recognizable credential fields, bearer tokens,
  PATs, JWTs and private keys fail closed. Detection is defense in depth, not a
  guarantee against arbitrary unlabeled or compressed historical secrets.
  SQLite free pages can contain deleted historical values. Only migrate trusted,
  reviewed local state. No ephemeral credentials are collected by this tool.

## Commands

Run from the repository root in PowerShell only for a separately authorized
archive operation. These are reference commands, not commands run for this
documentation update, and not a way to activate the historical archive in hosted
state. Supply the actual verified human profile and existing namespace.

```powershell
# Offline, read-only snapshot inventory. No SDK client or remote requests.
& .\.venv\Scripts\python.exe -B -m workbench.delta_state inventory --source output/workbench

# Explicit creation of missing Delta tables only, in existing owned resources.
& .\.venv\Scripts\python.exe -B -m workbench.delta_state ensure --profile labs --warehouse e3a0fc2c08db05bb --catalog orion_dev_ssuman --schema workbench

# Create a temporary SQLite-consistent copy, import rows, upload artifacts,
# validate live rows, then append a verified migration receipt.
& .\.venv\Scripts\python.exe -B -m workbench.delta_state migrate --source output/workbench --profile labs --warehouse e3a0fc2c08db05bb --catalog orion_dev_ssuman --schema workbench --volume artifacts

# Use the returned plan.sha256, not migration_id, as the plan checksum.
& .\.venv\Scripts\python.exe -B -m workbench.delta_state validate --plan-sha256 REPLACE_WITH_PLAN_SHA256 --profile labs --warehouse e3a0fc2c08db05bb --catalog orion_dev_ssuman --schema workbench --volume artifacts

# Recover the original backup files to a NEW, EMPTY directory.
& .\.venv\Scripts\python.exe -B -m workbench.delta_state export --plan-sha256 REPLACE_WITH_PLAN_SHA256 --destination C:/Temp/workbench-recovery --profile labs --warehouse e3a0fc2c08db05bb --catalog orion_dev_ssuman --schema workbench --volume artifacts
```

Every command returns exit code 0 on success or 1 on operational failure. Failures
are intentionally generic so SDK error text cannot echo SQL values, credentials,
or signed URLs. Inspect workspace query history through an authorized session
for diagnostics. Argument syntax errors use argparse's conventional exit code 2.
Only `inventory` is offline. `ensure` and `migrate` are explicit remote mutations.
`validate` reads remote Delta and Volume data. `export` reads remote objects and
writes only to the explicitly selected empty local destination.

## Table inventory and source mapping

| Delta table | Imported source |
| --- | --- |
| `projects` | Project registry project rows |
| `packages` | Registry membership rows and package manifest JSON |
| `jobs` | Registry jobs and per-context job JSON |
| `proposal_versions` | Every SQLite proposal version, redesign version and draft proposal JSON element |
| `review_events` | Every review database event and redesign review, including registration/document events, with original `kind` |
| `documents` | Package document rows and project document JSON |
| `settings` | SQLite settings and remaining JSON metadata, including active pointers and fixture receipts |
| `agent_runs` | Agent ledger run rows and per-context agent-run JSON |
| `agent_findings` | Agent ledger findings |
| `agent_memory` | Agent ledger memory rows |
| `agent_usage` | Agent ledger usage events |
| `agent_checkpoints` | Every user table row in each checkpoint database, including writes/migrations when present |
| `loop_runs` | Loop runs |
| `loop_attempts` | Loop attempts |
| `loop_events` | Loop events |
| `loop_lessons` | Loop lessons |
| `loop_comparisons` | Loop comparisons |
| `audit_events` | Original audit payload, sequence, previous hash and event hash |
| `artifact_manifest` | Archive: every captured file, including independently restorable SQLite backups; hosted runtime: active artifacts with SQLite references replaced by DDL only |
| `model_routes` | Identity-scoped route entries, only for the authenticated workspace/user |
| `migration_receipts` | Verified migration plan reference and per-table live counts |

All user tables in recognized SQLite databases are enumerated. Unknown tables,
unknown database families, or tables without stable primary keys fail closed.
SQLite internal tables are excluded from metadata rows but preserved in backups.
The source registry, global legacy context, project-wide documents, and every
project/package child context are scanned independently. Repeated copies of the
same package are **not deduplicated across project contexts or source paths**.
This preserves history rather than guessing which legacy copy should win.

## Namespace and row contract

Every table has a typed, non-null envelope:

- Strings: `workspace_id` (normalized workspace origin), `user_id` (resolved
  workspace user ID), `project_id`, `package_hash`, `context_id`, `record_id`,
  `content_hash`, `migration_id`, `source_path`, `source_table`, `source_key`.
- Queryable string keys: `entity_id`, `workflow_id`, `proposal_id`, `run_id`,
  `role`, `kind`, `status`, `actor`, `created_at`.
- Typed `BIGINT`: `version`, `sequence`, `byte_size`.
- Artifact keys: `artifact_path`, `artifact_sha256`.
- `payload`: canonical JSON **string**, queryable with Databricks JSON functions.
  It preserves every original SQLite column and separately parsed `*_decoded`
  JSON values when parsing succeeds. Empty strings and zero represent absent
  optional query keys; original nulls remain null inside the JSON payload.

Legacy global state uses project `__legacy__`; absent packages use `__none__`.
`context_id` distinguishes global, project, and project/package contexts. Raw
agent namespaces and identity-directory names remain in payload/source paths.
They are not assertions of authenticated ownership or verified memory scope.

`actor` is copied from the original record. It is never rewritten to the current
OAuth user. An imported `actor_authenticated` value is preserved as provenance,
not newly verified. A migration receipt explicitly says legacy actors were not
authenticated by migration. Gates and approvals are not promoted or cleared.

### Identity and append semantics

- `record_id`: SHA-256 of table, workspace, user, project, package context,
  source path, source table, and original primary key (or JSON element key).
- `content_hash`: SHA-256 of the canonical typed envelope, excluding the hash
  itself and migration ID. Includes the payload and artifact references.
- `migration_id`: SHA-256 of the versioned complete plan with file hashes and
  expected typed-row identities/hashes.
- `MERGE` matches the full namespace, record identity, content hash and migration
  ID. It only has `WHEN NOT MATCHED THEN INSERT`. No update or destructive SQL.
  Same-plan retries are idempotent. Changed content appends revisions. Re-imports
  under a different plan can repeat unchanged content with a new migration ID.
- Source path intentionally distinguishes SQLite rows from JSON draft records.
  Consumers must not treat both as separate effective proposals without an
  explicit application-level version/source policy.

Existing table ownership, Delta format, exact column types and nullability are
verified. An incompatible existing table is an error, never altered or replaced.
SQL identifiers use a strict allowlist. All values use typed Statement API
parameters, including source strings that contain SQL syntax. Writes batch up
to 40 rows or approximately 512 KiB of values per statement. Reads return at
most 50 rows with explicit exact project/package/context and user/workspace
predicates and a `(record_id, content_hash, migration_id)` keyset cursor.

## Artifacts and validation

Artifacts live below the same catalog/schema's user-owned Volume:

`/Volumes/<catalog>/<schema>/<volume>/delta-state/users/<workspace-user-hash>/blobs/<sha256>`

Large source XML, compressed understanding, evidence files, SQLite backups and
binary checkpoint cells are immutable, content-addressed objects. Text values
over 8 KiB are replaced by `$artifact` references. Large nested JSON structures
are externalized as canonical JSON; no metadata payload exceeds 48 KiB. Small
query keys remain typed columns. The file manifest preserves original byte
checksums independently of canonical row hashes. Checkpoints are never unpickled.

Preflight scans and stages all logical records locally before remote table
creation or uploads. Temporary copies are outside the source root and cleaned
up by the CLI. Hidden entries and WAL/SHM/lock sidecars are excluded. Symlinks
fail rather than being followed. JSON parsing is bounded at 64 MiB per metadata
file. Larger JSON metadata currently fails explicitly; XML/gzip/other binary
artifacts are not subject to that JSON parsing bound. File reads and remote
artifact verification currently materialize one whole file at a time.

A plan contains expected row identities/hashes for every metadata table,
including tables with zero rows. Independent validation re-queries Delta using
bounded pages, recomputes every typed row's content hash, compares exact identity
sets, and separately counts all rows for that user/workspace/migration across
all scopes. Extra scopes, missing rows, duplicates, or payload/key corruption
fail validation. The plan inventories both original file objects and externalized
payload objects. The separate `validate` command downloads and checks every one
of these objects. A receipt is appended only after row validation and is
itself re-read and hash checked before returning.

Uploads may leave immutable orphan objects if a later request fails. Remote
tables can contain partial migrations without a receipt. No automatic cleanup
deletes anything. Retry the same stopped-source migration; do not infer success
from artifact presence or a partial row count.

Migration uploads use at most eight concurrent workers in batches of 64 objects.
Exact principal, workspace, catalog/schema ownership and Volume ownership are
checked before and after each batch, without retaining authorization across
actions. The fixed client and workspace are pinned throughout each batch and
the service authorizes every request. Every object is independently downloaded
and checksum checked before any metadata writes. Existing immutable objects
are checked as well, never overwritten. The plan is also downloaded and checked.
Retries first download an existing immutable object and verify its size and
checksum. Only SDK `NotFound` permits a new upload. A concurrent create still
uses `overwrite=False` and verifies the `AlreadyExists` result. This avoids
re-sending large existing bodies through a gateway that can reset the PUT
connection rather than return a conflict.

Databricks MERGE sources place column aliases on the inner VALUES relation:
`USING (SELECT * FROM VALUES (...) AS source_rows(...)) AS s`. Placing column
aliases directly on the MERGE source alias is rejected with
`COLUMN_ALIASES_NOT_ALLOWED` (SQLSTATE `42601`). The insertion and full-namespace
matching contract is unchanged.

Optional `progress` callbacks on `migrate` and `validate_live` receive bounded
stage records, table names/counts, hashes, byte sizes and fixed blob categories.
Upload failures include only allowlisted exception categories and SDK codes,
never raw provider messages, URLs, source payloads or credentials. SQL failures
retain only these same sanitized categories and codes. Progress is opt-in and
does not change the default CLI JSON contract.

## Adapter interface and implemented hosted integration

Construct `DeltaStateStore(client, warehouse, catalog, schema, user_id)` using
the **fresh requesting human's** client. The adapter verifies exact principal,
catalog/schema/table ownership and workspace before reads/writes. Do not retain
the client across users or use an app service-principal fallback.

- `ensure_tables() -> list[str]`: explicit table initialization only.
- `make_record(table, *, project_id, package_hash, context_id, source_path,
  source_table, source_key, payload, **typed_keys) -> dict`: build canonical
  records; callers externalize large payloads first.
- `upsert_records(table, records, *, migration_id) -> int`: submitted unique
  revision count, not an unverified inserted-row count.
- `read_table(table, *, project_id, package_hash, context_id, migration_id=None,
  after=None, limit=50) -> list[dict]`: bounded scoped rows with payload strings.
- `migration_count(table, migration_id) -> int`: exact-user live count.
- `VolumeArtifacts(store, volume).put(bytes) -> dict`: immutable Volume reference.
  `get(sha256, max_bytes=...) -> bytes`: scoped checksum-verified download.
- `snapshot_local(source_root, destination) -> Snapshot`: local, read-only
  source capture. `migrate(store, snapshot, artifacts) -> dict`: plan reference,
  migration ID and verified table counts. `validate_live(...)` is independently
  callable. `restore_snapshot(artifacts, plan, destination)` exports backup files.

The adapter supplies primitives, not the domain Store API. The implemented
[hosted runtime](../workbench/delta_runtime.py) now provides complete-generation
selection, original-column reconstruction, immutable artifact dereferencing and
commit-last visibility. Application handlers can use local SQLite only as their
disposable execution cache; Delta rows, not periodic SQLite backups, are the
authority. Mutations independently verify content inventory and parent, append
the final receipt, and read it back before acknowledgement.

Observational GET/HEAD/OPTIONS do not rewrite whole content generations. They
restore authority and its separate HTTP-audit tail, append exact new audit
columns to Delta, and independently replay/read back the persisted chain before
responding. Action-local ownership memoization does not cache credentials or
authorization across requests/users. The dispatcher serializes each user's
stateful requests in a queue, not routine overlap 409 responses; default capacity
allows two executing users and returns 429 on capacity exhaustion. Domain 409
conflicts remain possible. One process/replica is still required, without a
distributed lock or CAS.

Hosted Volume metadata uses [the SQL wrapper](../workbench/hosted_volumes.py)
under the same requesting user's identity because Apps rejected `unity-catalog`
and `catalog.volumes`. The accepted requested scopes are `catalog.catalogs`,
`catalog.schemas`, `catalog.tables`, `files`, `model-serving` and `sql`. Files
supplies Volume blob bytes. This path does not grant warehouse/UC permissions
or introduce another principal. The standalone migration CLI uses its explicitly
configured SDK client; it does not automatically install the hosted SQL wrapper.

Delta does not enforce the logical uniqueness key used here. Serialize writers
and migrations per user until a tested concurrency protocol is added. There is
no cross-table transaction. Recovery export reconstructs the captured SQLite
backup files, **not later Delta edits**. It must not be used as evidence that the
archive is live hosted authority. The migrate result's
`hosted_authority_integrated: false` describes that import's non-activation, not
the absence of the separately implemented hosted Delta runtime.

Operational readiness remains limited: reported full overview latency was
76.7 seconds; the main UI helper budgets GET reads at 110 seconds and mutations
at 30 seconds. The 120-second Apps proxy limit can leave long-action outcomes
ambiguous even if a generation commits later. Verify committed job/project state
before retrying, not automatic rollback or independent durable-worker completion.
Real OBO health HTTP 200 is verified evidence, not general artifact-generation,
deployment authority or a production-readiness guarantee.

## Offline regression

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
try {
  & .\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_delta_state.py -q --tb=short -p no:cacheprovider
} finally {
  Remove-Item Env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
}
```

Tests use real SDK statement response/parameter classes and a mocked Statement
Execution client, with real SQLite fixtures for every persistence family,
multiple identical packages in different contexts, WAL backup, checkpoint BLOBs,
raw legacy actors, exact counts, hash tampering, SQL safety and backup recovery.
They do not prove live warehouse permissions or deployment readiness.
This test description is not a claim that the current entire regression suite
is green. Historical full-suite runs had failures, as recorded in
[the authority verification history](hosted-delta-authority.md#historical-regression-results-2026-10-29).