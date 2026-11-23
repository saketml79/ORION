# Current release verification ledger

This ledger records evidence and outstanding checks. It is not, by itself, an
authorization veto. A specific current user deployment instruction may proceed
with checks pending; record those gaps clearly and never present them as passed.
Passing regression tests is not a migration specification or runtime-parity
certificate.

## November 23 refresh and analytics testing release

- Browser refresh now remembers the selected project, package and page in the
	current tab's session storage. The project list and completed package are
	re-read before restoring navigation; credentials, consent and run state are
	not stored. Explicit user selection overrides a pending restoration. Local
	desktop refresh was checked on Migration analytics and Proposals, including
	the selected workflow. Other dynamic states and hosted refresh are unverified.
- The desktop navigation links for 14 pages were opened locally. This confirms
	page navigation only, not every page action or hosted behavior.
- Recent-run timing and current list-price analysis-cost estimates were checked
	against saved local runs. Five runs are available in a keyboard-focusable
	scroll region; three complete rows are visible at the checked desktop width.
	The estimate excludes writer, compute, cache and negotiated pricing. Human
	review/rework time and Genie Code attribution remain untracked and show N/A.
- No historical run or design was deleted. The app has no agent-run deletion
	endpoint; deleting a design version also removes associated drafts. The user
	intends to handle design versions separately.
- The first authorized snapshot deployment, `01f1e788af5e1b81992096391c7bd51b`,
	succeeded with traffic switched on November 23 at 22:07:17 UTC, but used an
	unwanted ID-based release source folder. The user subsequently confirmed the
	existing stable `orion-hosted` folder as the source. Its sync preview and
	actual sync reported no deletions. A second deployment from that folder is in
	progress; its final state is not yet confirmed. Source hashes for 195 files
	and bundle validation passed. No test files or new model calls were run.

## November 23 migration analytics, local only

- Added a Migration analytics page immediately below Folder migration. It
	reads the selected package's existing readiness and workflow-call summaries;
	it does not create records, call a model, execute notebooks, or deploy.
- Actual local Lumen desktop check: 243 workflows, 86 composite candidates,
	157 standalone candidates, two currently assessed workflows, and two saved
	current code-draft receipts in the bronze scenario. Literal workflow calls
	are classification hints, not a complete dependency assessment. A saved
	draft is not a verified conversion.
- The existing data does not establish accepted-as-is counts, Genie Code
	attribution, YAML re-edits, or verified conversion totals; those cards show
	N/A. Notebook edit receipts exist, but do not identify Genie Code authorship.
	These requested analytics require additional tracking and are not complete.
- In the actual local desktop app, switching scenarios updated counts and a
	global refresh retained all eight cards without replacing the content DOM.
	The page polls while visible and retains the last snapshot on read failure;
	hosted behavior and error/permission cases have not been independently tested.
- The user-requested local Sonnet 5.5 run `f05465c373aa4cbda7595daecc3729cb`
	finished completed after 24m31s. Writer `bf3fd661789c42cfb4996b4949acb76c`
	saved a generation-complete candidate, review-ready and fully AI-reviewed.
	Four notebooks were observed staged. No correction round was triggered, so
	the correction loop remains unverified. Analysis estimate $15.56 excludes
	writer and compute. Generated code was not executed.

## November 23 missing-value wording, local only

- User requested `0` for reported zero and `N/A` or an explicit unavailable
	message for values not returned. UI display wording was changed; internal
	state identifiers, count values and service contracts were retained.
- In the actual local desktop app, saved run `f798e284be634a8a85a3b55aef65b1e4`
	showed recorded counts and `N/A` for unavailable call counts. Saved run
	`f361853526bd4491884dd7b507b7c2c8` retained its recorded zero and failure
	counts and displayed `Ended`, rather than the previous 72% indicator. The
	latest saved run was reselected after this check. Nine desktop navigation
	pages were visited; this did not exercise every dynamic or error state.
- No model inference, test files, hosted browser, or new deployment was used.
	Missing values supplied dynamically by other responses may still need
	wording review; historical stored records were not modified.

## November 23 hosted testing release in progress

- The user requested deployment to the existing hosted app for testing. The
	single snapshot deployment was started from the new staged release
	`orion-hosted-review-5c9bf4e8a73c4244a5bd9024b099ef79`. Its terminal result
	and traffic switch were not yet confirmed when this entry was written.
- The local writer now requests missing task XML and SQL pages before review,
	with two feedback opportunities and existing budgets. It does not mark
	unread pages as read and does not guarantee that the model retrieves every
	page. Review remains incomplete if the required material is not retrieved.
	This change and the design-revision path have not been exercised by a new
	model run. Historical drafts and review records were not modified.
- The real local desktop app was restarted while idle. The saved partial run
	reopened showing 8,255,937 recorded tokens, 6/6 completed analysis results,
	and the clarified incomplete-review message. The API billing and privacy
	notice was checked at desktop size. No mobile check or new model run occurred.
- Python `py_compile` passed for app, agents, writer, reviewer, revision,
	candidate retry, hosted operations, and staging modules. `node --check`
	passed for app, arena, external models, hosted intake, and team flow.
	`git -c core.whitespace=cr-at-eol diff --check` passed. No test files ran.
	Staging verified 193 workbench source hashes; bundle validation passed.
	Hosted behavior, isolation, live corrections, and executable correctness
	remain unverified.

## November 23 local run and desktop follow-up

- The authorized local Opus 5.5 run `f798e284be634a8a85a3b55aef65b1e4`
	started 10:10:02 and finished 10:47:28 PST with a partial delivery status.
	Writer job `ba95a4fcf34749c3a132c60e37e2bcea` saved design v42 and a
	generation-complete candidate. Four of five tasks had complete AI review;
	`check_tgt_rows_lt_35` did not. No design revision round was admitted.
	The analysis estimate was $52.50, excluding writer and compute costs.
- The saved writer checkpoint shows one source request for the affected task:
	12 of 206 records returned at offset zero, with next offset 12 and no
	omissions on that page. No subsequent source-page request for that task was
	recorded. The exact cause of the missing follow-up is not established.
	The saved manifest and draft directory are present. No source or review gate
	was weakened and no historical record was changed.
- The shared local browser was restored from 900 × 390 to 1440 × 900 and the
	left navigation was visibly restored. The idle local app was restarted and
	the same saved run reopened. Its recorded 8,255,937 tokens, 6/6 completed
	agent results and 40 citations displayed. A labeled API billing and privacy
	card was visually checked at desktop size. No mobile checks were performed.
- Source changes display a verified partial token subtotal, retain the parent
	run start/finish times during writer work, and clarify future incomplete-review
	preflight messages. The live partial-token and writer-phase time changes have
	not been exercised by a fresh run; historical results retain their original
	preflight message. Node syntax checks, Python syntax and editor diagnostics,
	and scoped Git whitespace checks passed. No test files were run.
- A real correction cycle and hosted behavior remain unverified. No deployment,
	additional model invocation, generated notebook execution, or cloud job was
	performed in this follow-up.

## Writer visibility and post-review diagram, local changes

- The process-local writer-event path no longer deliberately excludes hosted
	execution. Local and hosted use the same validated job/package/execution-root
	key; hosted execution roots belong to separately authenticated sessions.
	The event buffer is bounded and lost on restart. Hosted multi-user isolation
	and live hosted event delivery have not been independently exercised.
- The actual local help diagram was opened and checked at desktop and mobile
	widths: data validation and CI/CD delivery now follow human review as
	separate user-directed steps. The idle local app was restarted to load the
	backend changes, clearing process-local event history. The saved Opus run
	was then opened and still displayed its completed status and `$40.65`
	analysis estimate. No new model call or test file was run.
- The two-round design-to-agent correction path was subsequently added in
	local source; it has not been validated by a live correction run. Hosted
	deployment and a hosted end-to-end run remain unverified.

