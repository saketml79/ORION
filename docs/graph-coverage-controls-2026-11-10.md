# Graph coverage and operational context

## Requested behavior

- The Estate graph opens only on an explicit Show package graph action. Workflows shown defaults to All, with 60, 100 and All options. The All label includes the actual selected-kind total after a response. Workflows, mappings, sources and targets use the same control.
- All uses GET /api/package-graph?kind=workflow&all=true, not concatenated pages. It includes all canonical nodes of that kind and their same-kind relationships, including cross-page relationships and complete existing path evidence. The paged API still defaults to 60 and validates limits from 1 through 100. Full reads require zero offset. Other API bounds are unchanged.
- The explicit full-kind response has all_nodes=true, an actual node_limit, edge_limit=null, and exact total/page counts. No 2,000-edge truncation applies to this opt-in read. Ordinary pages keep their existing 2,000-edge cap and report truncation. Neither mode reads full XML field lineage merely to display an overview.
- Estate displays every returned node and edge. Other object graphs retain their initial 100-node/2,000-edge display but offer Show all returned nodes and relationships when rendering would otherwise omit returned evidence. This button does not retrieve source nodes omitted by the object API. Its count reflects only actual returned evidence.
- Shared-definition relationships remain undirected in the SVG. Canonical directed references, cycles, IDs, source evidence and diagnostic assessments remain unchanged.
- Neutral nodes have no repeated Informatica / Not assessed caption. Recorded amber, red and green assessments retain distinct presentation and evidence. No status is promoted to green because an XML export has no error diagnostics.
- One graph explanation separates XML structure from agent review of transformation correctness, dependency semantics and validation coverage. No completed review is inferred.
- Operational context defines lifecycle, activity, execution, ownership and deprecation. Known supplied values remain visible. Unavailable dimensions are collapsed and labeled Not provided in export, with neutral styling in both themes. Raw unknown/null/empty values are retained in the original response and technical evidence disclosure. Enabled metadata is not converted into active lifecycle.
- Pagination follows the selected page size and remains disabled during its read, at boundaries, and in All mode. Selection changes retain request-ticket and package-epoch ownership. No automatic inference, Jobs invocation, deployment or assessment mutation is added.

## Verification

The package-graph tests extend the existing real Lumen XML ingestion test to verify all 243 canonical workflow IDs, unchanged per-node assessments, full edge totals, and relationships missing from the union of ordinary pages. A separate parsed 66-workflow shared-mapping fixture verifies all 2,145 relationships and their canonical path evidence, beyond the ordinary edge cap.

Offline Chromium runs the actual frontend against 243 distinct XML-parsed workflow definitions, checks 60/100/All transitions, pagination, stale responses, captions and exact evidence. Other tests cover the explicit all-returned object renderer, both lifecycle themes, known values and raw unknown preservation, existing navigation, loading gates and lineage/readiness boundaries.

Consolidated regression outcome: pending execution. No deployment or backend restart requested or performed. The required agentic-blueprint directory and PDF are absent in this checkout; no agent mesh or governance architecture changes are made.