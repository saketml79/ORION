# Writer tool-message wire normalization, local only

## Confirmed evidence

- Candidate job `30ff67f8daa341289ac5d4faade35115` references writer
  `6fbd16d56179472ca1a9209846ac8cfd`, design version 7. The public job is
  `completed`, but its writer manifest is `blocked` with `draft_agent_failed`.
  No status or historical output was changed.
- Manifest route: `databricks-claude-sonnet-4-6`. Budget used: 197 model calls,
  219 tool calls, eight submission attempts.
- The checkpoint error contains HTTP 400, `messages.415.name`, and
  `Extra inputs` / `extra_forbidden`. Only these fixed predicates were reported,
  never the raw provider error, request context, model reasoning, or credentials.
- Installed versions inspected: langchain-openai 1.7.3, langchain-core 1.7.0,
  openai 3.40.0, databricks-sdk 0.157.0.
- Installed OpenAI tool-message request schema has `content`, `role`, and
  `tool_call_id`, not `name`. System/user schemas permit `name`. The installed
  LangChain typed ToolMessage converter already filters `name`; its generic
  ChatMessage tool-role path does not. This guard covers the final serialized
  role rather than relying on a particular Python message class.

### Checkpoint inspection boundaries

Read SQLite with `mode=ro`, `PRAGMA query_only=ON`, and a closing connection.
Decode MessagePack extensions into plain data with ormsgpack, without pickle,
dynamic imports, object constructors, or executing checkpoint values.

There are 417 unique message IDs: two HumanMessage, 196 AIMessage, and 219
ToolMessage. All 219 tool messages have names and their IDs match assistant
tool calls. The final ordered message-write roles are:

| Zero-based write | Class |
| --- | --- |
| 411 | AIMessage |
| 412 | ToolMessage |
| 413 | AIMessage |
| 414 | ToolMessage |
| 415 | AIMessage |
| 416 | HumanMessage |

A single leading system message would put write 414 at request index 415.
The exact historical middleware-expanded HTTP request was not retained, so this
is a sequence correlation, not proof of the historical wire body or loaded
serializer version. No historical wire replay or live inference was attempted.

Hash comparison before/after the completed bounded read verified five files
unchanged: job, manifest, checkpoint database, WAL, and SHM. Manifest SHA256:
`c8d169133f512d79361e14d54f08d0fbb91975daa76f62fbc1e79b64bbc82931`.
Checkpoint database SHA256:
`4766f573ee1d8acee0a50f574ce7f5eec7139edb2970077f7d9d5d54b3342671`.
An initial full replay probe exceeded its execution limit; the completed probe
retained only bounded role/ID summaries, not full payload/model dumps.

## Exact source changes

- [workbench/databricks_models.py](../workbench/databricks_models.py): private
  `_DatabricksChatOpenAI` overrides `_get_request_payload`, calls its parent,
  returns a new outer dictionary/list and a new dictionary for each named
  tool-role message, removing only that dictionary's top-level `name`.
  Other fields and nested function names are unchanged. No recursive stripping,
  message mutation, ledger edits, checkpoint rewrite, or middleware changes.
- Only `build_chat_models` selects the subclass. Standard imported ChatOpenAI
  and other providers remain unchanged. The inherited `timeout` constructor
  alias supplies the identical existing per-role timeout value. Authentication
  callbacks, retry policy, stream options, endpoints, and route selection stay
  unchanged. No name removal for system, user, or assistant roles without
  verified rejection evidence.
- [workbench/tests/test_databricks_message_payload.py](../workbench/tests/test_databricks_message_payload.py):
  installed schema checks, typed and generic tool roles, source provenance,
  shared-parent payload nonmutation/idempotency, Responses passthrough,
  Databricks-only factory and lazy authentication, bound tool definitions,
  actual sync/async SDK mock HTTP JSON captures, and safe HTTP 400 diagnostics.
- [workbench/tests/test_databricks_models.py](../workbench/tests/test_databricks_models.py)
  and [workbench/tests/test_model_request_timeout.py](../workbench/tests/test_model_request_timeout.py):
  transport-constructor test injection targets the subclass now selected by the
  factory. Existing assertions and timeout policies were not weakened.

## Offline regression command

Run from the workspace root, using the configured existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -o addopts='' -q --tb=short workbench/tests/test_databricks_message_payload.py workbench/tests/test_databricks_models.py::test_mock_http_sync_and_async_use_fresh_user_authorization 'workbench/tests/test_model_request_timeout.py::test_explicit_timeout_is_bounded_role_local_and_not_persisted[1]' 'workbench/tests/test_model_request_timeout.py::test_explicit_timeout_is_bounded_role_local_and_not_persisted[60]' 'workbench/tests/test_model_request_timeout.py::test_explicit_timeout_is_bounded_role_local_and_not_persisted[90.5]' 'workbench/tests/test_model_request_timeout.py::test_explicit_timeout_is_bounded_role_local_and_not_persisted[180]'
```

Final identical focused pass after the test-helper annotation/key callback
correction: **10 passed**, one existing AnyIO pytest deprecation warning,
**6.73 seconds**, exit 0. Initial pass was also 10 passed in 13.19 seconds.
All four changed Python files pass AST parsing and editor error checks. The new
test module has no Pylance errors; production Pylance findings remain outside the
change in existing retry-after parsing, identity user_id, and ConfigParser
_sections code. No source/test changes followed the final passing gate.

The mock transport asserts complete decoded request-body equality, including
tool definitions and assistant function names. It never captures headers.
Application networking is forbidden, including during async requests; only
Windows' internal event-loop socketpair construction is allowed before running
client code. The mock 400 occurs once, with no automatic retry.

No live model calls, cloud suite, server restart, deployment, retry of the saved
job, folder module edits, static edits, or historical evidence rewriting.
The already-running backend has not loaded this new class. Activation remains
separate and requires a later explicitly authorized restart/deployment.

The required agentic-blueprint documents/PDF are absent from this checkout.
This change does not alter the agent mesh, governance, consent, or execution
authority.