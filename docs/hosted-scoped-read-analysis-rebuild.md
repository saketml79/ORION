# Authorized scoped reads and saved Java analysis rebuild

## Scope and release boundary

The user's subsequent “fix what is remaining” authorizes the previously proposed
Option B read projection and explicit versioned saved-source evidence enrichment.
This work does not deploy or restart the app. Compiler, parser, source-evidence
and agent implementations are outside this workstream and were not edited.
Parent/child conversion and output parity remain a separate release dependency.
No model inference, business Jobs, source-data queries, live UC writes or approval
renewal are performed here.

The referenced agentic-blueprint directory and PDF remain absent. The available
[proposal-first plan](proposal-first-deepagents-lakeflow-plan.md),
[authority contract](hosted-delta-authority.md),
[source-understanding investigation](package-source-understanding-investigation.md)
and [release ledger](orion-hosted-release-2026-11-04.md) govern this bounded change.

## Scoped committed-generation observations

[The projection](../workbench/hosted_object_reads.py) applies only to exact
canonical object or workflow migration-context GETs with both explicit project
and package headers. The dispatcher still obtains a fresh OBO identity, enforces
the fixed workspace, verifies owned catalog/schema/Volume with initialize=False,
and retains active-user capacity and request diagnostics. Mutations and legacy
unselected reads keep their existing full restoration path.

- Reuse [registry verification](../workbench/hosted_registry.py) for the latest
  committed receipt, immutable generation manifest and selected project/package
  membership. No older receipt or local/HEAD fallback is accepted.
- Read and validate every row of each queried scope against its manifest
  inventory, including full envelope types, workspace/user, generation, record
  identity, content hash and advancing cursor. Project/package membership is
  checked anew on each request, not cached by a TTL or client identity.
- Verify artifact-manifest scope and file identities. Materialize only activation
  metadata, the package manifest and compressed canonical model. Activation and
  manifest JSON remain reconstructed from Delta source columns after checking
  the corresponding immutable artifact bytes. An enriched package additionally
  needs its explicit analysis pointer and selected immutable analysis archive.
- Match the persisted selected activation hash. A stale selection is not quietly
  redirected to another package. Missing membership keeps the established 403;
  unknown project/object/workflow and invalid pagination retain their route status.
- Object and context presenters are shared with the full application in
  [object_views](../workbench/object_views.py). Tests compare complete JSON,
  including exact fields, graph, explanations, diagnostics and pagination.
- No source XML, English export, baseline audit history, review databases,
  agent checkpoints, model routing or unrelated jobs are restored. No application
  is constructed and no model clients are configured for these observations.
- The existing ordinary-read stdout audit policy remains unchanged. This path
  does not add a registry-journal write or modify immutable audit history. The
  existing async envelope still appends its own durable operation journal.

Without enrichment the required canonical file set is three artifacts, not the
historical sixteen-file runtime set. Including the generation manifest, the
offline fixture records exactly four artifact downloads per request. With
enrichment the canonical file set is five. Externalized source columns may add
their own verified object reads, so these fixture budgets are not universal
network-call or SQL-count guarantees. No hosted latency improvement is claimed
before deployment and an exact-scope authenticated smoke test.

## Explicit versioned source analysis

[Source analysis](../workbench/source_analysis.py) introduces:

- GET /api/source-analysis: observes selected package/base understanding/version
  metadata only. It never reparses XML, writes an analysis or treats same-hash
  upload as reanalysis.
- POST /api/source-analysis/rebuild: requires consent:true, exact selected
  package_hash, expected_understanding_sha256 and expected_analysis_version
  (null for the first enrichment). Existing mutation headers and hosted fresh OBO,
  full restore, serialization and commit-last publication remain mandatory.
- Both package and agent slots must be idle. Verify saved source SHA-256, parse
  only that selected source and join Java evidence at its actual canonical
  property-record location. Exact node IDs, owner kind/parent/evidence and each
  property's non-sidecar hash must match. Incompatible provenance is a 409,
  not a migration or silent replacement.
- Content-addressed compressed analysis artifacts and an explicit pointer retain
  all prior analysis versions. Canonical source/model/manifest/English artifacts,
  IDs, edges, selection timestamps, review documents and historical approvals
  remain unchanged. No deletion, automatic approval or generation permission.
- The verified pointer participates in immutable index cache identity. Warm cache
  reuse still verifies the selected analysis artifact. The old path-only cache
  fallback was removed because it could return an earlier analysis or another
  cache-scope identity after a pointer update.
- Agent input snapshots include the active analysis version when present. Old
  snapshots and reviews remain stored; their input-version comparison becomes
  stale instead of silently renewing historical approvals. Unenriched packages
  keep their existing input-version contract.
- Exact rebuild POST and migration-context GET are accepted by the existing
  durable envelope, with no replay, alternate execution identity or broader route
  wildcard. There is no new UI panel or automatic rebuild on reload.

### Actual saved Lumen proof, local isolated copy

An external context-managed temporary copy of the real saved package
08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba was
loaded and explicitly enriched using the production rebuild function.

- 5 Java-owning canonical objects, 30 Java property records.
- 167,120 nodes and 531,344 edges retained, not the distinct hosted upload counts.
- Analysis version 6126c0334315369b0af15e04b0fccfcbaefd012e84c6062baf72f08d4f654e89.
- Actual local load plus rebuild time: 43.235 seconds. Not HTTP or hosted timing.
- Original saved source, manifest, compressed understanding and English file
  SHA-256 values were checked before/after and unchanged. Temporary copy removed.
