# Architecture knowledge: evidence and boundaries

## Scope

The six existing campaign pillars were not a knowledge graph. This implementation adds a separate knowledge path, not a claim that six production-quality outcomes pass. The saved agentic-blueprint folder and PDF remain absent. The governing available design is [the proposal-first plan](proposal-first-deepagents-lakeflow-plan.md).

| Pillar | Implemented knowledge support | What it does not prove |
|---|---|---|
| Optimization | Applicable feature/pattern candidates with requirements and tradeoffs | Measured performance or cost improvement |
| Accuracy | Evidence-backed candidate rationale, role-local citations | Semantic equivalence or transformation correctness |
| Integrity | Strict schema, graph/manifest/blob hashes, immutable publication and readback | Business transaction or recovery parity |
| Security | Official-source allowlist, bounded public fetches, fresh OBO authority and per-project isolation | A completed deployed security audit |
| Databricks architecture | NetworkX retrieval wired into architecture, supervisor and validation invocations | Workspace/region availability or release approval |
| Data validation | Scoped requirements and unknown data availability, existing campaign checks remain independent | Populated data, independently matched outputs or runtime parity |

## Authority and invocation

- [Knowledge store](../workbench/knowledge_store.py) reuses the existing user-owned artifacts Volume and jobs Delta table. It creates no tables, volumes or catalogs. The project-specific context is `architecture-knowledge:v1`; its package hash is a fixed format namespace, not a customer XML package. It is intentionally reusable across XML packages in the same project, never across users or projects.
- The entire versioned JSON node-link envelope is a content-addressed immutable Volume blob. The small checksummed Delta manifest is published only after full artifact readback. The manifest is independently discoverable, like campaign records, not an execution-cache file or a universal table-setting change.
- A load validates the exact owner, workspace, project, manifest envelope and artifact bytes. A corrupt or missing latest revision fails closed, without selecting an earlier revision or local fallback. There is no global graph, credential, client or cross-user cache.
- [Hosted integration](../workbench/hosted.py) binds a loader within each fresh OBO action. [Agent runtime](../workbench/agents.py) loads one snapshot per run and performs actual deterministic role-entry retrieval for architecture, supervisor and validation. The returned data enters that role's invocation envelope, and graph version enters its runtime hashes. Compiled role tools can query again or stage short suggestions.
- Findings use `knowledge_citations` separately from canonical XML `evidence_ids`. IDs must have been retrieved by that role. Run results retain role retrieval receipts and source URL, response hash, section hash, retrieval/expiry timestamps and fact hash. Source knowledge cannot substitute for XML coverage or grant generation/deployment authority.
- Model suggestions remain `unverified`, release state `unknown`, and unaccepted in the run output. They cannot fetch, curate or persist automatically. A human must submit a separately consented curation request. Public documents are untrusted data, never agent instructions.

## Data contract and research

[Graph implementation](../workbench/architecture_knowledge.py) accepts only strict JSON models, not pickle, callables or serialized Python. Bounds: 1 MiB graph, 500 nodes, 2,000 edges, 12 features per curation and 12 results per query. Nodes are feature, document, pattern, constraint and the exact six pillars. Sources expire after 14 days. The source date distinguishes publication/release-section date from document update time and actual UTC retrieval time.

Fetches allow only credential-free HTTPS on the exact public Databricks docs host, AWS/GCP documentation paths, no query strings or traversal. Redirect targets are revalidated. Non-public DNS addresses, non-HTML responses, oversized bodies and time/redirect bounds fail closed. No auth headers, browser cookies, ambient proxies or model inference are involved. Only short excerpts, source statements and original curated summaries persist, not full documentation copies.

GA is accepted only with an explicit feature-associated dated release statement obtained by independent fetch. A model cannot submit a release-state field. Absence of a preview marker is not evidence of GA. Current usage-page preview/deprecation evidence overrides historical GA. Unknown and deprecated features are excluded; preview requires explicit run-level consent and is still excluded for a GA-only request. No source establishes current workspace, region, licensing, runtime or data availability automatically.

[Curation research inputs](../workbench/knowledge_curation.py) are URLs and short original summaries only, not a static latest-GA database. Each curation fetches all sources anew. The researched set includes Delta liquid clustering, predictive optimization, serverless workflows, For each, Beta job completion triggers, and unknown enforced-constraint release status. AWS evidence must not be generalized to Azure, GCP or all subfeatures. Four historical/core GA capabilities do not imply an exhaustive latest-feature catalogue.

## Explicit interfaces

- `GET /api/architecture-knowledge`: authenticated selected-project status only.
- `POST /api/architecture-knowledge/curate`: existing same-origin mutation boundary, selected project/package headers, `consent:true` and 1..12 strict feature requests. The body provides label, release/usage URLs, short summary, applicability, tradeoffs, requirements and pillar mapping, not asserted status or raw graph. No inference runs.
- Existing `POST /api/agents/run` adds `knowledge_preview_consent`, default false. Model/tool requests cannot grant consent themselves. Other run entrypoints retain GA-only defaults.
- Existing Readiness shows compact knowledge availability, version, freshness and six pillar counts, independently from blocked release and null runtime/data metrics.
- The explicit curation command uses the existing `profile_identity('labs')` human helper only, verifies committed project membership, opens existing user-owned UC resources without initialization, and can publish only with both publication and consent options. It independently restores through a second fresh human identity/client. This is CLI human authorization proof, not a live hosted OBO request or app deployment.

