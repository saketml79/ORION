# WIL_NET_DIS source compiler v2

Local implementation, 2026-11-05. No deployment, business query, procedure call,
Job execution, model inference or table creation is authorized or performed.
The earlier [v1 readiness report](../output/workbench/wil-net-dis-parent-child-parity-v1/parity_readiness.json)
and canonical source remain unchanged. This work is additive and does not modify
tested wrapper, parser, agent, hosting, UI or state paths.

## Implemented code versus runtime completeness

The separate [parent/child API](../workbench/wil_parent_child.py) uses the existing
dependency compiler and wrapper generator. It checks the package receipt and
complete canonical unit fingerprint before accepting a candidate:

- Package: `08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.
- Unit: `eae9d6f0f419dd13ba6da85c4444459b983c9b8e234a1720ecfb521a1a2dce44`.
- Scope: selected parent plus exact outward child, not MASTER_PREP or all callers.
- Child: 15 task instances, 9 session uses, four loads, four log checks and the
  Java dependency session. Parent adds one counter session. Seven unique mappings.

[Mapping compilation](../workbench/wil_mapping_compiler.py) implements scoped
source-query precedence, positional connected SQ ports, named scalar arguments,
explicit whole-table binding tokens, sequential local/output expressions, normal
two-stream joins, filters, independent router outputs, numeric equality-key ANY
lookups and explicit connected target projections/INSERT candidates. Original
fields, types, default/error metadata, properties and owner IDs remain inspectable.
No target value is padded with a guessed NULL. Unknown functions fail closed.

Connected lookup outputs are fused with their original row lineage before fan-out
reconvergence. Distinct lookup keys avoid duplicate multiplication without choosing
an arbitrary differing payload. Lookup results use separate internal storage names
when exported output names collide with original join ports. Persistent/dynamic
lookups and arbitrary ANY-value payloads are not supported.

OPENCI SQL has 24 projections matching 24 connected output ports, although 26 ports
are exported. Unused TRAIL_TYPE_NAME/TRUNK_GROUP_DESC are retained as evidence, not
invented projection values. BillTrack's unwired onm_LEVEL3_CIRCUIT_ID identity port
is retained but is not synthesized into the consumed graph. Effectful expressions
are not pruned by this rule. Its unused lookup definition is reported separately.

ODW's Ecckt_Group, Fcckt_Group and Recckt_Group all have literal TRUE conditions.
Each receives the input independently, preserving duplicates. The default output
receives no rows for this source graph. This is not an exclusive CASE router.

## Measured graph coverage

Counts below include target projection operators, not completed session writers.
All sessions remain not fully converted and not runtime verified.

| Session use | Compiled operator candidates | Blocked operators | Target SELECT candidates | INSERT candidates |
|---|---:|---:|---:|---:|
| Parent counter | 2 | 0 | 1 | 1 |
| Four log checks, each | 2 | 0 | 1 | 0 |
| Dependency session | 2 | 3 | 0 | 0 |
| BillTrack | 7 | 1 | 2 | 2 |
| ODW | 18 | 1 | 5 | 5 |
| ONM | 3 | 7 | 1 | 1 |
| OPENCI | 14 | 1 | 3 | 3 |
| Total | 54 | 13 | 16 | 12 |

These 68 operator obligations include one unused exported definition. The existing
pinned Java query/polling/error core is reused separately, not counted as a newly
completed full dependency session. Feasible individual expression ports are emitted
even where the assembled ONM expression is blocked by external calls. Exact counts
excluding targets and unique definition counts are recorded by the generator.

BillTrack, ODW and OPENCI consumed SQL row graphs reach their business targets.
Source-preload TRUNCATE_TABLE and session write ordering/transactions still block
execution. ONM's actual product conversion does not reach its business target.
Its system lookup, product-key procedure and Unix-log sequence allocation are
not replaced from names. The ONM INSERT candidate above is the job-log path only.

Four log-check SELECT/projection candidates remain separate uses, preserving their
distinct scoped parameters and override identities. TARGET_DUMMY is a file writer.
No successful target-row count is claimed merely from a SELECT count. File format,
destination, overwrite/reject behavior and loaded-row receipt need implementation
and approved binding before those sessions can drive real business execution.

SQL phases retain owner, exact source text and statement order. Each supported
statement gets its own candidate. Oracle COMMIT/ROLLBACK and malformed source SQL
remain blockers. Phase conversion does not establish cross-pipeline target-load
order or atomicity.

## Child conditional graph and previews

[Control evaluator](../workbench/wil_control.py) implements exact successful-status
and target-success-row predicates, explicit skips and TREAT_INPUTLINK_AS_AND.
A failed, skipped or unknown-row-count log session is not an empty success.
Decision instances are OR merges, not unconditional load lists.

Generated JSON notebooks have metadata.language and stable metadata.id values;
Python source twins and a serverless STANDARD Jobs review shape are also emitted.
All 15 source instances remain represented. Notebooks execute structural eligibility
and skips, but reached business tasks fail with an explicit blocker. They are
inspection artifacts, not TODO business logic counted as converted.

The ALL_DONE prerequisite shape waits for all predecessors before evaluating an
OR merge. It preserves tested terminal-path logic, but earliest-trigger timing is
not certified equivalent to Informatica. Parent wrapper artifacts stay in a separate
subtree and retain asynchronous launch semantics. No blocking run_job_task is
introduced or duplicate child submission scheduled. SMTP/email remains external.

## External evidence search and required owner input

Initial streaming repository scan: 4,255 non-environment files; 1,177 SQL, 8 XML,
1,660 JSON, 767 Python, 291 Markdown, 247 HTML, two TXT, two YML and one YAML files.
No exact p_ae2e.txt/p_ae2e_prep.txt files and no CREATE PROCEDURE/FUNCTION/PACKAGE BODY
declarations for AE2E_COMMON, TRUNCATE_TABLE or GET_PRODUCT_KEY_ID were found.
The implemented search additionally handles multiline declarations and emits
candidate file locations without dumping parameter values. A DDL candidate is
not automatically a complete, reviewed body.

Read-only discovery of the existing labs user's workbench artifacts Volume was
attempted. The existing labs refresh token is invalid, so authentication failed
before any directory listing. Remote files are unknown, not proven absent.
No alternate profile, identity, credential collection or business query was used.
The requested agentic-blueprint documents/PDF remain absent from this checkout;
the available proposal-first design was consulted instead.

Required inputs, with no default approval:

1. Sanitized exact AE2E_COMMON package spec/body, overloads and dependencies for
   TRUNCATE_TABLE and GET_PRODUCT_KEY_ID, including key allocation, exceptions,
   partition and transaction behavior. Names alone are insufficient.
2. Sanitized parameter-file sections for each workflow/session instance, precedence
   and resolved nonsecret values. Supply secret references, never credentials.
3. Owner-approved whole-source/target bindings with reviewed scope/hash, datatypes,
   precision/defaults, timezone, regex/collation and source snapshot/freshness.
   Empty fixture observations never constitute approval.
4. Explicit transaction choice: preserve Oracle commit interval 10000 and rollback
   NO, partition/key/recovery effects through a reviewed execution design, or
   approve a changed Delta atomicity/recovery contract with expected failure and
   concurrency results. No default choice or multi-table equivalence is claimed.
5. Paired independent outputs from identical source snapshots/date/parameters,
   including duplicates, NULLs, types/precision, rejects, logs, sequence/procedure
   effects, restarts and concurrent/partial-write cases, with receipts.

[Runtime contract utilities](../workbench/wil_runtime_contract.py) prepare SQL using
explicit binding receipts and named scalar args, and compare typed output multisets.
Equal counts or equal empty rows do not issue a parity certificate. No business
statement is run by these utilities on import or compilation.

## Validation

[New tests](../workbench/tests/test_wil_parent_child_compiler.py) use freshly parsed
real XML with the exact pin, all 16 four-load skip combinations, failed/unknown count
paths, actual ODW branch SQL and 60 deterministic random duplicate/NULL lookup
differentials against an independent SQLite reference. SQLite establishes the tested
operator algebra only, not Spark/Oracle scalar cardinality, date, regex, collation,
transactions or business parity. Notebook Python is AST checked; JSON/YAML, INSERT
shapes, explicit approval rejection and immutable export hashes are checked.

The consolidated regression command covers the new tests plus package parity,
existing wrapper unit/API, source evidence and package-context tests. Exact command
and results:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_wil_parent_child_compiler.py workbench/tests/test_package_parity.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_source_evidence.py workbench/tests/test_package_context.py -q --tb=short --disable-warnings -ra
```

First consolidated batch: **380 passed, 1 failed, 0 skipped, 1 warning, 380.90s**.
The new real-XML coverage test exposed colliding lookup/original join port names
in OPENCI. The compiler was corrected by storing lookup outputs separately while
keeping their exported connector names, not by weakening the coverage test.
Adjacent existing tests passed and no existing test expectations were changed.

The focused new-path verification, with additional generated-notebook and actual
log-query differentials, subsequently passed **36 tests, 0 failed, 0 skipped,
1 warning, 111.52s**. Additional flat-file evidence assertions and explicit import
fixes require the final focused verification recorded below. A clean
whole-repository suite, hosted smoke, deployment or runtime parity is not claimed.