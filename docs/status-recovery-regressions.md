# Agent status, refresh and release regressions

## Scope and evidence

Tracked reports: #45 (notice spacing), #46 (assessment history), #47 (stalled
progress and misleading activity), #48 (refresh navigation lock), and #49
(regression-verification coverage).

The hosted investigation found repeated successful status responses with the
same recorded event count, one unmatched model start, and a nonterminal run
record. The retained logs did not establish a fatal worker exception.
**An unchanged ledger proves no newer recorded progress; it does not identify
whether the provider, worker, transport or persistence layer stalled.**
No underlying worker/provider crash is claimed as diagnosed or repaired.

The fixes below are implemented locally and the focused check has passed,
including a reproduction of the old ready-package navigation lock. Deployment
and hosted verification must be recorded before the related issues are closed.

## Confirmed causes and fixes

### Optional initial reads blocked otherwise-ready package navigation (#48)

`startInitialPagePreload()` combines document, settings, history, connection and
readiness reads. `navigationPrerequisite()` treated the aggregate loading or
failure state as a prerequisite for every package page. A failed document read
therefore disabled Agent Activity and Explorer even when the package,
inventory, workspace preparation and readiness had been confirmed.

The aggregate optional-read gate is removed. Actual package selection, matching
package identity, inventory, hosted preparation and readiness checks remain.
Individual read errors remain visible; this does not permit invocation without
the existing consent, model and readiness checks.

The overview previously retained an indeterminate progress bar for a failed
read after loading had stopped. The bar is now shown only while an actual
overview, inventory, readiness or initial-page read is loading. Its error
heading, details and retry controls remain available.

### Status reachability was mistaken for execution progress (#47)

The status freshness clock measured the last successful GET. Repeated unchanged
`running` responses reset that clock, while pending-call receipts continued to
drive role and skill animation. A verified ledger receipt is not a worker
heartbeat.

The live panel now separately displays time since the last recorded progress.
Progress uses the run start, persisted phase/writer update and matching ledger
event timestamps, never the time a status response arrived. Five minutes without
new recorded progress produces a warning, using the existing writer warning
threshold. Role and skill activity pulses stop until newer progress is observed.
The warning explicitly distinguishes a long request from a potentially stalled
worker.

**This is a recorded-progress heartbeat, not a process-liveness probe.**
Wall-clock elapsed time may continue for a nonterminal stalled run. No elapsed
cutoff, automatic cancellation, fabricated failure or inference replay is added.

### Status-read failure was not propagated to every activity consumer (#47)

The polling error path stopped its main interval and displayed a read error but
left the arena's last active observation intact. Its pending roles and skills
could therefore continue to appear active.

The same scoped failure now publishes an explicitly unconfirmed observation,
pauses execution clocks and activity animation, and retains the last reported
status and counters. Polling reconnects to the same run. A successful subsequent
observation restores the display; terminal records stop live activity. A 404
clears the unavailable selected observation rather than implying completion.
The measured workspace-save elapsed value is preserved and frozen on read
failure instead of being reset to "not observed".

### Hosted Stop queued behind the run it needed to stop (#47)

An explicitly authorized Stop request returned HTTP 409 because the normal
per-user write queue was occupied by the agent mutation draining its worker.
Serializing cancellation behind that mutation prevents it from reaching the
worker while the worker is active.

For a POST to the exact agent-cancel route, the hosted dispatcher now borrows
only the freshly authenticated owner's retained app with the exact matching
live project/package scope. It calls the existing cancellation route, which
rechecks membership, package identity, active status and the worker's
cancellation capability. The retained lease is released even if dispatch fails.
Other writes remain serialized. No cross-user lookup, cold restore or new
execution is admitted by this control path.

Cancellation remains cooperative: an already in-flight model request may finish
before the worker observes Stop. A cancellation request is not evidence that
execution has stopped; terminal status and release safety must be checked.

### Assessment-history insertion and row height (#46)