## Two-round writer correction, local work in progress

- Local source now contains a bounded two-round design-revision path: an
	attributed design opinion is checked against the exact saved review and
	candidate, routed to a selected role, and followed by a design-author and
	validation handoff before a new writer. A revision ledger pins each runtime;
	prior drafts are retained. The local run-detail page shows recorded rounds.
	These statements describe implemented code paths, not a successful live
	correction. The path has not been exercised end to end and must not be
	treated as ready for hosted deployment on syntax checks alone.
- After restarting the idle local app, a completed pre-change Opus run was
	reopened in the actual shared app. Its status and historical candidate were
	displayed. This checks compatibility of that saved-run view only; it does
	not exercise reviewer feedback, either revision round or hosted isolation.
- Python parsing, JavaScript syntax, editor diagnostics, and scoped Git
	whitespace checks passed. No test files or new model calls were run.

- The writer now retains actionable review feedback instead of treating an
	unchanged review scope as accepted, and can request up to two additional
	correction passes in its existing writer checkpoint. The manifest records
	the number of distinct feedback scopes sent for correction. Unresolved
	review issues remain visible, and saved historical drafts are unchanged.
- Writer-side correction and the later-added design-to-agent route have not
	been exercised by a real correction run.
- Reviewer issues now optionally report whether the problem concerns code or
	design and which agent may be responsible. This is advisory, not a verified
	assignment. The new handoff and revision chain attempt to preserve exact
	per-round provenance; those paths still require end-to-end verification.
- The agent-help diagram was checked in the shared local app and now includes
	a concise AI-generated-content and human-review notice. Python syntax,
	JavaScript syntax, editor diagnostics and scoped whitespace checks passed.
	No `test_*.py` files or new model calls were run.
- Do not describe the requested feedback loop as verified or deploy this work
	as a finished feature until its real correction behavior is validated. Hosted
	remains unchanged.

## Active hosted run and local fixes, 2026-11-23 UTC

- Hosted run `60a1d5da56fb4780a5b3f7a00ae6bf25` was reported running,
	with its separate Spark/SQL writer in generated-code review. The hosted
	app and run were not stopped, replayed, or accessed through a browser.
- The deployed operation reporter rejected `committing_delta` before the
	final snapshot save. The two deployed Python files were exported read-only
	and hash-matched to the local sources before correction. The local fix adds
	this existing phase to the operation-progress allowlist; it cannot change
	an operation already executing in the deployed process.
- The live cost adapter recognized only Opus 5.5. Local changes also parse
	the independently validated official Sonnet 5.5 standard Azure Premium
	pricing row, identify missing rates or token usage, and visibly label partial
	estimates. These estimates exclude writer and shared-compute costs.
- The global button shadow rule suppressed the agent tile's shadow pulse.
	The local correction uses an in-flight outline animation with a static
	reduced-motion indication. The official-documentation checkbox is locked
	with the other run settings and reflects a selected active run's recorded
	consent; it resets on completion. The accepted-run message no longer says
	that a Delta flush is underway during agent execution. The skills grid has
	six desktop columns and clearer labels.
- One consolidated local check passed JavaScript syntax, Python parsing,
	progress-phase validation, current public price parsing for both models,
	and scoped CRLF-aware whitespace checks. The actual shared local app was
	restarted only after local jobs were checked idle; a saved run showed six
	cards per row and its existing `$54.94` analysis estimate. Its actual
	pricing route returned both Opus and Sonnet. No model was invoked. Live
	pulse, active consent behavior, Sonnet usage accounting, and the hosted
	final save have not been verified end to end.
- Deployment remains on hold while the hosted writer is running and until
	the run's terminal outcome and preservation of its work are established.
	No new deployment was made for these changes.
- A read-only Apps log lookup found an agent-request 503 at
	`2026-11-23T01:05:46Z`, diagnostic `79564d1233d64f0bac108c0bedb97c58`,
	with frames at the final-save progress callback and progress validator.
	The log contains no run ID, so attribution to the user's run is unverified.
	Its presence is evidence of the deployed bug, not of this run's outcome.
	The local summary now identifies an active writer separately from the six
	analysis agents and updates its stale-phase warning even when the response
	body has not changed. The warning does not assert a hang.

## Support and official-search local verification, 2026-11-22

- The idle local workbench was restarted to load the pending local changes.
	Python compilation, JavaScript syntax checks for 34 files, and the
	CRLF-aware Git whitespace check passed before the latest implementation.
	At that earlier point the controls were disabled. After the local API/UI
	changes the actual page was reloaded and now shows explicit consent controls,
	a saved-run-gated AI and human request button, skill upload, and an
	administrator queue. No live model or user submission was invoked.
- A single consented search for “Lakeflow jobs” in the actual Support page
	returned 15 URL-path matches and retrieved two Databricks AWS documentation
	pages with SHA-256 receipts. Apache Spark's sitemap was available and
	Informatica's was unavailable. The search is path matching, not full-text;
	AWS documentation is not evidence that a feature is available on Azure.
	Consent reset after the search, and clicking again without consent displayed
	the consent requirement. A narrow local viewport showed no horizontal
	overflow in the visible Support page. These observations do not establish
	complete search coverage, agent use, hosted behavior, or future availability.
- Two managed Volumes were created at the user's explicit request using the
	existing `labs` profile as `ssuman@bpcs.com`: approved-library
	`orion_dev_ssuman.workbench.orion_support` (ID
	`807d1ec5-4714-450d-a480-b647571d72cf`) and pending-submissions
	`orion_dev_ssuman.workbench.orion_support_submissions` (ID
	`12d773a6-5ca9-4de5-b60a-dfaa8f3ff61d`). Their expected directory layout was
	created. No sharing grant succeeded. The existing app ACL lists `ssuman@bpcs.com`
	and workspace group `admins`; Unity Catalog rejected both workspace-local
	`users` and `admins` as grant principals. No grant was applied. Ask for the
	Unity Catalog account-group name before enabling shared submissions/library
	access. Individual OBO owner access remains intact.
- Local Support code now includes one-consent AI review of bounded saved-run
	findings/activity and analysis pricing estimates, human request/decision
	endpoints, skill upload/vetting/promotion endpoints, approved-skill on-demand
	reads, and default-off agent/writer documentation search. Search is path-token
	matching (not full-text), at most two public pages and three agent queries per
	run, with exact source-name filters and URL/hash receipts. Search queries and
	page text are not placed in run receipts. The API changes are not deployed;
	local AI review was only checked with a no-network fake model and synthetic
	receipt, not with a live inference. Human queue/promotion require the missing
	Unity Catalog group grant. No actual user skill upload, promotion, model run,
	hosted browser, push, or deployment occurred.
- After the final code reload, the actual local Support page at 390 px had zero
	horizontal overflow. All four consent boxes started unchecked; AI and human
	review buttons stayed disabled with no saved run selected. An owner-scoped
	read of the human queue and pending skill queue returned zero items. The
	approved-skill registry read is available and empty for this owner. A second
	consented “Lakeflow jobs” search returned 15 path matches, two retrieved AWS
	Databricks pages with hashes, and no Informatica sitemap; consent reset after
	the request. Local AI-review, human-review and skill-upload POSTs with
	`consent:false` all returned HTTP 403 before action. No live model call or
	upload submission was performed.
- `compileall` for `workbench` and `deployment`, `node --check` for all 34 static
	JavaScript files, CRLF-aware `git diff --check`, and editor diagnostics for
	the changed Python/JS/HTML files passed. Focused no-network checks verified
	Markdown/secret/path rejection, the shared search page/query budget, approved
	skill receipt projection, price arithmetic, and that reuse of one AI review
	idempotency key causes exactly one fake-model invocation. No `test_*.py` suite
	was run. No deployment or hosted Databricks browser was used.
