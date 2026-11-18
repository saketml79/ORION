# Incremental writer metadata staging, 2026-11-12

## Root fix

File staging alone left the entire typed metadata manifest in one model response.
The new primary protocol stages files and strict metadata separately and calls
`commit_generation_receipts()` last, with no arguments. The server assembles every
explicit row. It does not derive ownership, obligations, translations or provenance
from code, filenames, prose, or historical artifacts.

- Actual current types are `TaskDraft`, `Obligation`, `TranslationItem`,
  `StagedArtifactReference` and `GenerationArtifactSet`, not `GenerationTask`.
- `stage_generation_task(task)` accepts one strict `TaskDraft`. Inline obligations
  are supported. Alternatively use `obligations:[]` and call
  `stage_generation_obligation(task_id, item)` for each category.
- `stage_generation_translation(item)` accepts one strict translation item. Large
  inventories use disjoint small items with explicit canonical record keys, no
  omitted-summary success. Every task's canonical inventory must be accounted for.
- `stage_generation_artifact_binding(relative_path, task_ids, evidence_ids,
  content_sha256)` binds an existing exact staged file receipt. A body revision
  invalidates the old binding until the model explicitly revises it.
- `stage_generation_basis(input_data_basis, behavior_basis)` authors both provenance
  labels explicitly. Version/hash authority is pinned from the exact server record.
- Metadata receipts hash the invocation ID, exact provenance envelope, row key and
  typed payload. Changed rows require `replace_sha256` matching the old metadata
  `receipt_sha256`. Identical restaging is not progress. Source-record identity
  partitions are immutable; revise the existing item's content, not overlapping keys.
- `inspect_generation_receipts(offset)` returns paged inventory under 2KB, including
  exact hashes and a whole-state hash. It never repeatedly dumps a 174-record list.
- Missing tasks, obligations, bindings, provenance, source pages, unfinished chunks,
  stale file hashes or translation coverage reject commit with actionable missing
  fields and explicit total/list-complete markers. Only identical bad state after
  corrective feedback stops as protocol no-progress, not timeout.
- The new empty commit argument object is valid. The old empty-finalizer guard
  applies only to the legacy finalizer, not the new commit tool.
- Assembly revalidates the strict full artifact set, explicit translation ownership
  and complete inventory, then uses the existing `_validate` and authority gates.
  Blocked metadata cannot fabricate drafted completion. All execute/deploy/approval
  flags remain false. Exact validated UTF-8 bytes and commit-last manifest are retained.
- Legacy full-set submission and metadata-only finalization remain compatible.
  The writer instructions make incremental staging plus commit the primary path.

## Exact historical evidence, read-only

Public run `740e131bb2bf426fb18bd6194b716439` remains `partial`. Its writer
`0c6041c832794bf99dcf16597716d0b3` remains blocked with
`writer_protocol_no_progress`. Five incomplete audit files retain exactly 47,704
bytes: four notebooks and one test. No metadata coverage was accepted.

The checkpoint contains 13 distinct legacy-finalizer calls with `{}` arguments,
11 with `finish_reason=length`. Observed output-token counts include 8,192.
This verifies the failure counters, not a successful historical recovery.

Actual counter evidence comprises 174 distinct task-owned canonical record keys.
The direct checker validates all 174 as separate, explicitly blocked citation-check
items in fresh in-memory staging only. It does not recover the old run, author its
missing semantic metadata, or claim business translation:

- Session source-query key:
  `42ef3ef9d9f47b605648ebed4935045e19e0b8b522b8ac622660fdb920d685a5:property:0`.
  Its canonical citation is
  `42ef3ef9d9f47b605648ebed4935045e19e0b8b522b8ac622660fdb920d685a5`.
  Exact captured query selects `18300 AS template_id, 1 as COUNTER` from
  `AE2E.AE2E_JOB_DEP_CHK_COUNT`. This is not the distinct mapping-default query
  selecting template 18537 from DUAL.
- Post-SQL key:
  `c99c1ac31ceca569b17d23e8dba9d2af1d98b3082725badef58a14a64f93afa7:property:1`.
  Its same-prefix canonical citation owns the captured template-18300 insert,
  partition-scoped delete with `60 <= (select count(1) ...)`, and commit. The
  60-row reset threshold is not the separate workflow condition threshold of 35.

The original public record, manifest and checkpoint hashes are checked unchanged.
Preserved audit bodies are independently checked against their existing hashes.

## Direct checks and boundaries

Direct command: `.venv/Scripts/python.exe -B -m tests.check_spark_codegen_metadata`.
This also invokes the existing direct staging checker, not pytest or a test suite.
Sockets are prohibited. Compiled model responses are scripted local fakes.

The checker covers four task roles, four saved notebooks and one test, 32 individual
obligations, 64 individual translation items, candidate/reviewed authority, exact
file hashes, native Jobs condition and explicit true/false branches, missing metadata
refusal and recovery, schema-field feedback, explicit revisions and invocation
isolation, stale/forged bindings, final reciprocal-ownership rejection, paged
inventory, plus the existing cancellation, truncation and compatibility checks.

Final result: `FINAL_DIRECT_METADATA_CHECKS=PASS`, exit 0. All 24 direct scenarios
passed, including the optional exact historical snapshot present in this checkout.
Each clean compiled four-role run used 125 scripted model calls with no elapsed
cutoff. The real counter's 174 receipt entries occupied 20 inventory pages, every
page under 2KB, without omissions. Python AST and trailing-whitespace checks passed
for all three changed Python files. No new Pylance errors remain; the writer retains
the same six pre-existing authority/graph/config/deadline diagnostics as baseline.
The new checker has unused-argument hints only.

Temporary artifacts are cleaned. No cloud, live inference, service restart,
deployment, generated-code execution, historical mutation, or original recovery.
This is protocol/static validation, not original-business parity proof.

## Parent-owned restart helper

No restart is performed by this change. After any active run is finished or the
user explicitly authorizes stopping it:

1. Stop the existing **Run local modernization workbench** task.
2. Restart that same task from VS Code, using the existing virtual environment,
   port 8000 and existing tracing environment. Do not start a second listener.
3. Start a fresh explicitly consented writer run. Do not resume the old writer
   checkpoint or synthesize missing metadata from preserved notebooks.

The process already in memory does not gain these new tools without a reload.
An incomplete-only draft-preview API/UI can be handled separately by the parent.
Existing `staged_drafts` remain audit drafts, never accepted artifacts.