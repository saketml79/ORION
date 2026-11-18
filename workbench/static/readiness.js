/* Read-only assessment. No models, validation creation, Jobs, or approval actions. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const el = id => document.getElementById(id), page = el('page-readiness');
  if (!page) return;
  let ticket = 0, offset = 0, data = null, busy = false;
  const scope = () => {
    const project = el('project-select')?.value || '';
    return JSON.stringify([project, project ? (el('estate-package-select')?.value ?? el('project-package')?.value ?? '') : '', el('readiness-scenario').value]);
  };
  let selected = scope();
  const node = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const names = {
    total_workflows: 'Workflows in package', structurally_analyzed: 'Source definitions analyzed',
    agent_assessed: 'Workflows reviewed', logical_draft: 'Designs proposed',
    generated_draft_receipts: 'Code drafts saved', structural_only: 'Source analyzed only',
    executable: 'Executable coverage', runtime_validated: 'Execution checks',
    output_parity_approved: 'Output comparisons approved', not_verified: 'Freshness not verified',
    not_assessed: 'Not assessed', GENERATION_TRACKING_INCOMPLETE: 'Generation evidence missing',
    BUSINESS_RUNTIME_NOT_ASSESSED: 'Execution evidence needed',
    OUTPUT_PARITY_APPROVAL_NOT_TRACKED: 'Independent output comparisons needed',
    CAMPAIGN_ARTIFACT_UNAVAILABLE: 'Validation report unavailable',
    CURRENT_SOURCE_EXECUTION_CONTRACTS_UNAVAILABLE: 'Source execution contracts needed',
    TARGET_LAYERS_UNASSIGNED: 'Target placement needs review',
    ADVISORY_INPUT_FRESHNESS_NOT_VERIFIED: 'Design freshness needs review',
    EVIDENCE_BOUND_REACHED: 'History is incomplete'
  };
  const plain = text => String(text ?? '').replace(/campaigns?/gi, match => /s$/i.test(match) ? 'validation runs' : 'validation run')
    .replace(/generated draft receipts/gi, 'Code drafts saved').replace(/not[ _]tracked/gi, 'not recorded');
  const label = key => names[key] || plain(String(key).replaceAll('_', ' ').toLowerCase());
  const known = n => typeof n === 'number' && Number.isFinite(n) && n >= 0;
  const value = n => n === null || n === undefined ? 'Not recorded' : plain(n);
  function controls() {
    el('readiness-refresh').disabled = busy;
    el('readiness-prev').disabled = busy || !data || offset === 0;
    el('readiness-next').disabled = busy || !data?.workflows.truncated;
    syncSourceLinks();
  }
  function invalidate(message = 'Selection changed. Refresh readiness for the current package version.') {
    ++ticket; selected = scope(); data = null; busy = false; offset = 0;
    el('readiness-content').replaceChildren(); el('readiness-status').textContent = message; controls();
  }
  function panel(title) { const n = node('article'); n.className = 'panel'; n.append(node('h2', title)); return n; }
  function chart(title, entries, basis, empty, icon) {
    const card = panel(title), ns = 'http://www.w3.org/2000/svg';
    card.classList.add('readiness-chart');
    const heading = card.querySelector('h2'), mark = node('span', icon);
    mark.className = 'readiness-chart-icon'; mark.setAttribute('aria-hidden', 'true'); heading.prepend(mark);
    const svg = document.createElementNS(ns, 'svg');
    const rows = entries.filter(([, count]) => known(count));
    const hasData = rows.some(([, count]) => count > 0);
    const height = Math.max(164, rows.length * 48 + 12);
    svg.setAttribute('viewBox', `0 0 360 ${height}`);
    svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${title}. ${rows.map(([k, v]) => `${label(k)}: ${v}`).join('. ')}. ${hasData ? basis : empty}`);
    const titleNode = document.createElementNS(ns, 'title'); titleNode.textContent = svg.getAttribute('aria-label'); svg.append(titleNode);
    const shape = (tag, attrs, text) => {
      const item = document.createElementNS(ns, tag);
      Object.entries(attrs).forEach(([key, val]) => item.setAttribute(key, String(val)));
      if (text !== undefined) item.textContent = text;
      svg.append(item); return item;
    };
    // Independent counts are never stacked, turned into percentages, or treated as passes.
    const max = Math.max(1, ...rows.map(([, count]) => count));
    rows.forEach(([key, count], i) => {
      shape('text', { x: 8, y: i * 48 + 19 }, label(key));
      shape('text', { x: 352, y: i * 48 + 19, 'text-anchor': 'end', class: 'readiness-chart-count' }, count);
      shape('rect', { x: 8, y: i * 48 + 28, width: 344, height: 9, rx: 4, class: 'readiness-track' });
      shape('rect', { x: 8, y: i * 48 + 28, width: 344 * count / max, height: 9, rx: 4, class: `readiness-bar readiness-bar-${i % 4}` });
    });
    if (!rows.length) {
      svg.classList.add('readiness-no-data');
      [40, 88, 136].forEach(y => shape('rect', { x: 8, y, width: 344, height: 9, rx: 4, class: 'readiness-track' }));
      shape('text', { x: 180, y: 24, 'text-anchor': 'middle' }, 'Awaiting recorded evidence');
    }
    if (!hasData) card.dataset.empty = 'true';
    const alternative = node('details'); alternative.append(node('summary', 'Chart data and legend'));
    const list = node('ul'); rows.forEach(([k, n]) => list.append(node('li', `${label(k)}: ${n}`)));
    alternative.append(list, node('p', basis), node('p', 'Bar length compares recorded counts within this chart, not readiness or accuracy. Unknown values have no bar.'));
    card.append(svg, node('p', hasData ? basis : empty), alternative); return card;
  }
  function table(headers, rows) {
    const wrap = node('div'); wrap.className = 'readiness-table';
    const t = node('table'), caption = node('caption', headers[0] === 'Role' ? 'Stored specialist evidence and advisory human outcomes'
      : headers[0] === 'Pillar' ? 'Architecture references, not validation results' : 'Canonical workflow readiness, one selected package');
    const head = node('thead'), line = node('tr');
    headers.forEach(h => { const th = node('th', h); th.scope = 'col'; line.append(th); }); head.append(line);
    const body = node('tbody'); rows.forEach(cells => { const tr = node('tr'); cells.forEach(cell => { const td = node('td'); td.append(cell instanceof Node ? cell : document.createTextNode(String(cell))); tr.append(td); }); body.append(tr); });
    t.append(caption, head, body); wrap.append(t); return wrap;
  }
  function render() {
    const root = el('readiness-content'); root.replaceChildren();
    const counts = data.counts, gate = data.release_gate;
    const header = panel('Conversion progress'); header.className += ' readiness-overview';
    const context = node('p', `${el('project-select')?.selectedOptions[0]?.textContent || 'Legacy workspace'} / ${el('package-name')?.textContent || 'Selected package'} / ${data.scope.scenario === 'bronze' ? 'Source data already in Unity Catalog' : 'Original source systems'}`);
    context.className = 'readiness-context';
    const progress = ['logical_draft', 'generated_draft_receipts'].filter(key => known(counts[key]))
      .map(key => `${counts[key]} ${label(key).toLowerCase()}`).join(' · ');
    header.append(context, node('p', progress ? `${progress}. Source analysis is not conversion.` : 'Conversion evidence is incomplete. Source analysis is not conversion.'));
    const blocked = gate.status === 'BLOCKED' || data.production_ready === false;
    const status = node('p', `${blocked ? 'Not ready for production' : 'Production readiness not established'}${known(gate.verified_business_workflows) && known(gate.minimum_business_workflows) ? `: ${gate.verified_business_workflows}/${gate.minimum_business_workflows} business workflows verified` : ''}.`);
    status.className = 'readiness-gate'; header.append(status);
    const actions = node('div'); actions.className = 'readiness-actions';
    const next = known(counts.logical_draft) && counts.logical_draft > 0 ? ['proposals', 'Review designs and code'] : ['agents', 'Review a workflow'];
    const nextButton = navigationButton(next[0], next[1]);
    if (nextButton) actions.append(node('span', 'Next step'), nextButton);
    else {
      const explore = navigationButton('explorer', 'Inspect source definitions');
      if (explore) actions.append(node('span', 'Next step'), explore);
    }
    header.append(actions);

    const charts = node('div'); charts.className = 'readiness-charts';
    const stages = Object.entries(data.stage_counts || {});
    const exclusive = known(counts.total_workflows) && stages.length > 0 && stages.every(([, n]) => known(n))
      && stages.reduce((sum, [, n]) => sum + n, 0) === counts.total_workflows;
    charts.append(chart('Workflow stages', stages,
      exclusive ? `${counts.total_workflows} workflows across exclusive recorded stages. A proposed design is not converted code.` : 'Recorded stage counts only. Coverage is incomplete; no overall share is inferred.',
      'No workflow stage counts recorded yet. Inspect the selected source package.', '≡'),
    chart('Review and conversion', ['agent_assessed', 'logical_draft', 'generated_draft_receipts'].map(key => [key, counts[key]]),
      'Workflows reviewed, designs proposed and code drafts saved can overlap. Saved code is not execution proof.',
      'No review or code-generation results recorded yet. Start with a workflow review.', '◇'),
    chart('Execution checks', Object.entries(data.campaigns.check_statuses || {}),
      'Recorded validation check outcomes, not full business execution or output parity.',
      'No execution results recorded yet. Review validation evidence before separately authorizing execution.', '✓'));
    root.append(charts, header);

    const metrics = node('div'); metrics.className = 'readiness-metrics';
    const summaryKeys = ['total_workflows', 'agent_assessed', 'logical_draft', 'generated_draft_receipts', 'structurally_analyzed'];
    for (const key of summaryKeys.filter(key => known(counts[key])).slice(0, 4)) {
      const card = panel(label(key)); card.dataset.metric = key; card.append(node('strong', counts[key])); metrics.append(card);
    }
    root.append(metrics);
    const gaps = Object.entries(counts).filter(([, n]) => n === null || n === undefined);
    if (gaps.length || !data.evidence_complete) {
      const missing = node('details'); missing.className = 'readiness-evidence-gaps'; missing.append(node('summary', 'Evidence gaps'));
      missing.append(node('p', 'Missing counts are unknown, not zero. No execution results recorded yet unless explicitly shown in Execution checks.'));
      const list = node('ul'); gaps.forEach(([key]) => list.append(node('li', `${label(key)}: no current evidence recorded.`)));
      missing.append(list, node('p', data.evidence_complete ? 'Review the exact workflow and validation evidence; execution requires separate approval.' : 'History is bounded. Sample counts cannot establish complete totals or freshness.'));
      root.append(missing);
    }
    const blockers = panel('What needs attention'); blockers.classList.add('readiness-attention');
    const reasonItem = reason => {
      const item = node('div'); item.className = 'readiness-reason';
      item.append(node('h3', label(reason.code)), node('p', plain(reason.impact)), node('p', plain(reason.next_step))); return item;
    };
    const reasons = data.reasons || [];
    reasons.slice(0, 3).forEach(reason => blockers.append(reasonItem(reason)));
    if (!reasons.length) blockers.append(node('p', 'No stored blockers reported. Missing evidence still does not clear production gates.'));
    if (reasons.length > 3) {
      const all = node('details'); all.append(node('summary', `All issues and next steps (${reasons.length})`));
      reasons.forEach(reason => all.append(reasonItem(reason))); blockers.append(all);
    }
    blockers.append(node('p', 'Mock examples and controlled fixtures are not original-data execution results.'));
    root.append(blockers);

    const workflows = panel(data.workflows.total ? `Workflow evidence (${offset + 1}–${Math.min(offset + data.workflows.limit, data.workflows.total)} of ${data.workflows.total})` : 'Workflow evidence (no workflow definitions)');
    workflows.append(table(['Workflow', 'Current evidence', 'Impact / next step'], data.workflows.items.map(w => {
      const identity = node('div'); identity.append(node('strong', w.name)); const id = node('details'); id.append(node('summary', 'Canonical ID'), node('p', w.workflow_id)); identity.append(id);
      const source = node('button', 'View source'); source.type = 'button'; source.className = 'secondary compact'; source.dataset.readinessWorkflow = w.workflow_id;
      source.hidden = true;
      source.addEventListener('click', () => {
        const select = sourceControl(w.workflow_id);
        if (!select) return;
        select.value = w.workflow_id; select.dispatchEvent(new Event('change', { bubbles: true }));
        el('migration-lineage')?.scrollIntoView({ block: 'start', behavior: 'instant' }); select.focus({ preventScroll: true });
      }); identity.append(source);
      const issue = node('div', `${plain(w.reason)} ${plain(w.next_step)}`), detail = node('details');
      detail.append(node('summary', `Exact stored blockers (${w.blockers.length + (w.campaign_blockers || []).length})`), node('p', JSON.stringify({ design: w.blockers, campaigns: w.campaign_blockers || [], business: w.business_validation_evidence ?? 'unavailable' })));
      issue.append(detail);
      return [identity, `${label(w.stage)}${w.design_version !== null && w.design_version !== undefined ? `; design v${w.design_version}, ${label(w.design_freshness)}` : ''}${known(w.campaign_count) ? `; ${w.campaign_count} current validation runs` : ''}.`, issue];
    }))); root.append(workflows);

    const agents = panel('Agent review and human feedback');
    const active = (data.agents || []).filter(a => a.latest_run_id || a.finding_count > 0 || a.typed_design_versions > 0 || Object.keys(a.feedback || {}).length);
    if (active.length) agents.append(table(['Role', 'Latest evidence', 'Findings / designs', 'Human feedback'], active.map(a => [
      label(a.role), `${label(a.latest_status)}; ${label(a.freshness)}`,
      `${value(a.finding_count)} findings / ${value(a.typed_design_versions)} designs`,
      Object.entries(a.feedback || {}).map(([key, n]) => `${label(key)}: ${value(n)}`).join(', ') || 'No feedback recorded'
    ])));
    else agents.append(node('p', 'No agent reviews recorded yet.'));
    agents.append(node('p', `Human feedback: ${Object.entries(data.human_feedback || {}).map(([key, n]) => `${label(key)} ${value(n)}`).join(', ') || 'none recorded'}. Model accuracy is not assessed; no live evaluation is requested.`));
    const agentDetails = node('details'); agentDetails.append(node('summary', 'Review findings, usage and evaluation evidence'));
    agentDetails.append(node('p', plain(data.feedback_basis)));
    active.forEach(a => {
      agentDetails.append(node('h3', label(a.role)), node('p', `Run: ${a.latest_run_id || 'No run reference'}. Whole-run usage, not role attribution: ${a.reported_usage ? JSON.stringify(a.reported_usage) : 'not recorded'}.`));
      (a.latest_findings || []).forEach(f => agentDetails.append(node('p', `${label(f.status)}: ${plain(f.summary)}. ${value(f.citation_count)} citations. ${plain((f.blockers || []).join(', '))}`)));
    });
    agentDetails.append(node('p', `Stored deterministic evaluations: ${value(data.evaluation_count)}. Schema and citation checks do not measure model accuracy.`));
    (data.evaluations || []).forEach(e => agentDetails.append(node('p', JSON.stringify(e))));
    agents.append(agentDetails); root.append(agents);

    const knowledge = node('details'); knowledge.className = 'readiness-reference'; knowledge.append(node('summary', 'Architecture reference material'));
    const k = data.architecture_knowledge;
    knowledge.append(node('p', k ? `${label(k.status)}. ${value(k.feature_count)} curated features; ${value(k.fresh_ga_count)} fresh GA candidates. Knowledge is not validation, runtime proof, or a complete latest-feature catalogue.` : 'Reference material has not been reported. No validation evidence inferred.'));
    if (k) {
      knowledge.append(node('p', `Version: ${value(k.version)}. Earliest expiry: ${value(k.earliest_expiry)}. Expired features: ${value(k.expired_feature_count)}. Data availability: ${value(k.data_availability)}.`));
      knowledge.append(table(['Pillar', 'Reference entries', 'Validation proof'], Object.entries(k.pillars || {}).map(([pillar, state]) => [label(pillar), value(state.knowledge_count), 'Not established by knowledge retrieval'])));
    }
    root.append(knowledge);
    const evidence = node('details'); evidence.className = 'readiness-technical'; evidence.append(node('summary', 'Assessment scope and technical evidence'));
    evidence.append(node('p', plain(data.count_basis)), node('p', plain(data.issue_count_basis)), node('p', plain(data.campaigns.basis)));
    if (data.business_validation) evidence.append(node('p', plain(data.business_validation.basis)));
    evidence.append(node('p', `Validation runs: ${value(data.campaigns.current)} current, ${value(data.campaigns.stale)} stale. Stale evidence does not clear gates. No execution or deployment authority is granted here.`));
    // Preserve the complete response, including every pin, receipt and role record, without turning metadata into progress tiles.
    const raw = node('pre', JSON.stringify(data, null, 2)); raw.className = 'json-block'; evidence.append(raw); root.append(evidence);
    syncSourceLinks(); controls();
  }
  function available(control) {
    return control && !control.disabled && control.getAttribute('aria-disabled') !== 'true' && !control.closest('[inert]');
  }
  function navigationButton(destination, text) {
    const nav = document.querySelector(`.navigation [data-page="${destination}"]`);
    if (!available(nav)) return null;
    const button = node('button', text); button.type = 'button'; button.className = 'secondary compact';
    const captured = selected;
    button.addEventListener('click', () => {
      if (captured !== scope() || !data || busy || !available(nav)) return;
      const scenario = data.scope.scenario;
      nav.click();
      const selector = el(destination === 'agents' ? 'agent-scenario' : destination === 'proposals' ? 'scenario' : '');
      if (available(selector) && [...selector.options].some(option => option.value === scenario && !option.disabled) && selector.value !== scenario) {
        selector.value = scenario; selector.dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    return button;
  }
  function sourceControl(id) {
    const select = el('lineage-workflow');
    return selected === scope() && data && !busy && available(select)
      && [...select.options].some(option => option.value === id && !option.disabled) ? select : null;
  }
  function syncSourceLinks() {
    page.querySelectorAll('[data-readiness-workflow]').forEach(button => { button.hidden = !sourceControl(button.dataset.readinessWorkflow); });
  }
  const lineageSelect = el('lineage-workflow');
  if (lineageSelect) new MutationObserver(syncSourceLinks).observe(lineageSelect, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'value'] });
  async function load() {
    if (page.hidden || busy) return;
    const captured = scope(), serial = ++ticket, [project, packageHash, scenario] = JSON.parse(captured);
    if (project && !packageHash) { invalidate('Select a completed XML package first.'); return; }
    busy = true; data = null; el('readiness-content').replaceChildren(); controls();
    el('readiness-status').textContent = 'Reading authoritative selected-package evidence. No assessment jobs or models are running.';
    const current = () => captured === scope() && ticket === serial && !page.hidden;
    try {
      const headers = { Accept: 'application/json' };
      if (project) { headers['X-Workbench-Project'] = project; headers['X-Workbench-Package'] = packageHash; }
      const response = await fetch(`/api/readiness?offset=${offset}&limit=25&scenario=${scenario}`, { headers, credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error(`Readiness unavailable (HTTP ${response.status}). No fallback or inferred counts.`);
      const result = await response.json();
      if (!current()) return;
      if (result.scope.project_id !== (project || '__legacy__') || project && result.scope.pins.package_hash !== packageHash || result.scope.scenario !== scenario)
        throw new Error('Readiness response does not match the selected project/package/scenario. No evidence displayed.');
      data = result; render(); el('readiness-status').textContent = `Saved evidence observed ${result.observed_at}. Runtime and output parity remain not assessed.`;
    } catch (error) { if (current()) { data = null; el('readiness-content').replaceChildren(); el('readiness-status').textContent = error.message; } }
    finally { if (serial === ticket) { busy = false; controls(); } }
  }
  el('readiness-refresh').addEventListener('click', load);
  el('refresh')?.addEventListener('click', () => { if (!page.hidden) { invalidate('Refreshing saved evidence.'); load(); } });
  el('readiness-prev').addEventListener('click', () => { offset = Math.max(0, offset - 25); load(); });
  el('readiness-next').addEventListener('click', () => { offset += 25; load(); });
  document.addEventListener('orion:navigate', event => { if (event.detail?.page === 'readiness') load(); });
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select', 'readiness-scenario'].includes(event.target.id)) {
      invalidate(); if (event.target.id === 'readiness-scenario') load();
    }
  });
  new MutationObserver(() => { if (selected !== scope() || page.hidden) invalidate(); }).observe(page, { attributes: true, attributeFilter: ['hidden'] });
  const ready = el('package-ready');
  if (ready) new MutationObserver(() => {
    if (ready.dataset.understanding !== 'loaded' || selected !== scope()) invalidate('Package understanding changed. Refresh exact readiness evidence once loading finishes.');
  }).observe(ready, { attributes: true, attributeFilter: ['data-understanding'] });
  controls();
})();