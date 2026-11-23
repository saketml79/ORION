/* Exact saved artifacts only. No page-view generation or remote execution. */
(() => {
  'use strict';
  const page = document.getElementById('page-release');
  if (!page) return;
  const element = (tag, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
  const panel = element('article'); panel.className = 'panel section-gap'; panel.id = 'reviewed-release-export';
  const inventory = element('div'), status = element('p', 'Open a saved native bundle from Proposed workflow & code to select exact reviewed artifacts here. No manual IDs are required.');
  status.setAttribute('role', 'status');
  const preview = element('button', 'Preview exact DAB & member report'), download = element('button', 'Download reviewed DAB (ZIP)');
  for (const control of [preview, download]) { control.type = 'button'; control.className = 'secondary'; control.disabled = true; }
  const report = element('section');
  panel.append(element('h2', 'Reviewed Jobs & notebooks'), element('p',
    'Single workflow or exact saved cohort. Original committed notebook bytes and reviewed version pins are exported. Missing members stay visible. Validation-only GitHub workflow included. Preview and Download are explicit export actions, not deployment or execution approval.'), inventory, preview, download, status, report);
  page.append(panel);
  let context = '', epoch = 0, pending = false;
  const records = new Map(), selected = new Set();
  function sync() { preview.disabled = download.disabled = pending || !selected.size; }
  function render() {
    inventory.replaceChildren();
    for (const [key, record] of records) {
      const label = element('label'); label.className = 'check-label';
      const check = element('input'); check.type = 'checkbox'; check.checked = selected.has(key);
      check.addEventListener('change', () => { check.checked ? selected.add(key) : selected.delete(key); sync(); });
      label.append(check, element('span', `${record.name || 'Saved workflow'} · design v${record.design_version} · exact committed native bundle`));
      inventory.append(label);
    }
    sync();
  }
  document.addEventListener('orion:reviewed-bundle', event => {
    const record = event.detail;
    if (!record || !/^[a-f0-9]{32}$/.test(record.job_id) || !/^[a-f0-9]{64}$/.test(record.bundle_id)) return;
    const key = JSON.stringify([record.project, record.package, record.scenario]);
    if (context !== key) { context = key; records.clear(); selected.clear(); ++epoch; report.replaceChildren(); }
    const id = record.workflow_id; records.set(id, record); selected.add(id); render();
  });
  const project = document.getElementById('project-select'), member = document.getElementById('project-package');
  function reset() { ++epoch; records.clear(); selected.clear(); context = ''; report.replaceChildren(); render(); }
  project?.addEventListener('change', reset); member?.addEventListener('change', reset);
  document.getElementById('estate-package-select')?.addEventListener('change', reset);
  document.getElementById('scenario')?.addEventListener('change', reset);
  async function submit(archive) {
    if (pending || !selected.size) return;
    const rows = [...selected].map(key => records.get(key)), first = rows[0], ticket = epoch;
    const cohortIds = new Set(rows.map(row => row.cohort_id).filter(Boolean));
    if (cohortIds.size > 1) { status.textContent = 'Select bundles from one exact saved cohort.'; return; }
    const headers = {'Content-Type': 'application/json', 'X-Workbench-Request': '1'};
    if (first.project) { headers['X-Workbench-Project'] = first.project; headers['X-Workbench-Package'] = first.package; }
    const body = {bundles: rows.map(row => ({job_id: row.job_id, bundle_id: row.bundle_id})),
      cohort_id: [...cohortIds][0] || null, consent: true};
    pending = true; sync(); status.textContent = 'Re-reading exact approval, source pins and committed artifact bytes. No deployment or execution.';
    try {
      const response = await fetch(`/api/releases/${archive ? 'archive' : 'preview'}`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', headers, body: JSON.stringify(body)});
      if (ticket !== epoch) return;
      if (!response.ok) throw new Error(`Export unconfirmed (HTTP ${response.status}). Refresh the exact saved bundle. No remote action performed.`);
      if (archive) {
        if (!response.headers.get('Content-Type')?.startsWith('application/zip')) throw new Error('ZIP response contract mismatch');
        const blob = await response.blob(); if (ticket !== epoch) return;
        const url = URL.createObjectURL(blob), link = element('a'); link.href = url; link.download = 'reviewed-migration-dab.zip'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
        status.textContent = 'Reviewed DAB exported. Not deployed, not run; source-output parity unverified.';
      } else {
        const value = await response.json(); if (ticket !== epoch) return;
        report.replaceChildren(element('h3', `${value.report.exported_member_count} of ${value.report.member_count} chosen members have exported code`));
        for (const row of value.report.members) report.append(element('p', `${records.get(row.workflow_id)?.name || 'Chosen workflow'}: ${row.status.replaceAll('_', ' ')}${row.action ? '. ' + row.action : ''}`));
        report.append(element('p', value.report.blockers.join(' ')));
        const source = element('details'); source.append(element('summary', 'DAB YAML source'), element('pre', value.artifacts['databricks.yml'])); report.append(source);
        status.textContent = 'Preview complete. Export scope is not execution approval or output-equivalence proof.';
      }
    } catch (error) { if (ticket === epoch) status.textContent = error.message; }
    finally { pending = false; sync(); }
  }
  preview.addEventListener('click', () => submit(false)); download.addEventListener('click', () => submit(true));
})();