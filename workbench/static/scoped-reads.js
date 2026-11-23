/* Page-memory read ownership only. Never cache authorization or mutable results. */
(() => {
  'use strict';
  const flights = new Map();
  const nativeFetch = window.fetch.bind(window);
  let revision = 0;
  const invalidate = () => { revision += 1; };
  function identity(path, headers, transport, policy) {
    const url = new URL(path, location.origin);
    url.searchParams.sort();
    const scopedHeaders = [...new Headers(headers).entries()]
      .filter(([name]) => !['x-workbench-request', 'xworkbenchreadid'].includes(name));
    return JSON.stringify([revision, transport, policy, url.href, scopedHeaders.sort()]);
  }
  // Live status is always a new observation, never joined to an older poll.
  const live = path => /^\/api\/(?:agents\/runs(?:\/|$)|loops(?:\/|$)|jobs\/|projects\/[^/]+\/jobs(?:\/|$)|hosted\/(?:operations|uploads|loading)|models(?:\/|$)|tracing\/|health$)/.test(new URL(path, location.origin).pathname);
  const runStatus = path => {
    const url = new URL(path, location.origin);
    return /^\/api\/agents\/runs\/[a-f0-9]{32}$/.test(url.pathname)
      && url.searchParams.getAll('status_only').length === 1
      && url.searchParams.get('status_only') === 'true';
  };
  function own(path, headers, loader, {signal, onStatus, transport = 'json', fresh = false, policy = null} = {}) {
    if (signal?.aborted) return Promise.reject(new DOMException('Selection changed', 'AbortError'));
    const key = identity(path, headers, transport, policy);
    fresh = fresh || live(path);
    let flight = fresh ? null : flights.get(key);
    if (!flight) {
      flight = {controller: new AbortController(), listeners: new Set(), status: null, operation: null};
      const active = flight;
      if (!fresh) flights.set(key, active);
      const report = value => {
        active.status = value;
        active.operation = value?.operation_id || active.operation;
        for (const listener of active.listeners) listener.onStatus?.(value);
      };
      // No app elapsed-time deadline. The initiating context still owns abort,
      // and transport/server errors remain errors, not automatic retries.
      active.promise = Promise.resolve().then(() => loader(active.controller.signal, report))
        .finally(() => { if (flights.get(key) === active) flights.delete(key); });
    }
    const active = flight;
    return new Promise((resolve, reject) => {
      const listener = {onStatus}; active.listeners.add(listener);
      const leave = () => { signal?.removeEventListener('abort', abort); active.listeners.delete(listener); };
      const abort = () => {
        leave(); reject(new DOMException('Selection changed', 'AbortError'));
        if (!active.listeners.size) {
          active.controller.abort();
          if (flights.get(key) === active) flights.delete(key);
        }
      };
      signal?.addEventListener('abort', abort, {once: true});
      if (active.status) onStatus?.(active.status);
      active.promise.then(value => {
        leave(); resolve(transport === 'response' ? value.clone() : structuredClone(value));
      }, error => { leave(); reject(error); });
    });
  }
  async function scopedFetch(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const url = new URL(path, location.origin);
    if (options.download || url.pathname.startsWith('/api/export/')) return nativeFetch(path, options);
    if (method !== 'GET' || url.origin !== location.origin) {
      let observation = url.pathname === '/api/hosted/heartbeat';
      if (url.pathname === '/api/hosted/operations' && method === 'POST') {
        try { observation = JSON.parse(options.body).method === 'GET'; } catch { /* Invalid envelopes retain write invalidation. */ }
      }
      const mutation = url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method) && !observation;
      if (mutation) invalidate();
      try {
        const response = await nativeFetch(path, options);
        if (mutation && response.ok) document.dispatchEvent(new CustomEvent('orion:workspace-changed', {
          detail: {path: url.pathname, method}
        }));
        return response;
      }
      finally { if (mutation) invalidate(); }
    }
    const captured = {...options, headers: new Headers(options.headers)};
    return own(path, captured.headers, async signal => {
      const response = await nativeFetch(path, {...captured, signal});
      // Complete the body once before releasing ownership. Consumers get
      // independent Response bodies; no shared mutable JSON or consumed stream.
      const bytes = await response.arrayBuffer();
      return new Response([204, 205, 304].includes(response.status) ? null : bytes,
        {status: response.status, statusText: response.statusText, headers: response.headers});
    }, {signal: options.signal, transport: 'response', fresh: live(path),
      // Transport security/cache policy is part of ownership, never a cached
      // cookie or permission verdict. Omitted credentials must not join an
      // authenticated same-origin read just because the URL/headers match.
      policy: [options.credentials || 'same-origin', options.mode || 'cors', options.redirect || 'follow',
        options.cache || 'default', options.integrity || '', options.referrer ?? 'about:client', options.referrerPolicy || '']});
  }
  for (const name of ['orion:agent-run', 'orion:loop-run']) {
    document.addEventListener(name, event => {
      if (!event.detail || !['queued', 'running'].includes(event.detail.status)) invalidate();
    });
  }
  document.addEventListener('change', event => {
    if (['project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) invalidate();
  });
  document.getElementById('refresh')?.addEventListener('click', invalidate, true);
  window.orionScopedReads = Object.freeze({fetch: scopedFetch, read: own, invalidate});
})();