# Capability inventory and reference resolution, 2026-11-09

## Scope and status

Local implementation only. No deployment, restart, live model inference, Jobs,
business SQL, fixture provisioning or business-data mutation is authorized by
this follow-up. The required agentic-blueprint notes and PDF remain absent.
The [proposal-first design](proposal-first-deepagents-lakeflow-plan.md) was read.

## Ask ledger

| Ask | Local implementation | Evidence / boundary |
| --- | --- | --- |
| Explain six cards and the reduced count | Separate complete versioned inventory from legacy flags | The health API has six legacy support/authentication keys. The overview has a broader inventory. The current renderer does not filter to six keys, but a health-only response before overview completion can display just six. This was inconsistent reporting, not proof features were removed. |
| Compact cards and honest denominator | 15 scoped implementation groups, 14 implemented and one configured/unverified | A count of presentation groups, not passed gates, verified runtime features or conversion coverage. Request identity and inference are one group, not two feature counts. |
| Correct assessment date | UTC assessment timestamp supplied by the server response | Removed the hardcoded 28 October deployment label. Assessment time is explicitly not deployment time. |
| Separate unfinished roadmap | General semantic conversion, controlled customer release, archive intake, governed learning | Scoped SQL drafts and pinned compilation do not promote general translation or release to available. Existing dbt/provider and delivery requirements remain. |
| Trace unresolved references rather than populate dummy data | Fresh parse of the actual unchanged Lumen XML, compared with immutable saved analysis | Same source SHA-256, all 243 workflows inspected independently of fixture data. |
| Fix canonical producer | Session override resolution follows its owning task instance to the exact session, shortcut and mapping after forward definitions resolve | Case-sensitive identities, ambiguity, literal qualifiers, disabled state and approval gates are retained. |
| Explain missing vs non-applicable sources | Separate source-definition blockers, connection metadata gaps, SQL and child scopes | Command-only callers have bounded child-review controls. No child metadata or data is read automatically. |
| Trace SQL and dynamic dependencies | SQLGlot static table extraction excludes CTE aliases and retains qualification | Unqualified identifiers need schema evidence. Parameterized/redacted SQL, Java expressions, unsupported SQL and missing procedure bodies remain unverified. No missing-table claim or canonical edge is fabricated. |
| Preserve history and runtime boundaries | Read-only diagnostic projections; no automatic artifact replacement | Saved and hosted canonical analyses are not silently upgraded. No models, Jobs or business data were touched. |

## Actual reference investigation