- Eleven dated one-off fix/implementation reports were consolidated into this
	ledger or the docs index and removed. Release histories, unresolved blockers,
	conversion evidence and durable architecture/operations references remain.

## Pending local analytics and writer-limit changes

- Analytics cards and log panels now use a consistent 12 px vertical gap
	between headings, values, controls, status messages and charts. In the actual
	shared local app, measured gaps were 12 px at 1440 px and 390 px, with no
	horizontal overflow. The desktop viewport was restored. The CSS check and
	`git diff --check` passed. This remains local and is not deployed.
- The Databricks CLI reports 34 installed skills in the complete Cursor
	skill directory; all seven previously identified Claude-directory copies
	match their Cursor counterparts by hash. With 19 Anthropic entries, the
	updated local catalog reports 53 of 53 available and named. A temporary
	stage included all 53 skill instructions and matched 860 release-preview
	file hashes; it was removed after verification. A fresh agent run has not
	verified discovery or reads. The official Databricks provider LICENSE and
	NOTICE are now included in the release package with pinned upstream commit
	`ed2c66be443e4c16502a0f7cb93c4edf60e93e94` and SHA-256 provenance. Agent
	Activity has a temporary, dismissible banner beside Invoke Agents with
	expandable license/notice text. The installed skill sources do not expose
	standalone licenses for 33 Databricks entries or Anthropic `doc-coauthoring`;
	the banner reports these gaps and makes no claim that those skills are
	independently licensed. Hosted review of the banner is pending deployment.
- Application Log Analytics was opened in the shared local app. CPU, memory
	and I/O are current-process samples; the 50-row log chart and the selected
	saved run's 65 recorded analysis-model calls were displayed. Writer calls,
	all-user totals, fleet history and hosted behavior are not established.
- The writer's per-file limit now uses the existing aggregate byte budget.
	Oversized task-owned evidence can be fetched in ordered JSON chunks, with
	its original page position recorded and citation credit applied only after
	the last chunk. A focused in-memory check reconstructed a large Unicode
	record, verified its hash and rejected out-of-order access. Aggregate, text,
	identifier and model-context bounds are unchanged and not justified yet.
- On-demand external-skill catalog and writer receipts remain unverified in a
	new model run. The downloaded skill instructions include operational commands;
	they are reference data, not permission to execute commands. Redistribution
	terms for some installed skill folders remain unverified. No new deployment,
	model invocation, old test suite or runtime-parity claim was made.

## Active agent tiles and compact skills, local changes

- Agent tiles now highlight and gently pulse only when a verified in-flight
	model or tool call identifies that agent. Reduced-motion mode retains a
	static highlight. Selection alone does not mean an agent is running.
- The skill/guidance panel displays four cards per desktop row and two on
	narrow screens. The shared production conversion document is labeled
	“Migration conversion guidelines” and identified as shared guidance, not a
	technical skill. Original paths remain available as card tooltips.
- In the actual shared local app, the completed run showed all 13 recorded
	analysis-agent stickers, four columns at 1440 px, two at 390 px, no clipped
	cards or document overflow, and no active agent pulse for the completed run.
	The available saved writer manifest contains a skill-directory digest and
	99 events, but no writer skill-load events; it cannot establish a historical
	per-skill access receipt. The panel explicitly states that the separate writer
	is not included. No model run was started, so an active agent pulse remains
	unverified in a live run. JavaScript syntax and diff checks passed; the
	existing Safari backdrop-filter advisory remains. Not pushed or deployed.

## Earlier explicit model-picker implementation, historical

- The package-independent workspace model picker separates endpoint metadata
	discovery from route application and inference. Hosted discovery uses fresh
	OBO; local discovery uses the explicit labs profile. Selecting a route remains
	an explicit user action, with tool compatibility confirmation. A prior local
	16-module batch passed 346 tests; its follow-up measurements and scope limits
	are preserved in the 2026-11-09 model-picker history. This entry is historical,
	not proof of current hosted latency or route behavior.

## Historical single-fix notes consolidated

- The saved-writer provider request timeout correction remains in
	`databricks_models.py`: selected Sonnet 4.6 analysis roles use the recorded
	180-second request timeout, while other routes retain their prior value and
	provider retry stays disabled. A prior local seven-module batch reported 218
	passes; this is historical and does not verify current hosted latency.
- The writer tool-message correction preserves model/tool message fields while
	normalizing the Databricks ChatOpenAI wire payload. Its previous one-module
	check reported 10 passes. This does not establish that every provider or a
	new hosted writer run has been verified.

## Hosted release 2026-11-22, diagnostics and presentation

- Deployment `01f1e6b669a213f796ef16e1797c6851` was created at
	20:50:55 UTC and switched traffic at 20:53:35 UTC. The existing app reported
	RUNNING and ACTIVE with its six OBO scopes unchanged. Authenticated health
	returned HTTP 200. Hosted app, tracing and guidance JavaScript plus app CSS
	matched the staged files, and the root HTML matched after normalizing Windows
	line endings. The hosted UI and a new hosted model run were not exercised.
- The saved local end-to-end run and the exact renamed heading were checked in
	the actual shared app. JavaScript syntax, Python compilation, whitespace,
	staged source hashes and bundle validation passed. The post-run clock fix
	was not checked on another active run. No old test suites were run.
- Writer-stage diagnostics and lower-flicker log updates are included but not
	verified against the original hosted failure. The Delta permission-denial
	root cause, granular live writer token accounting, application-only log filter,
	MLflow integration, and requested application analytics tab remain open.
	No enterprise-grade observability or migration runtime parity is claimed.
- Before switching traffic, the current user's committed run history reported
	no active run; other users and uncommitted in-memory operations were not
	proven idle. The user explicitly requested deployment after the heading fix.

## Hosted Agent license-banner release, 2026-11-23 UTC

- At the user's explicit request, snapshot deployment
	`01f1e6d2884c1f3e9e329bc0464363df` was created at `00:12:12Z` and traffic
	switched successfully at `00:23:31Z`. The existing app ID remained
	`cfada07d-ffa6-4018-9080-293f5e577083`, state RUNNING/ACTIVE, one instance,
	MEDIUM compute, and the six previously configured OBO scopes. The active
	source is `/Workspace/Users/ssuman@bpcs.com/ORION-releases/orion-hosted-agent-license-banner-39e0b8b8152b402bb993f5082e10cbb7`.
- The 871-file staged snapshot passed `databricks bundle validate`. Workspace
	SOURCE exports of the root HTML, Agent license-banner JS, app JS/CSS, Support
	JS, official-search code, and staged LICENSE/NOTICE/SOURCE.json exactly
	matched the staged SHA-256 values before deployment. No deployment resources,
	scopes, or replicas were added.
- Read-only app metadata confirms the deployment. Direct HTTP GETs to the app
	URL returned HTTP 200 but served Databricks sign-in HTML for both `/` and the
	new banner asset. Therefore authenticated hosted health, static assets, banner
	visibility, and Support behavior remain unverified. No hosted browser/sign-in,
	AI inference, user upload, review submission, or customer Job was run. The user
	should sign in through their usual route and test the hosted UI. No claim is
	made that the license banner or full new Support flow was visible in hosted
	traffic.

## Migration run heading, local change

- The run summary heading and accessible section label now read
	`AGENTic MIGRATION RUN DETAILS`, as requested. This is a presentation-only
	change; no run data or generation control flow was changed.

## Local visible end-to-end release check

- One user-requested run, `12a6273b9e004a8f828d1f5f81d49c29`, used the
	selected `wkf_Load_tblcircuits_temp` workflow, bronze scenario, unchanged
	workflow-independent prompt and the ready Opus 5.5 endpoint. The actual
	local app reported `completed`, writer `completed`, `generation complete`,
	and elapsed `00:31:49`. The selected design v3 presented one notebook
	with task details and download/open controls. Source and target parameters
	include unresolved bindings, and runtime correctness and parity remain
	unverified. No generated notebook or workflow was executed or deployed.
