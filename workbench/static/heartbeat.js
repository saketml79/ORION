/* Optional standalone activity adapter. Parent must include this asset and install
 * the guarded POST route. No startup pulse, active_work claim or response logging.
 * One-shot timers only: an idle document cannot sustain writes or retries.
 */
(() => {
  "use strict";
  const interval = 5 * 60 * 1000;
  let sequence = 0;
  let sentSequence = 0;
  let lastInteraction = -Infinity;
  let lastAttempt = -Infinity;
  let scrollIntent = -Infinity;
  let timer = null;
  let inFlight = false;
  let enabled = false;
  let confirmed = false;
  function health(data) {
    confirmed = true;
    enabled = data?.authentication === 'databricks_obo' && data?.heartbeat_enabled === true;
    if (!enabled && timer !== null) clearTimeout(timer);
    if (!enabled) { timer = null; sentSequence = sequence; }
  }
  document.addEventListener('orion:health', event => health(event.detail));
  // One finite read when no parent confirmation exists. Local false is cached;
  // interactions, scrolling and tab visibility never probe or poll health.
  queueMicrotask(() => {
    if (confirmed) return;
    fetch('/api/health', {credentials: 'same-origin', mode: 'same-origin',
      redirect: 'error', cache: 'no-store'})
      .then(response => response.ok ? response.json() : null)
      .then(data => { if (!confirmed) health(data); }).catch(() => {});
  });

  function flush() {
    timer = null;
    const now = performance.now();
    if (!enabled || document.visibilityState !== "visible" || inFlight ||
        sequence === sentSequence || now - lastInteraction > interval) return;
    const remaining = interval - (now - lastAttempt);
    if (remaining > 0) {
      timer = setTimeout(flush, remaining);
      return;
    }
    lastAttempt = now;
    sentSequence = sequence;
    inFlight = true;
    fetch("/api/hosted/heartbeat", {
      method: "POST",
      credentials: "same-origin",
      mode: "same-origin",
      redirect: "error",
      headers: { "X-Workbench-Request": "1" },
    }).catch(() => {}).finally(() => {
      inFlight = false;
      // Only a new real interaction can schedule another attempt.
      if (sequence !== sentSequence) flush();
    });
  }

  function activity(event) {
    if (!enabled || !event.isTrusted || document.visibilityState !== "visible") return;
    // scrollTo()/layout can produce isTrusted scroll events too. Require a
    // recent genuine wheel/touch gesture, not a programmatic scroll alone.
    if (event.type === "scroll") {
      if (performance.now() - scrollIntent > 1000) return;
      scrollIntent = -Infinity;
    }
    lastInteraction = performance.now();
    sequence += 1;
    if (timer === null && !inFlight) flush();
  }

  for (const type of ["wheel", "touchmove"]) {
    document.addEventListener(type, event => {
      if (event.isTrusted && document.visibilityState === "visible") {
        scrollIntent = performance.now();
      }
    }, { capture: true, passive: true });
  }
  for (const type of ["pointerdown", "keydown", "input", "scroll"]) {
    document.addEventListener(type, activity, { capture: true, passive: true });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") return;
    if (timer !== null) clearTimeout(timer);
    timer = null;
    scrollIntent = -Infinity;
    // Returning to a tab alone is not evidence of fresh human interaction.
    sentSequence = sequence;
  });
})();