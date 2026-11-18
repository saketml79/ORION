# ORION full app preview release, 2026-11-08 UTC

Explicit user authorization covers the current ORION app preview only. No business
Job deployment, run, controller replay, table truncate, model inference, grants,
attached resource changes or production gate changes occurred in this release.

## Request checklist

- Verified: one next action after clicks, page/tab entry and asynchronous readiness.
- Verified: stable completed-action identities across busy transitions/rerenders.
- Verified: actual Explorer object/node/tab callback identity, correct Fields return
  to Graph and no technical-download recommendation or repeating tab cycle.
- Verified: typing suppression, slow 3.2-second halo, reduced-motion static hint,
  accessible description preservation and modal suspension.
- Verified: exact package/navigation gates, disabled/hidden/busy/inert exclusions,
  connect-before-invoke and existing consent/input/design gates.
- Included: current Explorer tabs, Validate icon, source/mock lineage trace,
  readiness, destination/cohort review, codegen and all current app Python/static
  files admitted by the reviewed stage allowlist.
- Preserved: explicit execution/release boundaries and no invented task mapping.

Changed files for this request:

- [Guidance state machine](../workbench/static/next-action.js)
- [Authoritative Explorer event hooks](../workbench/static/app.js)
- [Guidance regression](../tests/test_next_action_guidance.py)
- [Full app packaging assertions](../workbench/tests/test_hosted_package.py)
- [Guidance documentation](next-action-guidance.md)
- [Later VYVX receipt note](vyvx-mock-demo.md)
- [Existing hosted release cross-link](orion-hosted-release-2026-11-04.md)
- This release record.

The existing stylesheet, index, lineage code/reference, backend modules and other
current assets were deployed unchanged from their current on-disk versions.
VYVX controller/runtime code remains a separate synthetic business Job package,
not an app runtime dependency. It was not redeployed or executed here.

## Consolidated verification

All 18 current JavaScript files passed `node --check`.

Exact final regression command:

```text
.\.venv\Scripts\python.exe -B -m pytest -c workbench/pytest.ini tests/test_next_action_guidance.py tests/test_explorer_graph_ui.py tests/test_migration_lineage_ui.py tests/test_readiness_ui.py tests/test_navigation_prerequisites.py tests/test_hosted_startup.py tests/test_proposal_selection_sync.py tests/test_vyvx_mock_demo.py workbench/tests/test_ui_structure.py workbench/tests/test_hosted_intake_ui.py workbench/tests/test_hosted_package.py workbench/tests/test_redesign_ui.py workbench/tests/test_package_parity.py workbench/tests/test_demo_conversion.py workbench/tests/test_validation_campaigns.py workbench/tests/test_hosted_operations.py -o addopts= -q --tb=short --disable-warnings -ra
```

Environment: workspace Python 3.13.12, `PYTHONDONTWRITEBYTECODE=1`,
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Final result: **639 passed, 1 warning,
587.58 seconds**, zero failures, errors or skips. This is a focused 16-module
batch, not a full-repository or independent business-parity result.

Two earlier runs each had 639 passes and one teardown error. The new CSV-click
test omitted the export mock; its first correction matched the query-free URL
instead of the actual filtered export URL. The final matcher includes the query.
Strict unexpected-request checks and production behavior were not weakened.

New external staging used the existing reviewed staging implementation:
**113 manifest entries**, **66 Python modules compiled**, **28 static assets
byte/hash checked**, all 18 JavaScript files syntax checked, nine hosted modules
imported and `databricks bundle validate --profile labs -t dev` passed.

## Actual deployment

- Existing labs user: `ssuman@bpcs.com`.
- Existing app: `orion-migration-workbench`.
- Stable app ID: `cfada07d-ffa6-4018-9080-293f5e577083`.
- Baseline deployment: `01f1da6451141193b064d1b739ada11c`.
- Release start: **2026-11-08T01:02:48.069724+00:00**.
- Release completion: **2026-11-08T01:08:35.226557+00:00**.
- New deployment: **01f1db1090d31d44978737267dc6042f**.
- Platform creation/update: **01:06:02Z / 01:08:33Z**.
- Outcome: **SUCCEEDED / RUNNING**.
- Bundle uploaded **114 files**, zero deleted. Zero resources created, one
  existing app changed, zero deleted. The extra uploaded file is the package manifest.
- Six requested OBO scopes, no attached resources (native JSON `null`), one
  minimum/maximum instance and approved existing warehouse were preserved.
- Existing workflow: bundle deploy, existing app stop, bundle run `workbench`.
  Identity/deployment drift checks ran before upload, after upload and after activation.
- URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com

## Hosted read-only smoke

At **2026-11-08T01:10:02.725572+00:00**, the same deployment was active.
Authenticated health returned 200 in 1.737 seconds, `databricks_obo`,
`deployment_allowed=false`. Root HTML and every one of the 27 other current static
assets, including the allowlisted JSON reference, returned 200 and matched exact
current local bytes and SHA-256. Key deployed hashes:

- app JS: `c2f597026c4088f026e66146f42acf154806ca86c574d649047ac748bc708a64`
- next-action JS: `3ac809d16fc1088c96110082f5fe315e2803e7ddf599aeca257acdbeabacb073`
- lineage JS: `dab237cfd3d5ae3a50c15aae5d708068d81c83da172580c294313a13f97a7cce`
- illustrative JSON: `f9d397efed60a8446dbf4b0ba887c139ba55f0e120183414e5378c420fe16af4`

Project `84b03a652f5e4371ba1c72d401acba6b`, package
`08987e6e377ff2c055d2e480a18e19ea280ec0ea4c83f0ee848e68669318c6ba`:

- `/api/readiness?limit=1`: 200, 42.890 seconds, exact scope. Release remains
  BLOCKED, 0/4 independently verified business workflows, deployment denied.
- `/api/readiness/lineage?limit=1`: 200, 21.748 seconds, exact scope.
- Existing committed generation `650e5b622fbe4d7d8c803296062bb64e` and analysis
  `9c23dc650c74a5a560846bef7d1e5d2f2c15feb97508a03bb5c3ef62ce45d469` remained selected.

These individual samples are not latency guarantees. Real browser interactions
were verified in offline Chromium regressions only. Hosted authenticated asset/API
verification does not prove a signed-in live animation interaction or parity.
No Git repository metadata exists, so no push or Git synchronization is claimed.

## Existing VYVX evidence, not rerun by this release

Read the immutable
[final mock receipt](../output/workbench/vyvx-mock-demo-20261107-v2/reports/20261108T001717594412Z-FINAL.json)
and [cleanup receipt](../output/workbench/vyvx-mock-demo-20261107-v2/reports/20261108T001717592634Z-CLEANUP_VERIFIED.json).
Final status is `READY_TO_RERUN_MOCK_ONLY`; cleanup is
`DESTINATIONS_EMPTY_SOURCES_PRESERVED`, with all seven destination counts zero.
Fresh/resume IDs are `133215706652645` and `936913853370412`.
`informatica_parity=false`. The illustrative UI reference still has unknown
runtime and no explicit original-task-to-Job mapping; no correspondence was invented.

The owned external stage, compiler scratch, verifier and temporary receipts were
removed after this durable release record was written.