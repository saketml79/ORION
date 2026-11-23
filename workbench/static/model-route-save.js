/* Nonsecret reconnect IDs only. Unknown acknowledgements are never replayed. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const STORAGE = 'orion.model-route-save.v1', API = '/api/hosted/operations';
  const running = new Map();
  function records() {
    const raw = sessionStorage.getItem(STORAGE);
    const value = raw ? JSON.parse(raw) : {};
    if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error('Invalid saved route operation IDs. Inspect recorded state before retrying.');
    return value;
  }
  const key = (scope, workspace) => JSON.stringify([workspace, scope.project || '', scope.package || '']);
  function pending(scope, workspace) { return records()[key(scope, workspace)] || null; }
  async function request(path, method, body, scope) {
    const headers = {Accept: 'application/json', 'X-Workbench-Request': '1'};
    if (scope.project) headers['X-Workbench-Project'] = scope.project;
    if (scope.package) headers['X-Workbench-Package'] = scope.package;
    if (body) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, {method, headers, credentials: 'same-origin', cache: 'no-store',
      ...(body ? {body: JSON.stringify(body)} : {})});
    let data;
    try { data = await response.json(); }
    catch { throw new Error(`HTTP ${response.status}: acknowledgement is unconfirmed, not an endpoint incompatibility.`); }
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${data.detail || data.error?.detail || 'Operation state unavailable'}`);
    return data;
  }
  async function save(options) {
    const {scope, workspace, body, onStatus = () => {}, isCurrent = () => true} = options;
    const ticket = key(scope, workspace);
    if (running.has(ticket)) return running.get(ticket);
    const work = (async () => {
      let record = pending(scope, workspace), first;
      if (!record) {
        if (!/^[a-f0-9]{64}$/.test(body.expected_route_version || '')) throw new Error('Load saved route settings before Apply. No save submitted.');
        record = {id: crypto.randomUUID().replaceAll('-', ''), endpoint: body.endpoint,
          previous_version: body.expected_route_version};
        const all = records(); all[ticket] = record;
        sessionStorage.setItem(STORAGE, JSON.stringify(all));
        onStatus({operation_id: record.id, status: 'submitting'});
        try {
          first = await request('/api/models/select', 'POST', {operation_id: record.id, ...body}, scope);
        } catch (error) {
          // A proxy failure can hide a durable acceptance. Observe this SAME ID
          // once, never submit a new operation or replay the mutation.
          onStatus({operation_id: record.id, status: 'reconciling'});
          try { first = await request(`${API}/${record.id}`, 'GET', null, scope); }
          catch { throw new Error(`Operation ${record.id}: ${error.message} Save status is not confirmed. Reconnect to this recorded operation; no save was replayed.`); }
        }
      }
      try {
        while (true) {
          if (!isCurrent()) throw new Error('Selection changed. Reconnect in the original context; no save was replayed.');
          const data = first || await request(`${API}/${record.id}`, 'GET', null, scope);
          first = null;
          if (data.operation_id !== record.id) throw new Error('Model save operation ID mismatch');
          onStatus(data);
          if (data.status === 'completed') {
            const result = data.result;
            if (!result || result.selected_endpoint !== record.endpoint ||
                result.project_id !== (scope.project || null) || result.package_hash !== (scope.package || null) ||
                result.previous_route_version !== record.previous_version ||
                !/^[a-f0-9]{64}$/.test(result.route_version || '') ||
                (result.settings_revision !== undefined
                  ? (!/^[a-f0-9]{32}$/.test(result.settings_revision || '') ||
                     result.settings_authority !== 'hosted-model-settings-v1' ||
                     result.parent_generation !== result.committed_generation ||
                     (result.committed_generation !== null && !/^[a-f0-9]{32}$/.test(result.committed_generation || '')))
                  : !/^[a-f0-9]{32}$/.test(result.committed_generation || ''))) throw new Error('Model save committed receipt scope/version mismatch');
            if (!isCurrent()) throw new Error('Selection changed before route receipt display');
            const all = records(); delete all[ticket]; sessionStorage.setItem(STORAGE, JSON.stringify(all));
            return result;
          }
          if (!['queued', 'running'].includes(data.status)) throw new Error(
            `Operation ${record.id}: ${data.status}. Completion is not confirmed. Inspect this operation and committed settings before any new save.`);
          await new Promise(resolve => setTimeout(resolve, 3000));
        }
      } catch (error) { throw new Error(`Operation ${record.id}: ${error.message}`); }
    })();
    running.set(ticket, work);
    try { return await work; } finally { running.delete(ticket); }
  }
  window.orionModelRouteSave = {save, pending};
})();