- The live run exposed an undefined `write` call in the elapsed-summary clock.
	After the run completed, it was corrected to update the DOM directly. This
	post-run correction has not been exercised during another active model run.
- The Git comparison initially reported false deletions because the original
	local index is on an unborn branch. An isolated index based on remote
	`feature/dev` showed no deleted files. The original index was not modified.

## Model route and tracing panel compactness, local changes

- Scoped styling reduces panel/card padding and spacing, and overrides the
	global prose rule for 12 px high-contrast route, gateway and trace notes.
	Status meaning, controls and configuration checks are unchanged.
- In the actual shared local app at 1440 px, the panel measured 359 px high
	(previously 466 px), and the affected notes rendered at 12 px. Dark-theme
	note contrast measured 14.16:1 against the card. At 390 px, notes fit their
	cards and the document had no horizontal overflow. Theme and viewport were
	restored. `git diff --check` passed; existing unrelated CSS compatibility
	advisories remain. No model invocation, hosted UI, push or deployment.

## Run clock synchronization and evaluation spacing, local changes

- The run summary elapsed value, live activity clock, and agent-arena timer now
	share the same run start and one-second clock tick. Writer duration and last
	persisted writer-stage age also advance from that tick. The second live timer
	is explicitly labeled as time since the last successful status check, not as
	backend progress freshness.
- The reference-quality section is separated from operational metric cards by
	18 px of vertical spacing. This is a scoped CSS change.
- `node --check workbench/static/app.js`, Python `compileall` for `workbench`,
	`git diff --check`, and editor diagnostics passed. The actual shared browser
	was returned to project selection after a package-context reload and did not
	show a selected run, so final run-detail geometry and advancing clocks were
	not visually verified in this pass. No `test_*.py` files, agent/model runs,
	push, or deployment were performed. Local only; hosted behavior is unverified.

## Code-generation diagnostics and log refresh, local changes

- Candidate-writer status now records bounded, payload-free model/tool stages,
	request/tool start counts, staged-file/task counters, and the final writer
	state. Failed drafts expose a safe failure stage, allowlisted exception
	class, and filtered source frames. The hosted request marks `committing_delta`
	before final flush; permission diagnostics can include the validated UC table
	name, and a failure ID can be resolved from the same user's in-memory buffer.
	The background-operation journal preserves only the validated failure ID and
	phase, so the UI can fetch that exact diagnostic after a 503 without replaying
	the run. This supports the reported final Delta `PermissionDenied` path; no
	hosted retry or generation was performed.
- The run detail now preserves its DOM when an unchanged status poll arrives.
	Recorded Logs and hosted diagnostics update by stable event ID, retaining
	expanded disclosures; automatic log polling is 10 seconds, and failures retain
	the last successful rows. In the actual local app, opening a log disclosure and
	observing a polling interval produced zero log-list mutations and kept the
	disclosure open. The selected saved-run details likewise had zero mutations
	during repeated unchanged status polls.
- The model banner now distinguishes saved route configuration from tested
	inference. The Databricks workspace URL in the next-step hint is a validated
	HTTPS anchor. MLflow itself remains unintegrated/unconfigured; no trace URL or
	delivery is claimed. Unity Gateway inference migration remains deferred in
	[ORION-TODO.md](ORION-TODO.md).
- JavaScript syntax, Python compilation, Pylance diagnostics, and CRLF-aware diff
	checks passed. A safe in-memory check verified a synthetic PermissionDenied
	retains the validated UC table and phase but not its raw message. Local-only;
	not pushed or deployed. Active writer-phase rendering, hosted failure lookup,
	and live MLflow delivery were not exercised.

## Cumulative agent skill stickers, local changes

- The selected-run sticker panel now aggregates recorded loaded/read skill
	receipts across all six agents instead of filtering to only currently active
	agents or leaving the cumulative container empty. Each sticker identifies its
	agent and access record. During an active run, the current agent's recorded
	stickers receive the pulse; earlier stickers stay visible. A role link points
	back to the cumulative list, avoiding a second per-agent sticker copy. Missing
	receipts are described as unavailable, not proof that no guidance was present.
- In the actual local saved Lumen run, all 12 recorded stickers (two per agent)
	appeared, collapsed agent rows stayed about 61 px, and the 390 px viewport had
	no horizontal overflow. The selected run was completed, so active pulse state
	was not exercised. JavaScript syntax and CRLF-aware diff checks passed. Local
	only, not pushed or deployed.

## Agent activity text and duration formatting, local changes

- The run-history header now separates its concise title from the recorded
	activity label. Explanatory notes use a consistent callout treatment. Run
	durations and service-update age render as zero-padded HH:MM:SS, with the
	active elapsed and last-update values in labeled, tabular-number chips.
- In the actual local app, a saved completed run showed `00:31:19` and the
	shortened history heading beside its descriptor at 1440 px. At 390 px, the
	heading stacked cleanly and the document had no horizontal overflow. No
	active run was available, so advancing live-clock behavior was not exercised.
	JavaScript syntax and CRLF-aware diff checks passed; existing Safari CSS
	compatibility advisories remain. Local only, not pushed or deployed.

## Agent activity table and cost investigation, local changes

- Each agent's first-column cell is now a closed-by-default disclosure. The
	compact row shows only the agent name; opening it reveals that agent's
	recorded skills and context. Open state survives saved-run rereads. In the
	actual local app, all six cells were collapsed by default at about 61 px on
	desktop; one opened to show its content, then remained open after rereading
	the saved run. At 390 px, the expanded details fit without horizontal
	overflow. This is local only, not pushed or deployed.
- The saved local Lumen run showed six agent rows, each with its recorded
	guidance and context under the agent name. Counts were centered and bars
	stayed within their cells. An expanded context stayed open when the saved
	run was selected again. At a 390 px viewport, the table fit the viewport.
- After restoring and restarting the idle local server, the saved Opus run
	showed $36.46 when the rate lookup completed. The existing rate adapter reads
	Databricks published pricing assets and supports only the Opus 5.5 endpoint;
	it is an analysis-model estimate, excluding writer and shared compute. The
	hosted cost display and the endpoint used in the user's run remain unverified.
	No new model run, deployment or push was performed.

## AI agent terminology, local changes

- Agent-facing labels now say AI agent instead of role, including the result
	card, activity charts, model controls, catalog, readiness view and log
	filter. Internal identifiers and log-filter values remain unchanged. The
	selected-run prompt and saved run records were not modified.
- In the actual local app, a saved run showed the AI agent result heading,
	all six named agents, the per-agent call chart, and configured-agent summary.
	Each of the six agent cards was selectable. The Logs filter showed readable
	agent names with the original filter values. Eight changed JavaScript files
	passed syntax checks and the changed files passed the Windows line-ending
	diff check. Other dynamic states and hosted UI remain unverified. No new
	agent run, deployment or GitHub push was performed.

## Agent context help layout, local change

- The help paragraph was constrained by the general page paragraph
	`max-width: 100%` rule to the 20 px help control. A saved local run
	reproduced the issue: the popup was about 25 px wide and 2,559 px tall.
	The scoped CSS now anchors it to the heading and overrides the cap.
- The same saved-run view showed a 320 px wide popup on desktop and a
	270 px wide popup within a 390 px viewport. No agent run, hosted browser,
	deployment, or data change was performed. The section and skill stickers
	remain unchanged; a pre-existing Safari backdrop-filter advisory remains.

## User-facing package labels, local changes