The source is [input/LumenExample.XML](../input/LumenExample.XML), SHA-256
`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.

Before this correction:

- The saved analysis had 167,120 nodes and 5,081 unresolved-reference nodes:
  1,848 instance references, 2,920 historical withheld connection references,
  and 313 source/shortcut references. These are historical analysis records,
  not 5,081 absent physical tables.
- Of 243 saved workflow definitions, 188 reached direct source definitions,
  44 had no direct sources and 11 were reported as having no resolved sources.
- A fresh current-parser analysis of the identical XML already reached sources
  for 199 workflows, with 44 having no direct source definitions. It left 56
  unresolved instance references. All 56 were session-extension overrides.
- Exact examples include ORDER_TYPE_DICT1 beneath
  wkf_Load_SRC_EWH_ORDER_TYPE_DICT and T012K_UPDATE beneath
  wkf_Load_AP_INVOICELOG_SST2. Their XML owners are TASKINSTANCE elements, not
  SESSION elements. The previous resolver read the enclosing workflow's
  container ID as a session ID and searched no mapping. This is a producer bug,
  not absent fixture data or proof the definitions are missing.

The producer fix defers session overrides until task and shortcut resolution is
complete, follows only the exact resolved owning session/mapping chain, and
retains unresolved/ambiguous state when that chain cannot be uniquely resolved.
It does not invent fallback matches by case, bare names or fixture identity.

The exact user-reported phrase “no references resolved” was not found verbatim
in the current workspace text. Related source-empty and unresolved-definition
messages were traced to their actual producers instead of assuming they shared
one cause. The UI now exposes causes and required evidence rather than implying
empty mock data caused failed XML resolution.

## Historical artifact boundary

The immutable saved analysis is deliberately unchanged. Re-uploading identical
source bytes currently reuses its existing content-addressed artifact; it does
not itself activate this parser fix. The existing Java source-analysis overlay
is also not a canonical-reference rebuild. A separately versioned canonical
re-ingestion/activation path is still required to replace an already-saved
canonical model without rewriting historical evidence. This change does not
claim such an activation occurred locally or on the hosted app.

The [architecture UC receipt](architecture-knowledge-uc-receipt-2026-11-09.json)
is historical user-profile readback evidence, not newly verified hosted OBO.
No additional live metadata call was needed for this source-resolution defect.
No mock resources were created: the missing input was canonical resolution,
not source rows. Existing mock execution results are not changed or promoted.

## Verification

All code and test edits precede one consolidated regression pass. The pass
covers capabilities, roadmap/layout, parser qualifiers, actual full Lumen,
reference/SQL evidence, fixture metadata, package graphs/context, hosted
projections, pinned compilers, validation contracts and architecture knowledge.
Exact command and results are recorded below after execution. No deployment is
part of this pass.

### Final measured result

- Python AST: **142 files passed**.
- JavaScript syntax: both changed scripts passed `node --check`.
- Consolidated regression: **812 passed, 1 warning, 729.11 seconds**.
- The first identical consolidated run had **811 passed, 1 failed, 1 warning**
  in 734.55 seconds. Its only failure expected the old “Targets unresolved” UI
  text. The assertion was aligned to “Target bindings unapproved” and strengthened
  to verify zero source-definition blockers and no source-data-validation claim.
  No production code was changed after that first run. The entire batch, not
  just the failed test, was repeated for the final green result.
- The real full-Lumen regression confirmed **zero fresh unresolved canonical
  references**, **199 workflows with captured direct source definitions**, and
  **44 without direct source definitions**, out of **243 workflows**. The saved
  analysis SHA-256 remained unchanged. This does not resolve runtime parameters,
  external connection metadata, dynamic SQL, procedure semantics or physical
  production bindings, and does not establish data/output parity.
- Offline Chromium verified the real card renderer at 1440, 1024 and 390 pixels,
  including dark/light contexts, 4/3/1-column layout, 13px purpose text,
  borderless scoped cards, collapsed full limits, exact inventory keys/counts,
  live assessment time, separate roadmap and no generation/deployment requests.

Exact regression command executed from the workspace root (PowerShell):

```powershell
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_capability_status.py workbench/tests/test_reference_diagnostics.py workbench/tests/test_app.py workbench/tests/test_understanding.py workbench/tests/test_qualified_resolution.py workbench/tests/test_explanations.py workbench/tests/test_package_context.py workbench/tests/test_migration_planning.py workbench/tests/test_source_evidence.py workbench/tests/test_source_analysis.py workbench/tests/test_hosted.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_workflow_artifacts.py workbench/tests/test_workflow_artifacts_api.py workbench/tests/test_package_parity.py workbench/tests/test_demo_conversion.py workbench/tests/test_validation_contracts.py workbench/tests/test_validation_campaigns.py workbench/tests/test_campaign_artifacts.py workbench/tests/test_architecture_knowledge.py workbench/tests/test_knowledge_store.py workbench/tests/test_ui_structure.py tests/test_fixture_metadata.py tests/test_package_context_visible_ui.py tests/test_explorer_graph_ui.py tests/test_readiness_ui.py tests/test_vyvx_mock_demo.py -q --tb=short --disable-warnings -ra
node --check workbench/static/app.js
node --check workbench/static/redesign-review.js
```

No live hosted verification or active canonical migration was performed. No
remote resources, mock/control tables or business rows were created or changed.

Final editor diagnostics were clean for the checked implementation modules and
both changed JavaScript files. The existing metadata test module still reports
four type-checking issues in unchanged fixture code: inferred string identity
modes, a SimpleNamespace supplied to the credentials mock, and a SimpleNamespace
heartbeat mock. The new assertion lines have no diagnostics. These fixture type
issues were not altered to conceal them; all tests in that module passed.