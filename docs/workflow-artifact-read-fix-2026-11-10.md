# Pure workflow artifact POST classification, local only

## Root cause and scope

The hosted dispatcher classified every POST except fixture metadata and target verification as a mutation. The two deterministic, in-memory workflow artifact endpoints therefore encountered the authenticated write queue and full runtime restoration even though the local compiler writes no state.

Only POST /api/proposals/workflow-artifacts and POST /api/proposals/workflow-artifacts/archive now use read-only classification. Both retain the existing same-origin, cross-site, custom-header and fresh forwarded-user authentication checks. They validate the existing strict WorkflowArtifactsBody schema, bronze scenario and consent:true before hydration, including a 64 KiB streamed-body limit. An explicit project and package are required. Other POST routes retain their previous mutation classification and guards, including SQL drafts and model selection.

## Canonical authority and response parity

- The existing canonical object reader verifies committed generation inventories, project/package membership, activation, manifest, understanding and optional source-analysis artifacts using the fresh OBO actor.
- A server-only callable projects the verified PackageIndex into artifact output. No HTTP query, header or public GET route enables this callable. Existing route capture groups, including design ID group 6, are unchanged.
- The shared pure response module preserves canonical workflow-kind checks, compiler fingerprint errors as HTTP 422, original download names, sorted deterministic ZIP metadata and exact UTF-8 bytes. Unsafe relative paths are rejected.
- No full application construction, XML parsing, model installation/inference, audit restore, history restore, generation flush, Delta publication, job submission or catalog business write occurs on this path. Disposable canonical hydration files are the only local materialization.
- Existing locally pending stored-redesign projection and frontend read-helper fixes are preserved. Redesign HTTP 502 and workflow generation results remain separate status fields. Explicit refresh still invalidates previous output and consent as before.

## Verification

Completed one consolidated offline batch after all code edits: **580 passed, 1 skipped, 6 subtests passed, 1 warning in 692.77 seconds**. The sole skip is the existing Windows symlink-creation limitation. No test exclusions or deselections.

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_hosted_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_readiness.py workbench/tests/test_redesign.py workbench/tests/test_redesign_ui.py workbench/tests/test_run_review_fixes.py workbench/tests/test_hosted.py workbench/tests/test_hosted_agent_reads.py workbench/tests/test_hosted_operations.py workbench/tests/test_hosted_intake_ui.py workbench/tests/test_hosted_lock_ownership.py workbench/tests/test_hosted_model_reads.py workbench/tests/test_hosted_intake_integration.py workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_runtime_planning.py workbench/tests/test_agent_arena.py tests/test_spark_codegen.py tests/test_spark_codegen_api.py -q --tb=short --disable-warnings -ra
node --check workbench/static/redesign-review.js
```

Python AST parsing passed for all six changed Python files. Pylance verified existing object reader callers remain signature-compatible. Editor checks were clean.

New hosted tests exercise both endpoints while a same-user writer is pending, retain active-user capacity limits, reject malformed/oversize/disconnected requests before remote access, recheck cross-user/project/package permissions and corrupted canonical artifacts, preserve compiler errors and verify unchanged remote rows and Volume objects. Existing local API tests verify archive traversal rejection and actual compiler fail-closed semantics. An offline Chromium case verifies a saved-redesign 502 remains distinct from subsequent successful workflow compilation.

## Release boundary

No deployment, restart, live POST, model call, Jobs call or remote business-state change was performed. The current hosted deployment is unchanged and does not contain this fix or the pending stored-redesign fix. Hosted latency and live success remain unverified until separately authorized deployment and verification. Existing historical artifacts and compiler pins were not changed or reactivated.