# Ten-workflow migration lineage, local implementation 2026-11-06

## Delivery and authority

**Implemented locally, not deployed. Zero independently verified business migrations.**
The existing [pre-deployment gate](request-reconciliation-2026-11-06.md) remains
blocked. This work does not change storage, models, business Jobs, databases,
catalogs, environment flags, four pinned wrapper artifacts or approval gates.

- [Read-only backend and exclusive export CLI](../workbench/migration_lineage.py)
- [Interactive SVG client](../workbench/static/migration-lineage.js)
- [Scoped styling](../workbench/static/migration-lineage.css)
- [Full linked JSON report](../output/workbench/ten-workflow-lineage-20261106-v1/lineage.json)
- [Report manifest](../output/workbench/ten-workflow-lineage-20261106-v1/manifest.json)
- [Original ten-name shortlist, preserved](../output/workbench/validation-shortlist-20261106-v2/shortlist.json)

The complete report is **550,070,885 bytes**, approximately 525 MiB. Prefer the
paged API for exploration rather than loading the entire report into a browser.
SHA-256: `7900d9b055644aefae0f3b7fc20c4f460e82a9d9480520a0f9aa0784d94906dd`.

It contains each linked canonical record in a normal-form object inventory:

| Inventory | Exact count |
| --- | ---: |
| Canonical objects | 68,134 |
| Canonical edges, including field/control/data connectors | 221,894 |
| Field-definition records | 32,055 |
| Property records, including SQL phases/expressions | 28,361 |
| Strict literal invocation sites | 31 |
| External, dynamic, unverified or unresolved obligation records | 6,914 |
| Invocation-cohort workflow definitions | 32 |
| Requested selected business definitions | 10 |

Obligations include captured-but-unbound SQL as well as missing evidence. They
are not 6,914 missing tables. Canonical connector completeness is scoped to the
saved input and cohort. **Semantic/physical/external SQL lineage is incomplete.**
External procedures such as AE2E_COMMON.GET_PRODUCT_KEY_ID, supplied parameter
files, dynamic SQL, scheduler routing, transactions and original output snapshots
must be resolved independently. SQL text is retained as exact linked property
evidence, not regex-promoted into fictitious table or procedure dependency edges.

## Input version, not a fresh hosted activation

