# Validation hard gate, 2026-11-05

## Release decision

**BLOCKED. No deployment, catalog creation, source writes or live Jobs performed.**
At least four distinct business workflows must finish on real Databricks compute
with independently acquired paired original/candidate outputs. Four wrappers,
four sessions, mock children, empty fixtures and synthetic arithmetic do not count.
No certificate issuer or gate-clearing API is implemented by this change.

Existing v2 business SQL is reused as evidence, not re-scaffolded or promoted.
See [v2 compiler boundaries](wil-parent-child-source-compiler-v2.md) and
[required owner inputs](../output/workbench/wil-net-dis-parent-child-source-v2/required_owner_inputs.json).
No procedure body or Oracle transaction equivalence was invented. Production
54-query/54-sleep behavior remains unchanged; existing tests virtualize sleep.

## Root causes and implemented changes

1. Baseline campaign planning rejected `double / Flat File`. The target planner
   now maps that exact target datatype to DOUBLE, preserves XML metadata, and
   leaves source-fixture behavior untouched. Actual XML: 307 targets, 10,378
   columns, seven DOUBLE target columns.
2. Original new campaign expected eight selected targets. Fresh parent plus child
   actually contains nine, including the parent counter and flat-file targets.
   The test now checks the parent counter rather than using a child-only count.
3. Full WIL evidence exceeded the unchanged Delta 48 KiB payload. Large receipts
   are losslessly gzip/base64 encoded with deterministic bytes, decoded size and
   SHA-256 validation, a 2 MiB decoded ceiling and bounded decompression. Small
   historical records remain directly readable. No evidence truncation or Delta
   limit increase. Hosted authority remains fresh request-scoped Delta.
4. Exactly three new catalogs are planned, bronze/silver/gold, with owner and
   campaign-qualified names. No fourth output catalog. Existing campaigns remain
   immutable; old four-catalog plans fail new provisioning validation and must
   not be replayed. Source catalog `infotodbxmig_src_dev_ssuman` is preserved.
5. Destination assignments require exact selected canonical target IDs. No
   inferred gold placement, invented source bindings or forced three-layer rewrite.
   Unassigned targets stay BLOCKED and create no target schemas/tables. Catalog-only
   creation is allowed separately, but never clears target schema readiness.
6. Readiness lists up to ten distinct business definitions from the selected
   invocation cohort, with actual source/target/mapping counts. Smaller cohorts
   are not padded with wrappers. Every absent runtime/reference receipt is
   BLOCKED or NOT_RUN. The release gate reports zero verified business workflows.
7. Exact typed row bags remain authoritative. Per-column multisets, null counts,
   numeric sum equality, duplicates and key diagnostics are available without
   exporting values, row hashes or aggregate values. Equal per-column sets or
   sums cannot conceal swapped cross-column relationships. DOUBLE comparisons
   reject nonfinite values, implicit casts and approximate tolerances.
8. Validation UI accepts explicit layer JSON, displays destination names, ten
   business readiness rows and the blocked four-workflow gate. No startup jobs,
   unconsented provisioning or model calls. Offline Chromium runs the actual
   script and markup with remote assets blocked.

Changed implementation:
[campaign lifecycle](../workbench/validation_campaigns.py),
[target planner](../workbench/target_fixtures.py),
[create-only provisioning](../workbench/validation_provisioning.py),
[readiness projection](../workbench/validation_readiness.py),
[typed comparison](../workbench/validation_data.py),
[Validation UI](../workbench/static/validation-campaigns.js),
[markup](../workbench/static/index.html),
[regressions](../workbench/tests/test_validation_campaigns.py).

## Live authorization and remaining inputs

Fresh `profile_identity(profile='labs')` failed with AuthenticationUnavailable
before App metadata or Volume directory listing. Tokens and raw exceptions were
not printed. No alternate identity or service-principal fallback was attempted.
Remote definitions are **unknown**, not proven absent. Current release was not
modified, stopped or redeployed. Live catalog names, schema counts and job/run
IDs are unavailable because no new remote resource was created.

The repository contains no .pkb/.pks/.prc/.par files; the existing v2 search also
reported no matching exact parameter files, external package-body declarations
or independent reference-output candidates. Missing inputs:

- Exact sanitized AE2E_COMMON spec/body, overloads and dependencies, particularly
  TRUNCATE_TABLE and GET_PRODUCT_KEY_ID, including exceptions and key allocation.
- Scoped p_ae2e/p_ae2e_prep sections and precedence, nonsecret resolved values and
  secret references only.
- Reviewed source/target column, layer, key, type, clock, freshness and snapshot
  bindings. Empty source fixtures do not supply business data.
- Explicit transaction/recovery/sequence and operational side-effect contracts.
- Independent paired nonempty outputs from identical inputs/date/parameters,
  with rejects, logs and retry/failure/concurrency receipts.
- Restored labs OAuth and a fresh hosted OBO action before authorized create-only
  catalog provisioning. No hosted feature deployment may bypass the business gate.

## Six criteria

| Criterion | Business status | Available local evidence |
|---|---|---|
| Optimization | NOT_RUN | No paired runtime, shuffle/scan/cost baseline |
| Accuracy | BLOCKED | Controlled synthetic differential PASS, not original parity |
| Integrity | NOT_RUN | Exact typed bags/nulls/keys tested; business transactions absent |
| Security | BLOCKED | Offline OBO, owner and no-fallback guards tested; live authentication failed |
| Databricks practices | BLOCKED | Three-catalog explicit layer plan; live compute/lineage not run |
| Data validation | BLOCKED | Same-count wrong-value and swapped-column controls PASS; original outputs absent |

## Regression ledger

Baseline exact six-file command: **136 passed, 1 failed, 2 warnings, 63.38s**.
Failure: target datatype planning, not a live execution failure.

Expanded command:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_validation_campaigns.py workbench/tests/test_source_fixtures.py workbench/tests/test_ui_structure.py workbench/tests/test_delta_state.py workbench/tests/test_hosted.py workbench/tests/test_hosted_package.py workbench/tests/test_wil_parent_child_compiler.py workbench/tests/test_package_parity.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_source_evidence.py workbench/tests/test_package_context.py -q --tb=short --disable-warnings -ra
```

First expanded batch: **533 passed, 1 failed, 2 warnings, 329.31s**. Only the new
child-only target-count assertion failed. No compiler or old test gate weakened.
Next expanded batch: **535 passed, 1 failed, 2 warnings, 433.69s**. Only new
browser harness failed on the existing external font request. Corrected harness
aborts external origins and decodes JSON only for POST. Production font/UI code
unchanged.

Final campaign-only command:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_validation_campaigns.py -q --tb=short --disable-warnings -ra
node --check workbench/static/validation-campaigns.js
```

**55 passed, 1 warning, 42.17s; JavaScript syntax PASS.** Editor diagnostics empty
for all changed Python, JavaScript and HTML. Final expanded rerun and immutable
proof export verification are recorded below when complete. No whole-repository
or live parity success is implied by these local results.