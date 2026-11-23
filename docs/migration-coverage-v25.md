# Migration coverage assessment: selected workflow, design v25

## Scope and evidence

Workflow: wkf_AE2E_DUMMY_CALL_WIL_NET_DIS. Saved run:
9970f372771442988b72a34952e9474b. Writer job:
aff26a5fba45448aabab4da2955b8e3b. Design version: 25.

This report compares the actual canonical XML with the exact saved design and
checksum-read artifacts. It does not use regression fixtures as migration rules.

## Observed results

The table below records the initial assessment before the native-condition builder
correction. The later update at the end documents the corrected YAML result.

| Dimension | Result | What it establishes |
|---|---|---|
| Direct original task dispositions | 5 of 5 represented once | Structural task coverage only |
| Workflow variables | 40 declarations | 3 user-defined, 37 system fields |
| Source variable/reference inventory | 48 distinct declaration/reference strings in the canonical selected closure | Includes configuration and task-status references, not 48 required Job parameters |
| Logical parameter declarations | 5 | Does not prove each declaration is consumed correctly |
| Notebook generation | generation_complete, 4 saved notebooks | Code availability, not business correctness |
| Generated native Jobs YAML | Unavailable | Cannot certify executable Job wiring or full YAML parameter coverage |
| AI code review | needs_information overall | Not a full semantic endorsement |
| Independent parameter-translation verification | Not established | Must trace each necessary source reference through scope, parameter and executable use |
| Independent business-logic and runtime parity | Not established | No execution or independent reference outcomes in this assessment |

## Original task dispositions

| Original task | v25 disposition |
|---|---|
| Start | Administrative entry removed, no notebook needed |
| tmr_WAIT_FOR_DEPENDENCY | wait_5min |
| s_m_AE2E_JOB_CHECK_COUNTER | load_counter and branch_tgt_rows |
| cmd_AE2E_CALL_WILTEL_NET_DIS_PULL | wilnet_call_placeholder, called workflow not implemented |
| eml_on_SUCCESS | email_dep_failed, send-capable code with runtime mail configuration |

## Variables and necessary runtime mappings

- $$SUPPORT_LIST: recipient runtime parameter support_list.
- $$INFA_ENV: subject environment runtime parameter infa_env.
- $$INFA_DOMAIN: used by the excluded Informatica launch. It must not create a
  Databricks child binding in a non-executing single-workflow placeholder.
- $$AE2E_SCHEMA: referenced by target operations. A qualified target_table is
  needed; the source table candidate is not evidence of the correct target binding.
- Session TgtSuccessRows: task output used by the native condition, not a user
  supplied parameter. Its value must come from the main append, not post-SQL count.
- System start/end/status/error fields: not automatically ordinary Job parameters.
  Every actively consumed system field still requires a documented disposition.
- Source/target/log path and connection references: platform configuration or
  identity mappings must be assessed separately, not copied as literal runtime tokens.

The five logical parameter declarations are bronze_source_table, target_table,
support_list, infa_env and mail_settings. The email notebook exposes individual
SMTP widgets; a declared aggregate object does not prove those widget names are
wired correctly in an executable Jobs definition. That wiring remains unverified.

## Actual reviewers and limitations

The configured validation-role model independently reviewed writer code. It recorded
review_ready for email_dep_failed, load_counter and wait_5min. branch_tgt_rows had
incomplete canonical source retrieval; wilnet_call_placeholder had an unavailable
or invalid scoped provider review. All six analysis roles recorded completed outputs,
but none established that no business logic or necessary variable was missed.

Operational call counters, schema acceptance and task coverage must not be reported
as measured groundedness, schema-linking accuracy or runtime correctness. The
formatted app report shows original task dispositions, variable/reference inventory,
logical parameter mentions, widget presence and actual AI-review issues separately.

## Native Job YAML correction

The builder previously required a condition task to own a separate notebook or module,
although its implementation is its structured condition and branch dependencies. The
corrected builder permits an empty condition artifact list and retains exact operand,
branch, upstream notebook and published-key checks. A read-only check against these
same saved v25 bytes now produces five native tasks and four notebook mappings, plus
eleven widget-derived Job parameters. An intentionally unpublished upstream metric
still fails validation. This does not resolve missing source evidence, establish that
all necessary parameters are translated, or certify runtime parity. Saved historical
designs, notebooks and manifest receipts were not rewritten.
