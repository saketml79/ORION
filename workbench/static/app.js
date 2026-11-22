/* ORION: dependency-free, evidence-preserving API client. */
(() => {
  'use strict';

  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);

  const $ = (id) => document.getElementById(id);
  const projectGrid = $('page-projects').querySelector('.two-column');
  const projectSelectionCard = $('project-select').closest('article');
  projectGrid.insertBefore(projectSelectionCard, projectGrid.firstElementChild);
  $('project-package').dataset.emptyMessage = 'Select a saved project to view its XML packages.';
  const packageCard = document.querySelector('.hero-aside');
  packageCard.querySelector('.eyebrow').textContent = 'CURRENT LEGACY INFORMATICA PACKAGE';
  const packagePickerLabel = document.createElement('label');
  packagePickerLabel.htmlFor = 'estate-package-select';
  packagePickerLabel.textContent = 'Packages in this project';
  const packagePicker = document.createElement('select');
  packagePicker.id = 'estate-package-select';
  packagePicker.size = 4;
  packagePicker.disabled = true;
  packagePicker.setAttribute('aria-describedby', 'estate-package-hint');
  const packagePickerHint = document.createElement('p');
  packagePickerHint.id = 'estate-package-hint';
  packagePickerHint.className = 'small muted';
  packageCard.append(packagePickerLabel, packagePicker, packagePickerHint);
  const overviewProgress = document.createElement('div');
  overviewProgress.id = 'package-understanding-progress';
  overviewProgress.setAttribute('role', 'status');
  overviewProgress.setAttribute('aria-live', 'polite');
  overviewProgress.tabIndex = -1;
  overviewProgress.hidden = true;
  const overviewProgressHeading = document.createElement('strong');
  overviewProgressHeading.className = 'package-progress-heading';
  overviewProgressHeading.textContent = 'Package understanding';
  const overviewProgressBar = document.createElement('progress');
  overviewProgressBar.setAttribute('aria-label', 'Loading package understanding: progress unknown');
  const overviewProgressDescription = document.createElement('span');
  overviewProgressDescription.className = 'package-progress-description';
  overviewProgressDescription.textContent = 'Loading selected XML understanding. Progress is not reported.';
  overviewProgress.append(overviewProgressBar, overviewProgressHeading, overviewProgressDescription);
  const loadingDiagnosticText = document.createElement('span');
  loadingDiagnosticText.className = 'package-progress-diagnostics';
  overviewProgress.append(loadingDiagnosticText);
  const inventoryRetry = document.createElement('button');
  inventoryRetry.type = 'button';
  inventoryRetry.textContent = 'Retry Explorer inventory';
  inventoryRetry.hidden = true;
  overviewProgress.append(inventoryRetry);
  const readinessRetry = document.createElement('button');
  readinessRetry.type = 'button';
  readinessRetry.textContent = 'Retry readiness';
  readinessRetry.hidden = true;
  overviewProgress.append(readinessRetry);
  const warmRetry = document.createElement('button');
  warmRetry.type = 'button';
  warmRetry.textContent = 'Retry package preparation';
  warmRetry.hidden = true;
  overviewProgress.append(warmRetry);
  packageCard.append(overviewProgress);
  document.querySelectorAll('a[href="/api/export/english"]').forEach((link) => {
    link.textContent = 'Download package analysis report (Markdown)';
    link.title = "Download the selected package's structural inventory, summaries, and source references as a Markdown report";
  });
  const all = (selector, root = document) => [...root.querySelectorAll(selector)];
  const workflowSessionStatus = document.createElement('p');
  workflowSessionStatus.id = 'agent-workflow-session-status';
  workflowSessionStatus.className = 'small muted';
  workflowSessionStatus.setAttribute('role', 'status');
  workflowSessionStatus.hidden = true;
  $('agent-workflow').after(workflowSessionStatus);
  const array = (value) => Array.isArray(value) ? value : [];
  const text = (value) => value == null ? 'Not supplied' : typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  const human = (value) => String(value).replace(/_/g, ' ');
  const roleLabel = value => ({supervisor: 'Lead migration design coordinator', discovery: 'Informatica package analyst',
    semantics: 'Transformation semantics analyst', architecture: 'Lakeflow workflow designer',
    validation: 'Independent validation analyst', artifact: 'Artifact requirements analyst'})[value] || human(value);
  const roleSkillDescription = value => ({
    supervisor: 'Coordinates specialist findings and can submit or correct the logical migration design. Candidate code is drafted separately by the Spark and SQL code writer.',
    discovery: 'Inspects scoped Informatica objects, source contracts, and discovery gaps.',
    semantics: 'Reviews captured mapping, write, transaction, and control semantics.',
    architecture: 'Proposes evidence-qualified Lakeflow boundaries, reuse, and medallion placement.',
    validation: 'Independently challenges coverage, evidence ownership, and parity claims.',
    artifact: 'Reviews artifact readiness and release contracts; this role does not produce executable artifacts.'
  })[value] || 'Role description is not available.';
  const scenarioLabel = value => value === 'bronze' ? 'Source data already in Unity Catalog' : 'Original source systems';
  const json = (value) => JSON.stringify(value, null, 2);
  const numberFormat = new Intl.NumberFormat('en-US');
  const count = (value) => typeof value === 'number' && Number.isFinite(value) ? numberFormat.format(value) : 'Not supplied';
  const terminal = new Set(['completed', 'partial', 'complete', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'rejected']);
  const state = {
    page: 'estate', overview: null, healthCapabilities: null, ready: false, packageKey: null, epoch: 0, hosted: false,
    overviewLoading: false, overviewError: '', loadingDiagnostic: null, loadingDiagnosticSequence: 0,
    overviewTimer: null, jobTimer: null, jobId: null, jobBusy: false,
    inventoryOffset: 0, inventoryTotal: 0, inventoryCount: 0, inventoryTicket: 0, inventoryLoaded: false,
    inventoryPreload: 'not_started', inventoryPreloadError: '',
    object: null, objectTicket: 0, selectedNode: null, selectedObjectId: null, objectLoadState: 'idle', view: 'graph',
    proposals: [], proposal: null, proposalOffset: 0, proposalTotal: 0, proposalPageSize: 20,
    proposalTicket: 0, proposalsLoaded: false,
    runTimer: null, runId: null, runTicket: 0, settingsLoaded: false, settingsTicket: 0, overviewTicket: 0,
    projectId: '', memberHash: '', project: null, legacyWorkspaceSelected: false, projectTicket: 0, projectTimer: null,
    projectJobs: [], projectSubmitting: false, requests: 0, agentRunning: false, agentSubmitting: false, submittedRunId: null, agentRunsLoaded: false,
    projectsLoaded: false, projectsLoading: false, projectsTicket: 0,
    inputsLoaded: false, inputsLoading: false, inputsTicket: 0, projectLoading: false,
    documentsLoaded: false, documentsLoading: false, documentsTicket: 0,
    runsRowsLoaded: false, runsListTicket: 0,
    runtimeWarmKey: null, runtimeWarmStatus: 'not_started', runtimeWarmFlight: null, runtimeWarmTicket: 0, runtimeWarmError: '',
    readinessPreload: 'not_started', readinessPreloadError: '', readinessTicket: 0, readinessEpoch: 0, readinessFlight: null,
    pagePreload: 'not_started', pagePreloadError: '', pagePreloadTicket: 0, pagePreloadFlight: null,
    graphOffset: 0, graphTicket: 0, graphOpen: false, graphBusy: false, graphHasMore: false, graphRenderedKey: '',
    logsOffset: 0, logsTicket: 0, logsTimer: null,
    hostedLogsTicket: 0, hostedLogsTimer: null,
  };
  // Presentation metadata only. Request accounting, polling, and workflow state stay authoritative.
  const requestActivity = new Map();
  let hostedActivity = null;
  const recordedRunStates = new Map();
  let legacyJobStatus = '';
  let initializingNavigation = true;
  let startupUserPage = '';
  const iconPaths = Object.freeze({
    grid: 'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    graph: 'M4 4h5v5H4z M15 15h5v5h-5z M15 4h5v5h-5z M9 6.5h6 M6.5 9v8.5H15',
    file: 'M14 3H5v18h14V8z M14 3v5h5 M8 12h8 M8 16h6',
    package: 'M12 3 3 8v9l9 5 9-5V8L12 3Z M3 8l9 5 9-5 M12 13v9 M7.5 5.5l9 5',
    source: 'M4 5h16v14H4z M8 9h8 M12 9v6 M9 12l3 3 3-3',
    target: 'M4 5h16v14H4z M8 15h8 M12 15V9 M9 12l3-3 3 3',
    link: 'M9 8l2-2a4 4 0 0 1 6 6l-2 2 M15 16l-2 2a4 4 0 0 1-6-6l2-2 M9 15l6-6',
    clock: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2',
    running: 'M12 3a9 9 0 0 1 9 9 M12 21a9 9 0 0 1-9-9 M16 3h-4v4 M8 21h4v-4',
    check: 'M20 6L9 17l-5-5',
    error: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M9 9l6 6 M15 9l-6 6',
    warning: 'M12 3L2 21h20z M12 9v5 M12 17v1',
    partial: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v10 M8 12h4',
    skipped: 'M5 6l9 6-9 6z M19 6v12',
    draft: 'M4 20l4-1L20 7l-4-4L4 15z M13 6l4 4',
    info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 11v6 M12 7v1',
    plus: 'M12 4v16 M4 12h16',
    arrow: 'M5 19L19 5 M5 5h14v14',
    help: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M9 9a3 3 0 1 1 5 2l-2 2v1 M12 17v1',
    theme: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 3v18',
    moon: 'M20.5 13A8.5 8.5 0 0 1 11 3.5 8.5 8.5 0 1 0 20.5 13Z',
    sun: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5L19 19 M5 19l1.5-1.5 M17.5 6.5L19 5',
  });
  function icon(name) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false', class: 'ui-icon' })) svg.setAttribute(key, value);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', Object.hasOwn(iconPaths, name) ? iconPaths[name] : iconPaths.info);
    svg.append(path);
    return svg;
  }
  function highlightedText(node, content) {
    const value = text(content);
    clear(node);
    // Only narrative count + unit phrases. Never interpret code, JSON, or SQL as prose.
    if (/[{}]|\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|MERGE|ALTER)\b/i.test(value)) { node.append(document.createTextNode(value)); return node; }
    const protectedSpans = [...value.matchAll(/`[^`]*`|"[^"\n]*"|'[^'\n]*'|https?:\/\/\S+|\b[\w./:@-]*[\/_:@][\w./:@-]*\b|\b\d{4}-\d{2}-\d{2}\S*|\bv?\d+(?:\.\d+)+\b|\b[a-f\d]{16,}\b/gi)].map((match) => [match.index, match.index + match[0].length]);
    const pattern = /(?<![\w.,/#:+-])(\d{1,3}(?:,\d{3})+|\d+)(?=\s+(?:XML elements?|repositories|repository|folders?|mappings?|mapplets?|sessions?|sources?|targets?|workflows?|worklets?|fields?|properties|objects?|nodes?|edges?|diagnostics?|blockers?|proposals?|candidates?|events?|files?|packages?|requests?|jobs?|tokens?|reviews?)\b)/gi;
    let cursor = 0;
    for (const match of value.matchAll(pattern)) {
      if (protectedSpans.some(([start, end]) => match.index < end && match.index + match[0].length > start)) continue;
      const number = Number(match[0].replace(/,/g, ''));
      if (!Number.isSafeInteger(number)) continue;
      node.append(document.createTextNode(value.slice(cursor, match.index)), el('strong', 'stat-emphasis', count(number)));
      cursor = match.index + match[0].length;
    }
    node.append(document.createTextNode(value.slice(cursor)));
    return node;
  }
  function semanticStatus(status) {
    const key = String(status ?? '').toLowerCase();
    const styles = {
      queued: ['clock', 'pending'], running: ['running', 'busy'], completed: ['check', 'good'],
      complete: ['check', 'good'], succeeded: ['check', 'good'], success: ['check', 'good'],
      failed: ['error', 'failed'], error: ['error', 'failed'], partial: ['partial', 'caution'],
      skipped: ['skipped', 'neutral'], rejected: ['error', 'failed'], draft: ['draft', 'neutral'],
      cancelled: ['skipped', 'neutral'], canceled: ['skipped', 'neutral'],
      generation_complete: ['check', 'good'],
    };
    const [glyph, tone] = styles[key] ?? ['info', 'neutral'];
    const badge = el('span', `status-pill semantic-status ${tone}${key === 'queued' || key === 'running' ? ' is-ongoing' : ''}`);
    badge.dataset.status = key;
    badge.append(icon(glyph), el('span', '', key === 'generation_complete' ? 'Generation complete' : human(status ?? 'Status unknown')));
    return badge;
  }
  function recordHeader(node, label, status, timestamp) {
    const header = el('div', 'record-heading');
    const name = title(el('strong', 'record-title', label), label);
    header.append(name, semanticStatus(status));
    node.append(header);
    if (timestamp != null) node.append(el('small', 'record-time', text(timestamp)));
  }
  function viewLogs(node, runId = '') {
    node.append(action('View logs', () => { $('logs-run').value = runId; state.logsOffset = 0; navigate('logs', true); }));
  }
  function fact(node, label, value, className = '') {
    const item = el('div', `inventory-fact ${className}`);
    item.append(el('strong', 'stat-emphasis', count(value)), el('span', '', label));
    node.append(item);
  }

  function el(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  }
  function clear(node) { node.replaceChildren(); return node; }
  function message(node, content, className = 'empty') {
    const pending = /^Loading\b/.test(String(content));
    const line = el('p', `${className}${pending ? ' loading-message' : ''}`);
    if (pending) line.append(icon('running'));
    line.append(document.createTextNode(text(content)));
    clear(node).append(line);
  }
  function pill(content, status = '') { return el('span', `status-pill ${status}`, content); }
  function title(node, value) { node.title = text(value); return node; }
  function heading(node, content, level = 'h3') { node.append(title(el(level, 'truncate', content), content)); }
  function paragraph(node, content, className = 'muted prose') { node.append(highlightedText(el('p', className), content)); }
  // Finding presentation only: slice existing text, never infer behavior or SQL phases.
  function findingText(node, content) {
    const source = text(content);
    const safe = window.orionRedaction.text(source);
    const protectedSpans = [...safe.matchAll(/```[\s\S]*?(?:```|$)|--[^\r\n]*|\/\*[\s\S]*?(?:\*\/|$)|`[^`]*(?:`|$)|"(?:\\[\s\S]|""|[^"\\])*(?:"|$)|'(?:\\[\s\S]|''|[^'\\])*(?:'|$)|https?:\/\/\S+|\b[\w./:@-]*[\/_:@][\w./:@-]*\b/g)]
      .map(match => [match.index, match.index + match[0].length]);
    const protectedAt = index => protectedSpans.some(([start, end]) => index > start && index < end);
    const sentences = typeof Intl.Segmenter === 'function'
      ? [...new Intl.Segmenter('en', {granularity: 'sentence'}).segment(safe)] : [];
    const boundaries = new Set([0, ...sentences.map(item => item.index)]);
    const labels = [...safe.matchAll(/\b(?:Flow|SQL phases|Session|Child|Package cohort):/g)]
      .filter(match => !protectedAt(match.index) && (boundaries.has(match.index)
        || /(?:^|[\r\n])[ \t]*$/.test(safe.slice(0, match.index))));
    const sections = [];
    let cursor = 0;
    for (const [index, match] of labels.entries()) {
      if (match.index > cursor) sections.push({offset: cursor, value: safe.slice(cursor, match.index)});
      const next = labels[index + 1];
      sections.push({label: match[0], offset: match.index + match[0].length,
        value: safe.slice(match.index + match[0].length, next?.index ?? safe.length)});
      cursor = next?.index ?? safe.length;
    }
    if (!labels.length) sections.push({offset: 0, value: safe});
    for (const section of sections) {
      const block = el('section', 'agent-finding-section');
      if (section.label) block.append(el('h5', '', section.label));
      // The complete SQL-containing section stays intact. No statement extraction,
      // semicolon splitting, fabricated language, or non-exported highlighter access.
      if (/\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|MERGE|ALTER|DROP|TRUNCATE)\b/i.test(section.value)
          || section.label === 'SQL phases:' || /```/.test(section.value)) {
        const pre = el('pre', 'agent-finding-technical', section.value);
        pre.tabIndex = 0;
        pre.setAttribute('aria-label', 'Original technical text, not parsed or executed');
        block.append(pre);
      } else {
        const parts = typeof Intl.Segmenter === 'function'
          ? [...new Intl.Segmenter('en', {granularity: 'sentence'}).segment(section.value)] : [];
        const cuts = parts.filter(item => item.index > 0 && !protectedAt(section.offset + item.index))
          .map(item => item.index);
        let start = 0;
        for (const end of [...cuts, section.value.length]) {
          if (end > start) block.append(el('p', 'agent-finding-prose', section.value.slice(start, end)));
          start = end;
        }
      }
      node.append(block);
    }
  }
  function renderAgentFinding(node, finding, assessment, repeats, references = []) {
    const card = el('article', 'agent-finding');
    card.append(el('h4', '', `${roleLabel(finding.role)}: ${human(finding.status)}`));
    card.append(el('p', 'small muted', 'Historical agent finding. Advisory wording, not verified original behavior or an accepted design.'));
    if (finding.status === 'unsupported') card.append(el('p', 'agent-finding-context',
      'Recorded status: unsupported. Retained for review, not proof of a current blocker.'));
    const summary = String(finding.summary ?? 'No summary recorded.');
    if (assessment !== summary) card.append(el('p', 'agent-finding-context', `Diagnostic scope context (separate from original wording): ${assessment}`));
    card.append(el('h5', '', 'Recorded summary'));
    findingText(card, summary);
    if (repeats > 1) card.append(el('p', 'small muted', `Identical submission recorded ${repeats} times for this role. Display collapsed only, not additional independent reviews.`));
    for (const [key, label, empty] of [
      ['assumptions', 'Assumptions', 'No assumptions recorded.'],
      ['blockers', 'Recorded blockers', 'No role-specific blockers recorded.'],
      ['proposed_changes', 'Agent-proposed design changes', 'No design changes proposed by this role.'],
      ['alternatives', 'Recorded alternatives', 'No alternatives recorded.'],
    ]) {
      const section = el('section', 'agent-finding-field');
      section.append(el('h5', '', label));
      const values = Array.isArray(finding[key]) ? finding[key] : finding[key] == null ? [] : [finding[key]];
      if (values.length) {
        const bullets = el('ul', 'agent-finding-list');
        for (const value of values) { const item = el('li'); findingText(item, value); bullets.append(item); }
        section.append(bullets);
      } else section.append(el('p', 'small muted', empty));
      card.append(section);
    }
    for (const [key, label] of [['evidence_ids', 'Recorded evidence IDs'], ['knowledge_citations', 'Recorded knowledge citations']]) {
      if (array(finding[key]).length) {
        const section = el('details', 'agent-finding-field');
        section.append(el('summary', '', `${key === 'evidence_ids' ? 'Source references' : 'Knowledge references'} (${array(finding[key]).length}, technical details)`));
        if (key === 'evidence_ids') {
          section.append(el('p', 'small muted', 'Captured source objects and XML locations supporting this finding. Raw identifiers remain in the original record below.'));
          const labels = new Map(array(references).map(ref => [ref.id, ref]));
          const bullets = el('ul', 'agent-finding-list');
          for (const identifier of array(finding[key])) {
            const ref = labels.get(identifier), item = el('li');
            item.append(el('strong', '', ref?.label || 'Source reference label unavailable'));
            item.append(el('p', 'small muted', ref
              ? `${human(ref.kind)} · ${ref.locator || 'XML location not supplied'}`
              : 'This historical record does not provide a readable source label. The exact reference is retained in the original technical record.'));
            bullets.append(item);
          }
          section.append(bullets);
        } else list(section, array(finding[key]).map(value => window.orionRedaction.text(text(value))));
        card.append(section);
      }
    }
    const details = el('details', 'agent-finding-original');
    details.append(el('summary', '', 'Original finding text and evidence (technical details)'),
      el('pre', 'agent-finding-technical', window.orionRedaction.text(json(finding))));
    card.append(details);
    node.append(card);
  }
  function jsonDetails(node, label, value, open = false) {
    const details = el('details');
    details.open = open;
    details.append(el('summary', '', label), el('pre', 'json-block', json(value)));
    node.append(details);
    return details;
  }
  function logCopyButton(record) {
    const button = action('Copy log', async () => {
      const contents = window.orionRedaction.text(JSON.stringify(record, null, 2));
      try {
        if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(contents);
        else {
          const field = document.createElement('textarea');
          field.value = contents; field.setAttribute('readonly', '');
          field.style.position = 'fixed'; field.style.opacity = '0';
          document.body.append(field); field.select();
          const copied = document.execCommand('copy'); field.remove();
          if (!copied) throw new Error('Clipboard unavailable');
        }
        button.textContent = 'Copied';
        button.setAttribute('aria-label', 'Log entry copied');
      } catch (_) {
        button.textContent = 'Copy unavailable';
        button.setAttribute('aria-label', 'Copy log entry unavailable');
      }
    });
    button.classList.add('log-copy-button');
    button.setAttribute('aria-label', 'Copy redacted log entry');
    button.title = 'Copy this entry with credential-like values redacted.';
    return button;
  }
  function rows(node, value, labelFor = human) {
    for (const [key, val] of Object.entries(value || {})) {
      const row = el('div', 'detail-row');
      row.append(el('span', 'detail-key', labelFor(key)), el('pre', 'detail-value', text(val)));
      node.append(row);
    }
  }
  function list(node, values, empty = 'None reported.') {
    if (!values.length) { paragraph(node, empty); return; }
    const ul = el('ul');
    for (const value of values) ul.append(el('li', '', text(value)));
    node.append(ul);
  }
  function action(label, callback, className = 'secondary compact') {
    const button = el('button', className, label);
    button.type = 'button';
    button.addEventListener('click', callback);
    return button;
  }
  function notify(content, kind = '') {
    $('notice').textContent = content;
    $('notice').className = `notice ${kind}`;
    $('notice').hidden = false;
  }
  function formStatus(form, content, kind = '') {
    const target = form.querySelector('.form-status');
    if (target) { target.textContent = content; target.className = `form-status small ${kind}`; }
  }
  function contextHeaders() {
    return state.projectId ? { 'X-Workbench-Project': state.projectId, ...(state.memberHash ? { 'X-Workbench-Package': state.memberHash } : {}) } : {};
  }
  const loadingPhases = Object.freeze({
    reading_history: 'Loading saved agent runs',
    queued: 'Queued', provisioning: 'Provisioning', metadata_restore: 'Restoring metadata',
    artifact_files: 'Restoring artifact files', audit_restore: 'Restoring audit records',
    preparing_package: 'Preparing package', audit_persist: 'Persisting audit records', responding: 'Responding',
  });
  const operationPhases = Object.freeze({
    reading_history: 'Loading saved agent history metadata',
    queued: 'Accepted; waiting for a background worker slot', starting: 'Starting background operation',
    provisioning: 'Opening user-scoped workspace storage', metadata_restore: 'Restoring committed package metadata',
    artifact_files: 'Verifying committed package files', verifying_artifact: 'Verifying package checksum',
    decoding_nodes: 'Reading canonical package nodes', decoding_edges: 'Reading canonical package links',
    index_cache: 'Reusing verified package index', indexing_nodes: 'Indexing package nodes',
    indexing_edges: 'Indexing package links', preparing_package: 'Preparing package view',
    querying_inventory: 'Querying requested package view', responding: 'Returning verified result',
    committed: 'Completed', failed: 'Failed', authentication_required: 'Authentication required',
    interrupted: 'Interrupted; no automatic replay',
  });
  function operationProgressText(operation) {
    const progress = operation?.progress || {};
    const phase = operationPhases[progress.stage] || 'Loading saved workspace information';
    const done = progress.completed, total = progress.total;
    const unit = progress.unit || 'items';
    const measured = Number.isInteger(done) && Number.isInteger(total) && total > 0 && done >= 0 && done <= total;
    return `${phase}${measured ? ` · ${count(done)} of ${count(total)} ${unit}` : ''}`;
  }
  function stopLoadingDiagnostic(diagnostic = state.loadingDiagnostic) {
    if (!diagnostic) return;
    diagnostic.pending = false;
    clearTimeout(diagnostic.timer);
    diagnostic.controller?.abort();
    if (state.loadingDiagnostic === diagnostic) state.loadingDiagnostic = null;
  }
  function loadingDiagnosticLabel(diagnostic) {
    const data = diagnostic.data;
    if (!data) return diagnostic.message;
    const warehouse = data.warehouse;
    const warehouseLabel = warehouse?.availability === 'available' && warehouse.state
      ? `Warehouse: ${warehouse.state}` : 'Warehouse state unknown (unavailable)';
    const elapsed = typeof data.elapsed_seconds === 'number' && Number.isFinite(data.elapsed_seconds)
      ? `Elapsed: ${data.elapsed_seconds}s` : 'Elapsed: not reported';
    const exception = data.exception_class || warehouse?.exception_class;
    const stage = loadingPhases[data.phase] ?? 'Backend stage not yet available';
    const activity = data.status === 'queued'
      ? 'Waiting in the app request queue; package processing has not started'
      : data.status === 'failed' ? `Package request failed during ${stage.toLowerCase()}`
        : data.status === 'succeeded' ? 'Package request completed' : stage;
    return `${activity} · ${warehouseLabel} · ${elapsed}${exception ? ` · ${text(exception)}` : ''}`;
  }
  function startLoadingDiagnostic(controller) {
    stopLoadingDiagnostic();
    const uuid = crypto.getRandomValues(new Uint8Array(16));
    // Keep diagnostic IDs unique within this tab even if a browser/test crypto
    // provider repeats random bytes. The random prefix remains 96-bit.
    const sequence = ++state.loadingDiagnosticSequence;
    uuid[12] = (sequence >>> 24) & 255;
    uuid[13] = (sequence >>> 16) & 255;
    uuid[14] = (sequence >>> 8) & 255;
    uuid[15] = sequence & 255;
    uuid[6] = (uuid[6] & 15) | 64;
    uuid[8] = (uuid[8] & 63) | 128;
    const diagnostic = {
      id: [...uuid].map((byte) => byte.toString(16).padStart(2, '0')).join(''), epoch: state.epoch,
      projectId: state.projectId, memberHash: state.memberHash, pending: true,
      timer: null, controller: null, data: null, message: '',
    };
    state.loadingDiagnostic = diagnostic;
    const current = () => diagnostic.pending && state.loadingDiagnostic === diagnostic
      && diagnostic.epoch === state.epoch && diagnostic.projectId === state.projectId
      && diagnostic.memberHash === state.memberHash && !controller.signal.aborted;
    const poll = async () => {
      if (!current()) return;
      diagnostic.controller = new AbortController();
      try {
        // Independent authenticated observation. Never enter request accounting or the overview queue.
        const response = await fetch(`/api/hosted/loading/${diagnostic.id}`, {
          signal: diagnostic.controller.signal, credentials: 'same-origin', cache: 'no-store',
          headers: { Accept: 'application/json', ...contextHeaders() },
        });
        if (!current()) return;
        if (response.status === 404) {
          diagnostic.data = null; diagnostic.message = 'Backend stage not yet available';
        } else {
          if (!response.ok) throw new Error('Loading diagnostics unavailable');
          const data = await response.json();
          if (!current()) return;
          diagnostic.data = data;
        }
      } catch (error) {
        if (!current()) return;
        diagnostic.data = null; diagnostic.message = 'Loading diagnostics unavailable; backend stage unknown';
      } finally {
        if (current()) {
          updateActivity();
          diagnostic.timer = setTimeout(poll, 3000);
        }
      }
    };
    diagnostic.timer = setTimeout(poll, 3000);
    return diagnostic;
  }
  const activeRunStatuses = new Set(['queued', 'running', 'cancelling']);
  const activeWriter = run => activeRunStatuses.has(String(run?.candidate_writer?.status || '').toLowerCase());
  const activeRun = run => activeRunStatuses.has(String(run?.status || '').toLowerCase()) || activeWriter(run);
  const runState = (run, epoch) => ({status: run.status, candidate_writer: run.candidate_writer ?? null, epoch});
  function updateActivity() {
    const activeJobs = state.projectJobs.filter((job) => ['queued', 'running'].includes(job.status));
    const activeRuns = [...recordedRunStates.values()].filter((run) => run.epoch === state.epoch && activeRun(run));
    const legacyActive = state.jobId && ['queued', 'running'].includes(legacyJobStatus);
    const jobs = activeJobs.length + (legacyActive ? 1 : 0) + activeRuns.length;
    const hostedBusy = state.hosted && hostedActivity?.busy === true;
    const busy = state.requests > 0 || jobs > 0 || hostedBusy;
    const requests = [...requestActivity.values()].filter((item) => item.epoch === state.epoch
      && (!item.inventory || item.inventoryTicket === state.inventoryTicket)
      && (!item.readOnly || item.overview || item.page === state.page
        || (item.inventory && state.inventoryPreload === 'loading')
        ));
    const foreground = requests.filter((item) => !item.background);
    const loadingOverview = foreground.some((item) => item.overview);
    const loadingInventory = foreground.some((item) => item.inventory);
    const loadingReadiness = state.readinessPreload === 'loading';
    const loadingPages = state.pagePreload === 'loading';
    const activeRead = foreground.find((item) => item.operation);
    const packageRead = foreground.find((item) => (item.overview || item.inventory) && item.operation);
    const operationLabel = packageRead ? operationProgressText(packageRead.operation) : '';
    const readinessFailed = state.readinessPreload === 'failed' && baseAssessmentReady();
    const warmFailed = state.hosted && state.runtimeWarmStatus === 'unavailable' && baseAssessmentReady();
    overviewProgress.hidden = !(loadingOverview || loadingInventory || loadingReadiness || loadingPages || state.pagePreloadError || state.inventoryPreload === 'failed' || readinessFailed || warmFailed);
    overviewProgress.setAttribute('aria-busy', String(loadingOverview || loadingInventory || loadingReadiness || loadingPages));
    overviewProgressHeading.textContent = (loadingInventory || state.inventoryPreload === 'failed') && !loadingOverview ? 'Explorer inventory'
        : loadingReadiness || readinessFailed ? 'Readiness evidence' : loadingPages || state.pagePreloadError ? 'Initial page data' : warmFailed ? 'Package preparation' : 'Package understanding';
    overviewProgressDescription.textContent = state.inventoryPreload === 'failed'
      ? `Explorer inventory could not be loaded. ${state.inventoryPreloadError} Retry to enable package views.`
      : readinessFailed ? `Readiness evidence could not be loaded. ${state.readinessPreloadError} Retry to enable package views.`
      : state.pagePreloadError ? `The initial read did not confirm: ${state.pagePreloadError}. Check the relevant page status and refresh if needed.`
      : loadingPages ? 'Loading saved documents, release settings, connections, readiness and the first page of agent history. Other details remain on demand.'
      : warmFailed ? `Package preparation could not be confirmed. ${state.runtimeWarmError} Retry before invoking agents.`
      : operationLabel || (loadingInventory ? 'Loading the current Explorer inventory page (up to 50 objects). Other pages and object details are not loaded.'
        : loadingReadiness ? 'Loading saved readiness evidence for the selected package. Runtime and output parity remain unassessed.' : 'Loading selected XML understanding in a background operation.');
    if (state.hosted && state.projectId && state.memberHash && !loadingOverview) {
      const step = loadingInventory || state.inventoryPreload === 'failed' ? 1
        : loadingReadiness || readinessFailed ? 2 : 0;
      if (step) overviewProgressDescription.textContent = `Step ${step} of 2: ${overviewProgressDescription.textContent}`;
    }
    inventoryRetry.hidden = state.inventoryPreload !== 'failed';
    readinessRetry.hidden = !readinessFailed;
    warmRetry.hidden = !warmFailed;
    const diagnostic = state.loadingDiagnostic;
    const diagnosticLabel = diagnostic ? loadingDiagnosticLabel(diagnostic) : '';
    loadingDiagnosticText.textContent = diagnosticLabel ? ` ${diagnosticLabel}` : '';
    const operationProgress = packageRead?.operation?.progress;
    const verified = diagnostic?.data?.verified_files ?? (operationProgress?.unit === 'files' ? operationProgress.completed : null);
    const total = diagnostic?.data?.total_files ?? (operationProgress?.unit === 'files' ? operationProgress.total : null);
    const operationCompleted = operationProgress?.completed, operationTotal = operationProgress?.total;
    const measuredFiles = Number.isInteger(verified) && Number.isInteger(total) && total > 0 && verified >= 0 && verified <= total;
    const measuredRecords = Number.isInteger(operationCompleted) && Number.isInteger(operationTotal)
      && operationTotal > 0 && operationCompleted >= 0 && operationCompleted <= operationTotal;
    overviewProgressBar.removeAttribute('value');
    overviewProgressBar.removeAttribute('max');
    overviewProgressBar.setAttribute('aria-label', 'Package operation progress is currently indeterminate');
    if (measuredFiles) {
      overviewProgressBar.max = total; overviewProgressBar.value = verified;
      overviewProgressBar.setAttribute('aria-label', `${verified} of ${total} artifact files verified`);
    } else if (measuredRecords) {
      overviewProgressBar.max = operationTotal; overviewProgressBar.value = operationCompleted;
      overviewProgressBar.setAttribute('aria-label', `${count(operationCompleted)} of ${count(operationTotal)} ${operationProgress.unit || 'records'}`);
    }
    if (Number.isInteger(verified) && Number.isInteger(total) && verified >= 0 && total >= verified) loadingDiagnosticText.textContent += ` · ${verified} of ${total} artifact files verified`;
    const prominent = jobs > 0 || foreground.length > 0 || hostedBusy;
    const packageTarget = selectedCompletedPackage()?.name || state.overview?.package?.name || 'selected package';
    const label = (hostedBusy && hostedActivity.message) || diagnosticLabel || (activeRead
      ? (activeRead.history ? activeRead.label : `Reading saved package information for ${packageTarget}`)
      : jobs ? `${count(jobs)} active ${jobs === 1 ? 'job' : 'jobs'}` : foreground.length ? foreground[0].label : requests.length ? 'Refreshing logs' : 'Idle');
    const activity = clear($('activity-status'));
    activity.classList.toggle('is-active', busy && prominent);
    activity.append(icon(prominent ? 'running' : requests.length ? 'file' : 'check'), el('span', '', label));
    $('activity-status').title = 'Activity reflects API requests in flight and service-reported jobs, not model reasoning.';
    const banner = clear($('operation-banner'));
    banner.hidden = !prominent;
    if (prominent) {
      const copy = el('div', 'operation-copy');
      copy.append(el('strong', '', label));
      const descriptions = [...activeJobs.map((job) => `Package job ${String(job.id).slice(0, 12)}: ${job.status}`), ...(legacyActive ? [`Package job: ${legacyJobStatus}`] : []), ...activeRuns.map((run) => activeWriter(run) ? `Candidate writer: ${run.candidate_writer.status}` : `Agent run: ${run.status}`), ...foreground.map((item) => item.operation ? operationProgressText(item.operation) : item.label)];
      copy.append(el('span', 'small muted', [...new Set(descriptions)].filter(description => description !== label).join(' · ')));
      banner.append(icon('running'), copy, el('span', 'operation-note small', 'Service activity, not model reasoning'));
      if (loadingOverview) banner.append(action('View package loading progress', () => {
        navigate('estate');
        overviewProgress.focus({ preventScroll: true });
        overviewProgress.scrollIntoView({ block: 'nearest' });
      }));
    }
  }
  function requestDescription(path, options) {
    const pathname = String(path).split('?')[0];
    const background = (!options.method || options.method === 'GET') && (pathname === '/api/logs'
      || (pathname === '/api/agents/runs' && state.page !== 'agents')
      || (/^\/api\/agents\/runs\/[a-f0-9]{32}$/.test(pathname)
        && new URLSearchParams(String(path).split('?')[1]).get('status_only') === 'true'));
    let label = options.download ? 'Downloading service export' : options.method === 'POST' ? 'Saving requested changes' : 'Waiting for workspace response';
    const overview = pathname === '/api/overview';
    const inventory = pathname === '/api/objects';
    const warm = pathname === '/api/package/warm';
    if (overview) label = state.projectId ? 'Loading selected package understanding' : 'Refreshing package inventory';
    else if (pathname === '/api/package/warm') label = 'Preparing selected package workspace';
    else if (inventory) label = 'Loading selected package inventory';
    else if (pathname === '/api/projects') label = 'Loading saved projects';
    else if (/^\/api\/projects\/[a-f0-9]{32}$/.test(pathname)) label = 'Loading selected project and its XML packages';
    else if (pathname.includes('/packages/')) label = 'Submitting package';
    else if (pathname === '/api/agents/run') label = 'Submitting requested agent run';
    else if (pathname.includes('/agents/runs')) label = 'Loading saved agent runs';
    else if (pathname.includes('/jobs/')) label = 'Checking package job';
    else if (pathname === '/api/package-graph') label = 'Loading package graph';
    else if (pathname.startsWith('/api/object/')) label = 'Inspecting pipeline structure';
    else if (pathname === '/api/proposals') label = 'Loading proposal candidates';
    else if (background) label = 'Refreshing logs';
    return { label, background, overview, inventory, warm, inventoryTicket: state.inventoryTicket, history: pathname.startsWith('/api/agents/runs'), epoch: state.epoch, operation: null,
      readOnly: !options.method || options.method === 'GET', page: state.page };
  }
  function hostedBackgroundReadPath(path) {
    const pathname = String(path).split('?')[0];
    if (/^\/api\/agents\/runs\/[a-f0-9]{32}$/.test(pathname)
        && new URLSearchParams(String(path).split('?')[1] || '').get('status_only') === 'true') return false;
    if (pathname === '/api/overview') return true;
    if (state.overview?.retained_service_ready === true && pathname !== '/api/package/warm') return false;
    return pathname === '/api/package/warm' || pathname === '/api/objects' || pathname === '/api/package-graph'
      || pathname === '/api/proposals' || /^\/api\/proposals\/[a-f0-9]{64}$/.test(pathname)
      || pathname === '/api/redesigns' || /^\/api\/redesigns\/[a-f0-9]{64}$/.test(pathname)
      || pathname === '/api/reviews' || pathname === '/api/impact'
      || /^\/api\/impact\/[a-f0-9]{64}$/.test(pathname)
      || /^\/api\/agents\/runs\/[a-f0-9]{32}$/.test(pathname);
  }
  async function request(path, options = {}) {
    const controller = new AbortController();
    // Never invent a client deadline. Long Databricks reads remain observable
    // through request diagnostics; context changes still cancel stale requests.
    const readOnly = !options.method || options.method === 'GET';
    const overviewRead = path.split('?')[0] === '/api/overview' && readOnly;
    const inventoryRead = path.split('?')[0] === '/api/objects' && readOnly;
    const timer = null;
    const epoch = state.epoch;
    let diagnostic = null;
    const descriptor = requestDescription(path, options);
    requestActivity.set(controller, descriptor);
    state.requests += 1; updateActivity();
    try {
      if (readOnly && hostedBackgroundReadPath(path) && state.hosted && window.orionHostedIntake?.read) {
        const result = await window.orionHostedIntake.read(path, contextHeaders(), operation => {
          if (epoch !== state.epoch || controller.signal.aborted || (inventoryRead && descriptor.inventoryTicket !== state.inventoryTicket)
              || (!overviewRead && descriptor.page !== state.page
                && !(inventoryRead && state.inventoryPreload === 'loading')
                && !(descriptor.warm && state.runtimeWarmStatus === 'loading'))) return;
          descriptor.operation = operation;
          updateActivity();
          if (inventoryRead && operation.status === 'running')
            message($('object-list'), operationProgressText(operation));
        }, {signal: controller.signal});
        if (controller.signal.aborted) throw new DOMException('Read presentation cancelled', 'AbortError');
        if (epoch !== state.epoch) throw new Error('Selection changed; stale background read ignored.');
        return result;
      }
      diagnostic = overviewRead && state.hosted ? startLoadingDiagnostic(controller) : null;
      const response = await fetch(path, { ...options, signal: controller.signal, credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json', ...contextHeaders(), ...(options.method && options.method !== 'GET' ? { 'X-Workbench-Request': '1' } : {}), ...options.headers, ...(diagnostic ? { XWorkbenchReadId: diagnostic.id } : {}) } });
      if (epoch !== state.epoch) throw new Error('Selection changed; stale response ignored.');
      if (options.download && response.ok) {
        const blob = await response.blob();
        if (epoch !== state.epoch) throw new Error('Selection changed; stale download ignored.');
        return blob;
      }
      let payload;
      try { payload = await response.json(); } catch { throw new Error(`Service returned an unreadable response (HTTP ${response.status}).`); }
      if (epoch !== state.epoch) throw new Error('Selection changed; stale response ignored.');
      if (!response.ok) throw new Error(`HTTP ${response.status}: ${text(payload.detail ?? payload.error ?? payload)}`);
      return payload;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('Request cancelled because the selected context changed. No service completion is inferred.');
      throw error;
    } finally { if (timer !== null) clearTimeout(timer); stopLoadingDiagnostic(diagnostic); requestActivity.delete(controller); state.requests -= 1; updateActivity(); }
  }
  const api = (path, options = {}) => request(path, options);
  const post = async (path, body) => {
    if (window.orionHostedIntake && (path === '/api/agents/run' || path === '/api/loops/run')
        && await window.orionHostedIntake.probe()) {
      return window.orionHostedIntake.submit(path, body, contextHeaders());
    }
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: json(body) });
  };

  function selectedCompletedPackage() {
    return state.projectId ? array(state.project?.packages).find((member) => member.sha256 === state.memberHash && member.status === 'completed') : null;
  }
  function syncPackagePicker() {
    const members = array(state.project?.packages);
    packagePicker.dataset.projectId = state.projectId;
    packagePicker.dataset.projectName = state.project?.name ?? '';
    clear(packagePicker);
    if (!state.projectId) {
      packagePicker.append(new Option(state.overview?.package?.name ?? 'No project selected', ''));
      packagePicker.disabled = true;
      packagePickerHint.textContent = 'Open New or existing project to choose a project and view its uploaded XML packages.';
      return;
    }
    if (!members.length) packagePicker.append(new Option('No packages added yet', ''));
    for (const member of members) {
      const option = new Option(`${member.name} · ${human(member.status)}`, member.sha256);
      option.disabled = member.status !== 'completed';
      option.dataset.completed = String(member.status === 'completed');
      option.dataset.packageName = member.name;
      option.title = `${member.name} | ${member.sha256} | ${member.status}`;
      packagePicker.append(option);
    }
    packagePicker.value = state.memberHash;
    packagePicker.disabled = !members.some((member) => member.status === 'completed');
    packagePickerHint.textContent = `${members.length} package${members.length === 1 ? '' : 's'} · Single selection. Use ↑ / ↓ or type a name. All views follow this selection.`;
  }
  function resetContext() {
    syncPackagePicker();
    state.overviewTicket += 1;
    clearTimeout(state.overviewTimer); clearTimeout(state.jobTimer);
    state.jobId = null; state.jobBusy = false; state.packageKey = null; state.overview = null;
    state.graphRenderedKey = '';
    state.overviewLoading = false; state.overviewError = '';
    invalidatePackage();
    state.logsOffset = 0;
    $('logs-run').value = '';
    $('notice').hidden = true;
    ['document-form', 'attestation-form', 'agent-form'].forEach((id) => { $(id).reset(); formStatus($(id), ''); });
    $('object-search').value = ''; clearTimeout(searchTimer);
    renderOverview({ ready: false, status: state.projectId ? 'package_selection_required' : state.legacyWorkspaceSelected ? 'loading' : 'selection_required' });
    $('workspace-scope').textContent = state.projectId
      ? `Project: ${state.project?.name ?? state.projectId} · ${state.memberHash ? `package ${state.memberHash.slice(0, 12)}` : 'select a ready member'}. No cross-package binding.`
      : state.legacyWorkspaceSelected ? 'Legacy package workspace' : 'No project or XML package selected.';
    updateControls();
  }
  async function switchProject(id, preferredHash = '') {
    const legacyWorkspace = !state.hosted && id === '__legacy__';
    const projectId = legacyWorkspace ? '' : id;
    state.projectTicket += 1;
    clearTimeout(state.projectTimer);
    state.projectLoading = false;
    state.projectId = projectId; state.memberHash = ''; state.project = null; state.legacyWorkspaceSelected = legacyWorkspace; state.projectJobs = []; state.projectSubmitting = false;
    clear($('project-members')); message($('project-jobs'), 'Loading the selected project.');
    $('project-upload-form').reset(); $('project-sample-form').reset();
    formStatus($('project-upload-form'), ''); formStatus($('project-sample-form'), '');
    clear($('project-package')).append(new Option('Select a package explicitly', ''));
    $('project-package').disabled = true;
    $('project-package').dataset.emptyMessage = projectId ? 'Loading XML packages for the selected project…' : 'Select a saved project to view its XML packages.';
    $('project-select').value = legacyWorkspace ? '__legacy__' : projectId;
    resetContext();
    if (projectId) await loadProject(preferredHash);
    else if (legacyWorkspace) {
      $('project-summary').textContent = 'Legacy package workspace selected.';
      message($('project-jobs'), 'Select a project to view persisted per-file outcomes.');
      await loadOverview();
    } else {
      $('project-summary').textContent = 'Choose a project to view its uploaded XML packages.';
      message($('project-jobs'), 'Select a project to view persisted per-file outcomes.');
    }
    if (!initializingNavigation && projectId && state.projectId === projectId && state.page === 'projects' && selectedCompletedPackage())
      navigate('estate', true);
    if (state.page === 'logs') loadLogs();
  }
  async function loadProjects() {
    const epoch = state.epoch, ticket = ++state.projectsTicket;
    state.projectsLoading = true;
    updateControls();
    if (!state.projectId && !state.legacyWorkspaceSelected) $('project-summary').textContent = 'Loading saved projects from the service. Project existence is not confirmed yet.';
    $('project-select').setAttribute('aria-busy', 'true');
    try {
      const data = await api('/api/projects');
      if (epoch !== state.epoch || ticket !== state.projectsTicket) return;
      state.projectsLoaded = true;
      const selector = clear($('project-select'));
      selector.append(new Option(state.hosted ? 'Choose an existing project' : 'Choose a project', ''));
      if (!state.hosted) selector.append(new Option('Legacy package workspace', '__legacy__'));
      array(data.items).forEach((project) => $('project-select').append(new Option(`${project.name} (${count(project.ready_package_count)} ready)`, project.id)));
      $('project-select').value = state.projectId || (state.legacyWorkspaceSelected ? '__legacy__' : '');
      if (!state.projectId && !state.legacyWorkspaceSelected) {
        const projects = array(data.items);
        $('project-summary').textContent = projects.length
          ? `${projects.length} saved project${projects.length === 1 ? '' : 's'} available. Choose a project above to view its uploaded XML packages.`
          : 'No projects exist for this signed-in user yet. Create a project, then upload XML or choose a file from your Databricks Volume.';
        message($('project-members'), projects.length ? 'No project selected. Uploaded XML packages are listed after selecting a saved project.' : 'No project packages to display yet. Create a project before adding XML.');
      }
    } catch (error) {
      if (epoch === state.epoch && ticket === state.projectsTicket) {
        $('project-summary').textContent = `Saved projects could not be loaded. This does not mean they are missing. ${error.message}`;
        if (!state.projectId) $('project-package').dataset.emptyMessage = 'Project list unavailable. Use Refresh projects; no package absence is inferred.';
      }
    } finally { if (ticket === state.projectsTicket) { state.projectsLoading = false; $('project-select').setAttribute('aria-busy', 'false'); updateControls(); } }
  }
  async function loadInputs() {
    const epoch = state.epoch, ticket = ++state.inputsTicket;
    state.inputsLoading = true;
    try {
      const data = await api('/api/input-packages');
      if (epoch !== state.epoch || ticket !== state.inputsTicket) return;
      state.inputsLoaded = true;
      const checked = new Set(all('input:checked', $('server-inputs')).map((input) => input.value));
      clear($('server-inputs')).append(el('legend', '', 'Available sample packages'));
      array(data.items).forEach((item, index) => {
        const label = el('label', 'check-label');
        const input = el('input'); input.type = 'checkbox'; input.name = 'names'; input.value = item.name; input.id = `server-input-${index}`; input.checked = checked.has(item.name);
        label.htmlFor = input.id;
        label.append(input, el('span', '', `${item.name} · ${count(item.size_bytes)} bytes`));
        $('server-inputs').append(label);
      });
      if (!array(data.items).length) paragraph($('server-inputs'), 'No sample packages found in the local input folder.');
    } catch (error) { if (epoch === state.epoch && ticket === state.inputsTicket) message($('server-inputs'), `Sample packages could not be loaded. ${error.message}`); }
    finally { if (ticket === state.inputsTicket) state.inputsLoading = false; }
  }
  function renderProjectJobs() {
    const host = clear($('project-jobs'));
    if (!state.projectJobs.length) paragraph(host, 'No persisted package jobs for this project.');
    for (const job of state.projectJobs) {
      const record = el('article', 'record job-card');
      record.dataset.jobStatus = String(job.status ?? 'unknown').toLowerCase();
      record.append(el('span', 'job-stage-label', 'XML package ingestion'));
      recordHeader(record, `Job ${String(job.id).slice(0, 12)}`, job.status, job.created_at ?? job.updated_at);
      record.querySelector('.record-title').title = text(job.id);
      if (Number.isSafeInteger(job.processed) && job.processed >= 0 && Number.isSafeInteger(job.total) && job.total >= job.processed) {
        const progressLine = el('div', 'job-progress-label');
        progressLine.append(el('span', 'small muted', 'Files analyzed'), el('strong', 'stat-emphasis', `${count(job.processed)} / ${count(job.total)}`));
        record.append(progressLine);
        if (job.total > 0) {
          const progress = el('progress', 'job-progress');
          progress.max = job.total; progress.value = job.processed;
          progress.setAttribute('aria-label', `${count(job.processed)} of ${count(job.total)} files processed`);
          record.append(progress);
        }
        if (job.status === 'completed' && state.overviewLoading)
          paragraph(record, 'Ingestion complete. Package understanding is a separate step and is still loading above.', 'job-stage-note');
      }
      if (job.error) paragraph(record, job.error, 'prose error-copy');
      const tasks = el('div', 'file-tasks');
      tasks.setAttribute('role', 'list');
      for (const item of array(job.items)) {
        const row = el('div', 'file-task'); row.setAttribute('role', 'listitem');
        const detail = el('div', 'min-width-zero');
        detail.append(title(el('strong', 'task-name', item.name), item.name));
        if (item.error) paragraph(detail, item.error, 'small error-copy');
        if (item.error_diagnostic) paragraph(detail, `Failure stage: ${human(item.error_diagnostic.stage)}. Error type: ${text(item.error_diagnostic.error_type)}.${item.error_diagnostic.winerror != null ? ` Windows error: ${item.error_diagnostic.winerror}.` : item.error_diagnostic.errno != null ? ` OS error: ${item.error_diagnostic.errno}.` : ''}`, 'small error-copy');
        if (item.audit_status === 'write_failed') paragraph(detail, 'The failure was saved in this job record, but the audit log could not be written.', 'small error-copy');
        if (item.reason) paragraph(detail, item.reason, 'small muted');
        row.append(icon('file'), detail, semanticStatus(item.status)); tasks.append(row);
      }
      record.append(tasks);
      if (['failed', 'error', 'partial', 'rejected'].includes(String(job.status).toLowerCase())) viewLogs(record, job.id);
      jsonDetails(record, 'Complete package job record', job);
      host.append(record);
    }
    updateControls();
  }
  async function loadProject(preferredHash = '') {
    const id = state.projectId, epoch = state.epoch, ticket = ++state.projectTicket;
    if (!id) return;
    state.projectLoading = true;
    clearTimeout(state.projectTimer);
    $('project-package').setAttribute('aria-busy', 'true');
    updateControls();
    if (!$('project-package').options.length || $('project-package').options.length === 1)
      $('project-package').dataset.emptyMessage = 'Loading XML packages for the selected project…';
    try {
      const data = await api(`/api/projects/${encodeURIComponent(id)}`);
      if (epoch !== state.epoch || ticket !== state.projectTicket) return;
      state.project = data; state.projectJobs = array(data.jobs);
      syncPackagePicker();
      $('project-summary').textContent = `${data.name} · ${count(data.ready_package_count)} ready packages. ${data.description ?? ''}`;
      clear($('project-members'));
      if (!array(data.packages).length) message($('project-members'), 'No XML packages have been added to this project. Upload from this machine or select an XML file from Databricks Volume.');
      for (const member of array(data.packages)) {
        const record = el('div', 'record');
        paragraph(record, `${text(member.name)} · ${text(member.status)}`, 'small');
        if (member.error) paragraph(record, member.error, 'small');
        $('project-members').append(record);
      }
      const members = array(data.packages).filter((member) => member.status === 'completed' && /^[a-f0-9]{64}$/.test(member.sha256));
      if (array(data.packages).length && !members.length) paragraph($('project-members'), 'XML packages exist, but none has completed analysis. Check package job status before selecting a ready package.', 'notice');
      clear($('project-package')).append(new Option('Select a package explicitly', ''));
      members.forEach((member) => $('project-package').append(new Option(`${member.name} · ${member.sha256.slice(0, 12)}`, member.sha256)));
      $('project-package').disabled = !members.length;
      $('project-package').dataset.emptyMessage = array(data.packages).length
        ? members.length ? 'Select a completed XML package to load its analysis.' : 'Packages found. Analysis must complete before they can be selected.'
        : 'No XML packages added to this project yet.';
      const desired = state.memberHash || preferredHash;
      const valid = members.some((member) => member.sha256 === desired) ? desired : '';
      $('project-package').value = valid;
      renderProjectJobs();
      if (state.memberHash !== valid) { state.memberHash = valid; resetContext(); }
      if (valid && (!state.overview || state.overview.package?.sha256 !== valid)) await loadOverview(false);
      if (id !== state.projectId || ticket !== state.projectTicket) return;
      const active = state.projectJobs.filter((job) => ['queued', 'running'].includes(job.status));
      if (active.length) state.projectTimer = setTimeout(() => pollProjectJobs(id, state.epoch), 2000);
      if (state.page === 'logs') loadLogs();
    } catch (error) { if (epoch === state.epoch && id === state.projectId && ticket === state.projectTicket) {
      $('project-summary').textContent = `Project packages could not be loaded. ${error.message}`;
      $('project-package').dataset.emptyMessage = 'Package loading failed. Use Refresh projects; existing XML was not deleted.';
    } } finally {
      if (id === state.projectId && ticket === state.projectTicket) {
        state.projectLoading = false;
        $('project-package').setAttribute('aria-busy', 'false');
        updateControls();
      }
    }
  }
  async function pollProjectJobs(id, epoch) {
    if (id !== state.projectId || epoch !== state.epoch) return;
    try {
      const active = state.projectJobs.filter((job) => ['queued', 'running'].includes(job.status));
      for (const job of active) {
        const record = await api(`/api/projects/${encodeURIComponent(id)}/jobs/${encodeURIComponent(job.id)}`);
        if (id !== state.projectId || epoch !== state.epoch) return;
        state.projectJobs = state.projectJobs.map((item) => item.id === record.id ? record : item);
      }
      renderProjectJobs();
      await loadProject();
    } catch (error) {
      if (id === state.projectId && epoch === state.epoch) {
        paragraph($('project-jobs'), `Job status unavailable. ${error.message} Refresh projects to reconnect; completion is not assumed.`);
        // Retain the last service-reported job state, without inventing success.
      }
    }
  }
  async function submitProjectBatch(form, suffix, options) {
    const id = state.projectId, epoch = state.epoch;
    if (!id || state.projectSubmitting) return;
    state.projectSubmitting = true; updateControls();
    formStatus(form, 'Submitting XML batch to the service…');
    try {
      const job = await api(`/api/projects/${encodeURIComponent(id)}/packages/${suffix}`, { method: 'POST', ...options });
      if (epoch !== state.epoch) return;
      if (!job.id) throw new Error('No job ID returned. Refresh projects before retrying.');
      formStatus(form, `Job ${job.id}: ${text(job.status)}. Per-file outcomes appear below.`, 'success');
      state.projectJobs = [job, ...state.projectJobs.filter((item) => item.id !== job.id)]; renderProjectJobs();
      form.reset(); await loadProject();
    } catch (error) { if (epoch === state.epoch) formStatus(form, `Submission not confirmed. ${error.message}`, 'error'); }
    finally { if (id === state.projectId) { state.projectSubmitting = false; updateControls(); } }
  }
  $('project-select').addEventListener('change', () => switchProject($('project-select').value));
  function selectProjectPackage(hash) {
    if (hash && !array(state.project?.packages).some((member) => member.sha256 === hash && member.status === 'completed')) return;
    $('project-package').value = hash;
    state.memberHash = hash; resetContext();
    if (hash) loadOverview(false);
    if (state.projectJobs.some((job) => ['queued', 'running'].includes(job.status))) { clearTimeout(state.projectTimer); state.projectTimer = setTimeout(() => pollProjectJobs(state.projectId, state.epoch), 2000); }
    if (state.page === 'logs') loadLogs();
  }
  $('project-package').addEventListener('change', () => selectProjectPackage($('project-package').value));
  packagePicker.addEventListener('change', () => selectProjectPackage(packagePicker.value));
  $('refresh-projects').addEventListener('click', () => { loadProjects(); if (state.projectId) loadProject(); });
  $('refresh-inputs').addEventListener('click', loadInputs);
  $('project-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget, epoch = state.epoch;
    const button = form.querySelector('button'); button.disabled = true;
    try {
      const project = await post('/api/projects', { name: $('project-name').value.trim(), description: $('project-description').value.trim() });
      if (epoch !== state.epoch) return;
      if (!project.id) throw new Error('No project ID returned. Refresh projects before retrying.');
      await switchProject(project.id); await loadProjects(); form.reset();
      formStatus(form, 'Project created and selected. Add packages below.', 'success');
    } catch (error) { if (epoch === state.epoch) formStatus(form, `Project creation not confirmed. ${error.message}`, 'error'); }
    finally { button.disabled = false; }
  });
  $('project-upload-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget, files = [...$('project-files').files];
    const project = state.projectId, epoch = state.epoch;
    if (window.orionHostedIntake && project && !state.projectSubmitting) {
      state.projectSubmitting = true; updateControls();
      try {
        const handled = await window.orionHostedIntake.upload(form, files, project, {
          isCurrent: () => project === state.projectId && epoch === state.epoch,
          refresh: () => loadProject(),
        });
        if (handled) return;
      } catch (error) {
        if (epoch === state.epoch) formStatus(form, error.message, 'error');
        return;
      } finally { state.projectSubmitting = false; updateControls(); }
    }
    if (!files.length || files.length > 10 || files.some((file) => !/\.xml$/i.test(file.name) || file.size > 80 * 1024 * 1024) || files.reduce((total, file) => total + file.size, 0) > 160 * 1024 * 1024) { formStatus(form, 'Choose 1 to 10 XML files, at most 80 MiB each and 160 MiB combined.', 'error'); return; }
    const body = new FormData(); files.forEach((file) => body.append('files', file));
    submitProjectBatch(form, 'upload', { body });
  });
  $('project-sample-form').addEventListener('submit', (event) => {
    event.preventDefault(); const form = event.currentTarget, names = all('input:checked', $('server-inputs')).map((input) => input.value);
    if (!names.length || names.length > 10) { formStatus(form, 'Select 1 to 10 sample Informatica packages.', 'error'); return; }
    submitProjectBatch(form, 'sample', { headers: { 'Content-Type': 'application/json' }, body: json({ names }) });
  });

  const dialog = $('agent-dialog');
  $('agent-help').addEventListener('click', () => dialog.showModal());
  $('agent-dialog-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => $('agent-help').focus());
  $('agent-design-open').addEventListener('click', () => dialog.showModal());
  const evidenceDialog = $('evidence-design-dialog');
  $('evidence-design-open').addEventListener('click', () => evidenceDialog.showModal());
  $('evidence-design-close').addEventListener('click', () => evidenceDialog.close());
  evidenceDialog.addEventListener('close', () => $('evidence-design-open').focus());
  dialog.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); dialog.close(); }
    if (event.key === 'Tab') {
      const focusable = all('button, a[href], input, select, textarea, [tabindex="0"]', dialog).filter((node) => !node.disabled && !node.hidden);
      const first = focusable[0], last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  });

  function logParams(paged = true) {
    const params = new URLSearchParams({ level: $('logs-level').value, role: $('logs-role').value, run_id: $('logs-run').value.trim() });
    if (paged) { params.set('offset', String(state.logsOffset)); params.set('limit', '50'); }
    return params;
  }
  async function loadHostedDiagnostics() {
    clearTimeout(state.hostedLogsTimer);
    const ticket = ++state.hostedLogsTicket, epoch = state.epoch;
    const host = clear($('hosted-diagnostics-list'));
    if (!state.hosted) { $('hosted-diagnostics-summary').textContent = 'Databricks app diagnostics are available only in the hosted app.'; return; }
    $('hosted-diagnostics-summary').textContent = 'Loading live app diagnostics…';
    try {
      const data = await api('/api/hosted/logs?limit=100');
      if (ticket !== state.hostedLogsTicket || epoch !== state.epoch || state.page !== 'logs') return;
      const items = array(data.items);
      $('hosted-diagnostics-summary').textContent = `${count(items.length)} recent diagnostic events · ${text(data.storage)} · SQL text and credentials excluded.`;
      if (!items.length) paragraph(host, 'No hosted request failures have been recorded by this app instance.', 'small muted');
      for (const item of items) {
        const row = el('article', 'record event-card'); row.dataset.severity = item.level === 'ERROR' ? 'error' : 'warning';
        const headingNode = el('div', 'record-heading');
        headingNode.append(el('strong', 'record-title', `${text(item.method)} ${text(item.path)} · HTTP ${text(item.status_code)}`),
          el('span', 'event-severity error', text(item.error_type)), logCopyButton(item));
        row.append(headingNode, el('small', 'record-time', text(item.timestamp)));
        const error = item.error && typeof item.error === 'object' ? item.error : {};
        const summary = [error.error_code && `Databricks error: ${error.error_code}`,
          error.sql_state && `SQL state: ${error.sql_state}`,
          error.statement_id && `Statement ID: ${error.statement_id}`].filter(Boolean).join(' · ');
        if (summary) paragraph(row, summary, 'small');
        if (error.provider_message) paragraph(row, error.provider_message, 'prose');
        const frames = array(item.frames);
        if (frames.length) {
          const list = el('ol', 'diagnostic-frames');
          frames.forEach((frame) => list.append(el('li', '', `${text(frame.file)} · ${text(frame.function)} · ${text(frame.line)}`)));
          row.append(list);
        }
        jsonDetails(row, 'Complete safe diagnostic record', item);
        host.append(row);
      }
      if (state.page === 'logs') state.hostedLogsTimer = setTimeout(loadHostedDiagnostics, 4000);
    } catch (error) {
      if (ticket !== state.hostedLogsTicket || epoch !== state.epoch) return;
      $('hosted-diagnostics-summary').textContent = `Live diagnostics unavailable. ${error.message}`;
      paragraph(host, 'Databricks app platform logs remain available separately in the workspace.', 'small muted');
      if (state.page === 'logs') state.hostedLogsTimer = setTimeout(loadHostedDiagnostics, 10000);
    }
  }
  async function loadLogs() {
    clearTimeout(state.logsTimer);
    const ticket = ++state.logsTicket, epoch = state.epoch;
    $('logs-prev').disabled = true; $('logs-next').disabled = true;
    $('logs-summary').textContent = 'Loading service-recorded logs…';
    try {
      const jobId = $('logs-run').value.trim();
      const projectJob = state.projectId && state.projectJobs.some(job => job.id === jobId && job.kind === 'package_batch');
      const path = projectJob ? `/api/projects/${encodeURIComponent(state.projectId)}/jobs/${encodeURIComponent(jobId)}/logs` : '/api/logs';
      const data = await api(`${path}?${logParams()}`);
      if (ticket !== state.logsTicket || epoch !== state.epoch || state.page !== 'logs') return;
      clear($('logs-list'));
      $('logs-summary').textContent = `${count(data.total)} events · chain ${data.chain_valid === true ? 'valid' : data.chain_valid === false ? 'INVALID' : 'unknown'} · storage: ${text(data.storage)} · ${state.projectId ? `selected project ${state.projectId}, package ${state.memberHash || 'not selected'}` : 'legacy / root events'}`;
      const items = array(data.items);
      if (!items.length) paragraph($('logs-list'), 'No matching events recorded.');
      for (const item of items) {
        const record = el('article', 'record event-card');
        const severity = ['error', 'warning', 'info', 'debug'].includes(item.level) ? item.level : 'unknown';
        record.dataset.severity = severity;
        const header = el('div', 'record-heading');
        const badge = el('span', `event-severity ${severity}`);
        badge.append(icon(severity === 'error' ? 'error' : severity === 'warning' ? 'warning' : 'info'), el('span', '', human(item.level ?? 'Unknown level')));
        header.append(el('strong', 'record-title', text(item.event)), badge, logCopyButton(item));
        record.append(header, el('small', 'record-time', text(item.timestamp)));
        paragraph(record, text(item.message), 'prose');
        const metadata = el('details', 'event-metadata');
        metadata.append(el('summary', '', 'Correlation, role & token usage'));
        rows(metadata, { sequence: item.sequence, level: item.level, role: item.role, run_id: item.run_id, package_hash: item.package_hash, correlation_id: item.correlation_id,
          input_tokens: item.input_tokens ?? 'Unknown', output_tokens: item.output_tokens ?? 'Unknown', total_tokens: item.total_tokens ?? 'Unknown', usage_status: item.usage_status ?? 'Unknown' });
        record.append(metadata);
        jsonDetails(record, 'Event integrity hashes', { previous_hash: item.previous_hash, event_hash: item.event_hash });
        jsonDetails(record, 'Complete service event', item);
        $('logs-list').append(record);
      }
      $('logs-prev').disabled = state.logsOffset === 0;
      $('logs-next').disabled = state.logsOffset + items.length >= data.total;
      if (state.page === 'logs') state.logsTimer = setTimeout(loadLogs, 4000);
    } catch (error) {
      if (ticket !== state.logsTicket || epoch !== state.epoch) return;
      message($('logs-list'), `Logs unavailable. ${error.message} No events or usage have been fabricated.`);
      $('logs-summary').textContent = 'Log service unavailable. Refresh to retry.';
    }
  }
  $('logs-form').addEventListener('submit', (event) => { event.preventDefault(); state.logsOffset = 0; loadHostedDiagnostics(); loadLogs(); });
  $('logs-prev').addEventListener('click', () => { state.logsOffset = Math.max(0, state.logsOffset - 50); loadLogs(); });
  $('logs-next').addEventListener('click', () => { state.logsOffset += 50; loadLogs(); });
  $('logs-export').addEventListener('click', async () => {
    const epoch = state.epoch; $('logs-export').disabled = true;
    try {
      const blob = await request(`/api/logs/export?${logParams(false)}`, { download: true, headers: { Accept: 'text/csv' } });
      if (epoch === state.epoch) saveBlob(blob, 'workbench-logs.csv');
    } catch (error) { if (epoch === state.epoch) notify(`Log CSV unavailable. ${error.message}`, 'error'); }
    finally { $('logs-export').disabled = false; }
  });

  const packageLegend = $('package-graph-panel').querySelector('.graph-legend');
  if (packageLegend) {
    const legend = el('ul', 'graph-legend graph-status-legend');
    legend.setAttribute('aria-label', 'Graph assessment legend');
    for (const [label, description, color] of [
      ['Source structure', 'XML definitions and references. Shared definitions are not runtime dependencies.', 'neutral'],
      ['Issue recorded', 'A scoped canonical error diagnostic exists. Inspect its details.', 'red'],
    ]) {
      const row = el('li', `graph-legend-${color}`);
      row.append(el('strong', '', `${label}: `), document.createTextNode(description)); legend.append(row);
    }
    packageLegend.replaceWith(legend);
  }
  function packageGraphViewKey() {
    return json([state.epoch, state.projectId, state.memberHash, $('package-graph-kind').value,
      $('package-graph-size').value, state.graphOffset]);
  }
  async function loadPackageGraph() {
    if (!state.ready || !state.graphOpen) return;
    const viewKey = packageGraphViewKey();
    if (state.hosted && state.graphRenderedKey === viewKey) return;
    const ticket = ++state.graphTicket, epoch = state.epoch;
    state.graphBusy = true; state.graphHasMore = false;
    $('package-graph-area').setAttribute('aria-busy', 'true');
    $('package-graph-prev').disabled = true; $('package-graph-next').disabled = true;
    message($('package-graph-area'), 'Loading the selected package graph…'); clear($('package-graph-inspector'));
    $('package-graph-summary').textContent = 'Loading selected graph coverage…';
    const kind = $('package-graph-kind').value, size = $('package-graph-size').value;
    const full = size === 'all';
    $('package-graph-size-label').textContent = `${human(kind)}s shown`;
    const params = new URLSearchParams({ kind, offset: String(full ? 0 : state.graphOffset) });
    if (full) params.set('all', 'true');
    else params.set('limit', size);
    try {
      let graph;
      if (full && kind === 'workflow') {
        const nodes = new Map(), edges = new Map(), groups = new Map();
        let offset = 0, identity = null, evidenceVersion = null, expectedNodes = null, expectedEdges = null;
        while (true) {
          const chunk = await api(`/api/package-graph/chunk?offset=${offset}&limit=100`);
          if (ticket !== state.graphTicket || epoch !== state.epoch || !state.graphOpen) return;
          if (chunk.chunk !== true || chunk.offset !== offset || chunk.edge_limit !== null
              || !chunk.evidence_version || (evidenceVersion !== null && evidenceVersion !== chunk.evidence_version)
              || !Number.isInteger(chunk.total_nodes) || !Number.isInteger(chunk.total_edges)
              || (identity !== null && (identity !== chunk.package_hash || expectedNodes !== chunk.total_nodes || expectedEdges !== chunk.total_edges)))
            throw new Error('Workflow chunk identity or totals changed. No partial graph accepted.');
          identity = chunk.package_hash; evidenceVersion = chunk.evidence_version; expectedNodes = chunk.total_nodes; expectedEdges = chunk.total_edges;
          for (const [values, store] of [[chunk.nodes, nodes], [chunk.edges, edges], [chunk.groups, groups]])
            for (const value of array(values)) {
              if (!value.id || (store.has(value.id) && json(store.get(value.id)) !== json(value)))
                throw new Error('Conflicting graph information. No partial graph accepted.');
              store.set(value.id, value);
            }
          $('package-graph-summary').textContent = `Loading all workflows: ${nodes.size} of ${expectedNodes} nodes, ${edges.size} of ${expectedEdges} relationships verified.`;
          graph = chunk;
          if (!chunk.has_more) break;
          if (!Number.isInteger(chunk.next_offset) || chunk.next_offset <= offset || !array(chunk.nodes).length)
            throw new Error('Non-advancing workflow page. No partial graph accepted.');
          offset = chunk.next_offset;
        }
        if (nodes.size !== expectedNodes || edges.size !== expectedEdges
            || [...edges.values()].some(edge => !nodes.has(edge.source) || !nodes.has(edge.target)))
          throw new Error('Incomplete workflow inventory or relationships. No complete graph claimed.');
        graph = {...graph, nodes: [...nodes.values()], edges: [...edges.values()], groups: [...groups.values()],
          offset: 0, all_nodes: true, truncated: false, has_more: false, page_total_edges: edges.size,
          has_cycles: null, outside_page_cycles: 'unknown', cycle_scope: 'not_assessed_after_client_aggregation'};
      } else graph = await api(`/api/package-graph?${params}`);
      if (ticket !== state.graphTicket || epoch !== state.epoch || !state.graphOpen) return;
      const returned = array(graph.nodes).length;
      state.graphHasMore = !full && Boolean(returned) && state.graphOffset + returned < graph.total_nodes;
      $('package-graph-size').querySelector('[value="all"]').textContent = `All (${count(graph.total_nodes)})`;
      $('package-graph-summary').textContent = full && !graph.truncated
        ? `${count(returned)} ${kind}s, ${count(array(graph.edges).length)} relationships. Complete ${kind} overview.`
        : `${count(returned)} of ${count(graph.total_nodes)} ${kind}s, ${count(array(graph.edges).length)} of ${count(graph.page_total_edges)} relationships on this page (${count(graph.total_edges)} across the overview). ${graph.truncated ? 'Choose All to include every node and cross-page relationship.' : ''}`;
      $('package-graph-assessment').textContent = json(graph);
      renderGraph($('package-graph-area'), graph, { full: true, onSelect: (node) => {
        clear($('package-graph-inspector')); rows($('package-graph-inspector'), { name: node.name, id: node.id, kind: node.kind, assessment: assessmentPresentation(node).summary, evidence: node.evidence }, key => key === 'evidence' ? 'Source details' : human(key));
        $('package-graph-inspector').append(action('Open in pipeline explorer', () => { navigate('explorer', true); selectObject(node.id); }));
        // Node activation navigates using the stable canonical ID, never a guessed name.
        navigate('explorer', true); selectObject(node.id, node.assessment);
      } });
      $('package-graph-prev').disabled = full || state.graphOffset === 0;
      $('package-graph-next').disabled = full || !returned || state.graphOffset + returned >= graph.total_nodes;
      if (state.hosted) state.graphRenderedKey = viewKey;
    } catch (error) { if (ticket === state.graphTicket && epoch === state.epoch) { state.graphRenderedKey = ''; message($('package-graph-area'), `Package graph unavailable. ${error.message} No graph inferred.`); $('package-graph-summary').textContent = 'Package graph service unavailable. Hide and show to retry.'; } }
    finally {
      if (ticket === state.graphTicket && epoch === state.epoch) {
        state.graphBusy = false; $('package-graph-area').setAttribute('aria-busy', 'false'); updateControls();
      }
    }
  }
  $('show-package-graph').addEventListener('click', () => {
    state.graphOpen = !state.graphOpen; state.graphTicket += 1;
    $('package-graph-panel').hidden = !state.graphOpen;
    $('show-package-graph').setAttribute('aria-expanded', String(state.graphOpen));
    $('show-package-graph').textContent = state.graphOpen ? 'Hide package graph' : 'Show package graph';
    if (state.graphOpen && (!state.hosted || state.graphRenderedKey !== packageGraphViewKey())) loadPackageGraph();
  });
  $('package-graph-kind').addEventListener('change', () => { state.graphOffset = 0; loadPackageGraph(); });
  $('package-graph-size').addEventListener('change', () => { state.graphOffset = 0; loadPackageGraph(); });
  $('package-graph-prev').addEventListener('click', () => { state.graphOffset = Math.max(0, state.graphOffset - Number($('package-graph-size').value)); loadPackageGraph(); });
  $('package-graph-next').addEventListener('click', () => { state.graphOffset += Number($('package-graph-size').value); loadPackageGraph(); });

  // Default is explicitly light, regardless of operating-system color preference.
  let theme = 'light';
  try { if (localStorage.getItem('lakeflow-studio-theme') === 'dark') theme = 'dark'; } catch { /* Storage may be disabled. */ }
  function applyTheme() {
    document.documentElement.dataset.theme = theme;
    $('theme-toggle').setAttribute('aria-pressed', String(theme === 'dark'));
    $('theme-toggle').title = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme';
    clear($('theme-toggle')).append(icon(theme === 'dark' ? 'sun' : 'moon'));
  }
  applyTheme();
  $('theme-toggle').addEventListener('click', () => {
    theme = theme === 'light' ? 'dark' : 'light';
    applyTheme();
    try { localStorage.setItem('lakeflow-studio-theme', theme); } catch { /* In-memory theme still works. */ }
  });

  const pageNames = { projects: 'New or existing project', estate: 'Estate', 'folder-migration': 'Folder migration', readiness: 'Readiness', explorer: 'Pipeline explorer', proposals: 'Agentic Migration Proposals', evidence: 'Legacy Data Integration Artifacts', agents: 'Agent activity', validate: 'Validate', release: 'CI CD (DAB + Github)', logs: 'Logs', evolution: 'Evolution roadmap' };
  function navigationPrerequisite(page) {
    if (['projects', 'logs', 'evolution'].includes(page)) return '';
    // Keep the selected package's Estate page available as its progress view;
    // data actions on that page remain gated while loading.
    if (page === 'estate' && state.projectId && selectedCompletedPackage()) return '';
    if (initializingNavigation || state.projectsLoading || state.projectLoading)
      return 'Wait for saved projects and the selected project to finish loading.';
    if (page === 'estate' && !state.projectId && state.ready && state.inventoryPreload !== 'ready') return '';
    if (state.overviewLoading || state.jobBusy || (state.jobId && ['queued', 'running'].includes(legacyJobStatus)))
      return 'Wait for the selected package understanding to finish loading before opening package views.';
    if (state.projectId) {
      if (!selectedCompletedPackage()) return 'Select a completed XML package in the selected project first.';
      if (page === 'estate') return '';
      if (!state.ready) return 'Wait for the selected XML package understanding to finish loading.';
      if (state.overview?.package?.sha256 !== state.memberHash) return 'Load understanding for the selected project XML package first.';
    } else if (!state.ready) return 'Select a project and completed XML package first, or load a standalone legacy package.';
    if (state.inventoryPreload !== 'ready') return state.inventoryPreload === 'failed'
      ? 'Explorer inventory could not be loaded. Retry from Estate before opening package views.'
      : 'Wait for the initial Explorer inventory page to finish loading.';
    if (state.hosted && state.projectId && state.memberHash && !hostedWarmReady())
      return state.runtimeWarmStatus === 'unavailable'
        ? 'Package preparation could not be confirmed. Retry from Estate before opening package views.'
        : 'Wait for the selected package workspace to finish preparing.';
    if (state.hosted && state.projectId && state.memberHash && state.readinessPreload !== 'ready')
      return state.readinessPreload === 'failed'
        ? 'Readiness evidence could not be loaded. Retry from Estate before opening package views.'
        : 'Wait for initial readiness evidence to finish loading.';
    if (state.pagePreload === 'not_started' || state.pagePreload === 'loading')
      return 'Wait for initial saved page data to finish loading.';
    if (state.pagePreload === 'failed')
      return 'Initial page data was not confirmed. Use Refresh data on Estate to retry.';
    return '';
  }
  function navigationFallback() {
    return selectedCompletedPackage() || (!state.projectId && state.ready) ? 'estate' : 'projects';
  }
  function baseAssessmentReady() {
    return !state.overviewLoading && state.inventoryPreload === 'ready' && !state.jobBusy && !(state.jobId && ['queued', 'running'].includes(legacyJobStatus)) && state.ready && (!state.projectId || Boolean(selectedCompletedPackage()
      && state.overview?.package?.sha256 === state.memberHash));
  }
  function hostedWarmReady() {
    return state.runtimeWarmStatus === 'ready' && state.runtimeWarmKey === `${state.epoch}:${state.projectId}:${state.memberHash}`;
  }
  function assessmentReady() {
    return baseAssessmentReady() && (!state.hosted || !state.projectId || !state.memberHash
      || state.readinessPreload === 'ready');
  }
  function navigate(page, focus = false, load = true) {
    if (!Object.hasOwn(pageNames, page)) page = 'estate';
    if (navigationPrerequisite(page)) page = navigationFallback();
    if (page === 'proposals' && state.page === 'agents') $('scenario').value = $('agent-scenario').value;
    state.page = page;
    updateActivity();
    for (const key of Object.keys(pageNames)) {
      const section = $(`page-${key}`), hidden = key !== page;
      // Do not retrigger extension observers for sections whose visibility is unchanged.
      if (section.hidden !== hidden) section.hidden = hidden;
    }
    all('[data-page]').forEach((button) => {
      const active = button.dataset.page === page;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
      const reason = navigationPrerequisite(button.dataset.page);
      button.disabled = Boolean(reason);
      button.setAttribute('aria-disabled', String(Boolean(reason)));
      button.title = reason || `Open ${pageNames[button.dataset.page]}`;
    });
    $('page-name').textContent = pageNames[page];
    document.title = `${pageNames[page]} | ORION`;
    if (location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
    if (focus) $('main').focus();
    window.scrollTo(0, 0);
    clearTimeout(state.logsTimer);
    clearTimeout(state.hostedLogsTimer); state.hostedLogsTicket += 1;
    state.logsTicket += 1;
    const main = document.getElementById('main');
    const entering = !initializingNavigation && main.dataset.currentPage !== page;
    if (load) loadPage(page, false);
    // Announce actual page entry only, after startup/context prerequisites.
    // Same-page refreshes and observer rerenders are not new visits.
    if (entering) {
      main.dataset.currentPage = page;
      document.dispatchEvent(new CustomEvent('orion:navigate', { detail: { page } }));
    }
  }
  all('[data-page]').forEach((button) => button.addEventListener('click', () => {
    if (initializingNavigation && ['projects', 'logs', 'evolution'].includes(button.dataset.page)) startupUserPage = button.dataset.page;
    navigate(button.dataset.page, true);
  }));
  all('[data-go]').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.go, true)));
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1)));

  function loadPage(page, force = false) {
    if ((initializingNavigation && !['projects', 'logs', 'evolution'].includes(page)) || navigationPrerequisite(page)) return;
    if (page === 'projects') {
      if (force || (!state.projectsLoaded && !state.projectsLoading)) loadProjects();
      if (force || (!state.inputsLoaded && !state.inputsLoading)) loadInputs();
      if (state.projectId && (force || (!state.project && !state.projectLoading))) loadProject();
      return;
    }
    if (page === 'logs') { loadHostedDiagnostics(); loadLogs(); return; }
    if (!state.ready) return;
    if (page === 'explorer' && (force || !state.inventoryLoaded)) loadInventory();
    if (page === 'proposals' && $('proposal-baseline').open && (force || !state.proposalsLoaded)) loadProposals();
    if (page === 'evidence' && force && !state.documentsLoading) loadDocuments();
    if (page === 'agents' && assessmentReady() && (force || !state.agentRunsLoaded)) loadRuns(0);
    if (page === 'release') { if (force) loadSettings(); if (force || !state.proposalsLoaded) loadProposals(); else renderReleaseBlockers(); }
  }
  function agentModelAvailable() {
    return window.orionExternalModels?.availability() ?? (state.overview?.agent?.available === true);
  }
  function updateControls() {
    if (assessmentReady() && state.pagePreload === 'not_started') startInitialPagePreload();
    const hostedOperationBusy = state.hosted && Boolean(window.orionHostedIntake?.hasActiveOperation());
    if (state.hosted && state.projectId && state.memberHash && baseAssessmentReady()
      && hostedWarmReady() && state.readinessPreload === 'not_started') startHostedReadiness();
    warmRetry.disabled = hostedOperationBusy || !baseAssessmentReady();
    const agentPackageReady = String(assessmentReady());
    const selectedPackageHash = state.memberHash || state.packageKey || '';
    const workflowId = $('agent-workflow').value;
    const listedWorkflow = array(state.overview?.workflows).some(workflow => workflow.id === workflowId);
    workflowSessionStatus.hidden = !state.hosted || !workflowId;
    if (!workflowSessionStatus.hidden) {
      const confirmed = listedWorkflow && state.overview?.retained_service_ready === true
        && state.overview?.package?.sha256 === state.memberHash;
      const message = confirmed
        ? 'This workflow was found in the loaded hosted package session. A restart or eviction may require reloading.'
        : 'Workflow selected. A loaded hosted package session has not been confirmed.';
      if (workflowSessionStatus.textContent !== message) workflowSessionStatus.textContent = message;
    }
    const folderPage = $('page-folder-migration');
    for (const [key, value] of Object.entries({ projectId: state.projectId, packageHash: selectedPackageHash, packageReady: agentPackageReady }))
      if (folderPage.dataset[key] !== value) folderPage.dataset[key] = value;
    for (const id of ['page-agents', 'page-proposals'])
      if ($(id).dataset.packageHash !== selectedPackageHash) $(id).dataset.packageHash = selectedPackageHash;
    if ($('page-agents').dataset.packageReady !== agentPackageReady) $('page-agents').dataset.packageReady = agentPackageReady;
    if ($('page-proposals').dataset.packageReady !== agentPackageReady) $('page-proposals').dataset.packageReady = agentPackageReady;
    const agentRunStatus = state.agentSubmitting || state.agentRunning ? 'busy' : !state.agentRunsLoaded ? 'unknown' : 'idle';
    if ($('page-agents').dataset.agentRunStatus !== agentRunStatus) $('page-agents').dataset.agentRunStatus = agentRunStatus;
    all('.navigation [data-page]').forEach((button) => {
      const reason = navigationPrerequisite(button.dataset.page);
      button.disabled = Boolean(reason);
      button.setAttribute('aria-disabled', String(Boolean(reason)));
      button.title = reason || `Open ${pageNames[button.dataset.page]}`;
    });
    // Redirect only on invalidation, not on every control or polling update.
    // Startup first establishes hosted/local mode and restores any saved package.
    // Do not redirect from the requested deep link using the temporary empty
    // pre-startup state; enforce prerequisites after initialization completes.
    if (!initializingNavigation && !state.overviewLoading && navigationPrerequisite(state.page)) navigate(navigationFallback(), false, false);
    const ingesting = state.jobBusy || Boolean(state.jobId);
    $('load-sample').disabled = ingesting || Boolean(state.projectId);
    $('package-form').querySelector('button').disabled = ingesting || Boolean(state.projectId);
    const packageLoading = state.overviewLoading || $('project-package').getAttribute('aria-busy') === 'true';
    const activeProjectJob = state.projectJobs.some((job) => ['queued', 'running'].includes(job.status));
    const activeLegacyJob = Boolean(state.jobId && ['queued', 'running'].includes(legacyJobStatus));
    const projectBusy = packageLoading || state.projectSubmitting || activeProjectJob;
    const packageBusy = packageLoading || state.jobBusy || activeLegacyJob || activeProjectJob;
    const packageActionReady = !packageBusy && assessmentReady();
    $('refresh').disabled = packageBusy;
    $('show-package-graph').disabled = !packageActionReady;
    all('[data-go="explorer"]').forEach((button) => {
      button.disabled = !packageActionReady;
      button.title = packageBusy ? 'Wait for the selected package understanding to finish loading.'
        : !state.ready ? 'Load a completed XML package first.' : 'Explore the selected package pipelines.';
    });
    all('[data-export]').forEach((link) => {
      const disabled = !packageActionReady;
      link.setAttribute('aria-disabled', String(disabled));
      link.tabIndex = disabled ? -1 : 0;
      link.classList.toggle('is-disabled', disabled);
      link.title = disabled ? 'Wait for the selected package understanding before exporting.' : 'Download the actual service export';
    });
    for (const selector of ['#object-kind', '#object-search', '#objects-prev', '#objects-next', '[data-view]',
      '#package-graph-kind', '#package-graph-size'])
      all(selector).forEach((control) => { control.disabled = !packageActionReady; });
    $('objects-prev').disabled = !packageActionReady || !state.inventoryLoaded || state.inventoryOffset === 0;
    $('objects-next').disabled = !packageActionReady || !state.inventoryLoaded || state.inventoryOffset + state.inventoryCount >= state.inventoryTotal;
    const fullGraph = $('package-graph-size').value === 'all';
    $('package-graph-prev').disabled = !packageActionReady || !state.graphOpen || state.graphBusy || fullGraph || state.graphOffset === 0;
    $('package-graph-next').disabled = !packageActionReady || !state.graphOpen || state.graphBusy || fullGraph || !state.graphHasMore;
    $('load-impact').disabled = !packageActionReady || state.object?.node?.kind !== 'workflow';
    $('download-object').disabled = !packageActionReady || !state.object;
    // A hosted settings write must not freeze the next workflow choice. The
    // invocation button remains gated by hostedOperationBusy below.
    all('#agent-workflow, #agent-scenario, #agent-review-scope, #agent-prompt')
      .forEach((control) => { control.disabled = packageBusy || !assessmentReady() || state.agentRunning || state.agentSubmitting; });
    for (const page of ['explorer', 'readiness', 'proposals', 'evidence', 'validate', 'release']) {
      const section = $(`page-${page}`);
      section.inert = packageBusy;
      section.setAttribute('aria-busy', String(packageBusy));
    }
    $('agent-form').inert = packageBusy;
    $('agent-form').setAttribute('aria-busy', String(packageBusy));
    $('package-graph-panel').inert = packageBusy;
    $('package-graph-panel').setAttribute('aria-busy', String(packageBusy));
    const intakeBusy = String(projectBusy);
    if ($('page-projects').dataset.intakeBusy !== intakeBusy) {
      $('page-projects').dataset.intakeBusy = intakeBusy;
      document.dispatchEvent(new Event('orion:intake-busy'));
    }
    packagePicker.setAttribute('aria-busy', String(packageLoading));
    all('#project-upload-form button, #project-sample-form button').forEach((button) => { button.disabled = !state.projectId || projectBusy; });
    $('refresh-inputs').disabled = projectBusy;
    const runtimeWarmBlocked = state.hosted && Boolean(state.projectId && state.memberHash)
      && !hostedWarmReady();
    $('agent-submit').disabled = !assessmentReady() || !agentModelAvailable()
      || !$('agent-workflow').value
      || !$('agent-workflow').selectedOptions[0] || $('agent-workflow').selectedOptions[0].disabled
      || state.agentRunning || state.agentSubmitting || !state.agentRunsLoaded || hostedOperationBusy || runtimeWarmBlocked;
    $('agent-submit').title = state.agentSubmitting ? 'Agent submission is in progress; its outcome has not yet been reconciled.' : state.agentRunning
      ? 'An agent run is queued or running for this package.'
      : hostedOperationBusy ? 'A hosted operation is still active. Its lock remains in force across page navigation.'
      : runtimeWarmBlocked ? state.runtimeWarmStatus === 'loading'
        ? 'Preparing the selected package workspace; Invoke will enable when warm-up completes.'
        : 'Package review is available, but Invoke waits for a verified warm workspace. Retry preparation from Estate if needed.'
      : !state.agentRunsLoaded ? 'Waiting for persisted run status to be confirmed.' : '';
    $('agent-submit').textContent = document.getElementById('agent-review-scope')?.value === 'cohort'
      ? 'Review cohort before invoking' : 'Invoke agents';
    all('#document-form button, #attestation-form button').forEach((button) => { button.disabled = !state.ready; });
    $('settings-submit').disabled = !state.ready || !state.settingsLoaded;
    updateActivity();
    window.orionExternalModels?.render();
  }
  document.addEventListener('orion:hosted-operation-state', event => {
    hostedActivity = event.detail || null;
    updateControls();
  });
  document.addEventListener('orion:hosted-agent-accepted', event => {
    const live = event.detail;
    if (!state.hosted || !state.agentSubmitting || live?.state !== 'accepted_in_process_not_committed'
        || live.project_id !== state.projectId || live.package_hash !== state.memberHash
        || live.workflow_id !== $('agent-workflow').value || !/^[a-f0-9]{32}$/.test(live.run_id || '')) return;
    if (state.submittedRunId === live.run_id) return;
    state.submittedRunId = live.run_id;
    formStatus($('agent-form'), `Run ${live.run_id} accepted in the active app session. Live status is shown below; the Delta commit is pending.`);
    selectRun(live.run_id);
  });
  document.addEventListener('orion:external-model-state', updateControls);
  window.orionModelRouteConfigured = (result) => {
    window.orionExternalModels?.databricksConfigured(result || {});
    if (!result || result.inference_ready !== true || !state.overview?.agent) return;
    state.overview.agent = { ...state.overview.agent, available: true,
      reason: 'Workspace model saved. This configuration step does not test the model.' };
    if (state.overview.capabilities?.agents && typeof state.overview.capabilities.agents === 'object') {
      state.overview.capabilities.agents = { ...state.overview.capabilities.agents, available: true,
        status: 'model_route_configured', inference_verified: false };
    }
    $('agent-availability').textContent = 'The selected workspace model is configured for the agents. It has not been tested by this step.';
    $('agent-availability').className = 'notice';
    updateControls();
    renderCapabilities(state.overview.capabilities);
  };
  function setPackageReady(ready) { state.ready = ready; updateControls(); }

  document.addEventListener('orion:inventory-preload', event => {
    const status = $('inventory-preload-status');
    if (status) status.textContent = event.detail?.message || '';
  });
  function renderOverview(data) {
    const ready = data.ready === true;
    window.orionInventory?.setContext({projectId: state.projectId || '',
      packageHash: state.memberHash || state.packageKey || data.package?.sha256 || '', ready,
      understanding: data.understanding_sha256 || '', analysis: data.analysis_version || ''});
    // Estate-only affordances. Do not change the shared fact() helper used for
    // agent usage, captured-field counts and other unrelated facts.
    const inventoryControl = (node, kind, value, label) => {
      node.dataset.inventoryKind = kind; node.setAttribute('role', 'button'); node.tabIndex = 0;
      node.setAttribute('aria-haspopup', 'dialog'); node.setAttribute('aria-label', `Browse ${value} ${label.toLowerCase()}`);
      const open = () => window.orionInventory?.open(kind, value, node);
      node.addEventListener('click', open);
      node.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(); } });
    };
    const selected = selectedCompletedPackage();
    const packageData = data.package ?? selected;
    const packageName = typeof packageData === 'string' ? packageData : packageData?.filename ?? packageData?.name ?? packageData?.path ?? (ready ? 'Loaded package' : 'No package ready');
    $('package-name').textContent = packageName;
    $('package-name').title = packageName;
    const selectedStatus = state.overviewLoading ? 'Loading package understanding' : state.overviewError ? `Loading interrupted: ${state.overviewError}` : 'Selected package understanding not loaded';
    $('package-ready').textContent = ready ? 'Package ready' : selected ? selectedStatus : human(data.status ?? 'Not ready');
    $('package-ready').dataset.understanding = ready ? 'loaded' : selected ? state.overviewLoading ? 'loading' : state.overviewError ? 'failed' : 'selected' : 'none';
    $('package-ready').className = `status-pill ${ready ? 'good' : ''}`;
    $('connection-status').textContent = ready ? 'API connected' : 'API connected · not ready';
    $('connection-status').className = `status-pill ${ready ? 'good' : ''}`;
    clear($('metrics'));
    const keyMetrics = ['workflow', 'mapping', 'session', 'source', 'target', 'connectionreference'];
    const metricLabels = { workflow: ['Workflow', 'Workflows'], mapping: ['Mapping', 'Mappings'], session: ['Session', 'Sessions'], source: ['Source', 'Sources'], target: ['Target', 'Targets'], connectionreference: ['Connection reference', 'Connection references'] };
    const metricIcons = { workflow: 'graph', mapping: 'grid', session: 'clock', source: 'source', target: 'target', connectionreference: 'link' };
    const counts = ready && data.counts && typeof data.counts === 'object' ? keyMetrics.filter((key) => typeof data.counts[key] === 'number').map((key) => [key, data.counts[key]]) : [];
    for (const [key, value] of counts) {
      const metric = el('div', 'metric');
      const label = metricLabels[key][value === 1 ? 0 : 1];
      metric.append(icon(metricIcons[key]), el('strong', 'metric-value', count(value)), el('strong', 'metric-label', label), el('span', 'metric-caption', 'Exported definitions'));
      title(metric, label);
      inventoryControl(metric, key, value, label);
      $('metrics').append(metric);
    }
    if (!counts.length) message($('metrics'), ready ? 'The API did not supply estate counts.' : 'Estate counts are unavailable until parsing is complete.', 'panel empty');
    const summary = clear($('overview-summary'));
    heading(summary, 'Package inventory');
    if (ready) {
      const preloadStatus = el('p', 'small muted', window.orionInventory?.preloadStatus() || '');
      preloadStatus.id = 'inventory-preload-status'; preloadStatus.setAttribute('role', 'status');
      summary.append(preloadStatus);
    }
    const inventory = el('div', 'inventory-facts');
    if (ready && typeof data.coverage?.xml_elements === 'number') fact(inventory, data.coverage.xml_elements === 1 ? 'XML element' : 'XML elements', data.coverage.xml_elements, 'inventory-total');
    const inventoryLabels = { repository: ['Repository', 'Repositories'], folder: ['Folder', 'Folders'], mapping: ['Mapping', 'Mappings'], mapplet: ['Mapplet', 'Mapplets'], workflow: ['Workflow', 'Workflows'], worklet: ['Worklet', 'Worklets'], session: ['Session', 'Sessions'], source: ['Source', 'Sources'], target: ['Target', 'Targets'] };
    if (ready) for (const [key, labels] of Object.entries(inventoryLabels)) {
      if (typeof data.counts?.[key] === 'number') {
        const label = labels[data.counts[key] === 1 ? 0 : 1];
        fact(inventory, label, data.counts[key]);
        inventoryControl(inventory.lastElementChild, key, data.counts[key], label);
      }
    }
    if (inventory.childElementCount) summary.append(inventory);
    else paragraph(summary, ready ? 'No structured inventory counts supplied.' : selected ? `${selected.name}: ${selectedStatus}. Existing XML remains selected; uploading another package is optional.` : 'Package is not ready. Load an XML package or wait for the service task.');
    const narrative = text(data.summary ?? (ready ? 'No English summary supplied by the service.' : 'Package is not ready.'));
    const narrativeDetails = el('details', 'service-narrative');
    narrativeDetails.append(el('summary', '', 'Full service narrative'), el('p', 'prose', narrative));
    summary.append(narrativeDetails);
    clear($('coverage'));
    const coverage = $('coverage');
    const boundary = el('div', 'coverage-boundary');
    boundary.append(icon('warning'), el('strong', '', ready ? 'Structural inspection only' : 'Not ready for inspection'));
    paragraph(boundary, 'Not verified transformation semantic translation. Structural readiness does not authorize execution.', 'small');
    coverage.append(boundary);
    if (ready && narrative.includes('Definitions, instances, field flows, SQL/property roles, schedules and original conditional control links are retained')) {
      heading(coverage, 'Preserved in the export', 'h4');
      const bullets = el('ul', 'feature-bullets');
      for (const label of ['Definitions and instances, including disabled instances', 'Field flows and SQL/property roles', 'Schedules and original conditional control links']) {
        const item = el('li'); item.append(icon('check'), el('span', '', label)); bullets.append(item);
      }
      coverage.append(bullets);
    }
    const diagnostic = el('div', 'diagnostic-summary');
    diagnostic.append(icon('warning'), el('strong', 'stat-emphasis', count(data.diagnostics_count)), el('span', '', data.diagnostics_count === 1 ? 'reference / field diagnostic' : 'reference / field diagnostics'));
    coverage.append(diagnostic);
    paragraph(coverage, 'Diagnostics require inspection. A diagnostic count is not a count of failed jobs.', 'small muted');
    coverage.append(action('Inspect package graph', () => {
      if (!state.graphOpen) $('show-package-graph').click();
      $('package-graph-panel').scrollIntoView({ block: 'start' });
    }));
    coverage.querySelector('button').disabled = !ready;
    if (ready) {
      const lifecycle = el('div', 'lifecycle-card');
      heading(lifecycle, 'Operational context', 'h4');
      paragraph(lifecycle, 'Lifecycle: active, inactive or retired. Activity: observed usage. Execution: run history. Ownership: responsible owner. Deprecation: retirement plans. These require operational records, not XML structure.', 'small muted');
      const dimensions = data.coverage?.lifecycle_dimensions;
      if (dimensions && typeof dimensions === 'object') {
        const details = el('details', 'lifecycle-details');
        details.append(el('summary', '', 'Unavailable operational dimensions'));
        const missing = Object.values(dimensions).filter(value => value == null || value === '' || value === 'unknown').length;
        if (missing) paragraph(lifecycle, 'Source export does not contain production run history or ownership. Unavailable operational dimensions do not mean workflows are broken, inactive or retired.', 'small muted');
        for (const [key, value] of Object.entries(dimensions)) {
          const unknown = value == null || value === '' || value === 'unknown';
          const row = el('dl', `lifecycle-row${unknown ? ' lifecycle-unknown' : ' lifecycle-provided'}`);
          row.append(el('dt', '', human(key)), el('dd', '', unknown ? 'Not provided in export' : human(text(value))));
          (unknown ? details : lifecycle).append(row);
        }
        if (missing) lifecycle.append(details);
      } else paragraph(lifecycle, 'Source export does not contain production run history or ownership.', 'small muted');
      const attest = action('Provide an owner statement', () => { navigate('evidence', true); $('attestation-form')?.scrollIntoView({block: 'start'}); });
      lifecycle.append(attest);
      coverage.append(lifecycle);
    }
    $('overview-details').textContent = json({ package: data.package, coverage: data.coverage, diagnostics_count: data.diagnostics_count, diagnostics: data.diagnostics, status: data.status });
    renderCapabilities(data.capabilities);
    const workflows = array(data.workflows);
    let rememberedWorkflow = '';
    try {
      const saved = JSON.parse(sessionStorage.getItem('orion-workflow-selection') || 'null');
      if (saved?.project === state.projectId && saved?.package === data.package?.sha256)
        rememberedWorkflow = saved.workflow || '';
    } catch { /* Session storage is optional. */ }
    all('[data-workflow-select]').forEach((select) => {
      const selected = select.value || (select.id === 'agent-workflow' ? rememberedWorkflow : '');
      clear(select);
      const optional = select.dataset.optional === 'true';
      const placeholder = el('option', '', optional ? 'No workflow association' : 'Choose a workflow');
      placeholder.value = '';
      select.append(placeholder);
      for (const workflow of workflows) {
        const option = el('option', '', workflow.name ?? workflow.id);
        option.value = workflow.id;
        option.title = `${text(workflow.name)} | ${text(workflow.scope)} | ${workflow.id}`;
        if (select.id === 'agent-workflow') {
          option.dataset.workflowClass = 'Assessing';
          option.dataset.invocationLabel = 'Checking calls';
        }
        select.append(option);
      }
      if (workflows.some((workflow) => workflow.id === selected)) select.value = selected;
    });
    const agent = data.agent ?? {};
    $('agent-availability').textContent = agent.available === true ? 'Ready. Invoke agents submits the workflow, saves its design and drafts candidate notebook/SQL artifacts in one run.' : `Agent execution unavailable. ${text(agent.reason ?? 'The API has not confirmed a configured model and runnable adapter.')}`;
    $('agent-availability').className = `notice ${agent.available === true ? 'success' : ''}`;
    $('agent-roles').textContent = array(agent.roles).length ? `Configured roles: ${agent.roles.map(text).join(', ')}` : 'No configured agent roles reported.';
    setPackageReady(ready);
  }

  document.addEventListener('change', event => {
    if (!['agent-workflow', 'redesign-workflow'].includes(event.target.id)) return;
    if (state.hosted && event.target.id === 'agent-workflow') updateControls();
    const workflow = event.target.value;
    const packageHash = state.overview?.package?.sha256;
    if (!packageHash) return;
    try {
      sessionStorage.setItem('orion-workflow-selection', JSON.stringify({
        project: state.projectId, package: packageHash, workflow
      }));
    } catch { /* In-memory selection remains available. */ }
  });

  function renderCapabilities(capabilities) {
    clear($('capabilities'));
    const inventory = state.healthCapabilityInventory || state.overview?.capability_inventory;
    if (inventory?.version === 'capability-inventory-v1' && Array.isArray(inventory.items)) {
      const entries = inventory.items;
      const implemented = entries.filter(row => row.implementation === 'implemented_scoped').length;
      const overview = el('div', 'capability-overview');
      overview.append(icon('grid'), el('strong', 'stat-emphasis', count(entries.length)),
        el('span', '', `scoped capability groups: ${implemented} implemented, ${entries.length - implemented} configured/unverified. Runtime validation is separate. Execution gates still apply.`));
      paragraph(overview, inventory.count_definition, 'small muted');
      paragraph(overview, 'Blue means scoped code exists. Amber means runtime verification is missing. Green is used only for the specific check that passed.', 'small muted');
      $('capabilities').append(overview);
      const assessed = document.getElementById('capability-assessed-at');
      if (assessed) {
        const date = new Date(inventory.assessed_at);
        assessed.textContent = Number.isNaN(date.getTime()) ? 'Assessment time unknown' : `Assessed ${date.toLocaleString()} (server implementation, not deployment date)`;
        assessed.dateTime = Number.isNaN(date.getTime()) ? '' : inventory.assessed_at;
      }
      for (const row of entries) {
        const box = el('article', 'capability capability-scoped');
        box.dataset.capabilityKey = row.key;
        const implemented = row.implementation === 'implemented_scoped';
        box.append(pill(implemented ? 'Implemented, scoped' : 'Configured, unverified', implemented ? 'capability-implemented' : 'caution'));
        box.append(el('strong', '', row.label));
        paragraph(box, row.purpose, 'capability-purpose');
        const runtimeLabel = row.runtime_validation === 'request_verified' ? 'Runtime: request verified'
          : row.runtime_validation === 'local_boundary_only' ? 'Runtime: local boundary only'
            : 'Runtime: not verified';
        const runtimeClass = row.runtime_validation === 'request_verified' ? 'good'
          : row.runtime_validation === 'local_boundary_only' ? 'capability-runtime-local' : 'caution';
        box.append(pill(runtimeLabel, `capability-runtime ${runtimeClass}`));
        const details = el('details'); details.append(el('summary', '', 'Scope and limits'));
        paragraph(details, row.purpose);
        paragraph(details, row.limits);
        box.append(details); $('capabilities').append(box);
      }
      const roadmap = document.getElementById('capability-roadmap');
      if (roadmap) {
        clear(roadmap);
        for (const row of array(inventory.roadmap)) {
          const item = el('article', 'capability'); item.dataset.roadmapKey = row.key;
          const roadmapStatus = row.status || 'Unfinished';
          item.append(pill(roadmapStatus, roadmapStatus === 'Unfinished' ? 'caution' : ''), el('strong', '', row.label));
          paragraph(item, row.limits); roadmap.append(item);
        }
        if (!array(inventory.roadmap).length) paragraph(roadmap, 'No roadmap items reported. This does not establish completion.');
      }
      return;
    }
    if (state.healthCapabilities) capabilities = { ...(capabilities || {}), ...state.healthCapabilities };
    const entries = capabilities && typeof capabilities === 'object' ? Object.entries(capabilities) : [];
    if (!entries.length) { message($('capabilities'), 'The API has not reported capabilities. Availability is unknown.'); return; }
    const availableCount = entries.filter(([, value]) => value === true || value?.available === true || value?.supported === true).length;
    const overview = el('div', 'capability-overview');
    overview.append(icon('grid'), el('strong', 'stat-emphasis', count(availableCount)), el('span', '', `of ${count(entries.length)} capabilities reported available. Execution gates still apply.`));
    $('capabilities').append(overview);
    for (const [name, value] of entries) {
      const box = el('div', 'capability');
      const available = value === true || value?.available === true || value?.supported === true;
      const unavailable = value === false || value?.available === false || value?.supported === false;
      box.classList.add(available ? 'capability-available' : 'capability-unavailable');
      const stateLabel = value && typeof value === 'object' && value.status === 'roles_configured_model_route_required'
        ? 'Model route required' : value && typeof value === 'object' && value.status === 'model_route_configured'
          ? 'Model route configured' : value?.status === 'bounded_reviewed_workflows' ? 'Available: four reviewed bronze workflows'
            : value?.status === 'request_obo_authenticated' ? 'Hosted request/OBO authenticated'
              : value?.status === 'inference_unverified' ? 'Model not tested'
                : value?.status === 'app_hosted' ? 'App hosted'
                  : value?.status === 'local_only' ? 'Local loopback only, not authenticated'
                    : value?.status === 'local_app' ? 'Local app, not hosted'
                      : available ? 'Reported available' : unavailable ? 'Unavailable' : 'Reported state';
      const badge = pill(stateLabel, available ? 'good' : 'caution');
      badge.prepend(icon(available ? 'check' : unavailable ? 'warning' : 'info'));
      box.append(badge);
      const label = name === 'deployment' ? 'Generated Jobs deployment'
        : name === 'authentication' && value?.scope === 'hosted_request_proxy_obo' ? 'Request/proxy authentication'
          : name === 'authentication' && value?.scope === 'local_loopback_boundary' ? 'Local access boundary' : human(name);
      box.append(title(el('strong', 'truncate', label), label));
      if (typeof value === 'boolean') paragraph(box, value ? 'Availability reported by the service. Execution gates still apply.'
        : name === 'deployment' ? 'Generated Jobs deployment is unsupported. Hosted app deployment is reported separately; no release or deployment gate is cleared.'
          : name === 'general_code_generation' ? 'General approved-design code generation for arbitrary workflows is not implemented. The bounded reviewed compiler is reported separately.'
            : 'Unsupported or not configured.', 'prose');
      else {
        paragraph(box, value?.reason ?? value?.description ?? 'See the service capability record for scope and limitations.', 'prose');
        if (name === 'generation' && Array.isArray(value?.supported_workflows)) {
          const list = el('ul', 'small');
          for (const workflow of value.supported_workflows) list.append(el('li', '', workflow.workflow_name));
          box.append(list);
        }
        jsonDetails(box, 'Service capability record', value);
      }
      $('capabilities').append(box);
    }
  }

  function invalidatePackage() {
    stopLoadingDiagnostic();
    for (const [controller, activity] of requestActivity) if (activity.overview) controller.abort();
    document.dispatchEvent(new CustomEvent('orion:agent-run', { detail: null }));
    state.epoch += 1;
    state.inventoryTicket += 1;
    state.objectTicket += 1;
    state.proposalTicket += 1;
    state.runTicket += 1;
    state.inventoryLoaded = false;
    state.inventoryCount = 0;
    state.inventoryPreload = 'not_started'; state.inventoryPreloadError = '';
    state.proposalsLoaded = false;
    state.settingsLoaded = false; state.settingsTicket += 1;
    state.inventoryOffset = 0;
    state.proposalOffset = 0;
    state.object = null;
    state.selectedNode = null;
    state.selectedObjectId = null;
    state.objectLoadState = 'idle';
    state.proposal = null;
    state.proposals = [];
    state.runId = null;
    state.agentRunning = false;
    state.agentSubmitting = false;
    state.submittedRunId = null;
    state.agentRunsLoaded = false;
    state.runtimeWarmKey = null; state.runtimeWarmStatus = 'not_started';
    state.runtimeWarmFlight = null; state.runtimeWarmTicket += 1; state.runtimeWarmError = '';
    state.readinessPreload = 'not_started'; state.readinessPreloadError = ''; state.readinessTicket += 1; state.readinessFlight = null;
    state.pagePreload = 'not_started'; state.pagePreloadError = ''; state.pagePreloadTicket += 1; state.pagePreloadFlight = null;
    state.runsRowsLoaded = false; state.runsListTicket += 1;
    state.documentsLoaded = false; state.documentsLoading = false; state.documentsTicket += 1;
    state.graphTicket += 1;
    state.graphOffset = 0;
    state.graphOpen = false;
    $('package-graph-panel').hidden = true;
    $('show-package-graph').textContent = 'Show package graph';
    $('show-package-graph').setAttribute('aria-expanded', 'false');
    clear($('package-graph-area')); clear($('package-graph-inspector'));
    $('package-graph-summary').textContent = '';
    state.graphBusy = false; state.graphHasMore = false;
    $('package-graph-area').setAttribute('aria-busy', 'false');
    $('package-graph-size').querySelector('[value="all"]').textContent = 'All';
    $('package-graph-assessment').textContent = 'Not loaded.';
    $('package-graph-prev').disabled = true; $('package-graph-next').disabled = true;
    state.logsTicket += 1;
    clearTimeout(state.logsTimer);
    message($('logs-list'), 'Context changed. Refresh logs for the current selection.');
    $('logs-summary').textContent = 'No log request for this selection yet.';
    $('logs-prev').disabled = true; $('logs-next').disabled = true;
    clearTimeout(state.runTimer);
    ['object-list', 'proposal-list', 'proposal-detail', 'documents-list', 'runs-list', 'run-detail', 'release-blockers', 'attestation-result', 'impact-content'].forEach((id) => message($(id), 'Package context changed. Reloading requires the service to be ready.'));
    clear($('settings-form').querySelector('.form-status'));
    $('settings-form').reset();
    all('[data-workflow-select]').forEach((select) => { select.value = ''; });
    renderObject();
    publishExplorerGuidance();
    $('inventory-count').textContent = 'Not loaded.';
    $('proposal-count').textContent = 'Not loaded.';
    $('objects-prev').disabled = true;
    $('objects-next').disabled = true;
    $('proposals-prev').disabled = true;
    $('proposals-next').disabled = true;
  }

  async function preloadHostedReadiness() {
    if (!state.hosted || !state.projectId || !state.memberHash || !baseAssessmentReady()
        || state.readinessPreload !== 'not_started') return;
    const ticket = ++state.readinessTicket, selectionEpoch = state.epoch;
    const scope = {projectId: state.projectId, packageHash: state.memberHash,
      scenario: $('readiness-scenario').value, epoch: ++state.readinessEpoch};
    const current = () => ticket === state.readinessTicket && selectionEpoch === state.epoch
      && scope.projectId === state.projectId && scope.packageHash === state.memberHash;
    state.readinessPreload = 'loading'; state.readinessPreloadError = '';
    $('package-ready').textContent = 'Loading readiness evidence';
    updateControls();
    try {
      if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
      if (!current()) return;
      if (typeof window.orionReadiness?.preload !== 'function') throw new Error('Readiness module is unavailable.');
      const result = await window.orionReadiness.preload(scope);
      if (!current()) return;
      if (result?.status !== 'ready' || result.epoch !== scope.epoch
          || result.scope?.projectId !== scope.projectId || result.scope?.packageHash !== scope.packageHash
          || result.scope?.scenario !== scope.scenario || $('readiness-scenario').value !== scope.scenario)
        throw new Error(result?.message || 'Readiness was not confirmed for the selected scope.');
      state.readinessPreload = 'ready';
      if (baseAssessmentReady()) $('package-ready').textContent = 'Package ready for review';
    } catch (error) {
      if (!current()) return;
      state.readinessPreload = 'failed'; state.readinessPreloadError = error.message;
      if (baseAssessmentReady()) $('package-ready').textContent = 'Readiness unavailable';
    }
    if (current()) updateControls();
  }
  function startHostedReadiness() {
    if (state.readinessPreload !== 'not_started') return;
    const flight = preloadHostedReadiness();
    state.readinessFlight = flight;
    flight.finally(() => { if (state.readinessFlight === flight) state.readinessFlight = null; });
  }
  readinessRetry.addEventListener('click', () => {
    if (state.readinessPreload !== 'failed' || !baseAssessmentReady()) return;
    state.readinessPreload = 'not_started';
    startHostedReadiness();
  });

  async function warmHostedPackage(packageHash) {
    if (!state.hosted || !state.projectId || !packageHash || packageHash !== state.memberHash || !baseAssessmentReady()) return;
    const key = `${state.epoch}:${state.projectId}:${packageHash}`;
    if (state.runtimeWarmKey === key && ['loading', 'ready', 'unavailable'].includes(state.runtimeWarmStatus))
      return state.runtimeWarmFlight;
    const ticket = ++state.runtimeWarmTicket, epoch = state.epoch, project = state.projectId;
    state.runtimeWarmKey = key;
    state.runtimeWarmStatus = 'loading';
    state.runtimeWarmError = '';
    $('package-ready').textContent = 'Preparing package workspace';
    const status = $('agent-runtime-cache-status');
    status.hidden = false;
    status.textContent = 'Preparing the selected package workspace in the background. No model is running.';
    $('agent-runtime-cache-retry').hidden = true;
    updateControls();
    const flight = (async () => {
      let result;
      try {
        for (let attempt = 0; attempt < 3; attempt += 1) {
          if (ticket !== state.runtimeWarmTicket || epoch !== state.epoch || project !== state.projectId
              || packageHash !== state.memberHash) return;
          result = await api('/api/package/warm');
          if (result.cache_ready === true || result.cache_reason !== 'capture_busy' || attempt === 2) break;
          status.textContent = `The package is being prepared by another request. Retrying (${attempt + 1} of 2); no model is running.`;
          await new Promise(resolve => setTimeout(resolve, 750));
        }
        if (ticket !== state.runtimeWarmTicket || epoch !== state.epoch || project !== state.projectId
            || packageHash !== state.memberHash) return;
        state.runtimeWarmStatus = result?.cache_ready === true ? 'ready' : 'unavailable';
        state.runtimeWarmError = result?.cache_ready === true ? '' : `Cache not retained: ${result?.cache_reason || 'reason unavailable'}.`;
        status.textContent = result?.cache_ready === true
          ? 'Package workspace prepared for reuse. Storage and access are still verified for each action.'
          : `Package review is available. Preparing the workspace for agent runs needs another attempt. ${state.runtimeWarmError} No model was invoked.`;
        $('agent-runtime-cache-retry').hidden = result?.cache_ready === true;
        updateControls();
      } catch (error) {
        if (ticket !== state.runtimeWarmTicket || epoch !== state.epoch || project !== state.projectId
            || packageHash !== state.memberHash) return;
        state.runtimeWarmStatus = 'unavailable';
        state.runtimeWarmError = error.message;
        status.textContent = `Package review is available, but the workspace could not be prepared for agent runs. ${error.message} Use retry to try again. No model was invoked.`;
        $('agent-runtime-cache-retry').hidden = false;
        updateControls();
      }
    })().finally(() => { if (state.runtimeWarmFlight === flight) state.runtimeWarmFlight = null; });
    state.runtimeWarmFlight = flight;
    return flight;
  }
  function retryHostedPackageWarm() {
    if (state.runtimeWarmStatus !== 'unavailable' || !baseAssessmentReady()
        || window.orionHostedIntake?.hasActiveOperation()) return;
    state.runtimeWarmKey = null;
    state.runtimeWarmStatus = 'not_started';
    void warmHostedPackage(state.memberHash);
  }
  $('agent-runtime-cache-retry').addEventListener('click', retryHostedPackageWarm);
  warmRetry.addEventListener('click', retryHostedPackageWarm);

  async function loadOverview(schedule = true) {
    if ((state.hosted && !state.projectId) || (state.projectId && !state.memberHash)
        || (!state.projectId && !state.legacyWorkspaceSelected)) return;
    clearTimeout(state.overviewTimer);
    clearTimeout(searchTimer);
    if (state.pagePreload === 'loading' || state.pagePreload === 'failed') {
      state.pagePreloadTicket += 1; state.pagePreload = 'not_started'; state.pagePreloadError = ''; state.pagePreloadFlight = null;
      state.documentsTicket += 1; state.documentsLoading = false; state.settingsTicket += 1; state.runsListTicket += 1;
    }
    if (state.inventoryPreload === 'loading') window.orionScopedReads?.invalidate();
    const ticket = ++state.overviewTicket;
    const wasReady = state.ready;
    state.inventoryTicket += 1;
    state.inventoryPreload = 'not_started'; state.inventoryPreloadError = '';
    state.overviewLoading = true; state.overviewError = '';
    updateControls();
    if (selectedCompletedPackage() && !wasReady) renderOverview({ ready: false });
    else if (wasReady) $('package-ready').textContent = 'Checking latest saved state';
    api('/api/health').then((health) => {
      if (ticket === state.overviewTicket && health.status === 'ok') {
        state.healthCapabilities = health.capabilities && typeof health.capabilities === 'object' ? health.capabilities : null;
        state.healthCapabilityInventory = health.capability_inventory || null;
        if (state.healthCapabilities || state.healthCapabilityInventory) renderCapabilities(state.overview?.capabilities);
        $('connection-status').textContent = 'API connected · loading overview';
        $('connection-status').className = 'status-pill good';
      }
    }).catch(() => { /* Overview response remains the package readiness authority. */ });
    try {
      const data = await api('/api/overview');
      if (ticket !== state.overviewTicket) return;
      const key = text(data.package?.sha256 ?? data.package ?? null);
      // Loading presentation may clear state.ready before this response arrives.
      const changed = key !== state.packageKey || (wasReady && data.ready !== true);
      const becameReady = !state.ready && data.ready === true;
      if (changed) { invalidatePackage(); state.packageKey = key; }
      state.overview = data;
      if (state.hosted && state.projectId && data.retained_service_ready === true
          && data.package?.sha256 === state.memberHash) {
        state.runtimeWarmKey = `${state.epoch}:${state.projectId}:${state.memberHash}`;
        state.runtimeWarmStatus = 'ready';
        state.runtimeWarmError = '';
        $('agent-runtime-cache-status').hidden = true;
        $('agent-runtime-cache-retry').hidden = true;
      }
      renderOverview(data);
      if (data.ready === true && (!state.projectId || (selectedCompletedPackage() && data.package?.sha256 === state.memberHash))) {
        state.inventoryPreload = 'loading';
        $('package-ready').textContent = 'Loading Explorer inventory';
        updateControls();
        const loaded = await loadInventory();
        await window.orionInventory?.waitForPreload();
        if (ticket !== state.overviewTicket) return;
        state.inventoryPreload = loaded ? 'ready' : 'failed';
        if (!loaded && !state.inventoryPreloadError) state.inventoryPreloadError = 'The current page was not confirmed.';
        $('package-ready').textContent = loaded ? (state.hosted && state.projectId && !hostedWarmReady()
          ? state.runtimeWarmStatus === 'unavailable' ? 'Package preparation unavailable' : 'Preparing package workspace'
          : state.hosted && state.projectId && state.readinessPreload !== 'ready'
            ? state.readinessPreload === 'failed' ? 'Readiness unavailable' : 'Loading readiness evidence'
            : 'Package ready') : 'Explorer inventory unavailable';
      }
      state.overviewLoading = false;
      if (data.ready !== true) renderOverview(data);
      else updateControls();
      if (data.ready && data.package?.sha256) {
        const packageHash = data.package.sha256;
        if (state.hosted && state.projectId && state.memberHash === packageHash)
          void warmHostedPackage(packageHash);
        if (state.hosted && state.projectId && state.memberHash === packageHash && hostedWarmReady())
          startHostedReadiness();
        api('/api/workflow-invocation-summary').then(summary => {
          if (ticket !== state.overviewTicket || state.overview?.package?.sha256 !== packageHash
              || summary.package_hash !== packageHash) return;
          for (const option of $('agent-workflow').options) {
            if (!option.value) continue;
            const calls = summary.workflows?.[option.value];
            if (summary.basis !== 'literal_pmcmd_calls_only_other_dependencies_not_assessed'
                || !calls || !['outgoing', 'incoming', 'unresolved'].every(key => Number.isSafeInteger(calls[key]) && calls[key] >= 0)) {
              option.dataset.workflowClass = 'Not assessed';
              option.dataset.invocationLabel = 'XML call information unavailable';
              continue;
            }
            option.dataset.workflowClass = calls.outgoing || calls.incoming ? 'Composite candidate'
              : calls.unresolved ? 'Needs review' : 'Standalone candidate';
            const labels = [];
            if (calls.outgoing) labels.push('Calls other workflows');
            if (calls.incoming) labels.push('May be called by another workflow');
            if (calls.unresolved) labels.push('Call target not confirmed');
            option.dataset.invocationLabel = labels.join(' · ') || 'No workflow calls found in XML';
          }
        }).catch(() => {
          if (ticket !== state.overviewTicket || state.overview?.package?.sha256 !== packageHash) return;
          for (const option of $('agent-workflow').options)
            if (option.value) {
              option.dataset.workflowClass = 'Not assessed';
              option.dataset.invocationLabel = 'XML call information unavailable';
            }
        });
      }
      syncPackagePicker();
      // Package readiness refreshes package views, not the project list that
      // initiated this hydration. Re-entering Projects here repeats its reads.
      if ((changed || becameReady) && state.page !== 'projects') loadPage(state.page);
      if (!state.ready && schedule && !state.jobId && !state.projectId) {
        const id = data.task_id ?? data.job_id ?? data.task?.id ?? data.job?.id;
        if (id) startJob(id);
        // An empty standalone context has no running work to poll. Repeated
        // state hydration here starves the user's project/log requests.
      }
    } catch (error) {
      if (ticket !== state.overviewTicket) return;
      state.overviewLoading = false; state.overviewError = error.message;
      if (selectedCompletedPackage()) renderOverview({ ready: false });
      $('connection-status').textContent = 'Overview unconfirmed';
      $('connection-status').className = 'status-pill warning';
      setPackageReady(false);
      notify(`Cannot load overview. ${error.message} No readiness is assumed. Use Refresh data to retry.`, 'error');
      try {
        const health = await api('/api/health');
        if (ticket !== state.overviewTicket) return;
        if (health.status === 'ok') {
          $('connection-status').textContent = 'API connected · overview delayed';
          $('connection-status').className = 'status-pill caution';
        }
      } catch { /* Do not infer server outage from an overview timeout alone. */ }
    }
  }

  async function ingest(path, body) {
    if (state.projectId || state.jobBusy || state.jobId) return;
    let epoch = state.epoch;
    state.jobBusy = true;
    updateControls();
    $('ingest-status').textContent = 'Submitting package to the service…';
    try {
      const job = await api(path, { method: 'POST', ...(body ? { body } : {}) });
      state.overviewTicket += 1;
      clearTimeout(state.overviewTimer);
      invalidatePackage();
      epoch = state.epoch;
      setPackageReady(false);
      $('package-ready').textContent = 'Package job submitted';
      $('package-ready').className = 'status-pill';
      message($('metrics'), 'Counts are unavailable while the submitted package is being processed.', 'panel empty');
      $('overview-summary').textContent = 'Submitted a package job. The previous package is not shown as the new result.';
      $('overview-details').textContent = json(job);
      if (!job.id) throw new Error('The service did not return a job ID. Refresh to inspect the actual package state.');
      startJob(job.id, job.status);
    } catch (error) {
      if (epoch !== state.epoch) return;
      $('ingest-status').textContent = `Package submission could not be confirmed. ${error.message}`;
      notify($('ingest-status').textContent, 'error');
    } finally { if (epoch === state.epoch) { state.jobBusy = false; updateControls(); } }
  }
  function startJob(id, status) {
    clearTimeout(state.jobTimer);
    clearTimeout(state.overviewTimer);
    state.jobId = String(id);
    legacyJobStatus = String(status ?? '').toLowerCase();
    $('ingest-status').textContent = `Job ${id}: ${text(status ?? 'Status pending')}. Polling the service every 2 seconds.`;
    $('ingest-status').prepend(semanticStatus(status));
    updateControls();
    state.jobTimer = setTimeout(pollJob, 2000);
  }
  async function pollJob() {
    const id = state.jobId, epoch = state.epoch;
    if (!id) return;
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (epoch !== state.epoch || state.jobId !== id) return;
      legacyJobStatus = String(job.status ?? '').toLowerCase();
      $('ingest-status').textContent = `Job ${id}: ${text(job.status)}${job.error ? `. ${text(job.error)}` : ''}`;
      $('ingest-status').prepend(semanticStatus(job.status));
      if (['failed', 'error', 'partial'].includes(legacyJobStatus)) viewLogs($('ingest-status'));
      if (terminal.has(String(job.status).toLowerCase())) {
        state.jobId = null;
        updateControls();
        await loadOverview(false);
      } else {
        await loadOverview(false);
        if (state.jobId === id) state.jobTimer = setTimeout(pollJob, 2000);
      }
    } catch (error) {
      if (epoch !== state.epoch || state.jobId !== id) return;
      state.jobId = null;
      updateControls();
      $('ingest-status').textContent = `Job ${id}: status unavailable. ${error.message} Refresh to reconnect; the job may still be running.`;
    }
  }
  $('load-sample').addEventListener('click', () => ingest('/api/packages/sample'));
  $('package-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const file = $('package-file').files[0];
    if (!file || !/\.xml$/i.test(file.name)) { notify('Choose an XML export before uploading.', 'error'); return; }
    const body = new FormData();
    body.append('file', file);
    ingest('/api/packages/upload', body);
  });

  async function loadInventory() {
    if (!state.ready) { message($('object-list'), 'A ready package is required.'); return false; }
    const ticket = ++state.inventoryTicket;
    const params = new URLSearchParams({ kind: $('object-kind').value, q: $('object-search').value, offset: String(state.inventoryOffset), limit: '50' });
    message($('object-list'), 'Loading inventory…');
    $('objects-prev').disabled = true;
    $('objects-next').disabled = true;
    try {
      const data = await api(`/api/objects?${params}`);
      if (ticket !== state.inventoryTicket) return false;
      if (!Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0)
        throw new Error('The service did not return a valid inventory page.');
      state.inventoryLoaded = true;
      state.inventoryTotal = data.total;
      state.inventoryCount = data.items.length;
      clear($('object-list'));
      const items = array(data.items);
      $('inventory-count').textContent = `${count(data.total)} matching objects. Showing ${items.length ? state.inventoryOffset + 1 : 0}–${state.inventoryOffset + items.length}.`;
      $('object-page').textContent = `Page ${Math.floor(state.inventoryOffset / 50) + 1}`;
      if (!items.length) message($('object-list'), 'No objects match this search.');
      for (const node of items) {
        const button = action('', () => selectObject(node.id), 'object-item');
        button.dataset.objectId = node.id;
        button.append(title(el('span', 'truncate', node.name ?? node.id), node.name ?? node.id));
        button.append(title(el('small', 'truncate', `${human(node.kind)} · ${[node.scope?.repository, node.scope?.folder].filter(Boolean).join(' / ') || 'Captured package scope'}`), `${text(node.scope)} | ${node.id}`));
        $('object-list').append(button);
      }
      syncObjectSelection();
      $('objects-prev').disabled = state.inventoryOffset === 0;
      $('objects-next').disabled = !(state.inventoryOffset + items.length < data.total);
      return true;
    } catch (error) {
      if (ticket !== state.inventoryTicket) return false;
      state.inventoryLoaded = false;
      message($('object-list'), `Inventory unavailable. ${error.message}`);
      $('inventory-count').textContent = 'Could not retrieve object counts.';
      if (state.inventoryPreload === 'loading') state.inventoryPreloadError = error.message;
      return false;
    }
  }
  inventoryRetry.addEventListener('click', () => { void loadOverview(false); });
  let searchTimer;
  $('object-search').addEventListener('input', () => {
    state.inventoryTicket += 1;
    clearTimeout(searchTimer);
    state.inventoryOffset = 0;
    searchTimer = setTimeout(loadInventory, 250);
  });
  $('object-kind').addEventListener('change', () => { state.inventoryOffset = 0; loadInventory(); });
  $('objects-prev').addEventListener('click', () => { state.inventoryOffset = Math.max(0, state.inventoryOffset - 50); loadInventory(); });
  $('objects-next').addEventListener('click', () => { state.inventoryOffset += 50; loadInventory(); });

  async function selectObject(id, assessment, refresh = false) {
    // invalidatePackage clears the loaded object and selection on every context epoch.
    // Reuse only this exact loaded root, never a graph child, pending read or failed retry.
    if (!refresh && state.ready && state.objectLoadState === 'loaded'
        && state.selectedObjectId === id && state.object?.node?.id === id) return;
    const ticket = ++state.objectTicket;
    state.selectedObjectId = id;
    state.objectLoadState = 'loading';
    syncObjectSelection();
    state.object = null;
    state.selectedNode = null;
    renderObject();
    $('object-title').textContent = 'Loading object…';
    clear($('impact-content'));
    try {
      const model = await api(`/api/object/${encodeURIComponent(id)}`);
      if (ticket !== state.objectTicket) return;
      if (!model.node) throw new Error('The API did not return an object node.');
      if (assessment) model.node.assessment = assessment;
      state.object = model;
      state.objectLoadState = 'loaded';
      state.selectedNode = model.node;
      renderObject();
      if (model.node.kind === 'workflow') {
        // Only the current, successfully loaded canonical workflow may hand off
        // scope. Graph-child clicks and stale object responses never select runs.
        if ([...$('agent-workflow').options].some(option => option.value === id && !option.disabled))
          $('agent-workflow').value = id;
        document.dispatchEvent(new CustomEvent('orion:workflow-selected', { detail: {
          workflowId: id, projectId: state.projectId, packageHash: state.memberHash || state.packageKey,
          epoch: state.epoch, objectTicket: ticket, scenario: $('agent-scenario').value,
        } }));
        updateControls();
        loadWorkflowAssessment(id, ticket);
      }
      syncObjectSelection();
    } catch (error) {
      if (ticket !== state.objectTicket) return;
      state.objectLoadState = 'failed';
      syncObjectSelection();
      $('object-title').textContent = 'Object loading failed';
      $('object-summary').textContent = error.message;
      message($('graph-area'), 'The selected object could not be loaded. No object absence is inferred.');
      $('graph-area').append(action('Retry selected object', () => selectObject(id, assessment)));
    }
  }
  function syncObjectSelection() {
    all('[data-object-id]', $('object-list')).forEach((button) => {
      const selected = button.dataset.objectId === state.selectedObjectId;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
      button.setAttribute('aria-busy', String(selected && state.objectLoadState === 'loading'));
      button.dataset.loadState = selected ? state.objectLoadState : 'idle';
    });
    publishExplorerGuidance();
  }
  function publishExplorerGuidance() {
    document.dispatchEvent(new CustomEvent('orion:explorer-state', { detail: {
      objectId: state.selectedObjectId, nodeId: state.selectedNode?.id || '', view: state.view,
      loadState: state.objectLoadState,
    } }));
  }
  window.orionOpenWorkflow = ({ workflowId, projectId, packageHash, scenario } = {}) => {
    const currentPackage = state.projectId ? state.memberHash : state.overview?.package?.sha256;
    if (!assessmentReady() || (projectId || '') !== state.projectId || !currentPackage
        || packageHash !== currentPackage || !['original', 'bronze'].includes(scenario)
        || !array(state.overview?.workflows).some(row => row.id === workflowId)) return false;
    $('scenario').value = scenario;
    $('agent-scenario').value = scenario;
    $('agent-workflow').value = workflowId;
    navigate('explorer', true, false);
    selectObject(workflowId);
    selectView('graph');
    return true;
  };
  function selectView(view, focus = false) {
    state.view = view;
    all('[data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      $(`model-${button.dataset.view}`).hidden = !active;
      if (active && focus) button.focus({ preventScroll: true });
    });
    publishExplorerGuidance();
  }
  async function loadWorkflowAssessment(id, ticket) {
    const epoch = state.epoch;
    const target = document.getElementById('workflow-agent-assessment');
    if (!target) return;
    try {
      const records = await api('/api/agents/runs');
      if (epoch !== state.epoch || ticket !== state.objectTicket || !target.isConnected) return;
      const latest = array(records.items).filter((r) => r.workflow_id === id && r.status === 'completed' && !r.stale)
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0];
      if (!latest) {
        target.textContent = 'No current completed agent assessment is recorded for this workflow. XML-derived behavior below is not an AI-generated proposal.';
        return;
      }
      const run = await api(`/api/agents/runs/${encodeURIComponent(latest.id)}`);
      if (epoch !== state.epoch || ticket !== state.objectTicket || !target.isConnected) return;
      clear(target);
      paragraph(target, `Recorded agent assessment · ${scenarioLabel(run.scenario)} · ${human(run.output?.status ?? run.status)}`, 'small muted');
      if (run.stale) { paragraph(target, 'This assessment became stale. Reassessment is required before reuse.'); return; }
      const currentDiagnostics = new Map(array(run.diagnostic_assessment?.items).map(item => [item.diagnostic_id, item]));
      const verifiedSummary = finding => {
        const summary = String(finding.summary ?? 'No finding summary recorded.');
          const connectionOnly = /\b(connection references?|connection metadata|redacted|oracle)\b/i.test(summary);
          if (!/\b(unresolved|ambiguous|not unique|lack(?:s)? unique|missing (?:field|definition|reference|connection))\b/i.test(summary)
              && !connectionOnly) return summary;
        const scopes = array(finding.diagnostic_scopes).filter(scope => {
          const diagnostic = currentDiagnostics.get(scope.diagnostic_id);
          return diagnostic && scope.owner_id === diagnostic.owner_id && scope.impact === diagnostic.impact
            && typeof scope.reason === 'string' && scope.reason.trim();
        });
        const exactNames = new Set(scopes.flatMap(scope => {
          const diagnostic = currentDiagnostics.get(scope.diagnostic_id);
          return [diagnostic?.name, diagnostic?.reference_name, diagnostic?.owner_name]
            .filter(value => typeof value === 'string').map(value => value.toLowerCase());
        }));
        const mentioned = [...summary.matchAll(/['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]/g)].map(match => match[1])
          .filter(name => !name.toLowerCase().startsWith('wkf_'));
        const namesMatch = mentioned.every(name => exactNames.has(name.toLowerCase()));
        const onlyHistoricalConnection = connectionOnly && scopes.length > 0
          && scopes.every(scope => scope.impact === 'non_blocking_context');
        if (onlyHistoricalConnection) return 'Historical Oracle connection metadata is informational only for source data already in Unity Catalog.';
        return scopes.length && namesMatch && !(finding.status === 'unsupported' && scopes.every(scope => scope.impact === 'human_review'))
          ? summary : 'No verified reference or field issue recorded for this role.';
      };
      paragraph(target, run.output?.summary ?? 'No agent summary recorded.');
      for (const finding of array(run.output?.findings)) {
        heading(target, `${roleLabel(finding.role)}: ${human(finding.status)}`, 'h4');
        paragraph(target, verifiedSummary(finding));
        list(target, array(finding.proposed_changes), 'No design changes recorded by this role.');
        if (array(finding.blockers).length) list(target, finding.blockers);
      }
      target.append(action('Open this agent run', () => { navigate('agents', true); selectRun(run.id); }));
    } catch (error) {
      if (epoch === state.epoch && ticket === state.objectTicket && target.isConnected)
        target.textContent = `Recorded agent assessment could not be loaded. ${error.message} No generated explanation is assumed.`;
    }
  }
  all('[data-view]').forEach((button, index, buttons) => {
    button.addEventListener('click', () => selectView(button.dataset.view));
    button.addEventListener('keydown', (event) => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
      if (event.key === 'ArrowLeft') next = (index - 1 + buttons.length) % buttons.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = buttons.length - 1;
      if (next !== undefined) { event.preventDefault(); selectView(buttons[next].dataset.view, true); }
    });
  });
  function renderFixtureMetadata(parent, workflowId) {
    const panel = el('section', 'disclosure');
    heading(panel, 'Live UC source fixture metadata', 'h3');
    paragraph(panel, 'Read metadata checks the existing source table schemas and displays the result here. It does not save a destination mapping, copy rows, run conversion or approve a binding. Hosted request progress may be recorded for diagnostics. Called child sources are reviewed separately.', 'small muted');
    const label = el('label', '', 'Fixture catalog');
    const catalog = el('input'); catalog.type = 'text'; catalog.maxLength = 255;
    catalog.value = 'infotodbxmig_src_dev_ssuman';
    label.append(catalog); panel.append(label);
    const output = el('div'); output.setAttribute('aria-live', 'polite');
    const button = action('Read metadata for selected workflow', async () => {
      const epoch = state.epoch, ticket = state.objectTicket;
      button.disabled = true;
      message(output, 'Reading current user UC schema and table metadata. Binding approval remains false.');
      try {
        const result = await post('/api/workflows/fixture-metadata', {
          workflow_id: workflowId, catalog: catalog.value.trim(), profile: state.hosted ? null : 'labs'
        });
        if (epoch !== state.epoch || ticket !== state.objectTicket || !panel.isConnected) return;
        clear(output);
        const empty = result.binding_status === 'no_direct_sources' ? 'No direct sources captured for this workflow.'
          : 'No resolved sources in this workflow; unresolved definitions need review.';
        paragraph(output, `${array(result.sources).length ? 'Source fixture observations only, not approved production bindings.' : empty} Target bindings unapproved (${array(result.targets).length} direct). Observed ${result.observed_at}.`);
        if (result.reference_diagnostics?.summary) {
          const summary = result.reference_diagnostics.summary;
          paragraph(output, `${summary.source_definition_blockers} source-definition blockers; ${summary.unresolved_total} total canonical reference gaps. Fixture data cannot resolve these XML references. Empty fixtures are not successful source-data validation.`, 'small muted');
        }
        for (const source of array(result.sources)) {
          heading(output, source.actual_full_name || source.expected_full_name || source.source_id, 'h4');
          paragraph(output, `${human(source.status)}. Table exists: ${source.exists === null ? 'unknown' : source.exists}. Schema exists: ${source.schema_exists === null ? 'unknown' : source.schema_exists}. Observed: ${source.observed_at || 'not read'}.`);
          if (source.error) paragraph(output, `Metadata error: ${source.error}. Not treated as missing.`);
          jsonDetails(output, 'Actual columns, expected schema and package fixture source information', source);
        }
        if (!array(result.sources).length) paragraph(output, 'Called child sources are separate scopes. See Convert together before choosing a child for a separate, bounded metadata read. No fixture existence is inferred.');
        jsonDetails(output, 'Scoped metadata observation and unresolved targets', result);
      } catch (error) {
        if (epoch === state.epoch && ticket === state.objectTicket && panel.isConnected)
          message(output, `Metadata read failed: ${error.message}. Existence remains unknown, no approval inferred.`);
      } finally {
        if (panel.isConnected) button.disabled = false;
      }
    });
    panel.append(button, output); parent.append(panel);
  }
  function assessmentPresentation(node) {
    const assessment = node.assessment;
    const structuralOnly = assessment?.basis === 'structural_unverified' && assessment?.status !== 'red';
    if (!assessment || structuralOnly || !['green', 'amber', 'red'].includes(assessment.status)) {
      const reason = 'XML/source structure only. Package risk not assessed, not marked safe. No completed agent or service assessment is recorded; correctness has not been scored.';
      return {status: 'neutral', reason, summary: reason};
    }
    return {status: assessment.status, reason: assessment.reason || 'Inspect recorded assessment details.', summary: assessment};
  }
  function renderPackageMigrationContext(parent, contract, loadPage) {
    clear(parent);
    heading(parent, 'Package migration context', 'h3');
    heading(parent, 'Where this workflow fits', 'h4');
    paragraph(parent, `${contract.selected.name || 'This workflow'} is part of a captured invocation group of ${contract.cohort.total} workflow(s). The group includes related callers and children; it is not an execution order or a single Job.`);
    const directCalls = array(contract.invocations.items).filter(call => call.direction === 'inward' || call.direction === 'outward');
    if (directCalls.length) {
      list(parent, directCalls.map(call => {
        const other = call.direction === 'inward' ? (call.caller_name || 'An unnamed caller') :
          (array(call.candidate_names).filter(Boolean).join(', ') || call.workflow_name || 'an unresolved child');
        const relation = call.direction === 'inward' ? `${other} can call this workflow` : `This workflow can call ${other}`;
        const mode = call.wait_mode === 'nowait' ? 'without waiting for completion' :
          call.wait_mode === 'wait' ? 'with a wait requested' : 'with waiting behavior not established';
        return `${relation}, ${mode}. This is a captured command definition, not proof of a live execution.`;
      }));
    } else {
      paragraph(parent, 'No direct caller or child is shown on this page. Other invocation forms and external schedulers may not be captured.', 'small muted');
    }
    if (contract.invocations.truncated) paragraph(parent, 'The call list is paged; review the other pages before treating it as complete.', 'small muted');
    paragraph(parent, `Source: selected saved canonical package ${contract.package_hash || 'not supplied'}; ${contract.version || 'context version not supplied'}. This is captured package information, not live execution.`, 'small muted');
    paragraph(parent, `Package risk not assessed. ${contract.selected.direct_sources} direct sources and ${contract.selected.direct_targets} direct targets in the selected workflow. Child counts below are XML metadata, not UC observations or readiness.`, 'small muted');
    if (contract.selected.source_explanation) paragraph(parent, contract.selected.source_explanation);
    paragraph(parent, 'Definition counts do not resolve embedded SQL, Java or procedure side effects. Source connectivity and output parity remain unverified.', 'small muted');
    if (contract.reference_diagnostics) {
      const report = contract.reference_diagnostics, summary = report.summary;
      heading(parent, 'Reference resolution and information needed', 'h4');
      const historicalConnections = summary.categories?.historical_connection_redaction || 0;
      paragraph(parent, `${summary.unresolved_total} unresolved references in the saved analysis, including ${historicalConnections} historical connection redactions. ${summary.source_definition_blockers} source-definition blockers; ${summary.external_connection_metadata_gaps} separately recorded external connection metadata gaps. These counts do not establish physical table or source-data availability.`, 'small muted');
      if (historicalConnections) paragraph(parent, 'The saved analysis withheld these connection values. They are not missing source definitions. A newer parser cannot change this immutable saved analysis automatically; a separately versioned canonical reanalysis is needed.', 'small muted');
      paragraph(parent, 'This view uses the selected saved analysis. A fresh parser result does not replace historical artifacts or activate a new version. Creating or populating mock tables does not repair XML references.', 'small muted');
      const refs = el('details'); refs.append(el('summary', '', `Inspect reference causes (${report.references.items.length} of ${report.references.total})`));
      list(refs, array(report.references.items).map(row => `${row.name}: ${human(row.status)}. ${row.action}`), 'No unresolved canonical references in this definition closure. External SQL, parameters and runtime bindings still require review.');
      parent.append(refs);
    }
    if (contract.sql_dependencies) {
      const sql = el('details'); sql.append(el('summary', '', `Static SQL dependencies (${contract.sql_dependencies.items.length} of ${contract.sql_dependencies.total})`));
      list(sql, array(contract.sql_dependencies.items).map(row => `${human(row.phase)}: ${human(row.status)}. ${array(row.tables).map(table => `${array(table.parts).join('.')} (${human(table.qualification)})`).join(', ') || 'No static table identity established'}. ${row.action}`), 'No captured SQL properties in this scope. This is not proof of no external data dependencies.');
      parent.append(sql);
    }
    heading(parent, 'Convert together', 'h4');
    paragraph(parent, contract.convert_together);
    paragraph(parent, `${contract.referenced_children.total} outward referenced child scopes: ${contract.referenced_children.source_count_sum} source uses and ${contract.referenced_children.target_count_sum} target uses. The wider cohort below also includes callers and connected siblings.`, 'small muted');
    paragraph(parent, `${contract.cohort.total} workflows in the connected invocation cohort. ${contract.cohort.direct_source_count_sum} source uses and ${contract.cohort.direct_target_count_sum} target uses across workflow scopes; shared definitions may repeat.`, 'small muted');
    const members = el('details'); members.append(el('summary', '', `Review cohort members (${contract.cohort.items.length} returned of ${contract.cohort.total})`));
    list(members, array(contract.cohort.items).map(w => `${w.name || 'Workflow name not supplied'} (${w.scope?.repository || 'repository unknown'} / ${w.scope?.folder || 'folder unknown'}): ${count(w.direct_sources)} direct sources, ${count(w.direct_targets)} direct targets, ${count(w.unresolved_references)} unresolved references. Readiness not assessed.`));
    parent.append(members);
    const children = array(contract.referenced_children.items);
    if (children.length) {
      const review = el('details'); review.append(el('summary', '', `Open a called workflow for scoped review (${children.length} returned)`));
      for (const child of children) review.append(action(`Review child: ${child.name || child.workflow_id}`, () => {
        navigate('explorer', true); selectObject(child.workflow_id);
      }));
      paragraph(review, 'Opening a child reads its package information only. Reading Unity Catalog, running agents and executing jobs are separate actions.', 'small muted');
      parent.append(review);
    }
    heading(parent, 'Invocation and completion boundaries', 'h4');
    paragraph(parent, `${contract.invocations.total} captured invocation sites in the cohort. Async launches are not completion dependencies. External callers and runtime routing remain unverified.`, 'small muted');
    const calls = el('details'); calls.append(el('summary', '', `Review inward, outward and cohort invocation details (${contract.invocations.items.length} returned)`));
    list(calls, array(contract.invocations.items).map(r => `${human(r.direction || 'direction unknown')}: ${r.caller_name || 'Caller name unknown'} → ${array(r.candidate_names).filter(Boolean).join(', ') || r.workflow_name || 'unresolved child'}. Command definition: ${r.command_name || 'not supplied'}. Launch mode: ${r.wait_mode === 'nowait' ? 'nowait, asynchronous launch, not child completion' : r.wait_mode === 'wait' ? 'wait requested, runtime completion unverified' : 'unknown or not specified'}. ${r.resolution === 'scoped_literal_candidate' ? 'Exact export candidate; runtime repository routing unverified.' : `${human(r.resolution_reason || r.resolution || 'resolution unknown')}; requires review.`}`), 'No literal pmcmd invocation captured. External callers remain unknown.');
    parent.append(calls);
    heading(parent, 'Keep external explicitly', 'h4'); paragraph(parent, contract.keep_external_explicitly);
    heading(parent, 'Coverage blockers and human questions', 'h4');
    list(parent, [...array(contract.blockers), ...array(contract.human_questions)]);
    if (contract.potential_data_dependencies.total) paragraph(parent, `${contract.potential_data_dependencies.total} potential exact-identity data/shared-write relationships. Not enforced dependencies; inspect technical details and confirm bindings.`);
    for (const key of ['cohort', 'referenced_children', 'invocations', 'outside_references', 'cycles', 'potential_data_dependencies']) {
      const page = contract[key];
      if (page.truncated) paragraph(parent, `${human(key)}: showing ${page.items.length} of ${page.total}, offset ${page.offset}. The review is partial; retrieve remaining pages before claiming coverage.`, 'small muted');
    }
    if (loadPage) {
      const offset = contract.cohort.offset, limit = contract.cohort.limit;
      if (offset > 0) parent.append(action('Previous package-context page', () => loadPage(Math.max(0, offset - limit))));
      if (['cohort', 'referenced_children', 'invocations', 'outside_references', 'cycles', 'potential_data_dependencies'].some(k => contract[k].has_more)
          || contract.sql_dependencies?.has_more || contract.reference_diagnostics?.references?.has_more
          || contract.reference_diagnostics?.connections?.has_more)
        parent.append(action('Read next package-context page', () => loadPage(offset + limit)));
    }
    jsonDetails(parent, 'Technical package contract, pagination and source details', contract);
  }
  // One presentation for Explorer and Proposals, scoped requests remain owned by each view.
  window.renderPackageMigrationContext = renderPackageMigrationContext;
  async function loadPackageMigrationContext(parent, workflowId, ticket, offset = 0) {
    const epoch = state.epoch;
    message(parent, 'Loading package invocation context. No child data or model calls.');
    try {
      const contract = await api(`/api/workflows/${encodeURIComponent(workflowId)}/migration-context?offset=${offset}`);
      if (epoch !== state.epoch || ticket !== state.objectTicket || !parent.isConnected) return;
      renderPackageMigrationContext(parent, contract, next => loadPackageMigrationContext(parent, workflowId, ticket, next));
    } catch (error) {
      if (epoch === state.epoch && ticket === state.objectTicket && parent.isConnected)
        message(parent, `Package context unavailable: ${error.message}. Closure and risk remain unassessed.`);
    }
  }
  function renderObject() {
    const model = state.object;
    $('download-object').disabled = !assessmentReady() || !model;
    $('load-impact').disabled = !assessmentReady() || model?.node?.kind !== 'workflow';
    if (!model) {
      if (state.objectLoadState === 'loading') {
        $('object-title').textContent = 'Loading selected object…';
        $('object-title').title = state.selectedObjectId || '';
        $('object-kind-label').textContent = 'LOADING SELECTED OBJECT';
        $('object-summary').textContent = 'Reading the selected package object. Its availability is not yet known.';
        ['graph-area', 'english-content', 'fields-content', 'object-diagnostics', 'inspector-content'].forEach((id) => message($(id), 'Selected object is loading.'));
        $('object-json').textContent = 'Selected object is loading.';
        return;
      }
      $('object-title').textContent = 'Your pipeline, in context';
      $('object-title').title = '';
      $('object-kind-label').textContent = 'SELECT AN OBJECT';
      $('object-summary').textContent = 'Select a workflow, mapping, source, or target to inspect its actual structure.';
      ['graph-area', 'english-content', 'fields-content', 'object-diagnostics', 'inspector-content'].forEach((id) => message($(id), 'No object selected.'));
      $('object-json').textContent = 'No object selected.';
      return;
    }
    $('object-title').textContent = model.node.name;
    $('object-title').title = `${model.node.name} | ${model.node.id}`;
    $('object-kind-label').textContent = human(model.node.kind).toUpperCase();
    $('object-summary').textContent = `${human(model.node.kind)} in ${[model.node.scope?.repository, model.node.scope?.folder].filter(Boolean).join(' / ') || 'captured package scope'}. Use Graph for relationships, Fields & XML details for the selected node's captured schemas, English Description for behavior, and JSON Definition for the loaded object.`;
    $('object-json').textContent = json(model);
    clear($('english-content'));
    if (model.node.kind === 'workflow') {
      const overview = array(model.behavior_explanation?.sections).find(section => section.title === 'What this workflow does');
      if (overview) {
        heading($('english-content'), overview.title, 'h3');
        list($('english-content'), array(overview.items));
      }
      const packageContext = el('section', 'disclosure'); packageContext.id = 'explorer-package-context';
      packageContext.setAttribute('aria-live', 'polite');
      $('english-content').append(packageContext);
      loadPackageMigrationContext(packageContext, model.node.id, state.objectTicket);
      heading($('english-content'), 'Recorded agent assessment', 'h3');
      const assessment = el('section', 'disclosure'); assessment.id = 'workflow-agent-assessment';
      assessment.setAttribute('aria-live', 'polite');
      assessment.textContent = 'Checking recorded assessments. Opening this tab does not invoke a model.';
      $('english-content').append(assessment);
      renderFixtureMetadata($('english-content'), model.node.id);
    }
    heading($('english-content'), 'XML-derived behavior', 'h3');
    if (model.behavior_explanation) {
      paragraph($('english-content'), 'Workflow behavior derived from the captured source XML. This same explanation is available to the runtime agents through their workflow_explanation tool.', 'small muted');
      for (const section of array(model.behavior_explanation.sections)) {
        if (section.title === 'What this workflow does') continue;
        heading($('english-content'), section.title, 'h4');
        const bullets = el('ul');
        for (const item of array(section.items)) {
          const entry = el('li'); highlightedText(entry, text(item)); bullets.append(entry);
        }
        if (!bullets.children.length) paragraph($('english-content'), 'No matching records captured in this inspected scope.', 'small muted');
        else $('english-content').append(bullets);
      }
      if (model.behavior_explanation.coverage?.truncated) paragraph($('english-content'), 'Explanation is bounded. Additional records require further retrieval; this is not complete semantic coverage.', 'notice');
      jsonDetails($('english-content'), 'Explanation coverage and source reference IDs', model.behavior_explanation);
    } else if (model.node.kind === 'workflow') {
      paragraph($('english-content'), 'A workflow behavior explanation is unavailable for this selection. Review the XML details and relationships for the captured source information.', 'notice');
    } else {
      heading($('english-content'), 'Captured object inventory', 'h4');
      const facts = el('div', 'inventory-facts');
      fact(facts, 'Captured fields (bounded detail)', array(model.node.fields).length);
      fact(facts, 'Captured properties (bounded detail)', array(model.node.properties).length);
      $('english-content').append(facts);
      paragraph($('english-content'), 'These are captured object attributes, not an agent interpretation. Referenced mappings and task links carry the workflow behavior.', 'small muted');
      const propertyRows = array(model.node.properties).map((property) => {
        const attrs = property.attributes ?? {};
        return { property: attrs.NAME ?? property.kind ?? 'Property', value: attrs.VALUE ?? 'No value captured' };
      });
      if (propertyRows.length) {
        const propertyDetails = el('details');
        propertyDetails.append(el('summary', '', 'Captured configuration properties'));
        const table = el('table');
        const head = el('tr'); head.append(el('th', '', 'Property'), el('th', '', 'Value')); table.append(head);
        for (const property of propertyRows) {
          const row = el('tr'); row.append(el('td', '', property.property), el('td', '', text(property.value))); table.append(row);
        }
        propertyDetails.append(table); $('english-content').append(propertyDetails);
      }
    }
    paragraph($('english-content'), 'This explanation describes the same canonical object shown in the graph and JSON. Structural references do not establish lifecycle, runtime execution, or semantic equivalence.');
    renderGraph($('graph-area'), model.graph, {
      total: model.graph_total_nodes, truncated: model.graph_truncated, selected: model.node.id,
      onSelect: (node) => { state.selectedNode = node; renderInspector(); renderFields(); selectView('fields', true); },
    });
    renderInspector();
    renderFields();
    clear($('object-diagnostics'));
    list($('object-diagnostics'), array(model.diagnostics), 'No scoped diagnostics returned by the service. This does not imply semantic verification.');
  }
  function renderInspector() {
    clear($('inspector-content'));
    const node = state.selectedNode;
    if (!node) return;
    rows($('inspector-content'), { name: node.name, id: node.id, kind: node.kind, scope: node.scope, assessment: assessmentPresentation(node).summary, attributes: node.attributes, evidence: node.evidence }, key => key === 'evidence' ? 'Source details' : human(key));
  }
  function renderFields() {
    clear($('fields-content'));
    const node = state.selectedNode ?? state.object?.node;
    if (!node) return;
    heading($('fields-content'), `Selected node: ${node.name ?? node.id}`);
    paragraph($('fields-content'), 'Select a graph node to inspect another object. Raw field attributes and locators are preserved; they are not a verified translation.');
    const fields = array(node.fields);
    paragraph($('fields-content'), `Showing ${fields.length} of ${node.fields_total ?? fields.length} fields${node.fields_truncated ? '. Bounded response; full records are in the compressed package export.' : '.'}`, 'small muted');
    if (!fields.length) paragraph($('fields-content'), 'No fields were returned for this node.');
    else {
      const scroll = el('div', 'table-scroll');
      const table = el('table');
      const caption = el('caption', 'small muted', 'Canonical field metadata. Full records follow below.');
      const head = el('thead');
      const row = el('tr');
      ['Field', 'Type', 'Kind', 'Source locator'].forEach((name) => row.append(el('th', '', name)));
      head.append(row);
      const body = el('tbody');
      for (const field of fields) {
        const tr = el('tr');
        const attrs = field.attributes ?? {};
        [attrs.NAME ?? field.name, attrs.DATATYPE ?? field.type, field.kind, field.evidence?.locator].forEach((value) => tr.append(title(el('td', '', text(value)), value)));
        body.append(tr);
      }
      table.append(caption, head, body);
      scroll.append(table);
      $('fields-content').append(scroll);
      jsonDetails($('fields-content'), 'Returned fields and source details (bounded snapshot)', fields);
    }
    jsonDetails($('fields-content'), 'Properties and source details', { properties: node.properties ?? [], evidence: node.evidence });
    const children = array(state.object?.children);
    jsonDetails($('fields-content'), `Object children (${children.length} of ${state.object?.children_total ?? children.length} returned${state.object?.children_truncated ? ', truncated' : ''})`, children);
  }

  // SCC condensation gives stable layered positions even when raw references cycle.
  // This is a layout only: it never adds, removes, or claims to optimize model edges.
  function layeredPositions(nodes, edges) {
    const ids = new Set(nodes.map((node) => node.id));
    const outgoing = new Map(nodes.map((node) => [node.id, []]));
    edges.forEach((edge) => { if (ids.has(edge.source) && ids.has(edge.target)) outgoing.get(edge.source).push(edge.target); });
    outgoing.forEach((targets) => targets.sort());
    let index = 0;
    const stack = [], onStack = new Set(), indices = new Map(), low = new Map(), components = [];
    function visit(id) {
      indices.set(id, index); low.set(id, index); index += 1;
      stack.push(id); onStack.add(id);
      for (const target of outgoing.get(id)) {
        if (!indices.has(target)) { visit(target); low.set(id, Math.min(low.get(id), low.get(target))); }
        else if (onStack.has(target)) low.set(id, Math.min(low.get(id), indices.get(target)));
      }
      if (low.get(id) === indices.get(id)) {
        const members = [];
        let current;
        do { current = stack.pop(); onStack.delete(current); members.push(current); } while (current !== id);
        components.push(members.sort());
      }
    }
    [...ids].sort().forEach((id) => { if (!indices.has(id)) visit(id); });
    const componentOf = new Map();
    components.forEach((members, i) => members.forEach((id) => componentOf.set(id, i)));
    const successors = components.map(() => new Set()), degree = components.map(() => 0), layers = components.map(() => 0);
    edges.forEach((edge) => {
      const a = componentOf.get(edge.source), b = componentOf.get(edge.target);
      if (a !== undefined && b !== undefined && a !== b && !successors[a].has(b)) { successors[a].add(b); degree[b] += 1; }
    });
    const queue = degree.flatMap((value, i) => value === 0 ? [i] : []);
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      [...successors[current]].sort((a, b) => a - b).forEach((next) => {
        layers[next] = Math.max(layers[next], layers[current] + 1);
        degree[next] -= 1;
        if (degree[next] === 0) queue.push(next);
      });
    }
    const columns = new Map();
    for (const node of nodes) {
      const supplied = node.attributes?.layer ?? node.layer;
      const layer = Number.isInteger(supplied) && supplied >= 0 && supplied < 100 ? supplied : layers[componentOf.get(node.id)];
      if (!columns.has(layer)) columns.set(layer, []);
      columns.get(layer).push(node);
    }
    const positions = new Map();
    [...columns.keys()].sort((a, b) => a - b).forEach((layer, column) => {
      columns.get(layer).sort((a, b) => String(a.name ?? a.id).localeCompare(String(b.name ?? b.id), 'en') || String(a.id).localeCompare(String(b.id), 'en'))
        .forEach((node, row) => positions.set(node.id, { x: 35 + column * 270, y: 35 + row * 100 }));
    });
    return positions;
  }
  let graphSequence = 0;
  function graphComponent(node) {
    const taskTypes = {
      notebook_task: 'Notebook task', pipeline_task: 'Lakeflow pipeline task',
      sql_task: 'SQL task', python_wheel_task: 'Python wheel task',
      spark_python_task: 'Python script task', spark_jar_task: 'JAR task',
      run_job_task: 'Run Job task', condition_task: 'If / else task',
      for_each_task: 'For each task', dbt_task: 'dbt task',
    };
    const task = node.task ?? node;
    const explicit = Object.keys(taskTypes).find((key) => Object.hasOwn(task, key));
    if (explicit) return { label: taskTypes[explicit], platform: 'Databricks', symbol: explicit === 'notebook_task' ? 'file' : 'graph' };
    const attrs = node.attributes ?? {};
    const kind = String(node.kind ?? 'object').toLowerCase();
    const type = attrs.TASKTYPE ?? attrs.TRANSFORMATION_TYPE ?? attrs.TYPE;
    const labels = { workflow: 'Workflow definition', taskinstance: type ? `${type} task instance` : 'Task instance',
      task: type ? `${type} task definition` : 'Task definition', session: 'Session definition',
      mapping: 'Mapping definition', source: 'Source definition', target: 'Target definition',
      transformation: type ? `${type} transformation` : 'Transformation', scheduler: 'Scheduler',
      instance: type ? `${type} instance` : 'Mapping instance', unresolved_reference: 'Unresolved reference' };
    return { label: labels[kind] ?? human(kind), platform: 'Informatica', symbol: kind === 'source' ? 'source' : kind === 'target' ? 'target' : kind === 'session' ? 'file' : 'graph' };
  }
  function renderGraph(host, graph, options = {}) {
    clear(host);
    const rawNodes = array(graph?.nodes);
    const nodes = options.full ? rawNodes : rawNodes.slice(0, 100);
    const ids = new Set(nodes.map((node) => node.id));
    const scopedEdges = array(graph?.edges).filter((edge) => ids.has(edge.source) && ids.has(edge.target));
    const edges = options.full ? scopedEdges : scopedEdges.slice(0, 2000);
    const total = typeof options.total === 'number' ? options.total : graph?.total_nodes ?? rawNodes.length;
    const toolbar = el('div', 'graph-toolbar');
    const bounded = options.truncated || graph?.truncated || total > nodes.length;
    toolbar.append(el('p', '', `Showing ${nodes.length} of ${total} nodes and ${edges.length} of ${graph?.page_total_edges ?? scopedEdges.length} in-view relationships.${bounded ? ' Additional source nodes or relationships are outside this response or view.' : ''}`));
    if (!options.full && (rawNodes.length > nodes.length || scopedEdges.length > edges.length)) {
      toolbar.append(action('Show all returned nodes and relationships', () => renderGraph(host, graph, {...options, full: true})));
    }
    host.append(toolbar);
    if (!nodes.length) { message(host, 'No graph nodes supplied by the service. A graph is not inferred from names.'); return; }
    const svgNS = 'http://www.w3.org/2000/svg';
    const svgEl = (tag, attributes = {}, content) => {
      const element = document.createElementNS(svgNS, tag);
      Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
      if (content !== undefined) element.textContent = text(content);
      return element;
    };
    const positions = layeredPositions(nodes, edges);
    const width = Math.max(600, ...[...positions.values()].map((p) => p.x + 255));
    const height = Math.max(260, ...[...positions.values()].map((p) => p.y + 105));
    const scroll = el('div', 'graph-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', 'Scrollable raw structure graph. Use Tab to select nodes, Enter to inspect, or arrow keys between nodes.');
    const svg = svgEl('svg', { class: 'graph-svg', width, height, viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': 'Canonical structure graph, not optimized or verified execution' });
    const markerId = `arrow-${++graphSequence}`;
    const defs = svgEl('defs');
    const marker = svgEl('marker', { id: markerId, markerWidth: 7, markerHeight: 7, refX: 6, refY: 3, orient: 'auto', markerUnits: 'strokeWidth' });
    marker.append(svgEl('path', { d: 'M0,0 L6,3 L0,6', fill: 'none', stroke: 'var(--muted)' }));
    defs.append(marker); svg.append(defs);
    for (const edge of edges) {
      const a = positions.get(edge.source), b = positions.get(edge.target);
      const x1 = a.x + 215, y1 = a.y + 33, x2 = b.x, y2 = b.y + 33;
      const curve = edge.source === edge.target ? `M${x1},${y1} C${x1 + 75},${y1 - 75} ${a.x - 65},${y1 - 75} ${a.x},${y1}` : `M${x1},${y1} C${x1 + 35},${y1} ${x2 - 35},${y2} ${x2},${y2}`;
      const path = svgEl('path', { d: curve, class: 'edge', ...(edge.directed === false ? {} : {'marker-end': `url(#${markerId})`}) });
      path.append(svgEl('title', {}, `${edge.source} ${edge.directed === false ? '↔' : '→'} ${edge.target} | ${text(edge.label || edge.kind)} | ${text(edge.attributes)}`));
      svg.append(path);
    }
    const nodeElements = [];
    nodes.forEach((node, i) => {
      const position = positions.get(node.id);
      const {status, reason} = assessmentPresentation(node);
      const group = svgEl('g', { class: `node status-${status}${options.selected === node.id ? ' selected' : ''}`, transform: `translate(${position.x},${position.y})`, role: 'button', tabindex: 0, 'aria-label': `${human(node.kind ?? 'object')}: ${node.name ?? node.id}. ${status}: ${reason}. Inspect details.`, 'aria-pressed': options.selected === node.id ? 'true' : 'false', 'data-node-id': node.id, 'data-assessment-status': status });
      group.append(svgEl('title', {}, `${text(node.name)} | ${node.id} | ${status}: ${reason} | basis: ${text(node.assessment?.basis)} | source references: ${text(node.assessment?.evidence_ids)} | ${text(node.attributes)}`));
      group.append(svgEl('rect', { width: 215, height: 66, rx: 9 }));
      const component = graphComponent(node);
      const componentIcon = icon(component.symbol);
      componentIcon.setAttribute('x', '12'); componentIcon.setAttribute('y', '12');
      componentIcon.setAttribute('width', '16'); componentIcon.setAttribute('height', '16');
      componentIcon.setAttribute('class', 'component-icon');
      group.append(componentIcon);
      const name = String(node.name ?? node.id);
      group.append(svgEl('text', { x: 36, y: 25 }, name.length > 23 ? `${name.slice(0, 22)}…` : name));
      const typeLabel = component.label.length > 30 ? `${component.label.slice(0, 29)}…` : component.label;
      const componentText = svgEl('text', { x: 14, y: 47, class: 'node-kind' }, typeLabel);
      componentText.append(svgEl('title', {}, `${component.platform}: ${component.label}`));
      group.append(componentText);
      if (status !== 'neutral') group.append(svgEl('text', { x: 8, y: 84, class: 'node-platform' }, status === 'amber' ? 'Review needed' : status === 'red' ? 'Issue recorded' : 'Scoped checks passed'));
      function select() {
        nodeElements.forEach((element) => { element.classList.remove('selected'); element.setAttribute('aria-pressed', 'false'); });
        group.classList.add('selected'); group.setAttribute('aria-pressed', 'true');
        if (options.onSelect) options.onSelect(node);
      }
      group.addEventListener('click', select);
      group.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); }
        let next;
        if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = (i + 1) % nodes.length;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (i + nodes.length - 1) % nodes.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = nodes.length - 1;
        if (next !== undefined) { event.preventDefault(); nodeElements[next].focus(); }
      });
      nodeElements.push(group); svg.append(group);
    });
    let zoom = 1;
    const setZoom = (value) => { zoom = Math.max(.25, Math.min(2, value)); svg.setAttribute('width', String(width * zoom)); svg.setAttribute('height', String(height * zoom)); };
    const zoomOut = action('−', () => setZoom(zoom - .25)), zoomIn = action('+', () => setZoom(zoom + .25));
    zoomOut.setAttribute('aria-label', 'Zoom graph out'); zoomIn.setAttribute('aria-label', 'Zoom graph in');
    toolbar.append(zoomOut, zoomIn, action('Reset', () => { setZoom(1); scroll.scrollTo(0, 0); }));
    scroll.append(svg); host.append(scroll);
    host.append(el('p', 'graph-legend', 'Raw canonical references · deterministic layered layout · arrows preserve supplied direction. Cycles remain visible. Select a node for full identifiers and source details.'));
    host.append(el('p', 'graph-legend', 'Source structure from XML is not a correctness assessment. Agent review checks transformation correctness, dependency semantics and validation coverage. No completed review is recorded for neutral nodes; this does not mean broken, disabled or failed.'));
    const controlLinks = edges.filter((edge) => edge.kind === 'workflow_control');
    host.append(el('p', 'graph-legend', controlLinks.length
      ? 'Execution sequence: workflow_control arrows capture original task ordering and branch conditions. Other arrows may represent containment, references, or data flow, not execution order. Parallel safety and cross-workflow scheduling are not established by this view.'
      : 'Execution sequence: no workflow_control links are present in this displayed graph. Package relationships and shared definitions do not by themselves establish workflow run order.'));
    if (!options.onSelect) {
      const inspector = el('div', 'detail-list');
      host.append(inspector);
      nodes.forEach((node, i) => nodeElements[i].addEventListener('click', () => { clear(inspector); rows(inspector, node); }));
      nodes.forEach((node, i) => nodeElements[i].addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { clear(inspector); rows(inspector, node); } }));
    }
  }

  $('load-impact').addEventListener('click', async () => {
    const model = state.object;
    if (model?.node?.kind !== 'workflow') return;
    const ticket = state.objectTicket;
    $('load-impact').disabled = true;
    message($('impact-content'), 'Loading impact from package information…');
    try {
      const impact = await api(`/api/impact/${encodeURIComponent(model.node.id)}`);
      if (ticket !== state.objectTicket) return;
      clear($('impact-content'));
      heading($('impact-content'), 'Workflow impact');
      rows($('impact-content'), { ...impact.counts, scoped_object_count: impact.scoped_object_count, total_related_workflows: impact.total_related_workflows, truncated: impact.truncated });
      list($('impact-content'), array(impact.limitations));
      for (const kind of ['direct', 'shared', 'transitive', 'unknown']) jsonDetails($('impact-content'), `${human(kind)}: ${count(impact.counts?.[kind])} total, up to ${text(impact.limit_per_category)} shown`, impact[kind] ?? []);
      jsonDetails($('impact-content'), 'Complete bounded impact response', impact);
    } catch (error) { if (ticket === state.objectTicket) message($('impact-content'), `Impact unavailable. ${error.message}`); }
    finally { if (ticket === state.objectTicket) $('load-impact').disabled = false; }
  });

  let proposalListFlight = null;
  function loadProposals(preferredId) {
    if (state.page === 'proposals' && !$('proposal-baseline').open) return Promise.resolve();
    const scope = json([state.epoch, $('scenario').value]);
    const key = json([state.epoch, $('scenario').value, state.proposalOffset,
      document.getElementById('redesign-workflow')?.value, preferredId]);
    // While a list is pending, retain one owner in this context. On completion
    // readProposals reconciles the latest selection, not every intermediate one.
    if (proposalListFlight?.scope === scope && proposalListFlight.readingList) return proposalListFlight.promise;
    if (proposalListFlight?.key === key) return proposalListFlight.promise;
    const flight = {key, scope, readingList: true, promise: null}; proposalListFlight = flight;
    flight.promise = readProposals(preferredId, flight).finally(() => { if (proposalListFlight === flight) proposalListFlight = null; });
    return flight.promise;
  }
  async function readProposals(preferredId, flight) {
    const ticket = ++state.proposalTicket;
    if (!state.ready) return;
    const epoch = state.epoch, scenario = $('scenario').value;
    const selectedId = preferredId ?? state.proposal?.id;
    state.proposalsLoaded = false;
    state.proposal = null;
    message($('proposal-list'), 'Loading proposals…');
    message($('proposal-detail'), 'Loading current proposal candidates.');
    $('proposals-prev').disabled = true;
    $('proposals-next').disabled = true;
    const workflowId = document.getElementById('redesign-workflow')?.value;
    try {
      const params = new URLSearchParams({ scenario, offset: state.proposalOffset, limit: state.proposalPageSize });
      if (workflowId) { params.set('workflow_id', workflowId); params.set('offset', '0'); }
      const data = await api(`/api/proposals?${params}`);
      flight.readingList = false;
      if (ticket !== state.proposalTicket || epoch !== state.epoch || scenario !== $('scenario').value || !state.ready) return;
      // Re-read selection before applying this paged, workflow-filtered response.
      if (workflowId !== document.getElementById('redesign-workflow')?.value) {
        if (proposalListFlight === flight) proposalListFlight = null;
        return loadProposals();
      }
      state.proposals = array(data.items).filter(proposal => proposal.scenario === scenario && (!workflowId || proposal.workflow_id === workflowId));
      state.proposalTotal = Number.isSafeInteger(data.total) ? data.total : state.proposals.length;
      state.proposalsLoaded = true;
      renderProposalList();
      const selected = state.proposals.find(proposal => proposal.id === selectedId)
        ?? (workflowId ? state.proposals.find(proposal => proposal.workflow_id === workflowId) : null);
      if (selected) await loadProposalDetail(selected, ticket, epoch, scenario);
      else { state.proposal = null; renderProposal(); }
      renderReleaseBlockers();
    } catch (error) {
      flight.readingList = false;
      if (ticket !== state.proposalTicket || epoch !== state.epoch || scenario !== $('scenario').value || !state.ready) return;
      if (workflowId !== document.getElementById('redesign-workflow')?.value) {
        if (proposalListFlight === flight) proposalListFlight = null;
        return loadProposals();
      }
      state.proposalsLoaded = false;
      state.proposal = null;
      state.proposals = [];
      state.proposalTotal = 0;
      message($('proposal-detail'), 'Current proposal data is unavailable. Refresh before reviewing a version.');
      message($('proposal-list'), `Proposals unavailable. ${error.message}`);
      message($('release-blockers'), `Current proposal gates unavailable. ${error.message}`);
      $('proposal-count').textContent = 'Could not retrieve proposal counts.';
    }
  }
  async function loadProposalDetail(summary, ticket, epoch, scenario) {
    state.proposal = summary;
    all('[data-proposal-id]', $('proposal-list')).forEach((button) => {
      const selected = button.dataset.proposalId === summary.id;
      button.classList.toggle('selected', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    message($('proposal-detail'), 'Loading structural baseline evidence and graph, not the saved agent design…');
    try {
      const detail = await api(`/api/proposals/${encodeURIComponent(summary.id)}?${new URLSearchParams({ scenario })}`);
      if (ticket !== state.proposalTicket || epoch !== state.epoch || scenario !== $('scenario').value) return;
      state.proposal = detail;
      renderProposal();
    } catch (error) {
      if (ticket === state.proposalTicket && epoch === state.epoch && scenario === $('scenario').value) {
        state.proposal = null;
        renderProposalList();
        message($('proposal-detail'), `Selected proposal details unavailable. ${error.message}`);
      }
    }
  }
  function selectProposalWorkflow(workflowId) {
    const waitingList = proposalListFlight?.readingList
      && proposalListFlight.scope === json([state.epoch, $('scenario').value]);
    if (!waitingList) state.proposalTicket += 1;
    state.proposalOffset = 0;
    state.proposal = null;
    state.proposalsLoaded = false;
    renderProposal();
    if (state.ready && $('proposal-baseline').open) loadProposals();
  }
  $('proposal-baseline').addEventListener('toggle', () => {
    if ($('proposal-baseline').open && state.ready && !state.proposalsLoaded) loadProposals();
  });
  // The redesign select is mounted after this script. Native change bubbles here;
  // rendering the same workflow below does not emit another change or list read.
  document.addEventListener('change', (event) => {
    if (event.target.id === 'agent-review-scope') { updateControls(); return; }
    if (event.target.id !== 'redesign-workflow') return;
    selectProposalWorkflow(event.target.value);
  });
  function renderProposalList() {
    const proposals = state.proposals;
    clear($('proposal-list'));
    $('proposal-count').textContent = `${proposals.length} of ${state.proposalTotal} actual candidates · ${$('scenario').selectedOptions[0].textContent}. Showing up to ${state.proposalPageSize} per page.`;
    if (!proposals.length) message($('proposal-list'), 'No proposals returned for this scenario.');
    for (const proposal of proposals) {
      const button = action('', () => {
        if (state.proposal?.id === proposal.id) return;
        document.dispatchEvent(new CustomEvent('orion:proposal-selected', { detail: { workflowId: proposal.workflow_id } }));
        const ticket = ++state.proposalTicket;
        loadProposalDetail(proposal, ticket, state.epoch, $('scenario').value);
        renderProposalList();
      }, 'object-item');
      button.dataset.proposalId = proposal.id;
      button.append(title(el('span', 'truncate', proposal.name), `${proposal.name} | ${proposal.id}`), el('small', '', `v${proposal.version} · ${human(proposal.status)} · ${array(proposal.blockers).length} blockers`));
      button.classList.toggle('selected', state.proposal?.id === proposal.id);
      button.setAttribute('aria-pressed', String(state.proposal?.id === proposal.id));
      $('proposal-list').append(button);
    }
    $('proposals-prev').disabled = state.proposalOffset === 0;
    $('proposals-next').disabled = state.proposalOffset + state.proposalPageSize >= state.proposalTotal;
  }
  $('proposals-prev').addEventListener('click', () => { state.proposalOffset = Math.max(0, state.proposalOffset - state.proposalPageSize); loadProposals(); });
  $('proposals-next').addEventListener('click', () => { state.proposalOffset += state.proposalPageSize; loadProposals(); });
  $('scenario').addEventListener('change', () => {
    state.proposal = null;
    state.proposalsLoaded = false;
    state.proposalOffset = 0;
    message($('proposal-detail'), 'Loading the selected scenario.');
    if ($('proposal-baseline').open || state.page === 'release') loadProposals();
  });
  function renderProposal() {
    const host = clear($('proposal-detail'));
    const proposal = state.proposal;
    if (!proposal) {
      const workflowId = document.getElementById('redesign-workflow')?.value;
      message(host, workflowId
        ? state.ready && state.proposalsLoaded
          ? `No structural candidate is available for workflow ${workflowId} in the selected scenario.`
          : 'Current proposal data is unavailable or still loading. Refresh before reviewing a version.'
        : 'Select a proposal to review rationale, evidence, and gates.');
      return;
    }
    host.append(el('span', 'eyebrow accent', 'CANDIDATE ONLY · NOT AN EXECUTABLE PLAN'));
    const name = title(el('h2', 'truncate', proposal.name), proposal.name);
    const proposalStatus = semanticStatus(proposal.status);
    proposalStatus.append(el('span', '', ` · version ${text(proposal.version)}`));
    host.append(name, proposalStatus);
    const reviewSummary = el('div', 'proposal-step-summary');
    const stepLabel = el('span', 'small', 'Review candidate → inspect evidence → record feedback');
    const risk = el('span', 'risk-count');
    risk.append(icon('warning'), el('strong', 'stat-emphasis', count(array(proposal.blockers).length)), document.createTextNode(array(proposal.blockers).length === 1 ? ' blocker' : ' blockers'));
    reviewSummary.append(stepLabel, risk); host.append(reviewSummary);
    paragraph(host, proposal.summary ?? 'No proposal summary supplied.', 'prose section-gap');
    rows(host, { proposal_id: proposal.id, workflow_id: proposal.workflow_id, scenario: proposal.scenario, generation_allowed: proposal.generation_allowed, deployment_allowed: proposal.deployment_allowed });
    const blockers = array(proposal.blockers);
    const blockerBox = el('div', 'blocker-box');
    const blockerHeading = el('h4', 'section-title');
    blockerHeading.append(icon('warning'), el('strong', 'stat-emphasis', count(blockers.length)), document.createTextNode(blockers.length === 1 ? ' actual blocker' : ' actual blockers'));
    blockerBox.append(blockerHeading);
    list(blockerBox, blockers, 'No blockers returned. This alone does not grant execution authority.');
    host.append(blockerBox);
    heading(host, 'Why this boundary?');
    paragraph(host, proposal.rationale ?? 'No rationale supplied.', 'prose');
    jsonDetails(host, 'Alternatives and their evidence', proposal.alternatives ?? []);
    jsonDetails(host, 'Sources, suggested names, and actual binding states', proposal.sources ?? []);
    jsonDetails(host, 'Targets, suggested names, and actual binding states', proposal.targets ?? []);
    jsonDetails(host, 'Coverage and evidence locators', { coverage: proposal.coverage, evidence: proposal.evidence });
    const comparison = el('div', 'comparison');
    const original = el('div', 'min-width-zero'), proposed = el('div', 'min-width-zero');
    heading(original, 'Original pipeline');
    paragraph(original, 'Informatica definitions and task instances. These are not Databricks task types.', 'small muted');
    const originalGraph = el('div', 'graph-host');
    original.append(originalGraph);
    message(originalGraph, 'Loading the original workflow object…');
    heading(proposed, proposal.proposed_graph ? 'Agent-proposed candidate, pending review' : 'Stored source-boundary baseline');
    paragraph(proposed, proposal.proposed_graph ? 'Proposed components show Databricks task types only where explicit task definitions are supplied. This is not a verified or approved best design.' : 'This historical structural proposal retains source boundaries. It is not the generated Databricks Job and does not describe artifact-generation or job-run status. Inspect the separate workflow YAML and notebook section for the generated native task graph. Generating a bundle does not overwrite this proposal or prove agent optimization.', 'small muted');
    if (!proposal.proposed_graph) {
      const generatedLink = el('a', 'small', 'View generated Databricks workflow and notebook artifacts');
      generatedLink.href = '#workflow-artifacts';
      generatedLink.addEventListener('click', (event) => {
        event.preventDefault(); document.getElementById('workflow-artifacts')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
      proposed.append(generatedLink);
    }
    const proposedGraph = el('div', 'graph-host');
    proposed.append(proposedGraph);
    renderGraph(proposedGraph, proposal.proposed_graph ?? proposal.graph);
    comparison.append(original, proposed); host.append(comparison);
    const epoch = state.epoch;
    api(`/api/object/${encodeURIComponent(proposal.workflow_id)}`).then((model) => {
      if (epoch !== state.epoch || state.proposal !== proposal || !originalGraph.isConnected) return;
      renderGraph(originalGraph, model.graph, { total: model.graph_total_nodes, truncated: model.graph_truncated });
    }).catch((error) => { if (originalGraph.isConnected) message(originalGraph, `Original structure unavailable. ${error.message}`); });
    heading(host, 'Review this version');
    paragraph(host, 'Reviews record comments and revision requests. To produce a new agent design, start a separate agent run; it will not automatically change this draft. Execution and deployment require separate approval.', 'small muted');
    const form = el('form', 'stack');
    const commentLabel = el('label', '', 'Review comment'); commentLabel.htmlFor = 'review-comment';
    const comment = el('textarea'); comment.id = 'review-comment'; comment.rows = 3; comment.required = true; comment.maxLength = 12000;
    const anchorLabel = el('label', '', 'Evidence anchor (optional object ID or locator)'); anchorLabel.htmlFor = 'review-anchor';
    const anchor = el('input'); anchor.id = 'review-anchor'; anchor.maxLength = 2048;
    const reviewActions = el('div', 'review-actions');
    const status = el('p', 'form-status small'); status.setAttribute('role', 'status');
    for (const [value, label] of [['comment', 'Comment'], ['reject', 'Reject'], ['request_changes', 'Request changes'], ['request_rework', 'Request rework']]) {
      const button = el('button', 'secondary', label);
      button.type = 'submit'; button.value = value; button.name = 'action';
      reviewActions.append(button);
    }
    form.append(commentLabel, comment, anchorLabel, anchor, reviewActions, status);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const reviewAction = event.submitter?.value;
      if (!reviewAction || state.proposal !== proposal || !state.ready) return;
      all('button', form).forEach((button) => { button.disabled = true; });
      formStatus(form, 'Saving a version-checked review…');
      try {
        const result = await post('/api/reviews', { proposal_id: proposal.id, expected_version: proposal.version, action: reviewAction, comment: comment.value, ...(anchor.value.trim() ? { anchor: anchor.value.trim() } : {}) });
        if (epoch !== state.epoch) return;
        formStatus(form, reviewAction === 'request_rework' ? 'Draft revision request stored. Rework was not executed. Separate live agent runs are advisory and do not apply draft graph changes.' : 'Review saved by the service.', 'success');
        notify(reviewAction === 'request_rework' ? 'Rework requested, not executed. The stored review is not an agent result.' : 'Version-checked review saved.', 'success');
        if (result && result.id === proposal.id && typeof result.version === 'number') {
          state.proposals = state.proposals.map((item) => item.id === result.id ? result : item);
          state.proposal = result;
        }
        await loadProposals(proposal.id);
      } catch (error) {
        if (epoch !== state.epoch) return;
        formStatus(form, `Review not confirmed. ${error.message} Refresh proposals before retrying if the version changed.`, 'error');
        all('button', form).forEach((button) => { button.disabled = false; });
      }
    });
    host.append(form);
    const links = el('div', 'button-row');
    links.append(action('Open workflow', () => { navigate('explorer', true); selectObject(proposal.workflow_id); }), action('Agent activity', () => { navigate('agents', true); $('agent-workflow').value = proposal.workflow_id; $('agent-scenario').value = proposal.scenario; }), action('Save proposal JSON', () => downloadJSON(proposal, 'proposal.json')));
    host.append(links);
    const history = el('div', 'section-gap');
    host.append(history);
    message(history, 'Loading persisted review history…');
    api(`/api/reviews?${new URLSearchParams({ proposal_id: proposal.id, limit: 100 })}`).then((data) => {
      if (epoch !== state.epoch || !history.isConnected) return;
      const reviews = array(data.items).filter((review) => review.proposal_id === proposal.id);
      clear(history); heading(history, 'Persisted review history');
      paragraph(history, `Showing ${reviews.length} of ${data.total ?? reviews.length} review events${data.truncated ? '. Older events are not included in this bounded view.' : '.'}`, 'small muted');
      if (!reviews.length) paragraph(history, 'No stored review events for this proposal.');
      reviews.forEach((review) => {
        const record = el('article', 'record review-card');
        const header = el('div', 'record-heading');
        header.append(el('strong', '', human(review.action)), semanticStatus(review.action === 'reject' ? 'rejected' : review.action === 'request_rework' ? 'draft' : review.status));
        record.append(header, el('small', 'record-time', `v${text(review.version)} · ${text(review.created_at)}`));
        if (review.comment != null) paragraph(record, review.comment, 'prose');
        jsonDetails(record, 'Complete review record', review); history.append(record);
      });
    }).catch((error) => { if (history.isConnected) message(history, `Review history unavailable. ${error.message}`); });
  }
  function renderReleaseBlockers() {
    clear($('release-blockers'));
    if (!state.proposalsLoaded) { paragraph($('release-blockers'), 'Current proposal gates have not been loaded.'); return; }
    if (!state.proposals.length) { paragraph($('release-blockers'), 'No proposals returned for the selected scenario. This is not a release approval.'); return; }
    paragraph($('release-blockers'), `Scenario: ${human($('scenario').value)}. ${state.proposals.length} structural proposals. Exact reviewed native bundles can be exported separately below. GitHub publication, general deployment and source-output parity are not established.`);
    const unique = [...new Set(state.proposals.flatMap((proposal) => array(proposal.blockers).map(text)))];
    list($('release-blockers'), unique, 'No proposal blockers returned. Adapter and verification gates still apply.');
    jsonDetails($('release-blockers'), 'Per-proposal gate states', state.proposals.map((proposal) => ({ id: proposal.id, name: proposal.name, version: proposal.version, blockers: proposal.blockers, generation_allowed: proposal.generation_allowed, deployment_allowed: proposal.deployment_allowed })));
  }

  async function loadDocuments() {
    if (!state.ready) return;
    const epoch = state.epoch, ticket = ++state.documentsTicket;
    state.documentsLoading = true;
    if (!state.documentsLoaded) message($('documents-list'), 'Loading stored documents…');
    try {
      const data = await api('/api/documents');
      if (epoch !== state.epoch || ticket !== state.documentsTicket) return;
      if (!Array.isArray(data.items)) throw new Error('The service did not return a valid document list.');
      state.documentsLoaded = true;
      clear($('documents-list'));
      if (!array(data.items).length) message($('documents-list'), 'No supporting documents have been stored for this package.');
      for (const documentRecord of array(data.items)) {
        const record = el('article', 'record document-card');
        const header = el('div', 'record-heading');
        header.append(icon('file'), title(el('strong', 'record-title', documentRecord.filename ?? documentRecord.id), documentRecord.filename ?? documentRecord.id));
        const badge = pill('Unverified supporting context', 'caution'); badge.prepend(icon('info')); header.append(badge);
        record.append(header);
        paragraph(record, 'The API extracts document text. Extracted text is unverified supporting context, not semantic assertions or independent verification; it does not clear blockers.', 'small muted');
        jsonDetails(record, 'Document record and metadata', documentRecord);
        $('documents-list').append(record);
      }
      return true;
    } catch (error) { if (epoch === state.epoch && ticket === state.documentsTicket) {
      if (!state.documentsLoaded) message($('documents-list'), `Documents unavailable. ${error.message}`);
      else notify(`Documents refresh unavailable. ${error.message} Previously displayed documents remain visible.`, 'error');
    } } finally { if (ticket === state.documentsTicket) state.documentsLoading = false; }
  }
  function startInitialPagePreload() {
    if (!assessmentReady() || state.pagePreload !== 'not_started') return;
    const epoch = state.epoch, ticket = ++state.pagePreloadTicket;
    state.pagePreload = 'loading'; state.pagePreloadError = '';
    $('package-ready').textContent = 'Loading initial page data';
    const flight = (async () => {
      if (document.readyState === 'loading') await new Promise(resolve => document.addEventListener('DOMContentLoaded', resolve, {once: true}));
      if (epoch !== state.epoch || ticket !== state.pagePreloadTicket) return;
      if (!assessmentReady()) { state.pagePreload = 'not_started'; updateControls(); return; }
      const localReadiness = !state.hosted || !state.projectId || !state.memberHash;
      const scope = {projectId: state.projectId, packageHash: state.projectId ? state.memberHash : '',
        scenario: $('readiness-scenario').value, epoch: ++state.readinessEpoch};
      const results = await Promise.allSettled([
        loadDocuments(), loadSettings(), readRuns(0, true),
        window.orionConnections?.preload() ?? Promise.reject(new Error('Connections module unavailable.')),
        localReadiness ? (window.orionReadiness?.preload(scope) ?? Promise.reject(new Error('Readiness module unavailable.'))) : Promise.resolve({status: 'ready'})
      ]);
      if (epoch !== state.epoch || ticket !== state.pagePreloadTicket) return;
      const unavailable = [];
      if (results[0].status !== 'fulfilled' || results[0].value !== true) unavailable.push('documents');
      if (results[1].status !== 'fulfilled' || results[1].value !== true) unavailable.push('release settings');
      if (results[2].status !== 'fulfilled' || results[2].value !== true) unavailable.push('saved agent history');
      if (results[3].status !== 'fulfilled' || results[3].value !== true) unavailable.push('connections');
      if (results[4].status !== 'fulfilled' || results[4].value?.status !== 'ready') unavailable.push('readiness');
      state.pagePreloadError = unavailable.join(', ');
      state.pagePreload = unavailable.length ? 'failed' : 'finished';
      $('package-ready').textContent = unavailable.length ? 'Initial page reads partially unavailable' : 'Initial selected page reads complete';
      updateControls();
    })().catch(error => {
      if (epoch !== state.epoch || ticket !== state.pagePreloadTicket) return;
      state.pagePreloadError = `Initial page loading failed: ${error.message}`;
      state.pagePreload = 'failed';
      $('package-ready').textContent = 'Initial page data unavailable';
      updateControls();
    }).finally(() => { if (state.pagePreloadFlight === flight) state.pagePreloadFlight = null; });
    state.pagePreloadFlight = flight;
  }
  $('refresh-documents').addEventListener('click', loadDocuments);
  $('document-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.ready) return;
    const form = event.currentTarget, file = $('document-file').files[0], epoch = state.epoch;
    if (!file || !/\.(md|txt|pdf)$/i.test(file.name)) { formStatus(form, 'Only .md, .txt, and .pdf supporting documents are supported.', 'error'); return; }
    const body = new FormData(); body.append('file', file);
    if ($('document-workflow').value) body.append('workflow_id', $('document-workflow').value);
    if ($('document-description').value.trim()) body.append('description', $('document-description').value.trim());
    const button = form.querySelector('button'); button.disabled = true;
    formStatus(form, 'Uploading supporting document…');
    try {
      await api('/api/documents', { method: 'POST', body });
      if (epoch !== state.epoch) return;
      formStatus(form, 'Document stored. The API extracts text; extracted text is unverified, not semantic assertions or blocker clearance.', 'success');
      form.reset(); await loadDocuments();
    } catch (error) { if (epoch === state.epoch) formStatus(form, `Upload not confirmed. ${error.message}`, 'error'); }
    finally { button.disabled = !state.ready; }
  });
  function attestationSuggestions() {
    const suggestions = { lifecycle: ['active', 'inactive', 'retired', 'unknown'], migration_scope: ['in_scope', 'out_of_scope', 'deferred', 'unknown'], invocation: ['scheduled', 'manual', 'external', 'unknown'] };
    clear($('attestation-suggestions'));
    for (const value of suggestions[$('attestation-field').value]) { const option = el('option'); option.value = value; $('attestation-suggestions').append(option); }
  }
  attestationSuggestions();
  $('attestation-field').addEventListener('change', () => { $('attestation-value').value = ''; attestationSuggestions(); });
  $('attestation-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.ready) return;
    const form = event.currentTarget, epoch = state.epoch, button = form.querySelector('button');
    button.disabled = true; formStatus(form, 'Recording unverified owner statement…');
    try {
      const record = await post('/api/attestations', { workflow_id: $('attestation-workflow').value, field: $('attestation-field').value, value: $('attestation-value').value.trim(), comment: $('attestation-comment').value });
      if (epoch !== state.epoch) return;
      formStatus(form, 'Owner attestation stored as an unverified statement. Proposal blockers remain unchanged.', 'success');
      clear($('attestation-result')); jsonDetails($('attestation-result'), 'Stored attestation response', record, true);
    } catch (error) { if (epoch === state.epoch) formStatus(form, `Attestation not confirmed. ${error.message}`, 'error'); }
    finally { button.disabled = !state.ready; }
  });

  updateControls();
  $('agent-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    if (document.getElementById('agent-review-scope')?.value === 'cohort') {
      if (!assessmentReady() || !agentModelAvailable()
          || state.agentRunning || state.agentSubmitting || !state.agentRunsLoaded || !$('agent-workflow').value) {
        formStatus(form, 'A configured agent, selected workflow and confirmed idle run status are required.', 'error'); return;
      }
      document.dispatchEvent(new CustomEvent('orion:open-cohort-review', {detail: {
        workflow_id: $('agent-workflow').value, scenario: $('agent-scenario').value}}));
      return;
    }
    if (!assessmentReady() || !agentModelAvailable() || state.agentRunning || state.agentSubmitting || !state.agentRunsLoaded) { formStatus(form, 'A configured agent and confirmed idle run status are required.', 'error'); return; }
    const epoch = state.epoch;
    $('agent-submit').disabled = true;
    state.agentSubmitting = true;
    state.agentRunning = true;
    updateControls();
    formStatus(form, 'Submitting the explicitly requested agent run…');
    try {
      const run = await post('/api/agents/run', { workflow_id: $('agent-workflow').value, scenario: $('agent-scenario').value, prompt: $('agent-prompt').value, consent: true, generate_code: true });
      if (epoch !== state.epoch) return;
      if (!run.id) throw new Error('The service did not return a run ID. Refresh recorded runs before submitting again.');
      formStatus(form, `Run ${run.id} submitted. Actual status: ${text(run.status)}. Design and candidate-code drafting are part of this run; execution and deployment are not.`, 'success');
      state.submittedRunId = String(run.id);
      await loadRuns();
      if (epoch !== state.epoch) return;
      // The list can lag the accepted submission. Keep the slot locked until
      // this run is observed and a terminal observation triggers a fresh list.
      state.agentRunning = true; state.agentRunsLoaded = false;
      updateControls();
      selectRun(run.id);
    } catch (error) {
      if (epoch === state.epoch) {
        formStatus(form, `Run submission not confirmed. ${error.message} Checking persisted run status before enabling another invocation.`, 'error');
        await loadRuns();
      }
    }
    finally { if (epoch === state.epoch) { state.agentSubmitting = false; updateControls(); } }
  });
  let runsPending = null;
  function loadRuns(offset = 0) {
    if (runsPending?.epoch === state.epoch && runsPending.offset === offset) return runsPending.promise;
    const pending = { epoch: state.epoch, offset, promise: null };
    runsPending = pending;
    pending.promise = readRuns(offset).finally(() => { if (runsPending === pending) runsPending = null; });
    return pending.promise;
  }
  async function readRuns(offset, prefetch = false) {
    if (!assessmentReady()) return;
    const epoch = state.epoch, ticket = ++state.runsListTicket;
    if (!prefetch) { state.agentRunsLoaded = false; updateControls(); }
    try {
      const data = await api(`/api/agents/runs?status_only=true&offset=${offset}&limit=50`);
      if (epoch !== state.epoch || ticket !== state.runsListTicket) return;
      if (!Array.isArray(data.items) || data.run_status_verified !== true
          || typeof data.active_run_present !== 'boolean' || array(data.items).some(run =>
        !terminal.has(String(run.status).toLowerCase()) && !activeRunStatuses.has(String(run.status).toLowerCase())
        || (run.candidate_writer && !terminal.has(String(run.candidate_writer.status).toLowerCase())
          && !activeRunStatuses.has(String(run.candidate_writer.status).toLowerCase()))))
        throw new Error('Persisted run status is unknown.');
      // Older local servers do not expose a whole-history active indicator.
      // A truncated page without that indicator cannot establish an idle slot.
      if (!prefetch && data.truncated && typeof data.active_run_present !== 'boolean')
        throw new Error('Active-run status is unknown beyond this history page.');
      recordedRunStates.clear();
      array(data.items).forEach((run) => recordedRunStates.set(run.id, runState(run, epoch)));
      state.agentRunning = Boolean(state.submittedRunId) || data.active_run_present || array(data.items).some(activeRun);
      state.agentRunsLoaded = true;
      state.runsRowsLoaded = true;
      clear($('runs-list'));
      $('runs-list').dataset.offset = String(offset);
      if (!array(data.items).length) message($('runs-list'), 'No agent runs recorded. No AI activity has been fabricated.');
      for (const run of array(data.items)) {
        const button = action('', () => selectRun(run.id), 'object-item');
        button.dataset.runId = run.id;
        const header = el('span', 'record-heading');
        const workflowLabel = run.workflow_name || run.name
          || [...$('agent-workflow').options].find(option => option.value === run.workflow_id)?.textContent
          || 'Workflow name unavailable';
        header.append(title(el('strong', 'record-title', workflowLabel), `${workflowLabel} | ${run.workflow_id ?? ''}`), semanticStatus(run.status));
        const start = run.started_at || run.created_at;
        const finished = Date.parse(run.finished_at || '');
        const began = Date.parse(start || '');
        const seconds = Number.isFinite(finished) && Number.isFinite(began) ? Math.max(0, Math.floor((finished - began) / 1000)) : null;
        const duration = seconds === null ? 'Not available' : `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ${seconds % 60}s`;
        button.append(header, el('small', 'record-time', `Started: ${historyTime(start)}`),
          el('small', 'record-time', `Finished: ${run.finished_at ? historyTime(run.finished_at) : terminal.has(String(run.status).toLowerCase()) ? 'Not recorded' : 'Not finished'}`),
          el('small', 'record-time', `Total duration: ${run.finished_at ? duration : 'Not finished'}`));
        if (run.candidate_writer) button.append(el('small', 'record-time',
          `Candidate writer: ${text(run.candidate_writer.status)}${run.candidate_writer.manifest_status ? ` · ${text(run.candidate_writer.manifest_status)}` : ''}`));
        button.title = `${workflowLabel}\nRun ID: ${run.id}`;
        $('runs-list').append(button);
      }
      if (data.truncated) paragraph($('runs-list'), `Showing ${array(data.items).length} of ${data.total} saved agent runs. ${prefetch ? 'Saved history is not a current active-status check.' : 'Active status was checked across the complete committed history.'}`, 'small muted');
      if (offset > 0) $('runs-list').append(action('Previous saved agent runs', () => loadRuns(Math.max(0, offset - 50))));
      if (data.truncated) $('runs-list').append(action('Next saved agent runs', () => loadRuns(offset + 50)));
      if (prefetch) { updateControls(); return true; }
      updateControls();
      const activeRuns = array(data.items).filter(activeRun);
      if (!state.runId && activeRuns.length) selectRun(activeRuns[0].id);
      // The selected active record has its own status-only poll. Observe other
      // active history rows without delaying the committed history display.
      for (const active of activeRuns.filter(run => run.id !== state.runId)) {
        api(`/api/agents/runs/${encodeURIComponent(active.id)}?status_only=true`).then(run => {
          if (epoch !== state.epoch || ticket !== state.runsListTicket) return;
          recordedRunStates.set(run.id, runState(run, epoch));
          const button = Array.from($('runs-list').querySelectorAll('[data-run-id]'))
            .find(item => item.dataset.runId === run.id);
          button?.querySelector('.record-heading')?.lastElementChild?.replaceWith(semanticStatus(run.status));
        }).catch(() => { /* History remains a saved observation, not an idle-slot verdict. */ });
      }
    } catch (error) {
      if (epoch === state.epoch && ticket === state.runsListTicket) {
        if (!prefetch) state.agentRunsLoaded = false;
        const detail = `Recorded agent runs unavailable. ${error.message} Agent invocation stays disabled until active-run status is confirmed.`;
        if (!state.runsRowsLoaded) message($('runs-list'), detail);
        else {
          $('runs-list').querySelector('.runs-load-error')?.remove();
          paragraph($('runs-list'), detail, 'runs-load-error small error-copy');
        }
        if (!prefetch) updateControls();
      }
    }
  }
  $('refresh-runs').addEventListener('click', () => { loadRuns(); if (state.runId) selectRun(state.runId); });
  const actualCounter = value => Number.isSafeInteger(value) && value >= 0;
  function historyTime(value) {
    const stamp = Date.parse(value || '');
    if (!Number.isFinite(stamp)) return 'Not recorded';
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return `${new Intl.DateTimeFormat(undefined, {year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZoneName: 'short'}).format(stamp)} (${zone}, local)`;
  }
  function activityTime(value) {
    const stamp = typeof value === 'string' ? Date.parse(value) : NaN;
    return Number.isFinite(stamp) ? new Date(stamp).toLocaleTimeString(undefined,
      { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : 'Not recorded';
  }
  function savedAgentObservation(run) {
    const saved = run.observation_snapshot;
    return terminal.has(String(run.status).toLowerCase()) && saved?.schema_version === 1
      && saved.mode === 'historical_not_live' && saved.public_run_id === run.id
      && saved.package_hash === run.package_hash && saved.workflow_id === run.workflow_id
      && saved.scenario === run.scenario && saved.run_status === run.status
      && saved.finished_at === run.finished_at
      && (!run.output?.run_id || saved.run_id === run.output.run_id) ? saved : null;
  }
  function agentObservationReceipt(run) {
    const saved = savedAgentObservation(run);
    if (saved) return saved.status === 'verified' ? saved : null;
    return [run.live_activity, run.current_usage_receipt].find(receipt =>
      receipt?.status === 'verified' && receipt.public_run_id === run.id) ?? null;
  }
  function observedTokenMetrics(run) {
    // Choose actual numbers per field, never an entire masked historical object.
    // An authoritative subtotal stays partial even if historical output has a total.
    const sources = [agentObservationReceipt(run)?.usage,
      run.usage, run.token_usage, run.output?.usage, run];
    return ['input_tokens', 'output_tokens', 'total_tokens'].map(key => {
      for (const usage of sources) {
        if (actualCounter(usage?.[key])) return { key, value: usage[key], partial: false };
        if (actualCounter(usage?.[`known_${key}`]))
          return { key, value: usage[`known_${key}`], partial: true };
      }
      return { key, value: null, partial: false };
    });
  }
  function activityBar(values, maximum, label) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 400 ${values.length * 18}`);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', label);
    const caption = document.createElementNS(svg.namespaceURI, 'title');
    caption.textContent = label; svg.append(caption);
    values.forEach((value, index) => {
      if (value === null) return;
      const bar = document.createElementNS(svg.namespaceURI, 'rect');
      bar.setAttribute('x', '0'); bar.setAttribute('y', String(index * 18 + 2));
      bar.setAttribute('width', String(maximum > 0 ? 400 * value / maximum : 0));
      bar.setAttribute('height', '12'); bar.setAttribute('rx', '3');
      bar.setAttribute('class', values.length === 1 ? 'agent-duration-bar' : index === 0 ? 'agent-input-bar' : 'agent-output-bar');
      svg.append(bar);
    });
    return svg;
  }
  function updateAgentTurnClocks() {
    document.querySelectorAll('#run-detail [data-agent-turn-start]').forEach(node => {
      const start = Date.parse(node.dataset.agentTurnStart);
      node.textContent = Number.isFinite(start)
        ? `Pending · client elapsed ${Math.max(0, (Date.now() - start) / 1000).toFixed(1)}s · completion not recorded`
        : 'Pending · elapsed unknown · completion not recorded';
    });
  }
  function renderAgentUsageCharts(run, completed, previous) {
    const receipt = agentObservationReceipt(run);
    const panel = el('section', 'agent-usage-charts');
    panel.dataset.runId = run.id;
    panel.setAttribute('aria-label', 'Agent token consumption and model-call durations');
    heading(panel, 'Agent tokens & model turns', 'h3');
    if (completed) paragraph(panel, 'Historical observation, not live. Recorded token series and model-call durations are frozen; missing completions remain unknown.', 'small muted');
    paragraph(panel, 'Each turn is one actual model call in recorded start order, not an inferred agent iteration or retry loop. Counts are provider-reported; unknown is not zero.', 'small muted');
    const aggregate = observedTokenMetrics(run).filter(metric => metric.key !== 'total_tokens');
    const legend = el('p', 'small agent-token-legend');
    legend.append(el('span', 'agent-input-key', 'Input tokens'), el('span', 'agent-output-key', 'Output tokens'));
    panel.append(legend);
    const aggregateRow = el('article', 'agent-token-row agent-aggregate-tokens');
    const aggregateLabel = aggregate.map(metric => `${human(metric.key)}: ${metric.value === null ? 'unknown'
      : `${metric.value.toLocaleString()}${metric.partial ? ' (partial, recorded subtotal)' : ''}`}`).join(' | ');
    aggregateRow.append(el('strong', '', 'Run aggregate token consumption'));
    if (aggregate.some(metric => metric.value !== null)) aggregateRow.append(activityBar(
      aggregate.map(metric => metric.value), Math.max(0, ...aggregate.map(metric => metric.value ?? 0)), aggregateLabel));
    aggregateRow.append(el('span', 'small', aggregateLabel));
    panel.append(aggregateRow);
    heading(panel, 'Per-agent consumption', 'h4');
    if (!receipt || !Array.isArray(receipt.per_role) || !Array.isArray(receipt.turns)) {
      const history = el('div', 'agent-call-history');
      const missing = el('div');
      paragraph(missing, 'Per-agent counts and call timestamps are unavailable for this run. The graph shows the aggregate counts that were recorded.', 'small muted agent-per-role-unavailable');
      history.append(missing, renderAgentToolTurns(run, receipt, previous));
      panel.append(history);
      return panel;
    }
    const roles = receipt.per_role;
    const tokenKeys = ['input_tokens', 'output_tokens'];
    const tokenValue = (role, key) => actualCounter(role[`${key}_reported_calls`]) && role[`${key}_reported_calls`] > 0
      && actualCounter(role[`known_${key}`]) ? role[`known_${key}`] : null;
    const tokenLabel = (role, key) => {
      const value = tokenValue(role, key), unknown = role[`${key}_unknown_calls`];
      return `${key === 'input_tokens' ? 'Input' : 'Output'}: ${value === null ? 'unknown' : value.toLocaleString()}${actualCounter(unknown) && unknown > 0 ? ` · partial, ${unknown} call(s) unknown` : ''}`;
    };
    const maximum = Math.max(0, ...roles.flatMap(role => tokenKeys.map(key => tokenValue(role, key) ?? 0)));
    for (const role of roles) {
      const row = el('article', 'agent-token-row');
      const label = `${roleLabel(role.role)} · ${tokenKeys.map(key => tokenLabel(role, key)).join(' · ')}`;
      row.append(el('strong', '', roleLabel(role.role)),
        activityBar(tokenKeys.map(key => tokenValue(role, key)), maximum, label),
        el('span', 'small', tokenKeys.map(key => tokenLabel(role, key)).join(' | ')));
      panel.append(row);
    }
    if (!roles.length) paragraph(panel, 'No model starts recorded. Per-agent consumption is unknown.', 'small muted');
    const disclosure = el('details', 'agent-model-turns');
    disclosure.open = previous ? previous.open : true;
    disclosure.append(el('summary', '', `Model-call turns (${receipt.turns.length}, all recorded turns)`));
    const modelNotes = el('div', 'agent-call-history-notes');
    paragraph(modelNotes, 'One actual model call per row. Token counts and elapsed durations are shown when recorded.', 'small muted');
    disclosure.append(modelNotes);
    const turns = el('div', 'agent-turns'); turns.tabIndex = 0;
    turns.setAttribute('aria-label', 'All recorded model-call turns, scroll to review');
    const durationValue = turn => ['completed', 'failed'].includes(turn.status)
      && typeof turn.duration_ms === 'number' && Number.isFinite(turn.duration_ms) && turn.duration_ms >= 0 ? turn.duration_ms : null;
    const durationMaximum = receipt.turns.reduce((maximum, turn) => Math.max(maximum, durationValue(turn) ?? 0), 0);
    for (const turn of receipt.turns) {
      const row = el('article', 'agent-turn-row');
      const duration = durationValue(turn);
      row.append(el('strong', '', `Turn ${turn.turn} · ${roleLabel(turn.role)} · ${text(turn.endpoint ?? 'Model not reported')}`));
      paragraph(row, `Started: ${activityTime(turn.started_at)} | ${turn.status === 'failed' ? 'Failed' : 'Completed'}: ${activityTime(turn.failed_at ?? turn.completed_at)}`, 'small muted');
      const elapsed = el('span', 'small agent-turn-elapsed');
      if (turn.status === 'started' && !completed) elapsed.dataset.agentTurnStart = turn.started_at;
      elapsed.textContent = duration === null ? `${turn.status === 'started' ? 'Pending, completion not recorded' : 'Duration unknown'}`
        : `${human(turn.status)} · ${(duration / 1000).toFixed(3)}s · ${turn.duration_source === 'recorded_monotonic' ? 'recorded elapsed duration' : 'recorded timestamp difference'}`;
      row.append(elapsed);
      if (duration !== null) row.append(activityBar([duration], durationMaximum, `Turn ${turn.turn}: ${(duration / 1000).toFixed(3)} seconds, ${turn.status}`));
      paragraph(row, tokenKeys.map(key => `${key === 'input_tokens' ? 'Input' : 'Output'}: ${actualCounter(turn[key]) ? turn[key].toLocaleString() : 'unknown'}`).join(' | '), 'small');
      turns.append(row);
    }
    disclosure.append(turns);
    const history = el('div', 'agent-call-history');
    history.append(disclosure, renderAgentToolTurns(run, receipt, previous));
    panel.append(history);
    // Restore the reader's location on the ordinary two-second status render.
    requestAnimationFrame(() => { if (turns.isConnected) turns.scrollTop = previous?.scrollTop ?? 0; });
    return panel;
  }
  function agentToolObservation(run, receipt) {
    if (receipt?.status !== 'verified' || receipt.public_run_id !== run.id) return null;
    if (Object.hasOwn(receipt, 'tool_turns')) return { receipt, ledgerReadback: false };
    const current = run.current_usage_receipt;
    // An immutable historical model snapshot stays selected. Only tool fields
    // may be read back from the separately verified, exactly matching ledger.
    if (receipt.mode === 'historical_not_live' && typeof receipt.run_id === 'string' && receipt.run_id
        && current?.status === 'verified' && current.public_run_id === run.id
        && current.public_run_id === receipt.public_run_id && current.run_id === receipt.run_id)
      return { receipt: current, ledgerReadback: true };
    return null;
  }
  function renderAgentToolTurns(run, receipt, previous) {
    const observation = agentToolObservation(run, receipt);
    const tools = observation?.receipt;
    const recorded = tools?.tool_activity_status === 'recorded' && Array.isArray(tools.tool_turns);
    const disclosure = el('details', 'agent-tool-turns');
    disclosure.open = previous ? previous.toolOpen ?? true : true;
    disclosure.append(el('summary', '', recorded
      ? `Tool-call turns (${tools.tool_turns.length}, all recorded calls)` : 'Tool-call turns (unavailable)'));
    const toolNotes = el('div', 'agent-call-history-notes');
    disclosure.append(toolNotes);
    if (observation?.ledgerReadback) paragraph(toolNotes,
      'Separate ledger readback for the same verified public and runtime run IDs. This is not part of the immutable historical snapshot; the historical observation is unchanged.', 'small muted agent-tool-ledger-readback');
    if (!recorded) {
      paragraph(toolNotes, 'Tool-call list unavailable from this receipt or older backend. Missing tool activity is unknown, not zero.', 'small muted agent-tool-unavailable');
      return disclosure;
    }
    paragraph(toolNotes, 'One actual tool call per row. Rejected is not failed. Retry status is explicit; repeated tool names do not establish retries.', 'small muted');
    const turns = el('div', 'agent-tool-turn-list');
    turns.tabIndex = 0;
    turns.setAttribute('aria-label', 'All recorded tool calls, scroll to review');
    for (const turn of tools.tool_turns) {
      const row = el('article', 'agent-turn-row agent-tool-turn-row');
      const name = typeof turn.tool_name === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(turn.tool_name)
        ? turn.tool_name : 'Tool name not recorded';
      const status = ['completed', 'failed', 'rejected', 'started'].includes(turn.status) ? turn.status : 'Status unknown';
      row.append(el('strong', '', `Tool call ${turn.turn} · ${roleLabel(turn.role)} · ${name}`));
      const badge = semanticStatus(status);
      if (status === 'rejected') badge.classList.replace('failed', 'caution');
      row.append(badge);
      paragraph(row, `Started: ${activityTime(turn.started_at)}`, 'small muted');
      if (status !== 'started' && status !== 'Status unknown') paragraph(row,
        `${human(status)}: ${activityTime(turn[`${status}_at`])}`, 'small muted');
      const duration = status !== 'started' && status !== 'Status unknown'
        && typeof turn.duration_ms === 'number' && Number.isFinite(turn.duration_ms) && turn.duration_ms >= 0
        ? turn.duration_ms : null;
      paragraph(row, duration === null ? 'Duration unknown, no terminal duration recorded'
        : `Duration: ${duration} ms · ${turn.duration_source === 'recorded_monotonic' ? 'recorded elapsed duration'
          : turn.duration_source === 'recorded_timestamps' ? 'recorded timestamp difference' : 'duration source unknown'}`, 'small');
      paragraph(row, turn.retry_status === 'not_recorded'
        ? 'Retry status: not_recorded (retries unknown)' : 'Retry status: unknown', 'small');
      turns.append(row);
    }
    if (!tools.tool_turns.length) paragraph(turns, 'No tool calls recorded in this verified receipt.', 'small muted');
    disclosure.append(turns);
    requestAnimationFrame(() => { if (turns.isConnected) turns.scrollTop = previous?.toolScrollTop ?? 0; });
    return disclosure;
  }
  function renderAgentSkills(run, previousOpen) {
    const stats = agentObservationReceipt(run)?.skill_stats ?? run.output?.skill_stats;
    const panel = el('section', 'agent-skill-stats');
    panel.dataset.runId = run.id;
    heading(panel, 'PER-AGENT SKILLS', 'h3');
    paragraph(panel, 'Loaded means explicitly injected into the compiled role system prompt. Tool reads count successful read_file calls, including partial reads. Neither proves use in reasoning. CONTEXT.md reads are identified separately by name.', 'small muted');
    const skillGuide = el('details', 'agent-skill-guide');
    skillGuide.append(el('summary', '', 'BROAD SKILL DESCRIPTIONS'));
    paragraph(skillGuide, 'These are general role responsibilities, not proof that a skill was loaded or used in this run.', 'small muted');
    for (const role of ['supervisor', 'discovery', 'semantics', 'architecture', 'validation', 'artifact']) {
      const description = el('p', 'small');
      description.append(el('strong', '', `${roleLabel(role)}: `), document.createTextNode(roleSkillDescription(role)));
      skillGuide.append(description);
    }
    panel.append(skillGuide);
    if (stats?.status !== 'recorded' || !Array.isArray(stats.per_role)) {
      paragraph(panel, 'Skill statistics are unavailable for this run. The saved activity does not identify which skills were read.', 'small muted');
      return panel;
    }
    const table = el('table');
    const header = el('tr');
    for (const label of ['ROLE', 'LOADED COUNT', 'TOOL-READ COUNT']) {
      const cell = el('th', '', label); cell.scope = 'col'; header.append(cell);
    }
    const head = el('thead'); head.append(header); table.append(head);
    const body = el('tbody');
    for (const role of stats.per_role) {
      const row = el('tr');
      for (const label of [roleLabel(role.role), actualCounter(role.loaded_count) ? String(role.loaded_count) : 'Not available',
        actualCounter(role.tool_read_count) ? String(role.tool_read_count) : 'Not available']) row.append(el('td', '', label));
      body.append(row);
    }
    table.append(body); panel.append(table);
    const details = el('details'); details.open = previousOpen ?? false;
    details.append(el('summary', '', 'SKILL AND CONTEXT NAMES'));
    for (const role of stats.per_role) {
      paragraph(details, roleLabel(role.role), 'small');
      for (const item of array(role.loaded)) paragraph(details, `Loaded: ${text(item.path)}`, 'small muted');
      for (const item of array(role.tool_reads)) paragraph(details, `Tool read: ${text(item.path)} · ${text(item.count)} successful call(s)`, 'small muted');
      if (role.status === 'unavailable') paragraph(details, 'No load/read receipts available for this role.', 'small muted');
    }
    panel.append(details);
    return panel;
  }
  let liveRunClock = null;
  let liveRunObservation = null;
  const agentEvaluationSnapshots = new Map();
  function recordedFailureCount(kind, counts, live, completed) {
    const explicit = counts?.[`${kind}_failed`];
    if (Number.isSafeInteger(explicit) && explicit >= 0) return explicit;
    const started = counts?.[`${kind}_started`], succeeded = counts?.[`${kind}_completed`];
    const rejected = kind === 'tool' ? counts?.tool_rejected : 0;
    const active = array(live?.active_calls).filter(call => call?.kind === kind).length;
    if (!Number.isSafeInteger(started) || started < 0 || !Number.isSafeInteger(succeeded) || succeeded < 0
        || !Number.isSafeInteger(rejected) || rejected < 0) return null;
    const unaccounted = started - succeeded - rejected - active;
    if (unaccounted === 0) return 0;
    if (completed && active === 0 && unaccounted > 0) return unaccounted;
    return null;
  }
  function recordAgentEvaluationSnapshot(run, live, completed) {
    if (!live || live.status !== 'verified' || completed) return;
    const counts = live.counts || {};
    const valid = value => Number.isSafeInteger(value) && value >= 0;
    const rate = (numerator, denominator) => valid(numerator) && valid(denominator) && denominator > 0 && numerator <= denominator
      ? numerator / denominator * 100 : null;
    const modelDone = valid(counts.model_completed) ? counts.model_completed : null;
    const modelFailed = valid(counts.model_failed) ? counts.model_failed : null;
    const toolAccepted = valid(counts.tool_completed) ? counts.tool_completed : null;
    const toolRejected = valid(counts.tool_rejected) ? counts.tool_rejected : null;
    const toolFailed = valid(counts.tool_failed) ? counts.tool_failed : null;
    const roleItems = array(run.output?.role_completion);
    const taskItems = array(run.candidate_writer?.task_statuses);
    const values = {
      'Model call success': rate(modelDone, modelDone !== null && modelFailed !== null ? modelDone + modelFailed : null),
      'Tool acceptance': rate(toolAccepted, toolAccepted !== null && toolRejected !== null && toolFailed !== null
        ? toolAccepted + toolRejected + toolFailed : null),
      'Role output completion': roleItems.length ? rate(roleItems.filter(item => item.status === 'completed').length, 6) : null,
      'Draft disposition completion': taskItems.length ? rate(taskItems.filter(item => item.status === 'drafted').length, taskItems.length) : null
    };
    const started = Date.parse(run.started_at || run.created_at || '');
    if (!Number.isFinite(started)) return;
    const captured = Date.now();
    const signature = JSON.stringify(values);
    const points = agentEvaluationSnapshots.get(run.id) || [];
    const prior = points.at(-1);
    if (prior?.signature === signature && captured - prior.captured_at < 10000) return;
    points.push({elapsed_seconds: Math.max(0, (captured - started) / 1000), captured_at: captured,
      signature, values});
    if (points.length > 600) points.splice(1, points.length - 600);
    agentEvaluationSnapshots.set(run.id, points);
    while (agentEvaluationSnapshots.size > 10) agentEvaluationSnapshots.delete(agentEvaluationSnapshots.keys().next().value);
  }
  function renderAgentEvaluationTrend(run, completed) {
    const panel = el('section', 'agent-evaluation-trend');
    panel.setAttribute('aria-label', 'Evaluation KPI trends over elapsed run time');
    heading(panel, 'Operational KPI trend over run time', 'h3');
    paragraph(panel, 'Each point is a recorded service-status snapshot; the line connects sampled observations and does not invent event timestamps. These are execution indicators, not business-quality scores.', 'small muted');
    let points = agentEvaluationSnapshots.get(run.id) || [];
    let sourceLabel = 'Browser-observed live status snapshots';
    let seriesNames = ['Model call success', 'Tool acceptance', 'Role output completion', 'Draft disposition completion'];
    if (points.length < 2) {
      const receipt = agentObservationReceipt(run);
      const turns = array(receipt?.turns);
      const finished = turns.filter(turn => ['completed', 'failed'].includes(turn.status))
        .map(turn => ({turn, at: Date.parse(turn.failed_at || turn.completed_at || '')}))
        .filter(item => Number.isFinite(item.at)).sort((a, b) => a.at - b.at);
      const started = Date.parse(run.started_at || run.created_at || '');
      let success = 0, failures = 0;
      points = [];
      if (Number.isFinite(started)) for (const item of finished) {
        if (item.turn.status === 'completed') success += 1; else failures += 1;
        points.push({elapsed_seconds: Math.max(0, (item.at - started) / 1000), captured_at: item.at,
          values: {'Model call success': 100 * success / (success + failures)}});
      }
      seriesNames = ['Model call success'];
      sourceLabel = 'Recorded model-turn completion timestamps';
    }
    const available = seriesNames.filter(name => points.some(point => Number.isFinite(point.values?.[name])));
    if (points.length < 2 || !available.length) {
      paragraph(panel, completed
        ? 'No timestamped evaluation trend is available for this saved run. Final operational counts remain in the scorecard; missing history is not reconstructed.'
        : 'Waiting for a second recorded observation before drawing a trend. Current KPI values remain visible in the scorecard.', 'small muted');
      return panel;
    }
    const ns = 'http://www.w3.org/2000/svg';
    const chart = document.createElementNS(ns, 'svg'); chart.setAttribute('viewBox', '0 0 720 300');
    chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', `Operational KPI trends from ${sourceLabel}`);
    const left = 54, right = 700, top = 18, bottom = 242;
    for (const value of [0, 25, 50, 75, 100]) {
      const y = bottom - (value / 100) * (bottom - top);
      const line = document.createElementNS(ns, 'line'); line.setAttribute('x1', String(left)); line.setAttribute('x2', String(right));
      line.setAttribute('y1', String(y)); line.setAttribute('y2', String(y)); line.setAttribute('class', 'eval-trend-gridline'); chart.append(line);
      const label = document.createElementNS(ns, 'text'); label.setAttribute('x', '44'); label.setAttribute('y', String(y + 4));
      label.setAttribute('text-anchor', 'end'); label.setAttribute('class', 'eval-trend-axis'); label.textContent = `${value}%`; chart.append(label);
    }
    const minX = Math.min(...points.map(point => point.elapsed_seconds));
    const maxX = Math.max(...points.map(point => point.elapsed_seconds), minX + 1);
    const x = value => left + ((value - minX) / (maxX - minX)) * (right - left);
    const y = value => bottom - (value / 100) * (bottom - top);
    const colors = {'Model call success': '#4f46e5', 'Tool acceptance': '#0f766e',
      'Role output completion': '#0369a1', 'Draft disposition completion': '#a16207'};
    for (const name of available) {
      const measured = points.filter(point => Number.isFinite(point.values?.[name]));
      const polyline = document.createElementNS(ns, 'polyline');
      polyline.setAttribute('points', measured.map(point => `${x(point.elapsed_seconds)},${y(point.values[name])}`).join(' '));
      polyline.setAttribute('fill', 'none'); polyline.setAttribute('stroke', colors[name]); polyline.setAttribute('stroke-width', '3');
      polyline.setAttribute('stroke-linecap', 'round'); polyline.setAttribute('stroke-linejoin', 'round');
      polyline.setAttribute('aria-label', `${name}, ${measured.length} measured observations`); chart.append(polyline);
      for (const point of measured) {
        const dot = document.createElementNS(ns, 'circle'); dot.setAttribute('cx', String(x(point.elapsed_seconds)));
        dot.setAttribute('cy', String(y(point.values[name]))); dot.setAttribute('r', '4'); dot.setAttribute('fill', colors[name]);
        dot.setAttribute('aria-label', `${name}: ${point.values[name].toFixed(1)} percent at ${Math.floor(point.elapsed_seconds)} seconds`);
        chart.append(dot);
      }
    }
    const axis = document.createElementNS(ns, 'text'); axis.setAttribute('x', String((left + right) / 2));
    axis.setAttribute('y', '282'); axis.setAttribute('text-anchor', 'middle'); axis.setAttribute('class', 'eval-trend-axis');
    axis.textContent = 'Elapsed seconds from run start'; chart.append(axis); panel.append(chart);
    const legend = el('div', 'agent-evaluation-trend-legend');
    for (const name of available) {
      const item = el('span', 'small', name); item.style.setProperty('--trend-color', colors[name]); legend.append(item);
    }
    panel.append(legend, el('p', 'small muted', `${sourceLabel}. Model and tool percentages exclude pending calls. Reference-based correctness is not inferred from these trends.`));
    return panel;
  }
  function renderAgentRoleCallTrend(run, completed) {
    const panel = el('section', 'agent-evaluation-trend agent-role-call-trend');
    panel.setAttribute('aria-label', 'Six agent roles model-call progression');
    heading(panel, 'Model calls by agent role · six lines', 'h3');
    paragraph(panel, 'Each line is cumulative actual model calls started by that role. Recorded call-start times plot the steps; during a live run, the current status observation extends the lines without inventing calls.', 'small muted');
    const receipt = agentObservationReceipt(run);
    const roles = ['supervisor', 'discovery', 'semantics', 'architecture', 'validation', 'artifact'];
    const turns = array(receipt?.turns);
    const start = Date.parse(run.started_at || run.created_at || '');
    const completeInventory = Number.isSafeInteger(receipt?.turn_count) && receipt.turn_count === turns.length
      && turns.every(turn => roles.includes(turn.role) && Number.isFinite(Date.parse(turn.started_at || '')));
    if (!completeInventory || !Number.isFinite(start)) {
      paragraph(panel, 'A complete role-by-role model-call timeline is not available for this run. No missing calls are filled in or inferred.', 'small muted');
      return panel;
    }
    const events = turns.map(turn => ({role: turn.role, at: Date.parse(turn.started_at)}))
      .sort((a, b) => a.at - b.at);
    const cumulative = Object.fromEntries(roles.map(role => [role, 0]));
    const points = [{elapsed_seconds: 0, values: {...cumulative}}];
    for (let index = 0; index < events.length;) {
      const at = events[index].at;
      while (index < events.length && events[index].at === at) {
        cumulative[events[index].role] += 1; index += 1;
      }
      points.push({elapsed_seconds: Math.max(0, (at - start) / 1000), values: {...cumulative}});
    }
    const observedAt = completed ? Date.parse(run.finished_at || '') : Date.now();
    if (Number.isFinite(observedAt)) {
      const observedElapsed = Math.max(0, (observedAt - start) / 1000);
      if (observedElapsed > points.at(-1).elapsed_seconds)
        points.push({elapsed_seconds: observedElapsed, values: {...cumulative}});
    }
    const maxY = Math.max(1, ...Object.values(cumulative));
    const maxX = Math.max(1, ...points.map(point => point.elapsed_seconds));
    const ns = 'http://www.w3.org/2000/svg';
    const chart = document.createElementNS(ns, 'svg'); chart.setAttribute('viewBox', '0 0 720 300');
    chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', `Six role lines showing cumulative model calls, maximum ${maxY} calls`);
    const left = 54, right = 700, top = 18, bottom = 242;
    const x = value => left + value / maxX * (right - left);
    const y = value => bottom - value / maxY * (bottom - top);
    for (let index = 0; index <= 4; index += 1) {
      const value = maxY * index / 4, py = y(value);
      const line = document.createElementNS(ns, 'line'); line.setAttribute('x1', String(left)); line.setAttribute('x2', String(right));
      line.setAttribute('y1', String(py)); line.setAttribute('y2', String(py)); line.setAttribute('class', 'eval-trend-gridline'); chart.append(line);
      const label = document.createElementNS(ns, 'text'); label.setAttribute('x', '44'); label.setAttribute('y', String(py + 4));
      label.setAttribute('text-anchor', 'end'); label.setAttribute('class', 'eval-trend-axis'); label.textContent = String(Math.round(value)); chart.append(label);
    }
    const colors = {supervisor: '#4f46e5', discovery: '#0f766e', semantics: '#0369a1',
      architecture: '#a16207', validation: '#be123c', artifact: '#7c3aed'};
    for (const role of roles) {
      const path = document.createElementNS(ns, 'polyline');
      path.setAttribute('points', points.map(point => `${x(point.elapsed_seconds)},${y(point.values[role])}`).join(' '));
      path.setAttribute('fill', 'none'); path.setAttribute('stroke', colors[role]); path.setAttribute('stroke-width', '3');
      path.setAttribute('stroke-linecap', 'round'); path.setAttribute('stroke-linejoin', 'round');
      path.setAttribute('aria-label', `${roleLabel(role)}: ${cumulative[role]} model calls`); chart.append(path);
    }
    for (const [label, at] of [['0 min', 0], [`${Math.round(maxX / 120)} min`, maxX / 2], [`${Math.round(maxX / 60)} min`, maxX]]) {
      const textNode = document.createElementNS(ns, 'text'); textNode.setAttribute('x', String(x(at)));
      textNode.setAttribute('y', '264'); textNode.setAttribute('text-anchor', 'middle'); textNode.setAttribute('class', 'eval-trend-axis');
      textNode.textContent = label; chart.append(textNode);
    }
    const xTitle = document.createElementNS(ns, 'text'); xTitle.setAttribute('x', String((left + right) / 2));
    xTitle.setAttribute('y', '286'); xTitle.setAttribute('text-anchor', 'middle'); xTitle.setAttribute('class', 'eval-trend-axis');
    xTitle.textContent = 'Elapsed minutes from run start'; chart.append(xTitle); panel.append(chart);
    const legend = el('div', 'agent-evaluation-trend-legend agent-role-call-legend');
    for (const role of roles) {
      const item = el('span', 'small', `${roleLabel(role)} · ${cumulative[role]} calls`);
      item.style.setProperty('--trend-color', colors[role]); legend.append(item);
    }
    panel.append(legend, el('p', 'small muted', `Source: ${completed ? 'complete saved' : 'current'} recorded model-turn inventory. One turn is one actual call. Roles with no recorded starts remain at zero so far; a live endpoint extends the chart to the latest observation time.`));
    return panel;
  }
  function renderLatestAgentEvent(run, live, completed) {
    const row = el('section', 'agent-latest-event-row');
    row.setAttribute('aria-label', 'Latest recorded event details');
    heading(row, 'Latest recorded event', 'h3');
    const event = live?.last_event;
    if (!event) {
      paragraph(row, 'No latest-event receipt is available for this run.', 'small muted');
      return row;
    }
    const kind = String(event.event || '').startsWith('tool_') ? 'tool'
      : String(event.event || '').startsWith('model_') ? 'model' : 'other';
    const card = el('article', `agent-latest-event agent-event-${kind}${completed ? '' : ' is-active'}`);
    const role = roleLabel(event.role);
    card.append(el('strong', '', role), el('span', 'agent-event-kind', human(event.event || 'event not reported')));
    const timestamp = typeof event.created_at === 'string' ? Date.parse(event.created_at) : NaN;
    const time = el('time', 'agent-event-time', Number.isFinite(timestamp)
      ? new Date(timestamp).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'medium'}) : 'Timestamp not recorded');
    if (Number.isFinite(timestamp)) time.dateTime = new Date(timestamp).toISOString();
    card.append(time);
    const workflow = run.workflow_name || run.name || run.workflow_id || 'Workflow not recorded';
    const scenario = run.scenario ? human(run.scenario) : 'Scenario not recorded';
    const started = Date.parse(run.started_at || run.created_at || '');
    const elapsed = Number.isFinite(started) && Number.isFinite(timestamp)
      ? ` · event at ${Math.max(0, Math.floor((timestamp - started) / 1000))}s from run start` : '';
    card.append(el('span', 'agent-event-context', `Run ${text(run.id)} · ${text(workflow)} · ${scenario}${elapsed}`));
    const label = kind === 'tool' ? event.tool_name : kind === 'model' ? event.endpoint : null;
    if (label) card.append(el('span', 'agent-event-detail', `${kind === 'tool' ? 'Tool' : 'Endpoint'}: ${text(label)}`));
    const counts = live.counts || {};
    const relevant = kind === 'tool' ? ['tool_started', 'tool_completed', 'tool_rejected', 'tool_failed']
      : kind === 'model' ? ['model_started', 'model_completed', 'model_failed'] : [];
    for (const key of relevant) {
      const value = key === 'tool_failed' ? recordedFailureCount('tool', counts, live, completed)
        : key === 'model_failed' ? recordedFailureCount('model', counts, live, completed) : counts[key];
      card.append(el('span', 'agent-event-detail', `${human(key)}: ${Number.isSafeInteger(value) && value >= 0 ? value : 'not recorded'}`));
    }
    card.append(el('p', 'small muted', completed
      ? 'This is the latest event retained in the completed run receipt, not a live activity indicator.'
      : 'This is the latest committed event receipt. It may have completed; see in-flight calls above for current recorded state.'));
    card.append(logCopyButton(event));
    row.append(card);
    return row;
  }
  function renderAgentEvaluations(run, live, completed) {
    const panel = el('section', 'agent-evaluation-panel');
    panel.setAttribute('aria-label', 'Agent evaluation scorecard');
    heading(panel, 'AGENT EVALUATIONS', 'h3');
    paragraph(panel, 'Review recorded activity and operational results here. Business correctness requires separate validation; unavailable evaluations remain unscored.', 'small muted');
    const references = el('section', 'agent-evaluation-unavailable');
    heading(references, 'Reference-based quality KPIs', 'h4');
    paragraph(references, 'These quality measures require a labeled reference set or calibrated evaluator. Operational success is reported separately.', 'small muted');
    const referenceList = el('ul');
    for (const label of ['Evidence precision@K and recall@K', 'Citation correctness and claim-level groundedness',
      'Business-logic equivalence to reviewed Informatica behavior', 'Source/target mapping correctness and target authorization'])
      referenceList.append(el('li', '', `${label}: Not measured for this run`));
    references.append(referenceList); panel.append(references);
    const grid = el('div', 'agent-evaluation-cards');
    const counter = value => Number.isSafeInteger(value) && value >= 0;
    function metric(label, numerator, denominator, explanation) {
      const card = el('article', 'agent-evaluation-card');
      heading(card, label, 'h4');
      const measured = counter(numerator) && counter(denominator) && denominator > 0 && numerator <= denominator;
      const percentage = measured ? Math.round(100 * numerator / denominator) : null;
      card.append(el('strong', 'agent-evaluation-value', measured ? `${percentage}%` : 'Not measured'));
      if (measured) {
        const ns = 'http://www.w3.org/2000/svg';
        const chart = document.createElementNS(ns, 'svg');
        chart.setAttribute('viewBox', '0 0 100 8');
        chart.setAttribute('role', 'img');
        chart.setAttribute('aria-label', `${label}: ${numerator} of ${denominator}`);
        for (const [width, className] of [[100, 'eval-bar-track'], [percentage, 'eval-bar-value']]) {
          const rect = document.createElementNS(ns, 'rect');
          rect.setAttribute('width', String(width)); rect.setAttribute('height', '8'); rect.setAttribute('rx', '4');
          rect.setAttribute('class', className); chart.append(rect);
        }
        card.append(chart, el('span', 'small', `${numerator} / ${denominator}`));
      }
      paragraph(card, explanation, 'small muted'); grid.append(card);
    }
    const counts = live?.counts || {};
    const modelCompleted = live?.status === 'verified' ? counts.model_completed ?? 0 : null;
    const modelFailed = live?.status === 'verified' ? recordedFailureCount('model', counts, live, completed) : null;
    const modelFinished = counter(modelCompleted) && counter(modelFailed)
      ? modelCompleted + modelFailed : null;
    metric('Model execution success', modelCompleted, modelFinished,
      'Completed model calls / completed and failed calls. Pending calls excluded; technical success only.');
    const rejected = counts.tool_rejected;
    const toolFailed = recordedFailureCount('tool', counts, live, completed);
    const toolFinished = counter(counts.tool_completed) && counter(toolFailed) && counter(rejected)
      ? counts.tool_completed + toolFailed + rejected : null;
    metric('Tool acceptance', counts.tool_completed, toolFinished,
      'Accepted calls / accepted, rejected and failed calls. Legacy recordings may combine rejections with failures. Not tool-selection accuracy.');
    const source = run.output || {};
    const roles = array(source.role_completion);
    const expectedRoles = ['supervisor','discovery','semantics','architecture','validation','artifact'];
    metric('Role output completion', roles.length ? expectedRoles.filter(role =>
      roles.some(item => item.role === role && item.status === 'completed')).length : null,
      roles.length ? expectedRoles.length : null, 'Recorded completed role outputs / six required roles. Not quality of their findings.');
    const writer = run.candidate_writer;
    const tasks = array(writer?.task_statuses);
    metric('Draft disposition completion', tasks.length ? tasks.filter(task => task.status === 'drafted').length : null,
      tasks.length || null, 'Writer-reported drafted dispositions / task dispositions, including native conditions. Does not prove each task has a notebook or native Jobs YAML.');
    panel.append(grid);
    const outcome = el('article', 'agent-evaluation-outcome');
    heading(outcome, 'Migration outcome', 'h4');
    paragraph(outcome, completed ? `Recorded run: ${text(run.status)}. ${writer?.manifest_status ? `Code generation: ${human(writer.manifest_status)}.` : 'Code generation result not available.'}`
      : 'Run in progress. Final task success has not been evaluated.', 'small');
    paragraph(outcome, 'Business-logic correctness, schema-linking accuracy, native Jobs YAML validity and runtime parity are not established by these operational counters.', 'small muted');
    panel.append(outcome);
    const article = el('a', 'small', 'Evaluation methodology');
    article.href = 'https://agenticmusings.hashnode.dev/ai-evaluation-metrics-explained-from-precision-k-and-recall-k-to-reliable-agents';
    article.target = '_blank'; article.rel = 'noopener noreferrer'; panel.append(article);
    return panel;
  }
  function updateLiveRunClock() {
    const observed = liveRunObservation;
    if (!observed || observed.epoch !== state.epoch || observed.ticket !== state.runTicket) {
      clearInterval(liveRunClock); liveRunClock = null; return;
    }
    const elapsed = Number.isFinite(observed.started) ? Math.max(0, Math.floor((Date.now() - observed.started) / 1000)) : null;
    document.dispatchEvent(new CustomEvent('orion:agent-clock', {detail: {id: observed.id, elapsed}}));
    const age = Math.max(0, Math.floor((Date.now() - observed.receivedAt) / 1000));
    formStatus($('agent-form'), `Run ${observed.id}: last reported ${observed.status}${elapsed === null ? '' : ` · elapsed ${elapsed}s`}. Last service update ${age}s ago. ${observed.pending ? 'Checking current status…' : 'Waiting for the next service update.'}`);
    const clock = document.querySelector('#run-detail .agent-live-clock');
    if (clock) clock.textContent = `${elapsed === null ? 'Elapsed time unavailable' : `Elapsed ${elapsed}s`} · Last service update ${age}s ago${observed.pending ? ' · Status request pending' : ''}`;
    updateAgentTurnClocks();
  }
  let modelPricesInFlight = false, modelPricesNextAttempt = 0;
  async function loadModelListPrices() {
    if (modelPricesInFlight || Date.now() < modelPricesNextAttempt) return;
    modelPricesInFlight = true;
    try {
      const data = await api('/api/model-pricing/list');
      const rates = {};
      if (data?.status === 'available' && Array.isArray(data.rates)
          && typeof data.retrieved_at === 'string' && Array.isArray(data.source_urls)) {
        for (const rate of data.rates) {
          if (typeof rate?.endpoint !== 'string' || Object.prototype.hasOwnProperty.call(rates, rate.endpoint)) continue;
          rates[rate.endpoint] = {input_usd_per_million: rate.input_usd_per_1m_tokens,
            output_usd_per_million: rate.output_usd_per_1m_tokens,
            source_url: data.source_urls[0], observed_at: data.retrieved_at};
        }
      }
      document.dispatchEvent(new CustomEvent('orion:model-list-prices', {detail: {
        version: 'model-list-prices-v1', status: Object.keys(rates).length ? 'available' : 'unavailable', rates}}));
      modelPricesNextAttempt = Date.now() + (Object.keys(rates).length ? 900000 : 60000);
    } catch (_) {
      document.dispatchEvent(new CustomEvent('orion:model-list-prices', {detail: {version: 'model-list-prices-v1', status: 'unavailable', rates: {}}}));
      modelPricesNextAttempt = Date.now() + 60000;
    } finally { modelPricesInFlight = false; }
  }
  function selectRun(id) {
    clearInterval(liveRunClock); liveRunClock = null; liveRunObservation = null;
    state.runId = String(id);
    state.runTicket += 1;
    clearTimeout(state.runTimer);
    message($('run-detail'), 'Reading saved run status…');
    pollRun(state.runTicket);
  }
  async function pollRun(ticket, completedRecord = null) {
    const id = state.runId, epoch = state.epoch;
    if (!id) return;
    if (liveRunObservation?.ticket === ticket) liveRunObservation.pending = true;
    try {
      const run = completedRecord ?? await api(`/api/agents/runs/${encodeURIComponent(id)}?status_only=true`);
      if (epoch !== state.epoch || ticket !== state.runTicket) return;
      const saved = savedAgentObservation(run);
      // Status-only terminal reads carry their own immutable renderer inputs.
      // A subsequent full read keeps the same graphs, with complete output intact.
      if (saved && !run.output) run.output = {
        findings: saved.findings, role_completion: saved.role_completion,
        design_submission: saved.design_submission
      };
      recordedRunStates.set(run.id, runState(run, epoch));
      document.dispatchEvent(new CustomEvent('orion:agent-run', { detail: run }));
      void loadModelListPrices();
      const parentTerminal = terminal.has(String(run.status).toLowerCase());
      const writerActive = activeWriter(run);
      const commitPending = state.hosted && state.agentSubmitting;
      const completed = parentTerminal && !writerActive && !commitPending;
      if (completed && state.submittedRunId === String(run.id)) {
        state.submittedRunId = null;
        state.agentRunsLoaded = false;
      }
      clearInterval(liveRunClock); liveRunClock = null;
      liveRunObservation = completed ? null : {id,
        status: text(writerActive ? `candidate writer ${run.candidate_writer.status}` : run.status), epoch, ticket,
        started: Date.parse(run.started_at || run.created_at || ''),
        receivedAt: Date.now(), pending: false};
      if (liveRunObservation) liveRunClock = setInterval(updateLiveRunClock, 1000);
      const phaseLabels = {agent_analysis: 'Agent analysis', candidate_code_generation: 'Candidate code drafting', complete: 'Complete'};
      const activeStatus = activeRun(run);
      const phaseValue = writerActive ? (run.candidate_writer.phase || 'candidate_code_generation')
        : run.phase || (activeStatus ? 'agent_analysis' : '');
      const phase = typeof phaseValue === 'string' ? (phaseLabels[phaseValue] || (phaseValue.startsWith('candidate_code_generation:') ? `Candidate code drafting ${phaseValue.split(':')[1]}` : human(phaseValue))) : '';
      const workflowName = run.workflow_name || run.name
        || [...$('agent-workflow').options].find(option => option.value === run.workflow_id)?.textContent
        || 'Workflow name unavailable';
      const started = Date.parse(run.started_at || run.created_at || '');
      const observedEnd = completed ? Date.parse(run.finished_at || '') : Date.now();
      const elapsedSeconds = Number.isFinite(started) && Number.isFinite(observedEnd)
        ? Math.max(0, Math.floor((observedEnd - started) / 1000)) : null;
      const elapsed = elapsedSeconds === null ? '' : ` · ${elapsedSeconds}s`;
      formStatus($('agent-form'), `Run ${run.id}: ${text(run.status)}${writerActive ? ` · Candidate writer ${text(run.candidate_writer.status)}` : ''}${phase ? ` · ${phase}` : ''}${run.output?.status ? ` · ${human(run.output.status)}` : ''}${elapsed}. ${commitPending && parentTerminal ? 'Run finished in the active session; Delta commit is not yet confirmed.' : completed ? 'Review the recorded findings and generated draft below.' : 'Waiting for the next service status update.'}`, run.status === 'failed' ? 'error' : completed ? 'success' : '');
      if (!completed) state.agentRunning = true;
      updateControls();
      const oldCharts = $('run-detail').querySelector('.agent-usage-charts');
      const previousCharts = oldCharts?.dataset.runId === run.id ? {
        open: oldCharts.querySelector('.agent-model-turns')?.open ?? true,
        scrollTop: oldCharts.querySelector('.agent-turns')?.scrollTop ?? 0,
        toolOpen: oldCharts.querySelector('.agent-tool-turns')?.open ?? true,
        toolScrollTop: oldCharts.querySelector('.agent-tool-turn-list')?.scrollTop ?? 0
      } : null;
      const oldSkills = $('run-detail').querySelector('.agent-skill-stats');
      const previousSkillsOpen = oldSkills?.dataset.runId === run.id ? oldSkills.querySelector('details')?.open : false;
      clear($('run-detail'));
      const live = agentObservationReceipt(run);
      recordAgentEvaluationSnapshot(run, live, completed);
      const activeAgentRoles = [...new Set(array(live?.active_calls).map(call => String(call?.role || '').trim()).filter(Boolean))];
      const configuredRoles = $('agent-roles')?.textContent?.match(/^Configured roles:\s*(.*)$/i)?.[1]?.trim();
      const runSummary = el('section', 'agent-run-summary');
      runSummary.setAttribute('aria-label', 'DETAILED AGENT RUN DETAILS');
      heading(runSummary, 'DETAILED AGENT RUN DETAILS', 'h3');
      if (completed) paragraph(runSummary, `Historical observation, not live.${saved ? ` Snapshot captured ${activityTime(saved.captured_at)}.` : ' No terminal snapshot was saved for this older run.'} Elapsed time is frozen at the recorded finish.`, 'small muted');
      runSummary.append(semanticStatus(run.status));
      if (run.delivery_summary) paragraph(runSummary, run.delivery_summary,
        run.status === 'partial' ? 'small caution' : 'small muted');
      rows(runSummary, {
        agent: 'ORION multi-agent migration workflow',
        configured_agents: configuredRoles || 'Not reported',
        active_agent_role: activeAgentRoles.length ? activeAgentRoles.map(roleLabel).join(', ')
          : completed ? 'No active agent, run finished' : 'Not reported by service',
        id: run.id, workflow: workflowName, status: run.status,
        candidate_writer: run.candidate_writer
          ? `${run.candidate_writer.status}${run.candidate_writer.manifest_status ? ` · ${run.candidate_writer.manifest_status}` : ''}`
          : 'none recorded',
        phase: phase || 'Phase unavailable',
        started_at: historyTime(writerActive ? run.candidate_writer.started_at || run.candidate_writer.created_at : run.started_at),
        finished_at: (writerActive ? run.candidate_writer.finished_at : run.finished_at)
          ? historyTime(writerActive ? run.candidate_writer.finished_at : run.finished_at)
          : completed ? 'Not recorded' : 'Not finished',
        elapsed_seconds: elapsedSeconds ?? 'unknown'
      }, key => human(key).toUpperCase());
      const writerRow = [...runSummary.querySelectorAll('.detail-row')]
        .find(row => row.querySelector('.detail-key')?.textContent === 'CANDIDATE WRITER');
      const writerValue = writerRow?.querySelector('.detail-value');
      if (writerValue && run.candidate_writer) {
        clear(writerValue);
        writerValue.append(semanticStatus(run.candidate_writer.status));
        if (run.candidate_writer.manifest_status) writerValue.append(document.createTextNode(' · '),
          semanticStatus(run.candidate_writer.manifest_status));
      }
      $('run-detail').append(runSummary);
      let logPanel;
      if (live?.status === 'verified' && live.public_run_id === run.id) {
        logPanel = el('section', 'agent-live-logs');
        logPanel.setAttribute('aria-label', completed ? 'ORION HARNESS HISTORICAL OBSERVATION' : 'ORION HARNESS LIVE LOGS');
        const logHeader = el('div', 'agent-live-logs-header');
        heading(logHeader, completed ? 'ORION HARNESS HISTORICAL OBSERVATION' : 'ORION HARNESS LIVE LOGS');
        logHeader.append(el('span', 'small muted', 'Recorded agent and tool activity'));
        logPanel.append(logHeader);
        logPanel.append(el('p', `small muted agent-live-logs-footer${completed ? '' : ' agent-live-clock'}`,
          completed ? 'Historical, not live. No in-flight activity or advancing clock is implied.' : 'Waiting for the next service update.'));
        const counts = live.counts ?? {};
        const logCounts = el('div', 'agent-live-logs-counts');
        rows(logCounts, Object.fromEntries([
          ['model_started', 'Model calls started'], ['model_completed', 'Model calls finished'],
          ['model_failed', 'Model calls failed'], ['tool_started', 'Tool calls started'],
          ['tool_completed', 'Tool calls finished'], ['tool_failed', 'Tool calls failed'],
          ['tool_rejected', 'Submissions rejected for correction']
        ].map(([key, label]) => {
          const value = key === 'tool_failed' ? recordedFailureCount('tool', counts, live, completed)
            : key === 'model_failed' ? recordedFailureCount('model', counts, live, completed) : counts[key];
          return [label, key === 'tool_rejected' && value === undefined ? 'Not separately recorded'
            : Number.isSafeInteger(value) && value >= 0 ? value : 'unknown'];
        })));
        const rowStates = [
          ['model_started', null], ['model_completed', 'clear'], ['model_failed', 'model'],
          ['tool_started', null], ['tool_completed', 'clear'], ['tool_failed', 'tool'], ['tool_rejected', 'rejected']
        ];
        rowStates.forEach(([key, stateName], index) => {
          if (!stateName) return;
          const row = logCounts.children[index];
          const value = stateName === 'model' ? recordedFailureCount('model', counts, live, completed)
            : stateName === 'tool' ? recordedFailureCount('tool', counts, live, completed) : counts[key];
          row.dataset.callState = stateName === 'model' || stateName === 'tool'
            ? Number.isSafeInteger(value) && value === 0 ? 'clear'
              : Number.isSafeInteger(value) && value > 0 ? 'failed' : 'unknown'
            : stateName;
        });
        heading(logPanel, completed ? 'Calls unfinished at run completion' : 'Currently in-flight calls', 'h4');
        const logEntries = el('div', 'agent-live-log-entries');
        logEntries.tabIndex = 0;
        logEntries.setAttribute('aria-label', 'Recorded calls');
        for (const call of array(live.active_calls)) {
          const entry = el('article', 'agent-live-log-entry');
          entry.append(el('strong', '', roleLabel(call.role)),
            el('code', '', call.kind === 'tool' ? `Tool: ${text(call.tool_name)}` : `Model: ${text(call.endpoint)}`),
            el('span', 'small muted', `Started ${activityTime(call.started_at)} · ${completed ? 'Completion absent at finish, not live' : 'Completion not yet recorded'}`),
            logCopyButton(call));
          logEntries.append(entry);
        }
        if (!array(live.active_calls).length) paragraph(logEntries, completed
          ? 'No unfinished call was recorded in this observation.'
          : 'No call is currently recorded in flight. This does not establish run completion.', 'small muted');
        logPanel.append(logEntries);
        paragraph(logPanel, 'New runs count rejected submissions separately. Older run failure counts may include rejections; their original records are unchanged.', 'small muted');
        logPanel.append(logCounts);
      } else {
        logPanel = el('section', 'agent-live-logs agent-observation-unavailable');
        logPanel.setAttribute('aria-label', 'Recorded agent and tool activity');
        heading(logPanel, 'RECORDED AGENT AND TOOL ACTIVITY', 'h3');
        paragraph(logPanel, 'A verified event/count receipt is not available for this run. Missing activity data is unknown, not zero.', 'small muted');
      }
      const evaluationLayout = el('div', 'agent-observation-layout');
      evaluationLayout.append(logPanel, renderAgentEvaluations(run, live, completed));
      $('run-detail').append(evaluationLayout, renderLatestAgentEvent(run, live, completed),
        renderAgentEvaluationTrend(run, completed), renderAgentRoleCallTrend(run, completed));
      const usageMetrics = observedTokenMetrics(run);
      if (usageMetrics.some(metric => metric.value !== null)) {
        const stats = el('div', 'inventory-facts usage-stats');
        for (const metric of usageMetrics) {
          const label = `${human(metric.key)}${metric.partial ? ' (partial)' : ''}`;
          if (metric.value !== null) fact(stats, label, metric.value);
          else {
            const item = el('div', 'inventory-fact');
            item.append(el('strong', '', 'Unknown'), el('span', '', label));
            stats.append(item);
          }
        }
        $('run-detail').append(stats);
        if (usageMetrics.some(metric => metric.partial)) paragraph($('run-detail'),
          'Partial token counts are recorded provider-reported subtotals. Pending or unreported usage is unknown, not zero.', 'small muted');
      } else paragraph($('run-detail'), 'Token usage: Unknown. No usage counts returned by the service.', 'small muted');
      $('run-detail').append(renderAgentUsageCharts(run, completed, previousCharts));
      $('run-detail').append(renderAgentSkills(run, previousSkillsOpen));
      if (!completed && run.cancellation_requested_at) {
        paragraph($('run-detail'), 'Stop requested. Any in-flight model request may finish before the worker observes cancellation; no new role or candidate-code work should start.', 'small muted');
      } else if (!completed && !parentTerminal) {
        const stopButton = action('Stop this run', async () => {
          stopButton.disabled = true;
          stopButton.textContent = 'Requesting stop…';
          try {
            const result = await api(`/api/agents/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
            if (epoch !== state.epoch || ticket !== state.runTicket || state.runId !== id) return;
            if (result.cancellation_requested) {
              formStatus($('agent-form'), `Stop requested for run ${id}. Waiting for any in-flight model request to return before the worker stops.`, '');
              await pollRun(ticket);
            }
          } catch (error) {
            if (epoch !== state.epoch || ticket !== state.runTicket || state.runId !== id) return;
            stopButton.disabled = false;
            stopButton.textContent = 'Stop this run';
            paragraph($('run-detail'), `Stop request was not confirmed. ${error.message}`, 'small error-copy');
          }
        }, 'secondary compact');
        stopButton.dataset.runId = id;
        $('run-detail').append(stopButton);
      }
      if (['failed', 'error', 'partial', 'rejected'].includes(String(run.status).toLowerCase())) viewLogs($('run-detail'), run.id);
      if (run.output != null) {
        const originalOutput = el('details', 'agent-finding-original');
        originalOutput.append(el('summary', '', 'Original technical run output (historical, not independently verified)'),
          el('pre', 'agent-finding-technical', window.orionRedaction.text(json(run.output))));
        $('run-detail').append(originalOutput);
      }
      const codeDrafts = array(run.output?.candidate_code_drafts);
      if (codeDrafts.length) {
        heading($('run-detail'), 'Generated notebooks', 'h3');
        for (const draft of codeDrafts) {
          const matchingWriter = draft.job_id && draft.job_id === run.candidate_writer?.id
            ? run.candidate_writer : null;
          const taskStatuses = Array.isArray(draft.task_statuses) ? draft.task_statuses
            : Array.isArray(matchingWriter?.task_statuses) ? matchingWriter.task_statuses : null;
          const paths = new Set(array(taskStatuses).flatMap(task => array(task.artifact_paths)));
          const available = [...paths].filter(path => path.endsWith('.ipynb')).length;
          const message = available
            ? `${available} notebook path(s) reported for design v${draft.design_version ?? 'not recorded'}. Open the matching View proposal & code link below to read checksum-verified artifacts.`
            : taskStatuses
              ? `No notebook paths are listed in this writer attempt's task receipt${draft.design_version ? ` for design v${draft.design_version}` : ''}. Earlier saved designs and their artifacts are unchanged; use the matching View proposal & code links below to inspect them.`
              : `This run summary does not contain a per-task notebook path receipt${draft.design_version ? ` for design v${draft.design_version}` : ''}. Notebook absence is not established; use the matching View proposal & code links below to inspect saved artifacts.`;
          paragraph($('run-detail'), message);
          jsonDetails($('run-detail'), 'Generation diagnostic (technical)', draft);
        }
        paragraph($('run-detail'), 'Saved notebooks appear in the selected task workspace in Proposals. AI review and actual Databricks execution are separate evidence.', 'small muted');
      }
      if (run.error != null || run.errors != null) jsonDetails($('run-detail'), 'Service-recorded errors', run.error ?? run.errors, true);
      if (completed) {
        const review = el('section', 'disclosure');
        heading(review, 'Review findings and choose the next step', 'h3');
        const summaryView = el('section', 'agent-findings-summary');
        summaryView.append(el('h4', '', 'Recorded run summary'));
        findingText(summaryView, run.output?.summary ?? run.error ?? 'No analysis summary was recorded.');
        review.append(summaryView);
        paragraph(review, 'Historical assessment: the saved findings below describe this run, not a fresh analysis. Original records and downloads remain unchanged.', 'small muted');
        if (run.diagnostic_assessment) {
          const assessment = run.diagnostic_assessment;
          paragraph(review, `Selected saved analysis, not reparsed: ${Object.entries(assessment.counts ?? {}).map(([category, count]) => `${count} ${human(category)}`).join('; ')}. Run inputs ${assessment.run_input_matches_current ? 'match' : 'do not match or are not verified against'} current selected inputs. No historical warning is automatically resolved.`, 'small muted');
          jsonDetails(review, 'Current selected diagnostic scope and evidence (history unchanged)', assessment);
        }
        const distinct = new Map();
        for (const finding of array(run.output?.findings)) {
          const key = JSON.stringify([finding.role, finding.status, finding.summary, [...array(finding.evidence_ids)].sort(),
            finding.assumptions, finding.blockers, finding.proposed_changes, finding.alternatives,
            [...array(finding.knowledge_citations)].sort(), finding.diagnostic_scopes]);
          const group = distinct.get(key) ?? {finding, count: 0}; group.count++; distinct.set(key, group);
        }
        const diagnosticItems = new Map(array(run.diagnostic_assessment?.items).map(item => [item.diagnostic_id, item]));
        const displayFinding = finding => {
          const summary = String(finding.summary ?? 'No summary recorded.');
          const connectionOnly = /\b(connection references?|connection metadata|redacted|oracle)\b/i.test(summary);
          if (!/\b(unresolved|ambiguous|not unique|lack(?:s)? unique|missing (?:field|definition|reference|connection))\b/i.test(summary)
              && !connectionOnly) return summary;
          const scopes = array(finding.diagnostic_scopes).filter(scope => {
            const diagnostic = diagnosticItems.get(scope.diagnostic_id);
            return diagnostic && scope.owner_id === diagnostic.owner_id && scope.impact === diagnostic.impact
              && typeof scope.reason === 'string' && scope.reason.trim();
          });
          const exactNames = new Set(scopes.flatMap(scope => {
            const diagnostic = diagnosticItems.get(scope.diagnostic_id);
            return [diagnostic?.name, diagnostic?.reference_name, diagnostic?.owner_name]
              .filter(value => typeof value === 'string').map(value => value.toLowerCase());
          }));
          const mentioned = [...summary.matchAll(/['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]/g)].map(match => match[1])
            .filter(name => !name.toLowerCase().startsWith('wkf_'));
          const namesMatch = mentioned.every(name => exactNames.has(name.toLowerCase()));
          const onlyHistoricalConnection = connectionOnly && scopes.length > 0
            && scopes.every(scope => scope.impact === 'non_blocking_context');
          if (onlyHistoricalConnection) return 'Matching diagnostic scope records non-blocking historical connection context; the original agent wording below is retained.';
          return scopes.length && namesMatch && !(finding.status === 'unsupported' && scopes.every(scope => scope.impact === 'human_review'))
            ? summary : 'The named reference or field claim is not verified by matching selected workflow diagnostics. The historical wording is retained, not treated as a current verified blocker.';
        };
        for (const {finding, count} of distinct.values()) {
          renderAgentFinding(review, finding, displayFinding(finding), count, run.evidence_references);
        }
        const controls = el('div', 'button-row');
        controls.append(action('Download run JSON', () => downloadJSON(run, `agent-run-${run.id}.json`)));
        controls.append(action('View original workflow graph', () => { navigate('explorer', true); selectObject(run.workflow_id); selectView('graph'); }));
        const links = array(run.submitted_design_links).filter(link => /^[a-f0-9]{64}$/.test(link.id)
          && Number.isInteger(link.version) && link.version > 0 && link.record_id === run.id
          && link.workflow_id === run.workflow_id && link.scenario === run.scenario && link.package_hash === run.package_hash);
        for (const link of links) controls.append(action(`View proposal & code · v${link.version}`, () => {
          if (epoch !== state.epoch || ticket !== state.runTicket || state.runId !== run.id) return;
          document.dispatchEvent(new CustomEvent('orion:review-submitted-design', { detail: link }));
        }));
        if (!links.length && (array(run.redesign_refs).length || array(run.output?.redesigns).length))
          paragraph(review, 'Saved design link unavailable. An accepted submission count alone is not a verified stored design link.', 'small muted');
        controls.append(action('Review migration candidates', () => {
          $('scenario').value = run.scenario; state.proposalsLoaded = false; navigate('proposals', true);
        }));
        review.append(controls);
        if (links.length) review.prepend(controls);
        paragraph(review, 'Migration candidates are structural records, not an automatically accepted version of these agent findings.', 'small muted');
        const feedbackLabel = el('label', '', 'Changes or acceptance requirements for another agent run');
        feedbackLabel.htmlFor = 'agent-revision-feedback';
        const feedback = el('textarea'); feedback.id = 'agent-revision-feedback'; feedback.rows = 3; feedback.maxLength = 4000;
        feedback.placeholder = 'Describe what is incorrect, missing, or should be compared. No credentials.';
        review.append(feedbackLabel, feedback, action('Prepare revised request', () => {
          if (!feedback.value.trim()) { feedback.focus(); return; }
          $('agent-workflow').value = run.workflow_id;
          $('agent-workflow').dispatchEvent(new Event('change', { bubbles: true }));
          $('agent-scenario').value = run.scenario;
          $('agent-prompt').value = `${$('agent-prompt').value}\n\nReview feedback for run ${run.id}:\n${feedback.value.trim()}`.slice(0, 12000);
          updateControls();
          formStatus($('agent-form'), 'Revised request prepared. Click Invoke agents when ready. No run has been started.');
          $('agent-prompt').focus();
        }));
        const codeDrafts = array(run.output?.candidate_code_drafts);
        paragraph(review, links.length
          ? `The exact saved design is available through View proposal & code. Candidate code drafts: ${codeDrafts.length ? codeDrafts.map(item => `${text(item.status)}${item.manifest_status ? ` (${text(item.manifest_status)})` : ''}`).join(', ') : 'not reported'}. The next tab opens the DAG, code, and Jobs YAML when every task is supported. Drafts are not executed or deployed.`
          : 'No verified saved design link is available here. Structural candidates are separate from submitted designs.', 'small muted');
        $('run-detail').append(review);
      }
      jsonDetails($('run-detail'), 'Complete run record', run);
      paragraph($('run-detail'), 'Output is advisory and statically checked only. Candidate notebooks/SQL remain unapproved drafts. No Databricks Job was run and no deployment occurred.', 'small muted');
      if (!completed) {
        updateLiveRunClock();
        paragraph($('run-detail'), commitPending && parentTerminal
          ? 'The run finished in this session. Waiting for the hosted commit to be confirmed.'
          : 'Status is nonterminal. Checking the service every 2 seconds.', 'small muted');
        state.runTimer = setTimeout(() => pollRun(ticket), 2000);
      } else {
        loadRuns();
        if (run.status_only === true) {
          // Saved terminal status is already visible. Full historical findings
          // are a separate read and cannot hold the status display hostage.
          api(`/api/agents/runs/${encodeURIComponent(id)}`).then(full => {
            if (epoch === state.epoch && ticket === state.runTicket) pollRun(ticket, full);
          }).catch(error => {
            if (epoch === state.epoch && ticket === state.runTicket)
              paragraph($('run-detail'), `Saved status is visible; detailed output unavailable. ${error.message}`, 'small muted');
          });
        }
      }
    } catch (error) { if (epoch === state.epoch && ticket === state.runTicket) {
      state.agentRunsLoaded = false; updateControls();
      if (liveRunObservation) liveRunObservation.pending = false;
      const notice = document.querySelector('#run-detail .agent-status-read-error') || el('p', 'small caution agent-status-read-error');
      notice.textContent = `Current status unconfirmed. ${error.message} Reconnecting to the same run; no completion or new execution is assumed.`;
      if (!notice.isConnected) $('run-detail').append(notice);
      state.runTimer = setTimeout(() => pollRun(ticket), 3000);
    } }
  }

  const settingNames = ['github_owner', 'github_repo', 'default_branch', 'dev_host', 'test_host', 'prod_host'];
  async function loadSettings() {
    if (state.settingsLoaded) return true;
    if (!state.ready) return false;
    const epoch = state.epoch, ticket = ++state.settingsTicket;
    try {
      const data = await api('/api/settings');
      if (epoch !== state.epoch || ticket !== state.settingsTicket) return false;
      const settings = data.settings ?? data;
      if (!settings || typeof settings !== 'object' || Array.isArray(settings)) throw new Error('The service did not return valid settings.');
      for (const name of settingNames) $('settings-form').elements.namedItem(name).value = typeof settings[name] === 'string' ? settings[name] : '';
      state.settingsLoaded = true;
      updateControls();
      return true;
    } catch (error) { if (epoch === state.epoch && ticket === state.settingsTicket) formStatus($('settings-form'), `Settings unavailable. ${error.message} Refresh before editing.`, 'error'); return false; }
  }
  function validateSettings(settings) {
    for (const [key, value] of Object.entries(settings)) {
      if (/\b(?:Bearer\s|gh[pousr]_|github_pat_|password\s*[:=]|token\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=])|-----BEGIN .*PRIVATE KEY/i.test(value)) throw new Error('Credential-like material is not allowed in display settings.');
      if (key.endsWith('_host') && value) {
        let url;
        try { url = new URL(value); } catch { throw new Error(`${human(key)} must be a credential-free HTTPS origin.`); }
        if (url.protocol !== 'https:' || url.username || url.password || (url.pathname !== '/' && url.pathname !== '') || url.search || url.hash) throw new Error(`${human(key)} must be a credential-free HTTPS origin without a path, query, or fragment.`);
      }
    }
  }
  $('settings-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.ready || !state.settingsLoaded) return;
    const form = event.currentTarget, epoch = state.epoch;
    const settings = Object.fromEntries(settingNames.map((name) => [name, form.elements.namedItem(name).value.trim()]));
    try { validateSettings(settings); } catch (error) { formStatus(form, error.message, 'error'); return; }
    $('settings-submit').disabled = true;
    formStatus(form, 'Saving nonsecret integration metadata…');
    try {
      await post('/api/settings', settings);
      if (epoch !== state.epoch) return;
      formStatus(form, 'Nonsecret settings saved. Connectivity, GitHub publication, and deployment have not been performed.', 'success');
    } catch (error) { if (epoch === state.epoch) formStatus(form, `Settings save not confirmed. ${error.message}`, 'error'); }
    finally { updateControls(); }
  });

  function saveBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = el('a'); link.href = url; link.download = filename;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function downloadJSON(value, filename) { saveBlob(new Blob([json(value)], { type: 'application/json' }), filename); }
  $('download-object').addEventListener('click', () => { if (state.object) downloadJSON(state.object, 'object-understanding.json'); });
  all('[data-export]').forEach((link) => link.addEventListener('click', async (event) => {
    event.preventDefault();
    if (!assessmentReady()) { notify('Package exports are unavailable while selected package understanding is loading or unconfirmed.'); return; }
    const epoch = state.epoch;
    try {
      const blob = await request(link.getAttribute('href'), { download: true });
      if (epoch !== state.epoch) { notify('Package changed during export. Retry against the current package.', 'error'); return; }
      saveBlob(blob, link.getAttribute('href').endsWith('/english') ? 'estate-understanding.md' : 'estate-understanding.json.gz');
      notify('Service export received and download requested.');
    } catch (error) { if (epoch === state.epoch) notify(`Export unavailable. ${error.message}`, 'error'); }
  }));

  $('refresh').addEventListener('click', async () => {
    $('notice').hidden = true;
    state.proposalTicket += 1;
    proposalListFlight = null;
    await loadOverview();
    loadPage(state.page, state.page !== 'explorer');
    if (assessmentReady() && state.object && state.page === 'explorer') selectObject(state.object.node.id, undefined, true);
    if (state.jobId) { clearTimeout(state.jobTimer); pollJob(); }
  });
  window.addEventListener('pagehide', () => {
    clearTimeout(state.overviewTimer); clearTimeout(state.jobTimer); clearTimeout(state.runTimer); clearTimeout(searchTimer);
    clearTimeout(state.projectTimer); clearTimeout(state.logsTimer);
  });
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
      if ((state.projectId && state.memberHash) || state.legacyWorkspaceSelected) loadOverview();
      if (state.runId) selectRun(state.runId);
      if (state.projectId) loadProject();
      if (state.page === 'logs') loadLogs();
    }
  });
  async function initialize() {
    const requestedPage = location.hash.slice(1);
    const navIcons = { projects: 'plus', estate: 'grid', readiness: 'help', explorer: 'graph', proposals: 'draft', evidence: 'file', agents: 'partial', validate: 'check', release: 'arrow', logs: 'file', evolution: 'draft' };
    all('[data-page]').forEach((button) => {
      const glyph = button.querySelector('span[aria-hidden]');
      if (glyph) clear(glyph).append(icon(navIcons[button.dataset.page]));
    });
    clear($('agent-help')).append(icon('help'));
    clear($('theme-toggle')).append(icon(theme === 'dark' ? 'sun' : 'moon'));
    const mark = document.querySelector('.orbit-mark');
    if (mark) clear(mark).append(icon('package'));
    const exploreArrow = document.querySelector('[data-go="explorer"] span');
    if (exploreArrow) clear(exploreArrow).append(icon('arrow'));
    clear($('refresh')).append(icon('running'), document.createTextNode('Refresh data'));
    updateControls();
    try { localStorage.removeItem('lakeflow-studio-selection'); } catch { /* Old selection hints are optional and no longer restored. */ }
    try {
      const health = await api('/api/health');
      state.hosted = health.local_only === false || health.authentication === 'databricks_obo';
      document.dispatchEvent(new CustomEvent('orion:health', {detail: health}));
      state.healthCapabilityInventory = health.capability_inventory || null;
      if (state.healthCapabilityInventory) renderCapabilities(health.capabilities);
    } catch (error) {
      initializingNavigation = false;
      navigate('projects');
      $('connection-status').textContent = 'Startup unconfirmed';
      notify(`Cannot confirm workspace mode. ${error.message} Existing projects and XML may still be available.`, 'error');
      return;
    }
    state.legacyWorkspaceSelected = false;
    $('connection-status').textContent = 'API connected · choose a project';
    $('workspace-scope').textContent = 'Choose a project and completed XML package to load package understanding. Nothing is selected automatically.';
    initializingNavigation = false;
    navigate(startupUserPage || requestedPage || 'projects');
  }
  initialize();
})();