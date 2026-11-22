/* Logical designs are server records, never structural baselines or execution authority. */
(() => {
  'use strict';
  const fetch = window.orionScopedReads?.fetch || window.fetch.bind(window);
  const $ = id => document.getElementById(id);
  const page = $('page-proposals'), agentWorkflow = $('agent-workflow');
  if (!page || !agentWorkflow) return;
  const agentPage = $('page-agents');
  const items = value => Array.isArray(value) ? value : [];
  const safe = value => window.orionRedaction.text(value ?? 'Unknown');
  const node = (tag, text = '', className = '') => {
    const element = document.createElement(tag); element.textContent = safe(text); element.className = className; return element;
  };
  // Presentation only: explicit languages, no HTML parsing or source execution.
  const codePatterns = {
    python: /(?<comment>#[^\r\n]*)|(?<string>(?:[rRuUbBfF]{1,2})?(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)|"(?:\\[^]|[^"\\\r\n])*"|'(?:\\[^]|[^'\\\r\n])*'))|(?<number>\b(?:0[xX][\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?j?)\b)|(?<word>\b[A-Za-z_]\w*\b)|(?<operator>[-+*/%=<>!&|^~:]+)/g,
    sql: /(?<comment>--[^\r\n]*|\/\*[\s\S]*?(?:\*\/|$))|(?<string>'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`)|(?<number>\b\d+(?:\.\d+)?\b)|(?<word>\b[A-Za-z_]\w*\b)|(?<operator>[-+*/%=<>!|:]+)/g,
    yaml: /(?<comment>#[^\r\n]*)|(?<string>"(?:\\[^]|[^"\\\r\n])*"|'(?:''|[^'\r\n])*')|(?<key>^[ \t]*(?:-\s+)?[A-Za-z_][\w.-]*(?=\s*:))|(?<number>\b\d+(?:\.\d+)?\b)|(?<word>\b[A-Za-z_]\w*\b)|(?<operator>[:\[\]{},&*!|>+-])/gm
  };
  const codeKeywords = {
    python: new Set('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield False None True'.split(' ')),
    sql: new Set('SELECT FROM WHERE JOIN LEFT RIGHT INNER OUTER FULL CROSS ON AS WITH INSERT INTO VALUES UPDATE SET DELETE MERGE USING WHEN MATCHED THEN ELSE END CASE CREATE OR REPLACE TABLE VIEW DROP ALTER DISTINCT GROUP BY ORDER HAVING LIMIT OFFSET UNION ALL EXCEPT INTERSECT AND NOT NULL IS IN EXISTS BETWEEN LIKE ASC DESC OVER PARTITION QUALIFY TRUE FALSE CAST'.split(' ')),
    yaml: new Set('true false null yes no on off'.split(' '))
  };
  const artifactLanguage = path => ({py: 'python', sql: 'sql', yaml: 'yaml', yml: 'yaml'}[String(path).split('.').pop().toLowerCase()] || 'unknown');
  const cellLanguage = (cell, notebook) => cell.cell_type === 'markdown' ? 'markdown'
    : cell.metadata?.language || notebook.metadata?.language_info?.name || notebook.metadata?.kernelspec?.language || 'unknown';
  function codePreview(text, language, {portableText = false} = {}) {
    const pre = node('pre', '', 'json-block proposal-code');
    const source = portableText ? String(text ?? '') : safe(text ?? '');
    const name = String(language || 'unknown').toLowerCase();
    pre.dataset.language = Object.prototype.hasOwnProperty.call(codePatterns, name) ? name : 'unknown';
    pre.tabIndex = 0; pre.setAttribute('aria-label', `${pre.dataset.language} source code`);
    if (pre.dataset.language === 'unknown') { pre.textContent = source; return pre; }
    const pattern = codePatterns[name]; pattern.lastIndex = 0;
    const fragment = document.createDocumentFragment();
    let offset = 0;
    for (const match of source.matchAll(pattern)) {
      fragment.append(document.createTextNode(source.slice(offset, match.index)));
      let kind = Object.keys(match.groups).find(key => match.groups[key] !== undefined);
      // Native Jobs YAML may use JSON-style quoted mapping keys.
      if (name === 'yaml' && kind === 'string' && /^\s*:/.test(source.slice(match.index + match[0].length))) kind = 'key';
      if (kind === 'word') {
        const word = name === 'sql' ? match[0].toUpperCase() : name === 'yaml' ? match[0].toLowerCase() : match[0];
        kind = codeKeywords[name].has(word) ? 'keyword' : name === 'python' && /^\s*\(/.test(source.slice(match.index + match[0].length)) ? 'function' : null;
      }
      if (kind) {
        const span = document.createElement('span'); span.className = `syntax-${kind}`;
        span.textContent = match[0]; fragment.append(span);
      } else fragment.append(document.createTextNode(match[0]));
      offset = match.index + match[0].length;
    }
    fragment.append(document.createTextNode(source.slice(offset))); pre.append(fragment); return pre;
  }
  const button = (id, text, parent) => {
    const element = node('button', text, 'secondary compact'); element.id = id; element.type = 'button'; parent.append(element); return element;
  };
  function triggerDownload(url, filename) {
    if (!url) return;
    const link = document.createElement('a');
    link.href = url; link.download = filename; link.className = 'orion-download-link';
    document.body.append(link); link.click();
    setTimeout(() => { link.remove(); if (url.startsWith('blob:')) URL.revokeObjectURL(url); }, 1000);
  }
  function savedArtifactUrl(candidate, path, content) {
    // Download the exact bytes already read under this project/package receipt.
    // A navigation download cannot carry the scoped request headers.
    if (!current(candidate.ticket) || page.hidden || typeof content !== 'string' || safe(content) !== content) return null;
    return URL.createObjectURL(new Blob([content], {
      type: path.endsWith('.ipynb') || path.endsWith('.json') ? 'application/json' : 'text/plain;charset=utf-8'
    }));
  }
  const field = (tag, id, label, parent) => {
    const caption = node('label', label); caption.htmlFor = id;
    const element = node(tag); element.id = id; parent.append(caption, element); return element;
  };
  const panel = node('article', '', 'panel section-gap'); panel.id = 'redesign-review';
  panel.setAttribute('aria-labelledby', 'redesign-heading');
  const heading = node('h2', 'Proposed workflow & code'); heading.id = 'redesign-heading';
  panel.append(heading, node('p', 'Select a workflow. Agent-generated designs and their notebook code appear here after the agent run produces and saves them. Review or request changes before continuing to validation.', 'muted'));
  const workflow = field('select', 'redesign-workflow', 'Workflow (from the current package overview)', panel);
  const sourceLinkRow = node('div', '', 'button-row'); sourceLinkRow.id = 'proposal-original-workflow';
  const originalName = node('strong'); sourceLinkRow.append(originalName);
  const originalWorkflow = button('redesign-open-original', 'View original workflow', sourceLinkRow);
  originalWorkflow.disabled = true; panel.append(sourceLinkRow);
  const scopeSummary = node('p', '', 'review-scope-summary'); scopeSummary.id = 'redesign-scope-summary'; panel.append(scopeSummary);
  const comparison = node('section', '', 'proposal-layout section-gap'); comparison.id = 'proposal-source-comparison';
  comparison.setAttribute('aria-label', 'Original source to proposed Databricks design');
  const sourceColumn = node('article', '', 'proposal-review-card'), proposedColumn = node('article', '', 'proposal-review-card');
  const sourceName = node('p'), sourceCount = node('p', '', 'small muted');
  const proposedName = node('p'), proposedCount = node('p', '', 'small muted');
  sourceColumn.append(node('h3', 'Original Informatica workflow'), sourceName, sourceCount);
  proposedColumn.append(node('h3', 'Proposed Databricks design'), proposedName, proposedCount);
  comparison.append(sourceColumn, proposedColumn); panel.append(comparison);
  const disclosure = (title, content, explanation = '') => {
    const block = node('details', '', 'review-disclosure section-gap');
    const body = node('div', '', 'review-disclosure-body');
    if (explanation) body.append(node('p', explanation, 'review-disclosure-help small muted'));
    body.append(content); block.append(node('summary', title), body); return block;
  };
  const understanding = node('section', '', 'section-gap'); understanding.id = 'workflow-understanding';
  const sourceDetails = disclosure('Source workflow explanation and XML evidence', understanding,
    'Read the captured Informatica behavior and the XML records supporting it.');
  panel.append(sourceDetails);
  const targets = node('section', '', 'section-gap'); targets.id = 'source-target-review';
  targets.append(node('h3', 'Review Informatica targets'), node('p', 'Inspect exported targets for one workflow or its literal invocation cohort. Saved destination selections are unapproved and unverified; fixture schemas are not production mappings.', 'small muted'));
  const targetScope = field('select', 'source-target-scope', 'Design scope', targets);
  targetScope.append(new Option('Individual workflow', 'workflow'), new Option('Cohort', 'cohort'));
  const agentScopeLabel = node('label', 'Design scope'); agentScopeLabel.htmlFor = 'agent-review-scope';
  const agentScope = node('select'); agentScope.id = 'agent-review-scope';
  agentScope.append(new Option('Individual workflow', 'workflow'), new Option('Cohort', 'cohort'));
  // Keep the existing event/value bridge for callers, not a second user choice.
  agentScopeLabel.hidden = true; agentScope.hidden = true;
  agentWorkflow.after(agentScopeLabel, agentScope);
  const targetRead = button('source-target-read', 'Inspect source targets', targets);
  const cohortCreate = button('cohort-review-create', 'Save reviewed workflow scope', targets);
  cohortCreate.disabled = true;
  const cohortId = field('input', 'cohort-review-id', 'Saved review ID (for reconnect)', targets);
  const cohortRead = button('cohort-review-read', 'Open saved review', targets);
  const cohortOutput = node('div', '', 'detail-list'); targets.append(cohortOutput);
  const targetStatus = node('p', 'Choose a workflow, then inspect its source targets. No model call or conversion is started.', 'small muted');
  targetStatus.setAttribute('role', 'status'); targetStatus.setAttribute('aria-live', 'polite');
  const targetOutput = node('div', '', 'detail-list'); targets.append(targetStatus, targetOutput);
  const targetDetails = disclosure('Output tables and cohort', targets,
    'Inspect destinations and related workflow calls. These selections are not production approvals.'); panel.append(targetDetails);
  const actions = node('div', '', 'button-row'); panel.append(actions);
  const generate = button('redesign-generate', 'Open Agent activity', actions);
  const refresh = button('redesign-refresh', 'Reload saved designs', actions);
  refresh.title = 'Check for saved designs for this workflow. This does not start agents or remove example output.';
  const actionHelp = node('p', 'Open Agent activity to start or revise a conversion. Reload saved designs checks for completed results without running agents.', 'small muted review-action-help');
  actions.after(actionHelp);
  const sqlPanel = node('section', '', 'section-gap'); sqlPanel.id = 'sql-drafts';
  sqlPanel.append(node('h3', 'Captured SQL artifact drafts'), node('p', 'Create SQL syntax drafts from the selected workflow without an agent design or model call. Non-SQL transformations require separate work, and unresolved references affect their related tasks. Review business behavior and data results before use.', 'muted'));
  sqlPanel.append(node('p', 'Clicking Draft captured SQL artifacts creates a syntax-only draft. It does not execute or deploy it.', 'small muted'));
  const sqlDraft = button('sql-draft-submit', 'Draft captured SQL artifacts', sqlPanel);
  const sqlStatus = node('p', 'Choose a workflow and explicitly approve SQL-only drafting.', 'small muted');
  sqlStatus.id = 'sql-draft-status'; sqlStatus.setAttribute('role', 'status'); sqlStatus.setAttribute('aria-live', 'polite');
  const sqlOutput = node('div', '', 'detail-list'); sqlOutput.id = 'sql-draft-output';
  sqlPanel.append(sqlStatus, sqlOutput); const sqlDetails = disclosure('SQL-only drafts', sqlPanel,
    'Optional SQL syntax drafts, separate from the agent-written notebooks.'); panel.append(sqlDetails);
  const workflowPanel = node('section', '', 'section-gap'); workflowPanel.id = 'workflow-artifacts';
  workflowPanel.append(node('h3', 'Static compiler examples, not agent-generated'), node('p',
    'Four reviewed bronze wrappers only. Native YAML and notebook boilerplate, no model invocation or execution.', 'muted'));
  workflowPanel.append(node('p', 'Clicking Generate creates the supported deterministic example artifacts for the selected workflow. This is not agent-generated code.', 'small muted'));
  const workflowDraft = button('workflow-artifact-submit', 'Generate workflow YAML and notebooks', workflowPanel);
  const workflowStatus = node('p', 'Choose a bronze workflow and explicitly approve generation.', 'small muted');
  workflowStatus.id = 'workflow-artifact-status'; workflowStatus.setAttribute('role', 'status'); workflowStatus.setAttribute('aria-live', 'polite');
  const workflowOutput = node('div', '', 'detail-list'); workflowOutput.id = 'workflow-artifact-output';
  workflowPanel.append(workflowStatus, workflowOutput);
  const compilerDetails = disclosure('Example compiler (four reviewed wrappers)', workflowPanel); panel.append(compilerDetails);
  const status = node('p', 'Open proposals to read actual redesigns.', 'small muted'); status.id = 'redesign-status';
  status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); panel.append(status);
  const versions = field('select', 'redesign-version', 'Stored redesign and version', panel);
  const paging = node('div', '', 'pagination'); panel.append(paging);
  const previous = button('redesign-prev', 'Previous redesign page', paging);
  const next = button('redesign-next', 'Next redesign page', paging);
  const detail = node('div', '', 'detail-list section-gap'); detail.id = 'redesign-detail'; panel.append(detail);
  const scopePanel = node('section', '', 'review-scope-controls');
  scopePanel.id = 'agent-scope-host';
  scopePanel.setAttribute('aria-label', 'Workflow scope and dependency review');
  scopePanel.append(panel.querySelector('label[for="source-target-scope"]') || targets.querySelector('label[for="source-target-scope"]'), targetScope);
  const scopeHint = node('p', 'Choose Individual workflow or Cohort. A cohort is a reviewed set of workflows, not automatic consolidation or completion order. Notebook drafts use the exact saved design and remain non-executable until separately prepared.', 'small muted');
  const scopeActions = node('div', '', 'button-row'); scopeActions.append(targetRead, cohortCreate);
  const reconnectControls = node('div'); reconnectControls.append(cohortId.previousElementSibling, cohortId, cohortRead);
  const scopeReconnect = disclosure('Advanced: reconnect a saved cohort review', reconnectControls);
  scopeReconnect.id = 'proposal-scope-reconnect'; scopeReconnect.hidden = true;
  const scopeStatus = node('p', 'Review the workflow scope before saving it or starting an agent run.', 'small muted');
  scopeStatus.setAttribute('role', 'status'); scopeStatus.setAttribute('aria-live', 'polite');
  scopePanel.append(scopeHint, scopeStatus, scopeActions, scopeReconnect, cohortOutput, targetStatus, targetOutput);
  const memberPanel = node('section', '', 'proposal-review-card'); memberPanel.id = 'cohort-member-plan'; memberPanel.hidden = true;
  const memberChoices = node('div');
  const scopeOwner = field('input', 'cohort-scope-owner', 'Scope owner label (not authenticated identity)', memberPanel);
  const migrationUnit = field('input', 'cohort-migration-unit', 'Owner-reviewed migration unit', memberPanel);
  const scopeRationale = field('textarea', 'cohort-scope-rationale', 'Why this chosen set, including excluded boundaries', memberPanel);
  const semanticLinks = field('textarea', 'cohort-semantic-links', 'Supporting semantic links, one per line (not execution dependencies)', memberPanel);
  memberPanel.prepend(node('h3', 'Review the exact member plan'), memberChoices); scopePanel.append(memberPanel);
  let chosenMembers = new Set();
  function memberPlan(contract) {
    chosenMembers = new Set(items(contract.cohort.items).map(member => member.workflow_id));
    memberPanel.hidden = false; memberChoices.replaceChildren(node('p',
      'Suggested set: undirected literal-call component, including callers and connected siblings. Data-only relationships do not select members. This is source graph inventory, not approved architecture or a completion DAG. Unresolved calls remain unverified.', 'small muted'));
    const suggested = new Map(items(contract.cohort.items).map(member => [member.workflow_id, member]));
    const callNames = new Map([...agentWorkflow.options].map(option => [option.value, option.textContent]));
    const shownCalls = new Set();
    for (const option of agentWorkflow.options) {
      if (!option.value || option.disabled) continue;
      const member = suggested.get(option.value), label = node('label', '', 'check-label'), check = node('input');
      check.type = 'checkbox'; check.value = option.value; check.checked = !!member; check.disabled = option.value === contract.workflow_id;
      check.addEventListener('change', () => check.checked ? chosenMembers.add(option.value) : chosenMembers.delete(option.value));
      label.append(check, node('span', `${option.textContent}: ${member?.inclusion_reason || 'Outside literal component, explicit owner scope override required'}`));
      memberChoices.append(label);
      for (const call of items(member?.inclusion_path)) {
        const identity = JSON.stringify([call.caller_workflow_id, call.candidate_workflow_ids, call.command_owner_id, call.property_ordinal, call.wait_mode]);
        if (shownCalls.has(identity)) continue;
        shownCalls.add(identity);
        memberChoices.append(node('p',
          `Literal call: ${callNames.get(call.caller_workflow_id) || call.caller_workflow_id} → ${items(call.candidate_workflow_ids).map(id => callNames.get(id) || id).join(', ')}: ${call.wait_mode || 'unspecified'}. ${call.wait_mode === 'nowait' ? 'Nonblocking launch, not child completion.' : 'Completion remains unverified.'}`, 'small muted'));
      }
    }
    for (const call of items(contract.outside_references?.items)) memberChoices.append(node('p',
      `Unverified call boundary: ${call.caller_name || 'caller'} → ${items(call.candidate_names).join(', ') || 'missing or dynamic child'}. Retained for review, not silently excluded.`, 'notice'));
  }
  // Move the original controls, not copies: Agent activity owns scope and progress.
  const workflowRoller = document.getElementById('agent-workflow-roller');
  (workflowRoller?.parentElement || document.getElementById('agent-workflow')?.parentElement || panel)
    .after(scopePanel);
  const scopeActive = () => scopePanel.isConnected && !agentPage.hidden;
  const scopeWorkerReady = () => scopeActive() && valid() && !agentScope.disabled
    && agentPage.dataset.agentRunStatus === 'idle' && !$('agent-submit').disabled;
  let syncScopeWorker = () => {};
  const savedOutputs = node('section', '', 'detail-list section-gap'); savedOutputs.id = 'proposal-saved-outputs';
  savedOutputs.setAttribute('aria-label', 'Saved workflow designs and artifact status');
  detail.before(savedOutputs);
  // Keep the deterministic example in its own clearly labelled section.
  const createExample = button('workflow-artifact-create-example', 'Generate example Job & notebooks', workflowPanel);
  createExample.title = 'Generate the supported example only. This does not invoke agents or run a Job.';
  createExample.addEventListener('click', () => { if (!workflowDraft.disabled) workflowDraft.click(); });
  const showExampleOutput = button('workflow-artifact-show-example', 'View example Job & notebooks', workflowPanel);
  showExampleOutput.hidden = true;
  showExampleOutput.addEventListener('click', () => {
    if (!workflowOutput.childElementCount) return;
    workflowOutput.scrollIntoView({block: 'start'});
    workflowOutput.querySelector('.workflow-native-card')?.focus({preventScroll: true});
  });
  const examples = disclosure('Source reference and optional tools', node('div'),
    'Background source information and separate draft tools. Expand only the topic you need.');
  examples.lastElementChild.append(sourceDetails, targetDetails, sqlDetails, compilerDetails);
  // Keep the primary review path ahead of optional source inventories and examples.
  panel.append(examples);
  const nextPages = node('section', '', 'section-gap');
  nextPages.append(node('h3', 'After reviewing the Job and notebooks'),
    node('p', 'When ready, review validation, readiness, and CI/CD. Notebook generation and review do not execute or deploy a Job.', 'small muted'));
  const nextButtons = node('div', '', 'button-row');
  for (const [label, destination] of [['Validate', 'validate'], ['Readiness', 'readiness'], ['CI/CD', 'release']]) {
    const link = button('', label, nextButtons); link.removeAttribute('id');
    link.addEventListener('click', () => document.querySelector(`.navigation [data-page="${destination}"]`)?.click());
  }
  nextPages.append(nextButtons); panel.append(nextPages);
  page.querySelector('.page-heading').after(panel);

  let epoch = 0, listTicket = 0, detailTicket = 0, sqlTicket = 0, workflowTicket = 0, understandingTicket = 0, targetTicket = 0, offset = 0, total = 0;
  let retainedExample = null;
  let record = null, latestVersion = null, loading = false, busy = false, loaded = false;
  const invocationContextCache = new Map();
  let loadSavedDrafts = () => {};
  let targetInventory = null, targetLoading = false, detailLoading = false, cohortSaving = false, cohortReading = false;
  let cohortPreparing = false;
  let closeCohortConsent = null;
  const cohortStorageKey = () => JSON.stringify(['orion-saved-review-v1', source().project, source().package, source().workflow, source().scenario, targetScope.value]);
  function savedCohortId() {
    try {
      const id = sessionStorage.getItem(cohortStorageKey());
      return /^[a-f0-9]{32}$/.test(id || '') ? id : '';
    } catch (_) { return ''; }
  }
  let listFlight = null, loadScheduled = false, understandingKey = '', understandingPending = false;
  let submittedLink = null;
  let compilerCapability = null;
  let compilerDiagnostic = '';
  document.addEventListener('orion:health', event => {
    compilerCapability = event.detail?.capabilities?.generation || null;
    controls();
  });
  function compilerSupported(scope = source()) {
    return compilerCapability?.available === true && compilerCapability.scenario === scope.scenario
      && items(compilerCapability.supported_workflows).some(item => item.workflow_id === scope.workflow);
  }
  let capturedTaskCount = null;
  let cohortContext = null, cohortContextKey = '', savedRows = [], selectedCohortWorkflow = null;
  const savedCodeNotes = new Map();
  let scopePending = false, scopeFlight = null;
  const scopeStorageKey = () => JSON.stringify(['orion-review-scope-v1', source().project, source().package, source().scenario]);
  function rememberScope() {
    try { sessionStorage.setItem(scopeStorageKey(), targetScope.value); } catch (_) { /* Session-only scope remains available. */ }
  }
  function restoreScope() {
    try { targetScope.value = sessionStorage.getItem(scopeStorageKey()) === 'cohort' ? 'cohort' : 'workflow'; } catch (_) { targetScope.value = 'workflow'; }
    agentScope.value = targetScope.value;
  }
  let reviewControls = () => {};
  const controllers = new Set();
  function source() {
    const project = $('project-select')?.value || '';
    return { project, package: project ? ($('estate-package-select')?.value ?? $('project-package')?.value ?? '') : (page.dataset.packageHash || ''),
      workflow: workflow.value, scenario: $('scenario').value };
  }
  const key = () => JSON.stringify(source());
  let contextKey = key();
  const valid = () => {
    const scope = source(); return page.dataset.packageReady !== 'false' && (!scope.project || !!scope.package) && ['original', 'bronze'].includes(scope.scenario);
  };
  const hasWorkflow = id => !!id && [...agentWorkflow.options].some(o => o.value === id && !o.disabled);
  const workflowKnown = id => hasWorkflow(id) && !agentWorkflow.disabled;
  // Independent of output-target review: this is a transient, explicit metadata browse.
  function createOracleSourceExplorer() {
    const root = node('details', '', 'review-disclosure section-gap oracle-source-disclosure');
    root.id = 'oracle-source-explorer-disclosure';
    root.append(node('summary', 'Optional: browse possible Oracle source metadata'));
    const panel = node('section', '', 'proposal-review-card oracle-source-content'); panel.id = 'oracle-source-explorer';
    panel.append(node('h3', 'Inspect foreign-catalog metadata'), node('p',
      'Choose a table only to inspect its columns. This does not bind it to the workflow or read rows.', 'small muted'));
    const steps = node('div', '', 'oracle-metadata-steps'); panel.append(steps);
    const catalogStep = node('section', '', 'oracle-metadata-step'); steps.append(catalogStep);
    catalogStep.append(node('strong', '1. Foreign catalog'));
    const catalogActions = node('div', '', 'button-row'); catalogStep.append(catalogActions);
    const start = button('oracle-source-catalogs-load', 'Load catalogs', catalogActions);
    const catalog = field('select', 'oracle-source-catalog', 'Catalog', catalogStep);
    const schemaStep = node('section', '', 'oracle-metadata-step'); steps.append(schemaStep);
    schemaStep.append(node('strong', '2. Schema'));
    const schemaActions = node('div', '', 'button-row'); schemaStep.append(schemaActions);
    const schemas = button('oracle-source-schemas-load', 'Load schemas', schemaActions);
    const schema = field('select', 'oracle-source-schema', 'Schema', schemaStep);
    const tableStep = node('section', '', 'oracle-metadata-step oracle-metadata-table'); steps.append(tableStep);
    tableStep.append(node('strong', '3. Table columns'));
    const tableActions = node('div', '', 'button-row'); tableStep.append(tableActions);
    const tables = button('oracle-source-tables-load', 'Load tables', tableActions);
    const detail = button('oracle-source-table-load', 'Inspect selected table columns', tableActions);
    const table = field('select', 'oracle-source-table', 'Table', tableStep);
    const nextPage = button('oracle-source-next', 'Next metadata page', tableStep);
    const status = node('p', 'Select a workflow, then explicitly load catalog metadata.', 'small muted');
    status.id = 'oracle-source-status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const output = node('div', '', 'detail-list'); output.id = 'oracle-source-output'; panel.append(status, output);
    root.append(panel);
    let serial = 0, pending = false, pageInfo = null, catalogForeign = false;
    const selects = {catalogs: catalog, schemas: schema, tables: table};
    const blank = (select, label) => select.replaceChildren(new Option(label, ''));
    const clearDetail = () => output.replaceChildren();
    function clearFrom(level) {
      if (level <= 0) { blank(catalog, 'Choose a catalog'); catalogForeign = false; }
      if (level <= 1) blank(schema, 'Choose a schema');
      if (level <= 2) blank(table, 'Choose a table');
      clearDetail(); pageInfo = null;
    }
    function sync() {
      const available = valid() && workflowKnown(source().workflow) && scopeActive();
      start.disabled = !available || pending;
      schemas.disabled = !available || pending || !catalog.value || !catalogForeign;
      tables.disabled = !available || pending || !catalog.value || !catalogForeign || !schema.value;
      detail.disabled = !available || pending || !catalog.value || !catalogForeign || !schema.value || !table.value;
      nextPage.disabled = !available || pending || !pageInfo;
      catalog.disabled = !available || catalog.options.length <= 1;
      schema.disabled = !available || schema.options.length <= 1;
      table.disabled = !available || table.options.length <= 1;
    }
    function reset() {
      serial++; pending = false; clearFrom(0);
      status.textContent = 'Selection changed. Previous source metadata cleared; explicitly load catalog metadata.';
      sync();
    }
    for (const [select, level] of [[catalog, 1], [schema, 2], [table, 3]]) {
      select.addEventListener('change', () => {
        serial++; pending = false;
        if (select === catalog) catalogForeign = select.selectedOptions[0]?.dataset.foreign === 'true';
        clearFrom(level);
        status.textContent = select === catalog && select.value && !catalogForeign
          ? 'Catalog type is unknown. Its connection type is not observed; schemas cannot be explored as foreign.'
          : 'Selection changed. Explicitly load the next metadata level.';
        sync();
      });
    }
    async function load(operation, offset = 0) {
      if (pending || !valid() || !workflowKnown(source().workflow) || !scopeActive()) return;
      if (operation !== 'catalogs' && (!catalog.value || !catalogForeign)) return;
      if (['tables', 'table'].includes(operation) && !schema.value) return;
      if (operation === 'table' && !table.value) return;
      const ticket = capture(), selection = {catalog: catalog.value, schema: schema.value, table: table.value};
      const id = ++serial;
      if (operation === 'catalogs') clearFrom(0);
      else if (operation === 'schemas') clearFrom(1);
      else if (operation === 'tables') clearFrom(2);
      else { clearDetail(); pageInfo = null; }
      pending = true; sync();
      status.textContent = 'Reading the requested metadata page. No data rows or connection verification.';
      const query = new URLSearchParams({operation});
      if (operation !== 'catalogs') query.set('catalog', selection.catalog);
      if (['tables', 'table'].includes(operation)) query.set('schema', selection.schema);
      if (operation === 'table') query.set('table', selection.table);
      else { query.set('offset', String(offset)); query.set('limit', '20'); }
      const active = () => current(ticket) && id === serial && scopeActive() && valid() && workflowKnown(ticket.scope.workflow)
        && (operation === 'catalogs' || catalog.value === selection.catalog)
        && (!['tables', 'table'].includes(operation) || schema.value === selection.schema)
        && (operation !== 'table' || table.value === selection.table);
      try {
        // This endpoint is a direct hosted OBO GET, not a hosted operation envelope.
        const result = await request(`/api/oracle-federation/metadata?${query}`, ticket);
        if (!active()) return;
        if (result.format !== 'oracle-federation-metadata-v1' || result.operation !== operation
            || !Array.isArray(result.items) || result.binding_approved !== false
            || result.execution_allowed !== false || result.deployment_allowed !== false)
          throw new Error('Invalid metadata response');
        if (result.status !== 'observed') {
          status.textContent = result.status === 'table_not_found' && result.exists === false
            ? 'The exact table was reported not found; no wider absence is established.'
            : result.status === 'not_foreign_catalog'
            ? 'This catalog was reported as not foreign; no schemas were inspected.'
            : 'Metadata unknown or unavailable, including possible permission denial. Absence and connection type are not established.';
          return;
        }
        if (operation === 'table') {
          const item = result.items[0];
          if (result.exists !== true || result.items.length !== 1 || item?.catalog_name !== selection.catalog
              || item?.schema_name !== selection.schema || item?.name !== selection.table
              || item?.full_name !== [selection.catalog, selection.schema, selection.table].join('.') || !Array.isArray(item.columns))
            throw new Error('Invalid table metadata');
          const format = item.oracle_format === true ? 'Oracle format observed' : item.oracle_format === false
            ? 'Non-Oracle format observed' : 'Table format unknown';
          status.textContent = `${format}. Metadata is not a source binding or approval.`;
          output.append(node('h4', `Table: ${item.full_name}`));
          if (!item.columns.length) output.append(node('p', 'No columns returned in this table metadata.', 'small muted'));
          for (const column of item.columns) output.append(node('p',
            `${column.name} · ${column.type_text} · ${column.nullable === true ? 'nullable' : column.nullable === false ? 'not nullable' : 'nullability unknown'}`, 'small muted'));
          return;
        }
        if (result.offset !== offset || result.limit !== 20 || typeof result.has_more !== 'boolean'
            || (result.has_more && !result.scan_limit_reached && (!Number.isInteger(result.next_offset) || result.next_offset <= offset || result.next_offset >= 1000)))
          throw new Error('Invalid pagination');
        const select = selects[operation];
        for (const item of result.items) {
          if (typeof item.name !== 'string' || !item.name) throw new Error('Invalid metadata item');
          if (operation === 'schemas' && item.catalog_name !== selection.catalog) throw new Error('Schema identity mismatch');
          if (operation === 'tables' && (item.catalog_name !== selection.catalog || item.schema_name !== selection.schema)) throw new Error('Table identity mismatch');
          const suffix = operation === 'catalogs' ? (item.foreign === true ? 'foreign, connection type unknown' : 'catalog type unknown')
            : operation === 'tables' ? (item.oracle_format === true ? 'Oracle format observed' : item.oracle_format === false ? 'non-Oracle format observed' : 'format unknown') : '';
          const option = new Option(safe(`${item.name}${suffix ? ` (${suffix})` : ''}`), item.name);
          if (operation === 'catalogs') option.dataset.foreign = item.foreign === true ? 'true' : 'unknown';
          select.append(option);
        }
        if (result.has_more && !result.scan_limit_reached) pageInfo = {operation, offset: result.next_offset};
        status.textContent = `${result.items.length ? `${result.items.length} metadata item(s) on this page.` : 'No visible items in this page.'} ${result.has_more ? 'Results are partial; more metadata may be available.' : 'No further page was reported, not an estate-wide absence.'} ${result.scan_limit_reached ? 'The scan limit was reached; results are incomplete.' : ''} Pages are raw SDK windows, not a snapshot.`;
      } catch (error) {
        if (active()) {
          clearFrom(operation === 'catalogs' ? 0 : operation === 'schemas' ? 1 : operation === 'tables' ? 2 : 3);
          status.textContent = /HTTP (401|403)\b/.test(error.message || '')
            ? 'Metadata request denied or authentication unavailable. Access and absence are not established.'
            : 'Metadata unavailable or response invalid. Access, absence, and connection type remain unknown.';
        }
      } finally { if (active()) { pending = false; sync(); } }
    }
    start.addEventListener('click', () => load('catalogs'));
    schemas.addEventListener('click', () => load('schemas'));
    tables.addEventListener('click', () => load('tables'));
    detail.addEventListener('click', () => load('table'));
    nextPage.addEventListener('click', () => { if (pageInfo) load(pageInfo.operation, pageInfo.offset); });
    blank(catalog, 'Choose a catalog'); blank(schema, 'Choose a schema'); blank(table, 'Choose a table');
    return {root, reset, sync};
  }
  const oracleExplorer = createOracleSourceExplorer();
  scopeActions.after(oracleExplorer.root);
  function controls() {
    const scope = source(), name = [...agentWorkflow.options].find(option => option.value === scope.workflow)?.textContent;
    originalName.textContent = name || 'Choose a workflow';
    originalWorkflow.disabled = busy || !valid() || !workflowKnown(scope.workflow) || typeof window.orionOpenWorkflow !== 'function';
    originalWorkflow.title = originalWorkflow.disabled ? 'Select an available workflow in a ready package. Pipeline explorer navigation must be available.' : 'Open this exact workflow in Pipeline explorer without changing design scope.';
    sourceName.textContent = name || 'Choose a workflow';
    sourceCount.textContent = Number.isInteger(capturedTaskCount) ? `${capturedTaskCount} tasks found in the source workflow.` : 'Open source evidence to see the captured tasks.';
    proposedName.textContent = record ? `${[...agentWorkflow.options].find(option => option.value === record.workflow_id)?.textContent || 'Selected workflow'}: ${record.design.title}` : 'No saved design selected';
    proposedCount.textContent = record ? `${items(record.design.tasks).length} tasks in this saved design. Notebook code is shown when a draft is available.` : 'Open Agent activity to run the conversion. Saved results can be reviewed here.';
    scopeReconnect.hidden = targetScope.value !== 'cohort';
    generate.textContent = targetScope.value === 'cohort' ? 'Review cohort before running agents' : 'Open Agent activity';
    generate.title = targetScope.value === 'cohort'
      ? 'Review the cohort scope before explicitly starting the design worker.'
      : 'Open Agent activity with this workflow selected. No agent run starts until Invoke agents is clicked.';
    scopeSummary.textContent = `${name || (scope.workflow ? 'Selected workflow unavailable in this package' : 'Choose one workflow')} · ${scope.scenario === 'bronze' ? 'Source data already in Unity Catalog' : 'Original source systems'} · ${scope.project ? 'Selected project / package' : 'Standalone package'}${page.dataset.packageReady === 'false' ? ' · Understanding not ready, actions locked' : ''}`;
    const reading = loading || detailLoading || scopePending || cohortPreparing;
    workflow.disabled = busy || !valid() || agentWorkflow.disabled;
    generate.disabled = busy || reading || !valid() || !workflowKnown(workflow.value);
    sqlDraft.disabled = busy || reading || !valid() || !workflowKnown(workflow.value);
    const exampleSupported = compilerSupported(scope);
    workflowDraft.disabled = busy || reading || !valid() || !workflowKnown(workflow.value) || !exampleSupported || !!compilerDiagnostic;
    createExample.hidden = true; // The original generator remains in the advanced example section.
    createExample.disabled = workflowDraft.disabled;
    if (!exampleSupported) {
      workflowStatus.textContent = scope.scenario === 'original'
        ? 'Original mode unsupported by the static example compiler. Use agent design & code for this workflow.'
        : 'Use agent design & code for this workflow. The static compiler supports only its four reviewed bronze examples.';
    } else if (compilerDiagnostic) workflowStatus.textContent = compilerDiagnostic;
    targetRead.disabled = busy || targetLoading || !valid() || !workflowKnown(workflow.value);
    cohortCreate.disabled = busy || reading || targetLoading || cohortSaving || !targetInventory || !valid() || !workflowKnown(workflow.value);
    cohortCreate.textContent = targetScope.value === 'cohort' ? 'Save reviewed cohort scope' : 'Save reviewed workflow scope';
    cohortCreate.title = targetScope.value === 'cohort'
      ? 'Save the reviewed cohort membership and source-target evidence. This does not start a worker.'
      : 'Save this workflow’s reviewed source-target evidence. This does not start a worker.';
    targetScope.disabled = busy || reading || targetLoading || !valid() || !workflowKnown(workflow.value);
    targetScope.disabled ||= agentScope.disabled;
    cohortCreate.disabled ||= agentScope.disabled;
    for (const input of memberPanel.querySelectorAll('input, textarea'))
      input.disabled = busy || reading || targetLoading || agentScope.disabled
        || (input.type === 'checkbox' && input.value === scope.workflow);
    cohortRead.disabled = busy || cohortReading || cohortSaving || !valid(); cohortId.disabled = busy || cohortReading || cohortSaving || !valid();
    refresh.disabled = busy || loading || detailLoading || !valid();
    versions.disabled = busy || loading || detailLoading || !valid() || ![...versions.options].some(option => option.value);
    previous.disabled = busy || loading || !loaded || offset === 0;
    next.disabled = busy || loading || !loaded || offset + 50 >= total;
    oracleExplorer.sync();
    syncScopeWorker();
    reviewControls();
  }
  function invalidate(message = 'Selection changed. Previous responses and actions cleared.', retainChoices = false) {
    window.orionRedesignAnimation?.close();
    const sameContext = contextKey === key();
    const example = retainChoices && sameContext && retainedExample?.key === key() ? retainedExample.result : null;
    closeCohortConsent?.(false); cohortPreparing = false;
    // Keep only the actual previously verified graph DOM during an explicit
    // same-context refresh. This is a labelled historical view, never authority.
    const retainedGraph = retainChoices && sameContext ? detail.querySelector('#saved-design-panel-0') : null;
    if (submittedLink && submittedLink.key !== key()) submittedLink = null;
    contextKey = key(); epoch++; listTicket++; detailTicket++; sqlTicket++; workflowTicket++; understandingTicket++; targetTicket++;
    oracleExplorer.reset();
    listFlight = null; versionFlight = null; understandingKey = ''; understandingPending = false; targetLoading = false; detailLoading = false; cohortSaving = false; cohortReading = false;
    targets.setAttribute('aria-busy', 'false');
    controllers.forEach(c => c.abort()); controllers.clear();
    capturedTaskCount = null;
    understanding.replaceChildren(node('p', 'Select a workflow to read current XML evidence. Previous selection cleared.', 'small muted'));
    sqlOutput.replaceChildren();
    sqlStatus.textContent = 'Selection changed. Previous SQL output cleared.';
    workflowOutput.replaceChildren(); compilerDiagnostic = '';
    retainedExample = null;
    showExampleOutput.hidden = true;
    targetOutput.replaceChildren(); targetStatus.textContent = 'Selection changed. Inspect the current workflow to review source targets.';
    targetInventory = null; cohortId.value = ''; cohortOutput.replaceChildren();
    syncScopeWorker = () => {};
    chosenMembers.clear(); memberPanel.hidden = true; memberChoices.replaceChildren();
    scopeOwner.value = migrationUnit.value = scopeRationale.value = semanticLinks.value = '';
    cohortContext = null; cohortContextKey = ''; scopeFlight = null; scopePending = false; savedRows = []; selectedCohortWorkflow = null;
    savedOutputs.replaceChildren(); savedCodeNotes.clear();
    workflowStatus.textContent = source().scenario === 'original'
      ? 'Original mode unsupported. Select bronze; original Oracle connections and transaction equivalence require a separate implementation.'
      : 'Selection changed. Previous workflow output cleared.';
    if (example) {
      retainedExample = {key: contextKey, result: example};
      renderWorkflowArtifacts(example, capture(), workflowTicket);
      workflowStatus.textContent = 'Previously generated example retained for this workflow. No agent design, execution or deployment is implied.';
    }
    record = null; latestVersion = null; reviewControls = () => {}; loadSavedDrafts = () => {}; loaded = false; busy = false; loading = false; offset = 0; total = 0;
    if (!retainChoices || !sameContext) versions.replaceChildren(new Option('Choose a stored design version', ''));
    detail.replaceChildren();
    delete detail.dataset.refreshUnconfirmed;
    if (retainedGraph) {
      retainedGraph.hidden = false;
      retainedGraph.removeAttribute('role'); retainedGraph.removeAttribute('aria-labelledby');
      retainedGraph.querySelectorAll('button').forEach(control => { control.disabled = true; });
      detail.dataset.refreshUnconfirmed = 'true';
      detail.append(node('p', `Previously verified saved graph (version ${retainedGraph.dataset.designVersion}). Refresh is unconfirmed; current version and permissions have not been reconfirmed. Review, drafting and downloads are unavailable until an exact saved record is read.`, 'notice'), retainedGraph);
    }
    status.textContent = message; controls();
  }
  function capture() {
    if (contextKey !== key()) invalidate();
    return { epoch, key: contextKey, scope: source() };
  }
  const current = ticket => ticket.epoch === epoch && ticket.key === key();
  originalWorkflow.addEventListener('click', () => {
    controls();
    if (originalWorkflow.disabled || page.hidden) return;
    const scope = source();
    // The main navigation owner validates current overview membership and all
    // scope pins again. No event fallback, inferred ID, or source fetch here.
    const opened = window.orionOpenWorkflow({workflowId: scope.workflow, projectId: scope.project,
      packageHash: scope.package, scenario: scope.scenario});
    if (opened !== true) status.textContent = 'Original workflow could not be opened for the current package. Select a current workflow and try again.';
  });
  async function loadTargets(start = 0, expected = null, history = []) {
    if (targetLoading || busy) return;
    const ticket = capture(), serial = ++targetTicket, selectedScope = targetScope.value;
    if (!valid() || !workflowKnown(ticket.scope.workflow) || !scopeActive()) return;
    targetLoading = true; controls(); targets.setAttribute('aria-busy', 'true');
    targetOutput.replaceChildren(); targetStatus.textContent = 'Reading the original workflow’s target definitions. This does not run agents or change the catalog.';
    try {
      const query = new URLSearchParams({ scope: selectedScope, offset: String(start), limit: '20' });
      if (expected) query.set('input_version', expected);
      const result = await request(`/api/workflows/${encodeURIComponent(ticket.scope.workflow)}/target-review?${query}`, ticket);
      if (!current(ticket) || serial !== targetTicket || selectedScope !== targetScope.value || !scopeActive()) return;
      if (result.format !== 'source-target-review-v1' || result.workflow_id !== ticket.scope.workflow
          || result.scope !== selectedScope || result.assignment_persisted !== false
          || result.execution_allowed !== false || result.deployment_allowed !== false
          || !result.targets || !Array.isArray(result.targets.items)
          || !/^[a-f0-9]{64}$/.test(result.input_version) || (expected && expected !== result.input_version))
        throw new Error('Target review scope or evidence version mismatch.');
      targetInventory = {input_version: result.input_version, scope: selectedScope}; controls();
      targetStatus.textContent = `${result.targets.total} output table definitions found across ${result.workflow_count} workflow(s). These are the tables the original workflow writes to; choose a Databricks destination only if you want to record one.`;
      if (!result.targets.items.length) targetOutput.append(node('p', 'No target definitions captured in this scope. This does not establish that there are no external outputs.', 'small muted'));
      for (const target of result.targets.items) {
        const card = node('article', '', 'proposal-review-card');
        card.append(node('h4', target.name || 'Unnamed output table'));
        const technical = node('details'); technical.append(node('summary', 'Original table details'), readableFields({
          exported_identity: target.exported_identity, source_scope: target.source_scope,
          role: target.role, destination: 'Unassigned, not approved', workflow_ids: target.workflow_ids,
          file_disposition_required: target.file_disposition_required
        })); card.append(technical);
        const columns = node('details'); columns.append(node('summary', `Columns in the original output table (${items(target.columns).length})`), readableFields({ columns: items(target.columns) }));
        card.append(columns); targetOutput.append(card);
        if (/^[a-f0-9]{64}$/.test(target.target_id || '')) targetSelection(card, target, result, ticket);
      }
      if (history.length) button('source-target-previous', 'Previous targets', targetOutput).addEventListener('click', () => loadTargets(history[history.length - 1], result.input_version, history.slice(0, -1)));
      if (result.targets.has_more) button('source-target-next', 'Next targets', targetOutput).addEventListener('click', () => loadTargets(result.targets.next_offset, result.input_version, [...history, start]));
    } catch (error) {
      if (current(ticket) && serial === targetTicket) targetStatus.textContent = `Target review unavailable: ${safe(error.message)}. No destination mapping is inferred.`;
    } finally {
      if (current(ticket) && serial === targetTicket) { targetLoading = false; targets.setAttribute('aria-busy', 'false'); controls(); }
    }
  }
  targetRead.addEventListener('click', async () => {
    await loadTargets();
    if (scopeActive()) targetStatus.scrollIntoView({block: 'nearest'});
  });
  targetScope.addEventListener('change', () => {
    agentScope.value = targetScope.value;
    rememberScope(); invalidate('Review scope changed. Reading saved designs only.'); scheduleLoad();
    document.dispatchEvent(new CustomEvent('orion:review-scope', {detail: {...source(), mode: targetScope.value}}));
  });
  agentScope.addEventListener('change', () => { targetScope.value = agentScope.value; targetScope.dispatchEvent(new Event('change', {bubbles: true})); });
  document.addEventListener('orion:open-cohort-review', async event => {
    if (event.detail && (event.detail.workflow_id !== agentWorkflow.value
        || !['original', 'bronze'].includes(event.detail.scenario))) return;
    const previousScope = targetScope.value;
    if (event.detail) $('scenario').value = event.detail.scenario;
    workflow.value = agentWorkflow.value; targetScope.value = agentScope.value = 'cohort'; rememberScope();
    // Reopening the same scope must preserve explicit member choices and receipts.
    if (previousScope !== 'cohort' || contextKey !== key())
      invalidate('Review the exact cohort and save its scope before starting its design run.');
    await loadScope();
    if (scopeActive()) { scopePanel.scrollIntoView({block: 'nearest'}); targetScope.focus({preventScroll: true}); }
  });
  function targetSelection(card, target, inventory, ticket) {
    const form = node('fieldset', '', 'section-gap review-action-card');
    form.append(node('h5', 'Choose where this output table should go'),
      node('p', 'Optional: save a proposed Databricks destination for this output table. This does not create a table.', 'small muted'));
    const layer = field('select', `target-layer-${target.target_id}`, 'Output category', form);
    for (const value of ['bronze', 'silver', 'gold']) layer.append(new Option(value, value));
    const catalog = field('select', `target-catalog-${target.target_id}`, 'Databricks catalog', form);
    catalog.append(new Option('Load catalogs, then choose one', ''));
    const catalogButton = button(`target-catalog-load-${target.target_id}`, 'Load available catalogs', form);
    const catalogStatus = node('p', 'Catalog names are loaded only when requested.', 'small muted'); catalogStatus.setAttribute('role', 'status'); form.append(catalogStatus);
    const schema = field('input', `target-schema-${target.target_id}`, 'Destination schema', form);
    const table = field('input', `target-table-${target.target_id}`, 'Destination table name', form);
    const label = node('label', '', 'check-label'), fixture = node('input');
    fixture.type = 'checkbox'; label.append(fixture, node('span', 'Test fixture only, not a production binding')); form.append(label);
    if (target.fixture_candidate) {
      const candidate = target.fixture_candidate;
      form.append(node('p', `XML-derived test fixture candidate: ${candidate.schema_name}.${candidate.table}. Choose an existing fixture catalog separately; existence and access are unverified.`, 'small muted'));
      button(`target-use-fixture-${target.target_id}`, 'Use candidate schema and table', form).addEventListener('click', () => {
        schema.value = candidate.schema_name; table.value = candidate.table; fixture.checked = true;
      });
    }
    const load = button(`target-load-${target.target_id}`, 'Load saved selection', form);
    const save = button(`target-save-${target.target_id}`, 'Save selection', form); save.disabled = true;
    const verify = button(`target-verify-${target.target_id}`, 'Verify UC metadata and SELECT access', form); verify.disabled = true;
    const note = node('p', 'Load the saved version before editing. UC existence and schema compatibility are not verified.', 'small muted');
    note.setAttribute('role', 'status'); form.append(note); card.append(form);
    let version = null;
    function chooseCatalog(name) {
      if (name && ![...catalog.options].some(option => option.value === name))
        catalog.append(new Option(`${name} (saved, not yet checked)`, name));
      catalog.value = name || '';
    }
    catalogButton.addEventListener('click', async () => {
      if (form.disabled || !current(ticket) || !valid()) return;
      catalogButton.disabled = true; catalogStatus.textContent = 'Loading available catalogs…';
      try {
        const connection = await request('/api/databricks/volumes/status', ticket);
        if (!current(ticket) || !form.isConnected) return;
        if (!connection.connected) {
          const connected = await request('/api/databricks/volumes/connect', ticket, {});
          if (!current(ticket) || !form.isConnected) return;
          if (!connected.connected) throw new Error('Databricks connection was not confirmed.');
        }
        const result = await request('/api/databricks/volumes/catalogs', ticket);
        if (!current(ticket) || !form.isConnected) return;
        if (!Array.isArray(result.items)) throw new Error('Catalog list was not returned.');
        const selected = catalog.value;
        const names = [...new Set(result.items.map(item => item?.name).filter(name => typeof name === 'string' && /^[A-Za-z_][A-Za-z0-9_]{0,254}$/.test(name)))].sort((a, b) => a.localeCompare(b));
        catalog.replaceChildren(new Option('Choose a catalog', ''));
        for (const name of names) catalog.append(new Option(name, name));
        chooseCatalog(selected);
        catalogStatus.textContent = names.length ? `${names.length} catalogs available.${result.truncated ? ' The list is limited; more may exist.' : ''}` : 'No selectable catalogs returned. Check workspace access.';
      } catch (error) { if (current(ticket) && form.isConnected) catalogStatus.textContent = `Catalogs unavailable: ${safe(error.message)}. Try again.`; }
      finally { if (form.isConnected) catalogButton.disabled = false; }
    });
    load.addEventListener('click', async () => {
      if (form.disabled || !current(ticket) || !valid()) return;
      form.disabled = true; load.disabled = true;
      try {
        const result = await request(`/api/target-selections/${encodeURIComponent(ticket.scope.workflow)}/${target.target_id}`, ticket);
        if (!current(ticket)) return;
        version = result.version;
        if (!Number.isInteger(version) || version < 0) throw new Error('Invalid selection version');
        const value = result.selection;
        if (value) {
          layer.value = value.layer; chooseCatalog(value.catalog); schema.value = value.schema_name; table.value = value.table;
          fixture.checked = value.is_test_fixture === true;
        }
        note.textContent = value?.stale ? 'Saved selection is stale. Review current evidence and save again.' : 'Selection loaded. UC existence and schema compatibility remain unverified.';
        save.disabled = false;
        verify.disabled = !value || value.stale;
      } catch (error) { if (current(ticket)) note.textContent = `Selection unavailable: ${safe(error.message)}`; }
      finally { if (current(ticket) && form.isConnected) { form.disabled = false; load.disabled = false; } }
    });
    save.addEventListener('click', async () => {
      if (version === null || form.disabled || !current(ticket) || !valid()) return;
      form.disabled = true; save.disabled = true; verify.disabled = true;
      try {
        const result = await request(`/api/target-selections/${target.target_id}`, ticket, {
          workflow_id: ticket.scope.workflow, scope: inventory.scope, input_version: inventory.input_version,
          expected_version: version, layer: layer.value, catalog: catalog.value, schema_name: schema.value,
          table: table.value, is_test_fixture: fixture.checked
        });
        if (!current(ticket)) return;
        version = result.version;
        note.textContent = `Saved ${result.selection.qualified_name}, version ${version}. Unapproved; UC metadata and schema are unverified.`;
        save.disabled = false; verify.disabled = false;
      } catch (error) { if (current(ticket)) { version = null; note.textContent = `Save not confirmed: ${safe(error.message)} Load the saved version before retrying.`; } }
      finally { if (current(ticket) && form.isConnected) form.disabled = false; }
    });
    verify.addEventListener('click', async () => {
      if (version === null || version < 1 || form.disabled || !current(ticket) || !valid()) return;
      form.disabled = true; verify.disabled = true;
      try {
        const result = await request(`/api/target-selections/${target.target_id}/verify`, ticket, {
          workflow_id: ticket.scope.workflow, expected_version: version, consent: true, check_select: true
        });
        if (!current(ticket)) return;
        note.textContent = `UC observation: ${safe(result.status)}; schema compatible: ${result.schema_compatible === null ? 'unknown' : String(result.schema_compatible)}; zero-row SELECT: ${safe(result.data_access_status || 'not tested')}. Write permission and data contents are not tested. Observation is not retained or approved; empty fixtures do not prove parity.`;
      } catch (error) { if (current(ticket)) note.textContent = `UC verification unavailable: ${safe(error.message)}. No approval inferred.`; }
      finally { if (current(ticket) && form.isConnected) { form.disabled = false; if (version !== null) verify.disabled = false; } }
    });
  }
  function showCohort(result) {
    const scope = source();
    if (!result || !/^[a-f0-9]{32}$/.test(result.id || '') || !Array.isArray(result.workflows)
        || result.request?.workflow_id !== scope.workflow || result.request?.scenario !== scope.scenario
        || result.request?.scope !== targetScope.value || (scope.package && result.package_hash !== scope.package)
        || typeof result.stale !== 'boolean') throw new Error('Saved review does not match the selected workflow, scenario and package.');
    chosenMembers = new Set(result.workflows.map(row => row.workflow_id));
    for (const check of memberChoices.querySelectorAll('input[type="checkbox"]')) check.checked = chosenMembers.has(check.value);
    cohortId.value = result.id;
    // Only a confirmed scoped receipt is remembered. Reconnect always reads
    // the actual server record; a session ID never authorizes model work.
    try { sessionStorage.setItem(cohortStorageKey(), result.id); } catch (_) { /* Optional session storage. */ }
    cohortOutput.replaceChildren(node('p', `${result.workflows.length} workflow review records. ${result.stale ? 'Evidence has changed; this scope is stale.' : 'Use the action buttons below to design or draft the selected scope.'} Hosted processing continues only while the current user authorization remains valid. An uncertain or failed operation must be inspected, never replayed automatically.`, 'notice'));
    cohortOutput.append(node('p', `Chosen migration unit: ${result.request.migration_unit || 'literal-call component, no owner override supplied'}. App-provided scope owner label (not authenticated identity): ${result.request.owner || 'not supplied'}. Rationale: ${result.request.scope_rationale || 'Review connected literal calls, not automatic consolidation'}. Excluded literal members: ${items(result.excluded_member_ids).length}. Added by owner scope: ${items(result.added_member_ids).length}. Missing calls remain unverified. Architecture and execution are not approved.`, 'small muted'));
    for (const item of result.workflows) {
      const card = disclosure(`${item.name}: ${item.status.replaceAll('_', ' ')}`, node('div'));
      card.lastElementChild.append(node('p', item.reason, 'small muted'));
      for (const ref of items(item.redesign_refs)) {
        if (!/^[a-f0-9]{64}$/.test(ref.id) || !Number.isInteger(ref.version) || ref.version < 1) continue;
        const open = button('', `View proposal & code · v${ref.version}`, card.lastElementChild); open.removeAttribute('id');
        open.disabled = !workflowKnown(item.workflow_id);
        open.addEventListener('click', () => {
          if (busy || loading || detailLoading || !valid()) return;
          const navigation = document.querySelector('.navigation [data-page="proposals"]');
          if (!navigation || navigation.disabled) return;
          navigation.click();
          const choice = JSON.stringify([ref.id, ref.version]);
          if (![...versions.options].some(option => option.value === choice)) versions.append(new Option(`${item.name} · v${ref.version}`, choice));
          selectedCohortWorkflow = item.workflow_id; versions.value = choice; selectVersion();
        });
      }
      cohortOutput.append(card);
    }
    const start = button('cohort-design-start', 'Design pending workflows sequentially', cohortOutput);
    start.disabled = result.stale || !result.workflows.some(item => item.status === 'awaiting_individual_review');
    const progress = node('p', 'No model action is started by opening this review.', 'small muted');
    progress.setAttribute('role', 'status'); cohortOutput.append(progress);
    const readError = node('p', '', 'small notice'); readError.setAttribute('role', 'status'); cohortOutput.append(readError);
    const stop = button('cohort-design-stop', 'Stop this run and remaining sequence', cohortOutput);
    stop.hidden = true;
    let activeRunId = null, activeTicket = null, stopSequence = false, stopRequested = false;
    stop.addEventListener('click', async () => {
      if (!activeRunId || !activeTicket || !current(activeTicket) || stop.disabled) return;
      stopSequence = true; stop.disabled = true;
      const id = activeRunId, ticket = activeTicket;
      try {
        const receipt = await request(`/api/agents/runs/${id}/cancel`, ticket, {});
        if (!current(ticket) || activeRunId !== id) return;
        if (receipt.id !== id || receipt.cancellation_requested !== true) throw new Error('Stop receipt not confirmed.');
        stopRequested = true;
        readError.textContent = 'Stop requested. Waiting for the worker to confirm terminal status; no remaining workflow will be submitted.';
      } catch (error) {
        if (current(ticket) && activeRunId === id) {
          readError.textContent = `Stop unconfirmed: ${safe(error.message)}. Status observation continues; remaining submissions are disabled.`;
          stop.disabled = false;
        }
      }
    });
    const draftRoot = field('input', 'cohort-draft-root', 'Workspace destination for draft notebook paths', cohortOutput);
    draftRoot.placeholder = '/Workspace/Users/...';
    const draftStart = button('cohort-draft-start', 'Draft approved cohort on server', cohortOutput);
    draftStart.disabled = result.stale || !result.workflows.some(item => item.status === 'design_ready');
    let cohortPending = false;
    function syncCohort() {
      start.disabled = cohortPending || !scopeWorkerReady() || result.stale || !result.workflows.some(item => item.status === 'awaiting_individual_review');
      draftStart.disabled = cohortPending || !scopeWorkerReady() || !draftRoot.value.trim() || result.stale || !result.workflows.some(item => item.status === 'design_ready');
      draftRoot.disabled = cohortPending;
    }
    syncScopeWorker = syncCohort;
    draftRoot.addEventListener('input', syncCohort); syncCohort();
    draftStart.addEventListener('click', async () => {
      if (cohortPending || !scopeWorkerReady()) return;
      const ticket = capture();
      if (!current(ticket) || result.stale || result.request.scenario !== ticket.scope.scenario
          || result.request.workflow_id !== ticket.scope.workflow || !draftRoot.value.trim()) {
        progress.textContent = 'Select the saved workflow scope and enter a workspace destination.'; return;
      }
      cohortPending = true; syncCohort();
      try {
        if (!window.orionHostedIntake || !await window.orionHostedIntake.probe()) throw new Error('The server cohort drafting worker requires hosted user authorization.');
        const headers = ticket.scope.project ? {'X-Workbench-Project': ticket.scope.project, 'X-Workbench-Package': ticket.scope.package} : {};
        const outcome = await window.orionHostedIntake.submit(`/api/cohorts/${result.id}/draft`,
          {consent: true, workspace_root: draftRoot.value.trim()}, headers);
        if (current(ticket)) progress.textContent = `Cohort drafting: ${safe(outcome?.status || 'unknown')}. ${outcome?.workflow_id ? `Workflow: ${safe(outcome.workflow_id)}. ` : ''}${outcome?.operation_id ? `Operation: ${safe(outcome.operation_id)}. Inspect the claim before continuing. ` : ''}Bundles are unverified drafts, not execution or deployment approval.`;
      } catch (error) {
        if (current(ticket)) progress.textContent = `Drafting stopped: ${safe(error.message)}. Inspect the saved operation before continuing.`;
      } finally { if (current(ticket) && draftStart.isConnected) { cohortPending = false; syncCohort(); } }
    });
    start.addEventListener('click', async () => {
      if (cohortPending || !scopeWorkerReady()) return;
      const ticket = capture();
      if (!current(ticket) || result.stale || result.request.scenario !== ticket.scope.scenario
          || result.request.workflow_id !== ticket.scope.workflow) {
        progress.textContent = 'Select the saved workflow and scenario before starting design.'; return;
      }
      cohortPending = true; syncCohort();
      const pending = result.workflows.filter(item => item.status === 'awaiting_individual_review');
      stopSequence = false; stopRequested = false;
      let modal = null;
      try {
        const hosted = window.orionHostedIntake && await window.orionHostedIntake.probe();
        if (hosted) {
          const headers = ticket.scope.project ? {'X-Workbench-Project': ticket.scope.project, 'X-Workbench-Package': ticket.scope.package} : {};
          progress.textContent = 'Starting the server worker. Each workflow is committed independently; interrupted claims are not replayed.';
          const outcome = await window.orionHostedIntake.submit(`/api/cohorts/${result.id}/design`, {consent: true}, headers);
          if (!current(ticket)) return;
          const saved = await request(`/api/cohorts/${result.id}`, ticket);
          if (current(ticket)) {
            showCohort(saved);
            cohortOutput.prepend(node('p', `Server worker: ${safe(outcome?.status || 'unknown')}. ${outcome?.workflow_id ? `Workflow: ${safe(outcome.workflow_id)}. ` : ''}${outcome?.operation_id ? `Operation: ${safe(outcome.operation_id)}. ` : ''}${outcome?.reason ? `${safe(outcome.reason)} ` : ''}Review saved records and inspect any uncertain operation before continuing.`, 'notice'));
          }
          return;
        }
        if (!hosted) modal = localDraftProgress('Workflow design progress', stop);
        for (let i = 0; i < pending.length; i++) {
          if (!current(ticket)) throw new Error('Selection changed; no further workflows were submitted.');
          const item = pending[i], path = `/api/cohorts/${result.id}/workflows/${item.workflow_id}/design`;
          const body = {operation_id: item.operation_id, consent: true};
          progress.textContent = `Designing ${i + 1} of ${pending.length}: ${item.name}. Review and approval remain separate.`;
          modal?.update(progress.textContent);
          let run;
          if (hosted) {
            const headers = ticket.scope.project ? {'X-Workbench-Project': ticket.scope.project, 'X-Workbench-Package': ticket.scope.package} : {};
            run = await window.orionHostedIntake.submit(path, body, headers, item.operation_id);
          } else {
            run = await request(path, ticket, body);
          }
          if (!/^[a-f0-9]{32}$/.test(run?.id || '')) throw new Error('Run receipt is missing. Inspect the saved operation before continuing.');
          const runId = run.id;
          activeRunId = runId; activeTicket = ticket; stop.hidden = false; stop.disabled = false;
          // Hosted submission returns the initially queued response after the
          // worker drains. Always inspect the committed run, not that response.
          for (;;) {
            if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
            try {
              run = await request(`/api/agents/runs/${runId}?status_only=true`, ticket);
            } catch (error) {
              if (!current(ticket)) throw error;
              progress.textContent = `Run ${runId} · ${item.name}: status unconfirmed. No completion or running state inferred.`;
              readError.textContent = `Status read unavailable: ${safe(error.message)}. Reconnecting to this same run only; no mutation replay.`;
              modal?.update(`${progress.textContent} ${readError.textContent}`);
              await new Promise(resolve => setTimeout(resolve, 1000));
              continue;
            }
            if (run.id !== runId || run.workflow_id !== item.workflow_id || run.cohort_id !== result.id
                || run.package_hash !== result.package_hash || run.scenario !== result.request.scenario)
              throw new Error('Run receipt does not match the saved workflow scope.');
            const activity = run.progress || {};
            progress.textContent = `Design ${i + 1} of ${pending.length} · ${item.name} · Run ${runId}: ${run.status}. Phase: ${run.phase || activity.stage || 'not reported'}. Agent: ${activity.active_agent || activity.agent || 'not reported'}. Model: ${activity.active_model || activity.model || 'not reported'}. Tool: ${activity.active_tool || activity.tool || 'not reported'}.`;
            if (!stopRequested) readError.textContent = '';
            modal?.update(`${progress.textContent}${stopRequested ? ' Stop requested; awaiting worker confirmation.' : ''}`);
            if (run.status === 'completed') break;
            if (!['queued', 'running', 'cancelling'].includes(run.status)) {
              stopSequence = true;
              break;
            }
            await new Promise(resolve => setTimeout(resolve, 1000));
          }
          stop.hidden = true; activeRunId = null;
          // Artifact freshness is independent of the stored terminal observation.
          try { await request(`/api/agents/runs/${runId}`, ticket); }
          catch (error) {
            if (!current(ticket)) throw error;
            readError.textContent = `Run ${runId}: confirmed ${run.status}. Detailed artifacts unavailable: ${safe(error.message)}. No run was restarted.`;
            stopSequence = true;
          }
          if (stopSequence) break;
        }
        try {
          const saved = await request(`/api/cohorts/${result.id}`, ticket);
          if (current(ticket) && !readError.textContent) showCohort(saved);
        } catch (error) {
          if (!current(ticket)) throw error;
          readError.textContent = `Saved review read unavailable: ${safe(error.message)}. Last observed run status is retained; no automatic replay.`;
        }
        modal?.finish(`${progress.textContent} ${readError.textContent} ${stopSequence ? 'Remaining sequence not submitted.' : 'Submitted actions finished.'} Review saved results; no deployment was performed.`);
      } catch (error) {
        modal?.finish(`Tracking interrupted: ${safe(error.message)}. Run completion is unconfirmed; no automatic replay.`);
        if (current(ticket)) readError.textContent = `Tracking interrupted: ${safe(error.message)}. Inspect run ${activeRunId || 'receipt'} before any further submission; no automatic replay.`;
      } finally { if (current(ticket) && start.isConnected) { stop.hidden = true; activeRunId = null; cohortPending = false; syncCohort(); } }
    });
  }
  async function saveCohort() {
    const ticket = capture(); if (cohortSaving || targetLoading || agentScope.disabled || !valid() || !targetInventory || !current(ticket)) return;
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    const id = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
    cohortId.value = id; cohortSaving = true; controls();
    try {
      const result = await request('/api/cohorts', ticket, {request_id: id, workflow_id: ticket.scope.workflow,
        scenario: ticket.scope.scenario, scope: targetInventory.scope, input_version: targetInventory.input_version, consent: true,
        ...(targetInventory.scope === 'cohort' && chosenMembers.size ? {
          member_ids: [...chosenMembers].sort(), owner: scopeOwner.value.trim(), migration_unit: migrationUnit.value.trim(),
          scope_rationale: scopeRationale.value.trim(), semantic_links: semanticLinks.value.split('\n').map(value => value.trim()).filter(Boolean)
        } : {})});
      if (current(ticket)) { showCohort(result); return result; }
    } catch (error) { if (current(ticket)) cohortOutput.replaceChildren(node('p', `Save unconfirmed: ${safe(error.message)}. Open the recorded ID before retrying.`, 'notice')); }
    finally { if (current(ticket)) { cohortSaving = false; controls(); } }
  }
  cohortCreate.addEventListener('click', saveCohort);
  cohortRead.addEventListener('click', async () => {
    if (cohortReading || cohortSaving || !valid()) return;
    const ticket = capture();
    if (!/^[a-f0-9]{32}$/.test(cohortId.value)) { cohortOutput.replaceChildren(node('p', 'Enter a saved review ID.', 'notice')); return; }
    cohortReading = true; controls();
    try { const result = await request(`/api/cohorts/${cohortId.value}`, ticket); if (current(ticket)) showCohort(result); }
    catch (error) { if (current(ticket)) cohortOutput.replaceChildren(node('p', `Saved review unavailable: ${safe(error.message)}`, 'notice')); }
    finally { if (current(ticket)) { cohortReading = false; controls(); } }
  });
  async function request(path, ticket, body, method = 'POST') {
    if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
    if (body !== undefined && (!valid() || !workflowKnown(ticket.scope.workflow || record?.workflow_id)))
      throw new Error('Selected workflow understanding is not ready. No mutation submitted.');
    const controller = new AbortController(); controllers.add(controller);
    // Hosted restores can exceed 30 seconds. Only mutations retain a deadline;
    // observational reads end on response, failure, or scoped invalidation.
    const timeout = body === undefined ? null : setTimeout(() => controller.abort(), 30000);
    const headers = { Accept: 'application/json', 'X-Workbench-Request': '1' };
    if (ticket.scope.project) {
      headers['X-Workbench-Project'] = ticket.scope.project;
      headers['X-Workbench-Package'] = ticket.scope.package;
    }
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const pathname = path.split('?')[0];
      if (body === undefined && (pathname === '/api/redesigns' || /^\/api\/redesigns\/[a-f0-9]{64}$/.test(pathname))
          && window.orionHostedIntake?.read && await window.orionHostedIntake.probe()) {
        const data = await window.orionHostedIntake.read(path, headers, operation => {
          if (current(ticket) && !controller.signal.aborted)
            status.textContent = `Reading saved redesign metadata: ${safe(operation.status)}. No model invocation.`;
        }, {signal: controller.signal});
        if (!current(ticket) || controller.signal.aborted) throw new DOMException('Selection changed', 'AbortError');
        return data;
      }
      const requestMethod = body === undefined ? 'GET' : method;
      const response = await fetch(path, { method: requestMethod, headers,
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
      if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
      if (!response.ok) {
        if (path.endsWith('/native-job-draft')) {
          const failure = await response.json();
          if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
          throw new Error(typeof failure.detail === 'string' ? safe(failure.detail) : `HTTP ${response.status}. Jobs draft not confirmed.`);
        }
        if (path === '/api/proposals/workflow-artifacts') {
          const failure = await response.json();
          if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
          if (response.headers.get('X-Workbench-Diagnostic') === 'saved_analysis_incompatible') {
            compilerDiagnostic = 'Saved analysis incompatible with this reviewed compiler profile. Explicit versioned analysis review and activation are required; no automatic rebuild or pin replacement. Use agent design & code for this workflow.';
            throw new Error(compilerDiagnostic);
          }
          throw new Error(`Workflow generation failed (HTTP ${response.status}). ${typeof failure.detail === 'string' ? safe(failure.detail) : 'Invalid workflow or bronze scenario.'}`);
        }
        let conflictDetail = '';
        if (response.status === 409) {
          const failure = await response.json().catch(() => null);
          if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
          if (typeof failure?.detail === 'string') conflictDetail = safe(failure.detail);
        }
        const error = new Error(response.status === 409 && pathname.startsWith('/api/agents/runs/')
          ? `${body === undefined ? 'Run read' : 'Stop request'} ${pathname}: HTTP 409. Observation or request unconfirmed; not evidence that the worker stopped.`
          : response.status === 409
          ? `Request blocked (HTTP 409). ${conflictDetail || 'No backend detail returned. Refresh actual records before retrying.'}`
          : body === undefined && pathname === '/api/redesigns'
          ? `Could not load saved designs (HTTP ${response.status}). Select Reload saved designs to try again.`
          : `${requestMethod} ${pathname}: HTTP ${response.status}. Request not confirmed. Refresh actual records before retrying.`);
        throw error;
      }
      const data = await response.json();
      if (!current(ticket)) throw new DOMException('Selection changed', 'AbortError');
      return data;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  async function loadWorkflowScopeNote(note, value, ticket, serial, workflowName, onContext) {
    const cacheKey = JSON.stringify([ticket.scope.project, ticket.scope.package,
      value.package_hash, value.workflow_id]);
    note.textContent = 'Checking captured workflow-call evidence. No model or execution request.';
    try {
      let context = invocationContextCache.get(cacheKey);
      if (!context) {
        context = await request(`/api/workflows/${encodeURIComponent(value.workflow_id)}/migration-context?offset=0&limit=40`, ticket);
        if (context.workflow_id !== value.workflow_id || context.package_hash !== value.package_hash
            || !context.invocations || !Array.isArray(context.invocations.items))
          throw new Error('Workflow call evidence did not match the selected saved design.');
        invocationContextCache.set(cacheKey, context);
        if (invocationContextCache.size > 100)
          invocationContextCache.delete(invocationContextCache.keys().next().value);
      }
      if (!current(ticket) || serial !== detailTicket || !note.isConnected) return;
      onContext(context);
      const calls = context.invocations.items;
      const outward = calls.filter(call => call.direction === 'outward');
      const inward = calls.filter(call => call.direction === 'inward');
      const callLabel = call => {
        const names = items(call.candidate_names).filter(Boolean);
        return names.length ? names.join(', ') : call.workflow_name || 'an unresolved workflow';
      };
      const line = (label, text) => {
        const paragraph = node('p', '', 'workflow-scope-note-line');
        paragraph.append(node('strong', `${label}: `), document.createTextNode(safe(text)));
        note.append(paragraph);
      };
      note.replaceChildren();
      note.append(node('strong', 'Workflow scope recommendation', 'workflow-scope-note-title'));
      if (outward.length) {
        line('Selected Informatica workflow', workflowName);
        for (const call of outward)
          line('Command task', `${call.command_name || 'Unnamed command'} calls Informatica workflow ${callLabel(call)}${call.wait_mode === 'nowait' ? ' without waiting (-nowait)' : call.wait_mode === 'wait' ? ' with wait requested' : ', wait behavior not established'}.`);
        line('Amber task', 'The called workflow’s notebook is not implemented by this single-workflow conversion. A saved launcher draft does not implement the called workflow.');
        if (outward.some(call => call.wait_mode === 'nowait'))
          line('Call behavior', 'An asynchronous launch does not establish that the called workflow completed.');
        if (outward.some(call => call.resolution !== 'scoped_literal_candidate'))
          line('Source evidence', 'At least one called workflow is unresolved or ambiguous in this export.');
        line('Recommendation', 'Single-workflow mode is valid and converts only the selected workflow; represent the external call as a placeholder. Cohort mode is optional if you choose to review and convert related workflows together.');
      } else if (inward.length) {
        line('Selected Informatica workflow', workflowName);
        line('Called by', inward.map(call => call.caller_name || 'an unnamed workflow').join('; '));
        line('Recommendation', 'Single-workflow mode converts only this workflow. Cohort mode is optional for reviewing and selecting related workflows.');
      } else if (context.invocations.truncated) {
        line('Source evidence', 'Workflow call evidence is incomplete on this page. Review the remaining package-context pages to see additional relationships.');
        line('Recommendation', 'Single-workflow mode remains available. Cohort mode is optional.');
      } else {
        line('Source evidence', 'No direct literal workflow call was found in the returned evidence. Other launch forms or external schedulers may not be captured.');
        if ((context.cohort?.total || 0) > 1)
          line('Recommendation', `This workflow is in a connected group of ${context.cohort.total}. Single-workflow mode remains available; cohort mode lets you choose related workflows.`);
      }
      if (context.invocations.truncated && (outward.length || inward.length))
        line('More evidence', 'Additional call evidence exists on other pages.');
    } catch (error) {
      if (current(ticket) && serial === detailTicket && note.isConnected)
        note.textContent = `Workflow-call evidence is unavailable: ${safe(error.message)}. The saved design remains scoped to the selected workflow; no related workflow was added.`;
    }
  }
  function syncWorkflows() {
    const selected = agentWorkflow.value || workflow.value;
    workflow.replaceChildren(new Option('All workflows, choose one to request a redesign', ''));
    for (const option of agentWorkflow.options) {
      if (!option.value || option.disabled) continue;
      workflow.append(new Option(safe(option.textContent), option.value));
    }
    if (hasWorkflow(selected)) workflow.value = selected;
    controls();
  }
  function scheduleLoad() {
    if (loadScheduled) return;
    loadScheduled = true;
    queueMicrotask(() => {
      loadScheduled = false;
      if (!valid()) return;
      if (!page.hidden) load();
      else if (scopeActive()) loadScope();
    });
  }
  async function loadScope() {
    if (!scopeActive() || !valid()) return;
    const ticket = capture();
    scopeStatus.textContent = 'Reading the selected dependency scope. No save, model call or worker start.';
    try {
      await loadCohortContext(ticket);
      if (current(ticket)) scopeStatus.textContent = targetScope.value === 'cohort'
        ? 'Review the exact members and call boundaries below. View output tables, then explicitly save this review scope. Starting its worker is a separate action.'
        : 'Individual workflow selected. Related dependencies are not converted implicitly. No run started.';
    } catch (error) {
      if (current(ticket)) scopeStatus.textContent = `Scope review unavailable: ${safe(error.message)}. No scope saved or worker started.`;
    } finally { if (current(ticket)) controls(); }
  }
  function scoped(value, ticket) {
    return value && typeof value.id === 'string' && Number.isInteger(value.version) && value.version >= 1
      && value.scenario === ticket.scope.scenario && workflowKnown(value.workflow_id)
      && (!ticket.scope.workflow || value.workflow_id === ticket.scope.workflow
        || (targetScope.value === 'cohort' && chosenMembers.has(value.workflow_id)));
  }
  async function loadCohortContext(ticket) {
    if (targetScope.value !== 'cohort' || !ticket.scope.workflow) return;
    if (cohortContextKey === ticket.key && cohortContext) return;
    if (scopeFlight?.key === ticket.key) return scopeFlight.promise;
    const flight = {key: ticket.key, promise: null}; scopeFlight = flight; scopePending = true; controls();
    flight.promise = (async () => {
      const contract = await request(`/api/workflows/${encodeURIComponent(ticket.scope.workflow)}/migration-context?offset=0&limit=200`, ticket);
      if (!current(ticket)) return;
      if (contract.workflow_id !== ticket.scope.workflow || (ticket.scope.package && contract.package_hash !== ticket.scope.package)
          || !Array.isArray(contract.cohort?.items) || contract.cohort.has_more || contract.invocations?.has_more
          || contract.cohort.items.length !== contract.cohort.total
          || !contract.cohort.items.some(member => member.workflow_id === ticket.scope.workflow))
        throw new Error('Exact cohort membership is unavailable or exceeds this review bound. No family conversion is inferred.');
      cohortContext = contract; cohortContextKey = ticket.key; memberPlan(contract);
    })().finally(() => { if (scopeFlight === flight) { scopeFlight = null; scopePending = false; controls(); } });
    return flight.promise;
  }
  function showSavedOutputs(ticket) {
    savedOutputs.replaceChildren(node('h3', targetScope.value === 'cohort' ? 'Saved cohort designs' : 'Saved designs and code'));
    savedCodeNotes.clear();
    const members = targetScope.value === 'cohort' ? [...chosenMembers].map(id =>
      items(cohortContext?.cohort?.items).find(member => member.workflow_id === id) || {
        workflow_id: id, name: [...agentWorkflow.options].find(option => option.value === id)?.textContent})
      : [{workflow_id: ticket.scope.workflow, name: workflow.selectedOptions[0]?.textContent}];
    const unique = members.filter(member => member.workflow_id);
    savedOutputs.append(node('p', `${unique.length} workflow scope${unique.length === 1 ? '' : 's'}. Saved logical designs and generated code are separate milestones. No whole-family conversion or runtime parity is claimed.`, 'small muted'));
    for (const member of unique) {
      const card = node('details', '', 'proposal-review-card'); card.open = unique.length === 1;
      const rows = savedRows.filter(row => row.workflow_id === member.workflow_id);
      card.append(node('summary', `${member.name || member.workflow_id}: ${rows.length ? `${rows.length} saved version${rows.length === 1 ? '' : 's'}` : 'no saved design on this page'}`));
      card.append(node('p', rows.length
        ? `Showing the ${Math.min(3, rows.length)} latest version${Math.min(3, rows.length) === 1 ? '' : 's'}${rows.length > 3 ? `. ${rows.length - 3} older versions are available below.` : '.'}`
        : 'No saved design for this workflow appears on this page.', 'small muted'));
      const codeNote = node('p', rows.length ? 'Open the saved design to check its notebook code.' : 'Select a saved design to check for notebook code.', 'small muted');
      card.append(codeNote); savedCodeNotes.set(member.workflow_id, codeNote);
      const versionList = node('ul', '', 'saved-design-versions');
      versionList.setAttribute('aria-label', `Saved versions for ${member.name || member.workflow_id}`);
      card.append(versionList);
      const olderRows = rows.slice(3);
      let olderList = null;
      if (olderRows.length) {
        const older = node('details', '', 'saved-design-older');
        older.append(node('summary', `Show ${olderRows.length} older version${olderRows.length === 1 ? '' : 's'}`));
        olderList = node('ul', '', 'saved-design-versions saved-design-versions-older');
        olderList.setAttribute('aria-label', `Older saved versions for ${member.name || member.workflow_id}`);
        older.append(olderList);
        card.append(older);
      }
      for (const [index, row] of rows.entries()) {
        const origin = row.source?.kind === 'agent' ? 'agent-submitted' : 'source not confirmed as agent';
        const entry = node('li'); (index < 3 ? versionList : olderList).append(entry);
        const open = button('', '', entry); open.removeAttribute('id'); open.classList.add('saved-design-version');
        open.append(node('span', row.design?.title || member.name || member.workflow_id, 'saved-design-version-title'),
          node('span', `Version ${row.version} · ${String(row.status || 'status not reported').replaceAll('_', ' ')}${row.stale ? ' · stale' : ''} · ${origin}`, 'saved-design-version-meta'));
        open.addEventListener('click', () => {
          if (!current(ticket) || busy || loading || detailLoading) return;
          selectedCohortWorkflow = row.workflow_id; versions.value = JSON.stringify([row.id, row.version]); selectVersion();
        });
        const remove = button('', 'Delete…', entry); remove.removeAttribute('id');
        remove.classList.add('saved-design-delete');
        remove.setAttribute('aria-label', `Delete ${row.design?.title || member.name || member.workflow_id}, version ${row.version}, and its generated draft files`);
        remove.title = 'Delete this exact design version, its review records, writer job records and generated files. Parent agent history and source XML are preserved.';
        remove.addEventListener('click', async event => {
          event.stopPropagation();
          if (!current(ticket) || busy || loading || detailLoading || scopePending) return;
          const title = row.design?.title || member.name || member.workflow_id;
          const confirmed = window.confirm(
            `Delete ${title} version ${row.version} and its generated draft files?\n\n` +
            'This removes the saved design and review records, related writer jobs, notebook drafts and bundles. ' +
            'The parent agent run and imported XML package will remain.'
          );
          if (!confirmed) return;
          remove.disabled = true;
          status.textContent = `Deleting ${title} version ${row.version} and its generated drafts…`;
          try {
            const result = await request(`/api/redesigns/${encodeURIComponent(row.id)}`, ticket, {
              version: row.version,
              expected_review_version: Number.isInteger(row.review_version) ? row.review_version : 0,
              consent: true
            }, 'DELETE');
            if (!current(ticket)) return;
            if (result.id !== row.id || result.version !== row.version || result.deleted !== true
                || result.parent_agent_history_preserved !== true || result.source_package_preserved !== true)
              throw new Error('Delete receipt scope mismatch. Refresh saved designs and inspect the exact version.');
            if (record?.id === row.id && record?.version === row.version) {
              record = null; latestVersion = null; detail.replaceChildren(); versions.value = '';
            }
            status.textContent = result.cleanup_pending
              ? `Design v${row.version} was removed, but some generated files remain in backend cleanup quarantine. Contact the workspace owner.`
              : `Design v${row.version} deleted with ${result.writer_jobs_removed} writer record(s) and ${result.artifact_runs_removed} generated draft run(s). Parent agent history and source XML are preserved.`;
            await load(true);
          } catch (error) {
            if (current(ticket)) status.textContent = `Design deletion not confirmed: ${safe(error.message)}. Refresh actual records before retrying.`;
          } finally {
            if (current(ticket)) { remove.disabled = false; controls(); }
          }
        });
      }
      savedOutputs.append(card);
    }
    if (targetScope.value === 'cohort' && cohortContext) {
      const hierarchy = disclosure('Caller / child hierarchy and async launch semantics', node('div'));
      for (const call of items(cohortContext.invocations?.items)) hierarchy.lastElementChild.append(node('p',
        `${call.caller_name} → ${items(call.candidate_names).join(', ') || 'unresolved child'} · ${call.wait_mode || 'unspecified'} · ${call.completion || 'completion unverified'}`, 'small muted'));
      savedOutputs.append(hierarchy);
    }
  }
  async function loadWorkflowUnderstanding(ticket = capture()) {
    if (!sourceDetails.open || understandingKey === ticket.key) return;
    understandingKey = ticket.key;
    const serial = ++understandingTicket, workflowId = ticket.scope.workflow;
    understanding.replaceChildren();
    if (!workflowId || !workflowKnown(workflowId)) {
      understanding.append(node('h3', 'Captured Informatica workflow understanding'),
        node('p', 'Select a workflow to inspect deterministic XML evidence. This view does not depend on an agent run.', 'small muted'));
      return;
    }
    understanding.append(node('h3', 'Captured Informatica workflow understanding'),
      node('p', 'Deterministic interpretation of the imported XML. This explains captured structure and settings, not live production behavior or semantic parity.', 'small muted'),
      node('p', 'Loading workflow evidence…', 'small muted'));
    understandingPending = true;
    try {
      const data = await request(`/api/object/${encodeURIComponent(workflowId)}`, ticket);
      if (!current(ticket) || serial !== understandingTicket || workflowId !== workflow.value) return;
      const taskCount = data.behavior_explanation?.coverage?.task_count;
      capturedTaskCount = Number.isInteger(taskCount) && taskCount >= 0 ? taskCount : null; controls();
      understanding.replaceChildren(node('h3', 'Captured Informatica workflow understanding'),
        node('p', `${data.node?.name ?? workflowId} · ${capturedTaskCount ?? 'Unknown'} task instances · ${data.behavior_explanation?.coverage?.control_link_count ?? 'Unknown'} control links · ${data.behavior_explanation?.semantic_parity ?? 'unverified'}`, 'disclosure'),
        node('p', 'Source: canonical XML evidence. Exported enabled state and on-demand settings do not prove current production activity.', 'small muted'),
        node('p', 'XML evidence and live Unity Catalog observations are separate. The metadata check below reads selected-workflow direct source fixtures only, not called children. Verified fixtures are not automatically approved production bindings.', 'small muted'));
      const packageContext = node('section', '', 'proposal-review-section');
      packageContext.id = 'proposal-package-context'; packageContext.setAttribute('aria-live', 'polite');
      understanding.append(packageContext);
      async function loadContext(offset = 0) {
        packageContext.replaceChildren(node('p', 'Loading package invocation context. No child data or model calls.', 'small muted'));
        try {
          const contract = await request(`/api/workflows/${encodeURIComponent(workflowId)}/migration-context?offset=${offset}`, ticket);
          if (!current(ticket) || serial !== understandingTicket || !packageContext.isConnected) return;
          window.renderPackageMigrationContext(packageContext, contract, loadContext);
        } catch (error) {
          if (current(ticket) && serial === understandingTicket && packageContext.isConnected)
            packageContext.replaceChildren(node('p', `Package context unavailable: ${error.message}. Closure and risk remain unassessed.`, 'small muted'));
        }
      }
      loadContext();
      const fixturePanel = node('section', '', 'proposal-review-section');
      fixturePanel.append(node('h3', 'Live UC source fixture metadata'));
      const catalogLabel = node('label', 'Fixture catalog'), catalog = node('input');
      catalog.type = 'text'; catalog.value = 'infotodbxmig_src_dev_ssuman'; catalog.maxLength = 255;
      catalogLabel.append(catalog); fixturePanel.append(catalogLabel);
      const check = node('button', 'Check existing UC source fixtures', 'secondary'); check.type = 'button';
      const observation = node('div'); observation.setAttribute('aria-live', 'polite');
      observation.append(node('p', 'Not checked in this selection yet. No table absence or binding approval is inferred.', 'small muted'));
      check.addEventListener('click', async () => {
        check.disabled = true;
        observation.replaceChildren(node('p', 'Reading current-user UC metadata. No table data reads or writes.', 'small muted'));
        try {
          const result = await request('/api/workflows/fixture-metadata', ticket, { workflow_id: workflowId, catalog: catalog.value.trim() });
          if (!current(ticket) || serial !== understandingTicket || !fixturePanel.isConnected) return;
          observation.replaceChildren(node('p', `Observed ${result.observed_at}. Fixture verification does not approve runtime bindings.`, 'small muted'));
          for (const source of items(result.sources)) {
            const card = node('article', '', 'proposal-review-card');
            card.append(node('h4', source.actual_full_name || source.expected_full_name || source.source_id), readableFields({
              status: String(source.status).replaceAll('_', ' '), table_exists: source.exists === null ? 'Unknown' : source.exists,
              schema_exists: source.schema_exists === null ? 'Unknown' : source.schema_exists,
              production_binding: 'Not approved', error: source.error
            }));
            const evidence = node('details'); evidence.append(node('summary', 'Full metadata and mapping evidence'), readableFields(source));
            card.append(evidence); observation.append(card);
          }
          if (!items(result.sources).length) observation.append(node('p', result.binding_status === 'no_direct_sources'
            ? 'No direct sources captured. Called child sources are separate scopes. See Convert together, then select a child explicitly for a bounded metadata read. No fixture existence inferred.'
            : 'No resolved sources; unresolved definitions need review. Called child sources are separate scopes. No fixture existence inferred.', 'small muted'));
          if (result.reference_diagnostics?.summary) {
            const summary = result.reference_diagnostics.summary;
            observation.append(node('p', `${summary.source_definition_blockers} source-definition blockers; ${summary.unresolved_total} total canonical reference gaps. Fixture data cannot resolve XML references. Empty fixtures are not successful source-data validation.`, 'small muted'));
          }
          const details = node('details'); details.append(node('summary', 'Observation identity and unresolved targets'), readableFields(result)); observation.append(details);
        } catch (error) {
          if (current(ticket) && serial === understandingTicket && fixturePanel.isConnected)
            observation.replaceChildren(node('p', `Metadata check failed: ${error.message}. Existence remains unknown, not missing.`, 'notice'));
        } finally { if (fixturePanel.isConnected) check.disabled = false; }
      });
      fixturePanel.append(check, observation); understanding.append(fixturePanel);
      const explanation = data.behavior_explanation;
      if (!explanation || explanation.basis !== 'canonical_xml_evidence') {
        understanding.append(node('p', 'The deterministic workflow explanation is unavailable for this object.', 'notice'));
        return;
      }
      for (const item of items(explanation.sections)) section(item.title, item.items, understanding);
      const technicalEvidence = node('details');
      technicalEvidence.append(node('summary', `Technical XML references (${items(explanation.evidence_ids).length})`));
      const evidenceList = node('ul');
      for (const id of items(explanation.evidence_ids)) evidenceList.append(node('li', id));
      technicalEvidence.append(evidenceList); understanding.append(technicalEvidence);
      section('Scope and unknowns', explanation.coverage ? [explanation.coverage] : [], understanding);
    } catch (error) {
      if (current(ticket) && serial === understandingTicket)
        understanding.replaceChildren(node('h3', 'Captured Informatica workflow understanding'),
          node('p', `Workflow evidence unavailable. ${safe(error.message)}`, 'notice'));
    } finally { if (current(ticket) && serial === understandingTicket) understandingPending = false; }
  }
  function load(force = false) {
    if (page.hidden || !valid()) return Promise.resolve();
    if (!force && loaded && contextKey === key()) return Promise.resolve();
    if (listFlight?.key === key()) return listFlight.promise;
    const flight = { key: key(), promise: null };
    listFlight = flight;
    flight.promise = loadRecords().finally(() => { if (listFlight === flight) listFlight = null; });
    return flight.promise;
  }
  async function loadRecords() {
    const ticket = capture(), serial = ++listTicket; ++detailTicket;
    const previousChoice = versions.value;
    loadWorkflowUnderstanding(ticket);
    loading = true; controls(); status.textContent = loaded ? 'Checking latest saved state. Your saved design remains visible.' : 'Reading actual agent-submitted designs...';
    try {
      if (!valid()) throw new Error('Choose a ready package member first.');
      await loadCohortContext(ticket);
      if (!current(ticket) || serial !== listTicket) return;
      const query = new URLSearchParams({ scenario: ticket.scope.scenario, offset: String(offset), limit: '50' });
      if (ticket.scope.workflow && targetScope.value !== 'cohort') query.set('workflow_id', ticket.scope.workflow);
      const data = await request(`/api/redesigns?${query}`, ticket);
      if (!current(ticket) || serial !== listTicket) return;
      const rows = items(data.items).filter(value => scoped(value, ticket));
      savedRows = rows; showSavedOutputs(ticket);
      versions.replaceChildren(new Option('Choose a stored design version', ''));
      total = Number.isInteger(data.total) ? data.total : rows.length; loaded = true;
      for (const value of rows) versions.append(new Option(safe(`${value.design?.title} | ${value.workflow_id} | v${value.version} | ${value.status}${value.stale ? ' | stale' : ''}`), JSON.stringify([value.id, value.version])));
      status.textContent = rows.length
        ? `${total} stored versions. Showing ${offset + 1} to ${offset + rows.length}${data.truncated ? '; additional versions are paged' : ''}. Reads do not invoke agents.`
        : 'No saved designs on this page. Open Agent activity to start a conversion, or use the page controls to check other saved versions.';
      if (submittedLink?.key === ticket.key) {
        const selected = JSON.stringify([submittedLink.id, submittedLink.version]);
        if (![...versions.options].some(option => option.value === selected))
          versions.append(new Option(`Submitted version v${submittedLink.version} (verify saved record)`, selected));
        versions.value = selected;
        await selectVersion();
      } else if (rows.length) {
        versions.value = previousChoice && [...versions.options].some(option => option.value === previousChoice) ? previousChoice : versions.options[1].value;
        await selectVersion();
      } else { record = null; latestVersion = null; detail.replaceChildren(); }
    } catch (error) {
      if (current(ticket) && serial === listTicket) status.textContent = (error.name === 'AbortError'
        ? 'Read interrupted or timed out. Refresh to read actual state.' : safe(error.message))
        + ' Previously listed choices are unconfirmed. Approval is unavailable until an exact saved record is read.';
    } finally {
      if (serial === listTicket) {
        loading = false;
        if (current(ticket)) { controls(); loadSavedDrafts(); }
        else { invalidate('Workflow scope restored. Reading its saved designs.'); scheduleLoad(); }
      }
    }
  }
  let versionFlight = null;
  function selectVersion() {
    const selectedKey = JSON.stringify([epoch, key(), versions.value]);
    if (versionFlight?.key === selectedKey) return versionFlight.promise;
    const flight = {key: selectedKey, promise: null}; versionFlight = flight;
    flight.promise = readVersion().finally(() => { if (versionFlight === flight) versionFlight = null; });
    return flight.promise;
  }
  async function readVersion() {
    const ticket = capture(), serial = ++detailTicket, selected = versions.value;
    record = null; latestVersion = null; detailLoading = true; controls();
    if (!selected) { detailLoading = false; detail.replaceChildren(); controls(); return; }
    status.textContent = 'Reading stored design and current latest version...';
    try {
      const [id, version] = JSON.parse(selected), base = `/api/redesigns/${encodeURIComponent(id)}`;
      const value = await request(`${base}?version=${version}`, ticket);
      const latest = Number.isInteger(value.latest_version) && value.latest_version >= version
        ? {...value, version: value.latest_version} : await request(base, ticket);
      if (!current(ticket) || serial !== detailTicket || versions.value !== selected) return;
      if (!scoped(value, ticket) || !scoped(latest, ticket) || value.id !== id || value.version !== version || latest.id !== id)
        throw new Error('Service returned a mismatched design scope. Refresh before reviewing.');
      if (submittedLink?.key === ticket.key && (value.package_hash !== submittedLink.package_hash
          || value.source?.record_id !== submittedLink.record_id || value.id !== submittedLink.id
          || value.version !== submittedLink.version))
        throw new Error('Saved design does not match the selected run submission. No alternate design was opened.');
      record = value; latestVersion = latest.version; render(value, ticket, serial);
      status.textContent = `Stored v${value.version}, latest v${latest.version}. ${value.stale ? 'The design input snapshot changed; this can include agent guidance, not just source data.' : 'Current input reported by backend.'} Draft generation does not authorize deployment.`;
    } catch (error) {
      if (current(ticket) && serial === detailTicket) status.textContent = error.name === 'AbortError'
        ? 'Read interrupted. Refresh actual records.' : safe(error.message);
    } finally {
      if (current(ticket) && serial === detailTicket) {
        detailLoading = false; controls(); loadSavedDrafts();
      }
    }
  }
  function section(title, values, parent = detail) {
    const block = node('section', '', 'proposal-review-section'); block.append(node('h3', title));
    if (!items(values).length) block.append(node('p', 'None recorded.', 'small muted'));
    else {
      const list = node('div', '', 'proposal-review-items');
      for (const value of values) {
        if (typeof value === 'string') {
          list.append(node('p', value, 'proposal-review-text'));
        } else if (value && typeof value === 'object') {
          const card = node('article', '', 'proposal-review-card');
          const titleKey = ['label', 'name', 'original_task_id', 'id'].find(key => value[key]);
          if (titleKey) card.append(node('h4', value[titleKey]));
          const fields = Object.fromEntries(Object.entries(value).filter(([key]) => key !== titleKey));
          card.append(readableFields(fields)); list.append(card);
        }
      }
      block.append(list);
    }
    parent.append(block);
  }
  function readableFields(value) {
    const labels = {evidence_ids: 'Source evidence', implementation_kind: 'Databricks task type',
      purpose: 'What this task does', proposed_task_ids: 'Replacement tasks', original_task_id: 'Original task',
      run_if: 'Run when', source: 'From task', target: 'To task', condition: 'Branch condition',
      outcome: 'Branch outcome', relative_path: 'Artifact path', value_type: 'Value type',
      created_at: 'Recorded at', expected_kinds: 'Expected object types'};
    const dl = node('dl', '', 'proposal-review-fields');
    for (const [key, item] of Object.entries(value)) {
      if (item === undefined || item === null) continue;
      dl.append(node('dt', labels[key] || key.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase())));
      const dd = node('dd');
      if (Array.isArray(item)) {
        if (!item.length) dd.append(node('span', 'None recorded', 'small muted'));
        else {
          const ul = node('ul', '', 'proposal-review-list');
          item.forEach(entry => {
            const li = node('li');
            if (entry && typeof entry === 'object') {
              const content = readableFields(entry);
              if (content.children.length) li.append(content);
              else li.textContent = 'No details recorded';
            } else li.textContent = entry === '' || entry === null || entry === undefined ? 'Not recorded' : safe(entry);
            ul.append(li);
          }); dd.append(ul);
        }
      } else if (item && typeof item === 'object') dd.append(readableFields(item));
      else dd.textContent = safe(typeof item === 'boolean' ? (item ? 'Yes' : 'No') : item);
      dl.append(dd);
    }
    return dl;
  }
  // Presentation adapter for app.js's generic rows/list renderer. Only whole
  // JSON objects/arrays in ordinary value slots qualify. Never touch controls,
  // source previews, technical disclosures, or the underlying application state.
  function presentObjectValues(root) {
    if (!(root instanceof Element)) return;
    const selector = '.detail-value, li, td, p.prose';
    const values = [...(root.matches(selector) ? [root] : []), ...root.querySelectorAll(selector)];
    for (const element of values) {
      if (!element.closest('main') || element.closest('details:not(#node-inspector), code, .json-block, .proposal-review-fields')
          || element.querySelector('button, input, select, textarea, a, details, dl, ul, li, pre, code')) continue;
      const text = element.textContent.trim();
      if (!text.startsWith('{') && !text.startsWith('[')) continue;
      let value;
      try { value = JSON.parse(text); } catch (_) { continue; }
      if (!value || typeof value !== 'object') continue;
      const content = Array.isArray(value) ? readableFields({ items: value }) : readableFields(value);
      if (!content.children.length) content.append(node('dd', 'None recorded', 'small muted'));
      if (element.matches('pre.detail-value')) {
        const replacement = node('div', '', element.className);
        replacement.append(content); element.replaceWith(replacement);
      } else element.replaceChildren(content);
    }
  }
  const presentationRoot = document.querySelector('main');
  if (presentationRoot) {
    presentObjectValues(presentationRoot);
    new MutationObserver(changes => {
      for (const change of changes) {
        if (change.type === 'characterData') presentObjectValues(change.target.parentElement);
        else {
          // A generic value can receive its text after it has been mounted.
          if (change.target instanceof Element && change.target.matches('.detail-value, li, td, p.prose'))
            presentObjectValues(change.target);
          for (const added of change.addedNodes) if (added instanceof Element) presentObjectValues(added);
        }
      }
    }).observe(presentationRoot, { childList: true, subtree: true, characterData: true });
  }
  function json(title, value, parent = detail) {
    const block = node('details'); block.append(node('summary', title), node('pre', JSON.stringify(value, null, 2), 'json-block')); parent.append(block);
  }
  function humanizeEvidence(value, references) {
    if (typeof value === 'string') {
      if (!/^[a-f0-9]{64}$/.test(value)) return value;
      const ref = references.get(value);
      return ref ? `${ref.label} (${ref.kind})` : 'Captured XML evidence (technical reference hidden)';
    }
    if (Array.isArray(value)) return value.map(item => humanizeEvidence(item, references));
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, humanizeEvidence(item, references)]));
  }
  function yamlLines(value, depth = 0, prefix = '') {
    const pad = '  '.repeat(depth);
    const scalar = item => item === null ? 'null' : typeof item === 'string' ? JSON.stringify(item)
      : typeof item === 'number' || typeof item === 'boolean' ? String(item) : JSON.stringify(item);
    if (Array.isArray(value)) return value.length
      ? value.flatMap(item => item && typeof item === 'object'
        ? yamlLines(item, depth, '- ') : [`${pad}${prefix}- ${scalar(item)}`])
      : [`${pad}${prefix}[]`];
    if (value && typeof value === 'object') {
      const entries = Object.entries(value);
      if (!entries.length) return [`${pad}${prefix}{}`];
      return entries.flatMap(([key, item], index) => {
        const safeKey = /^[A-Za-z_][A-Za-z0-9_-]*$/.test(key) ? key : JSON.stringify(key);
        const entryDepth = index > 0 && prefix.startsWith('- ') ? depth + 1 : depth;
        const entryPad = '  '.repeat(entryDepth);
        const lead = index === 0 ? prefix : '';
        if (item && typeof item === 'object' && !Array.isArray(item))
          return [`${entryPad}${lead}${safeKey}:`, ...yamlLines(item, entryDepth + 1)];
        if (Array.isArray(item) && item.length)
          return [`${entryPad}${lead}${safeKey}:`, ...yamlLines(item, entryDepth + 1)];
        if (Array.isArray(item)) return [`${entryPad}${lead}${safeKey}: []`];
        return [`${entryPad}${lead}${safeKey}: ${scalar(item)}`];
      });
    }
    return [`${pad}${prefix}${scalar(value)}`];
  }
  function renderProposalYaml(value) {
    const design = value.design;
    const proposal = {
      apiVersion: 'orion.databricks.com/v1alpha1',
      kind: 'LakeflowJobProposal',
      metadata: { workflow_id: value.workflow_id, scenario: value.scenario, design_id: value.id,
        design_version: value.version, package_hash: value.package_hash },
      spec: { title: design.title, rationale: design.rationale, tasks: items(design.tasks),
        dependencies: items(design.edges), coverage: items(design.coverage), parameters: items(design.parameters),
        cadence: design.cadence ?? null, artifact_plan: items(design.artifact_plan),
        scoped_unresolved: items(design.scoped_unresolved), alternatives: items(design.alternatives),
        blockers: items(design.blockers) },
      review_state: { status: value.status, stale: value.stale, input_version: value.input_version,
        current_input_version: value.current_input_version, deployable: false,
        note: 'Logical proposal serialization only. Not a Databricks Asset Bundle or deployment-ready job.' }
    };
    return yamlLines(proposal).join('\n') + '\n';
  }
  function taskTable(design) {
    const tasks = items(design.tasks), coverage = items(design.coverage);
    const table = node('table'); table.className = 'proposal-task-table';
    const head = node('thead'), header = node('tr');
    for (const label of ['Proposed task', 'Implementation', 'Original task coverage', 'Purpose']) header.append(node('th', label));
    head.append(header); table.append(head);
    const body = node('tbody');
    for (const task of tasks) {
      const row = node('tr');
      const sourceTasks = coverage.filter(item => items(item.proposed_task_ids).includes(task.id))
        .map(item => item.original_task_id);
      for (const value of [task.label, task.implementation_kind, sourceTasks.join(', ') || 'Not mapped', task.purpose])
        row.append(node('td', value));
      body.append(row);
    }
    table.append(body); return table;
  }
  function showArtifactPreviews(artifacts, parent, { portableText = false, active = () => true, sectionTitle = null, runCandidate = null } = {}) {
    const paths = Object.keys(artifacts).sort();
    if (!paths.length) return;
    const heading = node('h4', sectionTitle || (portableText ? 'Generated files (not executed or persisted)' : 'Saved generated files (not executed)'));
    parent.append(heading);
    const toolbar = node('div', '', 'tabs review-code-tabs'); toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Artifact file types'); parent.append(toolbar);
    const blocks = [];
    const categories = [['All files', null], ['Notebooks', /\.ipynb$/], ['Python', /\.py$/],
      ['YAML', /\.ya?ml$/], ['SQL & other', /\.(sql|json|toml|ini|cfg|txt|md)$/]]
      .filter(([label, pattern]) => pattern === null || paths.some(path => pattern.test(path)));
    for (const [label, pattern] of categories) {
      const count = pattern ? paths.filter(path => pattern.test(path)).length : paths.length;
      const filter = button('', `${label} (${count})`, toolbar); filter.removeAttribute('id'); filter.setAttribute('aria-pressed', String(pattern === null));
      filter.addEventListener('click', () => {
        for (const {path, element} of blocks) element.hidden = !!pattern && !pattern.test(path);
        for (const child of toolbar.children) child.setAttribute('aria-pressed', String(child === filter));
      });
    }
    for (const [path, content] of Object.entries(artifacts).sort(([a], [b]) => a.localeCompare(b))) {
      if (typeof content !== 'string') continue;
      const destination = parent;
      parent = node('article', '', 'review-code-file'); destination.append(parent); blocks.push({path, element: parent});
      // The deterministic compiler redacts canonical attributes before emission.
      // Preserve its text exactly, including symbolic password assignments and
      // notebook JSON metadata. Existing model drafts retain their egress guard.
      if (!portableText && safe(content) !== content) {
        parent.append(node('p', `Code preview withheld for ${path}: credential-shaped content requires correction at source.`, 'notice'));
        parent = destination;
        continue;
      }
      const originalText = (text, language) => codePreview(text, language, {portableText});
      if (portableText) {
        const save = button('', `Download ${path}`, parent); save.removeAttribute('id');
        save.addEventListener('click', () => {
          if (!active()) return;
          const url = runCandidate ? savedArtifactUrl(runCandidate, path, content)
            : URL.createObjectURL(new Blob([content], { type: path.endsWith('.ipynb') || path.endsWith('.json') ? 'application/json' : 'text/plain;charset=utf-8' }));
          triggerDownload(url, path.split('/').pop());
        });
      }
      if (!portableText) {
        const save = button('', `Download ${path}`, parent); save.removeAttribute('id');
        save.addEventListener('click', () => {
          if (!active()) return;
          const url = runCandidate ? savedArtifactUrl(runCandidate, path, content)
            : URL.createObjectURL(new Blob([content], {type: 'text/plain;charset=utf-8'}));
          triggerDownload(url, path.split('/').pop());
        });
      }
      if (path.endsWith('.ipynb')) {
        try {
          const notebook = JSON.parse(content);
          if (!Array.isArray(notebook.cells)) throw new Error('Invalid notebook cells');
          const showStagedSource = /draft/i.test(sectionTitle || '');
          const details = node('details', '', 'notebook-document'); details.open = showStagedSource;
          details.append(node('summary', `Notebook: ${path}`));
          notebook.cells.forEach((cell, index) => {
            const language = safe(cellLanguage(cell, notebook));
            const source = Array.isArray(cell.source) ? cell.source.join('') : String(cell.source ?? '');
            const block = node('details', '', `notebook-cell notebook-cell-${cell.cell_type === 'code' ? language : 'markdown'}`);
            block.open = showStagedSource && cell.cell_type === 'code';
            block.append(node('summary', `Notebook cell ${index + 1} (${language})`), originalText(source, language));
            if (runCandidate && cell.cell_type === 'code'
                && ['t_wait.ipynb', 't_counter.ipynb', 't_child_launch.ipynb', 't_email.ipynb'].includes(path.split('/').pop())) {
              attachCellRunner({block, path, notebook, cellNumber: index + 1, runCandidate, active});
            }
            details.append(block);
          });
          parent.append(details);
        } catch (_) { parent.append(node('p', `Notebook preview unavailable for ${path}; download the original artifact JSON.`, 'notice')); }
      } else {
        const details = node('details'); details.append(node('summary', `Source: ${path}`), originalText(content, artifactLanguage(path))); parent.append(details);
      }
      parent = destination;
    }
  }
  function attachPortalNotebook(parent, path, runCandidate) {
    const actions = node('div', '', 'notebook-review-actions');
    actions.setAttribute('role', 'group'); actions.setAttribute('aria-label', 'Notebook actions');
    const download = parent.lastElementChild;
    if (download?.tagName === 'BUTTON' && download.textContent.startsWith('Download ')) actions.append(download);
    const control = button('', 'Open in Databricks', actions); control.removeAttribute('id');
    const previewButton = button('', 'Preview returned edits', actions); previewButton.removeAttribute('id'); previewButton.hidden = true;
    const saveButton = button('', 'Save returned edits and delete temporary notebook', actions); saveButton.removeAttribute('id'); saveButton.hidden = true;
    const editDiff = node('pre', '', 'notebook-edit-diff'); editDiff.hidden = true;
    let attachment = null, preview = null, saved = false;
    const status = node('p', 'Run individual cells in the native Databricks editor on Serverless. Opening attaches this exact saved notebook; it does not run code.', 'small muted');
    const progress = node('progress'); progress.hidden = true; progress.setAttribute('aria-label', 'Attaching notebook to workspace');
    const link = node('a', 'Open attached notebook'); link.hidden = true; link.target = '_blank'; link.rel = 'noopener noreferrer';
    status.classList.add('notebook-attachment-note');
    parent.append(actions, progress, status, link, editDiff);
    previewButton.addEventListener('click', async () => {
      if (!attachment || !current(runCandidate.ticket) || page.hidden) return;
      previewButton.disabled = true; saveButton.hidden = true; progress.hidden = false;
      try {
        preview = await request(`/api/jobs/${encodeURIComponent(runCandidate.jobId)}/spark-drafts/edit-preview`, runCandidate.ticket,
          {consent: true, session_id: attachment.session_id, expected_workspace: attachment.workspace});
        if (!current(runCandidate.ticket) || page.hidden) return;
        if (preview.job_id !== runCandidate.jobId || preview.session_id !== attachment.session_id || preview.artifact_path !== path
            || !/^[a-f0-9]{64}$/.test(preview.remote_sha256)) throw new Error('Edit preview scope mismatch');
        editDiff.textContent = preview.diff || 'No cell-source changes. Execution outputs will not be imported.';
        editDiff.hidden = false; status.textContent = preview.message; saveButton.hidden = false; saved = false;
      } catch (error) { if (current(runCandidate.ticket)) status.textContent = `Edits not imported: ${safe(error.message)}. The temporary notebook is retained.`; }
      finally { if (current(runCandidate.ticket)) { previewButton.disabled = false; progress.hidden = true; } }
    });
    saveButton.addEventListener('click', async () => {
      if (!preview || !attachment || !current(runCandidate.ticket) || page.hidden) return;
      saveButton.disabled = true; previewButton.disabled = true; progress.hidden = false;
      const body = {consent: true, session_id: attachment.session_id, expected_workspace: attachment.workspace,
        expected_remote_sha256: preview.remote_sha256};
      try {
        if (!saved) {
          const receipt = await request(`/api/jobs/${encodeURIComponent(runCandidate.jobId)}/spark-drafts/edit-return`, runCandidate.ticket, body);
          if (!current(runCandidate.ticket) || page.hidden) return;
          if (receipt.saved !== true || receipt.session_id !== attachment.session_id || receipt.remote_sha256 !== preview.remote_sha256)
            throw new Error('Returned notebook save was not confirmed');
          saved = true;
        }
        status.textContent = 'Returned edits saved. Verifying the saved revision before removing the temporary Databricks notebook…';
        const cleanup = await request(`/api/jobs/${encodeURIComponent(runCandidate.jobId)}/spark-drafts/edit-cleanup`, runCandidate.ticket, body);
        if (!current(runCandidate.ticket) || page.hidden) return;
        if (cleanup.remote_deleted !== true || cleanup.session_id !== attachment.session_id) throw new Error('Temporary notebook cleanup is unconfirmed');
        status.textContent = cleanup.message; link.hidden = true; saveButton.hidden = true; previewButton.hidden = true;
        attachment = null; preview = null; editDiff.hidden = true;
        loadSavedDrafts();
        refresh.click();
      } catch (error) {
        if (current(runCandidate.ticket)) {
          status.textContent = `${saved ? 'Edits saved, cleanup incomplete' : 'Save not confirmed'}: ${safe(error.message)}. No original generated file was overwritten.`;
          if (saved) saveButton.textContent = 'Retry temporary notebook cleanup';
        }
      } finally { if (current(runCandidate.ticket)) { saveButton.disabled = false; previewButton.disabled = false; progress.hidden = true; } }
    });
    control.addEventListener('click', async () => {
      if (!current(runCandidate.ticket) || page.hidden) return;
      control.disabled = true; progress.hidden = false; status.textContent = 'Checking selected workspace…';
      try {
        const workspace = await request('/api/models/workspace', runCandidate.ticket);
        if (!current(runCandidate.ticket) || page.hidden) return;
        status.textContent = 'Uploading the saved notebook and confirming its workspace location…';
        const result = await request(`/api/jobs/${encodeURIComponent(runCandidate.jobId)}/spark-drafts/attach`, runCandidate.ticket,
          {consent: true, artifact_path: path, expected_workspace: workspace.workspace});
        if (!current(runCandidate.ticket) || page.hidden) return;
        const url = new URL(result.notebook_url), host = new URL(workspace.workspace);
        if (result.job_id !== runCandidate.jobId || result.artifact_path !== path || result.status !== 'attached'
            || url.origin !== host.origin || url.protocol !== 'https:' || result.execution_started !== false)
          throw new Error('Notebook attachment response did not match this workspace and file');
        link.href = url.href; link.hidden = false; status.textContent = result.message;
        if (/^[a-f0-9]{32}$/.test(result.session_id)) {
          attachment = result; previewButton.hidden = false; saveButton.hidden = true; preview = null;
          if (result.saved === true && /^[a-f0-9]{64}$/.test(result.remote_sha256)) {
            saved = true; preview = {remote_sha256: result.remote_sha256};
            saveButton.textContent = 'Retry temporary notebook cleanup'; saveButton.hidden = false;
          }
        }
        link.click();
      } catch (error) { if (current(runCandidate.ticket)) status.textContent = `Attachment not confirmed: ${safe(error.message)}. No code was run.`; }
      finally { if (current(runCandidate.ticket)) { control.disabled = false; progress.hidden = true; } }
    });
  }
  function attachCellRunner() { /* Portal mode: no prefix-Job controls. */ }
  function legacyPrefixJobRunner({block, path, notebook, cellNumber, runCandidate, active}) {
    const panel = node('section', '', 'notebook-cell-runner');
    const runButton = button('', `Run through cell ${cellNumber} on serverless`, panel); runButton.removeAttribute('id');
    const outputWindow = node('section', '', 'notebook-cell-output-window');
    outputWindow.hidden = true;
    outputWindow.setAttribute('aria-label', `Output for notebook through cell ${cellNumber}`);
    const outputHeading = node('h5', `OUTPUT · THROUGH CELL ${cellNumber}`);
    const statusLine = node('p', 'Not run yet.', 'notebook-run-status');
    statusLine.setAttribute('role', 'status'); statusLine.setAttribute('aria-live', 'polite');
    const runLink = node('a', 'Open Databricks run'); runLink.hidden = true;
    runLink.target = '_blank'; runLink.rel = 'noopener noreferrer';
    const resultOutput = node('pre', 'Output will appear here after this prefix finishes.', 'notebook-run-output');
    const logsOutput = node('details', '', 'notebook-run-logs');
    logsOutput.append(node('summary', 'Driver logs'), node('pre', '', 'notebook-run-output'));
    const errorOutput = node('pre', '', 'notebook-run-error'); errorOutput.hidden = true;
    const setup = node('div', '', 'notebook-run-setup');
    const fields = node('div', '', 'notebook-run-parameters');
    const effects = node('p', '', 'notebook-run-effects');
    const confirmation = node('label', 'I confirm these parameters and effects. Run the notebook through this cell.', 'notebook-run-confirm');
    const confirmInput = node('input'); confirmInput.type = 'checkbox'; confirmation.prepend(confirmInput);
    const actions = node('div', '', 'button-row');
    const submit = button('', `Run through cell ${cellNumber}`, actions); submit.removeAttribute('id'); submit.disabled = true;
    const cancel = button('', 'Cancel setup', actions); cancel.removeAttribute('id');
    const stop = button('', 'Stop run', actions); stop.removeAttribute('id'); stop.hidden = true;
    const setupStatus = node('p', '', 'notebook-run-status');
    setupStatus.setAttribute('role', 'status'); setupStatus.setAttribute('aria-live', 'polite');
    setup.append(setupStatus, fields, effects, confirmation, actions);
    outputWindow.append(outputHeading, statusLine, runLink, resultOutput, logsOutput, errorOutput, setup);
    panel.append(outputWindow); block.append(panel);

    let controls = new Map(), workspaceHost = '', stopRequested = false;
    const widgetDefaults = prefixCells => {
      const source = prefixCells.filter(cell => cell.cell_type === 'code')
        .map(cell => Array.isArray(cell.source) ? cell.source.join('') : String(cell.source ?? '')).join('\n');
      const pattern = /(?:dbutils\.widgets\.get|_get_widget)\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["'](?:\s*,\s*["']([^"']*)["'])?/g;
      const values = new Map();
      for (const match of source.matchAll(pattern)) if (!values.has(match[1])) values.set(match[1], match[2] ?? '');
      return {source, values};
    };
    const defaults = {demo_mode: 'true', wait_seconds: '0', ae2e_schema: 'orion_demo_ae2e',
      dev_schema: 'orion_demo_ae2e', dev_table: 'AE2E_JOB_DEP_CHK_COUNT_DEV'};
    const formParameters = () => Object.fromEntries([...controls].map(([key, control]) => [key, control.value.trim()]));
    const fillCatalogDefaults = () => {
      if (!controls.has('uc_catalog')) return;
      const catalog = controls.get('uc_catalog').value.trim();
      const schema = controls.get('ae2e_schema')?.value.trim() || 'orion_demo_ae2e';
      const devCatalog = controls.get('dev_catalog'), devSchema = controls.get('dev_schema');
      if (devCatalog?.dataset.autoDefault === 'true') devCatalog.value = catalog;
      if (devSchema?.dataset.autoDefault === 'true') devSchema.value = schema;
      const source = controls.get('bronze_source_table');
      if (source?.dataset.autoDefault === 'true') source.value = catalog ? `${catalog}.${schema}.AE2E_JOB_DEP_CHK_COUNT` : '';
      const output = controls.get('target_table');
      if (output?.dataset.autoDefault === 'true' && devCatalog?.value && devSchema?.value)
        output.value = `${devCatalog.value.trim()}.${devSchema.value.trim()}.${controls.get('dev_table')?.value.trim() || 'AE2E_JOB_DEP_CHK_COUNT_DEV'}`;
    };
    function renderParameterFields(prefixCells, catalogs = [], defaultCatalog = null) {
      fields.replaceChildren(); controls = new Map();
      const {source, values} = widgetDefaults(prefixCells);
      for (const [name, capturedDefault] of values) {
        const group = node('div', '', 'notebook-run-field');
        const label = node('label', notebookParameterLabel(name));
        label.htmlFor = `notebook-run-${runCandidate.receiptId}-c${cellNumber}-${name}`;
        let control;
        if (name === 'uc_catalog') {
          control = node('select');
          control.append(new Option('Choose a workspace catalog', ''));
          catalogs.forEach(catalog => control.append(new Option(catalog, catalog)));
          if (defaultCatalog && catalogs.includes(defaultCatalog)) control.value = defaultCatalog;
          if (catalogs.length === 0) control.disabled = true;
        } else {
          control = node('input');
          control.type = 'text'; control.maxLength = 1024; control.autocomplete = 'off';
          control.value = Object.prototype.hasOwnProperty.call(defaults, name) ? defaults[name] : capturedDefault;
          if (['dev_catalog', 'dev_schema', 'bronze_source_table', 'target_table'].includes(name))
            control.dataset.autoDefault = 'true';
          if (name === 'demo_mode' || name === 'child_job_id') control.readOnly = true;
        }
        control.id = label.htmlFor; control.name = name;
        controls.set(name, control); group.append(label, control); fields.append(group);
        control.addEventListener('change', () => {
          if (control.dataset.autoDefault === 'true') control.dataset.autoDefault = 'false';
          fillCatalogDefaults(); refreshEffects();
        });
        control.addEventListener('input', () => {
          if (control.dataset.autoDefault === 'true') control.dataset.autoDefault = 'false';
          fillCatalogDefaults(); refreshEffects();
        });
      }
      fillCatalogDefaults();
      refreshEffects();
      return source;
    }
    function refreshEffects(prefixSource = '') {
      const effect = notebookRunEffect(path, controls, prefixSource);
      effects.textContent = effect.text;
      submit.disabled = !confirmInput.checked || !active() || !workspaceHost
        || [...controls.values()].some(control => control.required && !control.value.trim());
    }
    confirmInput.addEventListener('change', () => refreshEffects());

    async function pollRun(receipt, prefixSource, attempt = 0) {
      if (!active()) return;
      try {
        const currentRun = await request(`/api/notebook-runs/${encodeURIComponent(receipt.id)}`, runCandidate.ticket);
        if (!active()) return;
        statusLine.textContent = `Through cell ${cellNumber}: ${currentRun.status}${currentRun.result_state ? ` · ${currentRun.result_state}` : ''}`;
        resultOutput.textContent = currentRun.output || (['terminated', 'skipped', 'internal_error'].includes(currentRun.status)
          ? 'Databricks returned no notebook output.' : 'Waiting for the selected notebook prefix to finish…');
        const logPre = logsOutput.querySelector('pre');
        if (currentRun.logs) logPre.textContent = currentRun.logs;
        if (currentRun.error) { errorOutput.hidden = false; errorOutput.textContent = currentRun.error; }
        if (currentRun.run_page_url) {
          try {
            const url = new URL(currentRun.run_page_url), expected = new URL(workspaceHost);
            if (url.protocol === 'https:' && url.origin === expected.origin) {
              runLink.href = url.href; runLink.hidden = false;
            }
          } catch { /* Do not render an unverified workspace URL. */ }
        }
        const terminal = ['terminated', 'skipped', 'internal_error'].includes(currentRun.status);
        stop.hidden = terminal;
        if (currentRun.output_truncated) resultOutput.textContent += '\nOutput is truncated. Open the Databricks run for full details.';
        if (terminal) {
          runButton.disabled = false;
          setupStatus.textContent = currentRun.result_state === 'SUCCESS'
            ? `Cell ${cellNumber} prefix finished successfully.` : `Cell ${cellNumber} prefix finished with ${currentRun.result_state || currentRun.status}.`;
          return;
        }
        if (attempt >= 300) { statusLine.textContent += ' Polling paused; Databricks may still be running.'; return; }
        setTimeout(() => pollRun(receipt, prefixSource, attempt + 1), 3000);
      } catch (error) {
        if (active()) statusLine.textContent = `Run status unavailable. ${safe(error.message)} It was not submitted again.`;
      }
    }

    runButton.addEventListener('click', async () => {
      if (!active()) return;
      outputWindow.hidden = false; runButton.disabled = true;
      setupStatus.textContent = 'Loading workspace catalog choices and parameter defaults…';
      submit.disabled = true; stop.hidden = true; runLink.hidden = true;
      resultOutput.textContent = 'Output will appear here after this cell prefix finishes.';
      logsOutput.querySelector('pre').textContent = '';
      errorOutput.hidden = true; errorOutput.textContent = '';
      try {
        const prefixCells = notebook.cells.slice(0, cellNumber);
        const hasCatalogParameter = widgetDefaults(prefixCells).values.has('uc_catalog');
        let catalogs = [], defaultCatalog = null;
        if (hasCatalogParameter) {
          const inventory = await request('/api/notebook-runs/catalogs', runCandidate.ticket);
          if (!active()) return;
          workspaceHost = inventory.workspace;
          catalogs = items(inventory.catalogs);
          defaultCatalog = inventory.default_catalog || null;
        } else {
          const workspace = await request('/api/models/workspace', runCandidate.ticket);
          if (!active()) return;
          workspaceHost = typeof workspace.workspace === 'string' ? workspace.workspace : '';
          if (!workspaceHost) throw new Error('Connect and select the intended Databricks workspace before running code.');
        }
        const prefixSource = renderParameterFields(prefixCells, catalogs, defaultCatalog);
        setupStatus.textContent = `Workspace: ${workspaceHost}. Set or confirm the values below, then choose Run through cell ${cellNumber}.`;
        if (hasCatalogParameter && catalogs.length === 0)
          setupStatus.textContent = 'No Unity Catalog choices were returned. Reauthenticate or ask your workspace administrator to grant catalog-list access. No run has started.';
        refreshEffects(prefixSource);
        cancel.hidden = false;
        cancel.addEventListener('click', () => {
          if (!stop.hidden) return;
          outputWindow.hidden = true; runButton.disabled = false;
        }, {once: true});
        submit.onclick = async () => {
          if (!active() || !confirmInput.checked) return;
          submit.disabled = true; cancel.disabled = true;
          setupStatus.textContent = `Submitting cells 1 through ${cellNumber} as one serverless notebook run…`;
          const parameters = formParameters();
          const effect = notebookRunEffect(path, controls, prefixSource);
          const outputTable = effect.output_table || '';
          try {
            const receipt = await request(`/api/jobs/${encodeURIComponent(runCandidate.jobId)}/spark-drafts/run`,
              runCandidate.ticket, {consent: true, artifact_path: path, cell_number: cellNumber,
                expected_workspace: workspaceHost, parameters, side_effects_confirmed: true,
                confirmed_output_table: outputTable});
            if (!active()) return;
            if (!receipt || !/^[a-f0-9]{32}$/.test(receipt.id) || receipt.artifact_path !== path
                || receipt.cell_number !== cellNumber || receipt.design_version !== runCandidate.version)
              throw new Error('Run receipt did not match the selected code cell. Check Databricks Jobs before retrying.');
            if (receipt.status !== 'queued') {
              setupStatus.textContent = receipt.message || `Run was not submitted (${receipt.submission_status || receipt.status}). Check Databricks Jobs before retrying.`;
              return;
            }
            setupStatus.textContent = `Submitted run ${receipt.workspace_run_id}. This output window is for cells 1 through ${cellNumber}.`;
            stop.hidden = false;
            stop.onclick = async () => {
              if (!active() || stopRequested) return;
              stopRequested = true; stop.disabled = true;
              setupStatus.textContent = 'Stop requested. Waiting for Databricks to confirm the final run state…';
              try {
                const stopped = await request(`/api/notebook-runs/${encodeURIComponent(receipt.id)}/cancel`,
                  runCandidate.ticket, {consent: true});
                setupStatus.textContent = stopped.message || 'Stop request sent. Checking the actual run state.';
              } catch (error) {
                stopRequested = false; stop.disabled = false;
                setupStatus.textContent = `Stop not confirmed. ${safe(error.message)} Check Databricks Jobs.`;
              }
            };
            await pollRun(receipt, prefixSource);
          } catch (error) {
            if (active()) setupStatus.textContent = `Run not confirmed. ${safe(error.message)} Check Databricks Jobs before retrying.`;
            submit.disabled = true;
          }
        };
      } catch (error) {
        if (active()) {
          setupStatus.textContent = `Could not prepare this cell run. ${safe(error.message)} No notebook was submitted.`;
          runButton.disabled = false;
        }
      }
    });
  }
  function notebookRunEffect(path, controls) {
    const name = path.split('/').pop();
    if (name === 't_counter.ipynb') {
      const params = Object.fromEntries([...controls].map(([key, input]) => [key, input.value.trim()]));
      const output = params.target_table || [params.dev_catalog, params.dev_schema,
        params.dev_table || 'AE2E_JOB_DEP_CHK_COUNT_DEV'].join('.');
      return `Reads ${params.bronze_source_table || 'the source table you enter'}; appends to ${output || 'the output table you enter'}; may delete rows for TEMPLATE_ID=18300 once its count reaches 60. The source and output must be different.`;
    }
    if (name === 't_wait.ipynb') return `No table writes. This run waits ${controls.get('wait_seconds')?.value || '0'} seconds and uses demo mode.`;
    if (name === 't_child_launch.ipynb') return 'Demo mode only. Prints a simulated child-launch receipt; it will not start the unconverted child workflow.';
    return 'Demo mode only. Prints a simulated email; it will not send email.';
  }
  function workflowTable(title, columns, rows, parent) {
    const region = node('div', '', 'workflow-table-region'); region.tabIndex = 0;
    region.setAttribute('role', 'region'); region.setAttribute('aria-label', title);
    const table = node('table', '', 'workflow-artifact-table'); table.append(node('caption', title));
    const head = node('thead'), header = node('tr');
    for (const label of columns) { const th = node('th', label); th.scope = 'col'; header.append(th); }
    head.append(header); table.append(head);
    const body = node('tbody');
    for (const values of rows) {
      const row = node('tr');
      for (const value of values) {
        const cell = node('td');
        if (value instanceof HTMLElement) cell.append(value); else cell.textContent = safe(value);
        row.append(cell);
      }
      body.append(row);
    }
    table.append(body); region.append(table); parent.append(region);
  }
  // Pending fits belong to mounted graphs, not detached placeholders replaced by a receipt.
  const pendingGraphFits = new Map();
  const graphMounts = new MutationObserver(() => {
    for (const [canvas, pending] of pendingGraphFits) {
      if (!canvas.isConnected) pending.cancel();
      else pending.fit();
    }
  });
  function workflowGraph(tasks, serial, parent, artifacts = {}, parameters = [], artifactMapping = null, logicalProposal = false, portableText = false, runCandidate = null, receiptLoaded = false) {
    const ns = 'http://www.w3.org/2000/svg';
    const svgNode = (tag, attrs, text) => {
      const el = document.createElementNS(ns, tag);
      for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
      if (text !== undefined) el.textContent = safe(text); return el;
    };
    const levels = new Map(), pending = new Map(tasks.map(task => [task.task_key, task]));
    while (pending.size) {
      let advanced = false;
      for (const [id, task] of pending) {
        const dependencies = items(task.depends_on);
        if (!dependencies.every(dep => levels.has(dep.task_key))) continue;
        levels.set(id, Math.max(-1, ...dependencies.map(dep => levels.get(dep.task_key))) + 1);
        pending.delete(id); advanced = true;
      }
      if (!advanced) throw new Error('Invalid workflow dependency graph. No output accepted.');
    }
    const lanes = new Map(), positions = new Map();
    for (const task of tasks) {
      const level = levels.get(task.task_key), lane = lanes.get(level) || 0;
      positions.set(task.task_key, { x: 30 + level * 320, y: 40 + lane * 145 }); lanes.set(level, lane + 1);
    }
    const width = (Math.max(0, ...levels.values()) + 1) * 320 + 30;
    const height = Math.max(280, ...[...lanes.values()].map(count => count * 145 + 80));
    const id = `workflow-native-${epoch}-${serial}`;
    const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, width: '100%', role: 'img', 'aria-labelledby': `${id}-title ${id}-desc` });
    svg.append(svgNode('title', { id: `${id}-title` }, logicalProposal ? 'Proposed Lakeflow task grid' : 'Databricks Jobs task grid'),
      svgNode('desc', { id: `${id}-desc` }, `${tasks.length} ${logicalProposal ? 'proposed logical' : 'native'} tasks, layered by dependencies. Condition outcomes are labelled on edges. Not an executed job.`));
    const defs = svgNode('defs', {}), marker = svgNode('marker', { id: `${id}-arrow`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto' });
    marker.append(svgNode('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#8b9eb1' })); defs.append(marker);
    const pattern = svgNode('pattern', { id: `${id}-dots`, width: 8, height: 8, patternUnits: 'userSpaceOnUse' });
    pattern.append(svgNode('circle', { cx: 1, cy: 1, r: 0.7, fill: '#dce3eb' })); defs.append(pattern); svg.append(defs);
    svg.append(svgNode('rect', { width, height, fill: `url(#${id}-dots)` }));
    for (const task of tasks) for (const dep of items(task.depends_on)) {
      const from = positions.get(dep.task_key), to = positions.get(task.task_key);
      const startX = from.x + 260, startY = from.y + 46 + (dep.outcome === 'false' ? 14 : dep.outcome === 'true' ? -14 : 0);
      const edge = svgNode('path', { d: `M ${startX} ${startY} C ${startX + 30} ${startY}, ${to.x - 30} ${to.y + 46}, ${to.x} ${to.y + 46}`, fill: 'none', stroke: '#8b9eb1', 'stroke-width': 1.3, 'marker-end': `url(#${id}-arrow)` });
      edge.append(svgNode('title', {}, `${dep.task_key} to ${task.task_key}; outcome ${dep.outcome ?? 'success'}`)); svg.append(edge);
      if (dep.outcome === 'true' || dep.outcome === 'false') {
        svg.append(svgNode('circle', { cx: startX, cy: startY, r: 4, fill: '#fff', stroke: '#8b9eb1' }),
          svgNode('text', { x: startX + 6, y: startY - 6, fill: '#52667a', 'font-size': 11 }, dep.outcome === 'true' ? 'True' : 'False'));
      }
    }
    const inspector = node('section', '', 'workflow-task-inspector');
    inspector.hidden = true; inspector.setAttribute('aria-label', 'Selected task details');
    const taskCards = new Map();
    const openTask = (task) => {
      inspector.replaceChildren(); inspector.hidden = false;
      const title = node('h4', task.label || `Task: ${task.task_key}`); title.tabIndex = -1;
      inspector.append(title);
      const close = node('button', 'Close task details', 'secondary'); close.type = 'button';
      close.addEventListener('click', () => {
        inspector.hidden = true;
        [...svg.querySelectorAll('.workflow-native-card')].find(card => card.getAttribute('data-task-key') === task.task_key)?.focus();
      }); inspector.append(close);
      if (task.writerNotice) inspector.append(node('p', task.writerNotice, 'notice'));
      if (task.notebook_task) {
        const purpose = task.purpose || items(record?.design?.tasks).find(item => item.id === task.task_key)?.purpose;
        inspector.append(readableFields({task_key: task.task_key, implementation_kind: task.implementation_kind}));
        if (purpose) inspector.append(taskDescription(purpose));
        const sourcePath = artifactMapping ? (artifactMapping[task.task_key]?.source_path || '')
          : task.notebook_task.notebook_path.replace(/^\.\//, '');
        const notebookPath = artifactMapping ? artifactMapping[task.task_key]?.notebook_path : sourcePath.replace(/\.py$/, '.ipynb');
        inspector.append(node('h4', 'Notebook'), node('p', task.notebook_task.notebook_path, 'small muted'));
        if (Object.prototype.hasOwnProperty.call(artifacts, notebookPath)) {
          try {
            const notebook = JSON.parse(artifacts[notebookPath]);
            const download = button('', `Download ${notebookPath}`, inspector); download.removeAttribute('id');
            download.addEventListener('click', () => {
              if (!runCandidate || page.hidden) return;
              triggerDownload(savedArtifactUrl(runCandidate, notebookPath, artifacts[notebookPath]), notebookPath.split('/').pop());
            });
            if (runCandidate) attachPortalNotebook(inspector, notebookPath, runCandidate);
            items(notebook.cells).forEach((cell, index) => {
              const cellBlock = node('details', '', `notebook-cell notebook-cell-${cell.cell_type === 'code' ? 'python' : 'markdown'}`);
              cellBlock.open = cell.cell_type === 'code';
              const code = codePreview(Array.isArray(cell.source) ? cell.source.join('') : cell.source || '', cellLanguage(cell, notebook), {portableText});
              cellBlock.append(node('summary', `Cell ${index + 1} · ${cell.metadata?.language || cell.cell_type}`), code);
              if (runCandidate && cell.cell_type === 'code'
                  && ['t_wait.ipynb', 't_counter.ipynb', 't_child_launch.ipynb', 't_email.ipynb'].includes(notebookPath.split('/').pop()))
                attachCellRunner({block: cellBlock, path: notebookPath, notebook, cellNumber: index + 1,
                  runCandidate, active: () => current(runCandidate.ticket) && !page.hidden});
              inspector.append(cellBlock);
            });
            inspector.append(notebookParameters(notebook, parameters, task.ucMappings));
          } catch { inspector.append(node('p', 'Notebook JSON could not be read. Exact generated source remains available below.', 'notice')); }
        } else inspector.append(node('p', 'No notebook JSON supplied for this task.', 'small muted'));
        if (Object.prototype.hasOwnProperty.call(artifacts, sourcePath)) {
          const source = node('details'), code = codePreview(artifacts[sourcePath], artifactLanguage(sourcePath), {portableText});
          source.append(node('summary', 'Python notebook source'), code);
          inspector.append(source);
        }
      } else if (task.condition_task || task.implementation_kind === 'condition_task') {
        if (task.condition_task) inspector.append(node('h4', 'Branch condition'), readableFields({condition: task.condition_task}));
        inspector.append(node('h4', 'Branch destinations'), readableFields(tasks.filter(candidate => items(candidate.depends_on).some(dep => dep.task_key === task.task_key))
          .map(candidate => ({ task_key: candidate.task_key, dependencies: candidate.depends_on }))));
        if (task.purpose) inspector.append(taskDescription(task.purpose));
      } else {
        inspector.append(readableFields({
          task_key: task.task_key, implementation_kind: task.implementation_kind || 'not specified',
          dependencies: items(task.depends_on).map(dep => ({task_key: dep.task_key, outcome: dep.outcome, condition: dep.condition}))
        }));
        if (task.purpose) inspector.append(taskDescription(task.purpose));
        const sourcePath = artifactMapping?.[task.task_key]?.source_path;
        const notebookPath = artifactMapping?.[task.task_key]?.notebook_path;
        const codePath = sourcePath && Object.prototype.hasOwnProperty.call(artifacts, sourcePath) ? sourcePath
          : notebookPath && Object.prototype.hasOwnProperty.call(artifacts, notebookPath) ? notebookPath : null;
        if (codePath) {
          inspector.append(node('h4', `Generated task source: ${codePath}`));
          const downloadSource = button('', `Download ${codePath}`, inspector); downloadSource.removeAttribute('id');
          downloadSource.addEventListener('click', () => {
            if (page.hidden) return;
            const content = artifacts[codePath];
            const type = codePath.endsWith('.ipynb') ? 'application/json' : 'text/plain;charset=utf-8';
            const url = runCandidate ? savedArtifactUrl(runCandidate, codePath, content)
              : URL.createObjectURL(new Blob([content], {type}));
            triggerDownload(url, codePath.split('/').pop());
          });
          if (codePath.endsWith('.ipynb')) {
            try {
              const notebook = JSON.parse(artifacts[codePath]);
              if (!Array.isArray(notebook.cells)) throw new Error('Invalid notebook cells');
              if (runCandidate) attachPortalNotebook(inspector, codePath, runCandidate);
              notebook.cells.forEach((cell, index) => {
                const cellBlock = node('details', '', `notebook-cell notebook-cell-${cell.cell_type === 'code' ? 'python' : 'markdown'}`);
                cellBlock.open = cell.cell_type === 'code';
                const code = codePreview(Array.isArray(cell.source) ? cell.source.join('') : cell.source || '', cellLanguage(cell, notebook), {portableText});
                cellBlock.append(node('summary', `Cell ${index + 1} · ${cell.metadata?.language || cell.cell_type}`), code);
                if (runCandidate && cell.cell_type === 'code'
                    && ['t_wait.ipynb', 't_counter.ipynb', 't_child_launch.ipynb', 't_email.ipynb'].includes(codePath.split('/').pop()))
                  attachCellRunner({block: cellBlock, path: codePath, notebook, cellNumber: index + 1,
                    runCandidate, active: () => current(runCandidate.ticket) && !page.hidden});
                inspector.append(cellBlock);
              });
            } catch { inspector.append(node('p', 'Notebook JSON could not be read. The original artifact remains available below.', 'notice')); }
          } else {
            const code = codePreview(artifacts[codePath], artifactLanguage(codePath), {portableText}); inspector.append(code);
          }
        } else inspector.append(node('p', receiptLoaded
          ? 'The selected saved result contains no notebook file for this task.'
          : 'Saved notebook code has not been loaded for this task yet. Read a saved writer result to check for its exact file.', 'notice'));
      }
      for (const card of svg.querySelectorAll('.workflow-native-card')) card.setAttribute('aria-pressed', String(card.getAttribute('data-task-key') === task.task_key));
      title.focus({preventScroll: true});
      inspector.scrollIntoView({block: 'nearest', behavior: 'instant'});
    };
    for (const task of tasks) {
      const { x, y } = positions.get(task.task_key);
      const card = svgNode('g', { class: 'workflow-native-card', role: 'button', tabindex: 0,
        'data-task-key': task.task_key, 'aria-label': `Open task ${task.task_key}`, 'aria-pressed': 'false' });
      card.addEventListener('click', () => openTask(task));
      card.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openTask(task); }
      });
      taskCards.set(task.task_key, card);
      card.append(svgNode('rect', { x, y, width: 260, height: 92, rx: 5, fill: '#fff', stroke: '#cbd5e1' }),
        svgNode('text', { x: x + 14, y: y + 24, fill: '#16324f', 'font-size': 13, 'font-weight': 600 }, task.task_key));
      const conditionOperand = operand => {
        if (!operand || typeof operand !== 'object') return '';
        if (operand.kind === 'task_value') return `${operand.task_id}.${operand.key}`;
        if (operand.kind === 'literal') return operand.value;
        return '';
      };
      const conditionOperator = {LESS_THAN: '<', LESS_THAN_OR_EQUAL: '≤', GREATER_THAN: '>',
        GREATER_THAN_OR_EQUAL: '≥', EQUAL_TO: '=', NOT_EQUAL: '≠'};
      const detail = task.condition_task
        ? ([conditionOperand(task.condition_task.left), conditionOperator[task.condition_task.op] || task.condition_task.op,
          conditionOperand(task.condition_task.right)].filter(Boolean).join(' ') || task.purpose || 'Branch condition')
        : task.notebook_task?.notebook_path || task.purpose || task.implementation_kind || 'Task';
      const displayDetail = task.condition_task ? detail.replace(/\{\{tasks\.[^.]+\.values\.([^}]+)\}\}/g, '$1') : detail;
      const subtitle = svgNode('text', { x: x + 14, y: y + 48, fill: '#52667a', 'font-size': 10 }, displayDetail.length > 38 ? `${displayDetail.slice(0, 35)}…` : displayDetail);
      subtitle.append(svgNode('title', {}, detail)); card.append(subtitle,
        svgNode('text', { x: x + 14, y: y + 72, fill: '#52667a', 'font-size': 11 }, task.implementation_kind || (task.condition_task ? 'condition_task' : 'notebook_task')));
      svg.append(card);
    }
    const canvas = node('div', '', 'workflow-native-canvas'); canvas.append(svg);
    const toolbar = node('div', '', 'workflow-native-toolbar'); toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Workflow zoom controls');
    const zoomOut = node('button', '− Zoom out', 'secondary'), zoomIn = node('button', '+ Zoom in', 'secondary');
    const reset = node('button', 'Reset zoom', 'secondary'), fit = node('button', 'Fit to view', 'secondary');
    const zoomLabel = node('output', 'Fitting…'); zoomLabel.setAttribute('aria-live', 'polite');
    let scale = 1;
    // Start with the complete viewBox visible, never a transient 100% crop.
    // Inline dimensions also override the example-output SVG max-width rule.
    Object.assign(svg.style, {width: '100%', height: 'auto', maxWidth: 'none', minWidth: '0', maxHeight: 'none'});
    const setZoom = (value, fitting = false) => {
      scale = fitting ? value : Math.max(0.25, Math.min(2.5, value));
      svg.setAttribute('width', String(width * scale)); svg.setAttribute('height', String(height * scale));
      svg.style.width = `${width * scale}px`; svg.style.height = `${height * scale}px`;
      zoomLabel.textContent = `${Math.round(scale * 100)}%`; zoomOut.disabled = scale <= 0.25; zoomIn.disabled = scale >= 2.5;
    };
    const fitToView = () => {
      if (!canvas.isConnected || !canvas.getClientRects().length) return false;
      const style = getComputedStyle(canvas);
      const available = canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      if (!(available > 0)) return false;
      setZoom(available / width, true); canvas.scrollLeft = 0; return true;
    };
    const cancelInitialFit = () => {
      initialFit.disconnect(); pendingGraphFits.delete(canvas);
      if (!pendingGraphFits.size) graphMounts.disconnect();
    };
    const tryInitialFit = () => {
      if (pendingGraphFits.has(canvas) && fitToView()) cancelInitialFit();
    };
    const initialFit = new ResizeObserver(tryInitialFit);
    for (const control of [zoomOut, zoomIn, reset, fit]) control.type = 'button';
    zoomOut.addEventListener('click', () => { cancelInitialFit(); setZoom(scale - 0.25); });
    zoomIn.addEventListener('click', () => { cancelInitialFit(); setZoom(scale + 0.25); });
    reset.addEventListener('click', () => { cancelInitialFit(); setZoom(1); canvas.scrollLeft = 0; });
    fit.addEventListener('click', () => { cancelInitialFit(); fitToView(); });
    toolbar.append(zoomOut, zoomIn, reset, fit, zoomLabel);
    parent.append(toolbar, canvas, inspector);
    pendingGraphFits.set(canvas, {cancel: cancelInitialFit, fit: tryInitialFit});
    graphMounts.observe(page, {subtree: true, childList: true});
    initialFit.observe(canvas);
    tryInitialFit();
    return {
      addRedesignAnimation(open) {
        const animate = node('button', 'View redesign animation', 'secondary');
        animate.type = 'button'; animate.setAttribute('aria-haspopup', 'dialog');
        animate.addEventListener('click', open); fit.after(animate);
      },
      openTask(taskKey) {
        const task = tasks.find(item => item.task_key === taskKey);
        if (!task || !taskCards.has(taskKey)) return false;
        openTask(task);
        return true;
      }
    };
  }
  function taskDescription(text) {
    const section = node('section', '', 'task-business-description');
    section.append(node('h4', 'What this task does'));
    const list = node('ul', '', 'proposal-review-list');
    const sentences = new Intl.Segmenter('en', {granularity: 'sentence'}).segment(String(text || ''));
    for (const sentence of sentences) {
      if (sentence.segment.trim()) list.append(node('li', sentence.segment.trim()));
    }
    section.append(list); return section;
  }
  function notebookParameters(notebook, parameters, ucMappings = []) {
    const names = new Set();
    for (const cell of items(notebook.cells)) {
      if (cell.cell_type !== 'code') continue;
      const source = Array.isArray(cell.source) ? cell.source.join('') : String(cell.source || '');
      for (const match of source.matchAll(/dbutils\.widgets\.(?:text|dropdown|combobox|multiselect|get)\(\s*['"]([^'"]+)['"]/g)) names.add(match[1]);
    }
    const block = node('details'); block.append(node('summary', 'This notebook’s parameters'));
    block.append(node('p', 'Documentation only. Set values in the Databricks notebook widgets or Job run parameters.', 'small muted'));
    if (ucMappings.length) {
      block.append(node('p', 'Agent-discovered Unity Catalog mappings', 'small'));
      const mappings = node('ul', '', 'proposal-review-list');
      for (const mapping of ucMappings) {
        const row = node('li');
        row.append(node('strong', `${mapping.kind === 'source' ? 'Source' : 'Target'}: ${mapping.label}`));
        if (mapping.candidates.length) {
          for (const name of mapping.candidates) {
            const label = node('p', '', 'small uc-mapping-name');
            const parts = name.split('.');
            if (parts.length === 3) {
              parts.forEach((part, index) => {
                if (index) label.append(document.createTextNode('.'));
                const component = node('span', part, ['uc-name-catalog', 'uc-name-schema', 'uc-name-table'][index]);
                component.title = ['Catalog', 'Schema', 'Table'][index];
                label.append(component);
              });
              label.setAttribute('aria-label', `Catalog ${parts[0]}, schema ${parts[1]}, table ${parts[2]}. Metadata-verified candidate, not an approved runtime binding.`);
              label.append(document.createTextNode(' (metadata-verified candidate, not an approved runtime binding)'));
            } else label.textContent = `${name} (metadata-verified candidate, not an approved runtime binding)`;
            row.append(label);
          }
          if (mapping.candidates.length > 1)
            row.append(node('p', 'Multiple candidates were recorded; the intended binding remains unresolved.', 'small muted'));
        } else row.append(node('p', 'No verified UC table mapping was recorded for this object.', 'small muted'));
        mappings.append(row);
      }
      block.append(mappings);
    }
    const list = node('ul', '', 'proposal-review-list');
    for (const name of names) {
      const parameter = items(parameters).find(item => item.name === name);
      const item = node('li'); item.append(node('strong', name));
      if (parameter?.purpose) item.append(node('p', parameter.purpose));
      if (parameter && Object.prototype.hasOwnProperty.call(parameter, 'default')) item.append(node('p', `Default: ${parameter.default || '(empty)'}`, 'small muted'));
      list.append(item);
    }
    block.append(names.size ? list : node('p', 'No notebook widgets are declared. No table parameters are implied.', 'small muted'));
    return block;
  }
  let nativeGraphSerial = 0;
  function nativeJobGraphs(nativeJobs, document, artifacts, parent, workspaceRoot, runCandidate = null) {
    parent.replaceChildren(node('h4', 'Databricks task view'), node('p',
      'Actual generated Jobs tasks. Draft only, not uploaded, executed, approved for release or proof of parity.', 'small muted'));
    try {
      // This native builder serializes JSON, valid YAML 1.2. Never regex-parse
      // arbitrary YAML or manufacture a graph from a logical outline/files.
      const resources = JSON.parse(document).resources.jobs;
      if (!Array.isArray(nativeJobs) || !nativeJobs.length) throw new Error('Missing structured Jobs metadata');
      const accepted = nativeJobs.map(job => {
        const resource = resources[job.job_resource], tasks = job.tasks, mapping = job.artifact_mapping;
        if (!resource || !Array.isArray(tasks) || !tasks.length || !mapping || typeof mapping !== 'object'
            || job.workspace_root !== workspaceRoot
            || JSON.stringify(resource.tasks) !== JSON.stringify(tasks)
            || JSON.stringify(resource.parameters || []) !== JSON.stringify(job.parameters)
            || !/^\/Workspace\/[A-Za-z0-9_./-]+$/.test(job.workspace_root)
            || job.workspace_root.split('/').slice(1).some(part => !part || part === '.' || part === '..'))
          throw new Error('Jobs metadata does not match the generated document');
        const keys = new Set(tasks.map(task => task.task_key));
        if (keys.size !== tasks.length) throw new Error('Duplicate Jobs task');
        for (const task of tasks) {
          if (typeof task.task_key !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(task.task_key)
              || items(task.depends_on).some(dep => !keys.has(dep.task_key))
              || (!!task.notebook_task === !!task.condition_task)) throw new Error('Unsupported Jobs task data');
          if (!task.notebook_task) continue;
          const path = mapping[task.task_key]?.notebook_path;
          if (typeof path !== 'string' || !path.endsWith('.ipynb') || path.startsWith('/')
              || path.includes('\\') || path.split('/').some(part => !part || part === '.' || part === '..')
              || !Object.prototype.hasOwnProperty.call(artifacts, path) || typeof artifacts[path] !== 'string'
              || task.notebook_task.notebook_path !== `${job.workspace_root}/${path.slice(0, -6)}`
              || !Array.isArray(JSON.parse(artifacts[path]).cells) || safe(artifacts[path]) !== artifacts[path])
            throw new Error('Exact committed notebook mapping unavailable');
        }
        return job;
      });
      const graphs = node('div');
      for (const job of accepted) {
        graphs.append(node('h5', job.job_resource));
        workflowGraph(job.tasks, `native-${++nativeGraphSerial}`, graphs, artifacts, job.parameters, job.artifact_mapping,
          false, false, runCandidate);
      }
      parent.append(graphs);
    } catch (_) {
      parent.append(node('p', 'Task data unavailable. Exact generated files remain available below; no task graph is inferred.', 'notice'));
    }
  }
  function renderWorkflowArtifacts(result, ticket, serial) {
    const output = node('div');
    const name = [...agentWorkflow.options].find(option => option.value === result.workflow_id)?.textContent?.trim() || 'Selected workflow';
    const summary = node('section', '', 'disclosure');
    summary.setAttribute('aria-label', 'Generated workflow summary');
    summary.append(node('strong', name),
      node('p', result.scenario === 'bronze' ? 'Source: Unity Catalog' : 'Source: original systems', 'small muted'),
      node('p', 'Example draft. Not approved or deployed.', 'small muted'));
    output.append(summary, node('p', result.notice),
      node('p', 'Bronze needs no original Oracle connections. Runtime bindings remain required: Delta source and target, real child job, SMTP secrets and network access, and supported compute. See every requirement below.', 'notice'));
    workflowGraph(result.tasks, serial, output, result.artifacts, result.parameters, null, false, true);
    workflowTable(`Native tasks (${result.tasks.length})`, ['Task key', 'Type', 'Dependencies', 'Parameters / notebook', 'Condition outcome'], result.tasks.map(task => [
      task.task_key, task.condition_task ? 'condition_task' : 'notebook_task', items(task.depends_on).map(dep => dep.task_key).join(', ') || 'None',
      readableFields(task.notebook_task
        ? { notebook_path: task.notebook_task.notebook_path,
            ...Object.fromEntries(Object.entries(task.notebook_task).filter(([key]) => key !== 'notebook_path')) }
        : { predicate: task.condition_task }), items(task.depends_on).map(dep => `${dep.task_key}: ${dep.outcome ?? 'success'}`).join(', ') || 'Not applicable'
    ]), output);
    workflowTable(`Runtime job parameters (${result.parameters.length})`, ['Name', 'Value / required binding'], result.parameters.map(param => [param.name, param.default ?? 'Required, unbound']), output);
    const inventory = result.parameter_inventory, counts = {};
    for (const item of inventory) counts[item.kind] = (counts[item.kind] || 0) + 1;
    output.append(node('p', `Complete variable inventory: ${inventory.length} records. ${Object.entries(counts).map(([kind, count]) => `${kind}: ${count}`).join('; ')}. All records included; references are evidence, not resolved runtime bindings.`, 'small muted'));
    const inventoryView = node('details'); inventoryView.append(node('summary', `Full scoped inventory (${inventory.length} records, no truncation)`));
    workflowTable('Declarations, session properties and reference resolution', ['Scope', 'Kind / name', 'Value / expression', 'Reference resolution', 'Full evidence'], inventory.map(item => {
      const attrs = item.attributes || {}, evidence = node('details');
      evidence.append(node('summary', 'All attributes and evidence'), node('pre', JSON.stringify(item, null, 2), 'json-block'));
      return [items(item.scope).map(part => `${part.kind}: ${part.name}`).join(' / '), `${item.kind}: ${item.name ?? ''}`,
        attrs.VALUE ?? attrs.DEFAULTVALUE ?? attrs.expression ?? readableFields(attrs), item.resolution ?? 'Captured declaration/property, not an execution binding', evidence];
    }), inventoryView); output.append(inventoryView);
    section(`Deployment requirements (${result.deployment_requirements.length}), not deployment results`, result.deployment_requirements, output);
    json('Compiler validation and unverified boundaries', result.validation, output);
    const archive = button('workflow-artifact-archive', 'Download complete bundle (ZIP)', output);
    const active = () => current(ticket) && serial === workflowTicket && !page.hidden;
    let pending = false;
    archive.addEventListener('click', async () => {
      // Successful explicit generation authorized this exact scope only. A ZIP
      // recompile uses that consent, never model/egress or deployment consent.
      if (!active() || pending || busy || loading) return;
      pending = true; archive.disabled = true; busy = true; controls();
      const controller = new AbortController(); controllers.add(controller);
      const timeout = setTimeout(() => controller.abort(), 30000);
      const headers = { Accept: 'application/zip', 'Content-Type': 'application/json', 'X-Workbench-Request': '1' };
      if (ticket.scope.project) {
        headers['X-Workbench-Project'] = ticket.scope.project;
        headers['X-Workbench-Package'] = ticket.scope.package;
      }
      workflowStatus.textContent = 'Preparing complete ZIP in memory. No model invocation, execution or deployment.';
      try {
        const response = await fetch('/api/proposals/workflow-artifacts/archive', {
          method: 'POST', headers, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
          body: JSON.stringify({ workflow_id: ticket.scope.workflow, scenario: ticket.scope.scenario, consent: true })
        });
        if (!active()) return;
        if (!response.ok) {
          const failure = await response.json();
          if (!active()) return;
          throw new Error(`Archive download failed (HTTP ${response.status}). ${typeof failure.detail === 'string' ? safe(failure.detail) : 'No archive accepted.'}`);
        }
        if (response.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/zip')
          throw new Error('Archive response contract mismatch. No download accepted.');
        const blob = await response.blob();
        if (!active()) return;
        const url = URL.createObjectURL(blob);
        const selectedName = [...agentWorkflow.options].find(option => option.value === result.workflow_id)?.textContent || '';
        const member = selectedName.match(/wkf_AE2E_DUMMY_CALL_(WIL_(?:NET_DIS|NET_INS|CUS_INST|CUS_DIS))/);
        triggerDownload(url, `${member ? member[1].toLowerCase() : 'wil_net_dis'}_bronze.zip`);
        workflowStatus.textContent = 'Complete ZIP downloaded with relative folder hierarchy. No execution, approval or deployment performed.';
      } catch (error) {
        if (active()) workflowStatus.textContent = error.name === 'AbortError'
          ? 'Archive interrupted or timed out. No download accepted. Retry for this scope.' : safe(error.message);
      } finally {
        clearTimeout(timeout); controllers.delete(controller); pending = false;
        if (active()) { busy = false; archive.disabled = false; controls(); }
      }
    });
    showArtifactPreviews(result.artifacts, output, { portableText: true, active: () => current(ticket) && serial === workflowTicket && !page.hidden });
    workflowOutput.replaceChildren(output);
    showExampleOutput.hidden = false;
  }
  function graph(design, serial, references = new Map(), parent = detail, artifacts = {}, manifest = null, runCandidate = null) {
    const tasks = items(design.tasks), edges = items(design.edges);
    const taskStates = new Map(items(manifest?.tasks).map(task => [task.task_id, task]));
    const observations = new Map(items(manifest?.uc_metadata_observations).map(item => [item.observation_id, item]));
    const artifactMapping = {};
    const graphTasks = tasks.map(task => ({
      task_key: task.id, label: task.label, implementation_kind: task.implementation_kind,
      purpose: task.purpose, condition_task: task.implementation_kind === 'condition_task' ? (task.condition_task || {}) : null,
      ucMappings: items(task.evidence_ids).map(id => {
        const ref = references.get(id);
        if (!ref || !['source', 'target'].includes(ref.kind)) return null;
        const candidates = items(manifest?.uc_table_bindings).filter(binding => {
          const observation = observations.get(binding.observation_id);
          return binding.canonical_id === id && binding.kind === ref.kind
            && binding.confidence === 'metadata_verified_candidate_not_semantic_proof'
            && observation?.operation === 'tables.get' && observation.status === 'observed'
            && observation.exists === true && observation.observation_sha256 === binding.observation_sha256
            && observation.query?.full_name === binding.full_name
            && items(observation.items).some(item => item.full_name === binding.full_name);
        }).map(binding => binding.full_name).filter(name => typeof name === 'string');
        return {kind: ref.kind, label: ref.label || id, candidates: [...new Set(candidates)]};
      }).filter(Boolean),
      notebook_task: null,
      depends_on: edges.filter(edge => edge.target === task.id).map(edge => ({
        task_key: edge.source, outcome: edge.outcome, run_if: edge.run_if, condition: edge.condition
      }))
    }));
    if (manifest) {
      for (const task of graphTasks) {
        const state = taskStates.get(task.task_key) || {};
        const paths = items(state.artifact_paths);
        const matching = Object.keys(artifacts).filter(path =>
          path.split('/').pop().replace(/\.(ipynb|py)$/i, '').toLowerCase() === task.task_key.toLowerCase());
        const notebookPath = paths.find(path => path.endsWith('.ipynb')) || matching.find(path => path.endsWith('.ipynb'));
        const sourcePath = paths.find(path => path.endsWith('.py')) || matching.find(path => path.endsWith('.py'));
        if (notebookPath || sourcePath) {
          artifactMapping[task.task_key] = {notebook_path: notebookPath || sourcePath.replace(/\.py$/, '.ipynb'),
            source_path: sourcePath || (notebookPath ? notebookPath.replace(/\.ipynb$/, '.py') : '')};
          task.notebook_task = {notebook_path: notebookPath || sourcePath};
        } else if (items(state.blockers).length) task.writerNotice = `Writer report: ${state.blockers.join('; ')}`;
        else if (state.status) task.writerNotice = `Writer status: ${state.status}.`;
      }
    }
    parent.append(node('p', manifest
        ? 'Select a task card for notebook details.'
        : 'Tasks are arranged by dependency. Select a card for task details. Saved notebook code will appear here after the app reads a writer result.', 'small muted'));
    const taskGraph = workflowGraph(graphTasks, `proposal-${serial}`, parent, artifacts, design.parameters, artifactMapping,
      true, false, runCandidate, !!manifest);
    const animationEpoch = epoch;
    taskGraph.addRedesignAnimation(() => {
      if (animationEpoch !== epoch || !parent.isConnected || page.hidden) return;
      window.orionRedesignAnimation?.open({design, references,
        title: design.title || 'Source to Lakeflow redesign',
        isCurrent: () => animationEpoch === epoch && parent.isConnected && !page.hidden});
    });
    const more = node('details', '', 'proposal-task-details');
    const coverageBody = node('div', '', 'review-disclosure-body');
    coverageBody.append(node('p', 'How source tasks map to this design, including dependency conditions.', 'review-disclosure-help small muted'), taskTable(design));
    more.classList.add('review-disclosure');
    more.append(node('summary', 'Task mapping and dependencies'), coverageBody);
    section('Directed dependencies and conditions', humanizeEvidence(edges.map(edge => ({
      source: edge.source, target: edge.target, run_if: edge.run_if,
      condition: edge.condition ?? 'none', outcome: edge.outcome ?? 'none', evidence_ids: edge.evidence_ids
    })), references), coverageBody);
    parent.append(more);
    return taskGraph;
  }
  function download(value, filename, ticket, serial) {
    if (!current(ticket) || serial !== detailTicket) return;
    const content = JSON.stringify(value, null, 2);
    if (safe(content) !== content) { status.textContent = 'Download blocked: credential-shaped content requires removal at source. No altered output is presented as original JSON.'; return; }
    const url = URL.createObjectURL(new Blob([content], { type: 'application/json' }));
    triggerDownload(url, filename);
  }
  function localDraftProgress(titleText = 'Conversion request', stopControl = null) {
    const dialog = node('dialog', '', 'draft-progress-dialog');
    dialog.setAttribute('aria-labelledby', 'draft-progress-heading');
    const title = node('h3', titleText); title.id = 'draft-progress-heading';
    const text = node('p', 'Submitting the requested draft.'); text.setAttribute('role', 'status');
    const close = node('button', 'Minimize'); close.type = 'button';
    const restore = node('button', 'Show conversion progress', 'secondary compact draft-progress-restore');
    restore.type = 'button'; restore.hidden = true;
    const stopParent = stopControl?.parentElement;
    let active = true;
    close.addEventListener('click', () => {
      dialog.close();
      if (active && stopControl && stopParent?.isConnected) stopParent.append(stopControl);
      if (active) restore.hidden = false;
      else { dialog.remove(); restore.remove(); }
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close.click(); });
    restore.addEventListener('click', () => {
      restore.hidden = true;
      if (active && stopControl) dialog.append(stopControl);
      dialog.showModal();
    });
    dialog.append(title, text, close);
    if (stopControl) dialog.append(stopControl);
    document.body.append(dialog, restore); dialog.showModal();
    return {
      update(message) { text.textContent = message; },
      finish(message) {
        active = false; text.textContent = message; close.textContent = 'Close';
        if (!dialog.open) { restore.hidden = false; restore.textContent = 'Show conversion result'; }
      }
    };
  }
  function draftControls(value, ticket, serial, parent = detail, onSavedArtifacts = null, outputParent = parent) {
    const canDraft = value.version === latestVersion && value.stale === false
      && value.input_version === value.current_input_version;
    const block = node('section', '', 'stack section-gap'); block.id = 'spark-drafts';
    block.append(node('p', 'Refresh to check for newer saved notebooks.', 'small muted'));
    const draft = button('spark-draft-submit', 'Start notebook writer', block);
    draft.hidden = true;
    const message = node('p', 'Checking for saved writer results for this exact design…', 'small muted');
    message.id = 'spark-draft-status'; message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
    const output = node('div', '', 'detail-list'); output.id = 'spark-draft-output'; block.append(message);
    parent.append(block);
    outputParent.append(output);
    const savedJob = document.createElement('input'); savedJob.type = 'hidden'; savedJob.id = 'spark-draft-job-id'; block.append(savedJob);
    const findSaved = button('spark-draft-find', 'Refresh notebooks', block);
    const savedPicker = field('select', 'spark-draft-saved-run', 'Saved writer result', block);
    savedPicker.append(new Option('Loading saved results…', ''));
    const openSaved = button('spark-draft-open-saved', 'Open selected result', block); openSaved.disabled = true;
    savedPicker.hidden = savedPicker.previousElementSibling.hidden = true;
    openSaved.hidden = true;
    const existingNote = node('p', 'Saved code is looked up automatically for this selected workflow design. Reading or downloading it does not start generation.', 'small muted');
    existingNote.hidden = true; block.append(existingNote);
    let pending = false, autoReadJob = null;
    const active = () => current(ticket) && serial === detailTicket && record === value && !page.hidden;
    let autoReadStarted = false;
    loadSavedDrafts = () => {
      sync();
      if (value.source?.kind !== 'agent' || autoReadStarted || !active() || findSaved.disabled) return;
      autoReadStarted = true;
      findSaved.click();
    };
    function sync() {
      draft.disabled = busy || loading || pending || !active() || !canDraft || !valid();
      savedJob.disabled = busy || loading || detailLoading || pending || !active() || !valid();
      findSaved.disabled = busy || loading || detailLoading || pending || !active() || !valid();
      openSaved.disabled = busy || loading || detailLoading || pending || !active() || !valid() || !savedPicker.value;
      savedPicker.disabled = busy || loading || detailLoading || pending || !active() || !valid()
        || ![...savedPicker.options].some(option => option.value);
    }
    function accept(job, id) {
      if (!job || !/^[a-f0-9]{32}$/.test(job.id) || (id && job.id !== id)
          || job.kind !== 'spark_draft' || job.design_id !== value.id || job.design_version !== value.version
          || job.package_hash !== value.package_hash || job.workflow_id !== value.workflow_id
          || job.scenario !== value.scenario || job.input_version !== value.input_version
          || !['queued', 'running', 'completed', 'failed'].includes(job.status))
        throw new Error('Draft job response scope mismatch. Refresh actual records.');
      return job;
    }
    function receiptSummary(result) {
      const stagedArtifacts = result.staged_artifacts && typeof result.staged_artifacts === 'object'
        && !Array.isArray(result.staged_artifacts) ? result.staged_artifacts : {};
      const notebookPaths = new Set([...Object.keys(stagedArtifacts), ...Object.keys(result.artifacts)]
        .filter(path => path.endsWith('.ipynb')));
      return `${notebookPaths.size} generated notebook(s) available for design v${value.version}. `
        + 'Select a task above to review or download its notebook, or open it in Databricks. Runtime correctness and Informatica parity are unverified.';
    }
    async function poll(id, attempt = 0, progress = null) {
      if (!active()) { progress?.finish('Tracking stopped after the selection changed. Inspect the saved job record before retrying.'); return; }
      try {
        const job = accept(await request(`/api/jobs/${encodeURIComponent(id)}`, ticket), id);
        if (!active()) return;
        message.textContent = `Notebook draft generation: ${job.status}. No execution or deployment.`;
        progress?.update(`Notebook draft generation: ${job.status}. No execution or deployment.`);
        if (job.status === 'failed') {
          progress?.finish('Notebook draft generation stopped. No deployment was performed.');
          pending = false; message.textContent += ` ${safe(job.error || 'Draft unavailable.')}`; sync(); return;
        }
        if (job.status === 'completed') {
          progress?.finish('Notebook draft generation finished. Review saved files and any incomplete items below.');
          pending = false;
          savedJob.value = id;
          const read = button('spark-draft-read', 'Read exact artifact receipt', output);
          read.hidden = true;
          read.addEventListener('click', async () => {
            if (!active()) return;
            read.disabled = true;
            try {
              const result = await request(`/api/jobs/${encodeURIComponent(id)}/spark-drafts`, ticket);
              if (!active()) return;
              if (result.job_id !== id || result.manifest?.run_id !== job.output?.run_id
                  || result.manifest?.design_id !== value.id || result.manifest?.design_version !== value.version
                  || result.manifest?.package_hash !== value.package_hash || result.manifest?.input_version !== value.input_version
                  || !result.artifacts || typeof result.artifacts !== 'object' || Array.isArray(result.artifacts))
                throw new Error('Committed draft response scope mismatch.');
              output.replaceChildren();
              const summary = receiptSummary(result);
              const writerReport = node('details');
              writerReport.append(node('summary', 'Generation diagnostics (technical)'), node('p',
                `Original saved generation state: ${safe(result.manifest.status || 'unknown')}${result.manifest.failure ? ` · ${safe(result.manifest.failure)}` : ''}. Historical records have not been rewritten.`, 'small muted'));
              output.append(writerReport);
              if (result.migration_evaluation?.workflow_id === value.workflow_id
                  && result.migration_evaluation.design_version === value.version) {
                const evaluation = result.migration_evaluation;
                const report = node('section', '', 'migration-evaluation-report');
                report.append(node('h3', 'XML-to-code migration evaluation'), node('p',
                  `Direct source-task coverage: ${evaluation.task_coverage_count}/${evaluation.direct_task_count}. `
                  + `Workflow variables: ${evaluation.workflow_variable_count}; mapping variables: ${evaluation.mapping_variable_count}. `
                  + `Generated Jobs YAML: ${evaluation.native_job_available ? 'available' : 'not available'}.`, 'small'));
                report.append(node('p', `AI code review: ${safe(evaluation.ai_review_status)}. Parameter translation, business-logic correctness and runtime parity are not independently verified.`, 'notice'));
                workflowTable('Original task dispositions', ['Source task', 'Type', 'Disposition', 'Proposed tasks'],
                  items(evaluation.tasks).map(task => [task.name, task.source_type, task.disposition,
                    items(task.proposed_tasks).join(', ') || 'None']), report);
                workflowTable('Source variable and parameter evidence', ['Source reference', 'Category', 'Used in source', 'Design parameter mentions', 'Widget present', 'Translation'],
                  items(evaluation.variables).map(variable => [variable.source_name, variable.category,
                    variable.referenced ? 'Yes' : 'Declaration only',
                    items(variable.design_parameter_mentions).join(', ') || 'No explicit token mapping',
                    items(variable.widgets_present).join(', ') || 'Not established', 'Not independently verified']), report);
                workflowTable('Agent code-review results', ['Task', 'AI reviewed', 'Disposition', 'Issues'],
                  items(evaluation.ai_review_tasks).map(task => [task.task_id, task.ai_reviewed === true ? 'Yes' : 'No',
                    task.status, items(task.issues).map(issue => issue.detail).join('; ') || 'None recorded']), report);
                for (const limitation of items(evaluation.limitations)) report.append(node('p', limitation, 'small muted'));
                output.append(disclosure('Migration evaluation: source tasks, variables and agent review', report));
              }
              const codeNote = savedCodeNotes.get(value.workflow_id);
              if (codeNote) codeNote.textContent = 'The selected design’s saved code is shown below.';
              const codeState = detail.querySelector('#saved-design-code-state');
              if (codeState) savedCodeState(codeState, summary);
              message.textContent = 'Saved notebooks loaded.';
              const reviewItems = items(result.manifest?.human_review_items);
              if (reviewItems.length) {
                const questions = node('section', '', 'proposal-review-section post-generation-questions');
                questions.append(node('h3', 'Questions to check after reviewing the notebooks'),
                  node('p', 'These notes do not block opening, viewing, or downloading the saved notebook drafts. They identify source details to confirm before production use.', 'small muted'));
                const categoryNames = {mapping: 'Mapping behavior', parameter: 'Job parameters', connection: 'Source and target tables', notification: 'Notifications'};
                for (const item of reviewItems) {
                  const card = node('article', '', 'proposal-review-card');
                  card.append(node('h4', categoryNames[item.category] || 'Source detail to confirm'));
                  let description = String(item.description || '');
                  description = description.replace(/UNSUPPORTED_MAPPING_IMPLEMENTATION for /g, 'Mapping behavior to verify for ')
                    .replace(/Source Qualifier writer: the application has not generated a full implementation\./g,
                      'The saved notebook draft should be checked against the captured query and target behavior.')
                    .replace(/Source Qualifier writer:/g, 'Source query:')
                    .replace(/no approved runtime binding/g, 'no configured runtime binding')
                    .replace(/DEMO ASSUMPTION:/g, 'Assumption to verify:');
                  card.append(node('p', description));
                  const taskNames = items(item.proposed_task_ids).map(taskId => {
                    const task = items(value.design.tasks).find(candidate => candidate.id === taskId);
                    return task?.label || taskId;
                  });
                  if (taskNames.length) card.append(node('p', `Related task: ${taskNames.join(', ')}`, 'small muted'));
                  const sources = items(item.evidence_sources);
                  if (sources.length) {
                    card.append(node('strong', 'Source evidence'));
                    const list = node('ul', '', 'proposal-review-list');
                    sources.forEach(source => list.append(node('li', `${source.name} · ${source.kind} · ${source.location}`)));
                    card.append(list);
                  }
                  questions.append(card);
                }
                output.append(disclosure('Notebook review notes', questions));
              }
              const stagedArtifacts = result.staged_artifacts && typeof result.staged_artifacts === 'object'
                && !Array.isArray(result.staged_artifacts) ? result.staged_artifacts : {};
              const revisions = result.notebook_revisions || {};
              const returned = Object.fromEntries(Object.entries(revisions).map(([path, revision]) => [path, revision.content]));
              const previewArtifacts = {...stagedArtifacts, ...result.artifacts, ...returned};
              const hasReturnedEdits = Object.keys(returned).length > 0;
              if (hasReturnedEdits) output.append(node('p', 'Showing returned notebook revisions. Original generation receipts and Jobs YAML are unchanged. Edited code requires fresh review and Job parameter validation before execution or deployment.', 'notice'));
              const runCandidate = {jobId: id, receiptId: job.output.run_id, version: value.version, ticket};
              onSavedArtifacts?.(result.manifest, previewArtifacts, runCandidate);
              document.dispatchEvent(new CustomEvent('orion:selected-job-candidate', {detail: hasReturnedEdits ? null : {
                job_id: id, name: value.design.title, project_id: ticket.scope.project,
                package_hash: value.package_hash, workflow_id: value.workflow_id, scenario: value.scenario,
                design_id: value.id, design_version: value.version, design_sha256: result.manifest.design_sha256,
                input_version: value.input_version, manifest_sha256: job.manifest_sha256, profile: 'labs'
              }}));
              const nativeView = node('section', '', 'section-gap'); nativeView.id = 'native-job-task-view';
              if (result.native_job && typeof result.native_job.yaml === 'string'
                  && result.native_job.execution_allowed === false && result.native_job.deployment_allowed === false) {
                showArtifactPreviews({'migration-job.yaml': result.native_job.yaml}, nativeView, {portableText: true, active});
              }
              output.append(nativeView);
              const committedFiles = Object.fromEntries(Object.entries(result.artifacts)
                .filter(([path]) => !path.endsWith('.ipynb')));
              if (Object.keys(committedFiles).length)
                showArtifactPreviews(committedFiles, output, { active, sectionTitle: 'Other saved files', runCandidate });
              if (Object.keys(stagedArtifacts).length) {
                const stagedFiles = Object.fromEntries(Object.entries(stagedArtifacts)
                  .filter(([path]) => !path.endsWith('.ipynb')));
                if (Object.keys(stagedFiles).length)
                  showArtifactPreviews(stagedFiles, output, { active, sectionTitle: 'Other staged files', runCandidate });
              }
              const jobSection = node('fieldset', '', 'section-gap review-action-card');
              jobSection.append(node('h4', 'Native Jobs draft'), node('p', 'Build a Jobs YAML draft from finalized notebooks. Missing code, unsupported tasks or conditions remain blocked. This does not upload or run the job.', 'small muted'));
              const root = field('input', 'native-job-workspace-root', 'Proposed /Workspace destination', jobSection);
              const buildJob = button('native-job-build', 'Build Jobs draft', jobSection);
              const jobBlocker = hasReturnedEdits
                ? 'Returned edits are unapproved. Complete fresh code review and parameter validation before generating native Jobs YAML.'
                : result.native_job_blocker || 'No Jobs document is created until requested.';
              const jobMessage = node('p', jobBlocker, 'small muted'); jobSection.append(jobMessage);
              buildJob.addEventListener('click', async () => {
                if (!active() || jobSection.disabled || !valid()) return;
                jobSection.disabled = true; buildJob.disabled = true;
                try {
                  const jobDraft = await request(`/api/jobs/${encodeURIComponent(id)}/native-job-draft`, ticket,
                    {workspace_root: root.value, consent: true});
                  if (!active()) return;
                  if (jobDraft.job_id !== id || jobDraft.design_id !== value.id || jobDraft.design_version !== value.version
                      || jobDraft.execution_allowed !== false || jobDraft.deployment_allowed !== false || typeof jobDraft.yaml !== 'string')
                    throw new Error('Jobs draft scope mismatch.');
                  jobMessage.textContent = 'Draft only. Notebook upload, workspace compatibility, semantic parity, execution and deployment are not verified.';
                  onSavedArtifacts?.(result.manifest, previewArtifacts, runCandidate,
                    {jobs: jobDraft.native_jobs, yaml: jobDraft.yaml, workspaceRoot: root.value});
                  nativeView.replaceChildren();
                  showArtifactPreviews({'migration-job.yaml': jobDraft.yaml}, nativeView, {active});
                } catch (error) { if (active()) jobMessage.textContent = `Jobs draft blocked: ${safe(error.message)}`; }
                finally { if (active()) { jobSection.disabled = false; syncNative(); } }
              });
              const publishJob = button('native-bundle-publish', 'Publish immutable draft bundle', jobSection);
              const bundleId = document.createElement('input'); bundleId.type = 'hidden'; bundleId.id = 'native-bundle-id'; jobSection.append(bundleId);
              const readBundle = button('native-bundle-read', 'Read saved draft bundle', jobSection); readBundle.hidden = true;
              const listBundles = button('native-bundle-list', 'Find saved draft bundles', jobSection);
              const nativeJobBlocked = hasReturnedEdits || !result.native_job || !!result.native_job_blocker;
              function syncNative() {
                root.hidden = root.previousElementSibling.hidden = nativeJobBlocked;
                buildJob.hidden = publishJob.hidden = nativeJobBlocked;
                buildJob.disabled = publishJob.disabled = nativeJobBlocked || !valid() || !active();
              }
              syncNative();
              function showBundle(bundle) {
                if (!/^[a-f0-9]{64}$/.test(bundle.bundle_id || '')
                    || bundle.receipt?.job_id !== id || bundle.receipt?.design_id !== value.id
                    || bundle.receipt?.design_version !== value.version
                    || bundle.receipt?.package_hash !== value.package_hash
                    || bundle.receipt?.workflow_id !== value.workflow_id || bundle.receipt?.input_version !== value.input_version
                    || bundle.receipt?.run_id !== job.output?.run_id
                    || bundle.execution_allowed !== false || bundle.deployment_allowed !== false
                    || !bundle.artifacts || typeof bundle.artifacts !== 'object')
                  throw new Error('Draft bundle scope mismatch.');
                bundleId.value = bundle.bundle_id;
                jobMessage.textContent = 'Saved draft bundle verified. No workspace upload, deployment, execution or output parity is established.';
                onSavedArtifacts?.(result.manifest, {...bundle.artifacts, ...returned}, runCandidate,
                  {jobs: bundle.native_jobs, yaml: bundle.artifacts['migration-job.yaml'], workspaceRoot: bundle.receipt.workspace_root});
                nativeView.replaceChildren();
                const bundleFiles = Object.fromEntries(Object.entries(bundle.artifacts)
                  .filter(([path]) => !path.endsWith('.ipynb')));
                showArtifactPreviews(bundleFiles, nativeView, {active, sectionTitle: 'Bundle files'});
                document.dispatchEvent(new CustomEvent('orion:reviewed-bundle', {detail: hasReturnedEdits ? null : {
                  project: ticket.scope.project, package: value.package_hash, scenario: value.scenario,
                  workflow_id: value.workflow_id, design_id: value.id, design_version: value.version,
                  name: [...agentWorkflow.options].find(option => option.value === value.workflow_id)?.textContent || 'Saved workflow',
                  job_id: id, bundle_id: bundle.bundle_id, cohort_id: savedCohortId() || null
                }}));
              }
              publishJob.addEventListener('click', async () => {
                if (!active() || jobSection.disabled || !valid()) return;
                jobSection.disabled = true; publishJob.disabled = true;
                try {
                  const path = `/api/jobs/${encodeURIComponent(id)}/native-bundles`;
                  const body = {workspace_root: root.value, consent: true};
                  const hosted = window.orionHostedIntake && await window.orionHostedIntake.probe();
                  let receipt;
                  if (hosted) {
                    const bytes = crypto.getRandomValues(new Uint8Array(16));
                    const operation = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
                    const headers = ticket.scope.project ? {'X-Workbench-Project': ticket.scope.project, 'X-Workbench-Package': ticket.scope.package} : {};
                    receipt = await window.orionHostedIntake.submit(path, body, headers, operation);
                  } else receipt = await request(path, ticket, body);
                  if (!active()) return;
                  if (!/^[a-f0-9]{64}$/.test(receipt?.bundle_id || '') || receipt.receipt?.job_id !== id)
                    throw new Error('Publication receipt scope mismatch.');
                  bundleId.value = receipt.bundle_id;
                  const bundle = await request(`${path}/${receipt.bundle_id}`, ticket);
                  if (active()) showBundle(bundle);
                } catch (error) {
                  if (active()) jobMessage.textContent = `Publication not confirmed: ${safe(error.message)}. Inspect saved state before retrying.`;
                } finally { if (active()) { jobSection.disabled = false; syncNative(); } }
              });
              readBundle.addEventListener('click', async () => {
                if (!active() || !/^[a-f0-9]{64}$/.test(bundleId.value)) { jobMessage.textContent = 'Enter a saved bundle ID.'; return; }
                readBundle.disabled = true;
                try {
                  const bundle = await request(`/api/jobs/${encodeURIComponent(id)}/native-bundles/${bundleId.value}`, ticket);
                  if (active()) showBundle(bundle);
                } catch (error) { if (active()) jobMessage.textContent = `Saved bundle unavailable: ${safe(error.message)}`; }
                finally { if (active()) readBundle.disabled = false; }
              });
              listBundles.addEventListener('click', async () => {
                if (!active()) return;
                listBundles.disabled = true;
                try {
                  const inventory = await request(`/api/jobs/${encodeURIComponent(id)}/native-bundles`, ticket);
                  if (!active()) return;
                  if (inventory.job_id !== id || !Array.isArray(inventory.bundles)) throw new Error('Bundle inventory scope mismatch.');
                  jobMessage.textContent = inventory.bundles.length
                    ? inventory.bundles.map(item => `${safe(item.bundle_id)}: ${safe(item.status)}`).join(' · ')
                    : 'No saved bundles found. An interrupted operation still requires inspection before retrying.';
                  let firstSaved = null;
                  for (const saved of inventory.bundles) {
                    if (saved.status !== 'draft_unverified') continue;
                    const open = button('', 'Open saved native task grid', jobSection); open.removeAttribute('id');
                    open.addEventListener('click', async () => {
                      if (!active()) return; open.disabled = true;
                      try { const bundle = await request(`/api/jobs/${encodeURIComponent(id)}/native-bundles/${saved.bundle_id}`, ticket); if (active()) showBundle(bundle); }
                      catch (error) { if (active()) jobMessage.textContent = safe(error.message); }
                      finally { if (active()) open.disabled = false; }
                    });
                    firstSaved ||= open;
                  }
                  firstSaved?.click();
                } catch (error) { if (active()) jobMessage.textContent = `Bundle inventory unavailable: ${safe(error.message)}`; }
                finally { if (active()) listBundles.disabled = false; }
              });
              const nativeDetails = disclosure('Advanced: complete Job package', jobSection);
              nativeDetails.hidden = nativeJobBlocked;
              output.append(nativeDetails);
              const bundleInventoryBlocked = nativeJobBlocked || !canDraft || result.manifest?.status !== 'generation_complete';
              if (bundleInventoryBlocked) {
                listBundles.hidden = readBundle.hidden = true;
                jobMessage.textContent = 'Complete Job package finalization is unavailable for this receipt. Individual notebook controls remain in the task workspace.';
              } else {
                listBundles.click();
              }
              message.textContent = 'Saved writer result opened. Its exact notebook files are linked to the DAG above.';
            } catch (error) {
              if (active()) { message.textContent = safe(error.message); read.disabled = false; }
            }
          });
          // A confirmed completed writer receipt hands off directly to its saved
          // artifacts. This performs only bounded reads, never model inference.
          read.click();
          sync(); return;
        }
        if (attempt >= 300) {
          progress?.finish('Polling stopped. Completion is unconfirmed; inspect the saved job before retrying.');
          pending = false; message.textContent += ' Polling stopped. Completion is unconfirmed; no retry was submitted.'; sync(); return;
        }
        setTimeout(() => poll(id, attempt + 1, progress), 2000);
      } catch (error) {
        progress?.finish('Polling interrupted. Completion is unconfirmed; inspect the saved job before retrying.');
        if (active()) { pending = false; message.textContent = `Draft job ${id}: polling interrupted. ${safe(error.message)} No retry submitted.`; sync(); }
      }
    }
    openSaved.addEventListener('click', async () => {
      sync(); if (openSaved.disabled) return;
      savedJob.value = savedPicker.value;
      if (!/^[a-f0-9]{32}$/.test(savedJob.value)) return;
      pending = true; output.replaceChildren(); sync();
      await poll(savedJob.value);
    });
    savedPicker.addEventListener('change', sync);
    findSaved.addEventListener('click', async () => {
      sync(); if (findSaved.disabled) return;
      pending = true; sync(); savedPicker.replaceChildren(); savedPicker.append(new Option('Loading saved results…', ''));
      try {
        const result = await request(`/api/redesigns/${encodeURIComponent(value.id)}?version=${value.version}&draft_jobs=1`, ticket);
        if (!active()) return;
        if (result.id !== value.id || result.version !== value.version || result.package_hash !== value.package_hash
            || result.workflow_id !== value.workflow_id || result.scenario !== value.scenario || !Array.isArray(result.draft_jobs))
          throw new Error('Saved code inventory scope mismatch.');
        result.draft_jobs.forEach(job => accept(job));
        const codeNote = savedCodeNotes.get(value.workflow_id);
        if (codeNote) codeNote.textContent = result.draft_jobs.length
          ? `Design v${value.version}: saved notebook results found (${result.draft_jobs.map(job => job.status).join(', ')}). Open a result to inspect its files.`
          : `Design v${value.version}: no saved notebook files were found for this exact input version.`;
        const codeState = detail.querySelector('#saved-design-code-state');
        if (codeState) savedCodeState(codeState, result.draft_jobs.length
          ? `Design v${value.version}: loading saved notebooks.`
          : `Design v${value.version}: no saved notebook files were found for this exact input version.`);
        existingNote.textContent = result.draft_jobs.length
          ? 'Saved writer results belong to this exact design. Select one result to inspect its files.'
          : 'No saved writer result was found for this exact design and input version.';
        autoReadJob = null;
        savedPicker.replaceChildren(new Option(result.draft_jobs.length ? 'Choose a saved writer result' : 'No saved writer results', ''));
        for (const job of result.draft_jobs) {
          if (job.status === 'completed' && !autoReadJob) autoReadJob = job.id;
          if (!/^[a-f0-9]{32}$/.test(job.id) || job.design_id !== value.id || job.design_version !== value.version) throw new Error('Saved job identity mismatch.');
          const created = String(job.created_at || '').replace('T', ' ').slice(0, 19);
          const label = job.output?.status === 'blocked' ? 'Finalization blocked'
            : job.output?.status === 'drafts_saved' ? 'Partial writer result'
              : job.status === 'completed' ? 'Saved writer receipt' : job.status;
          savedPicker.append(new Option(`${label} · ${created || job.id.slice(0, 8)}`, job.id));
        }
        draft.hidden = result.draft_jobs.some(job => job.status === 'completed');
        if (autoReadJob) savedPicker.value = autoReadJob;
      } catch (error) { if (active()) {
        existingNote.textContent = `Saved code unavailable: ${safe(error.message)}`;
        savedPicker.replaceChildren(new Option('Saved results unavailable', ''));
        const codeState = detail.querySelector('#saved-design-code-state');
        if (codeState) savedCodeState(codeState, 'Saved notebooks could not be loaded. Refresh to check again.');
      } }
      finally {
        if (active()) {
          pending = false; sync();
          if (autoReadJob) openSaved.click();
        }
      }
    });
    if (!canDraft) message.textContent = 'Draft generation is unavailable because this design is stale or its current input snapshot could not be verified.';
    draft.addEventListener('click', async () => {
      sync(); if (draft.disabled || !active()) return;
      pending = true; output.replaceChildren(); sync();
      message.textContent = 'Submitting the requested draft. Hosted persistence may delay acknowledgment.';
      let progress = null;
      try {
        const path = `/api/redesigns/${encodeURIComponent(value.id)}/spark-drafts`;
        const body = { version: value.version, consent: true };
        const hosted = window.orionHostedIntake && await window.orionHostedIntake.probe();
        if (!active()) return;
        let response;
        if (hosted) {
          const headers = {};
          if (ticket.scope.project) {
            headers['X-Workbench-Project'] = ticket.scope.project;
            headers['X-Workbench-Package'] = ticket.scope.package;
          }
          response = await window.orionHostedIntake.submit(path, body, headers);
        } else {
          progress = localDraftProgress();
          response = await request(path, ticket, body);
        }
        const job = accept(response);
        progress?.update(`Draft job ${job.id}: ${job.status}. No deployment was performed.`);
        if (!active()) { progress?.finish('Tracking stopped after the selection changed. Inspect the saved job record before retrying.'); return; }
        message.textContent = `Draft job ${job.id}: ${job.status}. Polling the package-scoped job record.`;
        await poll(job.id, 0, progress);
      } catch (error) {
        progress?.finish('Request outcome unconfirmed. Inspect the saved job record before retrying; no automatic retry was made.');
        if (active()) {
          pending = false; message.textContent = `Draft outcome unconfirmed. ${safe(error.message)} No automatic retry. Refresh actual records before another request.`; sync();
        }
      }
    });
    sync.findSaved = () => findSaved.click();
    sync(); return sync;
  }
  function savedCodeState(element, text) {
    element.textContent = text;
    window.orionProseFormat?.focusStatic(element);
  }
  function render(value, ticket, serial) {
    delete detail.dataset.refreshUnconfirmed;
    detail.replaceChildren(); const design = value.design;
    const evidenceReferences = new Map(items(value.evidence_references).map(ref => [ref.id, ref]));
    const workflowName = [...agentWorkflow.options].find(option => option.value === value.workflow_id)?.textContent || 'Selected workflow';
    detail.append(node('h3', design.title), node('p', workflowName, 'proposal-review-workflow'),
      node('p', `Source: ${value.scenario === 'bronze' ? 'Unity Catalog' : 'Original source systems'} · Version: ${value.version} · Review ${value.review_version} · ${String(value.status).replaceAll('_', ' ')}`, 'small muted'),
      node('p', `Design v${value.version} is saved. Review its code here, use Open in Databricks for native serverless cell execution, or confirm deployment of this selected Job from CI/CD. Runtime parity is unverified.`, 'disclosure'));
    const notebookWorkspace = node('section', '', 'section-gap'); notebookWorkspace.id = 'notebook-workspace';
    notebookWorkspace.setAttribute('aria-label', 'Notebook review, download and run');
    notebookWorkspace.append(node('h3', 'Notebooks: review, download and run'));
    const workflowScopeNote = node('div', '', 'workflow-scope-call-note notice');
    workflowScopeNote.id = 'workflow-scope-call-note'; workflowScopeNote.setAttribute('role', 'status');
    notebookWorkspace.append(workflowScopeNote);
    const codeState = node('p', '', 'disclosure');
    codeState.id = 'saved-design-code-state'; codeState.setAttribute('role', 'status');
    savedCodeState(codeState, 'Checking for saved notebooks for this design.');
    if (value.version !== latestVersion) detail.append(node('p', 'Historical version, read-only. Select the latest version before recording a review.', 'notice'));
    if (value.stale) detail.append(node('p', 'This saved design uses an earlier input snapshot. Changes may include agent guidance or supporting evidence, not necessarily the source workflow. Saved code remains available to review; a current design is required to generate new drafts.', 'notice'));
    const graphPanel = node('section', '', 'section-gap'); graphPanel.id = 'saved-design-graph';
    const artifactPanel = node('section', '', 'section-gap'); artifactPanel.id = 'saved-design-artifacts';
    const writerControls = node('div'); writerControls.id = 'saved-writer-controls';
    graphPanel.dataset.designVersion = String(value.version);
    notebookWorkspace.append(writerControls, graphPanel, codeState, disclosure('Saved files and writer report', artifactPanel,
      'Job YAML, planned file paths, review notes, and complete-package controls. Notebook code stays in the selected task above.'));
    detail.append(notebookWorkspace);
    let taskGraph = graph(design, serial, evidenceReferences, graphPanel);
    let sourceCallContext = null;
    const markExternalTasks = () => {
      const ownerIds = new Set(items(sourceCallContext?.invocations?.items)
        .filter(call => call.direction === 'outward').map(call => call.command_owner_id));
      let marked = false;
      for (const task of items(design.tasks)) {
        const externalCall = items(task.evidence_ids).some(id => ownerIds.has(id));
        const card = [...graphPanel.querySelectorAll('.workflow-native-card')]
          .find(item => item.getAttribute('data-task-key') === task.id);
        if (!card || !externalCall) continue;
        card.dataset.externalPlaceholder = 'true';
        card.setAttribute('aria-label', `Open task ${task.id} (external workflow call, child workflow not implemented in this conversion)`);
        marked = true;
      }
      graphPanel.querySelector('.workflow-external-call-legend')?.remove();
      if (marked) graphPanel.prepend(node('p', 'Amber: external workflow call. The called workflow’s notebook is not implemented by this single-workflow conversion.', 'workflow-external-call-legend small'));
    };
    loadWorkflowScopeNote(workflowScopeNote, value, ticket, serial, workflowName, context => {
      sourceCallContext = context;
      markExternalTasks();
    });
    markExternalTasks();
    const metadata = node('div');
    const yamlView = node('details');
    yamlView.id = 'proposal-yaml-preview';
    yamlView.append(node('summary', 'Proposal YAML preview (logical outline, not deployable DAB YAML)'),
      node('p', 'This YAML contains the submitted task plan, evidence coverage, dependencies and unresolved items. It does not contain executable task code and is not a Databricks Asset Bundle.', 'small muted'),
      codePreview(renderProposalYaml(value), 'yaml'));
    artifactPanel.append(yamlView);
    const notebookPlans = items(design.artifact_plan).filter(item => item.artifact_kind === 'notebook');
    if (notebookPlans.length) {
      const planSection = node('section', '', 'notebook-plan-section');
      planSection.setAttribute('aria-label', 'Planned notebook files');
      planSection.append(node('p', 'Planned paths from the design. Select a path to inspect its associated task and any saved notebook draft.', 'small muted'));
      const planList = node('ul', '', 'notebook-plan-list');
      for (const plan of notebookPlans) {
        const row = node('li', '', 'notebook-plan-item');
        const targets = items(plan.proposed_task_ids).map(taskId =>
          items(design.tasks).find(task => task.id === taskId)).filter(Boolean);
        row.append(node('span', targets.map(task => task.label || task.id).join(', ') || 'Unmapped task', 'notebook-plan-task'));
        for (const task of targets) {
          const pathLink = node('button', plan.relative_path, 'notebook-plan-link');
          pathLink.type = 'button';
          pathLink.setAttribute('aria-label', `Open task ${task.label || task.id} for planned notebook ${plan.relative_path}`);
          pathLink.addEventListener('click', () => {
            if (!taskGraph?.openTask(task.id)) {
              codeState.textContent = `Task ${task.label || task.id} is not available in the current graph. The planned path remains unchanged.`;
            }
          });
          row.append(pathLink);
        }
        if (!targets.length) row.append(node('code', plan.relative_path, 'notebook-plan-path'));
        planList.append(row);
      }
      planSection.append(planList);
      artifactPanel.append(disclosure(`Notebook paths listed in design (${notebookPlans.length})`, planSection));
    } else {
      artifactPanel.append(node('p', 'This design has no optional notebook path plan. Generated notebook paths are read from its saved writer result and linked to the task cards above.', 'small muted'));
    }
    const metadataStart = detail.childNodes.length;
    detail.append(node('h3', 'Rationale'), node('p', design.rationale, 'prose'));
    for (const [title, key] of [['Alternatives and tradeoffs', 'alternatives'], ['Original behavior coverage', 'coverage'], ['Parameters', 'parameters'], ['Reuse groups', 'reuse_groups'], ['Proposed constraints', 'proposed_constraints'], ['Blockers', 'blockers'], ['Design evidence sources', 'evidence_ids']])
      section(title, humanizeEvidence(design[key], evidenceReferences));
    section('Source questions to check after notebook generation', humanizeEvidence(items(design.scoped_unresolved).filter(item => item.impact === 'human_review'), evidenceReferences));
    section('Other scoped unresolved behavior', humanizeEvidence(items(design.scoped_unresolved).filter(item => item.impact !== 'human_review'), evidenceReferences));
    section('Append-only review history', value.reviews);
    if (value.artifact_completeness?.issues?.length)
      section('Design details to check while reviewing generated notebooks', value.artifact_completeness.issues);
    json('Actual stored redesign JSON', value);
    const downloads = node('div', '', 'button-row'); detail.append(downloads);
    button('redesign-download', 'Download actual redesign JSON', downloads).addEventListener('click', () => download(value, 'redesign.json', ticket, serial));
    button('redesign-download-graph', 'Download actual logical graph JSON', downloads).addEventListener('click', () => download({ tasks: design.tasks, edges: design.edges }, 'redesign-graph.json', ticket, serial));
    const form = node('form', '', 'stack section-gap'); form.id = 'redesign-review-form';
    form.append(node('h3', 'Optional design feedback'), node('p', 'Notebook generation does not require this review. You can inspect the saved notebook drafts first, then leave a comment or request a design change if needed.', 'small muted'));
    const comment = field('textarea', 'redesign-review-comment', 'Nonsecret review comment', form); comment.required = true; comment.maxLength = 2400; comment.rows = 3;
    const buttons = node('div', '', 'button-row'); form.append(buttons);
    const validVersions = Number.isInteger(value.review_version) && value.review_version >= 0 && /^[a-f0-9]{64}$/.test(value.current_input_version);
    const reviewButtons = [];
    for (const [action, caption] of [['comment', 'Comment'], ['request_changes', 'Request design changes'], ['reject', 'Reject design']]) {
      const b = button(`redesign-${action}`, caption, buttons); b.type = 'submit'; b.name = 'action'; b.value = action; reviewButtons.push(b);
    }
    function syncReview() {
      for (const b of reviewButtons) b.disabled = busy || loading || detailLoading || !valid() || page.hidden || !workflowKnown(value.workflow_id) || !current(ticket) || serial !== detailTicket || record !== value
        || !validVersions || value.version !== latestVersion || !comment.value.trim()
        || (b.value === 'approve_for_drafting' && (value.stale !== false || value.input_version !== value.current_input_version || items(design.blockers).length > 0));
    }
    comment.addEventListener('input', syncReview);
    form.addEventListener('submit', async event => {
      event.preventDefault(); const action = event.submitter?.value;
      syncReview(); if (!action || event.submitter.disabled || !form.reportValidity()) return;
      if (safe(comment.value) !== comment.value) { status.textContent = 'Remove credential-shaped material from the comment before submitting.'; return; }
      busy = true; controls(); syncReview();
      status.textContent = 'Recording a version-checked outline review. No execution is authorized.';
      try {
        const result = await request(`/api/redesigns/${encodeURIComponent(value.id)}/reviews`, ticket, {
          action, comment: comment.value, expected_version: value.version, expected_review_version: value.review_version,
          expected_input_version: value.current_input_version, consent: true });
        if (!current(ticket) || serial !== detailTicket) return;
        if (!scoped(result.redesign, ticket) || result.redesign.id !== value.id || result.redesign.version !== value.version)
          throw new Error('Review response scope mismatch. Refresh actual records.');
        record = result.redesign; busy = false; render(record, ticket, serial);
        status.textContent = 'Review recorded by service. Outline-only decision, no code generation or deployment.';
      } catch (error) {
        if (current(ticket) && serial === detailTicket) {
          record = null; status.textContent = error.name === 'AbortError'
            ? 'Review interrupted. Outcome unknown. Refresh actual records before retrying.' : safe(error.message);
        }
      } finally {
        if (current(ticket) && serial === detailTicket) { busy = false; controls(); syncReview(); }
      }
    });
    // Keep design commentary and feedback once, after the DAG and saved code.
    for (const child of [...detail.childNodes].slice(metadataStart)) metadata.append(child);
    const reviewContent = node('div'); reviewContent.append(metadata, form);
    detail.append(disclosure('Design explanation and feedback', reviewContent,
      'Why this design was proposed, its source coverage and limitations, and optional feedback.'));
    const syncDraft = draftControls(value, ticket, serial, writerControls, (manifest, artifacts, runCandidate, native = null) => {
      graphPanel.replaceChildren();
      if (native) {
        taskGraph = null;
        nativeJobGraphs(native.jobs, native.yaml, artifacts, graphPanel, native.workspaceRoot, runCandidate);
      } else taskGraph = graph(design, serial, evidenceReferences, graphPanel, artifacts, manifest, runCandidate);
      markExternalTasks();
      artifactPanel.querySelector('#saved-notebook-paths')?.remove();
      const notebookPaths = Object.keys(artifacts).filter(path => path.endsWith('.ipynb'));
      const savedPaths = node('section', '', 'notebook-plan-section'); savedPaths.id = 'saved-notebook-paths';
      savedPaths.append(node('h4', `Saved notebook files (${notebookPaths.length})`));
      const list = node('ul', '', 'notebook-plan-list');
      for (const path of notebookPaths) {
        const row = node('li', '', 'notebook-plan-item');
        const owners = items(manifest.tasks).filter(task => items(task.artifact_paths).includes(path));
        if (owners.length) {
          const open = node('button', path, 'secondary compact'); open.type = 'button';
          open.addEventListener('click', () => {
            if (!current(ticket) || serial !== detailTicket || page.hidden) return;
            const card = [...graphPanel.querySelectorAll('.workflow-native-card')]
              .find(item => item.getAttribute('data-task-key') === owners[0].task_id);
            card?.dispatchEvent(new MouseEvent('click', {bubbles: true}));
          });
          row.append(open);
        } else row.append(node('span', path));
        list.append(row);
      }
      savedPaths.append(list);
      yamlView.after(savedPaths);
    }, artifactPanel);
    reviewControls = () => { syncReview(); syncDraft(); }; reviewControls();
    requestAnimationFrame(() => loadSavedDrafts());
    const exactRunHandoff = submittedLink?.key === ticket.key && submittedLink.id === value.id
      && submittedLink.version === value.version && submittedLink.record_id === value.source?.record_id;
    if (exactRunHandoff) {
      const readCandidate = (attempt = 0) => {
        if (!current(ticket) || serial !== detailTicket || page.hidden) return;
        const find = document.getElementById('spark-draft-find');
        if (find?.disabled) {
          if (attempt < 120) requestAnimationFrame(() => readCandidate(attempt + 1));
          return;
        }
        syncDraft.findSaved();
      };
      requestAnimationFrame(() => readCandidate());
    }
    // The list read may still own the loading flag until its finally block.
    queueMicrotask(() => { if (current(ticket) && serial === detailTicket) syncReview(); });
  }
  function consentToCohort(ticket) {
    return new Promise(resolve => {
      const dialog = node('dialog', '', 'review-action-card'); dialog.id = 'cohort-agent-start-dialog';
      dialog.setAttribute('aria-labelledby', 'cohort-agent-start-title');
      const title = node('h3', 'Design cohort'); title.id = 'cohort-agent-start-title';
      dialog.append(title, node('p', 'Start the selected workflow designs on the configured model endpoint. Each workflow is recorded separately. No execution or deployment.', 'small muted'));
      const actions = node('div', '', 'button-row'); dialog.append(actions);
      const cancel = button('cohort-agent-cancel', 'Cancel', actions);
      const start = button('cohort-agent-confirm', 'Start scoped design worker', actions); start.disabled = !current(ticket);
      function finish(approved) {
        if (closeCohortConsent === finish) closeCohortConsent = null;
        dialog.close(); dialog.remove(); resolve(approved);
      }
      cancel.addEventListener('click', () => finish(false));
      dialog.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
      start.addEventListener('click', () => { if (current(ticket)) finish(true); });
      closeCohortConsent = finish;
      document.body.append(dialog); dialog.showModal(); cancel.focus();
    });
  }
  generate.addEventListener('click', async () => {
    capture(); const scope = source();
    if (generate.disabled || !valid() || !workflowKnown(scope.workflow)) return;
    if (targetScope.value === 'cohort') {
      const navigation = document.querySelector('.navigation [data-page="agents"]');
      if (!navigation || navigation.disabled) return;
      navigation.click();
      agentWorkflow.value = scope.workflow; $('agent-scenario').value = scope.scenario;
      document.dispatchEvent(new CustomEvent('orion:open-cohort-review', {detail: {
        workflow_id: scope.workflow, scenario: scope.scenario}}));
      return;
    }
    const navigation = document.querySelector('[data-page="agents"]');
    if (!navigation) return;
    navigation.click();
    agentWorkflow.value = scope.workflow; $('agent-scenario').value = scope.scenario;
    for (const element of [agentWorkflow, $('agent-scenario'), $('agent-prompt')]) {
      element.dispatchEvent(new Event('input', { bubbles: true })); element.dispatchEvent(new Event('change', { bubbles: true }));
    }
    $('agent-prompt').focus();
    status.textContent = 'Request prepared in Agent activity. Click Invoke agents to submit this workflow to the configured endpoint. No run started yet.';
  });
  workflowDraft.addEventListener('click', async () => {
    const ticket = capture();
    if (busy || loading || !valid() || !workflowKnown(ticket.scope.workflow) || !compilerSupported(ticket.scope) || compilerDiagnostic || page.hidden) return;
    const serial = ++workflowTicket;
    workflowOutput.replaceChildren();
    if (ticket.scope.scenario !== 'bronze') {
      workflowStatus.textContent = 'Original mode unsupported. Select bronze; original Oracle connections, partition DDL and transaction equivalence require a separate implementation.';
      controls(); return;
    }
    busy = true; controls();
    workflowStatus.textContent = 'Generating deterministic in-memory workflow text from the active canonical model. No model invocation, execution or deployment.';
    try {
      const result = await request('/api/proposals/workflow-artifacts', ticket,
        { workflow_id: ticket.scope.workflow, scenario: ticket.scope.scenario, consent: true });
      if (!current(ticket) || serial !== workflowTicket || page.hidden) return;
      if (result.workflow_id !== ticket.scope.workflow || result.scenario !== ticket.scope.scenario
          || result.ready_for_execution !== false || result.deployment_allowed !== false
          || result.execution_allowed !== false || result.approved !== false
          || !result.artifacts || Array.isArray(result.artifacts) || typeof result.artifacts !== 'object'
          || Object.values(result.artifacts).some(value => typeof value !== 'string')
          || !['tasks', 'parameters', 'parameter_inventory', 'deployment_requirements'].every(name => Array.isArray(result[name])))
        throw new Error('Workflow response scope or artifact contract mismatch. No output accepted.');
      renderWorkflowArtifacts(result, ticket, serial);
      retainedExample = {key: ticket.key, result};
      workflowStatus.textContent = 'Example Job and notebooks are ready below. Select a task to view its code. This is not an agent-generated design.';
    } catch (error) {
      if (current(ticket) && serial === workflowTicket) workflowStatus.textContent = error.name === 'AbortError'
        ? 'Generation interrupted or timed out. No output accepted. Explicitly approve before retrying.' : safe(error.message);
    } finally {
      if (current(ticket) && serial === workflowTicket) { busy = false; controls(); }
    }
  });
  sqlDraft.addEventListener('click', async () => {
    const ticket = capture();
    if (busy || loading || !valid() || !workflowKnown(ticket.scope.workflow) || page.hidden) return;
    const serial = ++sqlTicket;
    busy = true; sqlOutput.replaceChildren(); controls();
    sqlStatus.textContent = 'Drafting captured SQL only. No model invocation, execution or deployment.';
    try {
      const result = await request('/api/proposals/sql-drafts', ticket, { workflow_id: ticket.scope.workflow, consent: true });
      if (!current(ticket) || serial !== sqlTicket) return;
      if (result.workflow_id !== ticket.scope.workflow || result.manifest?.workflow_id !== ticket.scope.workflow
          || result.deployment_allowed !== false || !result.artifacts || Array.isArray(result.artifacts)
          || typeof result.artifacts !== 'object' || Object.values(result.artifacts).some(value => typeof value !== 'string'))
        throw new Error('SQL drafting response scope or artifact contract mismatch. No output accepted.');
      sqlOutput.append(node('p', `Workflow: ${result.workflow_id} | Manifest status: ${result.manifest.status} | Deployment: not authorized. SQL-only drafts, not a completed workflow.`, 'disclosure'),
        node('p', result.notice));
      section('SQL role statuses (reported by compiler)', items(result.manifest.sql_inventory).map(item => ({
        id: item.id, role: item.role, task_ids: item.task_ids, status: item.status,
        blocker_codes: item.blocker_codes, emitted_artifacts: item.emitted_artifacts
      })), sqlOutput);
      section('Task statuses, including affected and unsupported tasks', result.manifest.tasks, sqlOutput);
      section('Scoped blockers, not a whole-design stop', result.manifest.blockers, sqlOutput);
      json('Actual SQL draft manifest JSON', result.manifest, sqlOutput);
      for (const [name, content] of Object.entries(result.artifacts)) {
        const block = node('details');
        block.append(node('summary', name), codePreview(content, artifactLanguage(name))); sqlOutput.append(block);
      }
      sqlOutput.append(node('p', 'Returned artifact text is displayed as text, never executed. Credential-shaped material is redacted in this view. Non-SQL implementations and full workflow completion are not supplied.', 'small muted'));
      sqlStatus.textContent = 'SQL draft response received. Inspect actual role statuses and scoped blockers. No execution or deployment authorized.';
    } catch (error) {
      if (current(ticket) && serial === sqlTicket) sqlStatus.textContent = error.name === 'AbortError'
        ? 'SQL drafting interrupted or timed out. Outcome unconfirmed; explicitly approve before retrying.' : safe(error.message);
    } finally {
      if (current(ticket) && serial === sqlTicket) { busy = false; controls(); }
    }
  });
  // Let this native change bubble to app.js for baseline selection. The detail
  // observer below only updates a different workflow, avoiding a feedback read.
  document.addEventListener('orion:review-submitted-design', event => {
    const link = event.detail, scope = source();
    if (!link || busy || !valid() || !workflowKnown(link.workflow_id)
        || !/^[a-f0-9]{64}$/.test(link.id) || !Number.isInteger(link.version) || link.version < 1
        || !['original', 'bronze'].includes(link.scenario)
        || (scope.project && scope.package !== link.package_hash)) return;
    workflow.value = link.workflow_id; $('scenario').value = link.scenario;
    agentWorkflow.value = link.workflow_id; $('agent-scenario').value = link.scenario;
    submittedLink = {...link, key: key()};
    invalidate('Opening the exact saved design version submitted by this run. No model invocation.');
    workflow.dispatchEvent(new Event('change', {bubbles: true}));
    $('scenario').dispatchEvent(new Event('change', {bubbles: true}));
    if (page.hidden) document.querySelector('[data-page="proposals"]')?.click();
    else scheduleLoad();
  });
  sourceDetails.addEventListener('toggle', () => { if (sourceDetails.open && !page.hidden && valid()) loadWorkflowUnderstanding(); });
  workflow.addEventListener('change', () => {
    if (workflowKnown(workflow.value)) agentWorkflow.value = workflow.value;
    invalidate(); scheduleLoad();
  });
  document.addEventListener('orion:workflow-selected', event => {
    const selection = event.detail, scope = source();
    if (!selection || selection.projectId !== scope.project
        || (scope.project && selection.packageHash !== scope.package) || !workflowKnown(selection.workflowId)) return;
    workflow.value = selection.workflowId;
    $('scenario').value = selection.scenario;
    if (contextKey !== key()) { invalidate(); scheduleLoad(); }
  });
  document.addEventListener('orion:proposal-selected', event => {
    const id = event.detail?.workflowId;
    if (workflowKnown(id) && workflow.value !== id) {
      workflow.value = id; agentWorkflow.value = id; invalidate(); scheduleLoad();
    }
  });
  versions.addEventListener('change', () => { submittedLink = null; selectVersion(); });
  refresh.addEventListener('click', () => { invalidate('Refreshing saved records. This does not start the agents.', true); load(); });
  previous.addEventListener('click', () => { offset = Math.max(0, offset - 50); load(true); });
  next.addEventListener('click', () => { offset += 50; load(true); });
  document.addEventListener('change', event => {
    if (['agent-workflow', 'agent-scenario'].includes(event.target.id)) {
      if (workflowKnown(agentWorkflow.value)) workflow.value = agentWorkflow.value;
      else workflow.value = '';
      $('scenario').value = $('agent-scenario').value;
      restoreScope();
      if (contextKey !== key()) { invalidate(); scheduleLoad(); }
    }
    if (['scenario', 'project-select', 'project-package', 'estate-package-select'].includes(event.target.id)) {
      if (event.target.id === 'scenario') $('agent-scenario').value = $('scenario').value;
      restoreScope(); invalidate(); scheduleLoad();
    }
  });
  $('refresh')?.addEventListener('click', () => { invalidate('Refreshing saved records. This does not start the agents.', true); if (!page.hidden) load(); });
  new MutationObserver(() => {
    const before = workflow.value;
    syncWorkflows();
    if (contextKey !== key() || before !== workflow.value)
      invalidate('Package workflow inventory changed. Previous review actions were cleared.');
    scheduleLoad();
  }).observe(agentWorkflow, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
  new MutationObserver(() => {
    if (page.hidden) {
      // Leaving during a draft/download revokes its response identity as well
      // as consent. Keep completed read-only views when no action is pending.
      if (busy) {
        invalidate('Proposal page closed. Pending responses were cleared.');
        return;
      }
      // Retain the saved read-only view, but never retain actionable controls.
      panel.querySelectorAll('input[type="checkbox"]').forEach(input => { input.checked = false; });
      if (understandingPending) {
        understandingTicket++; understandingPending = false; understandingKey = '';
        understanding.replaceChildren(node('p', 'Previous selection cleared. Reopen source evidence to read the current workflow.', 'small muted'));
      }
      controls();
    } else {
      syncWorkflows();
      restoreScope();
      if (contextKey !== key()) invalidate();
      if (sourceDetails.open && understandingKey !== key()) loadWorkflowUnderstanding();
      scheduleLoad();
    }
  }).observe(page, { attributes: true, attributeFilter: ['hidden'] });
  new MutationObserver(() => {
    if (contextKey !== key()) {
      restoreScope();
      invalidate('Package identity changed. Previous responses and selections were cleared.');
    } else if (!valid()) oracleExplorer.reset();
    controls();
    if (valid() && !page.hidden) scheduleLoad();
  }).observe(page, { attributes: true, attributeFilter: ['data-package-ready', 'data-package-hash'] });
  new MutationObserver(() => {
    if (agentPage.hidden) oracleExplorer.reset();
    controls();
    if (scopeActive() && valid()) scheduleLoad();
  }).observe(agentPage, {attributes: true, attributeFilter: ['hidden', 'data-package-ready', 'data-package-hash', 'data-agent-run-status']});
  new MutationObserver(() => controls())
    .observe(agentScope, {attributes: true, attributeFilter: ['disabled']});
  new MutationObserver(() => syncScopeWorker())
    .observe($('agent-submit'), {attributes: true, attributeFilter: ['disabled']});
  syncWorkflows(); invalidate('No agent-submitted design loaded; structural baseline separate.');
  if (!page.hidden) load();
  else if (scopeActive()) loadScope();
})();