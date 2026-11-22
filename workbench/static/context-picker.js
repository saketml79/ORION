/* Shared single-selection rollers. Native selects retain application state. */
(() => {
  'use strict';
  for (const id of ['project-select', 'project-package', 'estate-package-select', 'document-workflow', 'attestation-workflow', 'agent-workflow']) {
    const select = document.getElementById(id);
    if (!select) continue;
    const box = document.createElement('div'); box.className = 'context-picker';
    const list = document.createElement('div'); list.className = 'context-roller';
    list.id = `${id}-roller`; list.tabIndex = 0;
    list.setAttribute('role', 'listbox'); list.setAttribute('aria-multiselectable', 'false');
    list.setAttribute('aria-label', select.labels?.[0]?.textContent || 'Select context');
    const hint = document.createElement('p'); hint.className = 'context-picker-hint';
    hint.id = `${id}-hint`;
    hint.textContent = '↑ ↓ to select · Home / End · type a name · scroll to browse';
    list.setAttribute('aria-describedby', hint.id);
    const footer = document.createElement('div'); footer.className = 'context-picker-footer';
    const returnSelected = document.createElement('button');
    returnSelected.type = 'button'; returnSelected.className = 'secondary compact context-show-selected';
    returnSelected.textContent = 'Show selected';
    returnSelected.setAttribute('aria-controls', list.id);
    returnSelected.title = 'Bring the current selection back into view without changing it';
    returnSelected.addEventListener('click', () => { centerSelected(); list.focus({ preventScroll: true }); });
    footer.append(hint, returnSelected);
    box.append(list, footer); select.after(box);
    if (id === 'agent-workflow') {
      const basis = document.createElement('p');
      basis.className = 'context-workflow-basis';
      basis.textContent = 'Candidates use workflow calls captured in the XML. Other data, scheduler, and external dependencies are not assessed.';
      box.append(basis);
    }
    select.classList.add('context-native-select'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true');
    const label = document.querySelector(`label[for="${id}"]`);
    if (label) {
      label.id ||= `${id}-label`;
      list.setAttribute('aria-labelledby', label.id);
      label.addEventListener('click', (event) => { event.preventDefault(); list.focus(); });
    }
    let prefix = '', lastKey = 0;
    function visibleOption(option) {
      // Placeholder options remain in the native state model, not the visible wheel.
      return option.value !== '' || id === 'project-select' || id === 'document-workflow';
    }
    function centerSelected() {
      const selected = list.querySelector('[aria-selected="true"]');
      if (selected && list.clientHeight) list.scrollTop = selected.offsetTop - (list.clientHeight - selected.offsetHeight) / 2;
    }
    function choose(index) {
      const option = select.options[index];
      if (!option || select.disabled || option.disabled) return;
      if (select.selectedIndex === index) { centerSelected(); return; }
      select.selectedIndex = index; select.dispatchEvent(new Event('change', { bubbles: true })); render();
    }
    function render() {
      list.replaceChildren(); list.setAttribute('aria-disabled', String(select.disabled));
      list.setAttribute('aria-busy', select.getAttribute('aria-busy') ?? 'false');
      list.tabIndex = select.disabled ? -1 : 0;
      [...select.options].forEach((option, index) => {
        if (!visibleOption(option)) return;
        const row = document.createElement('div'); row.className = 'context-option';
        const key = id === 'project-select' ? option.value : document.getElementById('project-select')?.value || '';
        const color = [...key].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 6;
        row.classList.add(`context-color-${color}`);
        row.id = `${id}-choice-${index}`; row.setAttribute('role', 'option');
        row.dataset.position = index < select.selectedIndex ? 'above' : index > select.selectedIndex ? 'below' : 'center';
        row.setAttribute('aria-selected', String(option.selected)); row.setAttribute('aria-disabled', String(option.disabled || select.disabled));
        const dot = document.createElement('span'); dot.className = 'context-dot'; dot.setAttribute('aria-hidden', 'true');
        const name = document.createElement('span'); name.className = 'context-name'; name.textContent = option.textContent;
        const check = document.createElement('span'); check.className = 'context-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
        // Never copy the native option's potentially enormous serialized scope/title.
        const shortName = option.textContent.replace(/\s+/g, ' ').trim().slice(0, 160);
        const shortId = option.value.replace(/\s+/g, ' ').slice(0, 40);
        row.title = shortId ? `${shortName} | ${shortId}` : shortName;
        row.append(dot, name);
        if (id === 'agent-workflow' && option.dataset.workflowClass) {
          const classification = document.createElement('span'); classification.className = 'context-workflow-class';
          classification.textContent = option.dataset.workflowClass;
          classification.title = 'Candidate based on captured workflow calls only. Other dependencies are not assessed.';
          row.append(classification);
        }
        if (id === 'agent-workflow' && option.dataset.invocationLabel) {
          const badge = document.createElement('span'); badge.className = 'context-invocation-badge';
          badge.textContent = `↗ ${option.dataset.invocationLabel}`;
          badge.title = 'Workflow-call commands captured in the Informatica XML, not previous runs. An unconfirmed target could not be matched confidently to one workflow definition. Other external or data dependencies may exist.';
          row.append(badge);
        }
        row.append(check);
        row.addEventListener('click', () => { list.focus(); choose(index); }); list.append(row);
        if (option.selected) list.setAttribute('aria-activedescendant', row.id);
      });
      if (!list.querySelector('[aria-selected="true"]')) list.removeAttribute('aria-activedescendant');
      returnSelected.disabled = select.disabled || !list.querySelector('[aria-selected="true"]');
      if (!list.children.length) {
        const empty = document.createElement('p'); empty.className = 'context-wheel-empty';
        empty.textContent = select.dataset.emptyMessage || (select.disabled ? 'No items available yet' : 'No matching items');
        list.append(empty);
      }
      centerSelected();
    }
    list.addEventListener('keydown', (event) => {
      if (select.disabled) return;
      const enabled = [...select.options].map((o, i) => o.disabled || !visibleOption(o) ? -1 : i).filter(i => i >= 0);
      const current = enabled.indexOf(select.selectedIndex);
      let next;
      if (event.key === 'ArrowDown') next = enabled[Math.min(enabled.length - 1, current + 1)];
      if (event.key === 'ArrowUp') next = current < 0 ? enabled[0] : enabled[Math.max(0, current - 1)];
      if (event.key === 'Home') next = enabled[0];
      if (event.key === 'End') next = enabled.at(-1);
      if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && (event.key !== ' ' || prefix)) {
        const now = Date.now(); prefix = now - lastKey > 700 ? event.key : prefix + event.key; lastKey = now;
        next = enabled.find(i => select.options[i].textContent.toLowerCase().startsWith(prefix.toLowerCase()));
      }
      if (next !== undefined) { event.preventDefault(); choose(next); }
    });
    new MutationObserver(render).observe(select, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['disabled', 'selected', 'label', 'value', 'data-empty-message', 'data-invocation-label', 'data-workflow-class', 'aria-busy'] });
    new ResizeObserver(centerSelected).observe(list);
    select.addEventListener('change', render);
    select.addEventListener('input', render);
    select.form?.addEventListener('reset', () => requestAnimationFrame(render));
    render();
  }
})();