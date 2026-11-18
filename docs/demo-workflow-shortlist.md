# ORION demo shortlist

Updated: 30 October 2026. Candidate inventory was verified against the complete
LumenExample.XML with the qualifier-aware analyzer on 29 October 2026.

## Demo boundary

The general candidates below remain understanding and advisory-design demos.
A separate constrained executable example now exists for
`wkf_AE2E_DUMMY_CALL_WIL_NET_DIS`: see the [tested demo runbook](wil-net-dis-demo-runbook.md).
Its generated notebook/DAB boundary suite succeeded in Databricks for 34 and 35
successful session rows. Email and the original child are test replacements, so
this is not full original-workflow equivalence or a generic package compiler.
The general engine cannot yet generate complete reviewed releases or publish GitHub changes.
Do not present any candidate as a completed migration. Do not hide disabled state,
truncate/update behavior, failure-email components or missing operational evidence.

The package contains 243 workflows. 168 have at least one resolved mapping, no
ambiguous references, no unresolved fields and no non-connection unresolved references.
Each candidate below has Start plus one Session, one mapping, an on-demand launch,
and two intentionally redacted connection references. Connection values are not
schema definitions. Use one selected workflow first, not an unrestricted estate run.

## Recommended general understanding demonstration

### wkf_Load_SRC_FED_CUSTOMER_SRC

- Folder: B_BWNG_FEDW.
- Mapping: m_Load_SRC_FED_CUSTOMER_SRC.
- Oracle VW_FED_CUSTOMER_SRC → Sybase customer_src, 21 fields each.
- One Source Qualifier; no nonempty SQL override observed.
- Target used by only one workflow in the inspected canonical relations.
- **Session disabled in export. Truncate target is YES, followed by insert-oriented loading.**
- A reusable failure-email component requires an explicit replacement disposition.
- Good for showing captured field contracts, reload semantics and design questions.
- Not an active-production claim or authorization to execute a truncate.

## Second demonstration: compact update and sequencing review

### wkf_Load_EWH_CIRCUIT_INVENTORY_PRODUCT_GROUP

- Folder: B_BWNG_CIRCUIT_VISION.
- Mapping: m_Load_EWH_CIRCUIT_INVENTORY_PRODUCT_GROUP.
- CV_CUSTOMER_ID_XREF (4 fields) → CIRCUIT_INVENTORY (20 fields).
- One Source Qualifier, 159-character source SQL override.
- **Updates PRODUCT_GROUP by CUSTOMER_ID; this is not append ingestion.**
- CIRCUIT_INVENTORY is shared by five workflows. Ordering must be established
  before proposing parallel execution or a consolidated job.
- Reusable failure-email behavior must be accounted for.

## Comparison examples, not the first simple demo

- wkf_Load_EWH_CIRCUIT_INVENTORY_PENDING: same shared target, insert-oriented
  writer, but 2,239-character SQL override with nine SELECT occurrences.
- wkf_Load_EWH_CIRCUIT_INVENTORY_CANCELLED: same shared target, 2,646-character
  SQL override with twelve SELECT occurrences. Graph size understates complexity.
- wkf_Load_FOC_BUILDING_CLLI: two-field source and target, but a Target Pre Load
  stored procedure and disabled session make it unsuitable for a clean conversion demo.

## Suggested runtime-agent instruction

Inspect the selected workflow using workflow_explanation, graph_slice,
lookup_evidence, source_contracts and sql_role_inventory. Retrieve exact SQL and session write settings.
Report captured source/target fields and distinguish them from physical bindings.
Explain original task order, cadence, disabled state, reload/update semantics and
failure-email behavior. Retrieve package_neighbors to assess shared definitions.
Propose Lakeflow task and reusable-component boundaries, parameter contracts,
validation fixtures and any required sequencing. Do not claim equivalent behavior,
safe parallel execution, generated notebooks or approved deployment. If a specialist
does not submit a typed finding, report incomplete agent execution rather than
asking the user to upload nonexistent missing XML schema evidence.

## Analysis versions and hosted demo

Older local snapshots can predate the resolver fix. Do not silently treat an old
snapshot as current. The hosted Lumen project was separately created through OBO
intake and freshly analyzed, producing 165,013 nodes, 529,237 edges and 2,976
remaining reference diagnostics. This is separate from the historical archive import.

## Available proposal-page actions

- **Generate agent redesign** prepares a selected-workflow request in Agent
  Activity. Model egress requires the user's explicit consent. Structured
  `submit_redesign` output is displayed as versioned logical designs, not inferred
  from narrative findings.
- **Draft captured SQL artifacts** produces role-aware syntax drafts with task-scoped
  blockers. It does not implement every non-SQL mapping or authorize execution.
- **Generate demo proposal and artifacts** invokes the constrained WIL_NET_DIS
  compiler after explicit approval of the fixture replacements. It is not AI authorship.

Opening the arena or the proposal page never automatically invokes a model.