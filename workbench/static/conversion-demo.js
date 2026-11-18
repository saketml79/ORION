/* Isolated contract-bound example, never relabelled as complete agent migration. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const $ = id => document.getElementById(id);
  const host = $('page-proposals');
  if (!host) return;
  const node = (tag, text = '') => { const e = document.createElement(tag); e.textContent = text; return e; };
  const panel = node('article'); panel.className = 'panel section-gap'; panel.id = 'conversion-demo';
  panel.append(node('h2', 'Generate an isolated conversion example'));
  panel.append(node('p', 'Available example: wkf_AE2E_DUMMY_CALL_WIL_NET_DIS. Converts its counter, wait and numeric branches. Email and child-workflow effects use explicitly approved test replacements. This is not a complete migration of the child workflow or AI-authored code.'));
  const form = node('form'); form.className = 'stack';
  const inputs = {};
  for (const [id, label, value] of [['catalog', 'Existing source-fixture catalog', ''], ['schema_name', 'Existing dedicated schema (orion_demo_ prefix)', '']]) {
    const caption = node('label', label), input = node('input'); input.id = `demo-${id}`; input.value = value; input.required = true;
    caption.htmlFor = input.id; inputs[id] = input; form.append(caption, input);
  }
  const approval = node('input'); approval.type = 'checkbox'; approval.id = 'demo-replacements'; approval.required = true;
  const label = node('label'); label.className = 'check-label'; label.htmlFor = approval.id;
  label.append(approval, node('span', 'Approve isolated artifact drafting with mocked email and a test child Job. No original data writes, deployment, execution or scheduling is authorized by this action.'));
  const submit = node('button', 'Generate demo proposal and artifacts'); submit.type = 'submit'; submit.className = 'primary';
  const status = node('p'); status.setAttribute('role', 'status');
  const output = node('div'); output.className = 'section-gap';
  form.append(label, submit, status); panel.append(form, output); host.append(panel);
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (!approval.checked) return;
    const project = $('project-select').value, pkg = $('project-package').value;
    const scope = JSON.stringify([project, pkg]);
    submit.disabled = true; output.replaceChildren(); status.textContent = 'Inspecting XML and compiling the reviewed test pattern. No model or deployment is invoked.';
    const headers = {'Content-Type': 'application/json', 'X-Workbench-Request': '1'};
    if (project) headers['X-Workbench-Project'] = project;
    if (project && pkg) headers['X-Workbench-Package'] = pkg;
    try {
      const listing = await fetch('/api/objects?kind=workflow&q=wkf_AE2E_DUMMY_CALL_WIL_NET_DIS&limit=100', {headers});
      const found = (await listing.json()).items?.find(n => n.name === 'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS');
      if (!listing.ok || !found) throw new Error('This package does not contain the supported example workflow. No alternative is invented.');
      const response = await fetch('/api/proposals/demo-conversion', {method: 'POST', headers, body: JSON.stringify({
        workflow_id: found.id, consent: true, bindings: {catalog: inputs.catalog.value, schema_name: inputs.schema_name.value,
          generation_approved: true, fixture_replacements_approved: true}})});
      const data = await response.json();
      if (scope !== JSON.stringify([$('project-select').value, $('project-package').value])) return;
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Conversion rejected. Inspect the contract and source version.');
      status.textContent = 'Isolated artifacts generated. Remote execution and deployment have not occurred.';
      for (const [title, value] of [['Proposal', data.proposal], ['Coverage and artifact hashes', data.manifest]]) {
        const detail = node('details'); detail.append(node('summary', title), node('pre', JSON.stringify(value, null, 2))); output.append(detail);
      }
      const graph = node('section'); graph.append(node('h3', 'Proposed test workflow dependencies'));
      const list = node('ul'); for (const edge of data.proposal.graph.edges) list.append(node('li', `${edge.source} → ${edge.target}${edge.outcome ? ` (${edge.outcome})` : ''}`));
      graph.append(list); output.append(graph);
      for (const [path, content] of Object.entries(data.artifacts)) {
        const detail = node('details'); detail.append(node('summary', path), node('pre', content));
        const download = node('button', 'Download artifact'); download.type = 'button'; download.className = 'secondary compact';
        download.addEventListener('click', () => { const url = URL.createObjectURL(new Blob([content], {type:'text/plain'}));
          const a = node('a'); a.href = url; a.download = path.split('/').pop(); a.click(); URL.revokeObjectURL(url); });
        detail.append(download); output.append(detail);
      }
    } catch (error) { status.textContent = error.message; }
    finally { submit.disabled = false; approval.checked = false; }
  });
})();