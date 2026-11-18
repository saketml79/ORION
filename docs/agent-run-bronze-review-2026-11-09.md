# Bronze agent-run review, local changes only

## Evidence boundary

Requested historical run: `16276644ad254af8b76992cc91ce9edc`, WILNETDIS bronze.
The actual exported receipt is not present in this checkout. Reported 23 calls,
419,278 tokens, 1,797 connection notices and 2,527 unsupported-control notices are
supplied historical observations, not a new inference run or newly verified totals.
An available local Lumen saved canonical artifact has different counts and must not
be substituted for that run. Required agentic-blueprint files/PDF are absent.
The existing proposal-first design and role skills were consulted instead.

## Four scoped corrections

1. **What unresolved means.** Raw canonical diagnostics remain immutable evidence.
   [Typed relevance](../workbench/diagnostic_relevance.py) examines the exact owner,
   unresolved node kind, expected kinds and reference status. Connection-only notices
   require human binding review. Bronze needs approved UC bindings, not a historical
   Oracle connection. Missing session/mapping/source/field evidence remains an
   affected-task gap. SQL adapter limitations describe application implementation,
   not proof that the XML source definition is missing. No parser rebuild/activation
   occurred and no old warning is declared resolved. New diagnostic blockers require
   role-local citations and exact typed scope/reason.
2. **Repeated findings and knowledge.** Same-role identical submissions are idempotent
   in new runs, without suppressing actual model/tool accounting. Historical display
   collapses only identical findings within a role, preserves different evidence and
   raw downloads, and shows the latest recorded role status. Retrieved architecture
   facts and explicitly cited facts are separate counts, neither proves optimization.
   Irrelevant GA citations are not required.
3. **Real design handoff and artifact boundary.** Run detail verifies persisted design
   references against package, workflow, scenario, exact input version and source run.
   Review submitted design opens that exact version, not structural candidates or a
   newer unreviewed version. Missing linkage is explicit. Existing scope/consent guards
   remain. Logical conditions may remain optional for review; missing structured
   operands or notebook .py versus writer .ipynb mismatch is separately reported as
   outline amendment required before claiming affected artifacts are drafted. No
   executable task or operand is invented and no historical review is changed.
4. **Loading is not rerunning.** Ordinary selected hosted agent-design list/detail
   reads use the existing fresh-OBO, committed-generation canonical reader and scoped
   design/review/proposal/document evidence. List output is compact. They do not
   restore agent history, Jobs, model routes, audit history or XML. Reads still verify
   current authority and may download pinned canonical evidence; no instant response
   or hosted latency improvement is claimed without deployment. The subsequent
   [stored-design read correction](stored-redesign-read-incident-2026-11-09.md)
   supersedes the initial loop fallback: verified scoped lessons now reproduce
   the existing ordered lesson hash without full restoration. This remains local.

## Validation ledger

All changes were assembled before the consolidated regression. Python AST and three
JavaScript syntax checks passed after correcting two inserted indentation mistakes.
The first pytest command used an incorrect Spark-test path and ran zero tests.
The actual first consolidated pass: 312 passed, 5 failed, 1 skipped, 6 subtests passed,
1 warning, 516.67 seconds. One new test accidentally contained an assertion displaced
from an existing helper; that assertion was restored to the helper, not removed.

Four remaining failures were the non-initial refresh cases in
[selection regressions](../tests/test_proposal_selection_sync.py). All four were
reproduced with the recorded pre-change app script supplied in memory from VS Code
editor history. Current files were unchanged during baseline replay. These fixtures
find Projects active when clicking global Refresh, so no proposals read starts.
Unrelated navigation/control flow was not changed and assertions were not weakened.
The final scoped gate explicitly excludes only these four baseline-proven cases.

Final consolidated command:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_run_review_fixes.py workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_runtime_planning.py workbench/tests/test_redesign.py workbench/tests/test_redesign_ui.py workbench/tests/test_agent_arena.py tests/test_spark_codegen.py tests/test_spark_codegen_api.py workbench/tests/test_readiness.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_hosted_agent_reads.py workbench/tests/test_hosted.py tests/test_proposal_selection_sync.py -k "not (test_current_top_selection_is_applied_when_candidate_fetch_completes and False)" -q --tb=short --disable-warnings -ra
```

Final consolidated result: **314 passed, 1 skipped, 4 deselected, 6 subtests passed,
1 warning, 624.86 seconds**. The skip is the existing Windows symlink permission
case. All new diagnostic, idempotency, exact-version browser handoff, duplicate-role,
artifact-boundary, hosted no-restore/current-hash and fresh-scope tests passed.
Final AST validation covered 12 changed Python/test modules; all three changed
JavaScript scripts passed `node --check`. No production/test edits followed this
passing gate. This is a scoped regression result, not a clean full-suite claim.

No deployment, model inference, historical
record mutation, Jobs invocation, real catalog/table writes, parser activation or
authorization change is part of this correction.