- The Estate scope now displays the selected project and XML filename rather
	than a package hash or internal binding terminology. Package pickers show
	filenames, with numbered copies when names repeat; their underlying IDs are
	unchanged. Inventory rows, logs summaries, lineage labels, and validation
	status use more readable descriptions. Folder and knowledge-source hashes
	remain accessible in optional technical details.
- The selected local Lumen project showed its XML filename in the package
	picker and Estate scope. The Mappings list displayed 434 named records, and
	its detail selection was exercised. The Logs summary showed project and XML
	names. These are local observations, not a complete audit of every state or
	hosted UI. No model run or deployment was performed for these edits.

## Plain-language release, 2026-11-22

- Deployment `01f1e6312aa21268976847acaa79e314` was created at
	2026-11-22 04:57:06Z and switched traffic at 05:00:01Z. The app reported
	SUCCEEDED, RUNNING and ACTIVE. Authenticated health returned HTTP 200;
	the deployed HTML and four checked scripts matched the staged bytes.
- The agent diagram is mounted only in the top-left help modal. The modal
	heading names the migration agents. Model setup and suggested-action text
	was revised to explain that connecting or saving a model does not start a
	conversion. The served page and scripts reported `Cache-Control: no-store`.
	Hosted interactive behavior has not been inspected in a browser.
- Eight changed JavaScript files passed `node --check`; three changed Python
	files passed AST parsing; `git diff --check` passed. The staged snapshot had
	182 workbench files matching the workspace by SHA-256, and bundle validation
	passed. No model calls or test suites were run for this release. Existing
	CSS compatibility advisories remain.

## Inventory and modal release, 2026-11-22

- Deployment `01f1e62f70191bd0b01d8587ad454bd8` was created at
	04:44:44Z and switched traffic at 04:47:24Z. The app reported SUCCEEDED,
	RUNNING and ACTIVE; health returned HTTP 200 and four checked assets
	matched the staged snapshot. This deployment is superseded by the release
	above.
- The selected local package preloaded 5,092 version-pinned inventory records;
	the Mappings dialog showed all 434 entries. Record details remain separately
	loaded. The local help modal showed the migration diagram and Agent activity
	had no diagram mount. Hosted interaction remains unverified.

## Agent communication graph deployment, 2026-11-22

- Deployment `01f1e62b8b0f105c83c3177bcbf3698e` was created at
	2026-11-22 04:16:51Z and switched traffic at 04:19:33Z. App metadata
	reported SUCCEEDED, RUNNING and ACTIVE, with one instance and the same six
	user scopes. Authenticated health returned HTTP 200, and the graph script,
	stylesheet, agent arena, main script and HTML matched the staged bytes.
- The external snapshot contained 186 files, including 182 workbench files
	whose SHA-256 hashes matched the workspace. `databricks bundle validate
	--profile labs -t dev` passed. Before deployment, `node --check` passed for
	`agent-team-flow.js`, `agent-arena.js` and `app.js`, and `git diff --check`
	passed. The actual local graph was inspected as described below.
- The consolidated outstanding-work checklist is [ORION-TODO.md](ORION-TODO.md).
	It is a workspace document, not a hosted app asset. The graph illustrates
	configured communication and the writer review loop, not live activity or
	automated scored hill climbing. No new model run or `test_*.py` suite was
	performed. Hosted interactive behavior and conversion quality remain
	unverified; existing CSS compatibility advisories remain.

## Agent communication and skills view, local observations before deployment

- The numbered view now uses a directed graph with animated arrows between
	the workbench, six agents, shared findings, and the conditional writer.
	Optional supervisor delegation is marked separately. The actual local dialog
	showed 16 directional paths and changing arrow dash positions; reduced motion
	stopped the animation. The full graph fits the checked desktop width, and
	the 390 px view has a keyboard-focusable horizontal scroll area.
- Agent activity and the agent-structure dialog now display the six configured
	analyst roles in invocation order, their actual role identifiers and named
	skills, plus the conditional separate Spark writer. The workbench invokes each
	analyst with the findings recorded so far; a supervisor may request focused
	follow-up. This view describes configuration, while the existing run view
	remains the source of recorded call counts and skills.
- The supervisor is labeled Lead migration design coordinator, reflecting its
	ability to submit or correct a logical design. The separate Spark and SQL
	code writer drafts candidate code using the configured artifact-role model.
	The graph now shows static checks, AI review when available, and an optional
	feedback-to-writer revision loop. This is iterative refinement, not a scored
	hill-climbing optimizer; saving a candidate does not establish parity.
- The local dialog showed 18 directional paths, readable role and writer labels,
	and the check/revision nodes. Reduced motion disabled the graph animation;
	mobile uses a keyboard-focusable horizontal scroll area. Syntax checks passed
	for the three changed JavaScript files. The reported observation of the lead
	writing code was not independently reproduced or attributed to a saved run.
- The actual local app showed both views with all six analyst names and skills,
	plus the writer. Desktop and 390 px mobile layout were inspected; mobile
	showed no overflowing cards. Dark theme and reduced motion were checked.
	`node --check` passed for the new script and `app.js`; `git diff --check`
	passed. Existing CSS compatibility advisories remain. No model run,
	`test_*.py` suite, or hosted deployment was started for this change.

## Named skills and animated agent diagram deployment, 2026-11-22

- The agent-structure diagram animates its connections and role highlights.
	The local browser confirmed animation is running and that reduced-motion
	mode disables it. The diagram remains an illustrative architecture guide.
- The named specialist skills and separate Spark writer skill are included;
	original skill files and historical receipts remain available. No new agent
	run was performed, so output quality and hosted live behavior remain
	unverified. Changed skills alter the current input fingerprint and may cause
	existing designs to be reported stale without rewriting their history.
- JavaScript syntax, Python diagnostics, skill-path checks, the local skill
	catalog, a saved-run view, 181 staged workbench hashes, and bundle validation
	passed. No `test_*.py` files were run. Existing CSS compatibility advisories
	remain.
- Deployment `01f1e626e0a7116798a3db5a02744eef` was created at
	2026-11-22 03:43:27Z and switched traffic at 03:46:08Z. App metadata reported
	RUNNING/ACTIVE with the same six requested scopes. Authenticated health
	returned HTTP 200 and eight hosted UI assets matched staged bytes. Hosted
	animation and a fresh model run were not exercised.

## Agent skills and context, 2026-11-22

- Deployment `01f1e622ef8e1a088cfd728760495049` switched traffic at
	03:17:53Z. The app was RUNNING/ACTIVE, authenticated health returned HTTP
	200, and the eight checked hosted assets matched the staged snapshot. The
	prior project-layout, copy, and skills/context updates were included.
- After that deployment, the local context view was simplified: an accessible
	help control explains the evidence, SHA-256 values are no longer displayed
	in the normal view, and technical source IDs remain optional. The saved
	records were not modified. A historical local run was checked at desktop
	and mobile sizes, including dark mode. These follow-up changes are not in
	the deployment above.
- Anthropic's official public skills repository was downloaded separately at
	`vendor/anthropic-skills`, revision `e4bb5d960d87126ff3b3a8af1a104b7db67560ba`.
	Its contents have not been activated in ORION. The user selected the named
	skill migration with compatibility. Six specialist loaders and the separate
	Spark writer now point to named enhanced skills, while the old skill files
	remain available for historical paths. The current local server was
	restarted while idle and its skill catalog returned all seven named skills.
	A saved run continued to show its actual historical `review` receipts.
	New skill instructions and files change the current skills fingerprint, so
	old designs may be reported stale; their saved records are not rewritten.
	No new model run or runtime-quality evaluation was performed. These changes
	are local only and require deployment before hosted runs can use them.

## Timer and UI deployment, 2026-11-22

- The live agent clock now uses the parent run start instead of switching to
	the candidate-writer start. The earlier ORION sizing, role-column styling,
	operation disclosure, wording, and call-list alignment changes are included.