An accepted invocation selected and polled its run but depended on a later
whole-history read to insert its row. Committed hosted history can lag the
accepted, genuinely observed run.

The existing row renderer is reused to insert a missing observed active run on
the first history page. It does not duplicate rows or alter nonzero pagination.
Rows are 120 px and the viewport is 376 px with 8 px gaps, retaining three visible
rows. Existing history information is preserved.

### Excess notice spacing (#45)

An empty trailing target-output detail list retained a flex row and its margin.
Three selectors scoped to the adjacent workflow notices suppress the empty row
and redundant margins, preserving populated outputs and unrelated lists.

## Required regression and release gates (#49)

Run the focused offline check from the repository root:

```powershell
python -B deployment\check_status_recovery.py
```

It executes production frontend functions and the actual arena event consumer,
and checks the production hosted cancellation-dispatch method. Fixtures do not
contact providers or submit real agent runs.

Required scenarios:

- Optional document failure or pending preload does not lock a ready package.
- Missing inventory/readiness, failed preparation and mismatched scope still
  block package navigation.
- Failed reads retain explicit errors without a false loading animation.
- Unchanged successful status GETs do not reset recorded-progress age.
- Threshold crossing, status failure, reconnection and terminal observations
  update all relevant activity consumers coherently.
- Observed history insertion preserves deduplication and pagination.
- Live-status layouts fit light/dark desktop, intermediate and mobile widths.
- Cancellation cannot bypass owner/live-scope matching, method/path validation
  or stale-session rejection; dispatch failures still release the lease.

The existing three-row history and notice-spacing checks must also be preserved.
Syntax checks and the exact reviewed package manifest are separate gates.

GitHub Actions must deploy the exact reviewed candidate to
`orion-migration-workbench`; it must not deploy a stale checkout or the other app.
Use a dedicated OAuth-federated deployment identity, a pinned source revision,
serialized releases and explicit active-work approval. Preserve existing app
identity, OBO scopes, compute and source path. Verify deployment state and
deployed content before closing issues. Do not publish private run logs,
screenshots, bearer credentials or user data as CI evidence.

The release assembler verifies all 889 source-file hashes against the reviewed
deployed baseline, overlays only four authorized runtime files, and preserves
the other 885. The public repository contains the repair files and safe baseline
hashes, not raw private baseline configuration or run evidence. The workflow
first performs a read-only authenticated preflight. Activation stays gated until
the exact protected-environment OIDC subject and interruption approval are
confirmed. The deployment identity is separate from the app's runtime identity;
it receives no explicit Job, model, UC or workspace-administrator grant.

The reusable Job remains outside bundle management, as previously agreed.
Its application/template code belongs in the reviewed package; the existing
Job must retain its owner, Run-as identity, permissions, tasks and manual
clearing policy. Deployment must not recreate it, clear it or run it.

## Limits of the assurance

These gates address the reported failure paths; they do not prove that every
application path is regression-free. Documentation and an isolated check are
not substitutes for the matching hosted observation. Never claim a crash cause,
successful cancellation, hosted repair, Job execution or universal absence of
regressions without the corresponding evidence.

## Release validation record

- Local production-function, event-consumer, scoped-cancellation, threshold,
  history geometry, notice preservation and six-layout checks passed.
- Python 3.11 syntax and production JavaScript syntax checks passed.
- A coupled workspace-save clock case failed during verification of the new
  unavailable-observation projection. Its missing measured elapsed value was
  corrected and the focused check now proves that the value survives read
  failure. The incomplete projection was never deployed.
- The exact candidate contains four runtime repairs and 885 unchanged source
  files. The reusable Job settings, Run-as identity and permissions were
  captured without modification.
- The first read-only GitHub preflight authenticated the dedicated deployment
  identity and exposed an SDK method-name mismatch before source upload.
  The export and deployment signatures were then checked against the installed
  SDK and corrected. This is why offline fixture checks are not sufficient for
  release authorization.
- GitHub rerun and hosted outcome remain pending; no successful deployment or
  normal cancellation is claimed by this record yet.
