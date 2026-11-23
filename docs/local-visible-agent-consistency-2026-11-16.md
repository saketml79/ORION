# Visible local agent consistency checks

## Test method

Two fresh runs were invoked through the shared local Agent activity page, with
one unchanged workflow-independent prompt, the individual-workflow selector,
Unity Catalog source scenario, and the existing six roles using the available
Databricks Claude Opus 5.5 endpoint. No supplied table names, thresholds, queries
or workflow-specific answers were added to the prompt. No generated notebooks
were executed, email sent, business tables changed or application deployed.

These are real local application generation runs, not offline pytest browser
fixtures. Generation success and AI review do not prove semantic/runtime parity.

## Run 1

- Public run: 94450e2cf9574b65aecd5f174e9e217f.
- Started: 2026-11-16T02:04:27.934994Z.
- Finished: 2026-11-16T02:38:17.928627Z.
- Runtime: 2843860aea134cc69babe8ba65e04f4b.
- Writer job: 58f8b301a75f4409a613d98a658cf62b.
- Selected saved design: version 27.
- Completed analysis model calls: 47, failed calls: 0.
- Generation: complete, four notebooks, five drafted tasks including native condition.
- Native Jobs YAML: returned by actual local saved-artifact API.
- AI reviewer: review_ready, not runtime verified.
- Source binding: metadata-verified candidate with matched column types/nullability.
- Destination: unresolved, empty required target_table parameter, no source table copied
  into target default. Notebook rejects equal source and target names.
- Counter preserves the captured session override, append, main-row count, post
  insert, scoped >=60 reset, and explicit partition/transaction/recovery assumptions.
- Email contains actual SMTP STARTTLS send code and required secret references;
  subject/environment/support-list are runtime parameters, no mail sent during testing.
- External command notebook is informational only, no child Job launch or ID widget.

## Run 2

- Public run: 1661261d19c0442f92dc99006f148979.
- Started: 2026-11-16T02:39:42.961193Z.
- Finished: 2026-11-16T03:14:51.326070Z.
- Runtime: aaccbf3727444a0cb118f55bedeba19e.
- Writer job: 481ef10f7f9a41659b64b6cd47879a25.
- Selected saved design: version 29.
- Completed analysis model calls: 44, failed calls: 0.
- Generation: complete, four notebooks, five drafted tasks including native condition.
- AI reviewer: review_ready for all five tasks, not runtime verified.
- Source candidate matches run 1.
- Discovery also recorded that source table as an unapproved TARGET candidate,
  based on matching columns, same connection alias and possible self-reference.
  It explicitly acknowledged missing $$AE2E_SCHEMA value, no mutation authorization
  and no parity proof. This is NOT a verified destination mapping.
- Counter uses empty defaults for both table widgets. Same-table mutation requires
  an additional explicit confirm_in_place_mutation=true runtime flag. No write ran.
- Actual native YAML builder fails: email notebook cell 2 declares widgets in a loop
  rather than explicit top-level literal declarations. Exact ContractError:
  "Job parameter defaults blocked ... Move conditional or nested widget declarations
  to explicit top-level literal declarations before generating the job."
- The API's generic native_job_blocker message obscures that actionable cause.

## Unresolved findings and deployment gate

**The repeated local conversion is not consistently release-ready.** Both runs saved
code, but source/destination mapping correctness is not fully established, and the
second run cannot build native YAML despite the AI review reporting review_ready.

Existing read-only catalog inspection found no AE2E_JOB_DEP_CHK_COUNT destination
in any schema of the three remembered destination fixture catalogs. That absence
must not be concealed with an automatic source-as-target default. A possible
source-system self-reference is distinct from permission to mutate a source snapshot.

Required follow-up: source-derived destination evidence/disposition, writer guidance
and generated-code review for literal widget declarations compatible with the
existing parameter-contract parser, and accurate actionable YAML error presentation.
Do not weaken native schema validation or rewrite historical generated artifacts.

Full UI/offline regression remains separate and must report actual results. Live
Databricks notebook edit-return is deferred to the user as explicitly requested.
No deployment performed. Multi-endpoint routing and extra agents are excluded.
