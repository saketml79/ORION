/* Estate-only, current-scope source inventory. Metadata only until selection. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const kinds = {repository: 'Repositories', folder: 'Folders', mapping: 'Mappings', mapplet: 'Mapplets',
    workflow: 'Workflows', worklet: 'Worklets', session: 'Sessions', source: 'Sources', target: 'Targets', connectionreference: 'Connection references'};
  const node = (tag, text = '', className = '') => { const n = document.createElement(tag); n.textContent = text; n.className = className; return n; };
  const button = (text, fn) => { const n = node('button', text); n.type = 'button'; n.addEventListener('click', fn); return n; };
  let context = null, epoch = 0, selection = 0, listController, detailController, opener, kind, version, records = [], total = null;
  let section = 'overview', selected = null, detailOffset = 0, detailTotal = 0, allMode = true, listOffset = 0;
  let preloadController, preloadFlight = null, preloadCache = null, preloadMessage = '';
  const dialog = node('dialog', '', 'inventory-dialog'); dialog.id = 'inventory-dialog';
  dialog.setAttribute('aria-labelledby', 'inventory-dialog-title');
  const header = node('header'), title = node('h2'); title.id = 'inventory-dialog-title';
  const close = button('×', () => dialog.close()); close.className = 'inventory-close'; close.setAttribute('aria-label', 'Close inventory');
  header.append(title, close);
  const form = node('form', '', 'inventory-search'), search = node('input'); search.type = 'search'; search.maxLength = 200;
  search.setAttribute('aria-label', 'Search inventory by name'); search.placeholder = 'Search names';
  const submit = node('button', 'Search'); submit.type = 'submit';
  const showAll = button('Show all metadata', () => { allMode = true; loadList(0); });
  const pageMode = button('100 per page', () => { allMode = false; loadList(0); });
  const prev = button('Previous', () => { allMode = false; loadList(Math.max(0, listOffset - 100)); });
  const next = button('Next', () => { allMode = false; loadList(listOffset + 100); });
  form.append(search, submit, showAll, pageMode, prev, next);
  const status = node('p', '', 'inventory-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
  const body = node('div', '', 'inventory-body'), list = node('div', '', 'inventory-list'), detail = node('section', '', 'inventory-detail');
  list.setAttribute('aria-label', 'Inventory records'); detail.setAttribute('aria-label', 'Selected record details');
  body.append(list, detail); dialog.append(header, form, status, body); document.body.append(dialog);
  const current = (ticket, captured) => dialog.open && ticket === epoch && captured === context;
  function invalidate() { ++epoch; ++selection; listController?.abort(); detailController?.abort(); records = []; selected = null; version = null; }
  function reportPreload(message) {
    preloadMessage = message;
    document.dispatchEvent(new CustomEvent('orion:inventory-preload', {detail: {message}}));
  }
  function clearPreload() {
    preloadController?.abort(); preloadController = null; preloadFlight = null; preloadCache = null;
    reportPreload('');
  }
  function clearDetail() { ++selection; detailController?.abort(); selected = null; detail.replaceChildren(node('p', 'Select a record to load its canonical details.')); }
  dialog.addEventListener('close', () => {
    invalidate(); list.replaceChildren(); detail.replaceChildren(); document.documentElement.classList.remove('inventory-modal-open');
    if (opener?.isConnected && !opener.closest('[inert]')) opener.focus({preventScroll: true});
  });
  function setContext(value) {
    const key = JSON.stringify(value);
    if (key !== context) { clearPreload(); context = key; if (dialog.open) dialog.close(); else invalidate(); }
    if (value.ready && !preloadCache && !preloadFlight) startPreload(key);
  }
  async function read(path, signal, captured) {
    const scope = JSON.parse(captured), headers = {'Accept': 'application/json'};
    if (scope.projectId) { headers['X-Workbench-Project'] = scope.projectId; headers['X-Workbench-Package'] = scope.packageHash; }
    const response = await fetch(path, {headers, signal, credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error(`Inventory read failed (HTTP ${response.status}). No absence or conversion inferred. Close and reopen to retry.`);
    return response.json();
  }
  function startPreload(captured) {
    const controller = new AbortController(); preloadController = controller;
    const flight = (async () => {
      let offset = 0, expectedTotal = null, expectedVersion = null;
      const byKind = Object.fromEntries(Object.keys(kinds).map(key => [key, []]));
      const ids = new Set();
      reportPreload('Loading package inventory lists...');
      try {
        do {
          const query = new URLSearchParams({offset: String(offset)});
          if (expectedVersion) query.set('input_version', expectedVersion);
          const page = await read(`/api/inventory-preload?${query}`, controller.signal, captured);
          if (context !== captured || controller.signal.aborted) return;
          if (!Number.isSafeInteger(page.total) || page.total < 0 || page.total > 20000
              || page.offset !== offset || !Array.isArray(page.items) || page.items.length > 1000
              || !page.input_version || (expectedVersion && page.input_version !== expectedVersion)
              || (expectedTotal !== null && page.total !== expectedTotal)
              || offset + page.items.length > page.total || (offset < page.total && !page.items.length))
            throw new Error('Inventory changed or a page was incomplete.');
          expectedTotal = page.total; expectedVersion = page.input_version;
          for (const row of page.items) {
            if (!row || !Object.prototype.hasOwnProperty.call(kinds, row.kind) || typeof row.id !== 'string' || !row.id
                || typeof row.name !== 'string' || ids.has(row.id)) throw new Error('Inventory contains an invalid or duplicate record.');
            ids.add(row.id); byKind[row.kind].push(row);
          }
          offset += page.items.length;
          reportPreload(`Loading package inventory lists: ${offset} of ${expectedTotal} records.`);
        } while (offset < expectedTotal);
        if (context === captured && !controller.signal.aborted) {
          preloadCache = {context: captured, version: expectedVersion, byKind};
          reportPreload(`Package inventory lists ready: ${offset} records.`);
        }
      } catch (error) {
        if (context === captured && !controller.signal.aborted)
          reportPreload('Inventory lists could not be preloaded. Open a category to load its pages.');
      }
    })();
    preloadFlight = flight;
    flight.finally(() => { if (preloadFlight === flight) preloadFlight = null; });
  }
  function json(container, label, value) {
    const disclosure = node('details'), summary = node('summary', label), pre = node('pre', JSON.stringify(value, null, 2));
    disclosure.append(summary, pre); container.append(disclosure);
  }
  function qualified(row) {
    return [row.scope?.repository, row.scope?.folder, row.attributes?.DBDNAME, row.attributes?.OWNERNAME,
      row.attributes?.DATABASETYPE].filter(Boolean).join(' / ') || 'Scope not supplied';
  }
  function renderRows() {
    list.replaceChildren();
    for (const row of records) {
      const b = button('', () => select(row)); b.className = 'inventory-record'; b.dataset.inventoryId = row.id;
      b.setAttribute('aria-pressed', String(row.id === selected?.id));
      b.append(node('strong', row.name), node('span', qualified(row)),
        node('span', Object.entries(row.counts || {}).map(([key, value]) => `${value} ${key}`).join(' · ')));
      list.append(b);
    }
    if (!records.length) list.append(node('p', total === 0 ? 'No matching records.' : 'No records confirmed.'));
  }
  async function loadList(offset) {
    listController?.abort(); clearDetail(); listController = new AbortController();
    const signal = listController.signal, ticket = ++epoch, captured = context, term = search.value;
    records = []; total = null; listOffset = offset; version = null; list.replaceChildren();
    title.textContent = `${kinds[kind]} · Loading`; status.textContent = 'Loading metadata in pages of at most 100. Details load only after selection.';
    prev.disabled = next.disabled = showAll.disabled = true;
    try {
      if (preloadFlight) await preloadFlight;
      if (!current(ticket, captured) || signal.aborted) return;
      if (!term && preloadCache?.context === captured) {
        const filtered = preloadCache.byKind[kind].filter(row => row.name.toLowerCase().includes(term.toLowerCase()));
        version = preloadCache.version; total = filtered.length;
        records = allMode ? filtered : filtered.slice(offset, offset + 100);
        const cursor = offset + records.length;
        title.textContent = `${kinds[kind]} ${total}`;
        status.textContent = `${records.length} of ${total} metadata records loaded from the selected package${allMode ? '' : `, offset ${offset}`}.`;
        renderRows(); prev.disabled = offset === 0; next.disabled = cursor >= total;
        showAll.disabled = allMode && records.length === total;
        return;
      }
      let cursor = offset;
      do {
        const query = new URLSearchParams({kind, inventory: 'true', q: term, offset: String(cursor), limit: '100'});
        if (version) query.set('input_version', version);
        const page = await read(`/api/objects?${query}`, signal, captured);
        if (!current(ticket, captured) || signal.aborted) return;
        if (!Number.isInteger(page.total) || page.total < 0 || !Array.isArray(page.items) || page.items.length > 100
            || page.offset !== cursor || !page.input_version || (version && page.input_version !== version)
            || (total !== null && page.total !== total) || cursor + page.items.length > page.total
            || (cursor < page.total && !page.items.length)) throw new Error('Inventory page is incomplete or changed. Close and reopen to retry.');
        version = page.input_version; total = page.total;
        const known = new Set(records.map(r => r.id));
        if (page.items.some(r => !r.id || r.kind !== kind || known.has(r.id)) || new Set(page.items.map(r => r.id)).size !== page.items.length)
          throw new Error('Ambiguous inventory page. Close and reopen to retry.');
        records.push(...page.items); cursor += page.items.length;
        title.textContent = `${kinds[kind]} ${total}`;
        status.textContent = `${records.length} of ${total} metadata records loaded${allMode ? ' (all records requested)' : `, offset ${offset}`}.`;
        // Incremental bounded requests, never retain field/property payloads for
        // all definitions. Only the current selected record has detail data.
        renderRows();
      } while (allMode && cursor < total);
      if (!current(ticket, captured)) return;
      prev.disabled = offset === 0; next.disabled = cursor >= total; showAll.disabled = allMode && records.length === total;
    } catch (error) {
      if (!current(ticket, captured) || signal.aborted) return;
      status.textContent = error.message; title.textContent = `${kinds[kind]} · Unconfirmed`;
      showAll.disabled = false;
    }
  }
  function tabsFor(row) {
    const result = [['overview', 'Overview']];
    if (['source', 'target', 'mapping', 'mapplet'].includes(row.kind) || row.counts?.fields) result.push(['fields', 'Columns / ports']);
    result.push(['properties', 'SQL / Expressions'], ['children', 'Configuration / children'], ['usage', 'Usage / lineage'], ['evidence', 'Technical evidence'], ['diagnostics', 'Diagnostics']);
    return result;
  }
  function select(row) {
    if (selected?.id === row.id) return;
    selected = row; section = 'overview'; renderRows(); loadDetail();
  }
  async function loadDetail(append = false, remaining = false) {
    detailController?.abort(); detailController = new AbortController();
    const signal = detailController.signal, ticket = ++selection, captured = context, ownerEpoch = epoch, row = selected;
    if (!row) return;
    const valid = () => current(ownerEpoch, captured) && ticket === selection && selected?.id === row.id && !signal.aborted;
    if (!append) {
      detailOffset = 0; detailTotal = 0; detail.replaceChildren(node('h3', row.name), node('p', qualified(row)));
      const tabs = node('div', '', 'inventory-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Record views');
      for (const [value, label] of tabsFor(row)) {
        const tab = button(label, () => { if (section !== value) { section = value; loadDetail(); } });
        tab.id = `inventory-tab-${value}`; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', 'inventory-content');
        tab.setAttribute('aria-selected', String(section === value)); tab.tabIndex = section === value ? 0 : -1;
        tab.addEventListener('keydown', event => {
          const buttons = [...tabs.children], i = buttons.indexOf(tab);
          const target = event.key === 'ArrowRight' ? (i + 1) % buttons.length : event.key === 'ArrowLeft' ? (i + buttons.length - 1) % buttons.length : event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : null;
          if (target !== null) { event.preventDefault(); const value = tabsFor(row)[target][0]; section = value; loadDetail(); detail.querySelector(`#inventory-tab-${value}`)?.focus(); }
        });
        tabs.append(tab);
      }
      const content = node('div'); content.id = 'inventory-content'; content.setAttribute('role', 'tabpanel');
      content.setAttribute('aria-labelledby', `inventory-tab-${section}`); content.tabIndex = 0;
      detail.append(tabs, content);
    }
    const content = detail.querySelector('#inventory-content');
    content.querySelector('.inventory-load-controls')?.remove();
    const progress = node('p', 'Loading source evidence...'); progress.setAttribute('role', 'status'); content.append(progress);
    try {
      do {
        const query = new URLSearchParams({section, offset: String(detailOffset), limit: '100', input_version: version});
        const data = await read(`/api/inventory/${kind}/${encodeURIComponent(row.id)}?${query}`, signal, captured);
        if (!valid()) return;
        if (data.input_version !== version || data.node?.id !== row.id || data.section !== section || data.offset !== detailOffset
            || !Number.isInteger(data.total) || !Array.isArray(data.items) || data.items.length > 100
            || detailOffset + data.items.length > data.total || (detailOffset < data.total && !data.items.length)
            || (detailOffset && data.total !== detailTotal)) throw new Error('Source evidence changed or is incomplete. Close and reopen to retry.');
        detailTotal = data.total;
        if (detailOffset === 0) {
          if (section === 'overview') {
            const suggestion = data.suggested_equivalent;
            content.append(node('h4', suggestion.label), node('p', suggestion.suggestion), node('p', suggestion.rationale), node('p', suggestion.limitations));
            json(content, 'All canonical identity, attributes and source evidence', data.node);
            json(content, 'Suggestion source evidence', suggestion.source_evidence);
            if (row.kind === 'workflow') content.append(button('Open source workflow in Explorer', () => {
              const scope = JSON.parse(captured);
              if (!valid()) return;
              dialog.close(); window.orionOpenWorkflow?.({workflowId: row.id, projectId: scope.projectId, packageHash: scope.packageHash});
            }));
          } else {
            if (section === 'evidence') json(content, 'Canonical technical evidence and provenance', {node: data.node, pins: data.pins});
            if (section === 'properties') content.append(node('p', 'Captured source properties in original order. Session overrides and pre/post SQL are phase-specific, not alternative queries. Nested configuration is available under Configuration / children.'));
            if (!data.total) content.append(node('p', 'No records captured in this section. This is not proof of external absence.'));
          }
        }
        for (const item of data.items) {
          const label = item.name || item.attributes?.NAME || item.kind || item.id || 'Source record';
          json(content, `${detailOffset + 1}. ${label}`, item); ++detailOffset;
        }
        progress.textContent = `${detailOffset} of ${detailTotal} ${section} records loaded. Full values are available in each disclosure.`;
      } while (remaining && detailOffset < detailTotal);
      if (valid() && detailOffset < detailTotal) {
        const controls = node('div', '', 'inventory-load-controls');
        controls.append(button('Load next 100', () => loadDetail(true)), button(`Load remaining ${detailTotal - detailOffset}`, () => loadDetail(true, true))); content.append(controls);
      }
    } catch (error) { if (valid()) { progress.textContent = error.message; const retry = button('Retry this section', () => loadDetail()); content.append(retry); } }
  }
  form.addEventListener('submit', event => { event.preventDefault(); allMode = true; loadList(0); });
  window.orionInventory = Object.freeze({setContext, preloadStatus: () => preloadMessage,
    waitForPreload: () => preloadFlight || Promise.resolve(), open(value, count, trigger) {
    if (!kinds[value] || !context || !JSON.parse(context).ready) return;
    if (dialog.open) dialog.close();
    kind = value; opener = trigger; allMode = true; search.value = ''; title.textContent = `${kinds[kind]} ${count}`;
    dialog.showModal(); document.documentElement.classList.add('inventory-modal-open'); search.focus(); loadList(0);
  }});
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) {
      clearPreload(); if (dialog.open) dialog.close();
    }
  }, true);
  document.addEventListener('orion:workspace-changed', () => { clearPreload(); if (dialog.open) dialog.close(); });
  document.getElementById('refresh')?.addEventListener('click', () => { clearPreload(); if (dialog.open) dialog.close(); }, true);
})();