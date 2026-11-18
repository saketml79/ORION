# Lakeflow Studio: implemented local workbench

## Current scope, not the complete platform

This is a working local structural discovery and review application. It is **not yet the full production modernization platform** described in [the blueprint](../docs/proposal-first-deepagents-lakeflow-plan.md).

The reference HTML's light palette, typography, rounded surfaces, indigo/teal/rose accents, and top-right theme toggle are implemented. Light is the default even when the OS prefers dark. Theme preference persists locally. No workshop benchmark numbers, branded logos, or demo animations have been copied into the product as purported results.

## Working capabilities

- Secure streaming analysis of the full supplied XML, with repository/folder-scoped object identities, definitions versus instances, source/target fields, ports/expressions, SQL/property roles, conditional workflow links, schedules, and connection references.
- Canonical typed graph JSON with stable IDs, evidence locators, English structural summaries, diagnostics, and explicit unknown lifecycle status.
- Searchable workflow/mapping/source/target inventories; interactive focused SVG graph, English, JSON, field/evidence inspection, and full-package downloads.
- Separate original-source and bronze-resident **structural candidates**, with explicit unresolved contracts and blockers. These are not agent-designed or optimized target pipelines.
- SQLite-persisted comment, rejection, changes-requested, and draft rework-request events with version conflicts checked. A rework request does not execute an agent or apply a revised graph.
- Directed/shared-definition impact reports. Shared source/target definitions indicate possible impact, not proven physical runtime dependency.
- Supporting document text extraction for UTF-8 Markdown/text and text-based PDFs; owner statements remain unverified. No OCR or automatic evidence adjudication is claimed.
- Validated nonsecret GitHub/workspace settings, persisted locally. Saving settings does not test connectivity or authorize external operations.
- Real Deep Agents SDK supervisor plus compiled specialist graphs, separate role skill/context configuration, SQLite checkpoints and scoped finding memory. Live invocation is opt-in and requires an explicit model selection and consent per run. Output is advisory and does not clear approvals or apply design edits.

## Not implemented or not verified

- Production authentication, tenant isolation, remote hosting, Databricks Apps deployment, and a production durable worker.
- Full transformation semantic translation/parity, verified source bindings, operational evidence adjudication, successful approval/gate-clearing workflow, and automated architecture rework/propagation.
- Approved executable Lakeflow generation, GitHub authentication/publication, CI/CD deployment, or one-click environment promotion.
- Pi or Genie integration. Their architecture assessments are documentation, not installed application features.
- Live model provider authentication/tool-calling validation. Agent tests execute real SDK graphs with fake offline chat responses, not provider requests.

Nonexistent generation, publishing, deployment, and approval actions are **not displayed as usable buttons**. Explicit scope notices replace those controls. Existing legacy generated outputs are never represented as approved releases.

## Storage and artifact contract

Source code lives in [this application directory](../workbench). Runtime state is separate from all existing compiler outputs, under the workspace's output/workbench directory:

| Location beneath output/workbench | Actual contents |
|---|---|
| packages / package SHA-256 | Original source XML, compressed canonical understanding JSON, English Markdown, integrity manifest, and structural proposal snapshots for each requested scenario. |
| packages / package SHA-256 / evidence | Original uploaded evidence bytes stored by content hash. Treat these as sensitive local files. |
| state / reviews.sqlite | Immutable proposal revisions, comments/rejections/rework requests, extracted supporting text, owner statements, and validated settings. |
| state / jobs and state / agent-runs | Atomic job/run records, completion/failure state, captured-input freshness, and advisory results. |
| state / active.json | Pointer to the active immutable package. Restart loads the persisted package. |
| agents | Deep Agents ledger, checkpoints, and role-scoped finding memory. |

**Canonical format:** versioned JSON with `schema_version`, `package`, `counts`, `nodes`, `edges`, `diagnostics`, `coverage`, and `summary`. Nodes identify scoped definitions/instances/fields and retain metadata and evidence. Edges retain direction, kind, attributes, and evidence. Compressed JSON is ordinary JSON after gzip decompression, not a pickle or a Python object.

**NetworkX:** `to_networkx()` creates a `MultiDiGraph` for deterministic traversal/SCC analysis. NetworkX is an analysis representation, not the agent exchange format or a source of semantic truth.

**English:** structural summaries and evidence-linked inventory are generated from the same model. They do not assert undocumented business meaning. Agents retrieve bounded context through tools; they are not sent the entire half-million-edge graph in a prompt.

**Display bounds:** focused graphs are limited to 100 nodes and 2,000 edges; field lists and text have explicit API bounds. These are local structural slices, not full-estate or complete source-to-target lineage. Use drill-down or the full export to inspect omitted content.

## Running and testing

Use the workspace virtual environment and install [the pinned application dependencies](requirements.txt). The local entrypoint is `python -m workbench --port 8000`, run from the workspace root. The VS Code task **Run local modernization workbench** starts it on loopback. Open http://127.0.0.1:8000 and choose **Load sample package** to ingest the supplied XML.

Remote clients and non-loopback hosting are deliberately rejected because production authentication is not implemented. Do not remove that boundary to deploy this version. App-local SQLite is not a Databricks production persistence plan.

The service does not read the environment credential file automatically. Live agents require `WORKBENCH_MODEL=provider:model` and the selected provider's appropriate LangChain integration and credentials supplied outside prompts. Provider integrations may require additional packages. Do not enter credentials in settings or evidence uploads.

Regression invocation: `python -m pytest -c workbench/pytest.ini workbench/tests -q`. The browser tests skip unless `WORKBENCH_TEST_URL=http://127.0.0.1:8000` points to a running service with the actual sample loaded. Install Chromium using Playwright's browser installation before running them.

Browser tests add visibly labelled regression evidence/reviews/settings to the local workspace. They do not invoke a provider, approve, generate, or deploy. Backend tests use temporary directories and also verify restart persistence, XML security, scoping, freshness, concurrency, settings validation, and full-export counts. See [the verification report](../docs/workbench-verification.md) for measured results and untested boundaries.