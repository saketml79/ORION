/* Read-only dashboard. The host page supplies #migration-analytics-root. */
(() => {
  'use strict';
  const root = document.getElementById('migration-analytics-root');
  if (!root || root.dataset.migrationAnalyticsMounted === 'true') return;
  root.dataset.migrationAnalyticsMounted = 'true';

  const page = root.closest('.page') || root;
  const folderPage = document.getElementById('page-folder-migration');
  const fetchRead = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const number = new Intl.NumberFormat();
  const validCount = value => Number.isSafeInteger(value) && value >= 0;
  const element = (tag, text, className) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = String(text);
    if (className) item.className = className;
    return item;
  };
  const header = element('header', undefined, 'ma-header');
  const heading = element('div');
  heading.append(element('p', 'Selected package · live snapshot', 'ma-subtitle'));
  const controls = element('div', undefined, 'ma-controls');
  const scenarioLabel = element('label', 'Scenario');
  const scenario = element('select');
  scenario.id = 'migration-analytics-scenario';
  scenarioLabel.htmlFor = scenario.id;
  for (const [value, label] of [['bronze', 'Source in Unity Catalog'], ['original', 'Original source systems']]) {
    const option = element('option', label); option.value = value; scenario.append(option);
  }
  const refresh = element('button', 'Refresh', 'ma-refresh');
  refresh.type = 'button';
  controls.append(scenarioLabel, scenario, refresh); header.append(heading, controls);
  const status = element('p', 'Select a ready package to see analytics.', 'ma-status');
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const content = element('div', undefined, 'ma-content');
  const recent = element('section', undefined, 'ma-recent');
  recent.setAttribute('aria-labelledby', 'ma-recent-heading');
  const recentHeader = element('div', undefined, 'ma-recent-header');
  const recentTitle = element('h2', 'Recent run time & cost');
  recentTitle.id = 'ma-recent-heading';
  const recentButton = element('button', 'Load recent runs', 'ma-refresh');
  recentButton.type = 'button';
  recentHeader.append(recentTitle, recentButton);
  const recentStatus = element('p', 'Load recent runs for the selected package and scenario.', 'ma-recent-status');
  recentStatus.setAttribute('role', 'status'); recentStatus.setAttribute('aria-live', 'polite');
  const recentResults = element('div', undefined, 'ma-recent-results');
  const humanTime = element('aside', undefined, 'ma-human-time');
  humanTime.append(element('h3', 'Human review and rework'), element('strong', 'N/A · not tracked'));
  recent.append(recentHeader, recentStatus, recentResults, humanTime);
  root.replaceChildren(header, status, content, recent);

  let serial = 0, flight = null, timer = null, snapshot = null, currentKey = '', signature = '';
  let lastRefresh = null, observedAt = null, errorText = '', wasVisible = false;
  let recentSerial = 0, recentFlight = null, recentKey = '', recentLoaded = false;

  function scope() {
    const selectedProject = document.getElementById('project-select')?.value || '';
    const project = selectedProject === '__legacy__' ? '' : selectedProject;
    const selectedPackage = document.getElementById('project-package')?.value || '';
    // The existing folder page is populated by the app's authoritative readiness state.
    const state = page.dataset.packageReady !== undefined ? page.dataset : folderPage?.dataset;
    const hash = state?.packageHash || '';
    const ready = state?.packageReady === 'true' && Boolean(hash)
      && (state?.projectId || '') === project && (!project || selectedPackage === hash);
    return {project, hash, ready, scenario: scenario.value};
  }
  function keyOf(value) { return JSON.stringify([value.project, value.hash, value.scenario]); }
  function visible() { return !document.hidden && !page.hidden && !root.closest('[hidden]'); }
  function stopTimer() { if (timer !== null) clearTimeout(timer); timer = null; }
  function cancelFlight() {
    serial += 1;
    if (flight) flight.controller.abort();
    flight = null;
  }
  function schedule() {
    stopTimer();
    if (visible() && scope().ready) timer = setTimeout(() => { timer = null; void load(); }, 30000);
  }
  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? 'not reported' : date.toLocaleString();
  }
  function updateStatus() {
    const selected = scope();
    refresh.disabled = !visible() || !selected.ready || Boolean(flight);
    if (!selected.ready) status.textContent = snapshot
      ? 'Refreshing package data. The last snapshot remains visible.'
      : 'Select a ready package to see analytics.';
    else if (!snapshot) status.textContent = errorText || 'Loading selected-package evidence…';
    else {
      const history = snapshot.evidence_complete ? 'History complete within the assessment bounds.' : 'History incomplete: historical counts unavailable.';
      status.textContent = `${errorText ? `${errorText} ` : ''}Last successful refresh: ${formatTime(lastRefresh)}. Readiness observed: ${formatTime(observedAt)}. ${history}`;
    }
  }
  function reset() {
    stopTimer(); cancelFlight(); snapshot = null; lastRefresh = null; observedAt = null;
    errorText = ''; signature = ''; content.replaceChildren(); currentKey = keyOf(scope()); updateStatus();
  }
  function workflowClasses(summary, total, hash) {
    if (!validCount(total) || !summary || summary.package_hash !== hash ||
        summary.basis !== 'literal_pmcmd_calls_only_other_dependencies_not_assessed' ||
        !summary.workflows || typeof summary.workflows !== 'object' || Array.isArray(summary.workflows)) return null;
    const entries = Object.entries(summary.workflows);
    if (entries.length !== total) return null;
    const result = [0, 0, 0];
    for (const [id, row] of entries) {
      if (!/^[a-f0-9]{64}$/.test(id) || !row || typeof row !== 'object' || Array.isArray(row) ||
          !['outgoing', 'incoming', 'unresolved'].every(field => validCount(row[field])) || row.unresolved > row.outgoing) return null;
      if (row.outgoing || row.incoming) result[0] += 1;
      else if (row.unresolved) result[1] += 1;
      else result[2] += 1;
    }
    return result.reduce((sum, value) => sum + value, 0) === total ? result : null;
  }
  function validateReadiness(result, selected) {
    if (!result || result.scope?.project_id !== (selected.project || '__legacy__') ||
        result.scope?.pins?.package_hash !== selected.hash || result.scope?.scenario !== selected.scenario ||
        !result.counts || !validCount(result.counts.total_workflows) ||
        result.workflows?.total !== result.counts.total_workflows || result.workflows?.package_total !== result.counts.total_workflows ||
        typeof result.evidence_complete !== 'boolean') throw new Error('Selected-package evidence could not be verified.');
    return result;
  }
  async function getJSON(path, selected, signal) {
    const headers = {Accept: 'application/json'};
    if (selected.project) {
      headers['X-Workbench-Project'] = selected.project;
      headers['X-Workbench-Package'] = selected.hash;
    }
    const response = await fetchRead(path, {headers, signal, credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error(`Evidence request failed (HTTP ${response.status}).`);
    return response.json();
  }
  function cancelRecent() {
    recentSerial += 1;
    if (recentFlight) recentFlight.controller.abort();
    recentFlight = null;
    recentButton.disabled = !visible() || !scope().ready;
  }
  function syncRecent() {
    const selected = scope(), key = keyOf(selected);
    if (key !== recentKey) {
      cancelRecent(); recentKey = key; recentLoaded = false;
      recentResults.replaceChildren();
      recentStatus.textContent = 'Load recent runs for the selected package and scenario.';
    }
    if ((!visible() || !selected.ready) && recentFlight) {
      cancelRecent();
      recentStatus.textContent = recentLoaded
        ? 'Loading stopped. Previously loaded results are retained.'
        : 'Loading stopped. Select a ready package and load recent runs.';
    }
    recentButton.disabled = !visible() || !selected.ready || Boolean(recentFlight);
  }
  const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const runId = value => typeof value === 'string' && /^[a-f0-9]{32}$/.test(value);
  const endpointName = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/.test(value);
  const safeLabel = value => typeof value === 'string' && value.trim() && typeof window.orionRedaction?.text === 'function'
    ? window.orionRedaction.text(value).slice(0, 300) : 'Workflow name unavailable';
  function timestamp(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return null;
    const result = Date.parse(value);
    return Number.isFinite(result) ? result : null;
  }
  function preciseTimestamp(value) {
    if (typeof value !== 'string') return null;
    const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match) return null;
    const seconds = Date.parse(`${match[1]}${match[3]}`);
    if (!Number.isFinite(seconds)) return null;
    return BigInt(seconds) * 1000000n + BigInt((match[2] || '').padEnd(9, '0'));
  }
  function duration(run) {
    const start = preciseTimestamp(run.started_at), end = preciseTimestamp(run.finished_at);
    if (start === null || end === null || end < start) return null;
    const elapsed = end - start;
    if (elapsed > BigInt(Number.MAX_SAFE_INTEGER) * 1000000n) return null;
    const total = Number(elapsed) / 1000000, writer = run.candidate_writer;
    const writerStart = preciseTimestamp(writer?.started_at), writerEnd = preciseTimestamp(writer?.finished_at);
    if (writerStart === null || writerEnd === null || writerStart < start || writerEnd < writerStart || writerEnd > end)
      return {total, writer: null};
    return {total, writer: Number(writerEnd - writerStart) / 1000000};
  }
  function durationLabel(ms) {
    const seconds = ms / 1000;
    if (seconds < 1 && ms > 0) return `${Number(ms.toPrecision(4))} ms`;
    const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds / 60) % 60;
    const remaining = Math.round((seconds % 60) * 1000) / 1000;
    return `${hours ? `${hours}h ` : ''}${minutes ? `${minutes}m ` : ''}${remaining}s`;
  }
  // Match the arena's 30-decimal fixed-point arithmetic and round only once.
  const priceScale = 10n ** 30n;
  function decimalRate(value) {
    if (typeof value === 'number') {
      if (!Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER) return null;
      const parts = String(value).toLowerCase().split('e');
      if (parts.length === 2) {
        const exponent = Number(parts[1]), digits = parts[0].replace('.', '');
        const point = parts[0].split('.')[0].length + exponent;
        if (!Number.isSafeInteger(exponent) || point > 40 || point < -30) return null;
        value = point <= 0 ? `0.${'0'.repeat(-point)}${digits}`
          : point >= digits.length ? digits + '0'.repeat(point - digits.length)
            : `${digits.slice(0, point)}.${digits.slice(point)}`;
      } else value = parts[0];
    }
    if (typeof value !== 'string' || !/^\d{1,40}(?:\.\d{1,30})?$/.test(value)) return null;
    const [whole, fraction = ''] = value.split('.');
    return BigInt(whole) * priceScale + BigInt(fraction.padEnd(30, '0'));
  }
  function pricingRates(pricing) {
    const rates = new Map(), duplicates = new Set();
    if (pricing?.status !== 'available' || !Array.isArray(pricing.rates)) return rates;
    for (const row of pricing.rates) {
      if (!isObject(row) || !endpointName(row.endpoint)) continue;
      if (rates.has(row.endpoint) || duplicates.has(row.endpoint)) {
        rates.delete(row.endpoint); duplicates.add(row.endpoint); continue;
      }
      const input = decimalRate(row.input_usd_per_1m_tokens), output = decimalRate(row.output_usd_per_1m_tokens);
      if (row.currency !== 'USD' || input === null || output === null) { duplicates.add(row.endpoint); continue; }
      rates.set(row.endpoint, {input, output});
    }
    return rates;
  }
  function verifiedSnapshot(detail, run, selected) {
    const saved = detail?.observation_snapshot;
    return isObject(detail) && detail.id === run.id && detail.package_hash === selected.hash
      && detail.scenario === selected.scenario && detail.workflow_id === run.workflow_id
      && detail.status === run.status && detail.finished_at === run.finished_at
      && isObject(saved) && saved.schema_version === 1 && saved.mode === 'historical_not_live'
      && saved.status === 'verified' && saved.public_run_id === detail.id
      && saved.package_hash === detail.package_hash && saved.workflow_id === detail.workflow_id
      && saved.scenario === detail.scenario && saved.run_status === detail.status
      && typeof detail.finished_at === 'string' && saved.finished_at === detail.finished_at
      && Array.isArray(saved.turns) && validCount(saved.turn_count) && saved.turn_count === saved.turns.length
      ? saved : null;
  }
  function costEstimate(receipt, rates) {
    if (!receipt || !rates.size) return null;
    const roles = new Set(['supervisor', 'discovery', 'semantics', 'architecture', 'validation', 'artifact']);
    const seen = new Set();
    let total = 0n, priced = 0, partial = receipt.usage?.accounting_complete !== true;
    for (const turn of receipt.turns) {
      if (!isObject(turn) || !runId(turn.call_id) || seen.has(turn.call_id) || !roles.has(turn.role)
          || !['started', 'completed', 'failed'].includes(turn.status)) { partial = true; continue; }
      seen.add(turn.call_id);
      if (turn.status !== 'completed') partial = true;
      if (turn.status === 'started') continue;
      const rate = rates.get(turn.endpoint);
      if (!rate || !validCount(turn.input_tokens) || !validCount(turn.output_tokens)) { partial = true; continue; }
      total += BigInt(turn.input_tokens) * rate.input + BigInt(turn.output_tokens) * rate.output;
      priced += 1;
    }
    if (!priced) return null;
    const divisor = priceScale * 10000n, cents = (total + divisor / 2n) / divisor;
    return {total, amount: total > 0n && cents === 0n ? '<$0.01' : `$${cents / 100n}.${String(cents % 100n).padStart(2, '0')}`,
      partial, priced};
  }
  function renderRecent(runs, pricing, truncated) {
    const rates = pricingRates(pricing);
    const entries = runs.map(({run, detail}) => ({run, time: duration(run),
      cost: costEstimate(verifiedSnapshot(detail, run, scope()), rates)}));
    const maxTime = Math.max(0, ...entries.map(entry => entry.time?.total ?? 0));
    const maxCost = entries.reduce((max, entry) => entry.cost && entry.cost.total > max ? entry.cost.total : max, 0n);
    const intro = element('p', `Up to five recent completed or partial runs. Scroll for more. ${truncated === true ? 'Older runs are not included.' : truncated === false ? '' : 'List completeness was not reported.'}`, 'ma-chart-note');
    const disclaimer = element('p', 'Analysis model cost is an estimate using current published list rates, not billed or historical prices. Writer model calls, compute, cache and negotiated rates are excluded. Time bars show the latest recorded writer within the parent run; other time may include work before and after that writer, not just model inference.', 'ma-chart-note');
    const list = element('div', undefined, 'ma-recent-list');
    list.tabIndex = 0;
    list.setAttribute('role', 'region');
    list.setAttribute('aria-label', 'Recent run timing and cost, scroll for more');
    if (!entries.length) list.append(element('p', 'No matching completed or partial runs in this window.', 'ma-empty'));
    for (const {run, time, cost} of entries) {
      const row = element('article', undefined, 'ma-recent-row');
      const name = element('h3', safeLabel(run.workflow_name));
      const date = timestamp(run.finished_at);
      row.append(name, element('p', `${date === null ? 'Finish time not recorded' : new Date(date).toLocaleString()} · ${displayLabel(run.status)}`, 'ma-recent-meta'));
      const timing = element('div', undefined, 'ma-recent-measure');
      timing.append(element('strong', `Run wall time: ${time ? durationLabel(time.total) : 'N/A'}`));
      if (time) {
        const bar = element('div', undefined, 'ma-recent-bar'); bar.setAttribute('aria-hidden', 'true');
        if (time.writer === null) {
          const part = element('span', undefined, 'ma-time-total');
          part.style.width = `${maxTime ? time.total / maxTime * 100 : 0}%`; bar.append(part);
          timing.append(bar, element('p', 'Total only · writer timing unavailable or outside the run interval.', 'ma-recent-detail'));
        } else {
          const other = element('span', undefined, 'ma-time-other'), writer = element('span', undefined, 'ma-time-writer');
          other.style.width = `${maxTime ? (time.total - time.writer) / maxTime * 100 : 0}%`;
          writer.style.width = `${maxTime ? time.writer / maxTime * 100 : 0}%`;
          bar.append(other, writer);
          timing.append(bar, element('p', `Other run time (blue): ${durationLabel(time.total - time.writer)} · Latest writer (orange): ${durationLabel(time.writer)}`, 'ma-recent-detail'));
        }
      }
      const costing = element('div', undefined, 'ma-recent-measure');
      costing.append(element('strong', `Analysis model cost: ${cost ? `${cost.partial ? 'Partial ' : ''}${cost.amount}` : 'N/A'}`));
      if (cost) {
        const bar = element('div', undefined, 'ma-recent-bar'); bar.setAttribute('aria-hidden', 'true');
        const fill = element('span', undefined, 'ma-cost-fill');
        fill.style.width = `${maxCost ? Number(cost.total * 10000n / maxCost) / 100 : 0}%`;
        bar.append(fill); costing.append(bar, element('p', `${number.format(cost.priced)} priced analysis call${cost.priced === 1 ? '' : 's'}.`, 'ma-recent-detail'));
      } else costing.append(element('p', 'Verified priced model calls unavailable.', 'ma-recent-detail'));
      row.append(timing, costing); list.append(row);
    }
    recentResults.replaceChildren(intro, disclaimer, list);
  }
  async function loadRecent() {
    const selected = scope(), captured = keyOf(selected);
    if (!visible() || !selected.ready || recentFlight || !/^[a-f0-9]{64}$/.test(selected.hash)) return;
    const controller = new AbortController(), ticket = ++recentSerial;
    const active = {controller, key: captured}; recentFlight = active;
    recentButton.disabled = true;
    recentStatus.textContent = recentLoaded ? 'Loading recent runs; previous results remain visible.' : 'Loading recent runs…';
    const current = () => recentSerial === ticket && recentFlight === active && visible()
      && scope().ready && keyOf(scope()) === captured;
    try {
      const [listResult, priceResult] = await Promise.allSettled([
        getJSON('/api/agents/runs?status_only=true&offset=0&limit=50', selected, controller.signal),
        getJSON('/api/model-pricing/list', selected, controller.signal)
      ]);
      if (!current()) return;
      if (listResult.status !== 'fulfilled') throw new Error('Run list unavailable');
      const list = listResult.value;
      if (!isObject(list) || !Array.isArray(list.items) || list.items.length > 50 || list.offset !== 0 || list.limit !== 50)
        throw new Error('Run list could not be verified');
      const seen = new Set();
      const runs = list.items.filter(run => {
        if (!isObject(run) || !runId(run.id) || seen.has(run.id) || run.package_hash !== selected.hash
            || run.scenario !== selected.scenario || !['completed', 'partial'].includes(run.status)
            || typeof run.workflow_id !== 'string' || !run.workflow_id) return false;
        seen.add(run.id); return true;
      }).slice(0, 5);
      const details = await Promise.allSettled(runs.map(run =>
        getJSON(`/api/agents/runs/${run.id}?status_only=true`, selected, controller.signal)));
      if (!current()) return;
      const truncated = validCount(list.total) && typeof list.truncated === 'boolean'
        && list.truncated === (list.total > 50) ? list.truncated : null;
      const pricing = priceResult.status === 'fulfilled' ? priceResult.value : null;
      renderRecent(runs.map((run, index) => ({run, detail: details[index].status === 'fulfilled' ? details[index].value : null})), pricing, truncated);
      recentLoaded = true;
      recentStatus.textContent = pricingRates(pricing).size
        ? 'Recent runs loaded. Reload manually to update.'
        : 'Recent runs loaded; current pricing is unavailable. Reload manually to try again.';
    } catch (error) {
      if (current() && error.name !== 'AbortError') recentStatus.textContent = recentLoaded
        ? 'Recent runs could not be loaded. Previous results are retained.'
        : 'Recent runs could not be loaded. Use Load recent runs to try again.';
    } finally {
      if (recentFlight === active) { recentFlight = null; syncRecent(); }
    }
  }
  function metric(label, value, note, color) {
    const card = element('article', undefined, `ma-metric ma-${color}`);
    card.append(element('h3', label), element('strong', validCount(value) ? number.format(value) : 'N/A'), element('p', note));
    return card;
  }
  function chart(title, rows, description) {
    const card = element('article', undefined, 'ma-chart');
    card.append(element('h2', title), element('p', description, 'ma-chart-note'));
    const list = element('div', undefined, 'ma-bars');
    const max = Math.max(1, ...rows.map(row => validCount(row[1]) ? row[1] : 0));
    rows.forEach(([label, count, color]) => {
      const row = element('div', undefined, `ma-bar-row ma-${color}`);
      const caption = element('div', undefined, 'ma-bar-caption');
      caption.append(element('span', label), element('strong', validCount(count) ? number.format(count) : 'N/A'));
      row.append(caption);
      if (validCount(count)) {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('viewBox', '0 0 300 12'); svg.setAttribute('preserveAspectRatio', 'none');
        svg.setAttribute('aria-hidden', 'true'); svg.classList.add('ma-bar-svg');
        const track = document.createElementNS(svg.namespaceURI, 'rect');
        track.setAttribute('width', '300'); track.setAttribute('height', '12'); track.setAttribute('rx', '6'); track.setAttribute('class', 'ma-track');
        const fill = document.createElementNS(svg.namespaceURI, 'rect');
        fill.setAttribute('width', String(300 * count / max)); fill.setAttribute('height', '12'); fill.setAttribute('rx', '6'); fill.setAttribute('class', 'ma-fill');
        svg.append(track, fill); row.append(svg);
      } else row.append(element('p', 'Not available', 'ma-unavailable'));
      list.append(row);
    });
    card.append(list); return card;
  }
  function recordedRows(value, complete) {
    if (!complete || !value || typeof value !== 'object' || Array.isArray(value)) return null;
    const entries = Object.entries(value);
    if (entries.some(([key, count]) => !/^[A-Za-z0-9_ -]{1,100}$/.test(key) || !validCount(count))) return null;
    return entries.sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }
  const displayLabel = value => String(value).replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
  function chartHeading(title, note) {
    const panel = element('article', undefined, 'ma-insight');
    panel.append(element('h3', title), element('p', note, 'ma-chart-note'));
    return panel;
  }
  function stageRing(readiness) {
    const panel = chartHeading('Workflow design coverage', 'Current designs, not verified conversions.');
    const total = readiness.counts.total_workflows;
    const stages = readiness.stage_counts;
    const values = readiness.evidence_complete && validCount(total) && stages
      && validCount(stages.logical_draft) && validCount(stages.structural_only)
      && stages.logical_draft + stages.structural_only === total
      ? [stages.logical_draft, stages.structural_only] : null;
    if (!values) { panel.append(element('p', 'N/A · Stage counts unavailable', 'ma-empty')); return panel; }
    if (!total) { panel.append(element('p', 'No workflows recorded.', 'ma-empty')); return panel; }
    const body = element('div', undefined, 'ma-ring-layout');
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 120 120'); svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', `${number.format(values[0])} workflows with current designs, ${number.format(values[1])} source analyzed only, out of ${number.format(total)} workflows.`);
    const circle = (className, dash, offset) => {
      const shape = document.createElementNS(ns, 'circle');
      for (const [key, val] of Object.entries({cx: 60, cy: 60, r: 45, pathLength: 100,
        class: className, 'stroke-dasharray': dash, 'stroke-dashoffset': offset})) shape.setAttribute(key, String(val));
      svg.append(shape);
    };
    circle('ma-ring-track', '100 0', 0);
    circle('ma-ring-design', `${values[0] / total * 100} ${100 - values[0] / total * 100}`, 0);
    const center = element('div', undefined, 'ma-ring-center');
    center.append(element('strong', number.format(total)), element('span', 'workflows'));
    const ring = element('div', undefined, 'ma-ring'); ring.append(svg, center);
    const legend = element('div', undefined, 'ma-insight-legend');
    for (const [label, value, color] of [['Current designs', values[0], 'teal'], ['Source analyzed only', values[1], 'violet']]) {
      const row = element('div', undefined, `ma-legend-row ma-${color}`);
      row.append(element('span', label), element('strong', number.format(value))); legend.append(row);
    }
    body.append(ring, legend); panel.append(body); return panel;
  }
  function outcomeColumns(outcomes, complete) {
    const panel = chartHeading('AI run outcomes', 'Saved runs for this scenario, including older runs.');
    const rows = recordedRows(outcomes, complete);
    if (!rows) { panel.append(element('p', 'N/A · Run history unavailable', 'ma-empty')); return panel; }
    if (!rows.length) { panel.append(element('p', 'No AI runs recorded.', 'ma-empty')); return panel; }
    const max = Math.max(1, ...rows.map(([, count]) => count));
    const columns = element('div', undefined, 'ma-columns');
    columns.setAttribute('role', 'img');
    columns.setAttribute('aria-label', rows.map(([label, count]) => `${displayLabel(label)}: ${number.format(count)}`).join('; '));
    rows.forEach(([label, count], index) => {
      const column = element('div', undefined, `ma-column ma-column-${index % 5}`);
      const barArea = element('div', undefined, 'ma-column-area');
      const bar = element('span', undefined, 'ma-column-bar');
      bar.style.height = `${count / max * 100}%`;
      barArea.append(bar);
      column.append(element('strong', number.format(count)), barArea, element('span', displayLabel(label), 'ma-column-label'));
      columns.append(column);
    });
    panel.append(columns); return panel;
  }
  function decisionStrip(title, note, values, complete, empty) {
    const panel = chartHeading(title, note), rows = recordedRows(values, complete);
    if (!rows) { panel.append(element('p', 'N/A · History unavailable', 'ma-empty')); return panel; }
    if (!rows.length || !rows.some(([, count]) => count > 0)) {
      panel.append(element('p', empty, 'ma-empty')); return panel;
    }
    const sum = rows.reduce((result, [, count]) => result + count, 0);
    if (!Number.isSafeInteger(sum)) { panel.append(element('p', 'N/A · Count unavailable', 'ma-empty')); return panel; }
    const strip = element('div', undefined, 'ma-segments');
    strip.setAttribute('role', 'img'); strip.setAttribute('aria-label', rows.map(([label, count]) => `${displayLabel(label)}: ${number.format(count)}`).join('; '));
    const legend = element('div', undefined, 'ma-segment-legend');
    rows.forEach(([label, count], index) => {
      const segment = element('span', undefined, `ma-segment ma-column-${index % 5}`);
      segment.style.flexGrow = String(count); segment.setAttribute('aria-hidden', 'true'); strip.append(segment);
      const entry = element('span', undefined, `ma-segment-entry ma-column-${index % 5}`);
      entry.append(element('span', displayLabel(label)), element('strong', number.format(count))); legend.append(entry);
    });
    panel.append(strip, legend); return panel;
  }
  function render(readiness, classes) {
    const counts = readiness.counts, complete = readiness.evidence_complete;
    const historical = key => complete && validCount(counts[key]) ? counts[key] : null;
    const total = counts.total_workflows;
    const databricks = element('section', undefined, 'ma-databricks');
    databricks.setAttribute('aria-label', 'Databricks editing activity');
    const databricksIntro = element('div', undefined, 'ma-databricks-intro');
    databricksIntro.append(element('p', 'DATABRICKS', 'ma-databricks-kicker'),
      element('h2', 'Editing activity'),
      element('p', 'Not tracked yet.', 'ma-databricks-note'));
    const editing = element('div', undefined, 'ma-editing');
    for (const label of ['Genie Code · notebooks', 'Job design edits', 'Code and YAML edits']) {
      const item = element('div', undefined, 'ma-editing-item');
      item.append(element('span', label), element('strong', 'N/A'));
      editing.append(item);
    }
    databricks.append(databricksIntro, editing);
    const metrics = element('section', undefined, 'ma-metrics');
    metrics.setAttribute('aria-label', 'Migration snapshot');
    metrics.append(metric('Workflows', total, 'Source definitions', 'violet'),
      metric('AI assessed', historical('agent_assessed'), complete ? 'Current findings' : 'History incomplete', 'blue'),
      metric('Design drafts', historical('logical_draft'), complete ? 'Current designs' : 'History incomplete', 'teal'),
      metric('Code drafts', historical('generated_draft_receipts'), complete ? 'Saved draft receipts' : 'History incomplete', 'orange'));
    const charts = element('section', undefined, 'ma-charts'); charts.setAttribute('aria-label', 'Migration evidence charts');
    charts.append(chart('Review and draft evidence', [
      ['AI assessed', historical('agent_assessed'), 'blue'], ['Design drafts', historical('logical_draft'), 'teal'],
      ['Code drafts', historical('generated_draft_receipts'), 'orange'], ['Executable', historical('executable'), 'violet'],
      ['Runtime validated', historical('runtime_validated'), 'pink']
    ], 'Counts may overlap. Drafts are not verified conversions.'),
    chart('Workflow call candidates', [
      ['Composite candidate', classes?.[0] ?? null, 'violet'], ['Needs review', classes?.[1] ?? null, 'orange'],
      ['Standalone candidate', classes?.[2] ?? null, 'teal']
    ], classes ? 'Literal pmcmd calls only. Other dependencies are not assessed.' : 'Complete matching workflow call evidence is unavailable.'));
    const insights = element('section', undefined, 'ma-insights');
    insights.setAttribute('aria-label', 'Migration activity charts');
    insights.append(stageRing(readiness), outcomeColumns(readiness.agent_run_outcomes, complete),
      decisionStrip('Human review actions', 'Recorded actions, not individual workflows.', readiness.human_feedback,
        complete, 'No review actions recorded.'),
      decisionStrip('Validation check outcomes', 'Current validation checks, not output parity.', readiness.campaigns?.check_statuses,
        complete, 'No validation checks recorded.'));
    const untracked = element('section', undefined, 'ma-untracked');
    untracked.setAttribute('aria-label', 'Review outcomes not tracked');
    untracked.append(metric('Verified conversions', null, 'Not tracked', 'blue'),
      metric('Accepted as-is', null, 'Not tracked', 'teal'));
    const provenance = element('p', 'Source: selected-package readiness and literal workflow invocation summary. Saved receipt hashes are not independent artifact verification.', 'ma-provenance');
    content.replaceChildren(databricks, metrics, charts, insights, untracked, provenance);
  }
  async function load() {
    const selected = scope(), captured = keyOf(selected);
    if (!visible() || !selected.ready) { updateStatus(); return; }
    if (flight && flight.key === captured) return flight.promise;
    if (flight) cancelFlight();
    stopTimer();
    const controller = new AbortController(), ticket = ++serial;
    const active = {key: captured, controller, promise: null}; flight = active;
    updateStatus();
    const current = () => serial === ticket && flight === active && visible() && keyOf(scope()) === captured;
    active.promise = (async () => {
      try {
        const [readiness, summary] = await Promise.all([
          getJSON(`/api/readiness?scenario=${encodeURIComponent(selected.scenario)}&offset=0&limit=1`, selected, controller.signal),
          getJSON('/api/workflow-invocation-summary', selected, controller.signal)
        ]);
        if (!current()) return;
        validateReadiness(readiness, selected);
        const classes = workflowClasses(summary, readiness.counts.total_workflows, selected.hash);
        const nextSignature = JSON.stringify([readiness.evidence_complete, readiness.counts, classes,
          readiness.stage_counts, readiness.agent_run_outcomes, readiness.human_feedback, readiness.campaigns?.check_statuses]);
        snapshot = readiness; observedAt = readiness.observed_at; lastRefresh = new Date().toISOString(); errorText = '';
        if (signature !== nextSignature) { render(readiness, classes); signature = nextSignature; }
      } catch (error) {
        if (current() && error.name !== 'AbortError') {
          errorText = snapshot ? 'Refresh failed; last successful evidence is retained.' : 'Evidence is unavailable. Use Refresh to try again.';
          controller.abort();
        }
      } finally {
        if (flight === active) { flight = null; updateStatus(); schedule(); }
      }
    })();
    return active.promise;
  }
  function sync() {
    syncRecent();
    const next = keyOf(scope());
    if (next !== currentKey) reset();
    const nowVisible = visible(), entered = nowVisible && !wasVisible;
    wasVisible = nowVisible;
    if (!nowVisible) { stopTimer(); if (flight) cancelFlight(); updateStatus(); return; }
    if (!scope().ready) { stopTimer(); if (flight) cancelFlight(); updateStatus(); return; }
    if ((!snapshot || entered) && !flight) void load();
    else { updateStatus(); if (!flight && timer === null) schedule(); }
  }
  refresh.addEventListener('click', () => { if (!flight) void load(); });
  recentButton.addEventListener('click', () => { void loadRecent(); });
  scenario.addEventListener('change', sync);
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target?.id)) sync();
  });
  document.addEventListener('orion:navigate', sync);
  document.addEventListener('orion:migration-analytics-refresh', () => { if (!flight) void load(); });
  document.addEventListener('visibilitychange', sync);
  new MutationObserver(sync).observe(page, {attributes: true, attributeFilter: ['hidden', 'data-project-id', 'data-package-hash', 'data-package-ready']});
  if (folderPage && folderPage !== page) new MutationObserver(sync).observe(folderPage, {attributes: true, attributeFilter: ['data-project-id', 'data-package-hash', 'data-package-ready']});
  currentKey = keyOf(scope()); sync();
})();