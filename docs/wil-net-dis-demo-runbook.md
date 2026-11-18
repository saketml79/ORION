# WIL_NET_DIS demonstration: exact scope and sequence

Updated: 2026-10-30. Live results below refer to the recorded boundary run, not a
fresh execution of the current application or proof of general migration parity.

## What exists

The original XML workflow `wkf_AE2E_DUMMY_CALL_WIL_NET_DIS` has a constrained,
scope-hash-checked compiler in the application. This is a deterministic conversion
pattern, not a generic LLM compiler. It rejects a different workflow or altered
unsupported semantics instead of emitting plausible code.

The Agentic Migration Proposals page has **Generate an isolated conversion example**.
It requires the selected package to contain the original workflow and explicit
approval of the test replacements. It reparses the original XML using the current
analyzer, without replacing historical analysis or reviews.

Enter:

- Catalog: `infotodbxmig_src_dev_ssuman`
- Schema: `orion_demo_wil_net_dis`

These are deployment/demo bindings, not constants in the generic application.
The isolated example itself is deliberately restricted to this inspected pattern.

## What to show

1. Select the package containing the workflow.
2. Open **Agentic Migration Proposals**, then the isolated example section.
3. Review the disclosure and approve artifact drafting with test replacements.
4. Generate the example. Inspect proposal coverage and declared gaps.
5. Inspect actual dependency links, native DAB YAML, canonical notebook JSON,
   Databricks Python notebook source, exported tests, and content hashes.
6. Open the deployed Jobs in Databricks and inspect their task graph and run result.

The main workflow preserves default wait=300 seconds. The boundary test overrides
wait=0 for testing and supplies fixture counts 34 and 35. This is disclosed, not
presented as proof of elapsed-time behavior.

## Deployed unscheduled resources

- Counter/control Job: 807302179770738.
- Test child Job: 597876252469608.
- Boundary suite: 179594372598801.
- Initial live boundary run: 1084134626224066.

Workspace: https://adb-1866518241053589.9.azuredatabricks.net

All three Jobs are created by DAB with the current user as run identity. No schedule
is generated. The boundary run is a manually requested isolated test, not an enabled
production schedule. DAB development mode can add its own user/environment display
prefix; that is separate from the ORION application's neutral resource name.

## Do not claim

- Full conversion of the original network-disconnect child workflow. It remains
  unconverted; the demo invokes a clearly named test child.
- Actual email delivery. The email action writes a mock result for verification.
- Production source connectivity, runtime data equivalence, or a generic package
  conversion engine.
- AI authorship of the deterministic pattern compiler. Agent review and execution
  are separate, and the arena animates actual recorded run state only.
- Full child completion from successful asynchronous dispatch alone.
- GitHub publication. No authorized repository was supplied for this demo.

## Runtime skills and agent visualization

Existing advisory agents have private role context and progressively disclosed
read-only skills under the workbench skill libraries. Additional Lakeflow pattern
skills accompany the demo compiler. Skill files alone do not enable new tools or
grant release authority.

The agent arena receives real polled agent/loop records. Its outer ring can animate
while a run is queued/running; specialist findings are shown only when recorded.
No animation claims a particular tool or agent is active without telemetry.

## Verified live boundary result

[Boundary run 1084134626224066](https://adb-1866518241053589.9.azuredatabricks.net/jobs/179594372598801/runs/1084134626224066?o=1866518241053589)
terminated **SUCCESS**, from **2026-10-30 06:54:44.802 UTC** to
**2026-10-30 07:19:22.867 UTC**.

| Fixture | Counter/control run | Threshold result | Observed branch |
|---|---|---|---|
| 34 rows | 282957826607790 | false | Test-child dispatch and dispatch verification succeeded |
| 35 rows | 624974305622156 | true | Mock email and email verification succeeded |

Each case recorded seven successful tasks and two excluded tasks. The figures 34
and 35 exercise the XML predicate `TgtSuccessRows >= 35`. They are successful-row
counts, not established dependency counts or model scores. Counter/post-SQL checks
are implemented in the generated verification notebook.

### Notebook output limitation

The verification tasks succeeded but their returned `notebook_output` objects were
empty. Their assertions and task-value writes do not automatically render visible
tables or return an exit result. Open the **Job run task notebook**, not only the
workspace source notebook. Visible result summaries remain a demonstration UX gap;
do not imply they have been added. Actual email and original child execution remain
outside this example's scope.

### Current UI and validation scope

The proposal page exposes the example's actual artifacts and dependency list.
The separate agent-design reviewer displays logical graphs, rationale, coverage,
parameters and version-checked inline review actions. A unified completion-to-review
modal and general approved-design artifact factory are not complete.

Arena cards now use non-overlapping responsive grid positions. The hub, active-run
orbit and illustrative hill path animate only for queued/running records. Per-attempt
rubric counts are actual recorded checks, not measured semantic improvement.
LangChain/LangGraph labels describe installed execution components; LangSmith is
explicitly marked **Tracing not enabled**. No tracing integration is claimed.