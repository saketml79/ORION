# ORION deployment safety blocker, 2026-11-12

## Outcome

Deployment was explicitly authorized, but was **not attempted** because the same instruction required active-work verification before interruption. All three live active-work observations timed out. Unknown activity is not evidence of an idle app. No app stop, bundle deploy, app deployment, inference, customer Job deployment, source write, grant or authentication-configuration change was performed.

## Baseline app manifest snapshot

Captured via `databricks apps get orion-migration-workbench --profile labs --output json` before any deployment operation.

| Field | Observed value |
|---|---|
| App name | orion-migration-workbench |
| App ID | cfada07d-ffa6-4018-9080-293f5e577083 |
| URL | https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com |
| Active deployment ID | 01f1dd5e1fc51cc786ab0a642e11e46b |
| Deployment state | SUCCEEDED, Traffic switched successfully |
| Deployment create/update | 2026-11-10T23:26:15Z / 2026-11-10T23:29:14Z |
| App state | RUNNING, one running instance |
| Compute | MEDIUM, ACTIVE, one active instance, min 1 / max 1 |
| Attached resources | Field absent in get response, no attached resources reported |
| Requested scopes | catalog.catalogs, catalog.schemas, catalog.tables, files, model-serving, sql |
| Additional effective default scopes | iam.access-control:read, iam.current-user:read |
| App service-principal client ID | cfada07d-ffa6-4018-9080-293f5e577083 |
| App service-principal numeric ID | 144724977568695 |
| App service-principal name | app-45c6gp orion-migration-workbench |
| OAuth application/integration ID | 509eee35-28e6-4007-8e5a-3b78163dd782 |
| Default and deployment source path | /Workspace/Users/ssuman@bpcs.com/.bundle/modernization-workbench-hosted/dev/files |
| Snapshot artifacts | /Workspace/Users/cfada07d-ffa6-4018-9080-293f5e577083/src/01f1dd5e1fc51cc786ab0a642e11e46b |
| Deployment mode | SNAPSHOT |
| Command | python -m workbench.hosted |
| App created | 2026-10-30T05:24:31Z |
| App updated | 2026-11-10T23:26:15Z |
| Creator/updater | ssuman@bpcs.com |
| Description | User-scoped proposal-first evidence workbench. No executable migration or deployment authority. |

Deployment environment names reported by app metadata: WORKBENCH_TRUST_DATABRICKS_PROXY, WORKBENCH_DATABRICKS_HOST, WORKBENCH_APP_ORIGIN, WORKBENCH_CATALOG_PREFIX, WORKBENCH_SQL_WAREHOUSE_ID, PYTHONDONTWRITEBYTECODE, LANGCHAIN_TRACING_V2, LANGSMITH_TRACING. Values were not returned by this metadata response and were not claimed independently verified.

## Identity and live observations

Explicit CLI profile `labs` resolved the active human user `ssuman@bpcs.com`, user ID `4321958869410665`, workspace `https://adb-1866518241053589.9.azuredatabricks.net`. The deployer is not the app service principal. Credentials were held in memory only, never printed or retained in the receipt. Each app HTTP request was authenticated through the managed app proxy; application responses independently identified OBO.

| GET path | Start UTC | End UTC | Outcome |
|---|---|---|---|
| /api/health | 2026-11-12T20:10:04.730165+00:00 | 2026-11-12T20:10:05.999647+00:00 | HTTP 200, status ok, authentication databricks_obo |
| /api/databricks/volumes/status | 2026-11-12T20:10:06.000976+00:00 | 2026-11-12T20:10:06.237928+00:00 | HTTP 200, mode obo, auth_check verified, same workspace/user ID |
| /api/agents/runs | 2026-11-12T20:10:06.239440+00:00 | 2026-11-12T20:11:56.325252+00:00 | TimeoutError, completion and active-run state unknown |
| /api/loops | 2026-11-12T20:11:56.327630+00:00 | 2026-11-12T20:13:46.474077+00:00 | TimeoutError, completion and active-loop state unknown |
| /api/jobs | 2026-11-12T20:13:46.475390+00:00 | 2026-11-12T20:15:36.601460+00:00 | TimeoutError, completion and active-job state unknown |

Each state request had a 110-second observation bound and was not retried. Client timeout does not establish server cancellation. These were unselected legacy state reads, not proof of complete selected-project history or all-user inactivity.

## SP versus OBO permission boundaries

- Hosted source requires a fresh forwarded token and `obo_identity` on each authenticated action; missing tokens fail closed, and identity mode/workspace are checked. No service-principal fallback was added or used.
- The generic platform recommendation to attach SP resources does not override this app's explicit user-owned OBO architecture. No resources, grants, new SP or secrets were added.
- The Volume status route is an identity-only fast path. Its `connected` field is **not** a live READ_VOLUME/WRITE_VOLUME test. No remote Volume write permission is claimed verified in this attempt.
- Process-local writer output and SQLite permission checks are distinct from OBO remote authorization. No writer preflight change was made after the deployment safety blocker was discovered.
- The historical local PermissionError was not reproduced here and cannot be attributed to the app SP.

## Validation and pending work

Read the Databricks Apps platform and other-framework references, staging helper, hosted configuration, current hosted runbook, writer diagnostics note and repository memory. The parent databricks-core skill and required agentic-blueprint files were absent.

No new runtime code was edited, staged, tested or deployed. No Python 3.11 compatibility, staged import closure, candidate direct checks, updated static-asset smoke or new deployed endpoint result is claimed. Those remain required after the activity safety blocker is resolved. The configured local interpreter is Python 3.13.12, which alone cannot prove hosted 3.11 compatibility.

Only this documentation receipt was added to the checkout. The external inspection helper and its temporary raw metadata/observation JSON were removed in the same attempt. No git push was attempted or claimed.

## Required decision

Either provide reliable idle confirmation for hosted work, or explicitly authorize replacing the process despite unknown activity. Until then, do not stop the existing single-instance writer or create an overlapping replacement.
