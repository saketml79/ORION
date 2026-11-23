# Cohort review milestone

## Draft bundle resume integrity, 2026-11-07 UTC

Before a cohort worker reports a published draft, it now reopens the exact
committed bundle through the existing checksum, approval and scope-verifying
read route. This also applies when a completed child journal is reused after
restart. Missing, corrupted, stale or mismatched bundles stop the worker; no
writer or publication action is replayed. A verified draft remains unexecuted
and does not establish semantic parity or release approval. Regression and
preview deployment status are recorded separately after they actually complete.

## Follow-up under verification, 2026-11-07 UTC

- A saved review now compares every stored workflow ID with the complete current
	canonical cohort. A missing or mismatched member blocks the review, rather than
	silently reporting a smaller cohort. The hosted worker rechecks membership
	before each workflow and after a design action; drafting also rechecks the
	exact saved membership and workflow record before each action.
- Server progress reports bounded completed-workflow counts. Explicitly opening
	the saved review and starting another consented action with fresh user OBO can
	continue confirmed completed steps. An uncertain or failed child claim still
	requires inspection; no automatic replay, credential persistence, service
	principal fallback or unattended reauthentication is provided. A lost commit
	acknowledgement cannot be automatically reconciled by this change.
- Target review displays an XML-derived fixture schema/table candidate. The
	catalog must be chosen by the user. An explicitly requested observation checks
	the canonical target fixture types and UC metadata; the separate zero-row
	SELECT probe uses the requesting user and configured warehouse. A failed or
	unavailable probe is not proof of absence. It does not test write permission,
	read business rows, approve a fixture, establish semantic equivalence, or
	persist the observation as an approval.
- Readiness now names the missing original input snapshots, parameter values,
	relevant procedure specifications and bodies, independent original/candidate
	output snapshots and run receipts. The independent evidence ingestion and
	positive release certificate remain unimplemented; no business parity or four
	qualifying E2E runs are claimed. Existing controlled fixtures are not a proxy.
- Read-only labs metadata on 2026-11-07 observed the three existing
	`orion_bronze_test_ssuman`, `orion_silver_test_ssuman` and
	`orion_gold_test_ssuman` fixture catalogs under the signed-in user. No catalog
	DDL, model call, business job or production approval was performed here.
	Regression and any preview deployment are recorded separately after execution.

## Local implementation, subsequent to the review correction

- Hosted cohort design now has a separately consented server operation. The
	browser submits one action; the server reads the saved package-scoped cohort,
	journals each deterministic workflow operation before inference, invokes the
	existing user-scoped action and checks its committed outcome before proceeding.
	Each workflow uses a separate existing commit-last Delta generation. The
	operation stops on missing design, failure, stale evidence or uncertain claim.
- A separate model-consent action drafts the cohort only when each workflow has
	exactly one current, individually approved design. It journals the writer and
	content-addressed native bundle publication separately. Existing writer and
	bundle gates recheck approval, inputs and artifact hashes. These are drafts,
	not uploaded workspace files, executed Jobs, semantic parity or release.
- OBO credentials remain in the initiating worker's memory only. There is no
	refresh token, service-principal fallback or autonomous reauthentication. A
	process restart or expired authorization stops the worker. A newly
	authenticated explicit action can continue completed steps, but a queued,
	running, failed or otherwise uncertain child claim requires inspection and
	is never automatically replayed. Commit acknowledgement can still be
	ambiguous. Single-replica and per-user serialization assumptions remain.
- Snapshot paths explicitly admit the writer's scoped generated-draft layout,
	not arbitrary root paths. Content-addressed JSON draft artifacts retain their
	exact bytes across restoration; ordinary JSON and existing row-backed draft
	records retain their prior reconstruction behavior. Path traversal, symlink,
	checksum and user namespace checks remain in force.
- An explicit read-only action can compare a saved target selection with fresh
	user-authorized UC schema and table metadata and canonical XML columns. The
	observation is not cached, persisted as approval, or treated as a data read.
	Permission failures do not imply absence. Fixture selection remains
	unapproved and cannot establish source connectivity or output parity.
- The existing typed multiset comparator includes nulls, duplicates and schema
	validation, but controlled fixtures are not business parity. Actual parity
	remains blocked until independently acquired source/reference outputs,
	original inputs and missing external behavior are supplied and compared.
	No model inference, catalog read, Job execution or deployment was performed
	to implement this local change. Regression results must be reported separately.

## Draft bundle publication, local implementation

An explicitly requested action can save a content-addressed draft bundle for a
completed writer job. The action rechecks the exact latest drafting approval,
current inputs and writer manifest, builds the existing conservative Jobs
document, and saves the actual writer notebooks with a checksum-bound receipt.
The receipt is written last. Reopening verifies the receipt, source manifest,
notebooks, Jobs document, scope and current approval; a partial or altered
bundle fails closed. The existing hosted generation mechanism can persist these
files to its scoped Delta/Volume authority when the enclosing action commits.
This does not upload workspace notebooks, deploy or execute a job, or establish
source-to-target parity. A response lost during hosted commit must be reconciled
against the committed generation, not blindly replayed.

