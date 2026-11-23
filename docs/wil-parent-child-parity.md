# WIL_NET_DIS parent and child: implementation and remaining parity boundaries

Date: 2026-11-04. Local implementation only. No deployment, business Jobs,
remote data query, credentials, model inference, hosted analysis activation or
server restart was performed.

## Implemented, not a full conversion claim

- [Dependency compiler](../workbench/dependency_compiler.py) validates the complete
  canonical mapping closure against one reviewed fingerprint, including original
  Java evidence. It emits a real parameterized Spark SQL/polling Python module,
  not a fake child, stubbed production query or a model-generated sketch.
- [Selected-unit contract](../workbench/package_parity.py) inventories the parent
  and outward child, not all 32 workflows in the connected caller cohort. It
  preserves every session use, mapping reference, instance/session overrides,
  field/connector evidence, SQL phases and scoped parameter references separately.
- [Wrapper compiler](../workbench/workflow_artifacts.py) adds a negative package
  completeness gate inside existing validation metadata. Its four supported
  wrappers, tasks, artifact names and generation flow remain available unchanged.
- [Agent tool and result contract](../workbench/agents.py) exposes the selected-unit
  gate, caches it for paging, and returns deterministic
  `package_candidate_complete=false` and `output_parity=not_established`.
  Prompt instructions forbid upgrading partial core coverage into a complete
  mapping/session or package claim. This is not a guarantee about free-form model
  prose or an authorization to generate/deploy anything.

Hosting, saved-analysis rebuild, UI, parser, Spark writer and SQL compiler runtime
files were not changed by this workstream. The required agentic-blueprint folder
and PDF are absent from this checkout. The available
[proposal-first design](proposal-first-deepagents-lakeflow-plan.md) and
[source investigation](package-source-understanding-investigation.md) were read.

## Persistent artifacts actually produced

The explicit local export created a new directory without overwriting existing
artifacts or modifying active saved package state:

- [Readiness report and complete evidence snapshot](../output/workbench/wil-net-dis-parent-child-parity-v1/parity_readiness.json).
- [Generated dependency-check module](../output/workbench/wil-net-dis-parent-child-parity-v1/modules/dependency_check.py).
- [Dependency binding/semantics contract](../output/workbench/wil-net-dis-parent-child-parity-v1/dependency_contract.json).
- [SHA-256 and byte-length manifest](../output/workbench/wil-net-dis-parent-child-parity-v1/manifest.json).

All three manifest entries were re-read and verified after export. The generated
module passed AST parsing and the tests execute the same generated source. The
manifest describes immutable artifact bytes, not a runtime or parity certificate.

Measured report contents:

| Item | Captured result |
|---|---|
| Migration unit | Selected WIL_NET_DIS parent plus WILTEL_NETWORK_DISCO child |
| Session uses | 10 total: 1 parent counter, 9 child uses |
| Unique mappings | 7 total: parent counter plus 6 child mappings |
| Child source/target definitions, fresh parse | 9 sources, 8 targets |
| Source parameter-file declarations | 13, including retained override-owner declarations |
| Scoped parameter inventory rows | 1,313, including declarations, references and session properties |
| SQL/query/override/call property rows | 79, including empty captured values |
| Evidence snapshot | 2,221 nodes, 6,942 internal edges |
| Fully converted sessions | 0 |
| Dependency implementation | Pinned query/polling/error core candidate only |
| Package conversion gate | blocked |
| Output parity | not established |

The fresh source count differs from the historical immutable artifact's reported
four sources. Fresh canonical definitions include AE2E_AM_OFFNET_MGR_PREP,
AE2E_ODW_PREP, WILTEL_OFFNET_MGR_INVENTORY, DUAL, AE2E_UNITY_ODW_STG,
WILTEL_OPENCI_STG, AE2E_JOB_LOG, AE2E_UNIX_JOB_LOG and AE2E_BTP_STG3.
These are exported definition counts, not nine verified physical live sources.
Neither historical state nor canonical IDs were overwritten to reconcile counts.

