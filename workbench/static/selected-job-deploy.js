/* Explicit deployment of only the candidate selected in Proposals. */
(() => {
  'use strict';
  const page = document.getElementById('page-release');
  if (!page) return;
  const el = (tag, text = '') => { const n = document.createElement(tag); n.textContent = text; return n; };
  const panel = el('article'); panel.id = 'selected-job-deployment'; panel.className = 'panel section-gap';
  const selected = el('p', 'Open a saved writer result in Proposals to select its Job.');
  const preview = el('button', 'Preview selected Job deployment'), deploy = el('button', 'Deploy this Job to development workspace');
  const consent = el('input'); consent.type = 'checkbox';
  const label = el('label', 'I confirm deployment of this selected Job and its notebooks. Do not run it.'); label.prepend(consent); label.className = 'check-label';
  const status = el('p'); status.setAttribute('role', 'status');
  const progress = el('progress'); progress.hidden = true; progress.setAttribute('aria-label', 'Selected Job deployment progress');
  const report = el('div');
  const link = el('a', 'Open deployed Job'); link.hidden = true; link.target = '_blank'; link.rel = 'noopener noreferrer';
  panel.append(el('h2', 'Deploy the selected Job'), selected, el('p',
    'One Job only, through DAB selective deployment. Its workspace destination is derived automatically from your signed-in user. Deployment does not execute the Job. Unresolved behavior remains visible in its generated code.'), preview, label, deploy, progress, status, link, report);
  for (const b of [preview, deploy]) { b.type = 'button'; b.className = 'secondary'; }
  page.querySelector('.page-heading').after(panel);
  let candidate = null, plan = null, busy = false, epoch = 0;
  const sync = () => { preview.disabled = busy || !candidate; deploy.disabled = busy || !plan || !consent.checked; consent.disabled = busy || !plan; };
  const reset = () => { ++epoch; candidate = plan = null; consent.checked = false; report.replaceChildren(); link.hidden = true; selected.textContent = 'Select a saved writer result in Proposals.'; sync(); };
  ['project-select', 'project-package', 'estate-package-select', 'scenario'].forEach(id => document.getElementById(id)?.addEventListener('change', reset));
  document.addEventListener('orion:selected-job-candidate', event => {
    reset(); candidate = event.detail;
    selected.textContent = `${candidate.name} · design v${candidate.design_version} · one selected Job`;
    sync();
  });
  consent.addEventListener('change', sync);
  const pins = () => Object.fromEntries(Object.entries(candidate).filter(([k]) => !['job_id', 'name'].includes(k)));
  async function request(path, method = 'GET', body = null) {
    const headers = {'Accept': 'application/json', 'X-Workbench-Project': candidate.project_id, 'X-Workbench-Package': candidate.package_hash};
    if (body) Object.assign(headers, {'Content-Type': 'application/json', 'X-Workbench-Request': '1'});
    const response = await fetch(path, {method, headers, body: body ? JSON.stringify(body) : undefined, credentials: 'same-origin', cache: 'no-store'});
    const value = await response.json();
    if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : `Request failed (HTTP ${response.status})`);
    return value;
  }
  preview.addEventListener('click', async () => {
    if (!candidate || busy) return;
    const ticket = epoch; busy = true; progress.hidden = false; sync(); status.textContent = 'Reading the selected Job YAML and notebook files…';
    try {
      const result = await request(`/api/jobs/${candidate.job_id}/selected-job/preview`, 'POST', pins());
      if (ticket !== epoch) return;
      plan = result; report.replaceChildren();
      const yaml = el('details'); yaml.append(el('summary', 'Selected Job DAB configuration'), el('pre', JSON.stringify(result.bundle, null, 2))); report.append(yaml);
      status.textContent = 'Preview ready. Review the selected Job, then confirm development deployment. Code and source-output parity remain unverified.';
    } catch (e) { if (ticket === epoch) status.textContent = e.message; }
    finally { busy = false; progress.hidden = true; sync(); }
  });
  async function poll(id, ticket) {
    if (ticket !== epoch) return;
    try {
      const result = await request(`/api/selected-job-deployments/${id}`);
      if (ticket !== epoch) return;
      status.textContent = `Deployment: ${result.status}${result.error_code ? '. ' + result.error_code : ''}. No Job run was submitted.`;
      if (['queued', 'authenticating', 'staging', 'validating', 'deploying', 'verifying'].includes(result.status)) {
        setTimeout(() => poll(id, ticket), 2000); return;
      }
      busy = false; progress.hidden = true; consent.checked = false;
      const jobURL = result.workspace_job_id && result.workspace ? `${result.workspace}/jobs/${result.workspace_job_id}` : null;
      if (result.status === 'succeeded' && jobURL) {
        const url = new URL(jobURL), expected = new URL(result.workspace);
        if (url.protocol === 'https:' && url.origin === expected.origin) { link.href = url.href; link.hidden = false; }
      }
      sync();
    } catch (e) { status.textContent = `Deployment status unconfirmed: ${e.message}. No retry was submitted.`; busy = false; progress.hidden = true; sync(); }
  }
  deploy.addEventListener('click', async () => {
    if (!candidate || !plan || busy || !consent.checked) return;
    const ticket = epoch; busy = true; progress.hidden = false; sync();
    try {
      const workspace = await request('/api/models/workspace');
      if (ticket !== epoch) return;
      status.textContent = 'Starting deployment of only the selected Job…';
      const result = await request(`/api/jobs/${candidate.job_id}/selected-job/deploy`, 'POST',
        {...pins(), consent: true, preview_sha256: plan.preview_sha256, expected_workspace: workspace.workspace});
      if (ticket === epoch) await poll(result.id, ticket);
    } catch (e) { if (ticket === epoch) status.textContent = `Deployment not confirmed: ${e.message}. No automatic retry.`; busy = false; progress.hidden = true; sync(); }
  });
  sync();
})();