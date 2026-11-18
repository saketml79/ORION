# Conversion acceptance, 2026-11-10

## Combined implementation regression, 2026-11-11

After the implementation edits and regression corrections, the final 20-module
batch completed with **564 passed, six subtests passed, one Windows symlink
permission skip, one warning, and zero failures/errors**, in 1051.43 seconds.
The external JUnit result is `orion-production-contract-verified-20261111.xml`
in the user's temporary directory. The scope includes production translation
contracts, actual canonical ten-workflow/two-cohort planning, agent/writer
integration, scope/provenance checks, original-workflow navigation, and clickable
native task cards with notebook content and byte-exact YAML downloads.

These are offline regression and scripted-provider results. **Ten live
agent-generated conversions and two live cohort executions were not performed.**
The trusted production Provider and independent execution evidence remain
unimplemented/unavailable. No application deployment or live inference was
performed in this pass. Earlier failed runs are not counted as passing proof.

**Internal report instrument, not an execution actor or production certificate.**
Implemented in [conversion_acceptance.py](../workbench/conversion_acceptance.py).
No existing agents, writer, API, readiness, policy flags or deployment paths were
edited. Existing release gates remain blocked. Blueprint documents/PDF requested
by startup instructions are absent from this checkout.

## Exact requested scope

[Persistent request](../output/workbench/conversion-acceptance-20261110-v1/request.json)
preserves the existing ten canonical business-definition IDs. Its two source
selectors are PCREPO_PRD1_01/B_AE2E/wkf_LOAD_AE2E_MASTER_PREP and
PCREPO_PRD1_01/B_AP_INVOICELOG/wkf_Load_AP_INVOICELOG.
The saved canonical English inventory and XML literal calls identify the latter,
including SST2, DLT2 and Generate_Parameter_File. No second seed in the AE2E
component qualifies as a second cohort.

`materialize_request(model, request)` resolves unique repository/folder/name
selectors, then `build_plan(model, workflow_ids, cohort_seeds)` recomputes actual
`PackageContext` components, invocation sites, source contracts and obligations.
It requires exactly the existing ten IDs, actual wired business classification,
two disjoint nonsingleton components and complete literal invocation resolution.
The expected shape is **32 + 4 = 36 distinct members**, not ten plus two wrappers.
All 36 members, including master/control/placeholder roles, require observations.
The request is explicitly **not materialized or run** during development.
No earlier fresh-parse contract hashes are substituted for saved/current inputs.

## Trust boundary and host handoff

`assess(plan, model, owner=Owner(...), provider=...)` is strictly internal.
There is no HTTP ingestion, raw attestation endpoint, signed-JSON shortcut,
default provider, network constructor, model invocation, SQL, Job submission,
repair, deployment, table lookup or business write. Without a trusted provider,
the result is `not_provided`, with zero verified workflows/cohorts.

The host must install `Provider` from its existing authenticated owner-scoped
storage/SDK boundary, after fresh delegated identity, commit receipt, manifest,
inventory and artifact authorization. `CommittedInventory` is a typed dependency
result, **not an object to deserialize from client JSON**. The loader resolves
only exact committed references and the gate checks byte size and SHA again.
The adapter must not promote model output or offline exports into recorder-owned
families. It must enforce independent recorder/reviewer permissions. Python
protocols cannot authenticate a malicious server-installed provider.

Required persisted record families, all resolved through that manifest:

| Families | Required correspondence |
| --- | --- |
| acceptance_item/member | Exact plan, full pins, member set and cohort edges |
| design, agent_run | Latest workflow/scenario/version, exact tool submission, agent source/runtime IDs |
| writer_manifest, writer_artifact | Exact design/input/approval digest and revision, artifact bytes, complete existing translation assessment |
| generation_provenance, model_call | Persisted agent_submitted origin, design/writer run IDs, separate transport-owned completed call receipts |
| target_selection | Current workflow source contract/input, design hash, approval and full selection hash |
| job_manifest, behavior_review | Exact writer/deployed artifact closure and independent source-preserving/dependency review |
| source_snapshot, expected_snapshot, reference_review | Immutable input identity, independently captured original engine outputs or separately authored literal expectations |
| actual_snapshot, comparison_case | Exact run, assets, target/input/expected/actual hashes, stages and observation coverage |
| cohort_execution, invocation_observations, cohort_reference | Every actual member run, exact canonical invocation sites and independent expected/observed launch behavior |

`current_source_pins` includes package/understanding/analysis, full input hashes
and versions, `model_sha256` and `workflow_inputs` indexed by canonical ID.
Each workflow entry contains current scenario, input version, input hashes and
versions, latest design ID/version and target-selection SHA. `_hashpins` hashes
the entire mapping, including additional identity fields, without dropping pins.
Authorization, manifest and full pins are checked again after aggregation;
changes invalidate all earlier observations and counts.

## Enforced evidence, not claims

- Exactly ten individual items and two complete cohort items must pass. Every
  canonical node, property, task, control and target obligation needs coverage.
- Expected rows never come from candidate execution. References need a separate
  review with an independent reviewer, matching source/pins and no candidate use.
- Reuses [compare_rows](../workbench/validation_data.py), the existing exact typed
  multiset comparator. [validation_metrics.py](../workbench/validation_metrics.py)
  is only a campaign projection, not the comparator. Schema, typed values, dates,
  nulls and duplicate multiplicity are checked, not counts/column marginals alone.
- Normal, null, duplicate, date and failure/recovery cases need completed stages;
  labels alone do not qualify. Normal cases need nonempty observations for every
  canonical business target. Only bounded case/count summaries leave the gate.
- Fresh read-only SDK `get_run`/`get` verifies exact IDs, creator/run-as identity,
  terminal success, complete successful tasks, task payloads and settings hashes.
  Workspace source exports must match **every** writer artifact SHA, including
  deployed modules. Tags, filenames and Job success alone cannot prove code.
- Unpinned libraries, non-workspace task sources and unsupported task types fail
  closed. The independent behavior review pins the whole dependency asset set.
  Adapters must use the exact writer export representation, not a lossy notebook
  re-encoding. Export mismatch is blocked, never normalized into success.
- A host-supplied `ValidationPolicy` restricts catalog identifiers and controlled
  schema prefixes. This observational check does **not** sandbox agent code or
  authorize deployment/business writes. An explicit execution path must enforce
  least privilege and namespace policy **before** it runs; none is added here.

## Validation and integration status

[Tests](../tests/test_conversion_acceptance.py) are authored offline unit/contract
tests, including canonical 10/32/4 planning, tampering and missing evidence,
single-member fake SDK checks, cohort aggregation seams and late identity changes.
Fake clients and stubbed aggregation are **unit tests only, never real E2E proof**.
No tests, CLI export, models, Jobs, deployments or remote calls were run during
development. Main owns the single final consolidated regression run and optional
explicit offline CLI plan export (`--model`, `--request`, `--output`, exclusive
new output). The CLI cannot execute or certify anything.
Editor diagnostics and static Python syntax checks passed for the new files;
this is not regression execution or acceptance evidence.

**Not wired into current readiness and no production Provider adapter supplied.**
Main must wire the internal function at the trusted read boundary and supply the
recorder/reviewer/export callbacks above. Missing records stay blocked, rather
than inventing them from legacy history. Even a fully passing evidence suite
always returns `production_certified=false`, `output_parity=false`,
`execution_allowed=false`, `deployment_allowed=false`. No real conversion,
accepted run, hosted authority, deployment or business parity is claimed.