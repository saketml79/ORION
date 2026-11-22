/* Presentation only: opening the future-state diagram performs no reads or actions. */
(() => {
  'use strict';
  const open = document.getElementById('future-architecture-open');
  const dialog = document.getElementById('future-architecture-dialog');
  const close = document.getElementById('future-architecture-close');
  if (!open || !dialog || !close) return;
  open.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
  close.addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { if (open.isConnected) open.focus({preventScroll: true}); });
})();
