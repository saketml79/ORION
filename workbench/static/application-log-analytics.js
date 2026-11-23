(() => {
  'use strict';
  const page = document.getElementById('page-application-log-analytics');
  const root = document.getElementById('application-log-analytics');
  if (!page || !root) return;
  root.innerHTML = `
    <div class="ala-boundary"><strong>Shared process, not individual usage.</strong> CPU, memory and I/O include all work in this app process and are visible to anyone with app access. Local access is not user-authenticated. They do not identify a user, include other processes, or describe the whole fleet.</div>
    <div class="ala-toolbar"><div><strong>Live process measurements</strong><p>Sampled every 10 seconds only while this page is visible. History stays in this browser and resets on reload or process change.</p></div><button class="secondary compact" type="button" id="ala-sample">Sample now</button></div>
    <p id="ala-status" class="small muted" role="status" aria-live="polite">Waiting for a sample.</p>
    <div class="ala-grid">
      <article class="panel ala-card"><h2>CPU</h2><strong id="ala-cpu" class="ala-value">Awaiting two samples</strong><p>Process CPU over the interval. 100% is one CPU core; multiple cores can exceed 100%.</p><div id="ala-cpu-chart"></div></article>
      <article class="panel ala-card"><h2>Resident memory</h2><strong id="ala-memory" class="ala-value">Unavailable</strong><p>Current process working set / resident memory, not total machine memory.</p><div id="ala-memory-chart"></div></article>
      <article class="panel ala-card"><h2>Process I/O</h2><strong id="ala-io" class="ala-value">Awaiting two samples</strong><p id="ala-io-source">Counter source not yet available.</p><div id="ala-io-chart"></div></article>
    </div>
    <article class="panel ala-logs"><div class="ala-toolbar"><div><h2>Recent recorded events</h2><p>Read the newest 50 audit rows for the selected project and XML, or the explicitly selected legacy workspace. Counts below describe only returned rows, not all app traffic. This read may take time in hosted mode.</p></div><button id="ala-load-logs" class="secondary" type="button">Read recent log sample</button></div><p id="ala-log-status" role="status" aria-live="polite">No log sample requested.</p><div id="ala-log-chart"></div><p class="small muted">Use the existing Logs page to inspect event details and filters.</p></article>
    <article class="panel ala-logs"><div class="ala-toolbar"><div><h2>Model endpoint calls for one saved agent run</h2><p>Choose a saved run to count only its verified recorded analysis-agent turns. This excludes the separate writer and is not an app-wide or fleet-wide counter. Reading hosted history may take time.</p></div><button id="ala-runs-load" class="secondary" type="button">Load saved runs</button></div><label for="ala-run">Saved agent run</label><select id="ala-run" disabled><option value="">Load saved runs first</option></select><button id="ala-run-read" class="secondary compact" type="button" disabled>Read selected run calls</button><p id="ala-run-status" role="status" aria-live="polite">No saved run selected. Application-wide endpoint counts are not available.</p><div id="ala-run-chart"></div></article>`;
  const get = id => document.getElementById(id);
  let samples = [], timer = null, controller = null, generation = 0, logGeneration = 0, runGeneration = 0;
  const active = () => !page.hidden && !document.hidden;
  const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const bytes = value => !finite(value) ? 'Unavailable' : value < 1024 ? `${value.toFixed(0)} B` : value < 1048576 ? `${(value / 1024).toFixed(1)} KiB` : value < 1073741824 ? `${(value / 1048576).toFixed(1)} MiB` : `${(value / 1073741824).toFixed(2)} GiB`;
  function chart(id, field, label, format) {
    const host = get(id); host.replaceChildren();
    const values = samples.map(s => s[field]);
    const present = values.filter(finite);
    if (!present.length) { host.textContent = 'No measured points yet.'; return; }
    const max = Math.max(...present, 0.000001);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 300 88'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `${label}: ${present.length} measured points. Latest ${format(present[present.length - 1])}.`);
    const title = document.createElementNS(ns, 'title'); title.textContent = `${label}, latest ${format(present[present.length - 1])}`; svg.append(title);
    values.forEach((v, i) => {
      if (!finite(v)) return;
      const circle = document.createElementNS(ns, 'circle');
      circle.setAttribute('cx', String(8 + i * (284 / Math.max(1, values.length - 1))));
      circle.setAttribute('cy', String(80 - 72 * v / max)); circle.setAttribute('r', '3'); svg.append(circle);
      if (i && finite(values[i - 1])) {
        const line = document.createElementNS(ns, 'line');
        line.setAttribute('x1', String(8 + (i - 1) * (284 / Math.max(1, values.length - 1))));
        line.setAttribute('y1', String(80 - 72 * values[i - 1] / max));
        line.setAttribute('x2', circle.getAttribute('cx')); line.setAttribute('y2', circle.getAttribute('cy')); svg.prepend(line);
      }
    });
    host.append(svg);
    const note = document.createElement('p'); note.className = 'ala-chart-note';
    note.textContent = `Browser samples: ${present.length} · chart maximum ${format(max)}`; host.append(note);
  }
  function renderSample(data) {
    if (samples.length && samples.at(-1).instance !== data.instance) samples = [];
    const prior = samples.at(-1), elapsed = prior && data.monotonic_seconds - prior.monotonic_seconds;
    const rate = (key) => elapsed > 0 && finite(data[key]) && finite(prior[key]) && data[key] >= prior[key] ? (data[key] - prior[key]) / elapsed : null;
    const cpu = rate('cpu_seconds'), read = rate('io_read_bytes'), write = rate('io_write_bytes');
    samples.push({...data, cpu: cpu === null ? null : cpu * 100, io: read === null || write === null ? null : read + write});
    if (samples.length > 60) samples.shift();
    get('ala-cpu').textContent = cpu === null ? 'Awaiting two samples' : `${(cpu * 100).toFixed(1)}% of one core`;
    get('ala-memory').textContent = bytes(data.resident_bytes);
    get('ala-io').textContent = read === null || write === null ? (data.io_read_bytes == null || data.io_write_bytes == null ? 'Unavailable' : 'Awaiting two samples') : `Read ${bytes(read)}/s · Write ${bytes(write)}/s`;
    get('ala-io-source').textContent = data.io_source || 'Counter source unavailable.';
    chart('ala-cpu-chart', 'cpu', 'Process CPU', n => `${n.toFixed(1)}%`);
    chart('ala-memory-chart', 'resident_bytes', 'Resident memory', bytes);
    chart('ala-io-chart', 'io', 'Combined process I/O rate', n => `${bytes(n)}/s`);
    get('ala-status').textContent = `Last sample: ${new Date(data.timestamp).toLocaleString()} · current app process only.`;
  }
  async function sample() {
    clearTimeout(timer);
    if (!active() || controller) return;
    const ticket = generation, request = new AbortController(); controller = request;
    try {
      const response = await fetch('/api/application-log-analytics/process', {credentials: 'same-origin', cache: 'no-store', signal: request.signal, headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (ticket === generation && active()) renderSample(data);
    } catch (error) {
      if (ticket === generation && error.name !== 'AbortError') get('ala-status').textContent = `Process sample unavailable (${error.message}). Previous measurements are not current.`;
    } finally {
      if (controller === request) controller = null;
      if (ticket === generation && active()) timer = setTimeout(sample, 10000);
    }
  }
  function selection() {
    const project = get('project-select').value, packageHash = get('project-package').value;
    if (project === '__legacy__') return {key: 'legacy', headers: {}, label: 'Selected legacy workspace'};
    if (/^[a-f0-9]{32}$/.test(project) && /^[a-f0-9]{64}$/.test(packageHash))
      return {key: `${project}:${packageHash}`, headers: {'X-Workbench-Project': project, 'X-Workbench-Package': packageHash}, label: 'Selected project and XML'};
    return null;
  }
  async function readLogs() {
    const selected = selection(), ticket = ++logGeneration;
    get('ala-log-chart').replaceChildren();
    if (!selected) { get('ala-log-status').textContent = 'Select a project and completed XML, or the legacy workspace, before reading logs.'; return; }
    get('ala-load-logs').disabled = true;
    get('ala-log-status').textContent = 'Reading the selected context’s newest 50 audit rows…';
    try {
      const response = await fetch('/api/logs?offset=0&limit=50', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json', ...selected.headers}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (ticket !== logGeneration || !active() || selection()?.key !== selected.key) return;
      const items = Array.isArray(data.items) ? data.items : [];
      const buckets = new Map(); let undated = 0;
      for (const item of items) {
        const date = new Date(item.timestamp);
        if (!item.timestamp || !Number.isFinite(date.getTime())) { undated++; continue; }
        const minute = Math.floor(date.getTime() / 60000) * 60000;
        buckets.set(minute, (buckets.get(minute) || 0) + 1);
      }
      get('ala-log-status').textContent = `${selected.label}: ${items.length} rows returned${Number.isInteger(data.total) ? ` of ${data.total} matching rows` : ''}. ${undated} without a valid timestamp. Integrity: ${data.chain_valid === true ? 'valid' : data.chain_valid === false ? 'invalid' : 'not reported'}.`;
      const host = get('ala-log-chart');
      if (!buckets.size) { host.textContent = 'No timestamped rows in this sample.'; return; }
      const max = Math.max(...buckets.values());
      for (const [timestamp, count] of [...buckets.entries()].sort((a, b) => a[0] - b[0])) {
        const row = document.createElement('div'); row.className = 'ala-bar-row';
        const time = document.createElement('time'); time.dateTime = new Date(timestamp).toISOString(); time.textContent = new Date(timestamp).toLocaleString();
        const bar = document.createElement('span'); bar.className = 'ala-bar'; bar.style.width = `${100 * count / max}%`; bar.setAttribute('aria-hidden', 'true');
        const number = document.createElement('strong'); number.textContent = String(count);
        row.append(time, bar, number); host.append(row);
      }
    } catch (error) {
      if (ticket === logGeneration && active()) get('ala-log-status').textContent = `Log sample unavailable (${error.message}). No absence of events is inferred.`;
    } finally { if (ticket === logGeneration) get('ala-load-logs').disabled = false; }
  }
  async function jsonRead(path, headers) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json', ...headers}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  async function loadRuns() {
    const selected = selection(), ticket = ++runGeneration;
    get('ala-run-chart').replaceChildren(); get('ala-run').replaceChildren(new Option('Choose a saved run', ''));
    get('ala-run').disabled = true; get('ala-run-read').disabled = true;
    if (!selected) { get('ala-run-status').textContent = 'Select a project and completed XML, or the legacy workspace, first.'; return; }
    get('ala-runs-load').disabled = true; get('ala-run-status').textContent = 'Reading up to 50 saved runs for the selected context…';
    try {
      const data = await jsonRead('/api/agents/runs?status_only=true&offset=0&limit=50', selected.headers);
      if (ticket !== runGeneration || !active() || selection()?.key !== selected.key) return;
      const items = Array.isArray(data.items) ? data.items : [];
      const terminal = new Set(['completed', 'partial', 'failed', 'cancelled', 'canceled']);
      for (const run of items) {
        if (!/^[a-f0-9]{32}$/.test(run.id || '') || !terminal.has(String(run.status).toLowerCase())) continue;
        const label = `${run.workflow_name || 'Saved workflow'} · ${run.status} · ${run.created_at ? new Date(run.created_at).toLocaleString() : 'time unavailable'}`;
        get('ala-run').add(new Option(label, run.id));
      }
      get('ala-run').disabled = get('ala-run').options.length <= 1;
      get('ala-run-status').textContent = `Showing terminal runs in the newest ${items.length} saved rows${Number.isInteger(data.total) ? ` of ${data.total}` : ''}. Select one and read its verified receipt.`;
    } catch (error) {
      if (ticket === runGeneration && active()) get('ala-run-status').textContent = `Saved runs unavailable (${error.message}). No call count is inferred.`;
    } finally { if (ticket === runGeneration) get('ala-runs-load').disabled = false; }
  }
  async function readRun() {
    const selected = selection(), id = get('ala-run').value, ticket = ++runGeneration;
    get('ala-run-chart').replaceChildren();
    if (!selected || !/^[a-f0-9]{32}$/.test(id)) return;
    get('ala-run-read').disabled = true; get('ala-run-status').textContent = 'Reading the selected saved run’s recorded calls…';
    try {
      const run = await jsonRead(`/api/agents/runs/${id}?status_only=true`, selected.headers);
      if (ticket !== runGeneration || !active() || selection()?.key !== selected.key || get('ala-run').value !== id) return;
      const saved = run.observation_snapshot;
      const historical = saved && saved.schema_version === 1 && saved.mode === 'historical_not_live'
        && saved.public_run_id === run.id && saved.package_hash === run.package_hash
        && saved.workflow_id === run.workflow_id && saved.scenario === run.scenario
        && saved.run_status === run.status && saved.finished_at === run.finished_at
        && (!run.output?.run_id || saved.run_id === run.output.run_id);
      const receipt = historical ? saved : !saved ? run.current_usage_receipt : null;
      if (run.id !== id || (selected.key !== 'legacy' && run.package_hash !== selected.key.split(':')[1])
          || receipt?.status !== 'verified' || receipt.public_run_id !== id || !Array.isArray(receipt.turns)
          || !Number.isSafeInteger(receipt.turn_count) || receipt.turn_count !== receipt.turns.length)
        throw new Error('verified matching call receipt unavailable');
      const counts = new Map(), ids = new Set();
      for (const turn of receipt.turns) {
        if (!/^[a-f0-9]{32}$/.test(turn.call_id || '') || ids.has(turn.call_id)
            || (turn.endpoint != null && !/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(turn.endpoint)))
          throw new Error('recorded call details incomplete');
        ids.add(turn.call_id);
        const endpoint = turn.endpoint || 'Endpoint not recorded';
        counts.set(endpoint, (counts.get(endpoint) || 0) + 1);
      }
      get('ala-run-status').textContent = `${receipt.turn_count} verified recorded analysis-agent calls for the selected saved run. Writer calls and other runs are not included.`;
      const host = get('ala-run-chart'), max = Math.max(...counts.values(), 1);
      for (const [endpoint, count] of counts) {
        const row = document.createElement('div'); row.className = 'ala-bar-row';
        const name = document.createElement('span'); name.textContent = endpoint;
        const bar = document.createElement('span'); bar.className = 'ala-bar'; bar.style.width = `${100 * count / max}%`; bar.setAttribute('aria-hidden', 'true');
        const number = document.createElement('strong'); number.textContent = String(count);
        row.append(name, bar, number); host.append(row);
      }
      if (!counts.size) host.textContent = 'The verified receipt contains no recorded model turns.';
    } catch (error) {
      if (ticket === runGeneration && active()) get('ala-run-status').textContent = `Saved run call counts unavailable (${error.message}). No count is inferred.`;
    } finally { if (ticket === runGeneration) get('ala-run-read').disabled = !get('ala-run').value; }
  }
  function clearRuns() {
    runGeneration++; get('ala-runs-load').disabled = false;
    get('ala-run').replaceChildren(new Option('Load saved runs first', '')); get('ala-run').disabled = true;
    get('ala-run-read').disabled = true; get('ala-run-chart').replaceChildren();
    get('ala-run-status').textContent = 'No saved run selected. Application-wide endpoint counts are not available.';
  }
  function visibility() {
    generation++; clearTimeout(timer); controller?.abort(); controller = null;
    logGeneration++; get('ala-load-logs').disabled = false;
    if (!active()) {
      get('ala-log-chart').replaceChildren();
      get('ala-log-status').textContent = 'Read a new sample for the current context.';
      clearRuns();
    }
    if (active()) sample();
  }
  function clearLogs() {
    logGeneration++; get('ala-load-logs').disabled = false; get('ala-log-chart').replaceChildren();
    get('ala-log-status').textContent = 'Selection changed. Read a new sample for the current context.';
    clearRuns();
  }
  get('ala-sample').addEventListener('click', sample);
  get('ala-load-logs').addEventListener('click', readLogs);
  get('ala-runs-load').addEventListener('click', loadRuns);
  get('ala-run').addEventListener('change', () => {
    runGeneration++; get('ala-run-chart').replaceChildren(); get('ala-runs-load').disabled = false;
    get('ala-run-read').disabled = !get('ala-run').value;
    get('ala-run-status').textContent = 'Read the selected run to check its verified call receipt.';
  });
  get('ala-run-read').addEventListener('click', readRun);
  get('project-select').addEventListener('change', clearLogs);
  get('project-package').addEventListener('change', clearLogs);
  document.addEventListener('visibilitychange', visibility);
  new MutationObserver(visibility).observe(page, {attributes: true, attributeFilter: ['hidden']});
  if (active()) visibility();
})();