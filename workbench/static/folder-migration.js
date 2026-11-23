/* Exact-folder evidence and explicit, nonexecuting candidate generation. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id), page = $('page-folder-migration');
  if (!page) return;
  // This page owns its reads and cancellation. The shared read wrapper has an
  // elapsed deadline, which is deliberately not applied to folder compilation.
  const fetch = window.fetch.bind(window);
  const rows = value => Array.isArray(value) ? value : [];
  const safe = value => window.orionRedaction.text(String(value ?? 'Unknown'));
  const human = value => String(value ?? 'not reported').replaceAll('_', ' ');
  const node = (tag, text, className = '') => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = safe(text ?? 'N/A');
    if (className) n.className = className;
    return n;
  };
  const scope = () => JSON.stringify([page.dataset.projectId || '', page.dataset.packageHash || '', page.dataset.packageReady === 'true']);
  let context = scope(), epoch = 0, controller = null, busy = false;
  let folders = [], folderId = '', plan = null, generated = null, selectedFile = '';
  let folderInventoryLoaded = false, evidenceGraph = null;
  const status = text => { $('fm-status').textContent = safe(text); };
  function controls() {
    const ready = JSON.parse(scope())[2];
    $('fm-folder').disabled = busy || !ready || !folders.length;
    $('fm-refresh').disabled = busy || !ready;
    $('fm-consent').disabled = busy || !plan;
    $('fm-generate').disabled = busy || !plan || !$('fm-consent').checked || plan.candidate_generation_available !== true;
    page.setAttribute('aria-busy', String(busy));
  }
  function clearReport() {
    plan = null; generated = null; evidenceGraph = null; selectedFile = '';
    $('fm-report').replaceChildren(); $('fm-files').replaceChildren();
    $('fm-file-view').replaceChildren(node('p', 'Select a generated file to inspect its exact contents.'));
    $('fm-generation').hidden = true; $('fm-artifacts').hidden = true;
    $('fm-consent').checked = false; $('fm-type').value = 'all'; $('fm-file-search').value = '';
    $('fm-artifact-status').textContent = 'No generation requested.';
  }
  function cancel() { ++epoch; controller?.abort(); controller = null; busy = false; }
  function invalidate() {
    cancel(); context = scope(); folders = []; folderId = ''; folderInventoryLoaded = false; clearReport();
    $('fm-folder').replaceChildren(node('option', 'Select a ready package, then open this tab.'));
    status('Package selection changed. Only the current ready package can be read.'); controls();
  }
  function begin() {
    cancel(); controller = new AbortController(); busy = true; controls();
    const serial = epoch, captured = scope(), id = folderId;
    const [project, hash, ready] = JSON.parse(captured);
    if (!ready || !hash || project && !hash) throw new Error('Select a ready package first.');
    const headers = { Accept: 'application/json' };
    if (project) { headers['X-Workbench-Project'] = project; headers['X-Workbench-Package'] = hash; }
    return { serial, captured, id, hash, headers, signal: controller.signal,
      current: () => serial === epoch && captured === scope() && id === folderId && !page.hidden };
  }
  async function request(url, op, mutation = false) {
    const response = await fetch(url, { method: mutation ? 'POST' : 'GET', signal: op.signal,
      credentials: 'same-origin', cache: 'no-store',
      headers: { ...op.headers, ...(mutation ? { 'Content-Type': 'application/json', 'X-Workbench-Request': '1' } : {}) },
      ...(mutation ? { body: JSON.stringify({ consent: true }) } : {}) });
    if (!response.ok) {
      if (response.status === 404) throw new Error('Folder migration is unavailable. Refresh the page or contact your administrator if the problem continues.');
      throw new Error(`Folder migration request could not be completed (HTTP ${response.status}). Check the selected package and try again.`);
    }
    const data = await response.json();
    if (data.package_hash !== op.hash) throw new Error('Response package does not match the selected package. No evidence displayed.');
    if (op.id && data.folder_id !== op.id) throw new Error('Response folder does not match the selection. No evidence displayed.');
    return data;
  }
  function finish(op) { if (op.serial === epoch) { busy = false; controller = null; controls(); } }
  const endpoint = () => `/api/folder-migration/folders/${encodeURIComponent(folderId)}`;
  async function loadFolders() {
    if (page.hidden || busy || folderInventoryLoaded || !JSON.parse(scope())[2]) return;
    const op = begin(); let loaded = [], offset = 0, total = null;
    clearReport(); status('Loading folders from the selected package.');
    try {
      while (true) {
        const data = await request(`/api/folder-migration/folders?offset=${offset}&limit=50`, op);
        if (!op.current()) return;
        const batch = rows(data.items);
        if (data.offset !== offset || !Number.isInteger(data.total) || data.total < 0 || total !== null && total !== data.total)
          throw new Error('Folder pagination changed or is incomplete. Refresh to read a consistent inventory.');
        total = data.total;
        if (batch.some(f => !f.id || loaded.some(previous => previous.id === f.id)) || new Set(batch.map(f => f.id)).size !== batch.length)
          throw new Error('Folder identities are missing or duplicated. No automatic selection.');
        loaded.push(...batch); offset += batch.length;
        status(`Reading folders: ${loaded.length} of ${total}. All pages are retained.`);
        if (!data.has_more) {
          if (loaded.length !== total) throw new Error('Folder inventory is incomplete. No silently truncated selection.');
          break;
        }
        if (!batch.length || offset >= total) throw new Error('Folder pagination made no valid progress. Refresh the inventory.');
      }
      folders = loaded; folderInventoryLoaded = true;
      $('fm-folder').replaceChildren();
      for (const folder of folders) {
        const option = node('option', `${folder.scope?.repository || 'Repository not supplied'} / ${folder.name} [${folder.id}]`);
        option.value = folder.id; $('fm-folder').append(option);
      }
      // Preferred demo scope only when its exact saved identity is available.
      // Never use a package-global or invented canonical ID.
      const preferred = folders.find(f => f.name === 'B_AP_INVOICELOG') || folders.find(f => f.name === 'B_AP') || folders[0];
      folderId = preferred?.id || ''; $('fm-folder').value = folderId;
      status(folders.length ? `${folders.length} of ${total} folders loaded. Reading the selected folder only.` : 'No canonical folders in this selected package.');
    } catch (error) { if (op.current() && error.name !== 'AbortError') status(error.message); }
    finally { finish(op); }
    if (folderId && op.serial === epoch && !page.hidden) loadPlan();
  }
  async function loadPlan() {
    if (page.hidden || busy || !folderId) return;
    const op = begin(); clearReport(); status('Reading the saved XML for the selected folder. This does not run agents or jobs.');
    try {
      const result = await request(endpoint(), op);
      if (!op.current()) return;
      plan = result; renderReport(); $('fm-generation').hidden = false;
      status(`${plan.folder.name}: folder report loaded. ${folders.length} folders available in this package.`);
    } catch (error) { if (op.current() && error.name !== 'AbortError') status(error.message); }
    finally { finish(op); }
  }
  function panel(title, parent = $('fm-report')) {
    const card = node('article', undefined, 'panel fm-section'); card.append(node('h2', title)); parent.append(card); return card;
  }
  function facts(parent, entries) {
    const dl = node('dl', undefined, 'fm-facts');
    for (const [label, value] of entries) { dl.append(node('dt', label), node('dd', value)); }
    parent.append(dl);
  }
  function list(parent, values, empty = 'None reported.') {
    const ul = node('ul'); for (const value of values) ul.append(node('li', value));
    parent.append(values.length ? ul : node('p', empty));
  }
  function disclosure(parent, title, populate) {
    const details = node('details'); details.append(node('summary', title));
    // Large source evidence is rendered only when its scoped disclosure opens.
    details.addEventListener('toggle', () => {
      if (details.open && !details.dataset.loaded) { details.dataset.loaded = 'true'; populate(details); }
    }); parent.append(details); return details;
  }
  function evidence(parent, value) { parent.append(node('pre', JSON.stringify(value, null, 2), 'fm-evidence')); }
  function graphNodes(kind) { return rows(evidenceGraph?.nodes).filter(n => n.kind === kind); }
  function mappings() {
    const exact = graphNodes('mapping');
    if (evidenceGraph) return exact;
    const linked = new Map();
    rows(plan.tables).forEach(t => rows(t.mapping_ids).forEach((id, i) => linked.set(id, { id, name: rows(t.mapping_names)[i] ?? id })));
    return [...linked.values()];
  }
  function sourceTargets(table) {
    if (!evidenceGraph) return [];
    const nodes = new Map(rows(evidenceGraph.nodes).map(n => [n.id, n]));
    // Mapping co-membership is not field lineage, target writes, or parity.
    const targets = new Map();
    for (const edge of rows(evidenceGraph.edges)) {
      if (!['instance_of', 'shortcut_reference'].includes(edge.kind) || nodes.get(edge.target)?.kind !== 'target') continue;
      let owner = nodes.get(edge.source); const visited = new Set();
      while (owner && !visited.has(owner.id)) {
        visited.add(owner.id);
        if (owner.kind === 'mapping') {
          if (rows(table.mapping_ids).includes(owner.id)) targets.set(edge.target, nodes.get(edge.target));
          break;
        }
        owner = nodes.get(owner.parent_id);
      }
    }
    return [...targets.values()];
  }
  function renderTable(table, grid) {
    const card = node('article', undefined, 'fm-table-card'); card.dataset.sourceId = table.id;
    card.append(node('h3', table.name), node('p', `${table.database_type || 'Database type not reported'} · ${[table.database, table.owner].filter(Boolean).join(' / ') || 'Database / owner not reported'}`));
    facts(card, [ ['Acquisition evidence', human(table.acquisition_mode)],
      ['Fields declared', table.field_count], ['Data rows observed', table.row_count ?? 'Not observed'],
      ['Incremental queries', table.incremental_query_count], ['Initial / snapshot queries', table.initial_or_snapshot_query_count],
      ['Declared source keys (not verified CDC keys)', rows(table.declared_source_keys).join(', ') || 'None declared'],
      ['Verified CDC keys', table.verified_cdc_keys == null ? 'Not reported' : JSON.stringify(table.verified_cdc_keys)],
      ['Verified sequence', table.verified_sequence_by == null ? 'Not reported' : JSON.stringify(table.verified_sequence_by)],
      ['Snapshot completeness', table.snapshot_completeness], ['Source refresh contract', 'Not supplied / unverified'],
      ['Recommended change application', human(table.recommended_change_application)] ]);
    card.append(node('h4', 'Staging and change-application candidates'), node('p', 'Capture source projections and acquisition predicates; propose temporary staging views. No ingestion binding, CDC flow or target publication is verified.'));
    list(card, rows(table.proposed_options));
    card.append(node('h4', 'Exact linked mapping names')); list(card, rows(table.mapping_names), 'No direct source-to-mapping reference captured.');
    card.append(node('h4', 'Target names in linked mappings (not lineage)'));
    list(card, sourceTargets(table).map(t => t.name), evidenceGraph ? 'No resolved target definitions in these mapping references.' : 'Not supplied by the report API. Generate files explicitly to inspect the captured folder graph, never infer targets from source names.');
    const sql = rows(plan.sql_inventory).filter(s => rows(table.sql_ids).includes(s.id));
    disclosure(card, `Source evidence, queries and assumptions (${sql.length} queries)`, details => {
      facts(details, [['Source ID', table.id], ['Mode evidence scope', table.mode_evidence_scope], ['SQL IDs', rows(table.sql_ids).join(', ') || 'None'], ['Source qualifier IDs', rows(table.source_qualifier_ids).join(', ') || 'None']]);
      details.append(node('p', 'Acquisition predicates are XML structure, not proof of refresh cadence, complete snapshots, change-feed semantics, deletion handling, null-key policy, business keys or ordering. MD5 alone does not establish SCD2.'));
      sql.forEach(s => { details.append(node('h4', `${s.role}: ${s.property_name} (${s.status})`), node('pre', s.original_sql, 'fm-evidence')); });
      evidence(details, table.evidence);
    }); grid.append(card);
  }
  function renderReport() {
    $('fm-report').replaceChildren();
    const purpose = panel('Purpose and boundary');
    purpose.append(node('h3', plan.folder.name), node('p', plan.business_purpose?.summary), node('p', `Purpose status: ${human(plan.business_purpose?.status)}. Domain intent: ${human(plan.business_purpose?.domain_intent)}.`));
    if (plan.business_purpose?.captured_description) purpose.append(node('p', plan.business_purpose.captured_description));
    purpose.append(node('p', plan.notice));
    facts(purpose, [['Repository', plan.folder.scope?.repository]]);
    disclosure(purpose, 'Technical package references', details => {
      facts(details, [['Folder ID', plan.folder_id], ['Package SHA-256', plan.package_hash], ['Input version', plan.input_version]]);
    });
    const counts = panel('Exact source facts');
    facts(counts, Object.entries(plan.counts || {}).map(([kind, count]) => [human(kind), count]));
    facts(counts, Object.entries(plan.table_summary || {}).map(([key, count]) => [human(key), count ?? 'Not observed']));
    counts.append(node('p', 'Counts describe XML definitions and occurrences. They are not row counts, conversion completeness or Genie coverage.'));
    const layers = panel('Proposed layer design'), layerGrid = node('div', undefined, 'fm-grid');
    rows(plan.layers).forEach(layer => { const card = node('article', undefined, 'fm-table-card'); card.append(node('h3', layer.name), node('p', human(layer.status), 'fm-label'), node('p', layer.purpose)); layerGrid.append(card); }); layers.append(layerGrid);
    const patterns = panel('Captured transformation patterns');
    facts(patterns, Object.entries(plan.transformation_types || {}).map(([type, count]) => [type, count]));
    const categories = [...new Set(rows(plan.patterns).map(p => p.category))];
    categories.forEach(category => {
      const matches = rows(plan.patterns).filter(p => p.category === category);
      disclosure(patterns, `${human(category)} (${matches.length} captured settings)`, details => matches.forEach(p => {
        details.append(node('h3', p.name), node('pre', p.value, 'fm-evidence')); facts(details, [['Owner ID', p.node_id]]);
      }));
    });
    if (!categories.length) patterns.append(node('p', 'No categorized settings reported. This does not verify semantics.'));
    const tables = panel(`Source / table contracts (${rows(plan.tables).length})`);
    tables.append(node('p', 'All source definitions are shown. SAP / ODBC hints are metadata, not verified connectivity. Exact source names are retained; target associations are shown only from captured references.'));
    const sap = rows(plan.tables).filter(t => t.database_type === 'ODBC' && t.sap_metadata_hint);
    const other = rows(plan.tables).filter(t => !sap.includes(t));
    for (const [label, group] of [['SAP / ODBC metadata', sap], ['Other source definitions', other]]) {
      if (!group.length) continue;
      tables.append(node('h3', `${label} (${group.length})`)); const grid = node('div', undefined, 'fm-grid'); group.forEach(t => renderTable(t, grid)); tables.append(grid);
    }
    const inventory = panel('Scoped mappings, workflows and target definitions');
    const mappingRows = mappings();
    inventory.append(node('p', evidenceGraph ? 'Exact definitions from the generated folder graph. Co-membership is not execution or field lineage.' : 'The report supplies source-linked mapping names, not a complete mapping / target inventory. Explicit generation supplies the full captured graph, including unused mappings. Missing names are not guessed.'));
    disclosure(inventory, `${evidenceGraph ? 'All mapping definitions' : 'Source-linked mappings'} (${mappingRows.length}; API mapping count ${plan.counts?.mapping ?? 'not reported'})`, d => list(d, mappingRows.map(m => `${m.name} [${m.id}]`)));
    disclosure(inventory, `Workflow definitions (${rows(plan.workflows).length})`, d => list(d, rows(plan.workflows).map(w => `${w.name} [${w.id}]`)));
    disclosure(inventory, `Target definitions (${evidenceGraph ? graphNodes('target').length : 'names not supplied'}; API target count ${plan.counts?.target ?? 'not reported'})`, d => list(d, graphNodes('target').map(t => `${t.name} [${t.id}]`), 'Not supplied by this report. Generate files explicitly to read exact target names from captured evidence.'));
    disclosure(inventory, `Workflow invocation evidence (${rows(plan.workflow_invocations).length})`, d => rows(plan.workflow_invocations).forEach(call => {
      d.append(node('h3', call.workflow_name || 'Workflow name not supplied'), node('p', `${human(call.resolution)}. ${call.resolution_reason || 'No resolution reason supplied'}`)); evidence(d, call);
    }));
    const gaps = panel(`Unresolved choices and compiler blockers (${rows(plan.blockers).length})`);
    gaps.append(node('p', 'Choose and verify business keys, source refresh, ordering, snapshot completeness, deletion policy and target writer behavior before any runnable conversion. All generated files remain partial candidates.'));
    disclosure(gaps, 'All exact blockers', d => rows(plan.blockers).forEach(b => { d.append(node('h3', b.code), node('p', b.detail ?? b.message ?? 'No detail supplied')); evidence(d, b); }));
    disclosure(gaps, `SQL inventory (${rows(plan.sql_inventory).length})`, d => rows(plan.sql_inventory).forEach(s => {
      const block = node('article'); block.append(node('h3', `${s.role}: ${s.property_name}`), node('p', `${s.status}. Dialect: ${s.source_dialect || 'not reported'}. Blockers: ${rows(s.blocker_codes).join(', ') || 'none reported, not semantic proof'}.`));
      disclosure(block, `Exact SQL and evidence: ${s.id}`, detail => { detail.append(node('pre', s.original_sql, 'fm-evidence')); evidence(detail, s); }); d.append(block);
    }));
    disclosure(gaps, `Expression candidates (${rows(plan.expression_candidates).length})`, d => rows(plan.expression_candidates).forEach(c => { d.append(node('h3', c.name), node('p', `${human(c.status)}: ${human(c.translation_claim)}`)); evidence(d, c); }));
    disclosure(gaps, 'Evidence flags and nonexecution authority', d => evidence(d, { ...plan.evidence_flags, execution_permitted: plan.execution_permitted, deployment_allowed: plan.deployment_allowed }));
  }
  async function generate() {
    if (busy || !plan || !$('fm-consent').checked || plan.candidate_generation_available !== true) return;
    const op = begin(), version = plan.input_version;
    generated = null; evidenceGraph = null; selectedFile = ''; $('fm-artifacts').hidden = true; renderReport();
    $('fm-artifact-status').textContent = 'Generating nonexecuting candidate files for this exact scope. No models requested.';
    try {
      const result = await request(`${endpoint()}/artifacts`, op, true);
      if (!op.current()) return;
      if (result.input_version !== version) throw new Error('Folder input version changed. Refresh the report and consent again; no files displayed.');
      if (result.execution_permitted !== false || result.deployment_allowed !== false || result.persisted !== false)
        throw new Error('Unexpected artifact authority. No files displayed.');
      if (!Array.isArray(result.artifacts) || result.artifacts.some(a => typeof a.path !== 'string' || typeof a.content !== 'string') || new Set(result.artifacts.map(a => a.path)).size !== result.artifacts.length)
        throw new Error('Artifact contents or exact file identities are incomplete. No files displayed.');
      const graphFile = result.artifacts.find(a => a.path === 'evidence/folder_graph.json');
      if (graphFile) {
        const graph = JSON.parse(graphFile.content);
        if (graph.package?.sha256 !== op.hash || rows(graph.nodes).some(n => n.scope?.folder_id !== op.id))
          throw new Error('Captured graph does not match the selected folder and package. No files displayed.');
        evidenceGraph = graph;
      }
      generated = result; renderReport(); $('fm-artifacts').hidden = false; renderFiles();
      $('fm-artifact-status').textContent = `${result.artifacts.length} files returned by the API. ${human(result.status)}. Not persisted; nothing executed or deployed. Not a complete Genie-equivalent conversion.`;
    } catch (error) { if (op.current() && error.name !== 'AbortError') { generated = null; evidenceGraph = null; $('fm-artifact-status').textContent = safe(error.message); } }
    finally { if (op.serial === epoch) { $('fm-consent').checked = false; } finish(op); }
  }
  function fileStatus(file) {
    if (file.path.startsWith('captured_sql/')) return 'Captured SQL evidence only, commented, not runnable';
    if (file.path.startsWith('evidence/')) return 'Captured structural evidence, not runnable';
    if (file.path === 'layers/stage.py') return 'Opt-in temporary-view scaffold; requires reviewed bindings, schemas and consent, not a complete runnable pipeline';
    if (file.path.startsWith('layers/')) return 'Proposed / blocked layer, comments only, not runnable';
    if (file.path.endsWith('.sql')) return 'Partial syntax candidate; bindings and semantic verification required, not independently runnable';
    return 'Candidate plan / metadata, not runnable';
  }
  function renderFiles() {
    const type = $('fm-type').value, q = $('fm-file-search').value.toLowerCase();
    const files = rows(generated?.artifacts).filter(a => (type === 'all' || a.language === type) && a.path.toLowerCase().includes(q));
    $('fm-files').replaceChildren(); $('fm-file-count').textContent = `${files.length} of ${rows(generated?.artifacts).length} files match. No list truncation.`;
    for (const file of files) {
      const li = node('li'), button = node('button', `${file.path} · ${human(file.language)}`, 'secondary');
      button.type = 'button'; button.setAttribute('aria-pressed', String(file.path === selectedFile));
      button.addEventListener('click', () => { selectedFile = file.path; renderFiles(); showFile(file); }); li.append(button); $('fm-files').append(li);
    }
    if (!files.some(f => f.path === selectedFile)) {
      selectedFile = files[0]?.path || '';
      if (files[0]) { showFile(files[0]); $('fm-files').firstElementChild.firstElementChild.setAttribute('aria-pressed', 'true'); }
      else $('fm-file-view').replaceChildren(node('p', 'No files match this filter. All API files remain available.'));
    }
  }
  function showFile(file) {
    const view = $('fm-file-view'), content = safe(file.content), changed = content !== file.content;
    view.replaceChildren(node('h3', file.path), node('p', `${human(file.language)} · Plain text (no public syntax-highlighting helper)`, 'fm-label'), node('p', fileStatus(file)));
    disclosure(view, 'Technical file reference', details => facts(details, [['Original file SHA-256', file.sha256]]));
    view.append(node('p', changed ? 'Credential values redacted by the existing policy. Copy / download uses this same redacted text; the original API checksum will differ.' : 'Exact API contents. Copy / download preserves whitespace and contents, with credential-value redaction only when needed.'));
    const buttons = node('div', undefined, 'fm-toolbar'), copy = node('button', 'Copy contents', 'secondary'), download = node('button', 'Download file', 'secondary');
    copy.type = 'button'; download.type = 'button'; const note = node('p'); note.setAttribute('role', 'status');
    const capturedEpoch = epoch, capturedScope = scope();
    const current = () => epoch === capturedEpoch && scope() === capturedScope && selectedFile === file.path && !page.hidden;
    copy.addEventListener('click', async () => {
      if (!current()) return;
      try { await navigator.clipboard.writeText(content); if (current()) note.textContent = 'Contents copied.'; }
      catch { if (current()) note.textContent = 'Clipboard unavailable. Select the plain text or download the file.'; }
    });
    download.addEventListener('click', () => {
      if (!current()) return;
      const url = URL.createObjectURL(new Blob([content], { type: 'text/plain;charset=utf-8' }));
      const link = node('a'); link.href = url; link.download = file.path.split('/').pop(); view.append(link); link.click(); link.remove();
      URL.revokeObjectURL(url); note.textContent = 'Selected file download requested.';
    });
    buttons.append(copy, download); const pre = node('pre', undefined, 'fm-code'); pre.tabIndex = 0; pre.setAttribute('aria-label', `${file.language} contents of ${safe(file.path)}`);
    // No unsafe HTML, executable previews, guessed syntax colors or broad masking.
    pre.textContent = content; view.append(buttons, note, pre);
  }
  function sync() {
    if (context !== scope()) invalidate();
    if (page.hidden) { if (busy) cancel(); controls(); return; }
    if (!JSON.parse(scope())[2]) { controls(); return; }
    if (!folderInventoryLoaded) loadFolders(); else if (!plan && folderId && !busy) loadPlan();
  }
  $('fm-folder').addEventListener('change', () => {
    const value = $('fm-folder').value;
    if (value === folderId || !folders.some(f => f.id === value)) return;
    cancel(); folderId = value; clearReport(); loadPlan();
  });
  function refresh() {
    if (page.hidden || busy) return;
    cancel(); clearReport();
    if (folderInventoryLoaded && folderId) loadPlan(); else { folderInventoryLoaded = false; loadFolders(); }
  }
  $('fm-refresh').addEventListener('click', refresh);
  $('refresh')?.addEventListener('click', refresh);
  $('fm-consent').addEventListener('change', controls);
  $('fm-generate').addEventListener('click', generate);
  $('fm-type').addEventListener('change', renderFiles); $('fm-file-search').addEventListener('input', renderFiles);
  document.addEventListener('orion:navigate', sync);
  // Synchronously invalidate before asynchronous package understanding can finish.
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) invalidate();
  });
  new MutationObserver(sync).observe(page, { attributes: true, attributeFilter: ['hidden', 'data-project-id', 'data-package-hash', 'data-package-ready'] });
  controls(); sync();
})();