- JavaScript syntax checks passed for `app.js`, `agent-arena.js`, and
	`hosted-intake.js`. The actual local app showed a saved run's 31m 19s
	duration, six role rows, a 132px ORION block, and aligned desktop call lists.
	No new agent run was invoked, so the active writer transition remains
	unverified in a live run. No `test_*.py` files were run. The existing CSS
	Safari `backdrop-filter` advisory remains.
- The staged 174 workbench files matched their source hashes and the bundle
	validated. Deployment `01f1e61a91451a24b82cb82aa1fb5f76` was accepted at
	2026-11-22 02:15:20Z and switched traffic at 02:18:11Z. App metadata reports
	RUNNING/ACTIVE with the same six requested scopes. The user authorized
	deployment despite the running hosted app. That run's final state and the
	new hosted UI's live timer behavior remain unverified.

## Local typography pass, pending deployment

- Shared page prose, headings, and control weight were adjusted for a calmer,
	more readable hierarchy. Long section headings wrap; the agent cost panel
	distinguishes numeric amounts from longer unavailable messages.
- The actual local app's twelve navigation pages were inspected at desktop and
	mobile widths, and the saved agent view was checked in light and dark themes.
	The desktop model/tool scroll areas remained aligned and the saved timer
	stayed at 31m 19s. This is not exhaustive verification of every dynamically
	loaded state or an active agent run. No model or `test_*.py` run was started.
- On the user's request, a new staged snapshot passed 174 source SHA-256
	comparisons and `databricks bundle validate --profile labs -t dev`. Deployment
	`01f1e61e428910bb8cca1a01de6718c2` was accepted at 2026-11-22 02:41:46Z
	and switched traffic at 02:44:22Z. The app is RUNNING/ACTIVE with the same
	six requested scopes. Authenticated health returned HTTP 200, and all three
	changed stylesheets matched the staged bytes. Hosted appearance remains for
	user review; no hosted browser or new agent run was exercised.

## Local project layout and user-facing copy, pending deployment

- The project summary and XML member details have reserved, keyboard-focusable
	scroll areas. The create-project card provides next steps without changing
	project creation. During actual local project selection, the selection card
	remained 872 px before, during, and after the XML list loaded; at 390 px it
	remained 908.5 px in the earlier mobile check. No project was created.
- Updated prominent page introductions, the Estate review banner, agent
	evaluation and missing-evidence copy, folder errors and progress, and
	proposal guidance to explain user actions and preserve actual limitations.
	The agent instruction prompt and execution behavior were not changed.
- The actual local app's twelve pages were inspected at desktop and mobile
	widths. Visible page headings showed no clipping in the inspected states;
	a saved run retained its elapsed time and aligned model/tool lists. Light
	and dark themes were checked for the project card and Estate banner. Dynamic
	error, active run, and all hosted states were not exhaustively exercised.
- `node --check` passed for the five changed JavaScript files. The editor
	reported no JavaScript or HTML errors and only existing CSS compatibility
	advisories. No `test_*.py` files, agent runs, or deployment were started.

## Requested checks

| Request | Current evidence / outstanding work |
|---|---|
| Single-workflow authority, optional cohort | Existing local UI/source-disposition verification; external call remains informational |
| Amber external-call task, other cards white | Prior local browser verification; retain after changes |
| Correct condition arrows | Prior local browser verification and published upstream-value negative check |
| Preserve workflow selection | Verified on prior refresh/reload; recheck current app |
| Smaller regular-weight saved-design dropdown | Prior local 13px / 400 browser result |
| Actual notebook paths below YAML | Four v25 paths verified, owning-task link works |
| Native Jobs YAML | v27 available; fresh v29 blocked on nested email widget declarations despite AI review_ready, unresolved release defect |
| Source/target identity not assumed equal | Separate runtime bindings and exact source-candidate receipt |
| Destination counter table | Live metadata search: absent in all schemas of the three remembered destination catalogs; not a bound or approved destination |
| Catalog/schema/table in three colors | Local change, current browser verification pending |
| Blueprint header logo | Actual local app: supplied color SVG immediately after Databricks in light mode; supplied transparent white PNG in dark mode. Both load; boxes match Databricks at 132×22 desktop and 99×16.5 mobile, no horizontal overflow. Repeated three desktop reloads and two mobile reloads in both themes. |
| Variable-to-parameter mapping | Mapping guidance and formatted inventory; complete source-to-use correspondence not independently established |
| Notebook top overview and per-cell business logic | Active guidance updated; fresh generated evidence and runtime behavior still require review |
| Notification is secondary | Send-capable code generated in v25; missing SMTP settings are runtime configuration, not code-generation blockers; no mail sent |
| Rejected submissions separate from failures | Actual middleware check passed; updated backend now loaded locally; historical records not rewritten |
| Agent evaluations live | Local scorecards/browser verified on selected historical receipt, not independently measured groundedness or business correctness |
| Latest Agent event layout | Live local browser: event is a separate row after the equal-height activity/evaluation panels; includes role, type, local timestamp, run/workflow/scenario, elapsed offset, endpoint and reconciled call counts |
| Six agent call lines | Live local browser: six distinct cumulative per-role model-call lines from all 44 timestamped model turns; counts supervisor 10, discovery 9, semantics 6, architecture 9, validation 6, artifact 4 |
| Operational KPI graph and reference KPIs | Live local browser: KPI trend chart distinguishes sampled operational snapshots from quality; visible Reference-based quality KPI section correctly says not measured without labeled references |
| Agent scorecard reconciliation | Live local browser: tool acceptance 105/115 = 91%, using terminal start=accepted+rejected reconciliation; model success 44/44, roles 6/6, drafted dispositions 5/5 |
| Missing LangSmith credentials not Failed | Configuration and browser checks passed; no tracing export enabled |
| MLflow mention | Explicit Not integrated; no fabricated trace delivery |
| Databricks notebook attachment | App-managed session code added, no overwrite; live cloud roundtrip explicitly deferred to user |
| Return edited Databricks notebook to existing app artifact | Preview/save/revision/cleanup code added; offline unit checks passed initially, consolidated regression not yet green; live testing deferred to user |
| Per-run memory / multiple model design | Proposed documentation only; no new endpoint selected or expanded mesh activated |
| Hosted authority | Existing hosted path uses committed Delta metadata and Volume artifacts; SQLite is reconstructed ephemeral cache, not hosted authority |
| Hosted loading timeout | Deployment 01f1e614055a1cbd994f87869edd664d is active. The app-proxy timeout remains a separate ~120s platform boundary. Operation 03303804ac0f42e49539b7139d1a8e01 remains unreplayed and unconfirmed. |
| Two full suite passes | Pending; all failures/skips must be reported, not hidden by exclusions |
| Two fresh visible agent conversions | Actual local v27/v29 runs completed and saved four notebooks each. Not two release passes: target not verified; v29 native YAML fails. See visible-run receipt |
| Deployment | Snapshot 01f1e614055a1cbd994f87869edd664d SUCCEEDED from 2026-11-22 01:28:28Z to 01:31:46Z. App RUNNING; hosted agent behavior and regression suite remain unverified. |

## Hosted workflow classification redeployment, 2026-11-22

- Deployment `01f1e614055a1cbd994f87869edd664d` switched traffic successfully
	at 01:31:46Z. App readback confirmed RUNNING/ACTIVE, six requested scopes and
	one instance. `/healthz` returned HTTP 200; deployed `app.js`,
	`context-picker.js`, and `app.css` matched the verified staged bytes.
- The pre-deployment status check reported no active run for the current
	user/project/package; this was not an all-user idle assertion. No model or
	job was invoked. No `test_*.py` files were run. The hosted workflow-picker
	presentation and hosted live-agent behavior remain browser-unverified.

## Hosted duplicate activity panel, pending deployment

