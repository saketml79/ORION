# Scoped Estate inventory browser

Local implementation only. No deployment, server restart, source ingestion,
model inference, destination metadata query, Jobs execution or UC writes.

## Scope and behavior

- The nine Package inventory facts and the existing Estate metric tiles open
  a native modal dialog. XML-element totals and unrelated shared facts do not.
- The collection defaults to all brief metadata, fetched sequentially in pages
  of at most 100. A 307-target inventory requires four requests: 100/100/100/7.
  The explicit 100-per-page mode offers Previous/Next; Show all metadata returns
  to complete enumeration. Search is explicit and bounded to 200 characters.
- Only a selected canonical ID loads details. Equal names remain distinct by
  ID, repository, folder and database metadata. No collection-wide fields or
  properties are hydrated in the browser.
- Relevant views expose Overview, Columns / ports, SQL / Expressions,
  Configuration / children, Usage / lineage, Technical evidence and Diagnostics.
  Fields/properties retain their complete captured values, types, key and
  nullability attributes. Additional records require Load next 100 or an
  explicit Load remaining action with progress. Technical evidence traverses
  containment only, not unrelated graph closures.
- Equivalent suggestions include rationale, limitations and canonical evidence.
  They are explicitly labelled Suggested Databricks equivalent, require review,
  and do not claim generated artifacts or an approved source/destination mapping.
  Flat-file targets retain a file-delivery contract suggestion, not automatic
  Delta equivalence. Session SQL order and phase-specific overrides are retained.
- Native modal inertness, Escape, focus return, borderless top-right close,
  page-scroll locking and independently scrolling list/details are preserved.
  Scope changes and refresh discard metadata and abort pending work. Epoch,
  selected-record and source-version guards reject late responses even when
  cancellation is ineffective.

## Read boundaries

The existing GET /api/objects gains an opt-in inventory=true mode. Its existing
generic Explorer behavior and kind support remain unchanged. The new
GET /api/inventory/{kind}/{canonical_id} route pages explicit detail sections.
Inventory mode accepts only the nine displayed kinds plus connectionreference.
All collection/section pages enforce limit <= 100; invalid kinds/search/paging
return 422, mismatched source versions return 409, unknown typed IDs return 404.
Failures remain unconfirmed, not fabricated zero counts.

Both selected hosted reads use the existing committed canonical projection:
fresh OBO identity, membership, complete scoped inventories, manifest hashes and
Volume authorization precede use of the immutable data-only index. They do not
restore XML, agent/audit history or full execution state. Existing regex capture
positions are unchanged. The frontend uses shared scoped-fetch ownership with
current project/package headers, no completed-response cache and no fallback
identity. Standalone local routes retain the existing service boundary.

The requested agentic-blueprint directory/PDF is absent in this checkout. No
agent-mesh, intervention, execution, governance or conversion business logic is
changed by this feature.

## Consolidated verification

Run only after completing all changes, using the configured workspace venv:

    python -m pytest workbench/tests/test_inventory_browser.py tests/test_inventory_dialog_ui.py workbench/tests/test_app.py workbench/tests/test_hosted_object_reads.py workbench/tests/test_immutable_read_ownership.py workbench/tests/test_package_graph.py tests/test_explorer_graph_ui.py tests/test_scoped_reads_ui.py tests/test_navigation_prerequisites.py tests/test_project_package_autoload.py workbench/tests/test_capability_status.py workbench/tests/test_ui_structure.py -q --disable-warnings

Final result is recorded below after the consolidated run. Tests are offline;
passing them does not establish hosted production latency or live deployment.