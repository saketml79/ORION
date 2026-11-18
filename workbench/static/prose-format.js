/* Presentation only: preserve exact prose, controls, code, and artifact bytes. */
(() => {
  'use strict';
  const selector = '.page p, .page li, .page dd';
  const excluded = 'pre, code, textarea, input, select, button, a, svg, [contenteditable], [role="status"], [role="log"], [aria-live], .notebook-cell, .notebook-run-output, .json-block';
  const focus = /\b(?:source(?:\/output)?|target|destination|mapping|parameters?|workflow|dependencies|recovery|rollback|commit|watermark|checksum|incremental|snapshot|SCD\s*(?:Type\s*)?2|read-only|serverless|Unity Catalog|DEMO|INSERT|MERGE|UPDATE|DELETE|TRUNCATE|SQL|MD5|NULL|NOT NULL|[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|\d+(?:\.\d+)?\s*(?:seconds?|minutes?|hours?|rows?))\b/gi;
  const processed = new WeakMap();
  // Explicitly authored saved-code subtext only. Never format live progress/log nodes.
  const staticStatus = '#saved-design-code-state';
  const statusFocus = new RegExp(`${focus.source}|\\b(?:notebooks?|Databricks|runtime correctness|Informatica parity)\\b`, 'gi');
  function focusStatic(element) {
    if (!element.matches(staticStatus) || element.querySelector('button, a, input, select, textarea, svg, pre, code, [contenteditable]')) return;
    if (element.children.length && ![...element.children].every(child => child.matches('.prose-focus'))) return;
    const text = element.textContent;
    if (!text || processed.get(element) === text) return;
    element.replaceChildren(emphasize(text, statusFocus)); processed.set(element, text);
  }
  window.orionProseFormat = {focusStatic};
  function emphasize(text, pattern = focus) {
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const match of text.matchAll(pattern)) {
      fragment.append(document.createTextNode(text.slice(offset, match.index)));
      const strong = document.createElement('strong'); strong.className = 'prose-focus';
      strong.textContent = match[0]; fragment.append(strong); offset = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(text.slice(offset))); return fragment;
  }
  function format(element) {
    if (element.closest(excluded) || element.querySelector('pre, code, input, button, a, select, textarea, svg, [contenteditable]')) return;
    const text = element.textContent;
    if (!text || processed.get(element) === text) return;
    // Keep existing structured markup and emphasis. Only format plain prose.
    if (element.children.length && ![...element.children].every(child => child.matches('.prose-sentence, .prose-focus'))) return;
    const fragment = document.createDocumentFragment();
    if (element.tagName === 'P' && text.length > 240) {
      for (const item of new Intl.Segmenter('en', {granularity: 'sentence'}).segment(text)) {
        const span = document.createElement('span'); span.className = 'prose-sentence';
        span.append(emphasize(item.segment)); fragment.append(span);
      }
    } else fragment.append(emphasize(text));
    element.replaceChildren(fragment); processed.set(element, text);
  }
  let scheduled = false;
  const root = document.querySelector('main');
  if (!root) return;
  root.querySelectorAll(staticStatus).forEach(focusStatic);
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false; observer.disconnect();
      root.querySelectorAll(selector).forEach(format);
      observer.observe(root, {subtree: true, childList: true, characterData: true});
    });
  });
  root.querySelectorAll(selector).forEach(format);
  observer.observe(root, {subtree: true, childList: true, characterData: true});
})();