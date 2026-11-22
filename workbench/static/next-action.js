/* One optional, presentation-only suggestion per explicit action context.
 * No requests, clicks, focus changes, or inferred readiness.
 */
(() => {
  'use strict';
  const main = document.getElementById('main');
  if (!main) return;
  const byId = id => document.getElementById(id);
  const ready = () => byId('package-ready')?.dataset.understanding === 'loaded';
  const canExplore = () => ready() && document.querySelector('.navigation [data-page="explorer"]')?.disabled === false;
  const routeReady = () => byId('agent-availability')?.classList.contains('success');
  const pickerReady = () => byId('workspace-model-panel')?.dataset.modelReady === 'true';
  const hostedModel = () => byId('workspace-model-panel')?.dataset.workspaceMode === 'obo';
  const runnable = () => routeReady() && pickerReady() && !byId('agent-submit')?.disabled
    && byId('agent-workflow')?.value && !byId('agent-workflow').disabled
    && [...byId('agent-workflow').selectedOptions].some(option => option.value && !option.disabled)
    && byId('agent-form')?.checkValidity();
  const candidate = (selector, why, when = () => true) => ({ selector, why, when });
  const value = id => byId(id)?.value || '';
  const checked = id => byId(id)?.checked === true;
  const validForm = id => byId(id)?.checkValidity() === true;
  const loadedObject = () => canExplore() && byId('download-object')?.disabled === false;
  function validationReady() {
    if (!validForm('validation-form')) return false;
    try {
      const layers = JSON.parse(value('validation-target-layers') || '{}');
      return layers !== null && typeof layers === 'object' && !Array.isArray(layers)
        && Object.values(layers).every(layer => ['bronze', 'silver', 'gold'].includes(layer));
    } catch { return false; }
  }
  // Explicit page/action priorities, not a generic first-button heuristic.
  // Only Invoke agents is a mutation candidate, with service-confirmed routing,
  // valid inputs and the parent's enabled run gate.
  const priorities = {
    projects: [
      candidate('#refresh-projects', 'Check saved projects, then choose a project and completed XML. Uploading another package is optional.'),
      candidate('#refresh-inputs', 'Review available sample packages before deciding whether to add one.'),
    ],
    estate: [
      candidate('#graph-view-trigger', 'Choose the relevant workflow or mapping overview in the open package graph.', () => canExplore() && !byId('package-graph-panel')?.hidden),
      candidate('[data-go="explorer"]', 'Inspect the verified selected package before requesting a migration design.', canExplore),
      candidate('#show-package-graph', 'Review the selected package inventory and relationships.', canExplore),
      candidate('.current-project > button', 'Choose a project and completed XML before opening package views.',
        () => !ready() && byId('package-ready')?.dataset.understanding !== 'loading'),
    ],
    readiness: [
      candidate('#readiness-refresh', 'Read exact selected-package evidence. Untracked runtime and parity remain blocked; refreshing never invokes agents or validation.', canExplore),
      candidate('#lineage-load', 'Load canonical workflow choices and saved source/destination evidence. This is a read, not conversion.', () => canExplore() && byId('lineage-workflow')?.disabled !== false && byId('readiness-refresh')?.disabled === false),
      candidate('#lineage-trace', 'Trace the selected source against saved draft or explicitly illustrative mock evidence. This never proves parity.', canExplore),
      candidate('#lineage-proposals', 'Review this workflow’s saved proposals and task grid.', () => canExplore() && !!value('lineage-workflow')),
    ],
    explorer: [
      candidate('#tab-graph', 'Return to Graph to choose another relevant source node.', () => loadedObject() && currentView() === 'fields'),
      candidate('#tab-english', 'Read the selected object’s captured behavior before reviewing its field evidence.',
        () => loadedObject() && currentView() === 'graph'),
      candidate('#tab-fields', 'Inspect field-level evidence for the selected object.',
        () => loadedObject() && currentView() === 'english'),
      candidate('#tab-graph', 'Return to the canonical relationship graph, not a technical download.', () => loadedObject() && currentView() === 'json'),
      candidate('#object-list button[data-object-id][aria-pressed="false"]', 'Open an object from the current inventory to inspect its structure.', () => canExplore() && !loadedObject() && !byId('object-list')?.querySelector('[aria-busy="true"]')),
    ],
    proposals: [
      candidate('#saved-design-tab-1', 'Inspect saved notebook artifacts and native bundle options for this exact design. Reading never generates code.', () => canExplore() && byId('saved-design-tab-1')?.getAttribute('aria-selected') === 'false'),
      candidate('#source-target-read', 'Inspect the selected workflow or invocation cohort’s source targets before reviewing destinations.', () => canExplore() && !!value('redesign-workflow')),
      candidate('#cohort-review-read', 'Read the exact saved cohort review. Reading does not queue agents or draft artifacts.', () => canExplore() && /^[a-f0-9]{32}$/.test(value('cohort-review-id'))),
      candidate('#spark-draft-submit', 'Generate code for the selected design. Deployment is a separate action.', () => canExplore() && routeReady() && !byId('spark-draft-submit')?.disabled),
      candidate('#sql-draft-submit', 'Draft captured SQL only. No execution or deployment.', () => canExplore() && !byId('sql-draft-submit')?.disabled),
      candidate('#workflow-artifact-submit', 'Generate only the supported deterministic example artifacts.', () => canExplore() && !byId('workflow-artifact-submit')?.disabled),
      candidate('#proposal-list button[data-proposal-id][aria-pressed="false"]', 'Review a current workflow candidate and its evidence. This does not approve or generate artifacts.', canExplore),
      candidate('#redesign-refresh', 'Read actual stored agent designs. Structural candidates and logical designs remain separate.', canExplore),
    ],
    evidence: [
      candidate('#connections-refresh', 'Review captured source and target bindings before adding supporting context.', canExplore),
      candidate('#refresh-documents', 'Review stored supporting documents. Owner statements do not clear gates.', canExplore),
    ],
    agents: [
      candidate('#connect-workspace-models', 'Find available Databricks models. This does not start a conversion.',
        () => hostedModel() && byId('agent-endpoint')?.disabled && !value('agent-endpoint')),
      candidate('#agent-endpoint', 'Choose a ready Databricks chat endpoint.',
        () => hostedModel() && !value('agent-endpoint')),
      candidate('label[for="endpoint-tool-support"]', 'Confirm that the model supports chat and the tools agents use.',
        () => hostedModel() && !!value('agent-endpoint') && !checked('endpoint-tool-support')),
      candidate('#apply-workspace-model', 'Save the selected model. This does not start a conversion.',
        () => hostedModel() && !!value('agent-endpoint') && checked('endpoint-tool-support')),
      candidate('#apply-workspace-model', 'Save the selected model. This does not start a conversion.', () => !!value('agent-endpoint') && checked('endpoint-tool-support') && !pickerReady()),
      candidate('#connect-workspace-models', 'Find available models. This does not start a conversion.', () => !pickerReady()),
      candidate('#agent-submit', 'Start the agents to generate code for the selected workflow.', () => canExplore() && runnable()),
      candidate('#refresh-runs', 'Review saved run status. A new conversion starts only when you start the agents.', () => canExplore() && routeReady() && pickerReady()),
    ],
    validate: [
      candidate('#validation-context', 'Load the exact selected analysis fingerprints first. No campaign, Job or model is started.', () => canExplore() && byId('validation-create')?.disabled !== false),
      candidate('#validation-create', 'Create a controlled local-fixture evidence report. Business parity remains separate.', () => canExplore() && validationReady()),
      candidate('#validation-export', 'Inspect the saved immutable evidence report. Downloading does not provision catalogs or clear release gates.', canExplore),
      candidate('#validation-read', 'Read the exact saved campaign ID, not an inferred campaign or execution result.', () => canExplore() && /^[a-f0-9]{32}$/.test(value('validation-campaign-id'))),
    ],
    release: [
      candidate('[data-export][href="/api/export/understanding"]', 'Inspect the package export before delivery configuration. Exporting does not publish or deploy.', canExplore),
      candidate('[data-export][href="/api/export/english"]', 'Review the structural report. Delivery and execution still require separate approval.', canExplore),
    ],
    logs: [
      candidate('#logs-refresh', 'Read recorded events for the current context. Missing usage remains unknown.'),
      candidate('#logs-export', 'Download recorded events for offline inspection.'),
    ],
    evolution: [
      candidate('#evidence-design-open', 'Review the evidence-to-decision flow. Roadmap features are not enabled integrations.'),
      candidate('#agent-design-open', 'Review the AI agents and the separate human approval steps.'),
    ],
  };
  const hints = new Map();
  for (const page of Object.keys(priorities)) {
    const section = byId(`page-${page}`);
    if (!section) continue;
    const hint = document.createElement('p');
    hint.id = `next-action-${page}`;
    hint.className = 'next-action-hint small muted';
    hint.hidden = true;
    section.querySelector('.page-heading, .hero')?.after(hint);
    hints.set(page, hint);
  }
  let page = '', target = null, frame = null, scopeKey = '', explorerIdentity = '';
  let completed = new Set(), typing = false, idleTimer = null;
  const currentView = () => byId('model-tabs')?.querySelector('[aria-selected="true"]')?.dataset.view || 'graph';
  const textInput = control => control?.matches('textarea, input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="button"]):not([type="submit"])');
  function context() {
    const fields = {
      projects: ['project-select', 'project-package'], estate: ['package-graph-kind'],
      readiness: ['readiness-scenario', 'lineage-example'],
      explorer: ['object-kind', 'object-search'],
      proposals: ['scenario', 'redesign-workflow', 'redesign-version', 'source-target-scope', 'cohort-review-id'],
      agents: ['agent-workflow', 'agent-scenario', 'agent-endpoint'],
      evidence: ['document-workflow'], validate: ['validation-workflow', 'validation-scenario', 'validation-source-catalog', 'validation-target-layers'],
      release: [], logs: ['logs-level', 'logs-role', 'logs-run'], evolution: [],
    };
    return JSON.stringify([page, value('project-select'), value('project-package'), value('estate-package-select'),
      byId('package-ready')?.dataset.understanding === 'loaded', byId('package-name')?.title,
      ...(fields[page] || []).map(id => byId(id)?.type === 'checkbox' ? checked(id) : value(id)),
      page === 'explorer' ? explorerIdentity : '']);
  }
  function actionKey(entry, control) {
    return `${entry.selector}:${control.dataset.objectId || control.dataset.proposalId || control.id || control.getAttribute('href') || ''}`;
  }
  function clear() {
    if (target) {
      target.classList.remove('orion-next-action');
      const describedBy = (target.getAttribute('aria-describedby') || '').split(/\s+/)
        .filter(id => id && ![...hints.values()].some(hint => hint.id === id));
      if (describedBy.length) target.setAttribute('aria-describedby', describedBy.join(' '));
      else target.removeAttribute('aria-describedby');
      target = null;
    }
    for (const hint of hints.values()) if (!hint.hidden) hint.hidden = true;
  }
  function available(control, section) {
    if (!control || !section.contains(control) || !control.matches('button, a.button, select, label[for="endpoint-tool-support"]')) return false;
    if (control.matches('label[for="endpoint-tool-support"]') && byId('endpoint-tool-support')?.disabled !== false) return false;
    if (control.matches(':disabled') || control.closest('[hidden], [inert], [aria-disabled="true"], [aria-busy="true"]')) return false;
    if (!control.getClientRects().length) return false;
    // checkVisibility also rejects visibility/opacity on ancestor containers.
    return control.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
  }
  function refresh() {
    frame = null;
    const section = byId(`page-${page}`);
    if (document.hidden) return; // Keep the existing animation paused, not restarted.
    const nextScope = context();
    if (nextScope !== scopeKey) { scopeKey = nextScope; completed = new Set(); }
    if (page === 'explorer' && loadedObject() && ['fields', 'english'].includes(currentView())) {
      const tab = byId(`tab-${currentView()}`);
      completed.add(`#tab-${currentView()}:${tab.id}`);
    }
    if (typing || !section || section.hidden
        || document.querySelector('dialog[open], [aria-modal="true"]')) { clear(); return; }
    let next = null, reason = '';
    for (const entry of priorities[page] || []) {
      if (!entry.when()) continue;
      next = [...section.querySelectorAll(entry.selector)].find(control => available(control, section)
        && !completed.has(actionKey(entry, control)));
      if (next) { reason = entry.why; break; }
    }
    if (target === next) return; // Routine reads/rerenders never restart animation.
    clear();
    if (!next) return;
    target = next;
    const hint = hints.get(page);
    const targetLabel = target.matches('select') ? target.labels?.[0]?.textContent : target.textContent;
    const label = (targetLabel || '').trim().replace(/\s+/g, ' ');
    const workspaceMatch = target.id === 'connect-workspace-models'
      ? /\bConnect workspace models:\s*(https:\/\/[A-Za-z0-9.-]+(?::443)?\/?)/.exec(label) : null;
    let workspaceOrigin = null;
    if (workspaceMatch) {
      try {
        const url = new URL(workspaceMatch[1]);
        if (url.protocol === 'https:' && url.hostname && !url.username && !url.password
            && (!url.port || url.port === '443') && (url.pathname === '/' || url.pathname === '')
            && !url.search && !url.hash
            && /^[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*$/.test(url.hostname)) workspaceOrigin = url.origin;
      } catch { /* Do not turn malformed status text into a link. */ }
    }
    if (workspaceOrigin) {
      const link = document.createElement('a');
      link.href = workspaceOrigin; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.textContent = workspaceOrigin; link.className = 'next-action-workspace-link';
      hint.replaceChildren(document.createTextNode('Suggested next step: Connect workspace models: '), link,
        document.createTextNode(`. ${reason}`));
    } else hint.textContent = `Suggested next step: ${label}. ${reason}`;
    hint.hidden = false;
    const tokens = new Set((target.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    tokens.add(hint.id); target.setAttribute('aria-describedby', [...tokens].join(' '));
    target.classList.add('orion-next-action');
  }
  function schedule() { if (frame === null) frame = requestAnimationFrame(refresh); }
  function enter(nextPage) {
    if (!Object.hasOwn(priorities, nextPage)) { clear(); page = ''; return; }
    if (nextPage !== page) { clear(); page = nextPage; scopeKey = ''; completed = new Set(); typing = false; }
    schedule();
  }
  document.addEventListener('orion:navigate', event => enter(event.detail?.page));
  document.addEventListener('orion:explorer-state', event => {
    const state = event.detail;
    // Object/node identity comes from the actual app callback, never graph text.
    if (state) explorerIdentity = JSON.stringify([state.objectId, state.nodeId]);
    schedule();
  });
  // Consume only the explicitly clicked candidate. Its stable identity remains
  // dismissed across busy -> ready transitions and rerenders in this scope.
  function interact(event) {
    if (!event.isTrusted || !page) return;
    const section = byId(`page-${page}`);
    if (!section?.contains(event.target)) return;
    if (event.type === 'click') {
      const control = event.target.closest('button, a.button');
      for (const entry of priorities[page] || []) {
        if (control?.matches(entry.selector) && available(control, section)
          && (control.matches('[data-view]') || entry.when())) completed.add(actionKey(entry, control));
      }
      // Keyboard/native input focus should not flash a moving cue while editing.
      if (textInput(event.target)) { typing = true; clear(); }
      else schedule();
    } else if (event.type === 'input' && textInput(event.target)) {
      typing = true; clear(); clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { typing = false; schedule(); }, 800);
    } else {
      if (event.type === 'change' && event.target.id === 'lineage-workflow') completed = new Set();
      schedule();
    }
  }
  for (const type of ['click', 'input', 'change']) document.addEventListener(type, interact, true);
  document.addEventListener('focusout', event => {
    if (textInput(event.target)) { clearTimeout(idleTimer); typing = false; schedule(); }
  });
  new MutationObserver(records => {
    // Exclude our own halo/description changes. Observe gates and mounted controls,
    // not every text update or animation tick, and never poll the DOM or an API.
    if (records.some(record => !record.target.closest?.('.next-action-hint')
      && (record.attributeName !== 'class' || ['package-ready', 'agent-availability'].includes(record.target.id)))) schedule();
  }).observe(main, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: ['class', 'hidden', 'disabled', 'inert', 'aria-disabled', 'aria-busy', 'aria-selected', 'aria-pressed', 'data-understanding', 'data-model-ready', 'data-workspace-mode', 'style'],
  });
  const locks = new MutationObserver(schedule);
  locks.observe(document.querySelector('.app-shell'), { attributes: true, attributeFilter: ['inert'] });
  for (const dialog of document.querySelectorAll('dialog')) locks.observe(dialog, { attributes: true, attributeFilter: ['open'] });
  function visibility() {
    main.classList.toggle('next-action-paused', document.hidden);
    schedule();
  }
  document.addEventListener('visibilitychange', visibility);
  window.addEventListener('pagehide', clear);
  window.addEventListener('pageshow', schedule);
  visibility();
  enter(main.dataset.currentPage);
})();