- This verifies compatibility with the actual older saved canonical artifact.
  It does not activate that enrichment in the user's live local or hosted state.

## Regression and staging record

Commands use the configured Python 3.13.12 venv, PYTHONDONTWRITEBYTECODE=1 and
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1. The consolidated backend command is:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_object_reads.py workbench/tests/test_source_analysis.py workbench/tests/test_hosted_registry.py workbench/tests/test_hosted.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_hosted_package.py workbench/tests/test_delta_runtime.py workbench/tests/test_package_context.py workbench/tests/test_source_evidence.py workbench/tests/test_migration_planning.py workbench/tests/test_redesign.py workbench/tests/test_app.py -q --tb=short --disable-warnings -ra
```

First result: 299 passed, 4 failed, 1 warning, 281.51 seconds. Two Java fixture
assertions put a control statement on the same line as a password declaration,
which existing deliberate fail-closed redaction masks; the fixture now places
the control on its own line. The activation corruption fixture attempted a
valid publication with an inconsistent package scope, which publication rightly
rejects; it now constructs the self-consistent corrupted Delta envelope directly.
No parser/redaction behavior or integrity gate was changed for these fixtures.
The fourth failure is the existing planning unresolved-reference assertion.

Second unchanged backend command: 301 passed, 2 failed, 1 warning, 402.32 seconds.
All new scoped-read/rebuild tests passed. The planning assertion still expects 1
and receives 0. Existing corruption-job test also exceeded its original 180-second
deadline in that batch; no assertion/deadline was modified. Exact isolated rerun
with faulthandler_timeout=35 passed: 1 passed, 1 warning, 4.92 seconds. This is an
observed intermittent timeout, not proof of a root cause or a clean batch.

Latest guidance/UI scripts passed node --check. Editor diagnostics for all seven
changed/new application modules and three changed/new test modules are empty.
Staging tests compile actual staged Python and compare every allowlisted static
asset by SHA-256, explicitly including next-action guidance JS/CSS and its HTML
includes.

### Completed final verification

The final expanded consolidated command was the backend command above plus
tests/test_next_action_guidance.py and tests/test_navigation_prerequisites.py,
with the same options. Result: **354 passed, 2 failed, 1 warning, 309.30 seconds**.
All 52 guidance/navigation tests passed, as did the corruption-job test that had
timed out in the preceding batch. The remaining failures were:

1. The unchanged planning unresolved-reference assertion, expected 1, actual 0.
2. The newly added envelope fixture incorrectly indexed the producer's return as
   a dict. Its actual API returns (operation ID, immutable request, request hash).
   The test now checks all three exact values; production code was unchanged.

After that test-only correction, this final changed-path command passed:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_object_reads.py workbench/tests/test_source_analysis.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_package.py -q --tb=short --disable-warnings -ra
```

Result: **92 passed, 0 failed, 1 warning, 50.24 seconds**. No deselection or skip
was added. The expanded batch was not repeated after the test-only tuple fix,
so this is not a clean combined-suite claim. The planning assertion remains open
for the separately owned parser/conversion workstream, unchanged here.

Actual external allowlisted stage, after all code/test edits:

- 83 manifest entries.
- **42 staged Python modules py_compile passed** with compilation output outside
  the package, not merely source AST/editor validation.
- **40 isolated staged workbench imports passed** in a subprocess whose working
  directory and PYTHONPATH were the staged candidate.
- **22 static assets matched local SHA-256 exactly**, including latest next-action
  JS/CSS and HTML includes.
- Stage and compilation scratch were context-managed external temporary paths
  and their absence was checked after cleanup. No repository logs or immutable
  history were deleted. No CLI deployment or remote resource operation occurred.

No live hosted cold/warm timing is available for this undeployed candidate. The
43.584-second historical hosted object sample and 43.235-second local isolated
saved-analysis proof measure different operations and must not be subtracted or
presented as before/after performance results.

## Coordinated release steps, not executed

1. Obtain the separate conversion workstream's exact status and immutable artifact
   parity evidence. Do not equate this hosted performance/enrichment change with
   business ETL conversion.
2. Resolve or explicitly disposition the unchanged planning failure. No shipment
   readiness or clean full-suite claim while an assertion remains red.
3. Use a new external allowlisted staging directory. Compile every staged Python
   file, test isolated staged imports, and compare all static bytes to the final
   combined workspace after the conversion workstream finishes.
4. Only after coordinated release authorization, use the existing labs app-only
   bundle validate/deploy/stop/run procedure. Preserve existing app ID, warehouse,
   OBO scopes, one replica and no attached business resources.
5. Fresh authenticated exact selected-project/package smoke: canonical singular
   object route and migration-context route, cold/warm status, exact selected IDs,
   full response parity and measured timings. Compare against historical object
   43.584-second unpolled hosted baseline only as a historical sample.
6. If activating saved analysis is desired, explicitly read its current base and
   analysis versions and submit the rebuild with consent through the durable
   envelope. Verify the committed pointer/version and stale prior-input status.
   Never mutate through GET or reupload the same hash as a substitute.
7. Record exact deployment timestamps, final static hashes, active generation and
   observed timings in the existing release ledger. Preserve immutable logs.