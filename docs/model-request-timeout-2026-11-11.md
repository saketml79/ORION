# Sonnet 4.6 provider request timeout, local-only fix

## Evidence and scope

The requester supplied four live fourth-call 60-second timeout incidents with
Sonnet 4.6 and contexts exceeding 100k. This change does not replay those calls
or independently establish their exact token counts. Source inspection confirmed
that `build_chat_models` hardcoded `timeout=60` and `max_retries=0`.

The local `install_model_access` selection path and hosted
`install_hosted_models.configure_runtime` execution path both create the six
runtime role clients. The separate Spark writer reuses
`service.runtime.role_models['artifact']`, including its timeout and zero retries.

## Narrow implementation

- `build_chat_models(..., request_timeout=60)` keeps the original default.
- `WorkspaceModels.chat_models(..., request_timeout=60)` exposes the same
  keyword-only setting. A numeric scalar applies to all roles; a partial role
  dictionary leaves unspecified roles at 60 seconds.
- Values must be finite native integers/floats between 1 and 180 seconds.
  Booleans, strings, null, unknown roles and unbounded values fail validation
  before model construction or authentication. No environment flag is added.
- Both app installers use `WorkspaceModels.app_chat_models`, which assigns
  180 seconds only to roles explicitly routed to the exact endpoint
  `databricks-claude-sonnet-4-6`. All other endpoints remain at 60 seconds.
- Endpoint selection, route persistence, fresh user authorization and metadata
  validation are unchanged. Timeout overrides are not saved into route records.
- This is the OpenAI transport timeout, not a total-generation or run budget.
  The existing scalar transport configuration applies to connect/read/write/pool
  phases. SDK metadata and CLI authentication timeouts are untouched.
- Automatic provider retries remain zero. No change to context construction,
  role-call limits, structured completion, writer budget, or the 900-second
  lifecycle ceiling. No extension of an exhausted run budget.
- Cancellation remains cooperative. An in-flight provider request may finish
  before cancellation is observed. A longer provider read timeout can therefore
  increase that existing cancellation wait; no hard-kill guarantee is introduced.

## Offline verification

One consolidated regression command was executed:

    .\.venv\Scripts\python.exe -m pytest -o addopts='' -q -ra --tb=short workbench/tests/test_model_request_timeout.py workbench/tests/test_databricks_models.py workbench/tests/test_model_workspace.py workbench/tests/test_hosted.py workbench/tests/test_hosted_model_reads.py tests/test_spark_codegen.py workbench/tests/test_run_review_fixes.py workbench/tests/test_runtime_planning.py::test_pre_cancelled_agent_run_does_not_invoke_any_model workbench/tests/test_runtime_planning.py::test_provider_failure_not_retried_and_other_roles_still_observed workbench/tests/test_runtime_planning.py::test_normal_agent_runtime_cleanup_does_not_signal_user_cancellation workbench/tests/test_app.py::test_agent_run_can_be_stopped_cooperatively

Result: **218 passed, 3 existing warnings, 67.43 seconds, exit 0**.
Tests cover installed synchronous/asynchronous client settings, actual mocked
OpenAI timeout/503 single-attempt transport, mixed-role routing, both installer
paths, metadata-only nonconstruction, fresh identity, route-file preservation,
writer contracts, pre-start and blocked in-flight cooperative cancellation.
AST checks passed for all four changed Python files. Pylance signature analysis
found all eight factory call sites compatible.

Protected agent source SHA256 before and after:
`a1dcc8d24f30d7f903690a92380d2be010bf3cda463dbea2e265ef70ea735d2f`.

No live model/auth/cloud calls, deployment or process restart. The running app
was not updated. This does not prove that a live large-context generation will
complete within 180 seconds or that generated artifacts achieve semantic parity.