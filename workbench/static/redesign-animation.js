/* Presentation only. Source-to-target mapping is a proposal, not proof of parity.
 * open({design, references: Map, title, isCurrent?: () => boolean}) returns
 * true when shown, false when unavailable/invalid/stale. close() is idempotent.
 * Main owns asset/button wiring and MUST call close() on context invalidation.
 * isCurrent is an optional synchronous freshness guard, never approval authority.
 * Only whitelisted own data fields are copied. No code/notebook bodies are read.
 */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const STEP_MS = 2800; // 800ms transition followed by a full 2000ms hold.
  const phases = ['Source task instances', 'Coverage correspondence', 'Proposed block transfer', 'Proposed Lakeflow blocks'];
  let active = null;

  const field = (value, key) => {
    if (!value || typeof value !== 'object') return undefined;
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    return descriptor && Object.hasOwn(descriptor, 'value') ? descriptor.value : undefined;
  };
  const id = value => typeof value === 'string' && value.trim() !== '' && !/[\u0000-\u001f\u007f]/u.test(value) ? value : null;
  const text = value => typeof value === 'string' ? value : '';
  const entries = value => Array.isArray(value) ? Array.from({length: value.length}, (_, index) => field(value, String(index))) : [];
  const frozen = rows => Object.freeze(rows.map(row => Object.freeze(row)));
  function snapshot(options) {
    const design = field(options, 'design');
    if (!design || typeof design !== 'object') return null;
    let ignored = 0;
    const tasks = [], seen = new Set(), references = new Map();
    for (const task of entries(field(design, 'tasks'))) {
      const key = id(field(task, 'id'));
      if (!key || seen.has(key)) { ignored++; continue; }
      seen.add(key);
      tasks.push({id: key, label: text(field(task, 'label')) || key,
        implementation_kind: text(field(task, 'implementation_kind')) || 'Implementation kind unavailable'});
    }
    const edges = [], edgeKeys = new Set();
    for (const edge of entries(field(design, 'edges'))) {
      const source = id(field(edge, 'source')), target = id(field(edge, 'target'));
      const key = JSON.stringify([source, target]);
      if (!seen.has(source) || !seen.has(target) || edgeKeys.has(key)) { ignored++; continue; }
      edgeKeys.add(key); edges.push({source, target});
    }
    const coverage = [];
    for (const row of entries(field(design, 'coverage'))) {
      const original = id(field(row, 'original_task_id'));
      if (!original) { ignored++; continue; }
      const targets = [], unique = new Set();
      for (const candidate of entries(field(row, 'proposed_task_ids'))) {
        const key = id(candidate);
        if (!seen.has(key) || unique.has(key)) { ignored++; continue; }
        unique.add(key); targets.push(key);
      }
      coverage.push({original_task_id: original, proposed_task_ids: Object.freeze(targets),
        disposition: text(field(row, 'disposition')) || 'Disposition unavailable'});
    }
    const supplied = field(options, 'references');
    if (supplied instanceof Map) Map.prototype.forEach.call(supplied, (value, key) => {
      if (!id(key)) return;
      references.set(key, Object.freeze({label: text(field(value, 'label')), kind: text(field(value, 'kind')),
        workflow_id: id(field(value, 'workflow_id'))}));
    });
    // The map stays private; no caller-owned containers survive this boundary.
    return Object.freeze({tasks: frozen(tasks), edges: frozen(edges), coverage: frozen(coverage), references,
      title: text(field(options, 'title')) || text(field(design, 'title')) || 'Proposal correspondence', ignored});
  }
  function safe(value) {
    const redact = window.orionRedaction?.text;
    if (typeof redact !== 'function') return value;
    try { const result = redact(value); return typeof result === 'string' ? result : '[Text unavailable]'; }
    catch { return '[Text unavailable]'; }
  }
  function element(tag, value = '', className = '') {
    const node = document.createElement(tag);
    node.textContent = safe(value); node.className = className; return node;
  }
  function svgElement(tag, attributes = {}, value = '') {
    const node = document.createElementNS(NS, tag);
    for (const [key, val] of Object.entries(attributes)) node.setAttribute(key, String(val));
    node.textContent = safe(value); return node;
  }
  // Wrap every character, including long unbroken IDs. No ellipsis or node cap.
  function lines(value, width = 34) {
    const result = [];
    for (const paragraph of safe(value).split(/\r?\n/u)) {
      const chars = Array.from(paragraph);
      let line = '', used = 0;
      for (let offset = 0; offset < chars.length; offset++) {
        // Reserve two monospace cells for Unicode glyphs and tabs.
        const char = chars[offset] === '\t' ? '  ' : chars[offset];
        const size = /^[\x20-\x7e]$/u.test(char) ? 1 : 2;
        if (used + size > width) { result.push(line); line = ''; used = 0; }
        line += char; used += size;
      }
      result.push(line);
    }
    return result;
  }
  function layout(rows, x) {
    let y = 86;
    return rows.map(row => {
      const wrapped = (row.displayParts || row.parts).flatMap(part => lines(part));
      const block = {...row, x, y, height: 28 + wrapped.length * 19, wrapped};
      y += block.height + 28; return block;
    });
  }
  const blockWidth = 316;
  function card(block, className) {
    const group = svgElement('g', {class: className, transform: `translate(${block.x} ${block.y})`});
    group.append(svgElement('title', {}, block.parts.join('\n')));
    group.append(svgElement('rect', {x: 4, y: 6, width: blockWidth, height: block.height, rx: 17, class: 'ora-block-depth'}));
    group.append(svgElement('rect', {width: blockWidth, height: block.height, rx: 17, class: 'ora-block-fill'}));
    const label = svgElement('text', {x: 16, y: 27});
    block.wrapped.forEach((line, index) => label.append(svgElement('tspan', {x: 16, dy: index ? 19 : 0}, line)));
    group.append(label); return group;
  }
  const centerY = block => block.y + block.height / 2;
  function curve(x1, y1, x2, y2) {
    const bend = Math.max(50, Math.abs(x2 - x1) / 2);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  }
  function diagram(data) {
    const sourceRows = new Map();
    for (const row of data.coverage) {
      if (!sourceRows.has(row.original_task_id)) sourceRows.set(row.original_task_id, []);
      sourceRows.get(row.original_task_id).push(row);
    }
    const sources = layout([...sourceRows].map(([key, rows]) => {
      const ref = data.references.get(key), workflow = ref?.workflow_id && data.references.get(ref.workflow_id);
      // Only an exact workflow reference label may name a workflow. Never group by guessed names.
      const workflowLabel = workflow?.kind === 'workflow' && workflow.label ? `Workflow: ${workflow.label}` : null;
      return {id: key, dim: rows.every(row => /^(removed|disabled)$/iu.test(row.disposition)),
        displayParts: [ref?.label || 'Source task label unavailable', ...new Set(rows.map(row => row.disposition))],
        parts: [ref?.label || 'Source label unavailable', `Task instance: ${key}`, ref?.kind || 'Source kind unavailable',
          ...(workflowLabel ? [workflowLabel] : []), ...new Set(rows.map(row => `Disposition: ${row.disposition}`))]};
    }), 24);
    const bridges = layout(data.coverage.map((row, index) => ({...row, id: index,
      displayParts: [row.disposition === 'removed' ? 'Administrative step removed' : row.disposition === 'disabled'
        ? 'Disabled behavior preserved' : row.proposed_task_ids.length > 1 ? 'Split responsibilities'
        : 'Behavior mapped', `${row.proposed_task_ids.length} proposed task${row.proposed_task_ids.length === 1 ? '' : 's'}`],
      parts: [`Coverage ${index + 1}: ${row.disposition}`, `Source instance: ${row.original_task_id}`,
        ...row.proposed_task_ids.map(key => `Proposed: ${key}`),
        ...(!row.proposed_task_ids.length ? ['No valid proposed correspondence supplied'] : [])]})), 452);
    const covered = new Set(data.coverage.flatMap(row => row.proposed_task_ids));
    const targets = layout(data.tasks.map(task => ({id: task.id, displayParts: [task.label, task.implementation_kind.replaceAll('_', ' ')], parts: [task.label, `Proposed task: ${task.id}`,
      task.implementation_kind, ...(!covered.has(task.id) ? ['No coverage correspondence supplied'] : [])]})), 880);
    const sourceById = new Map(sources.map(block => [block.id, block]));
    const targetById = new Map(targets.map(block => [block.id, block]));
    const height = Math.max(270, ...[sources, bridges, targets].map(column => {
      const last = column[column.length - 1]; return last ? last.y + last.height + 40 : 270;
    }));
    const svg = svgElement('svg', {viewBox: `0 0 1280 ${height}`, class: 'ora-svg', role: 'img',
      'aria-labelledby': 'ora-svg-title ora-svg-description'});
    svg.append(svgElement('title', {id: 'ora-svg-title'}, 'Source task instances, coverage correspondence, proposed Lakeflow blocks'));
    svg.append(svgElement('desc', {id: 'ora-svg-description'},
      'Dashed paths show supplied proposal coverage, not source dependencies. Solid violet paths show supplied proposed edges only. All labels are repeated in the accessible text inventory.'));
    for (const [x, label] of [[24, 'SOURCE INSTANCES'], [452, 'COVERAGE CORRESPONDENCE'], [880, 'PROPOSED LAKEFLOW']]) {
      svg.append(svgElement('text', {x, y: 36, class: 'ora-column-title'}, label));
    }
    const links = svgElement('g', {class: 'ora-coverage-links'});
    const transfers = svgElement('g', {class: 'ora-transfers', 'aria-hidden': 'true'});
    for (const bridge of bridges) {
      const source = sourceById.get(bridge.original_task_id);
      links.append(svgElement('path', {d: curve(source.x + blockWidth, centerY(source), bridge.x, centerY(bridge))}));
      for (const key of bridge.proposed_task_ids) {
        const target = targetById.get(key);
        const link = svgElement('path', {d: curve(bridge.x + blockWidth, centerY(bridge), target.x, centerY(target))});
        link.append(svgElement('title', {}, `${source.id} → ${key}: ${bridge.disposition}, proposed coverage only`)); links.append(link);
        const wrapper = svgElement('g', {class: 'ora-traveler'});
        wrapper.style.setProperty('--ora-from-x', `${source.x}px`);
        wrapper.style.setProperty('--ora-from-y', `${source.y}px`);
        wrapper.style.setProperty('--ora-bridge-x', `${bridge.x}px`);
        wrapper.style.setProperty('--ora-bridge-y', `${bridge.y}px`);
        wrapper.style.setProperty('--ora-to-x', `${target.x}px`);
        wrapper.style.setProperty('--ora-to-y', `${target.y}px`);
        wrapper.append(card({...source, x: 0, y: 0}, 'ora-moving-block'));
        transfers.append(wrapper);
      }
    }
    const dependencies = svgElement('g', {class: 'ora-proposed-edges'});
    for (const edge of data.edges) {
      const source = targetById.get(edge.source), target = targetById.get(edge.target);
      const x = source.x + blockWidth;
      const path = svgElement('path', {d: `M ${x} ${centerY(source)} C 1250 ${centerY(source)}, 1250 ${centerY(target)}, ${x} ${centerY(target)}`});
      path.append(svgElement('title', {}, `Proposed edge: ${edge.source} → ${edge.target}`));
      dependencies.append(path);
      // A local arrowhead at the target, no external SVG resource references.
      dependencies.append(svgElement('path', {d: `M ${x + 8} ${centerY(target) - 4} L ${x} ${centerY(target)} L ${x + 8} ${centerY(target) + 4}`}));
    }
    svg.append(links, dependencies);
    for (const source of sources) svg.append(card(source, `ora-source${source.dim ? ' ora-dim' : ''}`));
    for (const bridge of bridges) svg.append(card(bridge, 'ora-bridge'));
    for (const target of targets) svg.append(card(target, 'ora-target'));
    svg.append(transfers);
    const inventory = element('details', '', 'ora-inventory');
    inventory.append(element('summary', 'All task labels, correspondences and proposed edges (text)'));
    for (const [heading, blocks] of [['Source task instances', sources], ['Coverage correspondence', bridges], ['Proposed Lakeflow blocks', targets]]) {
      inventory.append(element('h3', heading));
      const list = element('ul');
      for (const block of blocks) list.append(element('li', block.parts.join(' · ')));
      if (!blocks.length) list.append(element('li', 'Not supplied'));
      inventory.append(list);
    }
    const edgeList = element('ul');
    for (const edge of data.edges) edgeList.append(element('li', `Proposed edge: ${edge.source} → ${edge.target}`));
    inventory.append(element('h3', 'Supplied proposed edges'), edgeList);
    return {svg, inventory};
  }
  function close() {
    const state = active;
    if (!state) return;
    active = null;
    clearTimeout(state.timer); state.timer = null;
    state.observer?.disconnect();
    for (const dispose of state.listeners) dispose();
    if (state.dialog.open) state.dialog.close();
    state.dialog.remove();
    if (state.trigger?.isConnected && typeof state.trigger.focus === 'function') state.trigger.focus({preventScroll: true});
  }
  function open(options) {
    close();
    let data, isCurrent;
    try { data = snapshot(options); isCurrent = field(options, 'isCurrent'); }
    catch { return false; }
    if (!data || (isCurrent !== undefined && typeof isCurrent !== 'function')) return false;
    const dialog = element('dialog', '', 'orion-redesign-animation');
    if (typeof dialog.showModal !== 'function' || !document.body) return false;
    const trigger = document.activeElement;
    const media = window.matchMedia('(prefers-reduced-motion: reduce)');
    const state = {dialog, trigger, timer: null, observer: null, listeners: [], phase: 0, paused: media.matches};
    const valid = () => {
      if (active !== state) return false;
      let fresh = true;
      try { fresh = !isCurrent || isCurrent() === true; } catch { fresh = false; }
      if (!fresh || !dialog.isConnected || !dialog.open) { close(); return false; }
      return true;
    };
    const listen = (target, event, handler) => {
      target.addEventListener(event, handler);
      state.listeners.push(() => target.removeEventListener(event, handler));
    };
    dialog.setAttribute('aria-labelledby', 'ora-heading');
    dialog.setAttribute('aria-describedby', 'ora-caution');
    const header = element('header', '', 'ora-header');
    const heading = element('h2', 'From Informatica to Lakeflow'); heading.id = 'ora-heading';
    const dismiss = element('button', 'Close', 'ora-control'); dismiss.type = 'button'; dismiss.autofocus = true;
    header.append(heading, dismiss);
    const caution = element('p', 'Saved proposal mapping, not proof of semantic or data parity.', 'ora-caution');
    caution.id = 'ora-caution';
    const sourceNotice = element('p', 'Source dependency links not shown: source topology was not supplied. Source blocks are exact task instances from coverage. Workflow labels appear only when supplied by exact workflow references.', 'ora-note');
    const completeness = element('p', `${data.tasks.length} proposed blocks · ${data.coverage.length} coverage rows · ${data.edges.length} proposed edges. ${data.ignored} malformed or duplicate entries ignored. Missing references are labelled unavailable. No workflow merge is inferred.`, 'ora-note');
    const controls = element('nav', '', 'ora-controls'); controls.setAttribute('aria-label', 'Correspondence animation controls');
    const play = element('button', '', 'ora-control'), replay = element('button', 'Replay', 'ora-control'), next = element('button', 'Next stage', 'ora-control');
    for (const button of [play, replay, next]) button.type = 'button';
    const status = element('p', '', 'ora-stage'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    controls.append(play, replay, next);
    const viewport = element('div', '', 'ora-viewport'); viewport.tabIndex = 0;
    viewport.setAttribute('role', 'region'); viewport.setAttribute('aria-label', 'Scrollable proposal diagram, all blocks included');
    const {svg, inventory} = diagram(data); viewport.append(svg);
    inventory.append(element('h3', data.title), sourceNotice, completeness);
    if (!data.coverage.length) viewport.prepend(element('p', 'Source correspondence unavailable: no valid coverage task instances were supplied.', 'ora-note'));
    dialog.append(header, caution, controls, status, viewport, inventory);
    function render() {
      dialog.dataset.phase = String(state.phase);
      dialog.dataset.reducedMotion = String(media.matches);
      dialog.dataset.suspended = String(state.paused || document.hidden);
      play.textContent = media.matches ? 'Automatic playback off (reduced motion)' : state.paused ? 'Play' : 'Pause';
      play.disabled = media.matches;
      play.setAttribute('aria-pressed', String(!state.paused && !media.matches));
      status.textContent = `${state.phase + 1} / ${phases.length}: ${phases[state.phase]}${media.matches ? ' · Static, use Next stage' : document.hidden ? ' · Hidden, playback suspended' : state.paused ? ' · Paused' : ' · Looping'}`;
    }
    function schedule() {
      clearTimeout(state.timer); state.timer = null;
      if (!valid()) return;
      render();
      if (state.paused || media.matches || document.hidden) return;
      state.timer = setTimeout(() => {
        state.timer = null;
        if (!valid() || document.hidden) return;
        state.phase = (state.phase + 1) % phases.length; schedule();
      }, STEP_MS);
    }
    listen(dismiss, 'click', close);
    listen(dialog, 'keydown', event => { if (event.key === 'Escape') close(); });
    // Native Escape default remains intact. The close event owns final cleanup.
    listen(dialog, 'close', () => { if (active === state) close(); });
    let backdropDown = false;
    const outside = event => {
      const rect = dialog.getBoundingClientRect();
      return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
    };
    listen(dialog, 'pointerdown', event => { backdropDown = outside(event); });
    listen(dialog, 'click', event => { if (backdropDown && outside(event)) close(); backdropDown = false; });
    listen(play, 'click', () => { if (valid() && !media.matches) { state.paused = !state.paused; schedule(); } });
    listen(replay, 'click', () => { if (valid()) { state.phase = 0; schedule(); } });
    listen(next, 'click', () => {
      if (!valid()) return;
      state.paused = true; state.phase = (state.phase + 1) % phases.length; schedule();
    });
    listen(document, 'visibilitychange', schedule);
    listen(window, 'pagehide', close);
    listen(media, 'change', () => { if (valid()) { state.paused = true; schedule(); } });
    state.observer = new MutationObserver(() => { valid(); });
    active = state;
    document.body.append(dialog);
    try { dialog.showModal(); }
    catch { close(); return false; }
    state.observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['open']});
    if (!valid()) return false;
    dismiss.focus({preventScroll: true}); schedule(); return true;
  }
  window.orionRedesignAnimation = Object.freeze({open, close});
})();