- The purple status ribbon remains across tabs. The existing detailed hosted
	operation disclosure, including its recorded-operation reconnect control,
	has been moved from the shared page header into the Logs page. The change
	does not cancel, replay, or delete an operation, and does not change polling.
- `node --check workbench/static/hosted-intake.js` passed. A read-only fetch
	from the shared local app returned the updated script and confirmed the Logs
	mount and retained status ribbon. No hosted operation was created to test
	the panel and no `test_*.py` file was run. Hosted behavior remains unverified
	until the change is deployed and exercised in the hosted app.

## Model and tool call alignment, pending deployment

- The model and tool call lists were both capped at 480 px but started at
	different positions because only the tool side had explanatory text. In a
	saved local run, their scroll areas initially differed by 55 px vertically.
- The two open call-history panels now share summary, note, and list rows in
	browsers supporting CSS subgrid and `::details-content`; narrow screens retain
	the stacked layout. Local browser verification with saved run
	`b0bbfb4360ff4259806fc10decc6506a` measured identical scroll-area top and
	bottom positions, with independent scrolling and working disclosure controls.
	The fallback in browsers without these layout features retains the previous
	layout and has not been separately validated for alignment.
- The current hosted run was not changed, cancelled, or restarted. No model or
	`test_*.py` run was started. This source change is not deployed to hosted.

## Hosted live-run handoff, 2026-11-22

- The user supplied a hosted audit record showing `POST /api/agents/run` returned
	HTTP 202 after 5742.74 ms at 2026-11-22T00:45:47.389692+00:00. This is the
	inner request acceptance, not evidence that the agent completed or that its
	results were committed. The related browser screenshot showed the outer
	operation stuck on `starting`.
- Source changes expose the exact accepted run ID as a user-scoped, process-local
	observation, labeled not yet committed. The agent status UI can read that run
	while the original hosted operation continues and remains responsible for
	reporting the Delta commit. No additional inference, replay, or automatic
	execution is introduced. Restart or eviction can lose this observation.
- The selected-workflow note reports only the last confirmed hosted package
	session, not a guarantee that its in-memory state still exists. Guided model
	endpoint selection and the request-details disclosure are source changes too.
- These changes are deployed but not verified in a hosted run. The existing
	operation journal, OBO checks, worker queue, status polling and final Delta
	commit remain, so zero waiting or instantaneous startup is not established.
	The user's previously active hosted invocation was interrupted by the
	explicitly requested app stop; a clean cancellation or final result is not
	established, and the invocation was not replayed.
- An allowlisted 178-file deployment candidate was staged on 2026-11-21 at
	`C:/Users/SAKETS~1/AppData/Local/Temp/orion-hosted-live-activity-20261121-d7ef95fe0b224052885a3e951a38d7ad`.
	The six changed executable files matched the workspace SHA-256 values; four
	staged JavaScript files passed `node --check`, and 108 staged top-level Python
	modules parsed with Python 3.11 grammar. The existing `labs` refresh token was
	invalid, so no remote bundle validation, upload, or deployment was attempted.
- After the user requested CLI refresh, the `labs` profile was refreshed. At
	2026-11-22T01:09:52Z the app was RUNNING, and a scoped status read returned
	`run_status_verified=true`, `active_run_present=true`, with run
	`e2348c4aa3674bd6b71269569d601ff9` running in `agent_analysis`. This status
	applies only to the observed user/project/package, not all app users.
- All 174 staged workbench files matched current source SHA-256 values and the
	staged bundle validated successfully. A new snapshot was uploaded to
	`/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261121-live-activity-d7ef95fe0b224052885a3e951a38d7ad`.
	The app was not switched or redeployed because the reported run was active.
- After the user's explicit stop-and-deploy request, app stop was requested at
	2026-11-22T01:14:18Z and compute was STOPPED at 01:15:28Z. Direct deployment
	on the stopped app was rejected by the platform; the app was started and the
	final snapshot deployed successfully, ID `01f1e612c43d18cfa0bf1a1ed3cf6c42`,
	created at 01:19:30Z and traffic switched at 01:22:16Z. Post-deployment app
	read reported RUNNING/ACTIVE, the same six requested scopes and one instance.
	`/healthz` returned HTTP 200, and the deployed app, intake, guidance, and
	workspace-access scripts matched the final stage SHA-256 hashes. No hosted
	browser or new agent run was used; live progress and semantic parity remain
	unverified. The deployed snapshot precedes any later workspace edits.
- Source syntax checks passed for the affected Python and JavaScript. In the
	actual shared local app, a project and XML were selected and Agent activity
	opened; workflow selection was verified without invoking an agent. The local
	viewport at 1156 px showed 22 px horizontal overflow in existing header/arena
	elements. Hosted live acceptance and cross-user isolation were not tested.
	No `test_*.py` files were edited or run.

## Hosted warm-session follow-up, 2026-11-20

- Deployment `01f1e52d18a51cda9e78973148a772aa` is active and `RUNNING` at the
	existing app URL. Source is the allowlisted 178-file snapshot under
	`/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261120-warm-session-6682018b4bff42069e9b7e6fca12a069`.
- Warm retained requests now reuse the initiating user's retained app, Delta
	storage root, and loaded selected-package index. They skip repeat UC provisioning,
	full manifest/table authority scans, and XML-derived artifact hydration. Each
	request still resolves fresh OBO identity. Mutations still serialize per user,
	commit a new Delta generation, and update the retained generation marker. Cold
	start/session eviction still requires a restore.
- Hosted activity no longer displays `DEBUG ONLY` labels. Worker queue and cold
	restore progress labels are more explicit. Workflow selection remains editable
	while an unrelated settings operation is active; invoking remains blocked until
	that operation finishes.
- Validation performed: `node --check workbench/static/hosted-intake.js`,
	`node --check workbench/static/app.js`, Python AST parse of `workbench/hosted.py`,
	Pylance diagnostics, and staged `databricks bundle validate`. No `test_*.py` files
	were edited or run. No model inference, customer Job, or hosted UI smoke was run.
	Hosted latency and workflow selection behavior require the user's actual app test.

## Hosted snapshot deployment, 2026-11-20

- Existing app: `orion-migration-workbench`, ID `cfada07d-ffa6-4018-9080-293f5e577083`.
- Deployment ID: `01f1e529b8db1b09a797e7055b94022c`; CLI reported `SUCCEEDED` and `Traffic switched successfully`.
- Deployment interval: 2026-11-20T21:31:18Z to 2026-11-20T21:33:53Z.
- Source: `/Workspace/Users/ssuman@bpcs.com/ORION-releases/20261120-fde924d15cde475cad18b12a7bae6024`, an allowlisted 178-file snapshot. Staged bundle validation passed before sync/deploy.
- Post-deploy `apps get` confirmed `RUNNING` / `ACTIVE`, the new deployment ID and source path, the six existing requested scopes, and min/max instances of 1. App URL: https://orion-migration-workbench-1866518241053589.9.azure.databricksapps.com.
- No `test_*.py` files were edited or run. No model inference, customer Job, fixture DDL, or hosted browser smoke was run. The user will perform hosted UI testing. Deployment success is not a regression, latency, or workflow-parity result.

## Proactive UI integrity checks

- The desktop left navigation no longer scrolls. Its workspace label/footer and
	item sizing adapt at shorter heights so all 14 destinations fit. In the actual
	shared local browser, checked 1152 px width at heights 1127, 800, 700, 500,
	460, 400, 320 and 240 px: the sidebar and navigation had zero scroll range,
	all 14 buttons stayed inside the pane, and no items overlapped. Reduced-height
	mode hides the icons at 460 px and below while keeping button names and actions.

The maintained release checklist covers these categories on every tab, not only
items a user reports. Each category requires observed evidence; unchecked is not
assumed passing. This is a release gate, not a promise that zero bugs exist.

