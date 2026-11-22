/* Standalone hosted intake. The parent owns its submit hook and existing spinner.
 * upload(form, files, projectId, {isCurrent, refresh}) returns a handled boolean.
 * submit(path, body, headers) returns the completed operation's public result.
 * resume(form, projectId, options) only reconnects recorded work. Chunk resumption
 * additionally requires a reselected file and the explicit Resume button.
 */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const UPLOADS = '/api/hosted/uploads', OPERATIONS = '/api/hosted/operations';
  const STORAGE = 'orion.hosted-intake.v1', CHUNK_BYTES = 4 * 1024 * 1024;
  const POLL_MS = 3000;
  const ID = /^[a-f0-9]{32}$/, SHA256 = /^[a-f0-9]{64}$/;
  const forms = new WeakMap();
  let hosted = false, probing = null, activity = null;
  const blockers = new Set(), inertRoots = new Map();
  const DISMISS = 'orion.hosted-notification-dismissed.v1';
  const COLLAPSED = 'orion.hosted-notification-collapsed.v1';
  let backdrop = null, previousFocus = null;

  function operationStateChanged() {
    document.dispatchEvent(new CustomEvent('orion:hosted-operation-state', {
      detail: {busy: blockers.size > 0, operationId: activity?.operationId || null,
        message: activity?.message?.textContent || ''},
    }));
  }

  // OLD presentation retained for rollback only; never invoked.
  function legacyBlockingState() {
    const blocked = blockers.size > 0;
    if (!backdrop) {
      backdrop = node('div'); backdrop.id = 'orion-hosted-operation-backdrop';
      backdrop.hidden = true;
      const show = node('button', 'Show operation progress'); show.type = 'button';
      show.addEventListener('click', () => {
        activity.dismissed = false; activity.element.hidden = false; blockingState(); activity.close.focus();
      });
      backdrop.append(show); document.body.append(backdrop);
    }
    if (blocked && !inertRoots.size) {
      previousFocus = document.activeElement;
      for (const root of document.querySelectorAll('.app-shell, .topbar, .skip-link')) {
        inertRoots.set(root, root.inert); root.inert = true;
        root.classList.add('hosted-operation-inert');
      }
      activity.close.focus();
    } else if (!blocked) {
      for (const [root, wasInert] of inertRoots) {
        root.inert = wasInert; root.classList.remove('hosted-operation-inert');
      }
      inertRoots.clear();
      if (previousFocus?.isConnected && !previousFocus.closest('[inert]')) previousFocus.focus();
      previousFocus = null;
    }
    backdrop.hidden = !blocked;
    backdrop.firstElementChild.hidden = !activity?.dismissed;
    if (activity) {
      const active = blocked || activity.element.getAttribute('aria-busy') === 'true';
      activity.closeIcon.textContent = active ? '−' : '×';
      activity.close.setAttribute('aria-label', active ? 'Minimize operation progress' : 'Close operation notification');
      activity.close.title = active
        ? 'Hide progress only. This does not cancel the active operation or unlock the workspace.' : 'Close the completed or failed notification.';
      activity.element.classList.toggle('hosted-operation-modal', blocked);
      const modalVisible = blocked && !activity.dismissed;
      activity.element.setAttribute('role', modalVisible ? 'dialog' : 'status');
      if (modalVisible) activity.element.setAttribute('aria-modal', 'true');
      else activity.element.removeAttribute('aria-modal');
      backdrop.setAttribute('role', blocked && activity.dismissed ? 'dialog' : 'presentation');
      if (blocked && activity.dismissed) {
        backdrop.setAttribute('aria-modal', 'true');
        backdrop.setAttribute('aria-label', 'Blocking operation still running');
      } else backdrop.removeAttribute('aria-modal');
    }
  }

  function blockingState() {
    if (!activity) return;
    // Release only ownership recorded by the legacy presentation, never all inert nodes.
    for (const [root, wasInert] of inertRoots) {
      if (root.classList.contains('hosted-operation-inert')) {
        if (root.getAttribute('aria-busy') !== 'true') root.inert = wasInert;
        root.classList.remove('hosted-operation-inert');
      }
    }
    inertRoots.clear(); previousFocus = null;
    const ownedBackdrop = backdrop || document.getElementById('orion-hosted-operation-backdrop');
    if (ownedBackdrop) {
      ownedBackdrop.hidden = true;
      for (const attribute of ['role', 'aria-modal', 'aria-label', 'aria-labelledby', 'aria-describedby']) ownedBackdrop.removeAttribute(attribute);
    }
    activity.element.classList.remove('hosted-operation-modal');
    activity.closeIcon.textContent = '×';
    activity.close.setAttribute('aria-label', 'Close operation notification');
    activity.close.title = 'Close activity details only. This does not cancel the operation.';
    activity.collapseIcon.textContent = activity.collapsed ? '+' : '−';
    activity.collapse.setAttribute('aria-label', activity.collapsed ? 'Expand operation progress' : 'Minimize operation progress');
    activity.collapse.setAttribute('aria-expanded', String(!activity.collapsed));
    activity.collapse.title = activity.collapsed ? 'Expand activity details.' : 'Collapse activity details. Work continues; this does not cancel it.';
    activity.element.hidden = activity.dismissed;
    activity.content.hidden = activity.collapsed || activity.dismissed;
    activity.element.setAttribute('role', 'status');
    activity.element.removeAttribute('aria-modal');
    const compact = activity.compact;
    compact.element.hidden = !activity.dismissed;
    compact.element.className = activity.element.className;
    compact.element.setAttribute('aria-busy', activity.element.getAttribute('aria-busy') || 'false');
    compact.indicator.hidden = activity.indicator.hidden;
    compact.message.textContent = activity.message.textContent;
    compact.elapsed.textContent = activity.elapsed.textContent;
    compact.phase.textContent = [
      !activity.transfer.bar.parentElement.hidden ? `${activity.transfer.caption.textContent}: ${activity.transfer.bar.value} / ${activity.transfer.bar.max} bytes` : '',
      !activity.processing.bar.parentElement.hidden ? activity.processing.caption.textContent : '',
    ].filter(Boolean).join(' · ');
  }
  function focusContainer() {
    // Activity lives on the page. No focus trap, backdrop, blur or shell inert.
    return null;
  }
  function focusable(container) {
    return Array.from(container.querySelectorAll('button, summary, [tabindex="0"]'))
      .filter(item => !item.disabled && item.getClientRects().length);
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && activity && !activity.dismissed && activity.element.contains(event.target)) {
      event.preventDefault(); activity.close.click(); return;
    }
    const container = focusContainer();
    if (!container) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      if (!activity.dismissed) activity.close.click();
      return;
    }
    if (event.key !== 'Tab') return;
    const controls = focusable(container), first = controls[0], last = controls.at(-1);
    if (!first) return;
    if (!container.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)
        || (!event.shiftKey && document.activeElement === last)) {
      event.preventDefault(); (event.shiftKey ? last : first).focus();
    }
  });
  document.addEventListener('focusin', event => {
    const container = focusContainer();
    if (container && !container.contains(event.target)) focusable(container)[0]?.focus();
  });

  function uuid() {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
  }
  function records() {
    try {
      const value = JSON.parse(sessionStorage.getItem(STORAGE) || '[]');
      return Array.isArray(value) ? value.filter(r => r && ID.test(r.id) && ID.test(r.project_id)) : [];
    } catch (_) { return []; }
  }
  function remember(record) {
    // Whitelist only reconnect metadata. Never persist bodies, files or headers.
    const safe = {};
    for (const key of ['id', 'project_id', 'name', 'size_bytes', 'stage_operation_id', 'analysis_operation_id', 'kind']) {
      if (record[key] !== undefined) safe[key] = record[key];
    }
    const all = records().filter(r => r.id !== safe.id);
    all.push(safe);
    try { sessionStorage.setItem(STORAGE, JSON.stringify(all)); }
    catch (_) { throw new Error('Cannot record reconnect IDs in session storage. No new request was submitted.'); }
  }
  function detail(data) {
    return typeof data?.detail === 'string' ? data.detail :
      typeof data?.error?.detail === 'string' ? data.error.detail : 'No endpoint detail returned.';
  }
  async function request(path, method = 'GET', body, headers = {}) {
    const controller = new AbortController();
    try {
      const response = await fetch(path, {
        method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {...(method === 'GET' ? {} : {'X-Workbench-Request': '1'}),
          ...(body === undefined ? {} : {'Content-Type': 'application/json'}), ...headers},
        ...(body === undefined ? {} : {body: JSON.stringify(body)}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(`${method} ${path}: HTTP ${response.status}. ${detail(data)}`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error(`${method} ${path}: interrupted. Completion is unconfirmed; reconnect before retrying.`);
      throw new Error(`${method} ${path}: ${error.message}`);
    }
  }
  async function probe() {
    if (hosted) return true;
    if (!probing) probing = request('/api/health').then(data => {
      hosted = data.authentication === 'databricks_obo';
      document.dispatchEvent(new CustomEvent('orion:health', {detail: data}));
      return hosted;
    }).finally(() => { probing = null; });
    // A health failure rejects, rather than silently falling back to a local POST.
    return probing;
  }
  const current = options => !options.isCurrent || options.isCurrent();
  function guard(options) {
    if (!current(options)) throw new Error('Selection changed. Work was not replayed. Reconnect in the original project to inspect recorded state.');
  }
  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }
  function activityFor() {
    if (!activity) {
      const element = node('aside'), messageNode = node('p'), indicator = node('span');
      element.id = 'orion-hosted-intake-activity';
      element.setAttribute('aria-label', 'Hosted intake activity across navigation');
      element.setAttribute('role', 'status');
      element.setAttribute('aria-live', 'polite');
      indicator.className = 'ui-icon hosted-intake-spinner';
      indicator.setAttribute('aria-hidden', 'true');
      indicator.textContent = '◌';
      const header = node('div'); header.className = 'hosted-activity-header';
      const heading = node('strong', 'Current request'); heading.id = 'hosted-activity-heading';
      const elapsed = node('span', ''); elapsed.className = 'small muted';
      elapsed.setAttribute('aria-live', 'off');
      messageNode.id = 'hosted-activity-message';
      element.setAttribute('aria-labelledby', heading.id);
      element.setAttribute('aria-describedby', messageNode.id);
      header.append(indicator, heading, elapsed); element.append(header);
      const content = node('div'); content.className = 'hosted-activity-content';
      content.append(messageNode); element.append(content);
      const reconnect = node('button', 'Reconnect to last recorded operation');
      reconnect.type = 'button'; reconnect.hidden = true;
      reconnect.addEventListener('click', () => reconnectOperation());
      const collapse = node('button'), collapseIcon = node('span', '−');
      collapse.className = 'hosted-activity-window-control'; collapse.type = 'button';
      collapseIcon.setAttribute('aria-hidden', 'true'); collapse.append(collapseIcon);
      collapse.setAttribute('aria-controls', 'hosted-activity-content');
      collapse.addEventListener('click', () => {
        activity.collapsed = !activity.collapsed; blockingState();
        try { sessionStorage.setItem(COLLAPSED, String(activity.collapsed)); } catch (_) { /* Presentation only. */ }
      });
      const close = node('button'), closeIcon = node('span', '×');
      close.className = 'hosted-activity-window-control';
      closeIcon.setAttribute('aria-hidden', 'true'); close.append(closeIcon);
      close.type = 'button'; close.setAttribute('aria-label', 'Close operation notification');
      close.title = 'Close activity details only. This does not cancel the operation.';
      close.addEventListener('click', () => {
        activity.dismissed = true; activity.content.hidden = true;
        try { sessionStorage.setItem(DISMISS, activity.operationId || ''); } catch (_) { /* Presentation only. */ }
        blockingState();
        activity.compact.restore.focus({preventScroll: true});
      });
      content.id = 'hosted-activity-content';
      const controls = node('div'); controls.className = 'hosted-activity-controls';
      controls.append(collapse, close); header.append(controls);
      const compactElement = node('aside'), compactIndicator = indicator.cloneNode(true);
      compactElement.id = 'orion-hosted-intake-summary'; compactElement.hidden = true;
      compactElement.setAttribute('role', 'status'); compactElement.setAttribute('aria-live', 'polite');
      compactElement.setAttribute('aria-label', 'Current request summary');
      const compactText = node('div'), compactMessage = node('p'), compactPhase = node('p'), compactElapsed = node('span');
      compactPhase.className = 'small muted'; compactElapsed.className = 'small muted';
      compactElapsed.setAttribute('aria-live', 'off');
      const restore = node('button', 'Show activity details'); restore.type = 'button';
      restore.title = 'Restore activity details. No operation is replayed.';
      restore.setAttribute('aria-controls', element.id);
      restore.addEventListener('click', () => {
        activity.dismissed = false; activity.collapsed = false;
        try { sessionStorage.removeItem(DISMISS); } catch (_) { /* Presentation only. */ }
        try { sessionStorage.setItem(COLLAPSED, 'false'); } catch (_) { /* Presentation only. */ }
        blockingState(); close.focus({preventScroll: true});
      });
      compactText.append(compactMessage, compactPhase, compactElapsed);
      compactElement.append(compactIndicator, compactText, restore);
      const details = node('details'), summary = node('summary', 'Operation details');
      const identifier = node('p'); identifier.className = 'small muted';
      const phase = node('p', 'Status: not started'); phase.className = 'small muted';
      const progressDetail = node('p', 'Progress: not reported'); progressDetail.className = 'small muted';
      const process = node('p', 'Worker process: not reported'); process.className = 'small muted';
      details.append(summary, identifier, phase, progressDetail, process);
      let collapsed = true;
      try { collapsed = sessionStorage.getItem(COLLAPSED) !== 'false'; } catch (_) { /* Default minimized. */ }
      activity = {element, indicator, reconnect, close, closeIcon, elapsed, dismissed: false, message: messageNode,
        identifier, phase, progressDetail, process, content, collapse, collapseIcon, collapsed,
        compact: {element: compactElement, indicator: compactIndicator, message: compactMessage, phase: compactPhase, elapsed: compactElapsed, restore},
        transfer: meter(content, 'Active hosted file: durable acknowledged bytes'),
        processing: meter(content, 'Hosted processing: current phase (count not reported)')};
      content.append(reconnect, details);
      const disclosure = node('details');
      disclosure.id = 'hosted-operation-disclosure';
      disclosure.className = 'hosted-operation-disclosure';
      disclosure.append(node('summary', 'View or reconnect to hosted operation'), element, compactElement);
      // The purple status ribbon already communicates activity across tabs.
      // Keep detailed progress and explicit recovery available only in Logs.
      const logs = document.getElementById('page-logs');
      const logsHeading = logs?.querySelector('.page-heading');
      if (logsHeading) logsHeading.after(disclosure);
      else if (logs) logs.prepend(disclosure);
    }
    return activity;
  }
  function beginActivity(blocking = true, operationId = null) {
    if (blocking && blockers.size) throw new Error('An operation is already active. Inspect its recorded progress before starting another. No request was submitted.');
    const panel = activityFor();
    panel.started = performance.now();
    clearInterval(panel.clock);
    const tick = () => {
      panel.elapsed.textContent = `${Math.floor((performance.now() - panel.started) / 1000)}s elapsed`;
      panel.compact.elapsed.textContent = panel.elapsed.textContent;
    };
    tick(); panel.clock = setInterval(tick, 1000);
    panel.operationId = operationId;
    panel.identifier.textContent = operationId ? `Operation ID: ${operationId}` : 'File transfer, operation ID not yet assigned.';
    panel.phase.textContent = 'Status: running';
    panel.progressDetail.textContent = 'Progress: waiting for the first status update';
    panel.process.textContent = 'Worker process: pending';
    panel.content.hidden = panel.collapsed;
    panel.dismissed = false; panel.element.hidden = false;
    const token = {};
    if (blocking) blockers.add(token);
    blockingState();
    operationStateChanged();
    return () => {
      blockers.delete(token);
      if (!blockers.size) { tick(); clearInterval(panel.clock); }
      blockingState(); operationStateChanged();
    };
  }
  function activityStatus(message, terminal = false, upload = true, state = '') {
    const panel = activityFor();
    if (terminal && blockers.size > 1) {
      message = 'Another blocking operation is still running. Progress remains available.';
      terminal = false;
      state = '';
    }
    panel.element.hidden = panel.dismissed;
    panel.element.className = terminal ? 'activity-status' : 'activity-status is-active is-busy';
    panel.element.setAttribute('aria-busy', String(!terminal));
    panel.indicator.hidden = terminal;
    panel.message.textContent = `${!terminal && state ? `${state}. ` : ''}${message.replace(/\b[a-f0-9]{32}\b/g, 'recorded operation')}`;
    panel.identifier.textContent = panel.operationId ? `Operation ID: ${panel.operationId}.` : 'File transfer, operation ID not yet assigned.';
    panel.transfer.bar.parentElement.hidden = !upload;
    panel.processing.bar.parentElement.hidden = terminal;
    panel.processing.bar.removeAttribute('value');
    panel.processing.caption.textContent = 'Hosted processing: current phase (count not reported)';
    panel.processing.bar.setAttribute('aria-label', panel.processing.caption.textContent);
    panel.reconnect.hidden = !lastOperation();
    blockingState();
    operationStateChanged();
  }
  function announce(view, message, terminal = false, state = '') {
    view.summary.textContent = message;
    activityStatus(`Hosted XML intake: ${message}`, terminal, true, state);
  }
  function viewFor(form, projectId, options) {
    let view = forms.get(form);
    if (!view) {
      const section = node('section');
      section.className = 'hosted-intake-status';
      section.setAttribute('role', 'status');
      section.setAttribute('aria-live', 'polite');
      section.setAttribute('aria-label', 'Hosted XML intake');
      const summary = node('p'), list = node('div');
      const reconnect = node('button', 'Reconnect to recorded operation');
      reconnect.type = 'button';
      section.append(summary, list, reconnect);
      form.after(section);
      view = {section, summary, list, reconnect, busy: false, projectId, options};
      forms.set(form, view);
      reconnect.addEventListener('click', () => {
        resume(form, view.projectId, view.options).catch(error => announce(view, error.message));
      });
    }
    view.projectId = projectId; view.options = options;
    view.reconnect.hidden = !records().some(r => r.project_id === projectId);
    return view;
  }
  function meter(parent, label) {
    const container = node('div'), caption = node('span', label), bar = node('progress');
    bar.setAttribute('aria-label', label);
    container.append(caption, bar); parent.append(container);
    return {bar, caption};
  }
  function rowFor(view, file) {
    const row = node('article'), title = node('strong', file.name), message = node('p', 'Waiting');
    row.append(title, message);
    const durable = meter(row, 'Transfer: durable server-acknowledged bytes');
    durable.bar.max = file.size; durable.bar.value = 0;
    const transport = meter(row, 'Transport: bytes sent, not persisted');
    transport.bar.max = file.size; transport.bar.value = 0;
    const stage = meter(row, 'Staging: progress unknown');
    const analysis = meter(row, 'Analysis: progress unknown');
    view.list.append(row);
    announce(view, `${file.name}: waiting for server state.`, false, 'Waiting');
    activity.transfer.bar.max = file.size || 1; activity.transfer.bar.value = 0;
    return {row, message, durable, transport, stage, analysis, view};
  }
  function receipt(state, data, file, requireHashes = false) {
    if (data.id !== state.id || data.project_id !== state.project_id || data.name !== file.name ||
        data.size_bytes !== file.size || !Number.isSafeInteger(data.chunk_bytes) ||
        data.chunk_bytes < 1 || data.chunk_bytes > CHUNK_BYTES ||
        data.total_chunks !== Math.ceil(file.size / data.chunk_bytes) || !Array.isArray(data.chunks)) {
      throw new Error('Upload status metadata does not match the selected file.');
    }
    const chunks = new Map();
    for (const item of data.chunks) {
      const size = Math.min(data.chunk_bytes, file.size - item.index * data.chunk_bytes);
      if (!Number.isInteger(item.index) || item.index < 0 || item.index >= data.total_chunks ||
          chunks.has(item.index) || item.size_bytes !== size) throw new Error('Invalid durable chunk receipt.');
        if (requireHashes && (typeof item.sha256 !== 'string' || !SHA256.test(item.sha256))) throw new Error('Invalid durable chunk SHA-256 receipt. Resume blocked.');
      chunks.set(item.index, item.size_bytes);
    }
    if (data.durable_bytes !== [...chunks.values()].reduce((a, b) => a + b, 0)) throw new Error('Durable byte acknowledgement is inconsistent.');
    return {data, chunks};
  }
  function durable(row, status) {
    const bytes = status.data.durable_bytes;
    row.durable.bar.value = bytes;
    row.durable.caption.textContent = `Transfer: ${bytes} / ${status.data.size_bytes} durable server-acknowledged bytes`;
    activity.transfer.bar.max = status.data.size_bytes; activity.transfer.bar.value = bytes;
    announce(row.view, `${status.data.name}: ${bytes} / ${status.data.size_bytes} durable bytes.`, false, 'Running');
  }
  async function sendChunk(path, blob, row, sentBefore, options) {
    const headers = {};
    if (crypto.subtle) {
      // Only a bounded slice is materialized, never File.arrayBuffer().
      const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
      headers['X-Chunk-SHA256'] = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    }
    guard(options);
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('PUT', path); xhr.timeout = 0;
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.setRequestHeader('X-Workbench-Request', '1');
      for (const [key, value] of Object.entries(headers)) xhr.setRequestHeader(key, value);
      xhr.upload.onprogress = event => {
        const sent = sentBefore + Math.min(blob.size, event.loaded);
        row.transport.bar.value = sent;
        row.transport.caption.textContent = `Transport: ${sent} bytes sent, not persisted`;
      };
      xhr.onload = () => {
        let data;
        try { data = JSON.parse(xhr.responseText); }
        catch (_) { reject(new Error(`PUT ${path}: invalid acknowledgement. Inspect upload status.`)); return; }
        if (xhr.status < 200 || xhr.status >= 300) reject(new Error(`PUT ${path}: HTTP ${xhr.status}. ${detail(data)}`));
        else resolve(data);
      };
      xhr.onerror = () => reject(new Error(`PUT ${path}: connection failed. Durable receipt is unconfirmed.`));
      xhr.ontimeout = () => reject(new Error(`PUT ${path}: transport timed out. Inspect durable receipts before resuming.`));
      xhr.onabort = () => reject(new Error(`PUT ${path}: transfer stopped. Inspect durable receipts before resuming.`));
      xhr.send(blob);
    });
  }
  async function poll(id, options, onStatus, initial) {
    let data = initial;
    while (true) {
      guard(options);
      if (!data) data = await request(`${OPERATIONS}/${id}`);
      guard(options);
      if (data.operation_id !== id) throw new Error('Operation ID acknowledgement mismatch.');
      onStatus?.(data);
      if (data.status === 'completed') return data.result;
      if (['failed', 'authentication_required', 'interrupted'].includes(data.status)) {
        throw new Error(`Operation ${id}: ${data.status}. ${data.recovery || detail(data)} No automatic replay.`);
      }
      if (!['queued', 'running'].includes(data.status)) throw new Error(`Operation ${id}: unknown status; completion is unconfirmed.`);
      await new Promise(resolve => setTimeout(resolve, POLL_MS));
      data = null;
    }
  }
  async function operation(path, body, headers, record, field, options, onStatus) {
    guard(options);
    // Record the ID BEFORE POST, including a request whose response may be lost.
    const id = record[field] || uuid();
    record[field] = id; remember(record);
    const data = await request(OPERATIONS, 'POST', {id, method: 'POST', path, query: '', body}, headers);
    return poll(id, options, onStatus, data);
  }
  async function submit(path, body = {}, headers = {}, recordedId = null) {
    if (!await probe()) return false;
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin || url.search || url.hash) throw new Error('Hosted operation requires a same-origin path without query or fragment.');
    const project = ID.test(headers['X-Workbench-Project'] || '') ? headers['X-Workbench-Project'] :
      url.pathname.match(/^\/api\/projects\/([a-f0-9]{32})\/analyze$/)?.[1];
    if (recordedId !== null && (!ID.test(recordedId)
      || !/^\/api\/cohorts\/[a-f0-9]{32}\/workflows\/[a-f0-9]{64}\/design$/.test(url.pathname)
      || body.operation_id !== recordedId)) throw new Error('Invalid saved cohort operation ID.');
    const id = recordedId || uuid();
    // Non-project operations still retain an ID for manual status inspection.
    const record = {id, project_id: project || '00000000000000000000000000000000'};
    record.stage_operation_id = id;
    const release = beginActivity(true, id);
    activityFor().reconnect.disabled = true;
    activityStatus('Operation: submitting to the background worker.', false, false, 'Waiting');
    try {
      const result = await operation(url.pathname, body, headers, record, 'stage_operation_id', {}, operationStatus(id, url.pathname));
      activityStatus(`Operation completed. Returned result: ${resultText(result)}.`, true, false);
      return result;
    } catch (error) {
      activityStatus(`Operation ${id}: ${error.message} Reconnect to inspect recorded state; no automatic replay.`, true, false);
      throw error;
    } finally { activity.reconnect.disabled = false; release(); }
  }
  function lastOperation() {
    const all = records().filter(record => record.kind !== 'read' && ID.test(record.stage_operation_id || ''));
    return all.find(record => record.stage_operation_id === activity?.operationId)
      || all.filter(record => !record.name).at(-1);
  }
  function operationStatus(id, path = '') {
    return data => {
      if (activity?.operationId !== id) return;
      const live = data.live_agent;
      if (path === '/api/agents/run' && live?.state === 'accepted_in_process_not_committed'
          && ID.test(live.run_id || '') && ID.test(live.project_id || '')
          && SHA256.test(live.package_hash || '') && SHA256.test(live.workflow_id || '')) {
        document.dispatchEvent(new CustomEvent('orion:hosted-agent-accepted', {detail: {...live, operation_id: id}}));
      }
      activity.phase.textContent = `Status: ${data.status || 'unknown'} · stage: ${data.progress?.stage || 'not reported'}`;
      const {completed, total, unit} = data.progress || {};
      activity.progressDetail.textContent = Number.isInteger(completed) && Number.isInteger(total)
        ? `Progress: ${completed} of ${total}${unit ? ` ${unit}` : ''}`
        : 'Progress: count not reported by the service';
      activity.process.textContent = `Worker process: ${data.process_id || 'not reported'}`;
      const preparingAgent = path === '/api/agents/run' && ['provisioning', 'metadata_restore', 'artifact_files', 'audit_restore', 'preparing_package'].includes(data.progress?.stage);
      const explanation = preparingAgent ? ' Workspace setup is still in progress; the workflow agent has not started yet.' : '';
      const label = live?.state === 'accepted_in_process_not_committed' && path === '/api/agents/run'
        ? 'Agent run accepted in the active app session; Delta commit is pending' : progressText(data);
      activityStatus(`${label}.${explanation}`, false, false,
        data.status === 'queued' ? 'Waiting' : data.status === 'running' ? 'Running' : '');
      const measured = Number.isInteger(completed) && Number.isInteger(total) && total > 0 && completed >= 0 && completed <= total;
      activity.processing.caption.textContent = `${label} (current phase only${measured ? '' : '; count not reported'})`;
      activity.processing.bar.setAttribute('aria-label', activity.processing.caption.textContent);
      if (measured) {
        activity.processing.bar.max = total; activity.processing.bar.value = completed;
      }
      blockingState();
    };
  }
  const progressStages = Object.freeze({
    queued: 'Accepted; waiting for an available operation worker', running: 'Processing the selected request', starting: 'Starting the selected request',
    audit_restore: 'Restoring recorded activity',
    provisioning: 'Opening workspace storage', metadata_restore: 'Preparing saved workspace state',
    reading_history: 'Loading saved agent history metadata',
    artifact_files: 'Preparing saved workspace files', verifying_artifact: 'Verifying package checksum',
    decoding_nodes: 'Reading canonical package nodes', decoding_edges: 'Reading canonical package edges',
    index_cache: 'Reusing verified package index', indexing_nodes: 'Indexing package nodes',
    indexing_edges: 'Indexing package links', preparing_package: 'Preparing package view',
    querying_inventory: 'Querying requested package view', building_proposals: 'Building proposal candidates',
    responding: 'Returning verified result',
    committed: 'Completed', failed: 'Failed', authentication_required: 'Authentication required',
    interrupted: 'Interrupted; no automatic replay',
    route_validate: 'Validate endpoint and saved settings', route_commit: 'Save scoped endpoint setting',
    route_readback: 'Confirm saved setting',
  });
  function progressText(operationRecord) {
    if (operationRecord?.status === 'failed' && operationRecord?.observation?.observation === 'committed_with_ack_failure')
      return 'Analysis commit independently verified; original acknowledgement failed. Do not replay the rebuild.';
    const progress = operationRecord?.progress || {};
    const stage = progressStages[progress.stage] || progressStages[operationRecord?.status] || 'Workspace processing';
    const done = progress.completed, total = progress.total, unit = progress.unit;
    const measured = Number.isInteger(done) && Number.isInteger(total) && total > 0 && done >= 0 && done <= total;
    return `${stage}${measured ? ` · ${new Intl.NumberFormat().format(done)} of ${new Intl.NumberFormat().format(total)} ${unit || 'items'}` : ''}`;
  }
  function read(path, headers = {}, onStatus = () => {}, options = {}) {
    if (!window.orionScopedReads) return readOnce(path, headers, onStatus, options);
    const capturedHeaders = {...headers};
    return window.orionScopedReads.read(path, capturedHeaders,
      // The owner belongs to every subscriber, not the originating view. Only
      // its shared signal can end polling; each view retains its own guard.
      (signal, report) => readOnce(path, capturedHeaders, report, {...options,
        isCurrent: () => !signal.aborted}),
      {signal: options.signal, onStatus: value => { if (current(options)) onStatus(value); }, transport: 'hosted'})
      .then(result => { guard(options); return result; });
  }
  async function readOnce(path, headers = {}, onStatus = () => {}, options = {}) {
    if (!await probe()) throw new Error('Hosted background reads require the authenticated Databricks App service.');
    const url = new URL(path, location.origin);
    if (url.origin !== location.origin || url.hash || url.pathname.startsWith('/api/hosted/operations'))
      throw new Error('Background read requires an allowed same-origin API path.');
    const operationId = uuid();
    const record = {id: operationId, project_id: ID.test(headers['X-Workbench-Project'] || '')
      ? headers['X-Workbench-Project'] : '00000000000000000000000000000000',
      stage_operation_id: operationId, kind: 'read'};
    remember(record);
    // Observations never own the mutation panel, focus, backdrop or blockers.
    // Keep polling the durable operation even after its originating view leaves.
    onStatus({operation_id: operationId, status: 'queued'});
    try {
      const accepted = await request(OPERATIONS, 'POST', {
        id: operationId, method: 'GET', path: url.pathname,
        query: url.searchParams.toString(), body: {},
      }, headers);
      if (accepted.operation_id !== operationId) throw new Error('Background read operation ID mismatch.');
      const result = await poll(operationId, options, onStatus, accepted);
      return result;
    } catch (error) {
      throw new Error(`Read operation ${operationId}: ${error.message} Inspect this recorded operation before retrying; no request was replayed.`);
    }
  }
  async function reconnectOperation() {
    const record = lastOperation(), panel = activityFor();
    if (!record || panel.reconnect.disabled || blockers.size) return;
    const id = record.stage_operation_id;
    const release = beginActivity(true, id);
    panel.reconnect.disabled = true;
    activityStatus('Operation: reconnecting to recorded state.', false, false, 'Connecting');
    try {
      const result = await poll(id, {}, operationStatus(id));
      activityStatus(`Operation ${id} committed. Returned result: ${resultText(result)}. No operation was replayed.`, true, false);
    } catch (error) {
      activityStatus(`Operation ${id}: ${error.message} No operation was replayed.`, true, false);
    } finally { panel.reconnect.disabled = false; release(); }
  }
  function resultText(result) {
    const items = Array.isArray(result?.items) ? result.items : [];
    return [result?.status || 'operation completed', ...items.map(item =>
      `${item.name || 'Package'}: ${item.status || 'unknown'}${item.error ? ` (${typeof item.error === 'string' ? item.error : 'error recorded'})` : ''}`)].join('; ');
  }
  async function finish(record, row, options, reconnect = false) {
    const status = (meterRow, label) => data => {
      meterRow.bar.removeAttribute('value');
      meterRow.caption.textContent = `${label}: ${data.status}, progress unknown`;
      announce(row.view, `${record.name}: ${label.toLowerCase()} ${data.status}, progress unknown.`, false,
        data.status === 'queued' ? 'Waiting' : data.status === 'running' ? 'Running' : '');
    };
    let staged;
    if (record.stage_operation_id) {
      staged = await poll(record.stage_operation_id, options, status(row.stage, 'Staging'));
    } else {
      if (reconnect) throw new Error('No recorded finalize operation. Reselect the same file and choose Resume selected file.');
      staged = await operation(`${UPLOADS}/${record.id}/finalize`, {}, {}, record,
        'stage_operation_id', options, status(row.stage, 'Staging'));
    }
    row.stage.caption.textContent = `Staging operation committed: ${resultText(staged)}`;
    if (['failed', 'partial'].includes(staged?.status)) throw new Error(`Staging did not fully succeed: ${resultText(staged)}. Analysis was not submitted.`);
    guard(options);
    if (options.refresh) await options.refresh(record.project_id, staged);
    guard(options);
    let analyzed;
    if (record.analysis_operation_id) {
      analyzed = await poll(record.analysis_operation_id, options, status(row.analysis, 'Analysis'));
    } else {
      // Reconnect is observational: it never submits a missing analysis operation.
      if (reconnect) {
        row.message.textContent = 'Staging committed. No analysis operation recorded. Use the explicit project Analyze action; no automatic replay.';
        return;
      }
      analyzed = await operation(`/api/projects/${record.project_id}/analyze`, {}, {}, record,
        'analysis_operation_id', options, status(row.analysis, 'Analysis'));
    }
    row.analysis.caption.textContent = `Analysis operation: ${resultText(analyzed)}`;
    row.message.textContent = `Recorded result: ${resultText(analyzed)}`;
    guard(options);
    if (options.refresh) await options.refresh(record.project_id, analyzed);
  }
  async function transfer(file, record, row, options, existing = false) {
    guard(options);
    if (record.stage_operation_id) return finish(record, row, options, true);
    let status = receipt(record, existing ? await request(`${UPLOADS}/${record.id}`) :
      await request(UPLOADS, 'POST', {id: record.id, project_id: record.project_id, name: file.name, size_bytes: file.size}), file, existing);
    durable(row, status);
    // Verify ALL existing slices before any PUT, even if a missing chunk comes
    // first. Otherwise a later mismatch could already have produced a hybrid.
    if (existing && status.chunks.size) {
      if (!crypto.subtle) throw new Error('SHA-256 verification requires crypto.subtle. Resume blocked; no existing chunks were skipped.');
      for (const chunk of status.data.chunks) {
        guard(options);
        row.message.textContent = `Verifying existing chunk ${chunk.index + 1} of ${status.data.total_chunks}`;
        const start = chunk.index * status.data.chunk_bytes;
        const digest = await crypto.subtle.digest('SHA-256',
          await file.slice(start, Math.min(file.size, start + status.data.chunk_bytes)).arrayBuffer());
        guard(options);
        const hash = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
        if (hash !== chunk.sha256) throw new Error(`Selected file content does not match durable chunk ${chunk.index + 1}. Resume blocked; no chunks were uploaded or finalized.`);
      }
    }
    for (let index = 0; index < status.data.total_chunks; index++) {
      guard(options);
      if (status.chunks.has(index)) continue;
      row.message.textContent = `Transferring chunk ${index + 1} of ${status.data.total_chunks}`;
      const start = index * status.data.chunk_bytes;
      const blob = file.slice(start, Math.min(file.size, start + status.data.chunk_bytes));
      try {
        const ack = await sendChunk(`${UPLOADS}/${record.id}/chunks/${index}`, blob, row, start, options);
        if (ack.index !== index || ack.size_bytes !== blob.size) throw new Error('Invalid chunk acknowledgement.');
        status.chunks.set(index, ack.size_bytes);
        status.data.durable_bytes = [...status.chunks.values()].reduce((a, b) => a + b, 0);
        durable(row, status);
      } catch (error) {
        // A failed PUT may already be durable. Observe once, never auto-retry.
        try { status = receipt(record, await request(`${UPLOADS}/${record.id}`), file); durable(row, status); }
        catch (recovery) { throw new Error(`${error.message} Status recovery failed: ${recovery.message} Reselect the same file to resume explicitly.`); }
        throw new Error(`${error.message} Server records ${status.data.durable_bytes} durable bytes. Reselect the same file and choose Resume selected file.`);
      }
    }
    guard(options);
    status = receipt(record, await request(`${UPLOADS}/${record.id}/complete`, 'POST'), file);
    durable(row, status);
    if (!status.data.ready_for_finalize || status.data.durable_bytes !== file.size) throw new Error('Server has not confirmed readiness for staging.');
    await finish(record, row, options);
  }
  function resumeButton(view, file, record, row, options) {
    const button = node('button', 'Resume selected file'); button.type = 'button';
    row.row.append(button);
    button.addEventListener('click', async () => {
      if (view.busy) return;
      const release = beginActivity();
      view.busy = true; button.disabled = true;
      try { await transfer(file, record, row, options, true); }
      catch (error) { row.message.textContent = error.message; }
      finally { view.busy = false; button.disabled = false; announce(view, 'Resume attempt finished. Inspect the recorded per-file outcome.', true); release(); }
    });
  }
  async function upload(form, files, projectId, options = {}) {
    if (!await probe()) return false;
    const view = viewFor(form, projectId, options);
    if (view.busy) return true;
    if (!ID.test(projectId)) { announce(view, 'Select a project before uploading.'); return true; }
    files = Array.from(files);
    if (!files.length || files.length > 10) { announce(view, 'Choose 1 to 10 XML files.'); return true; }
    const release = beginActivity();
    view.busy = true; view.list.replaceChildren();
    announce(view, 'Processing files sequentially. Byte limits are enforced by the hosted server.');
    try {
      for (const file of files) {
        const row = rowFor(view, file);
        try {
          guard(options);
          if (!/\.xml$/i.test(file.name) || !Number.isSafeInteger(file.size) || file.size < 1) throw new Error('Choose a nonempty XML file.');
          const matches = records().filter(r => r.project_id === projectId && r.name === file.name && r.size_bytes === file.size);
          if (matches.length) {
            row.message.textContent = 'Recorded upload found. Verify the reselected file is identical, then resume explicitly. Names and sizes alone do not prove content identity.';
            for (const record of matches) resumeButton(view, file, record, row, options);
            continue;
          }
          const record = {id: uuid(), project_id: projectId, name: file.name, size_bytes: file.size};
          remember(record);
          await transfer(file, record, row, options);
        } catch (error) { row.message.textContent = error.message; }
      }
    } finally {
      view.busy = false; view.reconnect.hidden = false;
      announce(view, 'Batch finished or paused. Inspect every per-file outcome below; completion is not assumed.', true);
      release();
    }
    return true;
  }
  async function resume(form, projectId, options = {}) {
    if (!await probe()) return false;
    const view = viewFor(form, projectId, options);
    if (view.busy) return true;
    const release = beginActivity();
    view.busy = true; view.list.replaceChildren();
    try {
      for (const record of records().filter(r => r.project_id === projectId && r.kind !== 'read')) {
        const row = rowFor(view, {name: record.name || `Operation ${record.stage_operation_id}`, size: record.size_bytes || 1});
        try {
          guard(options);
          if (record.name) await finish(record, row, options, true);
          else {
            const result = await poll(record.stage_operation_id, options, data => { row.message.textContent = `Operation: ${data.status}, progress unknown`; });
            row.message.textContent = resultText(result);
            guard(options); if (options.refresh) await options.refresh(projectId, result);
          }
        } catch (error) { row.message.textContent = error.message; }
      }
    } finally { view.busy = false; announce(view, 'Reconnect finished or paused. No operation was replayed. Inspect per-file outcomes.', true); release(); }
    return true;
  }
  window.orionHostedIntake = Object.freeze({probe, upload, submit, read, resume,
    hasActiveOperation: () => blockers.size > 0});
  const recorded = lastOperation();
  if (recorded) {
    activityFor().operationId = recorded.stage_operation_id;
    try { activityFor().dismissed = sessionStorage.getItem(DISMISS) === recorded.stage_operation_id; } catch (_) { /* Presentation only. */ }
    activityStatus('Recorded operation available. Reconnect to inspect; no automatic replay.', true, false);
  }
})();