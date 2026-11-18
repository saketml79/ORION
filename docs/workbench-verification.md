# Workbench implementation and verification report

Updated: 2026-10-30. This report separates current implementation evidence from
the retained 2026-10-27 local baseline. No single historical test count certifies
the whole current application or all legacy transformation semantics.

## Current verification summary (2026-10-30)

| Area | Verified result | Boundary |
|---|---|---|
| Hosted app | `orion-migration-workbench` deployed with OBO health/status returning 200 | No SP fallback; not a production-readiness certification |
| Hosted XML intake | Full 61,141,514-byte test XML uploaded and newly analyzed | Not reuse of original converter outputs |
| Corrected resolution | 165,013 nodes, 529,237 edges; 2,976 unresolved references | Zero ambiguous references/unresolved fields in this tested export; 2,920 redacted connections plus 56 other references remain |
| Package inventory | 243 workflows, 434 mappings, 592 sessions, 583 sources, 307 targets | Structural coverage is not semantic translation support |
| Source fixtures | 583 empty Delta tables, 136 schemas, 27,908 columns verified | Empty test schemas, no live-source parity |
| Metadata authority | Hosted committed Delta generations and Volume artifacts | SQLite is an ephemeral execution cache, not direct durable storage |
| Historical import | Hash/count-verified import and receipt | Archive not automatically merged into active hosted generations |
| Agent redesigns | Typed tool-submitted designs, immutable versions and inline review | No guaranteed design from every run; no generic approved release factory |
| SQL drafting | All captured SQL roles inventoried, supported syntax exported to notebooks/Python | Non-SQL transformations and blocked SQL remain incomplete |
| Native example | WIL_NET_DIS DAB deployed; both 34/35 boundary cases succeeded | Mock email, test-child replacement, wait=0 in tests |
| Connections UI | Captured source/target association inventory | No connection editor or live connectivity test |
| Arena | Real run events, role findings and recorded rubric results | No invented role activity; LangSmith disabled |

Hosted project: `84b03a652f5e4371ba1c72d401acba6b`. Its fresh package overview was
read after redeployment with `ready=true` and 243 workflows, taking **76.7 seconds**.
That is a successful data read but unacceptable interactive latency. Browser GETs
have a bounded 110-second budget; mutations retain a 30-second browser budget.
Long analysis can outlast the HTTP request and subsequently commit. Failure or
timeout is not automatically retried as a successful mutation.

### Targeted regression milestones

These are separate batches, **not additive suite totals**:

- Qualifier-aware resolver and related regressions: 206 passed.
- SQL drafting, redesign review, real agent-tool registration and packaging: 77 passed.
- Connections and Delta migration adapter: 40 passed.
- Non-overlapping arena, rubric display, responsive and reduced-motion behavior: 40 passed.
- Graph legend, package graph and UI checks: 48 passed.
- Read-timeout UI contracts: 16 passed.

Broad suites during development had known Windows asyncio/socket-guard failures
and project-analysis timeouts; an intermittent model-route test also failed in a
batch and passed isolated. No all-green final full-suite claim is made here.
Offline fake-model/browser tests are labelled as such in their test modules and
must not be described as real provider or Databricks execution tests.

### Outstanding end-to-end gates

Generic package conversion, complete non-SQL transformation generation, verified
physical connection bindings, visible notebook result reports, safe long-running
hosted workers, low-latency state retrieval, unified review modals, GitHub PR/CI-CD
publication, and promotion across environments remain incomplete. The passing
bounded example is not a substitute for these MVP requirements.

See [hosted authority](hosted-delta-authority.md),
[historical migration](delta-state-migration.md),
[demo runbook](wil-net-dis-demo-runbook.md), and the
[authoritative design](proposal-first-deepagents-lakeflow-plan.md).

## Historical baseline: 2026-10-27

The remainder preserves the original local milestone. Statements such as “not
deployed” and earlier graph counts describe that date, not the current hosted app.

## Actual input and artifacts

Input: [the full supplied XML](../input/LumenExample.XML), 61,141,514 bytes.

SHA-256: `08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`.

Independent streaming scan and canonical graph tests verified:

