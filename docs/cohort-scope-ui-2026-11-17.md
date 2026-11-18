# Cohort scope UI verification, 2026-11-17

## Requested correction

The two visible Design scope dropdowns were synchronized copies, not two decisions.
The source-target-scope control is now the sole visible selector. The existing
agent-review-scope value/event bridge remains hidden for compatibility. Both use
Individual workflow / Cohort. Cohort action, reconnect, saved-design and dialog
labels use cohort terminology. Internal API values remain workflow / cohort.
The repeated Design scope heading was removed from the visible section.

## Verification

- JavaScript syntax: node --check workbench/static/redesign-review.js passed.
- Editor diagnostics: no errors in the modified JavaScript.
- Actual shared localhost Agent activity page reloaded, not an offline fixture.
- Exactly one scope control visible, hidden bridge retained, no horizontal overflow.
- Selected Cohort then Individual workflow through the visible selector. Both
  controls matched each selection. No agent invocation, scope save or model call.
- Actual scope section screenshot inspected, one Design scope label and selector.
- Cohort backend regressions: 6 passed, 3 failed, 2 warnings in 59.27 seconds.
  Command: .venv/Scripts/python.exe -B -m pytest -c workbench/pytest.ini
  workbench/tests/test_cohort_review.py workbench/tests/test_cohort_design_actions.py -q --tb=short
- Failures: completeness expected blocked but returned incomplete; two action
  tests did not observe their fake runtime finishing within 10 seconds. These
  failures are not diagnosed as caused by this UI change or as pre-existing.
  No backend logic, budgets or assertions were changed to suppress them.

## Existing distinct-workflow results

See full-package-bootstrap-local-verification-2026-11-16.md for exact immutable
run IDs, timestamps, model counts, artifact hashes and quality limitations.
The three individual runs were tblcircuits_temp (26m05s), SRC_EWH_CKT_TRACKING
(24m59.8s), and SRC_FED_CUSTOMER_SRC (23m39.7s). All saved notebooks and native
YAML but reported needs_information. They were not cohort runs and are not
proof of final post-correction semantic success or a consistent speedup.

## Planning and next cohort test

agents.py constructs create_deep_agent graphs with tools, skills, evidence and
typed submissions, backed by checkpoints. This is a ReAct-style model/tool/result
iteration, not a separately configured framework named ReAct or a claim that
private reasoning is exposed. Structured logical design precedes the separate
code writer and review/native-parameter checks.

After satisfactory individual-workflow results and resolution of cohort regression
failures, use the same selected workflow as the cohort anchor, inspect and save
the exact member set, then explicitly invoke. Compare member coverage, boundary
semantics, source/target bindings, notebook ownership, parameters, YAML, review
status and total/per-workflow cost and elapsed time. No cohort run or deployment
was started for this UI correction.