| Category | Required checks | Current evidence |
|---|---|---|
| Navigation | All twelve tabs, correct title, active-page state, preserved project/package/workflow, keyboard access | Live desktop tab walk completed, no page-level horizontal overflow |
| Text | Exact readable sentences, inline emphasis, no unnecessary width caps or forced word breaks, long identifier wrapping | Early Agent introduction and guidance width caps reproduced; two scoped CSS rules corrected; responsive regression pending |
| Visible references | Nonempty links, available images, precise target/version, no stale task destinations | Live twelve-tab walk found no empty visible anchors or broken images |
| Notebook paths | Saved path list, task-to-file ownership, all files accessible after refresh, no false absence messages | Current live v29 task grid exposes all four files; explicit below-YAML saved-path section was not observed before reload, needs recheck |
| Downloads | Exactly one selected-task control, correct filename and complete original UTF-8 bytes, no transformed/sanitized code | All four actual v29 download buttons produced Blob bytes whose SHA-256 equals each saved reviewer artifact hash; integrated browser download-event delivery unverified |
| Notebook controls | Review, download, native editor attachment, logical-condition exception, no duplicate controls or task descriptions | Each actual v29 notebook task has one Download and one Open in Databricks control, expected cells 2/6/2/4. Cloud opening not invoked |
| Graphs | Correct edges/outcomes, readable labels, zoom/focus, one task inspector, external-call distinction | Actual five-task v29 graph inspected; keyboard/mobile/zoom checks require regression evidence |
| Agent run graphs | Per-role call counts use actual timestamped model-turn receipts; operational KPI snapshots are labeled as sampled snapshots; reference-based quality is not inferred | Selected v29 actual saved run: exactly six SVG lines, 44 complete turns, six role totals verified against live status receipt; visible graph reviewed |
| Loading and errors | Busy versus idle, optional/unavailable versus failed, actionable retry, no stale responses, no client initial-load cutoff | Actual two visible runs progressed through analysis/writer to terminal; v29 YAML error still too generic |
| Consent and safety | No navigation-triggered model/Job/business writes, selected scope, disabled unsafe actions, source/target separation | Live navigation/download audit issued no generation or execution actions; no deployment |
| Responsive/themes | Desktop/mobile in both themes, no clipped or overlapping controls/text, focus and reduced-motion intact | Desktop live walk done; current four-theme/viewport wrapping regression pending |
| Data integrity | Original manifests/notebooks/history unchanged by viewing/downloading, hash-bound receipts | Actual v29 downloaded Blob hashes match saved artifacts; full regression and hosted confirmation remain pending |

The older offline browser processes started November 14 were identified by exact
test command/PIDs and stopped as stale. Their unfinished run is not counted as a
passing regression. The local app was not stopped.

## Blueprint header logo, 2026-11-16

Integrated the provided `blueprint-logo.svg` for light mode and transparent white
`blueprint-logo.png` for dark mode as static app assets. Actual local browser
verification across three desktop reloads and two mobile reloads, each in both
themes: image `complete` and natural width > 0, correct active assets/order, equal
Databricks and Blueprint image boxes (132×22 desktop, 99×16.5 at 390px mobile),
and no document horizontal overflow. Header screenshots were inspected in both
themes at mobile and desktop. No model run, Databricks action or deployment was
performed.

Visible local run results: [v27/v29 consistency receipt](local-visible-agent-consistency-2026-11-16.md).
Edit return: [app-managed notebook revisions and cleanup](notebook-edit-return.md).

## Agent activity presentation pass, 2026-11-16

Performed in the user's shared local app page, not an offline browser:

- JavaScript syntax check: `node --check workbench/static/app.js` passed.
- The live DOM showed equal-height activity and evaluation columns within one
	672 px (42 rem) observation viewport. Content flows naturally inside the columns;
	a single parent scrollbar handles tall content. The columns have no outer border
	or internal scrollbar. Count cards and active-call entries use spacing rather
	than separator lines; there was no horizontal overflow.
- Reference-based quality KPIs are visible, not inside a collapsed details element.
	Precision/recall, citation correctness, groundedness, behavior parity, and mapping
	authorization remain explicitly not measured without a labeled evaluator.
- Latest event is structurally after the two-panel section, with the selected
	run/workflow/scenario and its timestamp/context.
- The visible six-line chart is derived from the exact complete 44-turn receipt;
	each legend count reconciles to the per-role counters. No model run was started.
- The 91% tool acceptance score is based on 105 accepted / 115 terminal tool
	submissions, with 10 recorded rejections and zero failures reconciled because
	all 115 started calls are accounted for and the completed receipt has no in-flight
	calls. Missing failure fields on incomplete/legacy records remain unknown.
- User-requested validation run a28092ec1a484674b31490bc48137bd8 was explicitly
	stopped from its visible Stop control and reached terminal `cancelled` status at
	2026-11-16T04:23:56.314084Z. It never entered candidate writing; no new notebook
	artifact or native YAML result is claimed from that stopped run.
- Reference KPI typography was reduced in the live local UI from 13 px list text
	to 10 px with 13.5 px line-height. The first CSS rule lost to the later, more
	specific shared prose-format selector; the final scoped `#run-detail` selector
	now wins. The rendered reference block reduced from 295 px to 162 px and all
	four not-measured KPI lines fit visibly. No run was active during reload.

## Destination metadata snapshot

Read-only authenticated `labs` metadata checked each catalog and all listed schemas:

- orion_bronze_test_ssuman: 56 tables listed, no ae2e_job_dep_chk_count match.
- orion_silver_test_ssuman: 50 tables listed, no ae2e_job_dep_chk_count match.
- orion_gold_test_ssuman: 55 tables listed, no ae2e_job_dep_chk_count match.

Exact lookup of catalog.tgt_oracle_220c515707fa.ae2e_job_dep_chk_count in each
catalog returned TABLE_DOES_NOT_EXIST. Current absence does not prove whether a
past table was omitted, removed or never created. No CREATE or business write was
performed. Choosing a new intended destination layer must not be confused with
schema-compatible fixture discovery.

Agent activity design: [responsibilities, memory and routing](migration-agent-responsibilities-memory-and-model-routing.md).
Evaluation definitions: [scorecard](agent-evaluation-scorecard.md).
Source assessment: [v25 coverage](migration-coverage-v25.md).

## Hosted timeout trace, 2026-11-16

Read-only `databricks apps get/list-deployments` confirmed the active Databricks
app is RUNNING deployment 01f1e14c095715a89139602d5fd11ae7. Exported only its
public static request scripts and compared hashes with local files:

- Deployed `hosted-intake.js`: SHA-256
	`6EBEDE5466EC17C5F3510E81E69DCD1FE768152CC16792A4AC282D0B5A811844`; it defines
	`REQUEST_MS = 110000` and aborts `request()` whenever the outer HTTP method is
	not GET. A hosted logical read is sent as `POST /api/hosted/operations`, so this
	abort applies even when the operation envelope's inner method is GET.
- Local `workbench/static/hosted-intake.js`: SHA-256
	`9E90A318B4042DA088F3BA83C5F9861AA79D6196B5A48C340D2082A71A719D6F`; its
	corresponding `request()` has no elapsed-time abort.
- Deployed `scoped-reads.js` defines `READ_MS = 120000` for reads without reported
	progress. Local `workbench/static/scoped-reads.js` explicitly has no app elapsed
	deadline. These deployed/local hashes differ.
- Databricks Apps' proxy timeout is a separate platform boundary and is not
	removed by browser code.

The hosted browser session redirected to Databricks sign-in, so operation
03303804ac0f42e49539b7139d1a8e01's per-user Delta status could not be verified
here. No retry or replay was made. The changed local request handling has not been
deployed. Release remains blocked by the separately recorded target-mapping and
native Job parameter/code-generation validation findings.