| Item | Count |
|---|---:|
| XML elements | 319,885 |
| Repository / folders | 1 / 10 |
| Workflows / worklets | 243 / 1 |
| Mappings / mapplets | 434 / 1 |
| Sessions | 592 |
| Source / target definitions | 583 / 307 |
| Task definitions / task instances | 774 / 1,423 |
| Connectors / workflow links | 83,429 / 1,412 |
| Canonical nodes / edges | 167,120 / 531,344 |
| Reference/field diagnostics | 10,350 |

No unrecognized XML tags were found in this input. **That is structural extraction coverage, not verified support for the semantics of every transformation.** Unresolved fields/references are preserved as diagnostics rather than guessed. The original README's reported counts are not used as the test oracle.

Actual persisted artifacts are under output/workbench, isolated from the legacy IR, SQL, notebooks, workflows, and visual outputs. The canonical artifact is gzip-compressed JSON; the human-readable artifact is English Markdown. Both are downloadable in the running app. SQLite stores review/evidence/settings state; proposal snapshots and job records also persist to disk.

## Executed checks

- Backend/parser/domain/agent-harness/full-XML API suite: **248 passed**, executed with `python -m pytest -c workbench/pytest.ini workbench/tests -q` before enabling browser tests.
- Real Chromium suite: **6 passed**, with `WORKBENCH_TEST_URL` targeting the running loopback service loaded with the full supplied XML. No mocked HTTP responses.
- Final combined run after integration fixes and removal of nonfunctional actions: **254 passed in 550.40 seconds**, including all six real-browser tests. The JUnit receipt is persisted under output/workbench/state/regression-results.xml.
- JavaScript syntax: `node --check workbench/static/app.js`, passed.
- Installed dependency compatibility: `python -m pip check`, passed.

The first browser run found a test-fixture error: relative API request URLs lacked a browser context base URL. That fixture was corrected and all six tests reran successfully.

### Backend coverage

Secure XML/DOCTYPE/entity handling; scoped case-sensitive identities; preserved fields and conditional controls; raw-tag and graph count comparison; bounded graph export; impact scope; SQLite restart persistence; version conflicts; rejection and rework history; non-bypassable gates; document extraction/errors/redaction; credential rejection; safe Git refs/hosts; loopback/Host/Origin/CSRF checks; bounded uploads and unsupported formats; malformed package nonactivation; artifact atomicity/integrity; exact review response consistency; relevant versus unrelated evidence freshness; captured agent-input snapshots; actual Deep Agents graph compilation/delegation, role skills/memory, citation checks, and checkpoint persistence using offline chat models.

### Real browser coverage

Actual input fingerprint/counts; default light theme and persisted dark toggle; real inventory selection; graph/English/JSON/field tabs; keyboard graph inspection; persisted rejection and draft rework; no unavailable approval action; actual evidence upload/extraction storage; settings save and API round-trip; English artifact download response; no automatic agent invocation; mutation protection; mobile page overflow across all six sections.

## Explicitly not tested or implemented

No live model/provider request, live source discovery, original Informatica execution comparison, Spark/Lakeflow runtime parity, authenticated GitHub integration, Databricks deployment, CI/CD promotion, production authentication, or multi-tenant isolation was exercised. The local app does not implement those operations and does not claim successful execution of them.

Structural proposal records are not optimized Lakeflow designs. Agent advisory findings are not automatically applied to proposal graphs. Owner statements do not clear evidence gates. A rework request records a new draft request, not a completed semantic correction. No successful design approval or executable release path exists in this local version.

The user request for the entire blueprint to work end to end is therefore **not yet satisfied**. This implementation supplies a tested local foundation and makes the remaining boundary explicit rather than presenting facade buttons or simulated success.

## Next required implementation gates

1. Authenticated owner/architect roles, evidence adjudication and scoped operational/source contracts.
2. Typed agent-proposed architecture deltas, independently validated semantic coverage, and comment-resolution-driven draft rework.
3. Verified correction applicability and coordinated dependency/approval invalidation.
4. Approval-gated executable Lakeflow artifact factory and source/runtime parity validation.
5. Real authenticated GitHub and Databricks deployment adapters, immutable promotion and recovery tests.

These are functional implementation tasks, not configuration-only switches. A configured model alone does not complete them.