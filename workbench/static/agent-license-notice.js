(() => {
  'use strict';
  const page = document.getElementById('page-agents');
  const banner = document.getElementById('agent-license-banner');
  const filesHost = document.getElementById('agent-license-files');
  const summary = document.getElementById('agent-license-summary');
  const missingHost = document.getElementById('agent-license-missing');
  if (!page || !banner || !filesHost) return;

  const dismissedKey = 'orion-agent-license-banner-dismissed-v1';
  let dismissed = false, loaded = false, loading = false;
  try { dismissed = sessionStorage.getItem(dismissedKey) === '1'; } catch (_) { /* Session storage is optional. */ }

  function renderError(message) {
    summary.textContent = message;
    banner.hidden = false;
  }

  async function loadNotices() {
    if (loaded || loading) return;
    loading = true;
    summary.textContent = 'Loading the packaged license and notice index…';
    try {
      const response = await fetch('/api/skill-notices', {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const manifest = await response.json();
      if (!Array.isArray(manifest.documents) || !Array.isArray(manifest.skills_without_standalone_license))
        throw new Error('invalid license manifest');
      const documents = manifest.documents.slice(0, 100);
      summary.textContent = `${documents.length} packaged license/notice file(s). Review the applicable terms for the skill content you use. This banner does not determine legal rights or replace a license.`;
      for (const item of documents) {
        if (!item || typeof item.provider !== 'string' || typeof item.skill !== 'string'
            || typeof item.path !== 'string' || !/^[a-f0-9]{64}$/.test(item.sha256 || '')
            || !Number.isSafeInteger(item.byte_size) || item.byte_size < 0 || item.byte_size > 2 * 1024 * 1024) continue;
        const details = document.createElement('details'), title = document.createElement('summary'), body = document.createElement('pre');
        title.textContent = `${item.provider} · ${item.skill} · ${item.path} (${item.byte_size.toLocaleString()} bytes)`;
        body.className = 'agent-license-text'; body.textContent = 'Open to read this notice.';
        details.append(title, body);
        details.addEventListener('toggle', async () => {
          if (!details.open || details.dataset.loaded === '1' || details.dataset.loading === '1') return;
          details.dataset.loading = '1'; body.textContent = 'Reading packaged notice…';
          try {
            const url = `/api/skill-notices/${encodeURIComponent(item.provider)}/${encodeURIComponent(item.skill)}/${item.path.split('/').map(encodeURIComponent).join('/')}`;
            const pageResponse = await fetch(url, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
            if (!pageResponse.ok) throw new Error(`HTTP ${pageResponse.status}`);
            const value = await pageResponse.json();
            if (value.sha256 !== item.sha256 || typeof value.content !== 'string') throw new Error('license hash mismatch');
            body.textContent = value.content;
            details.dataset.loaded = '1';
          } catch (error) {
            body.textContent = `Notice could not be verified (${error.message}).`;
          } finally { delete details.dataset.loading; }
        });
        filesHost.append(details);
      }
      if (manifest.skills_without_standalone_license.length) {
        const warning = document.createElement('p'); warning.className = 'agent-license-warning';
        const names = manifest.skills_without_standalone_license.slice(0, 20)
          .map(item => `${item.provider}/${item.skill}`).join(', ');
        warning.textContent = `No standalone license file was found in the installed source for: ${names}. Do not infer a license from this notice panel.`;
        missingHost.append(warning);
      }
      loaded = true;
    } catch (error) {
      renderError(`License/notice index is unavailable (${error.message}).`);
    } finally { loading = false; }
  }

  function sync() {
    if (page.hidden || dismissed) { banner.hidden = true; return; }
    banner.hidden = false;
    void loadNotices();
  }
  function dismiss() {
    dismissed = true;
    banner.hidden = true;
    try { sessionStorage.setItem(dismissedKey, '1'); } catch (_) { /* Dismissal is still effective for this page. */ }
  }
  document.getElementById('agent-license-close')?.addEventListener('click', dismiss);
  document.getElementById('agent-license-ok')?.addEventListener('click', dismiss);
  new MutationObserver(sync).observe(page, {attributes: true, attributeFilter: ['hidden']});
  sync();
})();
