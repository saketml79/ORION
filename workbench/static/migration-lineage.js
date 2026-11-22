/* Read-only selected canonical source trace. Animation never represents execution. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const $ = id => document.getElementById(id), host = $('migration-lineage');
  if (!host) return;
  const ns = 'http://www.w3.org/2000/svg';
  const html = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const svgNode = (tag, attrs = {}, text = '') => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v)); n.textContent = text; return n; };
  const scope = () => { const project = $('project-select')?.value || ''; return JSON.stringify([project, project ? ($('estate-package-select')?.value ?? $('project-package')?.value ?? '') : '', $('readiness-scenario')?.value || 'bronze']); };
  let epoch = 0, data = null, selectedScope = scope(), busy = false, workflow = '', root = '', family = 'nodes', offset = 0;
  let view = [0, 0, 1500, 640], base = [...view], selectedNode = '', play = false;
  let tracing = false;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  function animation() {
    const active = play && !motion.matches && !busy && !$('page-readiness').hidden && !document.hidden;
    host.classList.toggle('lineage-animate', active);
    $('lineage-play').textContent = play ? 'Pause trace' : 'Animate selected trace';
    $('lineage-play').setAttribute('aria-pressed', String(play));
  }
  function controls() {
    $('lineage-load').disabled = busy;
    for (const id of ['lineage-workflow', 'lineage-family']) $(id).disabled = busy || !data;
    $('lineage-prev').disabled = busy || !data || offset === 0;
    $('lineage-next').disabled = busy || !data?.page.has_more;
    $('lineage-play').disabled = busy || !data || motion.matches;
    $('lineage-trace').disabled = busy || !data;
    $('lineage-example').disabled = busy;
    host.setAttribute('aria-busy', String(busy)); animation();
  }
  function clear(message = 'Selection changed. Load lineage for the exact current package.') {
    ++epoch; busy = false; data = null; workflow = ''; root = ''; offset = 0; play = false; tracing = false;
    selectedScope = scope(); $('lineage-workflow').replaceChildren(); $('lineage-graph').replaceChildren();
    $('lineage-calls').replaceChildren();
    $('lineage-inspector').replaceChildren(); $('lineage-summary').replaceChildren(); $('lineage-relations').replaceChildren();
    for (const id of ['lineage-hierarchy', 'lineage-proposed', 'lineage-workflow-facts', 'lineage-trace-text', 'lineage-change-detail', 'lineage-cohort']) $(id).replaceChildren();
    $('lineage-example').checked = false;
    $('lineage-proposed-status').textContent = 'No selected evidence.';
    $('lineage-trace-status').textContent = message;
    $('lineage-graph-caption').textContent = '';
    $('lineage-status').textContent = message; controls();
  }
  function setView() { $('lineage-graph').setAttribute('viewBox', view.join(' ')); }
  function zoom(factor) {
    const w = Math.min(base[2] * 3, Math.max(320, view[2] * factor)), h = w * view[3] / view[2];
    view = [view[0] + (view[2] - w) / 2, view[1] + (view[3] - h) / 2, w, h]; setView();
  }
  function fit() { view = [...base]; setView(); }
  function inspect(id) { selectedNode = id; root = id; offset = 0; tracing = false; family = 'nodes'; $('lineage-family').value = family; load(); }
  function technical(container, title, value) {
    const d = html('details'); d.append(html('summary', title)); const pre = html('pre', JSON.stringify(value, null, 2)); d.append(pre); container.append(d);
  }
  function showChanges(row, example = false) {
    selectedNode = row.id;
    const panel = $('lineage-change-detail'); panel.replaceChildren();
    panel.append(html('h3', 'What changed'), html('p', row.changes || 'Evidence not specified.'));
    technical(panel, 'Original source code / paths and identities', row.source);
    technical(panel, 'Target code / paths and typed changes', row.targets || row.target);
    panel.append(html('p', example ? 'Static illustrative reference only. No selected-user correlation. Original/target code pairing is not specified. Historical MOCK execution proof is separate from task correspondence.' : 'Only explicit saved coverage is a correspondence. Original code is available through the canonical object expansion. Target code bytes are not reverified by this read; missing code, tables, filters, expressions, joins and receipts are evidence not specified.'));
    if (!example && row.source?.id) {
      const expand = html('button', 'Expand original task, session and mapping code'); expand.type = 'button';
      expand.addEventListener('click', () => inspect(row.source.id)); panel.append(expand);
    }
    for (const edge of $('lineage-graph').querySelectorAll('[data-trace-row]')) edge.classList.toggle('trace-selected', edge.dataset.traceRow === row.id);
    animation();
  }
  function drawTrace() {
    const example = $('lineage-example').checked ? data.illustrative_example : null;
    const trace = data.transformation_trace;
    const rows = example ? example.table_correspondences.map((r, i) => ({...r, id: `example-table-${i}`, source: {name: r.source}, targets: [{label: r.target, status: 'generated_mock_reference', columns_excerpt: r.columns_excerpt, column_count: r.column_count}]})) : data.page.items;
    const svg = $('lineage-graph'); svg.replaceChildren();
    const height = Math.max(640, 160 + rows.length * 112);
    base = [0, 0, 1500, height]; fit();
    const title = example ? example.title : data.selected.name;
    svg.append(svgNode('title', {}, `${title}: source to Databricks transformation trace, not execution`),
      svgNode('desc', {}, example ? `${example.status}. ${example.execution_note}` : 'One row per task in the selected XML. Tasks without a saved match are marked not mapped.'));
    for (const [x, text] of [[30, 'SOURCE PACKAGE / WORKFLOW / TASK'], [635, 'EXPLICIT CORRESPONDENCE'], [930, 'LAKEFLOW JOB / TASK / ARTIFACTS']]) svg.append(svgNode('text', {x, y: 26, class: 'lineage-column'}, text));
    svg.append(svgNode('text', {x: 30, y: 55}, example ? 'Reference XML' : 'Selected XML'),
      svgNode('text', {x: 30, y: 80}, title), svgNode('text', {x: 930, y: 55}, example ? example.job.name : trace.job.design_title ? `Design: ${trace.job.design_title}` : 'Not generated'),
      svgNode('text', {x: 930, y: 80, class: 'lineage-kind'}, example ? 'Generated MOCK / historical success, not live status' : 'Proposed or recorded draft / runtime unknown'));
    const alternative = $('lineage-trace-text'); alternative.replaceChildren();
    rows.forEach((row, i) => {
      const y = 120 + i * 112, targets = row.targets || [], mapped = targets.length > 0;
      const path = svgNode('path', {d: `M590 ${y + 36} C700 ${y + 36} 820 ${y + 36} 930 ${y + 36}`, class: `lineage-edge ${mapped ? 'lineage-correspondence trace-selected' : 'lineage-unmapped'}`, 'data-trace-row': row.id});
      path.append(svgNode('title', {}, mapped ? 'Explicit saved coverage or illustrative contract identity, not runtime flow' : 'Not mapped. Placeholder only, not a correspondence edge')); svg.append(path);
      for (const [x, label, sub, stage] of [[30, row.source.name, example ? 'Source-derived XML identity (reference)' : `Source derived: ${row.source.task_type || 'task'}`, 'stage-source'],
        [930, mapped ? targets.map(t => t.label).join(' + ') : 'Not mapped', mapped ? targets.map(t => t.status.replaceAll('_', ' ')).join(' / ') : 'Evidence not specified', mapped ? (example ? 'stage-generated' : 'stage-proposed') : 'stage-missing']]) {
        const g = svgNode('g', {transform: `translate(${x},${y})`, class: `lineage-node ${stage}`, role: 'button', tabindex: 0, 'aria-label': `What changed: ${label}`});
        g.append(svgNode('rect', {width: 560, height: 82, rx: 12}), svgNode('text', {x: 14, y: 23}, label.length > 68 ? label.slice(0, 65) + '…' : label),
          svgNode('text', {x: 14, y: 44, class: 'lineage-kind'}, sub.length > 76 ? sub.slice(0, 73) + '…' : sub),
          svgNode('text', {x: 14, y: 65, class: 'lineage-kind'}, x === 30 && !example ? `${row.source.sessions.map(s => s.name).join(', ') || 'No session'} → ${row.source.mappings.map(m => m.name).join(', ') || 'No mapping'}`.slice(0, 76) : 'Click to inspect code, paths and changes'), svgNode('title', {}, label));
        g.addEventListener('click', () => showChanges(row, !!example));
        g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); showChanges(row, !!example); } }); svg.append(g);
      }
      const detail = html('details'); detail.append(html('summary', `${row.source.name} → ${mapped ? targets.map(t => t.label).join(' + ') : 'Not mapped'}`), html('p', row.changes || 'Evidence not specified.'));
      technical(detail, 'Original task → session → mapping hierarchy', row.source);
      technical(detail, 'Target tasks, artifacts and columns', targets); alternative.append(detail);
    });
    if (!rows.length) svg.append(svgNode('text', {x: 30, y: 160}, 'No task instances returned. No correspondence invented.'));
    const status = example ? `${example.status}. ${example.execution_note} ${example.task_mapping_note}` : 'Selected-workflow trace. Source derived versus proposed / saved draft receipts. Execution result unknown. Not a whole-package conversion.';
    $('lineage-trace-status').textContent = status;
    $('lineage-graph-caption').textContent = example ? `${rows.length} explicit reference table counterparts. ${example.excerpt_note}` : `${rows.length}/${data.page.total} task rows, offset ${data.page.offset}. Continue with Previous/Next records. ${trace.design_truncated ? 'Saved design projection is bounded; incomplete target coverage is not a conversion claim.' : ''} Red dashed placeholders mean Not mapped, not a migration connector.`;
    if (example) {
      technical(alternative, 'Contract substitutions, not execution receipts', example.changes);
      alternative.append(html('p', example.code_note));
      if (example.execution_evidence) {
        technical(alternative, 'Historical MOCK execution proof, not current selected-user status', example.execution_evidence);
        const job = html('a', 'Open runnable VYVX MOCK Job (Databricks sign-in required)');
        job.href = example.execution_evidence.job_url; job.target = '_blank'; job.rel = 'noopener'; alternative.append(job);
        alternative.append(html('p', example.execution_evidence.rerun_instructions));
      }
      const link = html('a', 'Open sanitized reference manifest'); link.href = '/static/lineage-example.json'; link.target = '_blank'; link.rel = 'noopener'; alternative.append(link);
      const library = html('details'); library.append(html('summary', `Generated MOCK Job library (${example.job.tasks.length} tasks, no inferred source match)`));
      for (const task of example.job.tasks) technical(library, task.task_key, task);
      alternative.append(library);
    }
    animation();
  }
  function draw() {
    if (tracing && (data.family === 'trace' || $('lineage-example').checked)) { drawTrace(); return; }
    const svg = $('lineage-graph'); svg.replaceChildren();
    if (!data.focus) return;
    const rootNode = data.focus.nodes.items.find(n => n.id === data.focus.id);
    const visibleKinds = new Set(['workflow', 'taskinstance', 'task', 'session', 'mapping', 'instance', 'source', 'target', 'transformation', 'unresolved_reference', 'instance_field']);
    const nodes = data.focus.nodes.items.filter(n => n.id === data.focus.id || visibleKinds.has(n.kind));
    const left = nodes.filter(n => n.id !== data.focus.id && data.focus.edges.items.some(e => e.target === data.focus.id && e.source === n.id));
    const right = nodes.filter(n => n.id !== data.focus.id && !left.includes(n));
    const height = Math.max(640, 100 + Math.max(left.length, right.length) * 76);
    base = [0, 0, 1500, height]; fit();
    svg.append(svgNode('title', {}, 'Exact canonical lineage. Click a node to expand one source hop. Not execution.'),
      svgNode('desc', {}, `Selected source: ${rootNode?.name || 'source object'}. Only returned connections animate. Missing references are red. The Lakeflow outline has not been generated.`));
    const defs = svgNode('defs'), marker = svgNode('marker', { id: 'lineage-arrow', viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
    marker.append(svgNode('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: 'currentColor' })); defs.append(marker); svg.append(defs);
    for (const [x, title] of [[32, 'UPSTREAM / CANONICAL OWNERS'], [450, 'SELECTED SOURCE OBJECT'], [840, 'CHILDREN / DOWNSTREAM'], [1230, 'LAKEFLOW OUTLINE']])
      svg.append(svgNode('text', { x, y: 30, class: 'lineage-column' }, title));
    const positions = new Map();
    left.forEach((n, i) => positions.set(n.id, [30, 70 + i * 76]));
    right.forEach((n, i) => positions.set(n.id, [840, 70 + i * 76]));
    positions.set(data.focus.id, [450, Math.max(70, height / 2 - 28)]);
    const edgeLayer = svgNode('g', { class: 'lineage-edges' }); svg.append(edgeLayer);
    for (const e of data.focus.edges.items) {
      if (!positions.has(e.source) || !positions.has(e.target)) continue;
      const [sx, sy] = positions.get(e.source), [tx, ty] = positions.get(e.target);
      const forward = sx <= tx, x1 = sx + (forward ? 340 : 0), x2 = tx + (forward ? 0 : 340);
      const path = svgNode('path', { d: `M${x1},${sy + 28} C${(x1 + x2) / 2},${sy + 28} ${(x1 + x2) / 2},${ty + 28} ${x2},${ty + 28}`,
        class: `lineage-edge${e.source === selectedNode || e.target === selectedNode || !selectedNode ? ' trace-selected' : ''}`, 'marker-end': 'url(#lineage-arrow)' });
      path.append(svgNode('title', {}, `${e.kind}: ${JSON.stringify(e.attributes || {})}. Canonical edge ${e.id}, not runtime evidence.`)); edgeLayer.append(path);
    }
    for (const n of nodes) {
      const [x, y] = positions.get(n.id);
      const missing = n.kind === 'unresolved_reference' || n.kind === 'instance_field' && n.attributes?.status !== 'resolved';
      const g = svgNode('g', { transform: `translate(${x},${y})`, tabindex: 0, role: 'button',
        'aria-label': `Expand ${n.kind} ${n.name}`, class: `lineage-node ${missing ? 'stage-missing' : 'stage-source'}${n.id === data.focus.id ? ' current' : ''}` });
      g.append(svgNode('rect', { width: 340, height: 58, rx: 12 }), svgNode('text', { x: 14, y: 20, class: 'lineage-kind' }, n.kind.replaceAll('_', ' ')),
        svgNode('text', { x: 14, y: 42 }, n.name.length > 42 ? `${n.name.slice(0, 39)}...` : n.name),
        svgNode('title', {}, `${n.name}\nID: ${n.id}\n${missing ? 'Missing or ambiguous exported reference' : 'Captured source, not converted'}`));
      g.addEventListener('click', () => inspect(n.id));
      g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inspect(n.id); } }); svg.append(g);
    }
    const proposed = svgNode('g', { transform: 'translate(1230,70)', class: 'lineage-node stage-proposed' });
    proposed.append(svgNode('rect', { width: 245, height: 90, rx: 12 }), svgNode('text', { x: 14, y: 24 }, 'Proposed Lakeflow Job'),
      svgNode('text', { x: 14, y: 50, class: 'lineage-kind' }, 'Not generated'), svgNode('text', { x: 14, y: 72, class: 'lineage-kind' }, 'No verified correspondence')); svg.append(proposed);
    $('lineage-graph-caption').textContent = `${nodes.length} of ${data.focus.nodes.total} nearby objects shown. ${data.focus.edges.items.length} of ${data.focus.edges.total} connections shown. Expand a node to explore its sessions, mappings, tables and fields. More records are available below.`;
    animation();
  }
  function render() {
    const select = $('lineage-workflow'); select.replaceChildren();
    const all = new Map(data.selected_workflows.map(w => [w.workflow_id, { ...w, selected: true }]));
    for (const w of data.cohort) if (!all.has(w.workflow_id)) all.set(w.workflow_id, w);
    for (const w of all.values()) { const option = html('option', `${w.selected ? 'Target' : 'Source dependency'}: ${w.name}`); option.value = w.workflow_id; select.append(option); }
    select.value = data.workflow_id || ''; workflow = data.workflow_id || '';
    const summary = $('lineage-summary'); summary.replaceChildren();
    summary.append(html('p', `${data.selected_workflow_count}/10 selected migration targets. ${data.cohort.length} workflow definitions in this literal-invocation cohort. 0 verified business migrations. Canonical inventory complete: ${data.coverage.canonical_inventory_complete}; semantic / external SQL lineage: incomplete.`));
    for (const w of data.selected_workflows) {
      const button = html('button', w.name); button.type = 'button'; button.className = 'lineage-workflow-chip'; button.addEventListener('click', () => { workflow = w.workflow_id; root = ''; selectedNode = ''; offset = 0; load(); }); summary.append(button);
    }
    technical(summary, 'XML provenance, exact IDs, version and capability limits', { scope: data.scope, input_version: data.input_version, coverage: data.coverage, totals: data.totals });
    const tree = $('lineage-hierarchy'); tree.replaceChildren();
    const sourceFacts = $('lineage-workflow-facts'); sourceFacts.replaceChildren();
    sourceFacts.append(html('p', `Selected workflow: ${data.selected?.name || 'None'}. The source view shows the original XML.`));
    for (const n of data.focus?.nodes.items || []) {
      const b = html('button', `${n.kind}: ${n.name}`); b.type = 'button'; b.addEventListener('click', () => inspect(n.id)); tree.append(b);
    }
    tree.append(html('p', `${data.focus?.nodes.items.length || 0}/${data.focus?.nodes.total || 0} one-hop objects. Canonical evidence remains paged; expand each task, session or mapping to follow its exact hierarchy.`));
    if (data.family === 'trace') {
      tree.replaceChildren();
      for (const row of data.page.items) {
        const children = html('details'); children.append(html('summary', row.source.name));
        for (const object of [row.source, ...row.source.sessions, ...row.source.mappings]) {
          const b = html('button', `${object.kind}: ${object.name}`); b.type = 'button'; b.addEventListener('click', () => inspect(object.id)); children.append(b);
        }
        tree.append(children);
      }
      tree.append(html('p', `${data.page.items.length}/${data.page.total} task hierarchies. Session and mapping links use exact canonical IDs. Continue through the paged evidence controls.`));
    }
    const targetPanel = $('lineage-proposed'); targetPanel.replaceChildren();
    const trace = data.transformation_trace;
    $('lineage-proposed-status').textContent = trace?.job.design_title ? `Saved design: ${trace.job.design_title}. Not a deployed Job name. Runtime unknown. Source-to-task links require explicit saved coverage.` : 'Not generated in this projection. No current explicit source-to-Databricks correspondence. Destinations unassigned, not fabricated.';
    if (data.family === 'trace') for (const r of data.page.items) for (const t of r.targets) technical(targetPanel, `${t.label}: ${t.status}`, t);
    if ($('lineage-example').checked && data.illustrative_example) targetPanel.append(html('p', 'Illustrative mock library is separate from this selected package. It is not a persisted user conversion or runtime observation.'));
    const relations = $('lineage-relations'); relations.replaceChildren();
    if (data.selected) {
      relations.append(html('h3', 'Source children versus proposed jobs'), html('p', `${data.selected.name}: ${data.selected.source_children.length} literal child candidates, ${data.selected.source_callers.length} direct callers. Original async launch is not an enforced completion dependency. No entire-package conversion or runtime success is established.`));
      const mappings = data.selected.mapping_targets || [];
      const details = html('details');
      details.append(html('summary', `Captured mappings and target definitions (${mappings.length})`));
      details.append(html('p', 'Source XML definition relationships only. A wired source path does not prove a physical binding, approved destination, conversion or output parity.'));
      if (!mappings.length) details.append(html('p', 'No mapping definitions returned for this workflow.'));
      for (const mapping of mappings) {
        details.append(html('h4', mapping.mapping_name || 'Mapping name unavailable'));
        if (!mapping.targets?.length) details.append(html('p', 'No target definitions returned for this mapping.'));
        else {
          const list = html('ul');
          for (const target of mapping.targets) list.append(html('li', `${target.name || 'Target name unavailable'}: ${target.role || 'role unknown'}; ${target.wired_source_instance_count ?? 'unknown'} captured wired source instances. Destination unassigned; approval not recorded.`));
          details.append(list);
        }
      }
      relations.append(details);
      technical(relations, 'Exact upstream/downstream IDs, task-session-mapping uses and target assignments', data.selected);
    }
    const calls = $('lineage-calls'); calls.replaceChildren();
    const names = new Map(data.cohort.map(w => [w.workflow_id, w.name]));
    const neighborhood = data.invocation_neighborhood?.items || [];
    calls.setAttribute('viewBox', `0 0 1200 ${Math.max(130, 70 + neighborhood.length * 70)}`);
    calls.append(svgNode('title', {}, 'Exact literal invocation candidates. Not execution order or completion dependencies.'));
    neighborhood.forEach((call, i) => {
      const y = 32 + i * 70, exact = call.candidate_workflow_ids.length === 1 && call.resolution === 'scoped_literal_candidate';
      const source = call.caller_workflow_id, target = exact ? call.candidate_workflow_ids[0] : null;
      const path = svgNode('path', { d: `M420 ${y + 26} L770 ${y + 26}`, class: 'lineage-invocation', 'marker-end': 'url(#lineage-arrow)' });
      path.append(svgNode('title', {}, `${call.id}: ${call.wait_mode || 'unspecified'}; ${call.completion_semantics}; runtime not verified.`));
      if (exact) calls.append(path);
      calls.append(svgNode('text', { x: 438, y: y + 20, class: 'lineage-kind' }, exact ? `${call.wait_mode || 'unspecified'} / literal candidate only` : 'Unresolved: no edge invented'));
      for (const [id, x, title] of [[source, 10, names.get(source) || source], [target, 780, target ? names.get(target) || target : 'External / dynamic child unresolved']]) {
        const g = svgNode('g', { transform: `translate(${x},${y})`, class: `lineage-node ${id ? 'stage-source' : 'stage-missing'}`, ...(id ? { tabindex: 0, role: 'button', 'aria-label': `Focus workflow ${title}` } : {}) });
        g.append(svgNode('rect', { width: 410, height: 48, rx: 10 }), svgNode('text', { x: 12, y: 29 }, title.length > 49 ? title.slice(0, 46) + '...' : title), svgNode('title', {}, `${title}\n${id || call.resolution_reason}`));
        if (id) { const focus = () => { workflow = id; root = ''; offset = 0; selectedNode = ''; load(); }; g.addEventListener('click', focus); g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); focus(); } }); } calls.append(g);
      }
    });
    if (!neighborhood.length) calls.append(svgNode('text', { x: 20, y: 65 }, 'No captured literal invocation sites for this workflow. Other launch mechanisms are not inferred.'));
    const inspector = $('lineage-inspector'); inspector.replaceChildren();
    inspector.append(html('h3', `Linked ${family}: ${offset + data.page.items.length ? offset + 1 : 0} to ${offset + data.page.items.length} of ${data.page.total}`),
      html('p', `${root ? 'Filtered to the expanded object. Return to workflow to clear this scope.' : 'Entire invocation cohort, including unselected callers and children.'} Continuation: ${data.page.next_offset ?? 'none'}. No hidden truncation or execution claim.`));
    for (const row of data.page.items) {
      const title = row.name || row.role || row.kind || row.record?.attributes?.NAME || row.workflow_name || row.id;
      technical(inspector, `${title}${row.status ? ` (${row.status})` : ''}`, row);
    }
    draw(); controls();
  }
  async function load() {
    if (busy || $('page-readiness').hidden) return;
    const captured = scope(), serial = ++epoch, [project, packageHash] = JSON.parse(captured);
    if (project && !packageHash) { clear('Select a completed XML package first.'); return; }
    busy = true; play = false; controls();
    $('lineage-change-detail').replaceChildren();
    if (!tracing) { $('lineage-trace-text').replaceChildren(); $('lineage-trace-status').textContent = 'Source-only expansion. Trace source → Databricks to compare the selected workflow.'; }
    $('lineage-status').textContent = 'Reading pinned canonical lineage. No models, migration Jobs or storage restore requested.';
    const current = () => serial === epoch && captured === scope() && !$('page-readiness').hidden;
    try {
      const params = new URLSearchParams({ family, offset: String(offset), limit: '100', scenario: $('readiness-scenario').value, example: String($('lineage-example').checked) });
      if (workflow) params.set('workflow_id', workflow);
      if (root) params.set('root_id', root);
      if (data) params.set('input_version', data.input_version);
      const headers = { Accept: 'application/json' };
      if (project) { headers['X-Workbench-Project'] = project; headers['X-Workbench-Package'] = packageHash; }
      const response = await fetch(`/api/readiness/lineage?${params}`, { headers, credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error(`Lineage unavailable (HTTP ${response.status}). No fallback or missing-resource inference.`);
      const result = await response.json();
      if (!current()) return;
      if (result.format !== 'canonical-migration-lineage-v1' || result.scope.project_id !== (project || '__legacy__') || project && result.scope.pins.package_hash !== packageHash)
        throw new Error('Lineage scope mismatch. No evidence displayed.');
      data = result; selectedScope = captured; render(); $('lineage-status').textContent = 'Captured XML lineage, not running or migrated. External procedures, SQL dependencies and destinations remain unverified.';
      if (tracing) { play = !motion.matches; animation(); }
    } catch (e) { if (current()) { clear(e.message); } }
    finally { if (serial === epoch) { busy = false; controls(); } }
  }
  $('lineage-load').addEventListener('click', () => { data = null; root = ''; offset = 0; selectedNode = ''; tracing = false; family = 'nodes'; $('lineage-family').value = family; load(); });
  $('lineage-workflow').addEventListener('change', () => { workflow = $('lineage-workflow').value; root = ''; selectedNode = ''; offset = 0; $('lineage-change-detail').replaceChildren(); load(); });
  $('lineage-family').addEventListener('change', () => { family = $('lineage-family').value; tracing = family === 'trace'; offset = 0; load(); });
  $('lineage-trace').addEventListener('click', () => { tracing = true; root = ''; family = 'trace'; $('lineage-family').value = family; offset = 0; selectedNode = ''; load(); });
  $('lineage-example').addEventListener('change', () => { tracing = true; root = ''; family = 'trace'; $('lineage-family').value = family; offset = 0; $('lineage-change-detail').replaceChildren(); load(); });
  $('lineage-clear').addEventListener('click', () => { play = false; tracing = false; selectedNode = ''; $('lineage-graph').replaceChildren(); $('lineage-trace-text').replaceChildren(); $('lineage-change-detail').replaceChildren(); $('lineage-graph-caption').textContent = ''; $('lineage-trace-status').textContent = 'Trace cleared. Selected source inventory is retained. Trace again to compare.'; animation(); });
  $('lineage-prev').addEventListener('click', () => { offset = Math.max(0, offset - data.page.limit); load(); });
  $('lineage-next').addEventListener('click', () => { offset = data.page.next_offset; load(); });
  $('lineage-home').addEventListener('click', () => { root = ''; selectedNode = ''; offset = 0; load(); });
  $('lineage-in').addEventListener('click', () => zoom(0.8)); $('lineage-out').addEventListener('click', () => zoom(1.25));
  $('lineage-fit').addEventListener('click', fit);
  $('lineage-play').addEventListener('click', () => { play = !play; animation(); });
  $('lineage-graph').addEventListener('keydown', e => {
    if (e.target !== $('lineage-graph')) return;
    if (['+', '=', '-', '0', 'Home', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) e.preventDefault();
    if (e.key === '+' || e.key === '=') zoom(0.8); else if (e.key === '-') zoom(1.25);
    else if (e.key === '0' || e.key === 'Home') fit();
    else { const delta = view[2] * 0.08; if (e.key === 'ArrowLeft') view[0] -= delta; if (e.key === 'ArrowRight') view[0] += delta; if (e.key === 'ArrowUp') view[1] -= delta; if (e.key === 'ArrowDown') view[1] += delta; setView(); }
  });
  document.addEventListener('change', e => { if (['project-select', 'project-package', 'estate-package-select', 'readiness-scenario'].includes(e.target.id)) clear(); });
  new MutationObserver(() => { if ($('page-readiness').hidden || scope() !== selectedScope) clear(); }).observe($('page-readiness'), { attributes: true, attributeFilter: ['hidden'] });
  if ($('package-ready')) new MutationObserver(() => { if ($('package-ready').dataset.understanding !== 'loaded' || scope() !== selectedScope) clear(); }).observe($('package-ready'), { attributes: true, attributeFilter: ['data-understanding'] });
  document.addEventListener('visibilitychange', animation); motion.addEventListener('change', () => { if (motion.matches) play = false; controls(); });
  controls();
})();