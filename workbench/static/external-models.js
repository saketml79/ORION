/* Session-only API selection. No discovery, inference, browser storage or secret telemetry. */
(() => {
  'use strict';
  const providerName = value => ({anthropic: 'Claude (Anthropic)', openai: 'OpenAI'}[value] || 'External API');
  const validModel = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value) &&
    !/^(sk-|dapi)/i.test(value);
  function validStatus(data) {
    return !!data && ['databricks', 'external'].includes(data.mode) &&
      typeof data.configured === 'boolean' && typeof data.inference_ready === 'boolean' &&
      (data.mode === 'databricks' ? data.configured === false && data.inference_ready === false && data.status === 'default' :
        (data.status === 'expired' && !data.configured && !data.inference_ready &&
          data.provider === null && data.model === null && data.expires_in_seconds === null) ||
        (['anthropic', 'openai'].includes(data.provider) && validModel(data.model) &&
          data.inference_ready === data.configured && data.status === (data.configured ? 'configured' : 'expired') &&
          Number.isFinite(data.expires_in_seconds) && data.expires_in_seconds >= 0));
  }
  function availability(state, now = Date.now()) {
    if (!state.scopeReady) return state.desired === 'external' ? false : null;
    if (!state.known || state.busy) return false;
    if (state.desired === 'external') return state.status?.mode === 'external' &&
      state.status.configured === true && state.status.inference_ready === true && now < state.expiresAt;
    if (state.status?.mode === 'external') return false;
    return state.dbReady;
  }
  function blocksDatabricks(state) {
    return state.busy || state.desired === 'external' || state.status?.mode === 'external' ||
      (state.scopeReady && !state.known);
  }
  function canChange(state, idle, workspaceBusy) {
    return state.scopeReady && state.known && !state.busy && idle && !workspaceBusy;
  }
  // Pure, nonbrowser contract tests use exactly the production predicates.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {availability, blocksDatabricks, canChange, validStatus, providerName, validModel};
  }
  if (typeof document === 'undefined') return;
  const $ = id => document.getElementById(id);
  const mount = $('external-models-mount'), panel = $('workspace-model-panel'), page = $('page-agents');
  if (!mount || !panel || !page) return;
  const nativeFetch = window.fetch.bind(window);
  const state = {desired: 'databricks', known: false, busy: false, scopeReady: false,
    status: null, expiresAt: 0, dbReady: null};
  const context = () => ({project: $('project-select').value, package: $('project-package').value});
  const scopeKey = () => JSON.stringify(context());
  let currentScope = '', revision = 0, expiryTimer = null, notice = '', availabilityOwned = false;
  const element = (tag, text, id) => {
    const item = document.createElement(tag); if (text) item.textContent = text;
    if (id) item.id = id; return item;
  };
  const form = element('form'); form.autocomplete = 'off';
  const mode = element('select', '', 'external-model-mode');
  mode.append(new Option('Databricks (default)', 'databricks'), new Option('External API key', 'external'));
  const modeLabel = element('label', 'Model provider mode'); modeLabel.htmlFor = mode.id;
  const fields = element('fieldset'); fields.className = 'external-fields'; fields.disabled = true;
  const legend = element('legend', 'External API configuration'); fields.append(legend);
  const provider = element('select', '', 'external-model-provider');
  provider.append(new Option('Claude (Anthropic API)', 'anthropic'), new Option('OpenAI API', 'openai'));
  const model = element('input', '', 'external-model-id'); model.type = 'text'; model.maxLength = 128;
  model.spellcheck = false;
  const secret = element('input', '', 'external-model-key'); secret.type = 'password';
  secret.autocomplete = 'new-password'; secret.spellcheck = false; secret.maxLength = 512;
  secret.setAttribute('autocapitalize', 'none');
  // No name attributes: native form serialization and URLs never contain a key.
  for (const [control, title] of [[provider, 'External provider'], [model, 'Explicit API model ID'], [secret, 'Provider API key']]) {
    const label = element('label', title); label.htmlFor = control.id; fields.append(label, control);
  }
  const compatibility = element('p', ''); compatibility.className = 'small muted';
  const configure = element('button', 'Use external API for all six roles'); configure.type = 'submit';
  configure.className = 'secondary compact'; fields.append(compatibility, configure);
  const billing = element('p', 'Anthropic and OpenAI API use is billed separately. ChatGPT, Claude, and Codex subscriptions include no API credits.');
  billing.className = 'small muted';
  const privacy = element('p', 'Key stays in server memory for up to 1 hour, scoped to this project, package, and session. Never stored in browser. Select Databricks to clear it. Hosted access and Unity Catalog require Databricks sign-in.');
  privacy.className = 'small muted';
  const status = element('p', '', 'external-model-status'); status.className = 'external-status small';
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const modeStatus = {
    databricks: 'Selected: Databricks. Existing workspace endpoint and six-role routing controls apply.',
    external: 'External API selected for configuration, not connected. Invocation is disabled until configured.'
  };
  const statusMessage = element('span'); status.append(statusMessage);
  for (const text of Object.values(modeStatus)) {
    const sizingText = element('span', text);
    sizingText.setAttribute('aria-hidden', 'true'); status.append(sizingText);
  }
  const refresh = element('button', 'Refresh provider status'); refresh.type = 'button'; refresh.className = 'secondary compact';
  $('model-provider-mode-mount').append(modeLabel, mode);
  form.append(fields, billing, privacy, refresh, status); mount.append(form);

  function workspaceBusy() {
    try { return panel.getAttribute('aria-busy') === 'true' || !!window.orionWorkspaceModelBusy?.(); }
    catch { return true; } // Unknown save state must not permit provider replacement.
  }
  function render() {
    const editable = canChange(state, page.dataset.agentRunStatus === 'idle', workspaceBusy());
    mode.disabled = !editable; fields.disabled = state.desired !== 'external' || !editable;
    model.required = secret.required = state.desired === 'external';
    configure.disabled = !editable || !validModel(model.value) || !/^[A-Za-z0-9_-]{16,512}$/.test(secret.value);
    refresh.disabled = !state.scopeReady || state.busy;
    mode.value = state.desired;
    panel.dataset.providerBlocked = String(blocksDatabricks(state));
    compatibility.textContent = provider.value === 'openai'
      ? 'Requires a tool-capable Chat Completions model. Responses-only Codex models are not supported by this transport. No model discovery or compatibility test is performed.'
      : 'Enter an Anthropic Messages API model with tool support. No model discovery or compatibility test is performed.';
    const active = state.status;
    const text = !state.scopeReady ? 'Select an explicit project and ready package to configure an external API.' :
      !state.known ? 'Selected provider mode is not confirmed. Refresh provider status before invoking.' :
      active?.mode === 'external' ? `Selected: ${providerName(active.provider)} / ${active.model || 'model unavailable'}. ${availability({...state, busy: false}) === true ? 'Configured in session memory only. Authentication, tool compatibility and inference are unverified.' : 'External session expired or inactive for the chosen mode. Reconnect or explicitly select Databricks.'}` :
      modeStatus[state.desired];
    const message = `${text}${notice ? ' ' + notice : ''}${!editable && !state.busy && state.scopeReady ? ' Provider changes require confirmed idle run status and no pending workspace save.' : ''}`;
    if (statusMessage.textContent !== message) statusMessage.textContent = message;
    if (state.desired === 'external' || active?.mode === 'external' || (state.scopeReady && !state.known)) {
      availabilityOwned = true;
      const target = $('agent-availability'); if (target.textContent !== text) target.textContent = text;
    } else if (availabilityOwned) {
      $('agent-availability').textContent = 'Selected: Databricks. Confirm readiness with the workspace endpoint controls. No inference has been verified.';
      availabilityOwned = false;
    }
  }
  function publish(refreshDatabricks = false) {
    render();
    document.dispatchEvent(new CustomEvent('orion:external-model-state', {detail: {refreshDatabricks}}));
  }
  window.orionExternalModels = {
    availability: () => availability(state), blocksDatabricks: () => blocksDatabricks(state),
    databricksConfigured: result => { if (!blocksDatabricks(state)) state.dbReady = result.inference_ready === true; },
    render
  };
  async function request(action, method = 'GET', body) {
    const scope = context();
    const response = await nativeFetch(`/api/models/external/${action}`, {
      method, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      headers: {'Accept': 'application/json', 'X-Workbench-Request': '1',
        'X-Workbench-Project': scope.project, 'X-Workbench-Package': scope.package,
        ...(method === 'POST' ? {'Content-Type': 'application/json'} : {})},
      ...(body === undefined ? {} : {body: JSON.stringify(body)})
    });
    // Never echo server errors or submitted values into UI/logging.
    if (!response.ok) throw new Error(`Provider configuration not confirmed (HTTP ${response.status}). Refresh status before retrying.`);
    const data = await response.json();
    if (!validStatus(data)) throw new Error('Provider status contract unavailable. Invocation remains disabled.');
    return data;
  }
  function accept(data) {
    clearTimeout(expiryTimer); state.status = data; state.known = true;
    state.desired = data.mode; state.expiresAt = Date.now() + (data.expires_in_seconds || 0) * 1000;
    if (data.mode === 'external') {
      if (data.provider) provider.value = data.provider;
      model.value = data.model || '';
      expiryTimer = setTimeout(() => { notice = 'Session lifetime elapsed. Refresh status or reconnect.'; publish(); },
        Math.min(3600000, Math.max(0, data.expires_in_seconds * 1000)));
    }
  }
  async function operate(action, body) {
    const ticket = scopeKey(), generation = ++revision;
    const previousMode = state.status?.mode;
    let restoredDatabricks = false;
    state.busy = true; notice = action === 'status' ? 'Reading configuration only.' : 'Updating session configuration only. No inference.'; publish();
    try {
      const data = await request(action, action === 'status' ? 'GET' : 'POST', body);
      if (ticket !== scopeKey() || generation !== revision) return;
      accept(data); notice = '';
      if (data.mode === 'databricks' && (action === 'disconnect' || previousMode === 'external')) {
        restoredDatabricks = true;
        // Do not mistake the prior external overview for a restored Databricks route.
        state.dbReady = false;
        const scope = context();
        const response = await nativeFetch('/api/models/status', {credentials: 'same-origin', cache: 'no-store', redirect: 'error',
          headers: {'X-Workbench-Project': scope.project, 'X-Workbench-Package': scope.package}});
        const db = response.ok ? await response.json() : null;
        if (ticket !== scopeKey() || generation !== revision) return;
        state.dbReady = db?.mode !== 'external' && db?.inference_ready === true;
        if (!response.ok) notice = 'Databricks readiness is unconfirmed. Connect and Apply its endpoint.';
      }
    } catch (error) {
      if (ticket === scopeKey() && generation === revision) {
        state.known = false; notice = error instanceof Error && error.message.startsWith('Provider ')
          ? error.message : 'Provider request failed. Refresh status to reconcile. Invocation remains disabled.';
      }
    } finally {
      if (body) body.api_key = '';
      if (ticket === scopeKey() && generation === revision) { state.busy = false; publish(restoredDatabricks); }
    }
  }
  mode.addEventListener('change', () => {
    if (!canChange(state, page.dataset.agentRunStatus === 'idle', workspaceBusy())) { render(); return; }
    secret.value = ''; state.desired = mode.value;
    if (state.desired === 'databricks' && state.status?.mode === 'external') operate('disconnect', {});
    else { notice = ''; publish(); }
  });
  provider.addEventListener('change', () => { model.value = ''; secret.value = ''; notice = ''; publish(); });
  form.addEventListener('input', event => { if (event.target !== mode) render(); });
  form.addEventListener('submit', event => {
    event.preventDefault(); render(); if (configure.disabled) return;
    const body = {provider: provider.value, model: model.value, api_key: secret.value};
    secret.value = ''; operate('connect', body);
  });
  refresh.addEventListener('click', () => { secret.value = ''; if (!refresh.disabled) operate('status'); });
  function syncContext() {
    const scope = context();
    const ready = /^[a-f0-9]{32}$/.test(scope.project) && /^[a-f0-9]{64}$/.test(scope.package) &&
      page.dataset.packageReady === 'true' && page.dataset.packageHash === scope.package;
    const next = scopeKey() + ':' + ready;
    if (next === currentScope) { render(); return; }
    currentScope = next; revision += 1; clearTimeout(expiryTimer); secret.value = ''; model.value = '';
    Object.assign(state, {desired: 'databricks', scopeReady: ready, known: false, busy: false, status: null, expiresAt: 0, dbReady: null});
    notice = ''; publish(); if (ready) operate('status');
  }
  document.addEventListener('change', syncContext);
  new MutationObserver(syncContext).observe(page, {attributes: true, attributeFilter: ['data-package-ready', 'data-package-hash', 'data-agent-run-status']});
  new MutationObserver(render).observe(panel, {attributes: true, attributeFilter: ['aria-busy']});
  window.addEventListener('pagehide', () => { secret.value = ''; clearTimeout(expiryTimer); });
  syncContext();
})();