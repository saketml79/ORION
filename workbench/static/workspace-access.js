/* Fresh workspace authorization. Discovery never invokes a model or starts analysis. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const $ = (id) => document.getElementById(id);
  if (!$('workspace-model-panel') || !$('page-projects')) return;
  const node = (tag, text, className = '') => {
    const element = document.createElement(tag);
    element.textContent = text; element.className = className;
    return element;
  };
  const button = (id, text) => {
    const element = node('button', text, 'secondary compact');
    element.id = id; element.type = 'button'; return element;
  };
  const context = () => ({ project: $('project-select').value, package: $('project-package').value });
  const key = () => JSON.stringify(context());
  const disclosure = 'Uses the active Databricks authorization to browse workspace metadata. No token is requested or stored by this UI.';
  async function request(path, method = 'GET', body, scope = context()) {
    const headers = { Accept: 'application/json', 'X-Workbench-Request': '1' };
    if (scope.project) headers['X-Workbench-Project'] = scope.project;
    if (scope.package && scope.project) headers['X-Workbench-Package'] = scope.package;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { method, headers, credentials: 'same-origin', cache: 'no-store',
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
    let data;
    try { data = await response.json(); } catch { throw new Error(`HTTP ${response.status}: invalid JSON response.`); }
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? 'Request failed')}`);
    return data;
  }

  const endpoint = $('agent-endpoint'), confirmation = $('endpoint-tool-support');
  const modelStatus = $('workspace-model-status'), connect = $('connect-workspace-models');
  const modelControls = $('workspace-model-controls');
  const workspaceLine = node('p', 'Resolving configured workspace…', 'small muted');
  workspaceLine.id = 'workspace-model-target'; workspaceLine.setAttribute('aria-live', 'polite');
  connect.before(workspaceLine);
  const progress = node('div', '', 'workspace-model-progress');
  progress.id = 'workspace-model-progress'; progress.hidden = true;
  const spinner = node('span', '', 'workspace-model-spinner'); spinner.setAttribute('aria-hidden', 'true');
  const stage = node('span', ''); stage.setAttribute('role', 'status');
  const elapsed = node('span', '', 'small muted'); elapsed.id = 'workspace-model-elapsed';
  elapsed.setAttribute('aria-live', 'off');
  const bar = node('progress', ''); bar.setAttribute('aria-label', 'Workspace model operation in progress');
  progress.append(spinner, stage, elapsed, bar); connect.after(progress);
  const saveSteps = node('ol', '', 'workspace-model-save-steps');
  saveSteps.setAttribute('aria-label', 'Endpoint settings save stages'); saveSteps.hidden = true;
  for (const label of ['Validate endpoint', 'Save setting', 'Confirm saved']) {
    const step = node('li', label); step.dataset.state = 'waiting'; saveSteps.append(step);
  }
  progress.append(saveSteps);
  const timing = node('p', '', 'small muted'); timing.id = 'workspace-model-timing';
  const savedStatus = node('p', '', 'small muted'); savedStatus.id = 'workspace-model-saved-status';
  savedStatus.setAttribute('role', 'status');
  modelStatus.after(timing, savedStatus);
  let progressTimer = null, progressStarted = 0;
  const duration = seconds => seconds >= 60
    ? `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s` : `${seconds.toFixed(1)}s`;
  function tickProgress() {
    const seconds = (performance.now() - progressStarted) / 1000;
    elapsed.textContent = `${duration(seconds)} elapsed${seconds >= 15 ? ' · Still waiting for the server response.' : ''}`;
  }
  let workspaceURL = '', workspaceMode = '', workspaceGeneration = 0;
  function showWorkspace(data) {
    // Defense in depth: display only a strict HTTPS origin, never credentials,
    // paths, query strings or a guessed browser/application origin.
    let origin = '';
    try {
      const url = new URL(data.workspace);
      if (typeof data.workspace === 'string' && data.workspace === data.workspace.trim() &&
          url.protocol === 'https:' && !url.username && !url.password && !url.port &&
          (!url.pathname || url.pathname === '/') && !url.search && !url.hash &&
          /^[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*$/.test(url.hostname) &&
          /^https:\/\/[A-Za-z0-9.-]+(?::443)?\/?$/.test(data.workspace)) origin = url.origin;
    } catch { /* Unknown is safer than exposing an invalid target. */ }
    workspaceURL = origin; workspaceMode = data.mode ?? workspaceMode;
    $('workspace-model-panel').dataset.workspaceMode = workspaceMode;
    const mode = workspaceMode === 'obo' ? 'Hosted signed-in user (OBO)' :
      workspaceMode === 'local_user_profile' ? 'Local labs profile' : 'Authentication mode not confirmed';
    workspaceLine.textContent = `Workspace ${origin || 'URL unavailable'} · ${mode}`;
    connect.textContent = origin ? `Connect workspace models: ${origin}` : 'Connect workspace models';
    if (!progress.hidden) {
      stage.textContent = `${stage.textContent.split(' · Workspace ')[0]} · Workspace ${origin || 'URL unavailable'}`;
      bar.setAttribute('aria-label', stage.textContent);
    }
  }
  async function workspaceContext() {
    const ticket = key(), revision = ++workspaceGeneration;
    workspaceURL = ''; workspaceMode = '';
    workspaceLine.textContent = 'Resolving configured workspace…';
    connect.textContent = 'Connect workspace models';
    try {
      const data = await request('/api/models/workspace');
      if (ticket === key() && revision === workspaceGeneration) showWorkspace(data);
    } catch {
      if (ticket === key() && revision === workspaceGeneration) showWorkspace({});
    }
  }
  const apply = button('apply-workspace-model', 'Apply endpoint selection');
  apply.disabled = true;
  const modelDisclosure = node('p', disclosure, 'small muted workspace-model-disclosure');
  modelControls.append(apply, modelDisclosure);
  let modelScope = '', modelBusy = false, endpoints = [], modelGeneration = 0;
  let activeEndpoint = '', savedReady = false, settingsGeneration = 0, routeVersion = null;
  let runningEndpoint = '', runningModelId = '';
  const runLocked = () => $('page-agents').dataset.agentRunStatus === 'busy';
  function showLockedEndpoint() {
    const name = runningEndpoint || activeEndpoint;
    if (!runLocked() || !name) return;
    if (![...endpoint.options].some(option => option.value === name))
      endpoint.append(new Option(name, name));
    endpoint.value = name;
  }
  const pendingSave = () => workspaceMode === 'obo' && window.orionModelRouteSave?.pending(context(), workspaceURL);
  const savePhases = Object.freeze({submitting: 'Submitting settings save', reconciling: 'Checking recorded save receipt',
    queued: 'Waiting for settings writer', starting: 'Starting settings save',
    provisioning: 'Opening user-scoped settings storage', route_validate: 'Validate endpoint',
    route_commit: 'Save setting', route_readback: 'Confirm saved setting', committed: 'Saved setting confirmed'});
  const selectionSummary = node('div', '', 'endpoint-selection-summary');
  selectionSummary.id = 'workspace-model-selection';
  selectionSummary.setAttribute('aria-live', 'polite');
  confirmation.closest('label').after(selectionSummary);
  function renderSelection() {
    selectionSummary.replaceChildren();
    showLockedEndpoint();
    for (const [label, value] of [[runLocked() ? 'Run endpoint (locked)' : 'Selected endpoint', endpoint.value || 'Not selected'],
      ['Previously saved route', activeEndpoint || 'Not confirmed by the service']]) {
      const row = node('div', '', 'endpoint-selection-row');
      row.append(node('span', label), node('strong', value)); selectionSummary.append(row);
    }
    const recorded = pendingSave();
    const pending = recorded || (!endpoint.disabled && endpoint.value && endpoint.value !== activeEndpoint);
    selectionSummary.append(node('p', recorded ? `Operation ${recorded.id}: completion is not confirmed here. Reconnect to its receipt. The previously saved route does not confirm this save.` :
      pending ? 'Pending changes: confirm tool compatibility and Apply endpoint selection. The active runtime has not changed.' :
      runLocked() ? 'Model selection is locked until the active run finishes. Reloading does not change its route.' :
      activeEndpoint ? 'Saved configuration shown. Model tool support has not been tested here.' : 'Connect to retrieve the saved configuration.',
      pending ? 'endpoint-pending' : 'small muted'));
  }
  function modelContext() {
    return context();
  }
  function syncApply() {
    showLockedEndpoint();
    if (window.orionExternalModels?.blocksDatabricks()) {
      $('workspace-model-panel').dataset.modelReady = String(window.orionExternalModels.availability() === true);
      connect.disabled = endpoint.disabled = confirmation.disabled = apply.disabled = true;
      return;
    }
    $('workspace-model-panel').dataset.modelReady = String(savedReady && Boolean(activeEndpoint) && !modelBusy && !pendingSave() &&
      (!endpoint.value || endpoint.value === activeEndpoint));
    connect.disabled = modelBusy || runLocked();
    endpoint.disabled = modelBusy || runLocked() || modelScope !== key() ||
      !endpoints.some((item) => item.type === 'chat' && item.state === 'READY');
    confirmation.disabled = modelBusy || runLocked();
    apply.textContent = pendingSave() ? 'Reconnect to recorded model save' : 'Apply endpoint selection';
    apply.disabled = modelBusy || runLocked() || (!pendingSave() && (modelScope !== key() || !confirmation.checked ||
      (workspaceMode === 'obo' && (!routeVersion || !window.orionModelRouteSave)) ||
      !endpoints.some((item) => item.name === endpoint.value && item.type === 'chat' && item.state === 'READY')));
  }
  function modelProgress(text = '') {
    clearInterval(progressTimer); progressTimer = null;
    if (text) {
      delete timing.dataset.measurements;
      progressStarted = performance.now(); timing.textContent = '';
      tickProgress(); progressTimer = setInterval(tickProgress, 1000);
    } else if (!progress.hidden) {
      timing.textContent = `Request finished after ${duration((performance.now() - progressStarted) / 1000)}.`;
    }
    progress.hidden = !text; stage.textContent = text;
    $('workspace-model-panel').setAttribute('aria-busy', String(Boolean(text)));
    bar.setAttribute('aria-label', text || 'Workspace model operation in progress');
    syncApply();
  }
  function resetModels(scopeChanged = true) {
    modelGeneration += 1;
    modelBusy = false; modelProgress();
    modelScope = ''; endpoints = []; confirmation.checked = false;
    endpoint.replaceChildren(new Option('Connect to discover model endpoints', ''));
    endpoint.disabled = true; syncApply();
    if (scopeChanged) {
      settingsGeneration += 1; activeEndpoint = ''; savedReady = false; routeVersion = null;
      savedStatus.textContent = ''; timing.textContent = '';
    }
    syncApply(); renderSelection();
  }
  endpoint.addEventListener('change', () => {
    confirmation.checked = false; syncApply(); renderSelection();
    modelStatus.textContent = `Selected endpoint: ${endpoint.value || 'none'}. Not applied. Confirm tool compatibility, then Apply endpoint selection. Existing runs retain their original endpoint.`;
  });
  confirmation.addEventListener('change', syncApply);
  connect.addEventListener('click', async () => {
    if (modelBusy || runLocked()) return;
    const ticket = key();
    resetModels(false); modelBusy = true;
    modelProgress(`Discovering endpoints · Workspace ${workspaceURL || 'URL not yet resolved'}`);
    const revision = modelGeneration;
    try {
      const scope = modelContext();
      modelStatus.textContent = 'Connecting and listing workspace models. Your package is not sent to a model during this step.';
      const data = await request('/api/models/connect', 'POST', {}, scope);
      if (ticket !== key() || revision !== modelGeneration) return;
      if (data.connected !== true || !Array.isArray(data.endpoints)) throw new Error('Workspace endpoint discovery was not confirmed. Reconnect to retry.');
      if (data.workspace) { workspaceGeneration += 1; showWorkspace(data); }
      endpoints = Array.isArray(data.endpoints) ? data.endpoints : [];
      endpoint.replaceChildren(new Option('Choose a ready chat endpoint', ''));
      endpoints.forEach((item) => {
        const option = new Option(`${item.name} · ${item.type} · ${item.state} · tools: ${item.tool_capability ?? 'not reported'}`, item.name);
        option.disabled = item.type !== 'chat' || item.state !== 'READY'; endpoint.append(option);
      });
      const first = endpoints.find((item) => item.type === 'chat' && item.state === 'READY');
      endpoint.value = ''; endpoint.disabled = !first; modelScope = ticket;
      renderSelection();
      modelStatus.textContent = first
        ? 'Connected. Choose a READY chat model, confirm it supports the tools agents use, then apply your selection. This does not start a conversion.'
        : 'Connected, but no endpoint explicitly reports both READY and chat. No selection applied.';
      const measurements = data.timings;
      if (measurements && ['authentication_seconds', 'endpoint_list_seconds'].every(name =>
        Number.isFinite(measurements[name]) && measurements[name] >= 0)) {
        timing.dataset.measurements = `Authentication ${duration(measurements.authentication_seconds)} · Endpoint list ${duration(measurements.endpoint_list_seconds)}`;
      }
    } catch (error) {
      if (ticket === key() && revision === modelGeneration) {
        settingsGeneration += 1; savedReady = false;
        savedStatus.textContent = 'Route readiness not confirmed. Reconnect to retry.';
        modelStatus.textContent = error.message;
      }
    }
    finally {
      if (ticket === key() && revision === modelGeneration) {
        modelBusy = false; modelProgress();
        if (timing.dataset.measurements) {
          timing.textContent += ` ${timing.dataset.measurements}.`; delete timing.dataset.measurements;
        }
        syncApply(); renderSelection();
      }
    }
  });
  apply.addEventListener('click', async () => {
    syncApply(); if (apply.disabled) return;
    const ticket = key(); modelBusy = true; connect.disabled = true; syncApply();
    settingsGeneration += 1;
    const revision = ++modelGeneration;
    modelProgress(`Applying endpoint selection · Workspace ${workspaceURL || 'URL unavailable'}`);
    saveSteps.hidden = workspaceMode !== 'obo';
    for (const step of saveSteps.children) { step.dataset.state = 'waiting'; step.removeAttribute('aria-current'); }
    try {
      modelStatus.textContent = 'Saving the selected model for this project and package. This does not start a conversion.';
      const body = {
        endpoint: endpoint.value, tool_support_confirmed: confirmation.checked === true,
      };
      const scope = modelContext();
      const data = workspaceMode === 'obo'
        ? await window.orionModelRouteSave.save({scope, workspace: workspaceURL,
          body: {...body, expected_route_version: routeVersion},
          isCurrent: () => ticket === key() && revision === modelGeneration,
          onStatus: state => {
            if (ticket !== key() || revision !== modelGeneration) return;
            const phase = state.progress?.stage || state.status;
            stage.textContent = savePhases[phase] || 'Waiting for confirmed settings progress';
            bar.setAttribute('aria-label', stage.textContent);
            const index = ['route_validate', 'route_commit', 'route_readback'].indexOf(phase);
            if (index >= 0) Array.from(saveSteps.children).forEach((step, position) => {
              step.dataset.state = position < index ? 'complete' : position === index ? 'active' : 'waiting';
              if (position === index) step.setAttribute('aria-current', 'step');
              else step.removeAttribute('aria-current');
            });
            modelStatus.textContent = `Operation ${state.operation_id}: ${state.status}. Waiting for a scoped committed receipt. Previously saved route is not proof this Apply succeeded.`;
          }})
        : await request('/api/models/select', 'POST', body, scope);
      if (ticket !== key() || revision !== modelGeneration) return;
      activeEndpoint = data.selected_endpoint; savedReady = data.inference_ready === true;
      routeVersion = data.route_version ?? routeVersion;
      savedStatus.textContent = `Saved route: ${activeEndpoint || 'not confirmed'}.`; renderSelection();
      modelStatus.textContent = savedReady
        ? `Saved model: ${data.selected_endpoint}. The agents are configured; the model has not been tested by this step.`
        : `Saved model: ${data.selected_endpoint}. The service has not confirmed that the agents are ready.`;
      if (typeof window.orionModelRouteConfigured === 'function') window.orionModelRouteConfigured(data);
    } catch (error) { if (ticket === key() && revision === modelGeneration) modelStatus.textContent = error.message; }
    finally {
      if (ticket === key() && revision === modelGeneration) { modelBusy = false; modelProgress(); syncApply(); renderSelection(); }
    }
  });
  async function modelState() {
    const ticket = key();
    const revision = ++settingsGeneration;
    savedStatus.textContent = 'Loading saved model settings (optional). Discovery is available now.';
    modelStatus.textContent = 'Connect workspace models, choose a READY chat endpoint, confirm tool compatibility, then Apply.';
    try {
      const data = await request('/api/models/status', 'GET', undefined, modelContext());
      if (ticket !== key() || revision !== settingsGeneration) return;
      activeEndpoint = data.active_endpoint ?? ''; savedReady = data.inference_ready === true;
      routeVersion = data.route_version ?? null;
      savedStatus.textContent = `Saved model: ${activeEndpoint || 'none confirmed'}. This check does not test the model.`;
      syncApply(); renderSelection();
    } catch (error) {
      if (ticket === key() && revision === settingsGeneration) {
        savedReady = false; syncApply();
        savedStatus.textContent = `Saved settings unavailable. Discovery is still available. ${error.message}`;
      }
    }
  }
  window.orionWorkspaceModelBusy = () => modelBusy || Boolean(pendingSave());
  document.addEventListener('orion:agent-run', event => {
    const run = event.detail;
    if (run && ['queued', 'running', 'cancelling'].includes(run.status)) {
      if (runningModelId !== run.id) { runningModelId = run.id; runningEndpoint = ''; }
      const live = run.live_activity;
      if (live?.status === 'verified' && live.public_run_id === run.id) {
        const names = [...new Set([...(live.turns || []), ...(live.active_calls || [])]
          .map(call => call.endpoint).filter(name => typeof name === 'string'
            && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(name)))];
        if (names.length === 1) runningEndpoint = names[0];
      }
    } else if (run?.id === runningModelId && !['queued', 'running', 'cancelling'].includes(run.status)) {
      runningEndpoint = ''; runningModelId = '';
    }
    syncApply(); renderSelection();
  });
  new MutationObserver(() => { syncApply(); renderSelection(); }).observe($('page-agents'),
    {attributes: true, attributeFilter: ['data-agent-run-status']});
  document.addEventListener('orion:external-model-state', event => {
    if (event.detail?.refreshDatabricks) { resetModels(); modelState(); }
    else syncApply();
  });
  window.addEventListener('pagehide', () => clearInterval(progressTimer));
  const availability = $('agent-availability');
  const connectHint = 'Choose a ready chat endpoint above, confirm tool compatibility, and Apply. Then select a workflow and click Invoke agents to start a run.';
  function humanHint() {
    // This exact overview prefix is not a run error. Never touch form/run notices.
    if (availability.textContent.startsWith('Agent execution unavailable. ')) availability.textContent = connectHint;
  }
  new MutationObserver(humanHint).observe(availability, { childList: true, subtree: true, characterData: true });
  humanHint();

  const card = node('article', '', 'panel'); card.id = 'workspace-volume-panel';
  const identityNote = node('p', disclosure, 'small muted volume-identity-note');
  const volumeIntro = node('p', 'Browse workspace metadata and stage selected Informatica XML files into this project.', 'volume-intake-lead');
  const volumeLimits = node('p', 'Up to 10 files · 80 MiB per file · 160 MiB per batch', 'volume-intake-limits');
  const volumeEffect = node('p', 'No analysis is started during staging. Staging does not create an analysis job.', 'small muted volume-intake-effect');
  card.append(node('h2', 'Select from Databricks Volume'), identityNote, volumeIntro, volumeLimits, volumeEffect);
  const volumeConnect = button('connect-workspace-volume', 'Connect Databricks Volume'); card.append(volumeConnect);
  const selectors = {};
  for (const [kind, label] of [['catalog', 'Catalog'], ['schema', 'Schema'], ['volume', 'Volume']]) {
    const id = `workspace-volume-${kind}`, select = node('select', ''); select.id = id; select.disabled = true;
    const title = node('label', label); title.htmlFor = id;
    select.append(new Option(`Choose ${label.toLowerCase()}`, '')); selectors[kind] = select; card.append(title, select);
  }
  const location = node('p', 'No volume selected.', 'small muted'); location.id = 'workspace-volume-path';
  const back = button('workspace-volume-back', 'Back to parent folder'); back.disabled = true;
  const files = node('fieldset', ''); files.id = 'workspace-volume-files';
  const importButton = button('workspace-volume-import', 'Stage selected XML files'); importButton.className = 'primary'; importButton.disabled = true;
  const volumeStatus = node('p', 'Select a saved project, then connect to browse Databricks.', 'small muted');
  volumeStatus.id = 'workspace-volume-status'; volumeStatus.setAttribute('role', 'status'); volumeStatus.setAttribute('aria-live', 'polite');
  card.append(location, back, files, importButton, volumeStatus);
  $('page-projects').querySelector('.two-column').append(card);
  // Supersede only the obsolete Volume placeholder, not sample behavior or results.
  const sampleHint = $('project-sample-form').previousElementSibling;
  if (sampleHint?.textContent.startsWith('Databricks Volume selection is not connected yet.')) sampleHint.textContent = 'For workspace files, use the separate Select from Databricks Volume card.';
  const prefix = '/api/databricks/volumes';
  let directory = '', volumeBusy = false, volumeConnected = false, generation = 0;
  let volumeScope, volumeTicket;
  const selectedFiles = new Set();
  async function volumeRequest(path, method = 'GET', body) {
    const scope = volumeScope, ticket = volumeTicket;
    if (ticket !== generation || scope.project !== context().project) throw new Error('Project selection changed. Reconnect for the selected project.');
    const data = await request(path, method, body, scope);
    if (ticket !== generation || scope.project !== context().project) throw new Error('Project selection changed. Reconnect for the selected project.');
    return data;
  }
  function resetSelect(kind) {
    selectors[kind].replaceChildren(new Option(`Choose ${kind}`, '')); selectors[kind].disabled = true;
  }
  function clearFiles() {
    selectedFiles.clear(); directory = '';
    files.replaceChildren(node('legend', 'Volume folders and XML files'));
    location.textContent = 'No folder listing loaded.'; back.disabled = true; syncVolume();
  }
  function syncVolume() {
    const busy = volumeBusy || $('page-projects').dataset.intakeBusy === 'true';
    volumeConnect.disabled = busy || !context().project;
    for (const select of Object.values(selectors)) select.disabled = busy || select.options.length < 2;
    back.disabled = busy || !directory;
    importButton.disabled = busy || !volumeConnected || !context().project || !selectedFiles.size || selectedFiles.size > 10;
  }
  document.addEventListener('orion:intake-busy', syncVolume);
  function query() {
    return new URLSearchParams({ catalog: selectors.catalog.value, schema: selectors.schema.value,
      volume: selectors.volume.value, directory }).toString();
  }
  function limitNotice(data) { return data.truncated ? ` Listing capped at ${data.limit} raw entries; other entries may not be shown.` : ''; }
  async function names(kind) {
    const plural = { catalog: 'catalogs', schema: 'schemas', volume: 'volumes' }[kind];
    const data = await volumeRequest(`${prefix}/${plural}?${query()}`);
    resetSelect(kind);
    for (const item of data.items) {
      const source = [selectors.catalog.value, kind === 'volume' ? selectors.schema.value : '', item.name].filter(Boolean).join('.');
      selectors[kind].append(new Option(kind === 'catalog' ? item.name : source, item.name));
    }
    volumeStatus.textContent = `${data.items.length} ${plural} returned from Databricks.${limitNotice(data)}`;
  }
  async function listFiles() {
    const data = await volumeRequest(`${prefix}/files?${query()}`);
    clearFiles(); directory = data.directory;
    location.textContent = `${data.volume_path}${directory ? '/' + directory : ''}`;
    for (const item of data.items) {
      if (item.is_directory) {
        const open = button('', `Open folder: ${item.name}`);
        open.addEventListener('click', () => volumeAction(async () => { directory = item.path; await listFiles(); }));
        const row = node('div', '', 'button-row'); row.append(open); files.append(row);
      } else {
        const label = node('label', '', 'check-label'), input = node('input', ''); input.type = 'checkbox'; input.value = item.path;
        input.addEventListener('change', () => { if (input.checked) selectedFiles.add(item.path); else selectedFiles.delete(item.path); syncVolume(); });
        label.append(input, node('span', `${item.name} · ${item.size_bytes === null ? 'size not reported' : item.size_bytes + ' bytes'} · ${item.path}`)); files.append(label);
      }
    }
    volumeStatus.textContent = `${data.items.length} folders/XML files returned from Databricks.${limitNotice(data)} Select files, then click Stage selected XML files.`;
  }
  async function volumeAction(action) {
    if (volumeBusy) return;
    const ticket = ++generation, project = context().project;
    volumeScope = context(); volumeTicket = ticket;
    volumeBusy = true; syncVolume(); volumeStatus.textContent = 'Requesting Databricks metadata or staging result…';
    // Freeze only this card, including file/folder actions, during its request.
    files.disabled = true;
    try {
      if (!project) throw new Error('Select a saved project ID before browsing or staging Volume files.');
      await action();
    } catch (error) {
      if (ticket === generation) { clearFiles(); volumeStatus.textContent = error.message; }
    } finally {
      volumeBusy = false; files.disabled = false;
      if (project !== context().project || ticket !== generation) resetVolume();
      syncVolume();
    }
  }
  function resetVolume() {
    generation += 1; volumeConnected = false;
    Object.keys(selectors).forEach(resetSelect); clearFiles();
    volumeStatus.textContent = 'Select a saved project, then connect to browse Databricks.'; syncVolume();
  }
  volumeConnect.addEventListener('click', () => volumeAction(async () => {
    volumeConnected = false; Object.keys(selectors).forEach(resetSelect); clearFiles();
    const data = await volumeRequest(`${prefix}/connect`, 'POST', {});
    if (data.connected !== true) throw new Error('Databricks Volume authentication was not confirmed.');
    volumeConnected = true; await names('catalog');
  }));
  for (const [kind, next] of [['catalog', 'schema'], ['schema', 'volume'], ['volume', null]]) {
    selectors[kind].addEventListener('change', () => volumeAction(async () => {
      clearFiles();
      if (kind === 'catalog') { resetSelect('schema'); resetSelect('volume'); }
      if (kind === 'schema') resetSelect('volume');
      if (!selectors[kind].value) { volumeStatus.textContent = `Choose a ${kind} to continue.`; return; }
      if (next) await names(next); else await listFiles();
    }));
  }
  back.addEventListener('click', () => volumeAction(async () => {
    directory = directory.split('/').slice(0, -1).join('/'); await listFiles();
  }));
  importButton.addEventListener('click', () => {
    syncVolume(); if (importButton.disabled) return;
    const scope = context(), paths = [...selectedFiles];
    volumeAction(async () => {
      const data = await volumeRequest(`${prefix}/import`, 'POST', { project_id: scope.project,
        catalog: selectors.catalog.value, schema: selectors.schema.value, volume: selectors.volume.value,
        paths, consent: true });
      if (scope.project !== context().project) return;
      clearFiles();
      volumeStatus.textContent = `Staging response: ${data.status}. ${data.total} file(s). Analysis started: ${data.analysis_started === true ? 'yes' : 'no'}.`;
      for (const item of data.items) volumeStatus.append(node('span', ` ${item.name}: ${item.status}${item.reused ? ' (reused)' : ''}.`));
      $('refresh-projects').click();
    });
  });
  let lastContext = key();
  function contextChanged() {
    if (lastContext === key()) return;
    const previous = JSON.parse(lastContext); lastContext = key();
    resetModels(); workspaceContext(); modelState();
    if (previous.project !== context().project) resetVolume();
    syncVolume();
  }
  document.addEventListener('change', contextChanged);
  for (const id of ['project-select', 'project-package']) new MutationObserver(contextChanged).observe($(id), { childList: true, subtree: true, attributes: true });
  syncVolume(); workspaceContext(); modelState();
})();