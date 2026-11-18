/* Accessible graph-view menu; the existing select remains the state authority. */
(() => {
  'use strict';
  const select = document.getElementById('package-graph-kind');
  const label = document.querySelector('label[for="package-graph-kind"]');
  const wrapper = document.createElement('div');
  wrapper.className = 'graph-view-picker';
  const trigger = document.createElement('button');
  trigger.type = 'button'; trigger.id = 'graph-view-trigger';
  trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'graph-view-menu');
  const menu = document.createElement('div');
  menu.id = 'graph-view-menu'; menu.className = 'graph-view-menu';
  menu.setAttribute('role', 'menu'); menu.setAttribute('aria-label', 'Package graph view'); menu.hidden = true;
  const items = [...select.options].map((option) => {
    const item = document.createElement('button');
    item.type = 'button'; item.setAttribute('role', 'menuitemradio');
    item.dataset.value = option.value;
    const glyph = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    glyph.setAttribute('viewBox', '0 0 24 24'); glyph.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(glyph.namespaceURI, 'path');
    path.setAttribute('d', option.value === 'workflow' ? 'M3 4h6v6H3z M15 14h6v6h-6z M9 7h9v7' : 'M3 4h7v7H3z M14 13h7v7h-7z M14 4h7v5h-7z M3 15h7v5H3z');
    glyph.append(path);
    const text = document.createElement('span'); text.textContent = option.textContent;
    const check = document.createElement('span'); check.className = 'view-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
    item.append(glyph, text, check);
    item.addEventListener('click', () => {
      select.value = option.value; select.dispatchEvent(new Event('change', { bubbles: true }));
      sync(); close(true);
    });
    menu.append(item); return item;
  });
  function sync() {
    trigger.textContent = `${select.selectedOptions[0].textContent}  ▾`;
    trigger.setAttribute('aria-label', `Graph overview: ${select.selectedOptions[0].textContent}`);
    items.forEach((item) => item.setAttribute('aria-checked', String(item.dataset.value === select.value)));
  }
  function close(focus = false) { menu.hidden = true; trigger.setAttribute('aria-expanded', 'false'); if (focus) trigger.focus(); }
  function open() { menu.hidden = false; trigger.setAttribute('aria-expanded', 'true'); items.find((item) => item.dataset.value === select.value)?.focus(); }
  trigger.addEventListener('click', () => menu.hidden ? open() : close());
  trigger.addEventListener('keydown', (event) => { if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); open(); } });
  wrapper.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); close(true); }
    if (event.key === 'Tab') close();
    const index = items.indexOf(document.activeElement);
    if (index >= 0 && ['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next].focus();
    }
  });
  document.addEventListener('click', (event) => { if (!wrapper.contains(event.target)) close(); });
  select.addEventListener('change', sync);
  select.hidden = true; label.htmlFor = trigger.id; label.textContent = 'Graph view';
  select.after(wrapper); wrapper.append(trigger, menu); sync();
})();