The child's four log-check session definitions all use the same mapping but have
distinct session identities/overrides. They are four obligations, not one converted
session. The four loads, dependency check and log check are six unique mappings.
The parent adds the seventh. The report retains exact session and mapping IDs.

## Dependency core: preserved semantics and limits

Only `m_Check_Dependency_Status` with the reviewed canonical definition closure is
accepted. Scope fingerprint:
`d6396df9601a2f8a5160c9b284d77062ed91fa2bd4dfb2433d32ecc936816150`.
Original OnInputRow Java SHA-256:
`440b5d8e78c4c8bdb300cab35bd59ff40b0f1561cfbb2b718923d61402140c49`.
The pin includes other Java snippets, fields, expressions, properties and edges.
Changed code, changed 54 binding, missing sidecars and changed edges fail closed.
This does not certify arbitrary Java, old redacted saved artifacts or every
future enriched artifact version. Those require deliberate evidence rebuild/review.

- Query immediately, never sleep before the first query.
- Nonzero count: sleep 300 seconds, increment, break on equality with 54.
- Persistent nonzero: exactly 54 queries and 54 sleeps, 16,200 seconds of intended
  sleep plus database overhead. No query after the last sleep. Not a wall-clock cap.
- Zero: return without another sleep. Timeout preserves the last remaining count.
- Query/validation/sleep exception: Java-equivalent output 1, followed by typed
  `DependencyQueryError`. Nonzero timeout raises `DependencyTimeout`, preserving
  the last count and trace counters. Both carry the exact exported error message
  `Max Wait Time Exceeded`. They do not return a successful boolean/string result.
- The SQL uses `EXCEPT DISTINCT`, not an anti-join or `EXCEPT ALL`. Duplicate
  dependencies collapse; NULL set membership follows set-operator semantics.
- The template selection remains a scalar subquery: no matching template gives
  NULL, multiple matching rows must raise a database error, never silently select
  one or multiply rows through a join.
- No success-status filter is invented: completion is `end_time IS NOT NULL`.
- Run-date equality is preserved on the log column, with no truncation of that
  column. Explicit MMDDYYYY input becomes a bound ISO date. Explicit empty string
  is the supported Oracle empty-string NULL case and uses `current_date()` on
  every query. Invalid dates error rather than silently defaulting to today.
- Tables are explicit validated three-part identifiers; template/date values use
  Spark SQL named parameter binding. No credential, catalog, host or schema is
  invented, and no JDBC connection code is copied into generated artifacts.

The owner must verify Oracle-compatible clock/timezone, numeric ID and DATE/time
types, collation, scalar cardinality errors, and source freshness. The selected
strict date syntax intentionally does not claim every permissive Oracle TO_DATE
spelling. Non-midnight timestamps must not become matches through truncation.
SQL parsing and offline algebra checks are not execution on Spark or Oracle.

This core does **not** implement the mapping's target writes, session SQL phases,
Stop on errors/reject-file behavior, connection setup/cleanup, restarts or child
routing. Typed Python task failure is a candidate realization of ERROR semantics,
not evidence of an entire Informatica session's transactional failure parity.

## Remaining work, split by code versus owner evidence

