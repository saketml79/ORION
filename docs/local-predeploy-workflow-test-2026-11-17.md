# Visible local pre-deployment workflow test, 2026-11-17

## Request and scope

One fresh conversion through the shared local application before deciding on
Databricks app deployment. No offline browser fixture, notebook execution,
customer Job, email delivery, table write or Databricks browser was used.

## Exact run receipt

- Workflow: wkf_AE2E_DUMMY_CALL_WIL_NET_DIS.
- Individual workflow scope, bronze scenario, unchanged 4599-character generic prompt.
- Existing databricks-claude-opus-5-5 endpoint explicitly applied through the UI.
- Run: c050bfcd4d7d49e0aa4f75122ff246c0.
- Started: 2026-11-17T20:36:08.487325+00:00.
- Finished: 2026-11-17T21:12:19.766643+00:00.
- Elapsed: 36 minutes 11.279 seconds, including writer.
- Analysis: 49 model calls completed, no recorded model failures;
  135 tools started, 126 completed, 9 rejected submissions.
- Analysis reported tokens: 6726713. Writer tokens below are separate;
  do not treat analysis tokens as the combined run cost.
- Writer: 8200a17782934ba6bf5e22226d2dc111.
- Writer started: 2026-11-17T21:04:13.754951+00:00.
- Writer finished: 2026-11-17T21:12:19.762878+00:00.
- Saved Design version: 33, identifier
  3a654380c729d744a475e0ced3a49db3031161bfdac1d24f4498d34f7b4c6fc3.
- Writer: generation_complete, failure null, 4 notebooks and one native condition.
- Writer model calls: 26 artifact and 6 validation, 32 total.
- Writer tool calls: 78. Writer reported tokens: 6088012.

## Verified outputs

Saved notebook paths:

- notebooks/01_wait_dependency.ipynb
- notebooks/02_load_dep_counter.ipynb
- notebooks/03_call_net_disco_placeholder.ipynb
- notebooks/04_notify_dependency_failure.ipynb

The actual saved result API returned native Jobs YAML and all four bodies.
Static validation passed: notebook constrained schema, Python AST, strict
constant SQL checks, executable Informatica-variable checks, and native widget
parameter contracts. Dynamic SQL is not independently runtime-validated.
Authored tests were not run against Databricks.

The agent actually invoked check_generation_code twice. Both named tool events
completed with static_validation passed and check_status review_unverified.
This verifies the agent-callable tool path, not just a prompt or static helper.
The real UI displayed the completed writer and actual saved paths.

## Review and release decision

Overall generated-code review: needs_information, not fully AI-reviewed.

- Child placeholder, native condition and notification: AI review_ready.
- Counter: AI needs_information. The Oracle partition definition was not
  captured, so partition-scoped count/delete equivalence to TEMPLATE_ID=18300
  is unproven. Saved code still contains this predicate translation.
- Wait: review unavailable because the configured validation-model review
  budget was exhausted. This is not a finding of a syntax bug or a completed
  independent semantic review.
- No parent or writer UC bindings were recorded. Target catalog/schema/table
  and source FQN remain required blank runtime values. Code refuses empty
  values and does not create target objects.
- Informatica schema variables appear in provenance comments, not runtime
  defaults. The source schema variable is not used as a catalog.

Deployment was NOT attempted. A completed generation and successful static
checks do not clear the missing partition semantics, independent-review gap,
or unresolved bindings. No history or generated notebook was rewritten to
make this test appear successful. The user required testing before deployment;
this receipt does not certify bug-free conversion or production readiness.

## Regression and staging context

- Consolidated pre-test checks: 513 passed, 1 skipped, 2 warnings, 211.56 seconds.
- Subsequent workflow-label/cohort/sanity scoped checks: 38 passed, 2 warnings.
- Subsequent activity-layout renderer checks: 4 passed, 1 warning.
- Current external stage: 162 files, 100 Python grammar/current-compile checks,
  26 JavaScript syntax checks, 11 isolated imports and bundle validation passed.
- Grammar checks used Python 3.11 syntax rules; runtime was Python 3.13.14.
  This is not execution evidence under the hosted Python 3.11 runtime.
- Read-only hosted activity observations cover selected current-user history,
  not an all-user idle lease. No app configuration, scopes or resources changed.