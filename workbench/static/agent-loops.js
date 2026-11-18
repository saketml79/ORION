/* Explorer-to-agent handoff only. Agent verification is part of the selected run, not a second manual loop. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const summary = $('object-summary');
  const objectJson = $('object-json');
  const workflow = $('agent-workflow');
  const status = $('agent-form')?.querySelector('[role="status"]');
  if (!summary || !objectJson || !workflow) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'secondary compact';
  button.textContent = 'Review this workflow with agents';
  button.disabled = true;
  summary.after(button);

  function selectedWorkflow() {
    try {
      const model = JSON.parse(objectJson.textContent);
      return model.node?.kind === 'workflow' && typeof model.node.id === 'string' ? model.node.id : '';
    } catch { return ''; }
  }
  function sync() {
    const id = selectedWorkflow();
    const available = !!id && [...workflow.options].some(option => option.value === id && !option.disabled);
    button.disabled = !available;
    button.title = available ? 'Select this canonical workflow in Agent activity. No run starts.'
      : 'Select a workflow available in the current package first.';
  }
  new MutationObserver(sync).observe(objectJson, {childList: true, subtree: true, characterData: true});
  sync();

  button.addEventListener('click', () => {
    const id = selectedWorkflow();
    if (!id || ![...workflow.options].some(option => option.value === id && !option.disabled)) {
      if (status) status.textContent = 'The selected workflow is not available in the current package.';
      return;
    }
    workflow.value = id;
    const scenario = $('scenario')?.value === 'original' ? 'original' : 'bronze';
    $('agent-scenario').value = scenario;
    workflow.dispatchEvent(new Event('change', {bubbles: true}));
    $('agent-scenario').dispatchEvent(new Event('change', {bubbles: true}));
    document.querySelector('[data-page="agents"]')?.click();
  });
})();