XML/package SHA-256:
`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.
Saved understanding SHA-256:
`51c17512fbf769c5185af3a3847b9f7fa0d39c3fbea588bad3a7bd5be6de5d7e`.
Source-analysis overlay: null in this local saved package.

The XML hash and ten canonical IDs match the existing shortlist, but that
shortlist uses a **fresh canonical parse**. This export intentionally uses the
selected saved canonical artifact. Its contract hashes and source counts are
not substituted for the fresh analysis. In particular WIL NETWORK DISCO has
four resolved source definitions here, versus nine in the fresh campaign.

The full [v2 candidate manifest](../output/workbench/wil-net-dis-parent-child-source-v2/manifest.json)
was checked, including all 79 referenced artifact byte sizes/hashes. Its references
are recorded separately as **offline candidates**. Same package membership and
matching artifact bytes do not establish current selected-understanding freshness,
full-session conversion, live Job correspondence or execution. No offline export
folder is imported as hosted authority. The new canonical-only API does not
read Jobs or campaign tables, so absent receipts in this projection are not
classified as remotely deleted resources.

## The ten definitions and exact source dependencies

All names are in repository PCREPO_PRD1_01, folder B_AE2E. The exact IDs are in
the linked report and the [existing ten-ID ledger](request-reconciliation-2026-11-06.md).
Counts below are per selected workflow's saved definition closure, not unique
physical tables, completed migrations or runtime invocation totals.

Every row has `runtime_status=NOT_RUN`, no verified full conversion, no assigned
destination catalog/schema/table and a **Not generated** Lakeflow outline.
There are no invented destination Job names or source-to-Job correspondence edges.

| # | Exact workflow name | Tasks / sessions / mappings / sources / targets | Direct literal callers, all nowait | Additional caller |
| --- | --- | --- | --- | --- |
| 1 | wkf_Load_AE2E_WILTEL_NETWORK_DISCO | 15 / 9 / 6 / 4 / 8 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL_WIL_NET_DIS |
| 2 | wkf_LOAD_AE2E_AOB_PREP_PULL | 12 / 7 / 5 / 7 / 7 | wkf_LOAD_AE2E_MASTER_PREP | None captured |
| 3 | wkf_LOAD_AE2E_BW_DISC_PREP_PULL | 21 / 13 / 8 / 9 / 10 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL_DISC |
| 4 | wkf_LOAD_AE2E_BW_HIST_PREP_PULL | 21 / 13 / 8 / 9 / 10 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL |
| 5 | wkf_LOAD_AE2E_BW_INST_PREP_PULL | 21 / 13 / 8 / 9 / 10 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL_INST |
| 6 | wkf_LOAD_AE2E_FUSION_PREP_PULL | 6 / 3 / 3 / 5 / 5 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_FUSION_DEPENDENCY_LOOP_CALL |
| 7 | wkf_LOAD_AE2E_IOB_PREP_PULL | 9 / 5 / 4 / 6 / 6 | wkf_LOAD_AE2E_MASTER_PREP | None captured |
| 8 | wkf_Load_AE2E_VYVX_HIST_PREP_PULL | 9 / 5 / 4 / 4 / 6 | wkf_LOAD_AE2E_MASTER_PREP | None captured |
| 9 | wkf_Load_AE2E_WILTEL_CUSTOMER_DISCO | 24 / 15 / 9 / 8 / 11 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL_WIL_CUS_DIS |
| 10 | wkf_Load_AE2E_WILTEL_NETWORK_INSTALL | 15 / 9 / 6 / 4 / 8 | wkf_LOAD_AE2E_MASTER_PREP | wkf_AE2E_DUMMY_CALL_WIL_NET_INS |

None of these ten definitions has an outward literal child invocation in the
captured source. Their callers are outside the ten selections but remain in the
32-definition cohort. Invocation candidates are **not execution ordering,
completion dependencies, data readiness or evidence that an object is active**.
Shared definitions do not imply a runtime dependency.

### Master dependency retained even though not selected

Exact source parent: wkf_LOAD_AE2E_MASTER_PREP,
ID `ac197951c36607afcda14ce4b2adfa4c7dbc789461304980c0e6996a4095e0e1`.
It has **19 distinct outward literal sites**, all `nowait`: the ten above plus
these nine source definitions. No completion DAG is invented:

| Additional child | Canonical ID |
| --- | --- |
| wkf_LOAD_UNITY_AE2E_NET_INS | d47e1f8e52436e6d6b492eb51678b1e7d45b6beca6b0de03bc7660f749952a45 |
| wkf_LOAD_UNITY_AE2E_CUS_DIS | d498c18472eca8b7556431febaac3629fcc80f84175bdcfdd43a7b58f4d6d0a2 |
| wkf_LOAD_UNITY_AE2E_CUS_INS | 135ac25989173c3a8c60d099a1a78a466dbe00d14c6104dfe21b6f561ceb8749 |
| wkf_LOAD_UNITY_AE2E_NET_DIS | 8cb8c41bb89aebe47ec0d4a580b7afe666448c020b5fe0fd1d9d1460828223e4 |
| wkf_Load_WILTEL_FGD_DISCO | 5848ede1b064c385b9fd1f529a4d37218a0bc963af451918de498743d9714ac9 |
| wkf_Load_WILTEL_FGD_INSTALL | 2d2bc36036288aaac068120d86ecdb37d453223cf3d0116950246d3955495df7 |
| wkf_LOAD_UNITY_AE2E_ONNET_CUSINS | 614a16a3acd534d9c34a7fca332d5190c20d134417211b5fd55859e46f45f074 |
| wkf_LOAD_UNITY_AE2E_ONNET_CUSDIS | 59e30f12f39a6efcab691330f0a167b9bdaec32b2207d0df75bb4ddb611d41d2 |
| wkf_Load_AE2E_WILTEL_CUSTOMER_INSTALL | 59faa00f588bd2a82ad1a677ed15bfaa265404d5ed2fd5a24283af98c9162897 |

### Output definitions, not created or bound destination tables

Each row also includes the shared source target definitions AE2E_UNIX_JOB_LOG,
AE2E_JOB_LOG, TARGET_DUMMY and dummy_tgt. These are control/log/placeholder
obligations, not completed business outputs. Additional target definitions:

| Selected rows | Additional exported target names |
| --- | --- |
| 1 and 10 | AE2E_AM_OFFNET_MGR_PREP, AE2E_AM_OPENCI_PREP, AE2E_ODW_PREP, AE2E_AM_BILLTRACK_PRO_PREP |
| 2 | AE2E_XLINK_CV_PREP, AE2E_ODW_PREP, AE2E_AMT_PREP |
| 3, 4 and 5 | AE2E_ADC_KENAN_PREP, AE2E_BILL_TAMER_PREP, AE2E_XLINK_CV_PREP, AE2E_REVAMART_PREP, AE2E_KENAN_PREP, AE2E_IXPLUS_PREP |
| 6 | AE2E_FUSION_ORDER_PREP |
| 7 | AE2E_NEXTGEN_PREP, AE2E_ENDSTATE_GRANITE_PREP |
| 8 | AE2E_BILL_TAMER_PREP, AE2E_VBUS_PREP |
| 9 | AE2E_AM_SIEBEL_PREP, AE2E_AM_OFFNET_MGR_PREP, AE2E_REVAMART_PREP, AE2E_AM_OPENCI_PREP, AE2E_ODW_PREP, AE2E_AM_BILLTRACK_PRO_PREP, AE2E_KENAN_PREP |

## Browse locally and API continuation

With the newer local application started, choose the completed project/package,
open **Readiness**, then click **Load migration lineage**. Loading is explicit
and read-only. It does not restart the app, run a model or restore all state.
The currently running process was not restarted by this work, so a coordinated
restart/reload may be needed to expose the new route and asset includes.

Click a workflow chip or a caller/child in the invocation map to focus its exact
source dependencies. Click/keyboard-expand canonical objects to traverse
workflow, task instance, session definition, mapping, table and field hierarchy.
Use the full inventory selector for exact control conditions, SQL/property
phases, field records, invocation evidence and unresolved obligations. IDs are
collapsed in details, not discarded.

- Gray: captured source. Purple: proposed outline.
- Blue is reserved for hash-verified generated correspondences, none claimed by
  this canonical-only projection. Green requires independent execution proof,
  none currently present. Red denotes missing/ambiguous evidence.
- The optional slow 9-second animation applies only to selected returned
  canonical edges. It represents source exploration, never running/migrated.
  Reduced motion, page hiding, document hiding, busy state or Stop stops it.
  Buttons do not pulse and inherited button transitions are disabled locally.
- Zoom in/out, Fit/reset, +/-, arrows, Home/0, Tab and Enter/Space are supported.
- One-hop SVG limits are disclosed with totals. Full records are continued,
  not silently truncated. Source invocation candidates are drawn separately
  and never animated into purported completion edges.

`GET /api/readiness/lineage` accepts `workflow_id`, `root_id`, `family`, `offset`,
`limit` (1 to 200) and `input_version`. Families: nodes, edges, fields,
properties, invocations, obligations. The response includes exact input pins,
global cohort totals, filtered page totals and `next_offset`/`has_more`.
Subsequent requests must send the returned `input_version`; changed inputs
return 409. Every hosted page re-enters existing fresh OBO membership/envelope
verification and reads only the selected canonical artifacts. No full restore,
SQLite authority, local fallback, model routes, Jobs or campaign table is needed.

The exclusive export command actually executed was:

```powershell
.\.venv\Scripts\python.exe -B -m workbench.migration_lineage --package-directory output/workbench/packages/08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba --output output/workbench/ten-workflow-lineage-20261106-v1 --shortlist output/workbench/validation-shortlist-20261106-v2/shortlist.json --source input/LumenExample.XML --artifact-export output/workbench/wil-net-dis-parent-child-source-v2
```

Do not replay against that existing output directory. Any new export requires
a new versioned directory; existing files are deliberately never overwritten.

## Consolidated regression and remaining limits

Final identical seven-file batch: **81 passed, 3 warnings, zero failures/errors**
in **105.45 seconds**. No skips or known-failure deselection. Exact command:

```powershell
.\.venv\Scripts\python.exe -B -m pytest workbench/tests/test_migration_lineage.py workbench/tests/test_readiness.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_package_context.py tests/test_migration_lineage_ui.py tests/test_readiness_ui.py tests/test_navigation_prerequisites.py -o addopts= -q --disable-warnings -ra
```

The first batch had 76 passes, 3 failures and 2 setup errors. Root causes were
new test assumptions: missing small_xml fixture import, guessed master name
with an incorrect PULL suffix, and inherited button CSS transitions counted
as animations. The fixture/name were corrected against actual source evidence;
only the new lineage section's button transitions were disabled. The entire
same batch was rerun green, not just failing assertions.

Tests cover actual saved Lumen ten IDs, master 19 outward calls, exact unchanged
canonical records and field/control edges, all-family continuation, wrong/stale
scope rejection, read-only API, exclusive export and manifest hashes, fresh
hosted scoped projection without full restore/publication/Jobs reads, desktop
and mobile SVG interaction, keyboard, reduced motion, late responses and
existing Readiness/navigation guards. JavaScript syntax and Python compilation
also passed. One additional **offline fixture-only** desktop screenshot was
visually inspected. No live hosted/browser acceptance is claimed.

This is not a clean full-project regression claim. The lineage regression did
not cover the actual Lumen campaign persistence failure at the unchanged 48 KiB
Delta row bound. The later [Option B follow-up](request-reconciliation-2026-11-06.md#approved-option-b-immutable-full-campaign-artifact-and-small-delta-reference)
records its local resolution and separate regression, without raising the bound.
Runtime validation tables unavailable to the current identity are
unknown, not presumed absent. The prior four-workflow gate remains 0/4, and no
business Jobs, catalogs, targets, models, production rollout or deployment were
performed. Coordinate deployment only after the governing gate is satisfied.