| Boundary | Exact remaining requirement |
|---|---|
| Four child loads | Convert their actual SQL, expressions, lookup/join/router graphs, procedures, sequence use, writes and session overrides. Exact transformation types and IDs are in the report. Nothing is marked fully converted. |
| Four log-check uses | Implement the shared mapping for each distinct scoped session override and its loaded-row-count result. |
| Dependency session | Integrate the tested core with its real source/target writes, session error/recovery contract and approved bindings. |
| Child workflow | Preserve successful-status gating, ONM/OPENCI/ODW/BILLTRACK decision branches, email and failure/restart behavior. Not an unconditional list of four loads. |
| AE2E_COMMON.TRUNCATE_TABLE | Exact package body and overloads are not captured in the inspected transformation definitions. Four Source Pre Load calls retain qualified name, exact arguments and ports. No TRUNCATE replacement is invented. |
| AE2E_COMMON.GET_PRODUCT_KEY_ID | Additional reusable procedure definition is in the selected closure. Its exact body, expression calls, key allocation, exception and transaction behavior need review. No sequence/function replacement is invented. |
| Parameter files | Resolved sections/precedence for the exported p_ae2e and p_ae2e_prep references, including per-instance values. File path evidence is not file contents. Supply sanitized values and secret references, not credentials. |
| Parent counter | Oracle partition membership, commit interval/rollback, concurrent writers and partial replay are not equivalent to separate Delta commits. Existing wrapper generation remains a candidate only. |
| Runtime bindings | Owner-approved table/type/clock/freshness and source/target bindings, plus child Job and operational side-effect contracts. Empty fixtures are not real source data. |
| Output parity | Paired actual outputs from the same source snapshots/date/parameters: multiset row comparison, duplicates/NULLs, values/types, rejects/counts, procedure/sequence/log effects and recovery/concurrency cases, with independent receipts. |

The MASTER_PREP caller remains an **external review boundary**. Both it and the
dummy wrapper launch the child with `-nowait`. This contract does not execute
the master, merge all connected workflows, make submission wait for completion,
or schedule the same child a second time. External schedulers remain unknown.

The negative gate cannot be cleared merely by having wrapper artifacts, marking a
dependency unit test successful, providing empty fixtures, or supplying a procedure
name without its behavior. Owner evidence is necessary but does not itself finish
the unimplemented load/log/session/control compiler work.

## Regression evidence

[Tests](../workbench/tests/test_package_parity.py) execute generated code with a
virtual sleep and independent literal Java interpreter. They cover all 54 success
positions, every query and sleep exception position, timeouts with multiple
remaining counts and a hypothetical zero on poll 55 which must never be observed.
Set algebra uses an independent Python Oracle-MINUS oracle and SQLite executing
the candidate query with explicit dialect shims, including 120 deterministic
random duplicate/NULL cases, wrong dates, non-midnight values and missing templates.
SQLite's scalar-subquery cardinality behavior is explicitly **not** used to claim
Spark/Oracle coverage; the error path is tested with a driver exception instead.
Agent tool tests invoke the actual compiled tool without any model call and
forbid socket connections. Existing wrapper generation/API tests remain intact.

Executed final regression command:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_package_parity.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_package_context.py workbench/tests/test_source_evidence.py workbench/tests/test_agents.py tests/test_spark_codegen.py tests/test_spark_codegen_api.py -q --tb=short --disable-warnings -ra
```

Result: **442 passed, 1 skipped, 6 subtests passed, 1 warning, 425.04 seconds**.
The existing skipped Spark API test requires Windows symlink permission. No
deselections or test expectation changes were made to existing regression files.
New compiler modules have empty Pylance document diagnostics. Editor diagnostics
were clean for the changed agent/wrapper modules and new tests.

An initial new-test-only batch had 178 passes and four failures. These were traced
to SQLite CURRENT_DATE syntax, selecting the first Java snippet rather than the
OnInputRow snippet, and reusing a historical source-count expectation. The test
adapter/selection and fresh evidence expectation were corrected, with the final
combined batch above passing. Production query/polling code was not weakened.

Additional adjacent command:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini tests/test_sql_artifacts.py workbench/tests/test_demo_conversion.py workbench/tests/test_runtime_planning.py -q --tb=short --disable-warnings -ra
```

Result: **70 passed, 53.26 seconds**. Across both final batches: **512 passed,
1 skipped, 6 subtests passed**. No application code changed after these batches.

The broader project's previously documented migration-planning unresolved-reference
assertion and other unrelated historical failures are not represented as fixed by
this batch. This is not a clean whole-repository suite or live deployment claim.

Explicit export and hash verification also passed. No temporary instrumentation,
test-only production branch, server, staging directory or external process was
left running. The persistent candidate is intentional evidence, not scratch output.