## Validation and live receipt

Final consolidated regression: **256 passed, 6 subtests passed, one existing warning, 350.73 seconds**. Python AST validation passed for 138 files. Editor diagnostics reported no errors across the changed Python, JavaScript and test files. The batch includes offline Chromium Readiness interaction and actual compiled Deep Agents fake-model calls, not live inference.

Exact regression command: `.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini workbench/tests/test_architecture_knowledge.py workbench/tests/test_knowledge_store.py workbench/tests/test_agents.py workbench/tests/test_agent_usage.py workbench/tests/test_runtime_planning.py workbench/tests/test_migration_planning.py workbench/tests/test_readiness.py workbench/tests/test_hosted_readiness.py workbench/tests/test_hosted_package.py workbench/tests/test_hosted.py workbench/tests/test_delta_runtime.py workbench/tests/test_hosted_operations.py tests/test_readiness_ui.py -q --tb=short --disable-warnings -ra`.

Earlier verification exposed an additive packaging-test indentation error before collection, then one exact-order assertion mismatch because NetworkX groups exported edges by source. The assertion now compares exact edge content independent of ordering; no schema, bounds, authority or hash checks were weakened. Live research also exposed NetworkX storing IDs as node keys rather than attributes; retrieval and report generation now return those exact keys explicitly. The final complete batch above ran after these corrections.

### Actual UC publication, 2026-11-09 UTC

The [complete live receipt](architecture-knowledge-uc-receipt-2026-11-09.json) records actual response hashes, section hashes, release evidence, timestamps, version and storage path. It was generated by explicit publication with consent for committed project `84b03a652f5e4371ba1c72d401acba6b`, using the existing labs human-profile helper. A second fresh identity/client reauthorized and restored exactly the published envelope. This is actual UC proof, not a mock receipt or hosted OBO inference proof.

- Version: `9ee253c04ef6974a50cea1d2dacfd16c56e93843577bc21522f48e6520893200`.
- Graph-data SHA-256: `4fb8345f21fef5467038819a67ef29db49f7f3993b5084e0133db0946e9dfbea`.
- Immutable envelope/blob SHA-256: `27afd2318975a842924aadb177d81552a7ef071cfb3c3728162de7fbc42204b9`, 54,738 bytes.
- Manifest SHA-256: `ab2ca1ac3a096a46a9995993613ae87bf283e2eae726f85982c006040e60defb`.
- Existing Volume path: `/Volumes/orion_dev_ssuman/workbench/artifacts/delta-state/users/eb365f23fb1d7ccbcc66fdf4339cbe6cc73360458833fe30944fd196cbbadbc7/blobs/27afd2318975a842924aadb177d81552a7ef071cfb3c3728162de7fbc42204b9`.
- Sources retrieved from `2026-11-09T20:55:01.400039+00:00` through `2026-11-09T20:55:04.852139+00:00`; earliest expiry `2026-11-23T20:55:01.400039+00:00`.

| Actual researched capability | Stored state | Default retrieval |
|---|---|---|
| Liquid clustering, scoped Delta GA statement | GA | Included |
| Predictive optimization, June 11, 2024 announcement plus current usage page | GA | Included |
| Serverless compute for workflows, July 15, 2024 announcement plus current usage page | GA | Included |
| For each task, August 21, 2024 announcement plus current usage page | GA | Included |
| Job completion triggers, explicit current Beta notice | preview | Excluded |
| Enforced Delta constraints, no matching explicit GA statement | unknown | Excluded |

Six pillar graph counts are optimization 4, accuracy 3, integrity 4, security 3, Databricks architecture 5, and data validation 3. These include preview/unknown research, not six passing validation metrics. Every pillar remains `validated:false`; data availability is null; production readiness is false. GA-default retrieval has four fresh candidates, not an exhaustive catalogue or a claim that those are newly released in 2026.

### Remaining proof boundaries

No app deployment, live model calls, Jobs execution, customer fixtures, new UC resources, universal Delta settings or business table writes occurred. The graph is remotely persisted now; the newly implemented hosted runtime/API/UI code requires a separately authorized deployment before it is live in ORION. Real hosted OBO calls to the new curation endpoint and live architecture-model citation behavior have not been verified. Offline tests verify the actual adapters, compiled tools and identity boundaries. Public fetches use HTTPS certificate verification and reject non-public DNS results; no adversarial DNS-rebinding penetration test was performed. Current research is AWS-specific and does not establish Azure/GCP feature availability. No hidden automatic refresh runs: expiry excludes stale candidates until another explicitly consented curation.