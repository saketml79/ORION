/* Read-only catalog UI. All source text is inserted with textContent, never HTML. */
(() => {
  'use strict';
  const dialog = document.getElementById('agent-library-dialog');
  const openButton = document.getElementById('agent-tools');
  if (!dialog || !openButton || dialog.dataset.initialized) return;
  dialog.dataset.initialized = 'true';
  const byId = (id) => document.getElementById(id);
  const status = byId('agent-library-status');
  const tabs = [byId('agent-library-tools-tab'), byId('agent-library-knowledge-tab')];
  const panels = [byId('agent-library-tools-panel'), byId('agent-library-knowledge-panel')];
  const state = {
    section: 'tools', category: 'all', view: 'skills', offsets: {tools: 0, knowledge: 0},
    pages: {tools: null, knowledge: null}, controller: null, request: 0, selected: {tools: null, knowledge: null},
  };
  const text = (value) => String(value ?? 'Unknown');
  const make = (tag, value = '', className = '') => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text(value);
    return element;
  };
  const makeSvg = (tag, attrs, parent) => {
    const item = document.createElementNS('http://www.w3.org/2000/svg', tag);
    Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, String(value)));
    parent.append(item);
    return item;
  };
  function setStatus(value) { status.textContent = text(value); }
  function selectTab(index) {
    state.section = index === 0 ? 'tools' : 'knowledge';
    tabs.forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
      panels[i].hidden = i !== index;
    });
  }
  function setFacts(parent, pairs) {
    const list = make('dl', '', 'agent-library-facts');
    for (const [label, value, isCode] of pairs) {
      list.append(make('dt', label));
      const dd = make('dd');
      if (isCode) dd.append(make('code', value)); else dd.textContent = text(value);
      list.append(dd);
    }
    parent.append(list);
  }
  function addSourceLinks(parent, refs) {
    for (const ref of Array.isArray(refs) ? refs : []) {
      const entry = make('p');
      const linkText = text(ref.label || ref.url || 'Source reference');
      try {
        const parsed = new URL(ref.url);
        if (parsed.protocol === 'https:' && ['docs.databricks.com', 'spark.apache.org'].includes(parsed.hostname)) {
          const link = make('a', linkText);
          link.href = parsed.href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          entry.append(link);
        } else entry.textContent = `${linkText}: ${text(ref.url)}`;
      } catch (_) { entry.textContent = linkText; }
      parent.append(entry);
      setFacts(parent, [
        ['Source hash', ref.source_sha256 || 'Not recorded', true],
        ['Section hash', ref.section_sha256 || 'Not recorded', true],
        ['Retrieved', ref.retrieved_at || 'Not recorded'],
        ['Published', ref.published_at || 'Not recorded'],
        ['Updated', ref.updated_at || 'Not recorded'],
        ['Expires', ref.expires_at || 'Not recorded'],
      ]);
      if (ref.excerpt) parent.append(make('p', ref.excerpt));
    }
  }
  function showDetail(section, item) {
    const target = byId(section === 'tools' ? 'agent-library-tools-detail' : 'agent-library-knowledge-detail');
    target.replaceChildren();
    target.append(make('h3', item.name || item.label || item.id));
    if (item.description) target.append(make('p', item.description));
    if (item.text) target.append(make('p', item.text));
    if (item.signature) target.append(make('p', item.signature, 'agent-library-note'));
    const source = item.source || {};
    const lineRef = source.line_start ? `${source.path}#L${source.line_start}${source.line_end && source.line_end !== source.line_start ? `-L${source.line_end}` : ''}` : source.path;
    const facts = [
      ['Kind', item.category || item.node_kind || item.kind],
      ['Roles', (item.roles || []).join(', ') || 'Not role-scoped'],
      ['Source path / lines', lineRef || source.external_url || (source.node_id ? `Knowledge node ${source.node_id}` : 'No local source path')],
    ];
    if (source.file_sha256) facts.push(['File / graph SHA-256', source.file_sha256, true]);
    if (source.snippet_sha256) facts.push(['Snippet SHA-256', source.snippet_sha256, true]);
    if (item.conditional) facts.push(['Availability condition', item.condition || 'Conditional in source']);
    if (item.release_state) facts.push(['Documented release state', item.release_state]);
    if (item.verification) facts.push(['Verification', item.verification]);
    const freshness = item.freshness || {};
    if (freshness.state) facts.push(['Freshness', freshness.state]);
    if (freshness.observed_at || freshness.retrieved_at) facts.push(['Observed / retrieved', freshness.observed_at || freshness.retrieved_at]);
    if (freshness.published_at) facts.push(['Published', freshness.published_at]);
    if (freshness.updated_at) facts.push(['Updated', freshness.updated_at]);
    if (freshness.expires_at) facts.push(['Expires', freshness.expires_at]);
    if (freshness.note) target.append(make('p', freshness.note, 'agent-library-note'));
    if (item.lifecycle) {
      facts.push(['Discard state', item.lifecycle.discard]);
      facts.push(['Superseded by', item.lifecycle.superseded_by]);
    }
    setFacts(target, facts);
    addSourceLinks(target, item.source_refs);
    if (item.source_refs?.length) {
      const refs = item.source_refs.map(ref => ref.url || ref.label).filter(Boolean).join(' · ');
      if (refs) target.append(make('p', `Citations: ${refs}`));
    }
  }
  function drawMap(section, page) {
    const host = byId(section === 'tools' ? 'agent-library-tools-map' : 'agent-library-knowledge-map');
    host.replaceChildren();
    const items = Array.isArray(page.items) ? page.items : [];
    if (!items.length) {
      host.append(make('p', section === 'knowledge' && page.status?.status !== 'available'
        ? `No stored architecture graph is available (${page.status?.status || 'unknown'}). The skill catalog remains separately available.`
        : 'No entries are available for this view.'));
      byId(section === 'tools' ? 'agent-library-tools-detail' : 'agent-library-knowledge-detail').replaceChildren();
      return;
    }
    const roleRows = page.status?.roles || [];
    const itemRoles = [...new Set(items.flatMap(item => item.roles || []))];
    const roles = roleRows.length ? roleRows : itemRoles.map(id => ({id, label: id.replaceAll('_', ' ')}));
    const roleY = new Map(roles.map((role, index) => [role.id, 42 + index * 48]));
    const columns = 4;
    const rowCount = Math.ceil(items.length / columns);
    const height = Math.max(roles.length * 48 + 50, rowCount * 72 + 72, 300);
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 1160 ${height}`);
    svg.setAttribute('role', 'group');
    svg.setAttribute('aria-label', section === 'tools' ? 'Role-to-tool graph' : 'Role-to-source knowledge graph');
    const links = makeSvg('g', {'aria-hidden': 'true'}, svg);
    const roleNodes = makeSvg('g', {}, svg);
    const graphNodes = makeSvg('g', {}, svg);
    const positions = new Map();
    items.forEach((item, index) => {
      const col = index % columns;
      const row = Math.floor(index / columns);
      positions.set(item.id, {x: 318 + col * 207, y: 42 + row * 72, cx: 402 + col * 207, cy: 62 + row * 72});
    });
    for (const item of items) {
      const point = positions.get(item.id);
      for (const role of item.roles || []) {
        const y = roleY.get(role);
        if (y === undefined) continue;
        makeSvg('path', {d: `M146 ${y + 13} C220 ${y + 13}, 238 ${point.cy}, ${point.x} ${point.cy}`, class: 'agent-library-edge'}, links);
      }
    }
    const visibleKnowledgeNodes = new Map();
    items.forEach((item) => {
      const nodeId = item.source?.node_id;
      if (nodeId && !visibleKnowledgeNodes.has(nodeId)) visibleKnowledgeNodes.set(nodeId, positions.get(item.id));
    });
    for (const edge of page.edges || []) {
      const from = visibleKnowledgeNodes.get(edge.source);
      const to = visibleKnowledgeNodes.get(edge.target);
      if (from && to) makeSvg('path', {d: `M${from.cx} ${from.cy} Q${(from.cx + to.cx) / 2} ${(from.cy + to.cy) / 2 - 22} ${to.cx} ${to.cy}`, class: 'agent-library-edge is-knowledge'}, links);
    }
    roles.forEach((role) => {
      const y = roleY.get(role.id);
      const group = makeSvg('g', {class: 'agent-library-role'}, roleNodes);
      makeSvg('rect', {x: 16, y, width: 130, height: 28, rx: 14}, group);
      const label = makeSvg('text', {x: 81, y: y + 18}, group);
      label.textContent = text(role.label || role.id).slice(0, 20);
    });
    items.forEach((item) => {
      const point = positions.get(item.id);
      const group = makeSvg('g', {class: 'agent-library-node', role: 'button', tabindex: '0',
        'aria-label': `${item.name || item.label || item.id}; roles ${(item.roles || []).join(', ')}`,
        'aria-pressed': String(state.selected[section] === item.id)}, graphNodes);
      makeSvg('rect', {x: point.x, y: point.y, width: 168, height: 42, rx: 9}, group);
      const title = makeSvg('text', {x: point.x + 10, y: point.y + 17}, group);
      title.textContent = text(item.name || item.label || item.id).slice(0, 25);
      const caption = makeSvg('text', {x: point.x + 10, y: point.y + 32, class: 'agent-library-node-kind'}, group);
      caption.textContent = text(item.category || item.node_kind || item.kind).slice(0, 26);
      const choose = () => {
        state.selected[section] = item.id;
        host.querySelectorAll('.agent-library-node').forEach(node => node.setAttribute('aria-pressed', String(node === group)));
        showDetail(section, item);
      };
      group.addEventListener('click', choose);
      group.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(); }
      });
    });
    host.append(svg);
    const selected = items.find(item => item.id === state.selected[section]) || items[0];
    state.selected[section] = selected.id;
    showDetail(section, selected);
  }
  function describeStatus(section, page) {
    const value = page.status || {};
    if (section === 'tools') {
      return `${value.analysis_role_count || 0} analysis roles; ${value.analysis_unique_tools || 0} declared analysis tools; ${value.writer_unique_tools || 0} separate writer tools; ${value.harness_operations || 0} harness operations. Internet search: unavailable. This page lists ${page.returned} of ${page.total}.`;
    }
    const graph = value.status === 'available'
      ? `Stored graph ${value.version}; ${value.feature_count} features, ${value.fresh_source_count} source records inside their recorded retrieval window and ${value.expired_source_count} expired.`
      : `Stored architecture graph: ${value.status || 'unavailable'}.`;
    return `${graph} ${value.freshness_note || ''} Internet search: unavailable. This page lists ${page.returned} of ${page.total}.`;
  }
  function updatePager(section, page) {
    state.pages[section] = page;
    const prefix = section === 'tools' ? 'tools' : 'knowledge';
    byId(`agent-library-${prefix}-page`).textContent = page.total
      ? `Showing ${page.offset + 1}–${page.offset + page.returned} of ${page.total}${page.has_more ? '. More entries are available on the next page.' : '. Catalog complete.'}`
      : 'No entries in this view. Catalog count is complete.';
    byId(`agent-library-${prefix}-prev`).disabled = page.offset <= 0;
    byId(`agent-library-${prefix}-next`).disabled = !page.has_more;
  }
  async function load() {
    if (!dialog.open) return;
    state.controller?.abort();
    const controller = new AbortController(); state.controller = controller;
    const ticket = ++state.request;
    const section = state.section;
    const offset = state.offsets[section];
    setStatus('Reading current source catalog… no model or internet search is started.');
    const params = new URLSearchParams({section, offset: String(offset), limit: section === 'tools' ? '100' : '36'});
    if (section === 'tools') params.set('category', state.category);
    else params.set('view', state.view);
    try {
      const headers = {Accept: 'application/json'};
      const projectId = document.getElementById('project-select')?.value || '';
      if (/^[a-f0-9]{32}$/.test(projectId)) headers['X-Workbench-Project'] = projectId;
      const response = await fetch(`/api/agent-library?${params}`, {headers, signal: controller.signal, cache: 'no-store'});
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || `Catalog request failed (${response.status})`);
      if (ticket !== state.request || controller.signal.aborted) return;
      drawMap(section, body);
      updatePager(section, body);
      setStatus(describeStatus(section, body));
    } catch (error) {
      if (error?.name === 'AbortError' || ticket !== state.request) return;
      setStatus(`Catalog unavailable: ${text(error?.message || 'request failed')}. No online search was attempted.`);
      const host = byId(section === 'tools' ? 'agent-library-tools-map' : 'agent-library-knowledge-map');
      host.replaceChildren(make('p', 'The catalog could not be loaded. Use Refresh catalog to retry.'));
      state.pages[section] = null;
      const prefix = section === 'tools' ? 'tools' : 'knowledge';
      byId(`agent-library-${prefix}-page`).textContent = 'Catalog unavailable. No entries are claimed complete.';
      byId(`agent-library-${prefix}-prev`).disabled = true;
      byId(`agent-library-${prefix}-next`).disabled = true;
      const detail = byId(section === 'tools' ? 'agent-library-tools-detail' : 'agent-library-knowledge-detail');
      detail.replaceChildren(make('p', 'No source details are available until the catalog endpoint responds.'));
    }
  }
  function open() {
    if (!dialog.open) dialog.showModal();
    selectTab(state.section === 'tools' ? 0 : 1);
    state.pages[state.section] = null;
    load();
  }
  openButton.addEventListener('click', open);
  byId('agent-library-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    state.controller?.abort();
    state.request += 1;
    openButton.focus();
  });
  tabs.forEach((tab, index) => tab.addEventListener('click', () => {
    selectTab(index);
    if (!state.pages[state.section]) load();
  }));
  byId('agent-library-tools-tab').addEventListener('keydown', (event) => {
    if (event.key === 'ArrowRight') { event.preventDefault(); tabs[1].focus(); selectTab(1); if (!state.pages.knowledge) load(); }
  });
  byId('agent-library-knowledge-tab').addEventListener('keydown', (event) => {
    if (event.key === 'ArrowLeft') { event.preventDefault(); tabs[0].focus(); selectTab(0); if (!state.pages.tools) load(); }
  });
  byId('agent-library-refresh').addEventListener('click', () => { state.pages[state.section] = null; load(); });
  byId('agent-library-tool-category').addEventListener('change', (event) => {
    state.category = event.target.value; state.offsets.tools = 0; state.pages.tools = null; if (state.section === 'tools') load();
  });
  byId('agent-library-knowledge-view').addEventListener('change', (event) => {
    state.view = event.target.value; state.offsets.knowledge = 0; state.pages.knowledge = null; if (state.section === 'knowledge') load();
  });
  for (const section of ['tools', 'knowledge']) {
    const prefix = section;
    byId(`agent-library-${prefix}-prev`).addEventListener('click', () => {
      state.offsets[section] = Math.max(0, state.offsets[section] - (section === 'tools' ? 100 : 36)); load();
    });
    byId(`agent-library-${prefix}-next`).addEventListener('click', () => {
      const page = state.pages[section]; if (!page?.has_more) return;
      state.offsets[section] = page.offset + page.returned; load();
    });
  }
})();
