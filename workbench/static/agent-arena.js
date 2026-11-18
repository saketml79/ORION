/* Original observational arena. No requests, persistence, timers, or inference.
 * Parent integration: dispatch on document (or window) CustomEvent:
 * orion:agent-run detail = {id,status,output?,workflow_id,created_at?,finished_at?,usage?}
 * orion:loop-run detail = actual loop record including attempts[].
 * orion:model-list-prices detail = the model-list-prices-v1 API response.
 * Null detail clears the display. Dispatch selected records after scope guards.
 * Full records replace snapshots; absent fields remain unknown. Loop role/evidence
 * displays use the latest recorded attempt, never infer live specialist activity.
 */
(() => {
  'use strict';
  const mount = document.getElementById('agent-arena');
  if (!mount || mount.dataset.initialized) return;
  mount.dataset.initialized = 'true';
  const roles = ['supervisor', 'discovery', 'semantics', 'architecture', 'validation', 'artifact'];
  const roleLabels = {supervisor: 'Lead migration analyst', discovery: 'Informatica package analyst',
    semantics: 'Transformation semantics analyst', architecture: 'Lakeflow workflow designer',
    validation: 'Independent validation analyst', artifact: 'Artifact requirements analyst'};
  const purposes = ['Coordinates the specialists and reconciles their source records.',
    'Inspects Informatica folders, definitions, references, tasks, and connections.',
    'Traces mapping expressions, SQL phases, write behavior, and control conditions.',
    'Proposes a Lakeflow task graph while preserving the original workflow behavior.',
    'Checks source coverage, unsupported behavior, and validation gaps.',
    'Checks what YAML, notebook, test, and release artifacts are actually authorized.'];
  const findingStatus = {supported: 'Agent-reported observation', needs_information: 'Incomplete analysis',
    unsupported: 'Agent-reported unsupported behavior', failed: 'Analysis failed'};
  const array = (value) => Array.isArray(value) ? value : [];
  const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
  const safe = (value) => window.orionRedaction.text(value ?? 'Unknown').slice(0, 4000);
  function node(tag, text = '', className = '') {
    const element = document.createElement(tag);
    element.className = className; element.textContent = safe(text); return element;
  }
  function write(element, value) {
    const text = safe(value); if (element.textContent !== text) element.textContent = text;
  }
  function svgNode(tag, attrs, parent) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    parent.append(element); return element;
  }
  const arena = node('section', '', 'agent-arena');
  arena.setAttribute('aria-labelledby', 'arena-heading'); arena.dataset.active = 'false';
  const header = node('header', '', 'arena-header');
  const heading = node('div'); heading.append(node('span', 'ORION / OBSERVATION DECK', 'arena-kicker'));
  const title = node('h2', 'Agent run'); title.id = 'arena-heading'; heading.append(title);
  const badge = node('span', 'Awaiting explicit run', 'arena-badge'); header.append(heading, badge);
  const status = node('p', '', 'arena-status'); status.id = 'arena-status';
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.setAttribute('aria-atomic', 'true');
  const layout = node('div', '', 'arena-layout');
  const middle = node('div', '', 'arena-middle');
  const telemetry = node('aside', '', 'arena-telemetry'); telemetry.setAttribute('aria-label', 'Recorded run facts');
  telemetry.append(node('span', 'RUN STATUS', 'arena-kicker'));
  const facts = node('dl'); const values = {};
  for (const [key, label] of [['id', 'Run ID'], ['workflow', 'Workflow'], ['attempts', 'Specialist results'],
    ['citations', 'XML references cited'], ['tokens', 'Total tokens'], ['models', 'Model calls started / completed'],
    ['tools', 'Tool calls started / completed'], ['created', 'Created'], ['finished', 'Finished']]) {
    const value = node('dd', 'Unknown'); value.dataset.fact = key; values[key] = value;
    facts.append(node('dt', label), value);
  }
  telemetry.append(facts);
  const stage = node('div', '', 'arena-stage');
  const platform = node('div', '', 'arena-platform'); platform.setAttribute('aria-hidden', 'true');
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 600 600'); svg.setAttribute('focusable', 'false'); platform.append(svg);
  const tracks = svgNode('g', {class: 'arena-tracks'}, svg);
  for (const radius of [130, 195, 260, 282]) svgNode('circle', {cx: '300', cy: '300', r: String(radius)}, tracks);
  const spokes = svgNode('g', {class: 'arena-spokes'}, svg);
  for (const [x, y] of [[300, 40], [525, 170], [525, 430], [300, 560], [75, 430], [75, 170]]) {
    svgNode('path', {d: `M300 300L${x} ${y}`}, spokes);
  }
  const orbit = svgNode('g', {class: 'arena-orbit'}, svg);
  svgNode('circle', {cx: '300', cy: '300', r: '242'}, orbit);
  for (const [x, y] of [[300, 58], [510, 420], [90, 420]]) svgNode('circle', {cx: x, cy: y, r: '5', class: 'arena-packet'}, orbit);
  stage.append(platform);
  const core = node('div', '', 'arena-core');
  core.append(node('span', 'O', 'arena-core-mark'), node('strong', 'ORION'), node('span', '6-agent team'));
  const grid = node('div', '', 'arena-roles'); grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', 'Inspect recorded role findings');
  const cards = new Map(); let selected = 'supervisor', findings = [];
  const inspector = node('aside', '', 'arena-inspector'); inspector.setAttribute('aria-labelledby', 'arena-role-heading');
  const roleHeading = node('h3'); roleHeading.id = 'arena-role-heading';
  const rolePurpose = node('p', '', 'arena-role-purpose');
  const roleFindings = node('div', '', 'arena-findings');
  inspector.append(node('span', 'ROLE RESULT', 'arena-kicker'), roleHeading, rolePurpose, roleFindings,
    node('p', 'Findings are advisory. Supported does not mean approved, executed, or semantically equivalent.', 'arena-boundary'));
  const outcome = node('section', '', 'arena-run-outcome'); outcome.hidden = true;
  function showDesignOutcome(submission, source) {
    outcome.replaceChildren(); outcome.hidden = true;
    if (!source || !current || kind !== 'agent' || current.status === 'queued' || current.status === 'running') return;
    const accepted = Number(submission?.accepted_count ?? array(source.redesigns).length);
    if (accepted > 0) {
      outcome.hidden = false;
      const deterministic = source.design_origin === 'deterministic_workflow_compiler';
      const drafts = array(source.candidate_code_drafts);
      const draftText = drafts.length
        ? ` Candidate code draft: ${drafts.map(item => `${safe(item.status)}${item.manifest_status ? ` (${safe(item.manifest_status)})` : ''}`).join(', ')}. Open the saved proposal to view its task grid, YAML when all tasks are supported, and exact notebook code.`
        : ' No candidate code receipt was returned.';
      outcome.append(node('h3', 'Logical design draft created'),
        node('p', `${deterministic ? 'A deterministic XML-bound compiler' : 'The agent team'} produced ${accepted} validated logical task outline${accepted === 1 ? '' : 's'}.${draftText} Approval, execution and deployment are not included.`, 'arena-outcome-summary'));
      return;
    }
    const rejections = array(submission?.rejections);
    const supervisor = array(source.findings).find(finding => finding?.role === 'supervisor');
    const legacySchemaFailure = /failed to submit redesign/i.test(String(supervisor?.summary ?? ''));
    const noDesignRequested = submission?.requested === false;
    if (noDesignRequested || (!submission && !legacySchemaFailure && source.status !== 'failed')) return;
    outcome.hidden = false;
    outcome.dataset.state = 'failed';
    outcome.append(node('h3', 'Conversion proposal not produced'),
      node('p', legacySchemaFailure
        ? 'The run reached design validation, but no design was accepted. This is a workflow-proposal format error, not a Unity Catalog table/schema error.'
        : 'The requested design did not pass validation. Findings below are not a conversion deliverable.', 'arena-outcome-summary'));
    const friendly = issue => {
      const field = String(issue?.field ?? '');
      if (/implementation_kind/.test(field)) return 'Task type must be supported by the Lakeflow proposal format.';
      if (/edges\.\d+\.evidence_ids|edge.*evidence/i.test(field)) return 'Each dependency must cite at least one retrieved XML control link.';
      if (/edges\.source\/target|endpoint/i.test(field)) return 'Each dependency must point to task IDs present in the proposed task list.';
      if (/coverage/.test(field)) return 'Every original task instance must appear exactly once in the coverage table.';
      if (/cadence/.test(field)) return 'Separate the workflow launch schedule from an in-run timer wait.';
      if (/edges\.\d+\.(condition|outcome|source)/.test(field)) return 'Preserve the exact threshold on a condition task and identify the true/false branch.';
      if (/scoped_unresolved/.test(field)) return 'Keep the affected connection, email, or asynchronous child-launch gap visible for human review.';
      if (/artifact_plan/.test(field)) return 'List notebook responsibilities in the artifact plan; do not imply notebook code was generated.';
      if (/evidence_ids/.test(field)) return 'Citations must be full canonical IDs retrieved by the submitting specialist.';
      return String(issue?.issue ?? 'The proposal did not satisfy a required field.');
    };
    const issues = rejections.flatMap(rejection => array(rejection.validation_issues).map(issue => ({...issue, role: rejection.role})));
    if (issues.length) {
      outcome.append(node('h4', 'What needs correction'));
      const list = node('ul');
      const groups = new Map();
      for (const issue of issues) {
        const label = friendly(issue), group = groups.get(label) ?? {label, issues: []};
        group.issues.push(issue); groups.set(label, group);
      }
      const unique = [...groups.values()];
      unique.forEach(group => list.append(node('li', `${group.label}${group.issues.length > 1 ? ` (${group.issues.length} fields)` : ''}`)));
      outcome.append(list);
      const technical = node('details');
      technical.append(node('summary', 'Developer diagnostics'));
      const raw = node('ul');
      unique.flatMap(group => group.issues).forEach(issue => raw.append(node('li', `${roleLabels[issue.role] ?? 'Design specialist'}: ${issue.field}: ${issue.issue}`)));
      technical.append(raw); outcome.append(technical);
    } else if (legacySchemaFailure) {
      const text = String(supervisor.summary ?? '');
      const details = [];
      if (/implementation kinds?/i.test(text)) details.push('The proposed tasks used unsupported task types.');
      if (/edge.*evidence|evidence.*edge/i.test(text)) details.push('One or more dependency evidence citations were invalid or empty.');
      if (details.length) {
        outcome.append(node('h4', 'Recorded validation issues'));
        const list = node('ul'); details.forEach(issue => list.append(node('li', issue))); outcome.append(list);
      }
    }
    outcome.append(node('p', 'The specialist text below is analysis only. It is not a converted workflow.', 'arena-outcome-note'));
  }
  function inspect() {
    cards.forEach((card, role) => card.button.setAttribute('aria-pressed', String(role === selected)));
    write(roleHeading, roleLabels[selected] ?? selected);
    write(rolePurpose, purposes[roles.indexOf(selected)]);
    roleFindings.replaceChildren();
    const raw = findings.filter((finding) => finding.role === selected);
    const groups = new Map();
    for (const finding of raw) {
      const key = JSON.stringify([finding.status, finding.summary, [...array(finding.evidence_ids)].sort(),
        finding.assumptions, finding.blockers, finding.proposed_changes, finding.alternatives,
        [...array(finding.knowledge_citations)].sort(), finding.diagnostic_scopes]);
      const group = groups.get(key) ?? {finding, count: 0}; group.count++; groups.set(key, group);
    }
    const matching = [...groups.values()];
    if (raw.length) roleFindings.append(node('p', `${raw.length} recorded submission${raw.length === 1 ? '' : 's'}, ${matching.length} distinct finding${matching.length === 1 ? '' : 's'} for this role. Latest recorded status: ${findingStatus[raw.at(-1).status] ?? 'Unknown'}. These are not additional independent reviews.`, 'arena-boundary'));
    if (!matching.length) roleFindings.append(node('p', 'This specialist did not submit a finding. Its analysis state is unknown, not successful.'));
    for (const {finding, count} of matching) {
      const item = node('article', '', 'arena-finding');
      const summary = String(finding.summary ?? 'No explanation was submitted.');
      const ambiguityClaim = /\b(unresolved|ambiguous|not unique|lack(?:s)? unique|non-unique)\b/i.test(summary);
      const diagnosticItems = array(current?.diagnostic_assessment?.items);
      const diagnosticById = new Map(diagnosticItems.map(item => [item.diagnostic_id, item]));
      const scopes = array(finding.diagnostic_scopes);
      const validScopes = scopes.filter(scope => {
        const diagnostic = diagnosticById.get(scope.diagnostic_id);
        return diagnostic && scope.owner_id === diagnostic.owner_id && scope.impact === diagnostic.impact
          && typeof scope.reason === 'string' && scope.reason.trim();
      });
      const quotedNames = [...summary.matchAll(/['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]/g)]
        .map(match => match[1]).filter(name => !name.toLowerCase().startsWith('wkf_'));
      const exactNames = new Set(validScopes.flatMap(scope => {
        const diagnostic = diagnosticById.get(scope.diagnostic_id);
        return [diagnostic?.name, diagnostic?.reference_name, diagnostic?.owner_name]
          .filter(value => typeof value === 'string').map(value => value.toLowerCase());
      }));
      const namesMatch = quotedNames.every(name => exactNames.has(name.toLowerCase()));
      const historicalConnectionOnly = /\b(connection references?|connection metadata|redacted|oracle)\b/i.test(summary)
        && validScopes.length > 0 && validScopes.every(scope => scope.impact === 'non_blocking_context');
      const verifiedClaim = !historicalConnectionOnly && (!ambiguityClaim || (validScopes.length > 0 && namesMatch
        && !(finding.status === 'unsupported' && validScopes.every(scope => scope.impact === 'human_review'))));
      item.append(node('strong', findingStatus[finding.status] ?? 'Finding status not recognized'),
        node('p', verifiedClaim ? summary : historicalConnectionOnly
          ? 'Historical Oracle connection metadata is informational only because this workflow reads source data already in Unity Catalog.'
          : 'No verified reference or field issue recorded for this role. The agent statement lacked an exact matching workflow diagnostic.'));
      if (count > 1) item.append(node('p', `Identical finding recorded ${count} times. Collapsed for display only; original history is unchanged.`, 'arena-boundary'));
      const citationIds = array(finding.evidence_ids).filter(id => typeof id === 'string');
      if (citationIds.length) {
        const refs = new Map(array(current?.evidence_references).map(ref => [ref.id, ref]));
        const evidence = node('section', '', 'arena-evidence');
        evidence.append(node('strong', `Source records (${citationIds.length})`));
        const list = node('ul');
        for (const id of citationIds) {
          const ref = refs.get(id);
          const row = node('li');
          row.append(node('span', ref ? `${ref.kind}: ${ref.label}` : 'Captured package record'));
          if (ref?.locator) row.append(node('small', ref.locator));
          list.append(row);
        }
        evidence.append(list);
        const technical = node('details');
        technical.append(node('summary', 'Technical XML IDs'));
        for (const id of citationIds) technical.append(node('code', id));
        evidence.append(technical); item.append(evidence);
      } else item.append(node('p', 'No XML references were returned for this finding.', 'arena-boundary'));
      roleFindings.append(item);
    }
  }
  roles.forEach((role, index) => {
    const button = node('button', '', `arena-role arena-role-${role}`); button.type = 'button';
    button.dataset.role = role; button.dataset.state = 'unknown'; button.setAttribute('aria-controls', 'arena-role-heading');
    const label = node('strong', role.charAt(0).toUpperCase() + role.slice(1));
    label.textContent = roleLabels[role];
    const signal = node('span', 'Analysis not reported', 'arena-role-signal');
    button.append(node('span', String(index + 1).padStart(2, '0'), 'arena-role-number'), label, signal);
    button.addEventListener('click', () => { selected = role; inspect(); });
    cards.set(role, {button, signal}); grid.append(button);
  });
  const progressPanel = node('section', '', 'arena-progress');
  progressPanel.setAttribute('aria-label', 'Recorded task completion and run controls');
  const progressValue = node('strong', '0%', 'arena-progress-value');
  progressValue.setAttribute('role', 'progressbar');
  progressValue.setAttribute('aria-label', 'Recorded task completion');
  const progressBasis = node('span', 'Select a run', 'arena-progress-basis');
  progressBasis.className += ' arena-visually-hidden';
  const pauseControl = node('button', 'Ⅱ', 'arena-progress-control');
  pauseControl.className += ' arena-pause-control';
  pauseControl.type = 'button'; pauseControl.disabled = true;
  pauseControl.setAttribute('aria-label', 'Pause unavailable: runtime pause and resume are not implemented');
  pauseControl.title = 'Pause unavailable: runtime pause and resume are not implemented';
  const stopControl = node('button', '■', 'arena-progress-control'); stopControl.type = 'button';
  stopControl.className += ' arena-stop-control';
  stopControl.setAttribute('aria-label', 'Stop selected run');
  stopControl.title = 'Stop selected run. An in-flight request may finish first.';
  stopControl.disabled = true;
  stopControl.addEventListener('click', () => {
    if (!current || !['queued', 'running'].includes(current.status)) return;
    const existing = [...document.querySelectorAll('#run-detail button[data-run-id]')]
      .find(button => button.dataset.runId === current.id);
    if (existing && !existing.disabled) { existing.click(); stopControl.disabled = true; }
  });
  const runtimeCost = node('span', 'Select a run.', 'arena-run-cost');
  runtimeCost.setAttribute('role', 'note');
  runtimeCost.title = 'Current Azure list rates, uncached standard reference, not the final billed amount. Writer and shared compute are excluded.';
  const elapsedTimer = node('time', '00:00:00', 'arena-elapsed-timer');
  elapsedTimer.setAttribute('aria-label', 'Run elapsed time');
  elapsedTimer.setAttribute('aria-live', 'off');
  const runtimeCostBox = node('div', '', 'arena-progress-detail arena-cost-box');
  runtimeCostBox.append(node('span', 'EST. MODEL COST', 'arena-progress-label'), runtimeCost, progressBasis);
  const elapsedBox = node('div', '', 'arena-progress-detail arena-timer-box');
  elapsedBox.append(node('span', 'ELAPSED', 'arena-progress-label'), elapsedTimer);
  progressPanel.append(progressValue, runtimeCostBox, elapsedBox, pauseControl, stopControl);
  function showElapsed(seconds) {
    const total = Number.isFinite(seconds) && seconds >= 0 ? Math.floor(seconds) : 0;
    const value = `${String(Math.floor(total / 3600)).padStart(2, '0')}:${String(Math.floor(total / 60) % 60).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
    write(elapsedTimer, value);
    elapsedTimer.setAttribute('datetime', `PT${total}S`);
  }
  const activityPanel = node('section', '', 'arena-activity');
  activityPanel.setAttribute('aria-label', 'Recorded agent activity');
  // Observational only: consume the exact receipt selected by render(), never
  // current_usage_receipt, other runs, findings, or candidate summaries as counts.
  const activityCount = value => Number.isSafeInteger(value) && value >= 0 ? value : null;
  const activityLabel = value => activityCount(value) === null ? 'Unknown' : String(value);
  const activityName = value => typeof value === 'string'
    && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(value) ? value : null;
  // Decimal rates are scaled exactly, without floating-point overflow or rounding
  // per call. A bounded input size also prevents untrusted events exhausting memory.
  const priceScale = 10n ** 30n;
  let listPrices = null;
  function decimalRate(value) {
    if (typeof value !== 'string' || !/^\d{1,40}(?:\.\d{1,30})?$/.test(value)) return null;
    const [whole, fraction = ''] = value.split('.');
    return BigInt(whole) * priceScale + BigInt(fraction.padEnd(30, '0'));
  }
  function readPrices(value) {
    if (!object(value) || value.version !== 'model-list-prices-v1'
        || value.status !== 'available' || !object(value.rates)) return null;
    const rates = new Map();
    for (const [endpoint, rate] of Object.entries(value.rates)) {
      if (!activityName(endpoint) || !object(rate)) continue;
      const input = decimalRate(rate.input_usd_per_million);
      const output = decimalRate(rate.output_usd_per_million);
      if (input === null || output === null || typeof rate.source_url !== 'string'
          || rate.source_url.length > 2048 || typeof rate.observed_at !== 'string' || rate.observed_at.length > 40
          || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(rate.observed_at)
          || !Number.isFinite(Date.parse(rate.observed_at))) continue;
      try {
        const url = new URL(rate.source_url);
        if (url.protocol !== 'https:' || !url.hostname || url.username || url.password) continue;
      } catch (_) { continue; }
      rates.set(endpoint, {input, output, source: rate.source_url, observed: rate.observed_at});
    }
    return rates;
  }
  function renderCost(receipt, active) {
    const disclaimer = 'Current Azure list rates, uncached standard reference, not historical rates or the final invoice. Writer and shared compute are excluded.';
    runtimeCost.title = disclaimer;
    runtimeCost.removeAttribute('aria-label');
    if (!current) { write(runtimeCost, 'Select a run.'); return; }
    if (!receipt || !Array.isArray(receipt.turns)) { write(runtimeCost, 'Unknown: verified model turns unavailable.'); return; }
    if (!listPrices) { write(runtimeCost, 'Unknown: current list prices unavailable.'); return; }
    let total = 0n, priced = 0, partial = false, pending = false;
    const seen = new Set(), sources = new Set();
    if (activityCount(receipt.turn_count) !== receipt.turns.length) partial = true;
    for (const turn of receipt.turns) {
      if (!object(turn) || typeof turn.call_id !== 'string' || !/^[a-f0-9]{32}$/.test(turn.call_id)
          || seen.has(turn.call_id) || !roles.includes(turn.role)
          || !['started', 'completed', 'failed'].includes(turn.status)) { partial = true; continue; }
      seen.add(turn.call_id);
      if (turn.status === 'started') { pending = true; partial = true; continue; }
      const rate = listPrices.get(turn.endpoint);
      if (!rate || activityCount(turn.input_tokens) === null || activityCount(turn.output_tokens) === null) {
        partial = true; continue;
      }
      total += BigInt(turn.input_tokens) * rate.input + BigInt(turn.output_tokens) * rate.output;
      priced++; sources.add(`${rate.source} (observed ${rate.observed})`);
    }
    if (!priced) {
      write(runtimeCost, pending || (active && !receipt.turns.length) ? 'Pending: no priced model calls yet.' : 'Unknown: no priced model calls.');
      return;
    }
    const centsScale = priceScale * 10000n;
    const cents = (total + centsScale / 2n) / centsScale;
    const amount = `$${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`;
    write(runtimeCost, amount);
    runtimeCost.setAttribute('aria-label', `${amount}, ${partial ? 'partial ' : ''}analysis model cost estimate. ${disclaimer}`);
    runtimeCost.title = `${partial ? 'Partial estimate. ' : ''}${disclaimer} ${[...sources].join('; ')}`;
  }
  const activityStates = ['model_started', 'model_completed', 'model_failed',
    'tool_started', 'tool_completed', 'tool_rejected', 'tool_failed'];
  function activityTime(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toLocaleString();
  }
  function renderActivity(receipt, active, historical) {
    activityPanel.replaceChildren();
    activityPanel.append(node('h3', 'Recorded agent activity (short summary, details in the sections below)'));
    if (!current) {
      activityPanel.append(node('p', 'Select a run to inspect its recorded activity.', 'arena-activity-note'));
      return;
    }
    const mode = active ? 'Active run receipt, not a live clock'
      : historical ? 'Historical snapshot, not live' : 'Historical run receipt, not live';
    activityPanel.append(node('p', `${mode}. ${receipt ? 'Verified ledger observation.' : 'Verified activity unavailable.'}`, 'arena-activity-note'));
    const counts = receipt?.counts;
    const totals = node('dl', '', 'arena-activity-totals');
    for (const [prefix, label] of [['model', 'Model calls'], ['tool', 'Tool calls']]) {
      const item = node('div');
      item.append(node('dt', label), node('dd', ['started', 'completed', 'rejected', 'failed']
        .map(state => `${state}: ${activityLabel(counts?.[`${prefix}_${state}`])}`).join(' · ')));
      totals.append(item);
    }
    const turnNote = node('aside', '', 'arena-turn-note');
    turnNote.setAttribute('role', 'note');
    const turnDefinition = 'One turn is one recorded model call, including summary calls, not a whole ReAct cycle or a correction loop.';
    const turnInfo = node('span', 'ⓘ', 'arena-turn-info');
    turnInfo.tabIndex = 0; turnInfo.title = turnDefinition;
    turnInfo.setAttribute('aria-label', turnDefinition);
    turnNote.append(node('span', 'NOTE', 'arena-turn-note-label'),
      node('strong', `Recorded model turns: ${activityLabel(receipt?.turn_count)}`), turnInfo);
    activityPanel.append(totals, turnNote);
    const table = node('table', '', 'arena-activity-table');
    const caption = node('caption');
    caption.append(node('h4', 'Model calls by role'),
      node('span', 'Completed calls on a shared scale. Missing counters are unavailable, not zero.', 'arena-chart-help'));
    const head = node('thead'), headings = node('tr');
    for (const label of ['Role', 'Started', 'Completed', 'Failed']) {
      const cell = node('th', label); cell.setAttribute('scope', 'col'); headings.append(cell);
    }
    head.append(headings);
    const body = node('tbody');
    const roleRows = array(receipt?.per_role).filter(object);
    const rows = roles.map(role => {
      const matches = roleRows.filter(row => row.role === role);
      return {role, row: matches.length === 1 ? matches[0] : null};
    });
    const scale = Math.max(1, ...rows.map(({row}) => activityCount(row?.model_completed) ?? 0));
    for (const {role, row} of rows) {
      const tr = node('tr'), label = node('th', roleLabels[role]);
      label.setAttribute('scope', 'row'); tr.append(label);
      for (const state of ['started', 'completed', 'failed']) {
        const value = activityCount(row?.[`model_${state}`]);
        const cell = node('td', activityLabel(value));
        if (state === 'completed') {
          const bar = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
          bar.setAttribute('viewBox', '0 0 100 8'); bar.setAttribute('class', 'arena-activity-bar');
          bar.setAttribute('role', 'img'); bar.setAttribute('focusable', 'false');
          bar.setAttribute('aria-label', safe(`${roleLabels[role]}: ${activityLabel(value)} completed model calls`));
          svgNode('rect', {width: '100', height: '8', rx: '4', class: 'arena-activity-track'}, bar);
          if (value !== null) svgNode('rect', {width: String(value / scale * 100), height: '8', rx: '4', class: 'arena-activity-fill'}, bar);
          cell.append(bar);
        }
        tr.append(cell);
      }
      body.append(tr);
    }
    table.append(caption, head, body); activityPanel.append(table);
    const skillStrip = node('section', '', 'arena-current-skills');
    skillStrip.setAttribute('aria-label', 'Recorded skills for active agents');
    skillStrip.append(node('h4', 'Skills'));
    const activeRoles = new Set(array(receipt?.active_calls).map(call => call.role).filter(role => roles.includes(role)));
    const skillRows = array(receipt?.skill_stats?.per_role).filter(row => object(row)
      && roles.includes(row.role) && (!active || activeRoles.has(row.role)));
    skillStrip.dataset.active = String(active && skillRows.some(row => activeRoles.has(row.role)));
    let skillCount = 0;
    for (const row of skillRows) {
      const readCounts = new Map(array(row.tool_reads).map(read => [read.path, read.count]));
      const paths = new Set([...array(row.loaded).map(skill => skill.path), ...readCounts.keys()]);
      for (const path of paths) {
        if (typeof path !== 'string' || !/^\/(?:CONTEXT\.md|production-conversion-contract\.md|library\/(?:[A-Za-z0-9_-]+\/)+SKILL\.md)$/.test(path)) continue;
        const card = node('article', '', 'arena-current-skill');
        card.dataset.active = String(active && activeRoles.has(row.role));
        const label = path === '/CONTEXT.md' ? 'Role context' : path === '/production-conversion-contract.md'
          ? 'Production conversion contract' : path.split('/').slice(2, -1).join(' / ').replaceAll('-', ' ')
            .replace(/\b[a-z]/g, letter => letter.toUpperCase());
        const agent = row.role[0].toUpperCase() + row.role.slice(1);
        card.setAttribute('aria-label', `${agent} agent, ${label} skill`);
        card.append(node('span', agent, 'arena-current-skill-agent'), node('strong', label));
        skillStrip.append(card); skillCount++;
      }
    }
    if (!skillCount) skillStrip.append(node('p', 'No skills recorded.', 'arena-activity-note'));
    skillStrip.append(node('p', 'Receipts show context, not execution.', 'arena-activity-note'));
    activityPanel.append(skillStrip);
    const latest = object(receipt?.last_event) ? receipt.last_event : null;
    const event = activityStates.includes(latest?.event) ? latest.event : null;
    const role = roles.includes(latest?.role) ? roleLabels[latest.role] : 'Role unknown';
    const name = event?.startsWith('tool_') ? activityName(latest?.tool_name)
      : event?.startsWith('model_') ? activityName(latest?.endpoint) : null;
    const stamp = activityTime(latest?.created_at);
    activityPanel.append(node('p', latest
      ? `Latest activity: ${role} · ${name ?? 'Name unknown'} · ${event?.replaceAll('_', ' ') ?? 'State unknown'} · ${stamp ?? 'Time unknown'}`
      : 'Latest activity: not recorded.', 'arena-activity-latest'));
    // The current receipt has a last event, not a full named-tool history.
    // A named completion is not evidence of how many check calls were started.
    activityPanel.append(node('p', event?.startsWith('tool_') && name === 'check_generation_code'
      ? `Artifact sanity tool check_generation_code: ${event.replaceAll('_', ' ')} is the latest recorded event. Total check calls unknown.`
      : 'Artifact sanity tool check calls: not recorded in the available named-tool events.', 'arena-activity-note'));
    activityPanel.append(node('p', 'Correction activity not recorded in this receipt. Candidate writer manifest repair evidence and review issues are not included here; repeated calls or findings are not correction loops. ReAct cycles and loop counts: unknown.', 'arena-activity-note'));
    const turns = array(receipt?.turns).filter(turn => object(turn) && roles.includes(turn.role)
      && ['started', 'completed', 'failed'].includes(turn.status));
    const details = node('details', '', 'arena-activity-turns');
    details.append(node('summary', 'Recorded model-call turns'));
    if (!Array.isArray(receipt?.turns)) details.append(node('p', 'Model-call turn records unavailable.'));
    else {
      details.append(node('p', `${turns.length} displayable call records. Aggregate turn count above is reported separately, not inferred from this list.`));
      const list = node('ol');
      for (const turn of turns) {
        const number = activityCount(turn.turn);
        const time = activityTime(turn.completed_at) ?? activityTime(turn.failed_at) ?? activityTime(turn.started_at);
        list.append(node('li', `${number !== null && number > 0 ? `Turn ${number}` : 'Recorded call'} · ${roleLabels[turn.role]} · ${activityName(turn.endpoint) ?? 'Model unknown'} · ${turn.status} · ${time ?? 'Time unknown'}`));
      }
      details.append(list);
    }
    activityPanel.append(details);
  }
  stage.append(core, grid); middle.append(stage, progressPanel);
  layout.append(telemetry, middle, inspector, activityPanel);
  const stack = node('div', '', 'arena-stack');
  let smithStatus;
  const traceLabel = trace => ['server_key_required', 'explicit_project_required'].includes(trace?.reason)
    ? 'Not configured' : String(trace?.state || 'unknown').replaceAll('_', ' ');
  for (const [name, description] of [['LangChain', 'Agent tools and model interfaces'], ['LangGraph', 'Agent execution and checkpoints'], ['LangSmith', 'Configuration unknown'], ['MLflow', 'Not integrated']]) {
    const item = node('span'); item.append(node('strong', name));
    const descriptionNode = node('span', ` · ${description}`); item.append(descriptionNode); stack.append(item);
    if (name === 'LangSmith') { smithStatus = descriptionNode; smithStatus.id = 'arena-langsmith-status'; }
  }
  arena.append(header, status, outcome, stack, layout); mount.append(arena);
  let current = null, kind = 'agent', version = '';
  let progressRunId = null, estimatedProgress = 0;
  function render() {
    const active = ['queued', 'running', 'cancelling'].includes(current?.status);
    const startTime = Date.parse(current?.started_at || current?.created_at || '');
    const finishTime = active ? Date.now() : Date.parse(current?.finished_at || '');
    showElapsed(Number.isFinite(startTime) && Number.isFinite(finishTime)
      ? Math.max(0, (finishTime - startTime) / 1000) : 0);
    const saved = current?.observation_snapshot;
    const historical = !active && saved?.schema_version === 1 && saved.mode === 'historical_not_live'
      && saved.public_run_id === current?.id && saved.package_hash === current.package_hash
      && saved.workflow_id === current.workflow_id && saved.scenario === current.scenario
      && saved.run_status === current.status && saved.finished_at === current.finished_at
      && (!current.output?.run_id || saved.run_id === current.output.run_id) ? saved : null;
    const live = historical ? historical.status === 'verified' ? historical : null
      : current?.live_activity?.status === 'verified'
        && current.live_activity.public_run_id === current.id ? current.live_activity : null;
    renderActivity(live, active, historical);
    // A mismatched saved terminal snapshot must not fall through to a newer
    // receipt. Old runs without snapshots may use their own verified ledger.
    const costReceipt = !active && saved && !historical ? null : live;
    renderCost(costReceipt && (!current?.output?.run_id || costReceipt.run_id === current.output.run_id)
      ? costReceipt : null, active);
    const activeCalls = array(live?.active_calls);
    arena.dataset.active = String(active);
    arena.dataset.state = current?.status === 'failed' || current?.status === 'error' ? 'failed' : 'neutral';
    write(badge, current ? `${active ? 'Run' : 'Historical run, not live'} / ${safe(current.status)}` : 'Awaiting explicit run');
    write(status, !current ? 'No run selected.' : active
      ? live ? activeCalls.length
        ? `Run active. ${activeCalls.map(call => `${roleLabels[call.role] ?? call.role}: ${call.kind === 'tool' ? `tool ${safe(call.tool_name)}` : `model ${safe(call.endpoint)}`} in flight`).join('; ')}. Based on committed local ledger events.`
        : 'Run active. No call is currently recorded in flight. Awaiting the next committed event.'
      : 'Run active. Specialist-level progress is not verified by the saved ledger.'
      : `Historical observation, not live. Run ${safe(current.status)}. Specialist findings below are advisory.`);
    const source = current?.output ?? historical;
    const milestones = array(source?.role_completion);
    const completedRoles = new Set(milestones.filter(item => roles.includes(item.role)
      && item.status === 'completed').map(item => item.role));
    const writerTasks = array(current?.candidate_writer?.task_statuses);
    const writerTaskIds = writerTasks.map(task => task.task_id);
    const writerKnown = writerTasks.length > 0 && writerTaskIds.every(id => typeof id === 'string')
      && new Set(writerTaskIds).size === writerTasks.length;
    const knownProgress = writerKnown || milestones.length > 0;
    const totalTasks = writerKnown ? writerTasks.length : roles.length;
    const doneTasks = writerKnown ? writerTasks.filter(task => task.status === 'drafted').length : completedRoles.size;
    if (progressRunId !== current?.id) { progressRunId = current?.id; estimatedProgress = 0; }
    const modelCalls = activityCount(live?.counts?.model_completed) ?? 0;
    const toolCalls = activityCount(live?.counts?.tool_completed) ?? 0;
    // User-selected heuristic, not a task inventory, time estimate or success rate.
    const callEstimate = Math.floor(100 * (Math.min(modelCalls / 50, 1) + Math.min(toolCalls / 125, 1)) / 2);
    estimatedProgress = Math.max(estimatedProgress, Math.min(99, callEstimate));
    const generated = current?.status === 'completed'
      && current?.candidate_writer?.manifest_status === 'generation_complete';
    const percentage = !current ? 0 : generated ? 100 : estimatedProgress;
    write(progressValue, `${percentage}%`);
    progressValue.setAttribute('aria-label', 'Estimated conversion progress');
    progressValue.setAttribute('aria-valuemin', '0'); progressValue.setAttribute('aria-valuemax', '100');
    progressValue.setAttribute('aria-valuenow', String(percentage));
    write(progressBasis, !current ? 'Select a run' : generated
      ? 'Generation finished. Review and runtime correctness are separate.'
      : `Estimated progress: ${modelCalls}/50 model calls · ${toolCalls}/125 tool calls completed. Baseline, not a fixed task total.${knownProgress ? ` ${doneTasks}/${totalTasks} ${writerKnown ? 'writer tasks generated' : 'analysis milestones completed'}.` : ''}${percentage === 99 ? ' Awaiting completion.' : ''}`);
    const existingStop = [...document.querySelectorAll('#run-detail button[data-run-id]')]
      .find(button => button.dataset.runId === current?.id);
    stopControl.disabled = !active || !!current?.cancellation_requested_at || !existingStop || existingStop.disabled;
    showDesignOutcome(source?.design_submission, source);
    if (source?.tracing && smithStatus) write(smithStatus, ` · Run trace: ${traceLabel(source.tracing)}${source.tracing.project ? ` (${source.tracing.project})` : ''}`);
    findings = array(source?.findings).filter(object);
    if (source?.architecture_knowledge) {
      const knowledge = source.architecture_knowledge;
      outcome.hidden = false;
      outcome.append(node('p', `Architecture knowledge: ${array(knowledge.retrievals).length} recorded retrievals; ${array(knowledge.citations).length} applied citations. Retrieval alone does not demonstrate that a recommendation used the knowledge or improved the design. Irrelevant features do not need citations.`, 'arena-boundary'));
    }
    const id = safe(current?.id); write(values.id, current ? (id.length > 20 ? `${id.slice(0, 17)}…` : id) : 'Unknown');
    values.id.title = current ? id : '';
    write(values.workflow, current?.workflow_name || current?.workflow_id);
    values.workflow.title = safe(current?.workflow_name ? `${current.workflow_name} (${current.workflow_id})` : current?.workflow_id);
    const dateLabel = value => {
      if (!value) return 'Unknown';
      const date = new Date(value); return Number.isNaN(date.getTime()) ? safe(value) : date.toLocaleString();
    };
    write(values.created, dateLabel(current?.created_at)); write(values.finished, dateLabel(current?.finished_at));
    const roleResults = array(source?.role_completion);
    write(values.attempts, roleResults.length ? `${roleResults.filter(item => item.status === 'completed').length}/${roleResults.length} completed` : 'Unknown');
    const citations = new Set(findings.flatMap((f) => array(f.evidence_ids)).filter((id) => typeof id === 'string'));
    write(values.citations, Array.isArray(source?.findings) && findings.every((f) => Array.isArray(f.evidence_ids)) ? citations.size : 'Unknown');
    values.citations.title = 'Unique citations in recorded findings';
    const usage = live?.usage ?? current?.usage ?? current?.output?.usage ?? current?.output?.usage_metadata;
    const total = usage?.total_tokens;
    write(values.tokens, typeof total === 'number' && Number.isFinite(total) && total >= 0 ? total : 'Unknown');
    const countLabel = kind => {
      if (!live) return 'Unknown';
      const started = live.counts?.[`${kind}_started`] ?? 0, completed = live.counts?.[`${kind}_completed`] ?? 0;
      return Number.isSafeInteger(started) && started >= 0 && Number.isSafeInteger(completed) && completed >= 0
        ? `${started} / ${completed}` : 'Unknown';
    };
    write(values.models, countLabel('model')); write(values.tools, countLabel('tool'));
    const roleReceipts = new Map(array(source?.role_completion).map(item => [item.role, item]));
    cards.forEach(({button, signal}, role) => {
      const matching = findings.filter((f) => f.role === role);
      const states = matching.length ? [matching.at(-1).status] : [];
      const severity = ['failed', 'unsupported', 'needs_information', 'supported'].find((s) => states.includes(s));
      button.dataset.state = severity ?? 'unknown';
      const receipt = roleReceipts.get(role);
      const roleCalls = active ? activeCalls.filter(call => call.role === role) : [];
      const result = roleCalls.length ? roleCalls.map(call => call.kind === 'tool'
        ? `Tool in flight: ${safe(call.tool_name)}` : `Model in flight: ${safe(call.endpoint)}`).join(' · ')
        : matching.length ? [...new Set(states.map(s => findingStatus[s] ?? 'Finding status not recognized'))].join(' · ')
        : receipt?.status === 'failed' ? `Failed${receipt.error_type === 'TimeoutError' ? ' · timed out' : ''}`
        : receipt?.status === 'incomplete' ? 'Incomplete output'
        : receipt?.status === 'completed' ? 'Completed, no finding'
        : 'No result reported';
      write(signal, result); signal.title = safe(result);
    });
    inspect();
  }
  function receive(event) {
    const record = event.detail;
    if (record !== null && (!object(record) || typeof record.id !== 'string' || typeof record.status !== 'string')) return;
    const nextKind = 'agent';
    // Repeated snapshots do not rebuild inspector DOM or repeat live announcements.
    const nextVersion = JSON.stringify([nextKind, record]);
    if (version === nextVersion) return;
    version = nextVersion; current = record; kind = nextKind; render();
  }
  window.addEventListener('orion:agent-run', receive);
  document.addEventListener('orion:agent-run', receive);
  function receivePrices(event) {
    listPrices = readPrices(event.detail);
    render();
  }
  window.addEventListener('orion:model-list-prices', receivePrices);
  document.addEventListener('orion:model-list-prices', receivePrices);
  document.addEventListener('orion:agent-clock', event => {
    if (current?.id === event.detail?.id && ['queued', 'running', 'cancelling'].includes(current.status))
      showElapsed(event.detail.elapsed);
  });
  document.addEventListener('orion:tracing-status', event => {
    if (smithStatus && object(event.detail)) write(smithStatus, ` · ${traceLabel(event.detail)}${event.detail.project ? ` (${event.detail.project})` : ''}`);
  });
  document.addEventListener('change', (event) => {
    if (['project-select', 'project-package', 'estate-package-select', 'agent-workflow', 'agent-scenario'].includes(event.target.id)) {
      current = null; version = ''; render();
    }
  });
  render();
})();