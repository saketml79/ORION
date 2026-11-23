/* Explicit scoped validation only. No startup calls, models or Jobs. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const el = id => document.getElementById(id);
  const page = el('page-validate');
  if (!page) return;
  let context = null, report = null, events = [], ticket = 0, busy = false, fresh = false;
  let catalogTicket = 0, catalogLoading = false;
  const scope = () => {
    const project = el('project-select')?.value || '';
    return JSON.stringify([project, project ? (el('estate-package-select')?.value ?? el('project-package')?.value ?? '') : '']);
  };
  let selection = scope();
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const samePins = (a, b) => !!a && !!b && ['package_hash', 'understanding_sha256', 'analysis_version']
    .every(key => Object.hasOwn(a, key) && Object.hasOwn(b, key) && a[key] === b[key]);
  function showFreshness() {
    const text = el('validation-report-freshness');
    if (text && report) text.textContent = `Business parity: ${report.business_parity}. Current fingerprints: ${fresh ? 'match' : 'stale or not verified'}.`;
  }
  function controls() {
    el('validation-create').disabled = busy || !context;
    el('validation-export').disabled = !report || busy;
    el('validation-provision').disabled = busy || !context?.hosted_obo || !report || !fresh || events.length > 0
      || report.catalog_plan.target_errors.length > 0;
    el('validation-context').disabled = busy;
    el('validation-read').disabled = busy;
    el('validation-campaign-id').disabled = busy;
  }
  function clear() {
    ++ticket; selection = scope(); context = null; report = null; events = []; fresh = false; busy = false;
    el('validation-results').replaceChildren(); el('validation-campaign-id').value = '';
    el('validation-status').textContent = 'Context changed. Load exact fingerprints before creating a campaign.';
    el('validation-provision-status').textContent = 'No provisioning requested.'; controls();
  }
  async function request(path, body, raw = false) {
    const [project, packageHash] = JSON.parse(scope());
    if (project && !packageHash) throw new Error('Select a ready project package first.');
    const headers = { Accept: 'application/json' };
    if (project) { headers['X-Workbench-Project'] = project; headers['X-Workbench-Package'] = packageHash; }
    if (body) { headers['Content-Type'] = 'application/json'; headers['X-Workbench-Request'] = '1'; }
    const response = await fetch(path, { method: body ? 'POST' : 'GET', headers,
      ...(body ? { body: JSON.stringify(body) } : {}), credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error(`Validation unavailable (HTTP ${response.status}). No automatic retry or fallback.`);
    return raw ? response : response.json();
  }
  el('validation-load-catalogs').addEventListener('click', async () => {
    if (catalogLoading) return;
    const captured = scope(), serial = ++catalogTicket;
    const button = el('validation-load-catalogs'), select = el('validation-source-catalog');
    const status = el('validation-catalog-status');
    const current = () => serial === catalogTicket && captured === scope() && !page.hidden;
    catalogLoading = true; button.disabled = true;
    status.textContent = 'Connecting to Databricks and reading available catalog names…';
    try {
      const connection = await request('/api/databricks/volumes/status');
      if (!current()) return;
      if (!connection.connected) {
        const connected = await request('/api/databricks/volumes/connect', {});
        if (!current()) return;
        if (connected.connected !== true) throw new Error('Databricks connection was not confirmed.');
      }
      const result = await request('/api/databricks/volumes/catalogs');
      if (!current()) return;
      if (!Array.isArray(result.items)) throw new Error('Catalog list was not returned.');
      const previous = select.value;
      const names = [...new Set(result.items.map(item => item?.name)
        .filter(name => typeof name === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,254}$/.test(name)))].sort((a, b) => a.localeCompare(b));
      select.replaceChildren(new Option('Choose a source catalog', ''));
      for (const name of names) select.append(new Option(name, name));
      select.value = names.includes(previous) ? previous : '';
      if (previous && !select.value) select.dispatchEvent(new Event('change', {bubbles: true}));
      status.textContent = names.length
        ? `${names.length} catalog(s) available. Select the catalog containing your source data.${result.truncated ? ' The service returned a capped list; additional catalogs may not be shown.' : ''}`
        : 'No selectable catalogs were returned. Check your workspace access.';
    } catch (error) {
      if (current()) status.textContent = `Could not load catalog names: ${error.message} Check the Databricks connection and try again.`;
    } finally {
      if (serial === catalogTicket) { catalogLoading = false; button.disabled = false; }
    }
  });
  async function action(fn) {
    if (busy) return;
    const captured = scope(), serial = ++ticket;
    busy = true; controls();
    const current = () => captured === scope() && serial === ticket && !page.hidden;
    try { await fn(current); }
    catch (error) { if (current()) el('validation-status').textContent = error.message; }
    finally { if (serial === ticket) { busy = false; controls(); } }
  }
  function render() {
    const result = el('validation-results'); result.replaceChildren();
    result.append(node('h3', `Campaign ${report.id}: ${report.status}`));
    const freshness = node('p', ''); freshness.id = 'validation-report-freshness'; result.append(freshness); showFreshness();
    const pillars = node('dl', ''); pillars.className = 'detail-list';
    for (const [name, status] of Object.entries(report.pillars)) { pillars.append(node('dt', name.replaceAll('_', ' ')), node('dd', status)); }
    result.append(pillars, node('h3', 'Exact proposed test catalog names'));
    for (const [layer, name] of Object.entries(report.catalog_plan.names)) result.append(node('p', `${layer}: ${name}`));
    result.append(node('h3', 'Where test output tables would go'));
    for (const target of report.catalog_plan.targets) result.append(node('p',
      `${target.xml_identity.join(' / ')} → ${target.destination_catalog ? `${target.destination_catalog}.${target.schema}.${target.table}` : 'Not assigned to a test destination'}`));
    if (report.release_gate) result.append(node('p',
      `Release gate: ${report.release_gate.status}. ${report.release_gate.verified_business_workflows}/${report.release_gate.minimum_business_workflows} distinct business workflows verified. Synthetic tests and wrappers do not qualify.`));
    if (report.workflow_candidates) {
      const candidates = node('details', ''); candidates.append(node('summary', 'Distinct business workflow readiness'));
      for (const candidate of report.workflow_candidates) candidates.append(node('p',
        `${candidate.name}: ${candidate.status}. ${candidate.direct_sources} source definitions, ${candidate.direct_targets} target definitions, ${candidate.mappings} mappings. ${candidate.blocker_codes.join(', ')}`));
      result.append(candidates);
    }
    result.append(node('p', `Existing external source preserved: ${report.bindings.source_fixture_catalog}. Approved physical bindings: no.`));
    const checks = node('details', ''); checks.append(node('summary', 'Check statuses and evidence basis'));
    for (const check of report.checks) checks.append(node('p', `${check.pillar} / ${check.id}: ${check.status}. ${check.basis}`));
    result.append(checks);
    const coverage = node('details', ''); coverage.append(node('summary', 'Source coverage versus runtime coverage'));
    for (const task of report.source_coverage.obligations) coverage.append(node('p', `${task.kind} ${task.id}: ${task.type}, ${task.runtime_status}`));
    result.append(coverage);
    const blockers = node('details', ''); blockers.open = true; blockers.append(node('summary', `${report.blockers.length} exact execution blockers`));
    for (const b of report.blockers) blockers.append(node('p', `${b.code}: ${b.evidence_ids.join(', ')}`));
    result.append(blockers);
    el('validation-campaign-id').value = report.id;
    el('validation-provision-status').textContent = events.length ? `Recorded attempt: ${events.at(-1).status || 'started'}. No automatic replay.` : 'Not run. Review the exact three names before selecting Provision. Unassigned targets will not be provisioned.';
    controls();
  }
  el('validation-context').addEventListener('click', () => action(async current => {
    const value = await request('/api/validation/context'); if (!current()) return;
    context = value; fresh = report ? samePins(report.pins, context.pins) : false; showFreshness();
    el('validation-status').textContent = 'The selected package’s analysis is ready for validation. No job has been run.';
  }));
  el('validation-form').addEventListener('submit', event => {
    event.preventDefault(); if (!context) return;
    action(async current => {
      const targetLayers = JSON.parse(el('validation-target-layers').value || '{}');
      if (!targetLayers || Array.isArray(targetLayers) || typeof targetLayers !== 'object'
          || Object.values(targetLayers).some(layer => !['bronze', 'silver', 'gold'].includes(layer))) {
        throw new Error('Provide an object mapping exact canonical target IDs to bronze, silver or gold.');
      }
      const value = await request('/api/validation/campaigns', { consent: true, workflow_id: el('validation-workflow').value,
        scenario: el('validation-scenario').value, source_fixture_catalog: el('validation-source-catalog').value,
        target_layers: targetLayers,
        expected_package_hash: context.pins.package_hash, expected_understanding_sha256: context.pins.understanding_sha256,
        expected_analysis_version: context.pins.analysis_version });
      if (!current()) return; report = value; events = []; fresh = true; render();
      el('validation-status').textContent = 'Immutable campaign saved. Controlled fixtures ran; business execution remains blocked.';
    });
  });
  el('validation-read').addEventListener('click', () => action(async current => {
    const id = el('validation-campaign-id').value;
    if (!/^[a-f0-9]{32}$/.test(id)) throw new Error('Enter the exact saved 32-character campaign ID.');
    const value = await request(`/api/validation/campaigns/${id}`); if (!current()) return;
    if (value.report?.id !== id || !Array.isArray(value.provisioning_events)) throw new Error('Saved campaign response does not match the requested ID. No evidence displayed.');
    report = value.report; fresh = value.current_input_pins_match === true; events = value.provisioning_events; render();
    el('validation-status').textContent = `Saved campaign ${id} read from scoped authority. Current fingerprints: ${fresh ? 'match' : 'stale or not verified'}.`;
  }));
  el('validation-campaign-id').addEventListener('input', () => {
    if (busy || !report || el('validation-campaign-id').value === report.id) return;
    report = null; events = []; fresh = false;
    el('validation-results').replaceChildren();
    el('validation-provision-status').textContent = 'No campaign selected. No provisioning requested.';
    el('validation-status').textContent = 'Campaign ID changed. Read the saved campaign before downloading or provisioning.'; controls();
  });
  el('validation-export').addEventListener('click', () => action(async current => {
    if (!report) return;
    const id = report.id;
    const response = await request(`/api/validation/campaigns/${encodeURIComponent(id)}/export`, null, true);
    const blob = await response.blob(); if (!current()) return;
    const length = response.headers.get('X-Workbench-Campaign-Artifact-Bytes');
    if (!length || !/^\d+$/.test(length) || Number(length) !== blob.size)
      throw new Error('Saved report download length could not be verified. No download accepted.');
    const url = URL.createObjectURL(blob);
    const link = node('a', ''); link.href = url; link.download = `validation-${id}.json`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    const match = response.headers.get('X-Workbench-Campaign-Current-Input-Pins-Match');
    el('validation-status').textContent = `Saved report downloaded from scoped authority. Current fingerprints: ${match === 'true' ? 'match' : match === 'false' ? 'stale' : 'not verified'}. Provisioning events are not included in this report.`;
  }));
  el('validation-provision').addEventListener('click', () => {
    if (el('validation-provision').disabled) return;
    action(async current => {
      const value = await request(`/api/validation/campaigns/${report.id}/provision`, { consent: true,
        expected_report_sha256: report.report_sha256, confirmed_catalog_names: report.catalog_plan.names });
      if (!current()) return; events = [value]; render();
      el('validation-status').textContent = `Empty fixture provisioning: ${value.status}. Business parity remains not established.`;
    });
  });
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) {
      ++catalogTicket; catalogLoading = false; el('validation-load-catalogs').disabled = false;
      el('validation-source-catalog').replaceChildren(new Option('Load catalogs, then choose one', ''));
      el('validation-catalog-status').textContent = 'Load the catalogs available to your Databricks identity. This reads catalog names only.';
    }
    if (['project-select', 'project-package', 'estate-package-select', 'validation-workflow', 'validation-scenario', 'validation-source-catalog', 'validation-target-layers'].includes(event.target.id)) clear();
  });
  new MutationObserver(() => {
    if (!page.hidden) return;
    if (catalogLoading) {
      ++catalogTicket; catalogLoading = false; el('validation-load-catalogs').disabled = false;
      el('validation-catalog-status').textContent = 'Catalog loading stopped after leaving this page. Load available catalogs when ready.';
    }
    clear();
  }).observe(page, { attributes: true, attributeFilter: ['hidden'] });
  controls();
})();