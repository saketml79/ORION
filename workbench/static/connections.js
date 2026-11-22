/* Canonical configuration inventory only. No inference, credentials, or live tests. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const $ = id => document.getElementById(id);
  const page = $('page-evidence'), list = $('connections-list'), status = $('connections-status');
  if (!page || !list || !status) return;
  const previous = $('connections-prev'), next = $('connections-next');
  const scope = () => {
    const project = $('project-select')?.value || '';
    return { project, package: project ? ($('estate-package-select')?.value ?? $('project-package')?.value ?? '') : '' };
  };
  const key = () => JSON.stringify(scope());
  let context = key(), offset = 0, total = 0, serial = 0, controller = null, loading = false, loaded = false, preloading = false;
  const element = (tag, text) => { const el = document.createElement(tag); el.textContent = text; return el; };
  function controls() {
    previous.disabled = loading || offset === 0;
    next.disabled = loading || offset + 100 >= total;
  }
  function clear(message) {
    ++serial; controller?.abort(); controller = null; context = key();
    offset = 0; total = 0; loading = false; loaded = false; preloading = false; list.replaceChildren(); status.textContent = message; controls();
  }
  function row(parent, label, value) {
    const line = element('p', ''); line.append(element('strong', `${label}: `), document.createTextNode(value)); parent.append(line);
  }
  function render(record) {
    const article = element('article', ''); article.className = 'panel section-gap';
    const aliases = Object.entries(record.declared_aliases || {}).map(([name, field]) => `${name}: ${field.value} (${field.status})`).join('; ');
    article.append(element('h3', aliases || 'No declared alias captured'));
    row(article, 'Role / source or target', `${record.role} / ${record.source_target}`);
    row(article, 'Session', record.session?.name || 'Unknown, not captured');
    row(article, 'Component', record.component?.name || 'Unknown, not captured');
    row(article, 'Mapping', record.mapping?.name || 'Unknown, unresolved or not captured');
    row(article, 'Mapping instance', record.mapping_instance?.name || 'Unknown, unresolved or not captured');
    row(article, 'Configured physical binding', `Unknown (${record.physical_binding?.status || 'not_captured'})`);
    row(article, 'Source schema', `Unknown (${record.source_schema?.status || 'not_captured'})`);
    const details = element('details', ''); details.append(element('summary', 'Captured metadata and XML evidence'));
    for (const [label, fields] of [['Connection', record.metadata], ['Component', record.component_metadata]]) {
      for (const field of (Array.isArray(fields) ? fields : []).slice(0, 100)) row(details, `${label} ${field.name}`, `${field.value} (${field.status})`);
    }
    if (record.metadata_truncated || record.component_metadata_truncated) details.append(element('p', 'Metadata display limited to 100 fields per object.'));
    row(details, 'XML locator', record.evidence?.locator || 'Unknown'); article.append(details); list.append(article);
  }
  async function load({preload = false} = {}) {
    if (page.hidden && !preload) return false;
    if (context !== key()) clear('Context changed. Refresh to read this package.');
    const selected = scope(), captured = key(), ticket = ++serial;
    controller?.abort(); controller = new AbortController();
    const activeController = controller;
    if (selected.project && !selected.package) { clear('Select a ready project package first.'); return; }
    const current = () => ticket === serial && captured === key() && (preload || !page.hidden);
    const headers = { Accept: 'application/json' };
    if (selected.project) { headers['X-Workbench-Project'] = selected.project; headers['X-Workbench-Package'] = selected.package; }
    loading = true; preloading = preload; loaded = false; total = 0; list.replaceChildren(); controls(); status.textContent = 'Reading captured XML connection configuration...';
    try {
      const response = await fetch(`/api/connections?offset=${offset}&limit=100`, { method: 'GET', headers,
        credentials: 'same-origin', cache: 'no-store', signal: activeController.signal });
      if (!current()) return;
      if (!response.ok) throw new Error(`Connection inventory unavailable (HTTP ${response.status}). Refresh to retry.`);
      const data = await response.json();
      if (!current()) return;
      if (!Array.isArray(data.items) || !Number.isSafeInteger(data.total) || data.total < 0)
        throw new Error('Connection inventory response is invalid. Refresh to retry.');
      const records = data.items.slice(0, 100);
      total = data.total;
      records.forEach(render);
      loaded = true;
      status.textContent = records.length ? `${offset + 1}-${offset + records.length} of ${total} captured connection records. No live verification.` : 'No connection references, components, or declarations captured in this package. No live discovery performed.';
      return true;
    } catch (error) {
      if (current()) status.textContent = error.name === 'AbortError' ? 'Inventory read interrupted or timed out. Refresh to retry.' : error.message;
    } finally { if (current()) { loading = false; preloading = false; controls(); } }
  }
  window.orionConnections = Object.freeze({preload: () => load({preload: true})});
  $('connections-refresh').addEventListener('click', () => { clear('Refreshing captured configuration.'); load(); });
  previous.addEventListener('click', () => { offset = Math.max(0, offset - 100); load(); });
  next.addEventListener('click', () => { offset += 100; load(); });
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) clear('Context changed. Refresh connections or reopen this page to load.');
  });
  $('refresh')?.addEventListener('click', () => { clear('Refreshing captured configuration.'); load(); });
  new MutationObserver(() => {
    if (context !== key() || (page.hidden && loading && !preloading)) clear('Page changed. Refresh to read captured configuration.');
  }).observe(page, { attributes: true, attributeFilter: ['hidden'] });
  // Initial scoped preload is coordinated by the parent; refresh and pagination are explicit.
})();