This is not the requested complete cohort worker. The per-workflow design loop
still depends on the browser to submit the next action, and a fresh user action
is needed after interruption. Design approvals and model-egress consent remain
separate. Structured condition operands and proven task-value bindings are not
represented in the current approved design contract, so conditional Jobs remain
blocked. UC target selections remain unverified proposals. Actual output parity
requires independently supplied original inputs, procedure definitions and
outputs; empty fixtures cannot establish it. No autonomous generation, live
source access, execution, or deployment is claimed.

## Review correction, 2026-11-06

The per-workflow loop is in the browser, not a server-side cohort scheduler.
Each accepted hosted operation is user-scoped and journaled; its worker is
drained before the committed generation is published. A process interruption
can leave an uncertain journal claim, and the remaining browser sequence does
not resume itself. The UI now checks the actual scoped terminal run before
submitting the next workflow and stops on failure or an unknown outcome. Local
tracking displays a minimizable progress dialog; hosted actions use the existing
hosted operation modal. Neither mode retries an uncertain operation automatically.
Failed agent runs are recorded as failed, not as missing-information results.

The native Jobs preview now requires the manifest's design hash and input
versions and hashes to match the currently approved design. This is still an
in-memory preview: it is not automatically produced or durably saved. Notebooks
and the Jobs document are not uploaded or executed. The logical task contract
contains no structured condition operands or assignment mapping. A free-text
predicate and a true/false label do not establish which task value produces the
operand, whether a threshold is numeric, or whether the opposite branch is
actually equivalent. Conditional Jobs tasks remain blocked rather than
inventing operands, task values, control edges, or task implementations.
The Databricks SDK documents distinct numeric ordering and string equality
semantics; SDK round-tripping alone is not runtime or parity validation.

Automatic post-approval artifact generation, durable Jobs publication, a
reauthenticating restart-safe cohort worker, and general source-to-target
behavioral conversion are not implemented. Target selections are unapproved
proposals and are not automatic bindings. The original blueprint files and PDF
are absent from this checkout. No release or live E2E conclusion follows from
these changes.

## Local implementation update

Saved reviews now expose an explicit consent-gated sequential design action.
Each workflow has a stable operation ID derived from its package, review and
workflow. Hosted execution must enter the existing user-scoped operation journal
and existing agent executor. The journal commits the operation before execution,
does not persist credentials, and does not replay interrupted work. The workflow
result is committed with the existing hosted generation. A failed or uncertain
operation must be inspected before any new request. Browser closure stops the
client-side sequence, it does not cancel a currently accepted operation. This is
not an autonomous, reauthenticating worker or a distributed transaction.

The agent receives a package-context redesign request, but model output may be
incomplete. No design is marked ready unless a design was actually captured.
Design approval and artifact-model consent remain separate; no business job,
catalog write, deployment or execution is triggered by cohort design.

For a current, explicitly approved design with committed artifacts, a separate
action can produce an in-memory native Jobs YAML document for fully drafted
notebook-only DAGs with unconditional dependencies. It requires an explicit
workspace path, validates notebooks and SDK task structure, and blocks unsupported
task kinds and conditions. It does not upload notebooks, validate the workspace,
persist a release, establish parity, or authorize deployment. STANDARD serverless
is the draft default because no latency requirement is supplied. The existing
completeness endpoint continues to report missing committed Jobs YAML.

The remaining sections describe the earlier milestone, not the updated local
action. Live hosted activation and end-to-end migration are not established by
this local change.

This change records an explicitly selected workflow or exact literal-invocation
cohort as separate, project/package-scoped workflow review records. It does not
start model inference, submit an agent run, generate code, execute Jobs, write UC
tables, or approve a release. Literal invocation connectivity is not a completion
dependency. Each record says that individual review is still required.

The request ID is an idempotency key. A repeat with identical input returns the
same records, while changed input under the same ID conflicts. Each record pins
the package evidence version and can be marked stale when read. Records are
stored in the existing hosted committed Delta generation via the explicitly
registered SQLite tables. Hosted SQLite remains an ephemeral reconstruction;
the existing single-writer, fresh OBO authorization and commit-last limitations
remain in force. A lost acknowledgement must be reconciled by reading the saved
ID, not by submitting a new request blindly.

Target selections are version-checked per package, selected workflow and exact
canonical target ID. They are proposals, not approved bindings. The identifier
components are validated as simple names and rendered as separately quoted UC
components. UC existence, field/type compatibility, physical access and data
parity remain unverified. A fixture selection is explicitly labeled as such.
No catalog or schema is created. Evidence changes make the selection stale.

The artifact completeness endpoint checks the existing committed writer output
and reports blocked or unverified status. The current agent writer does not
produce native Jobs YAML; a logical YAML preview is not a Jobs resource. Missing
YAML blocks dependency and condition verification. Notebook validation and
authored tests alone do not prove semantic parity or authorize deployment.

The existing hosted executor drains agent workers before publishing its final
generation. There is no independent reauthentication-capable worker, mid-action
commit, restart-safe inference replay, or distributed transaction. Accordingly
these review milestones are not an automatic conversion queue. Completing that
requirement needs a separately designed durable worker with independently
committed per-workflow transitions, scoped identity renewal, idempotency and
explicit model egress consent. The review record deliberately does not pretend
that work is running or complete.