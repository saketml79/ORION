(() => {
  'use strict';
  const page = document.getElementById('page-support');
  const root = document.getElementById('support-root');
  if (!page || !root) return;

  root.innerHTML = `
    <header class="support-hero">
      <div><span class="support-kicker">SUPPORT / REVIEW WITH CONTEXT</span><h1 id="support-heading">How can we help?</h1><p>Start with your saved agent activity, then explore the evidence and tools already available in ORION.</p></div>
      <div class="support-hero-note"><strong>Nothing runs automatically</strong><span>Opening this page does not call an AI model, submit a request, or upload a file.</span></div>
    </header>
    <div class="support-layout">
      <div class="support-main">
        <article class="panel support-card" aria-labelledby="support-runs-heading">
          <div class="support-card-heading"><div><span class="support-kicker">01 / YOUR CONTEXT</span><h2 id="support-runs-heading">Saved agent runs</h2><p>Read saved history for the selected project and XML, or the selected legacy workspace. Local workspaces do not authenticate individual users.</p></div><button id="support-load-runs" class="secondary" type="button">Load saved runs</button></div>
          <p id="support-context" class="support-callout">Select a project and completed XML, or a legacy workspace, before reading history.</p>
          <div class="support-field"><label for="support-run">Saved run</label><select id="support-run" disabled><option value="">Load saved runs first</option></select></div>
          <div class="support-actions"><button id="support-read-run" class="secondary" type="button" disabled>Read selected run</button><button id="support-previous" class="secondary compact" type="button" disabled>Previous page</button><button id="support-next" class="secondary compact" type="button" disabled>Next page</button></div>
          <p id="support-run-status" class="support-status" role="status" aria-live="polite">No history requested.</p>
          <div id="support-run-detail" class="support-detail" aria-live="polite"></div>
          <p class="support-footnote">Reading history does not start a run. Receipts cover recorded analysis-agent activity, not the separate writer or complete billing.</p>
        </article>
        <article class="panel support-card" aria-labelledby="support-review-heading">
          <span class="support-kicker">02 / REVIEW OPTIONS</span><h2 id="support-review-heading">Ask for a review</h2><p>Request a review of agent actions and cost only when the corresponding service is available. Neither action runs automatically.</p>
          <label class="support-consent"><input id="support-ai-consent" type="checkbox"> I consent to sending this selected run's recorded findings and action/token receipts to the configured validation model for one advisory review. Findings may contain source names. It may incur model charges; estimates are not bills.</label>
          <label class="support-consent"><input id="support-human-consent" type="checkbox"> I consent to sharing this selected run's bounded findings and recorded activity/token estimate with the designated human reviewer, ssuman@bpcs.com. Findings may contain source names; raw XML and generated code are excluded.</label>
          <div class="support-field"><label for="support-ai-review-id">AI review ID (saved result or retry safety)</label><input id="support-ai-review-id" type="text" inputmode="text" maxlength="32" pattern="[a-f0-9]{32}"></div><p class="support-footnote">ORION creates this ID before the request. Reuse it to check an uncertain result without repeating inference. Clear it only to request a separate review.</p><button id="support-load-ai-review" type="button" class="secondary" disabled>Load saved AI review</button>
          <div class="support-choice-grid">
            <section class="support-choice"><span class="support-badge">Consent required</span><h3>AI review</h3><p>Reviews bounded saved findings, recorded model/tool actions, and available token-based list-price estimates. It does not receive source XML or generated code.</p><button id="support-ai-review" type="button" class="secondary" disabled aria-describedby="support-ai-reason">Request AI review</button><p id="support-ai-reason" class="support-footnote">Select and read a completed saved run, then check the one-time consent above.</p></section>
            <section class="support-choice"><span class="support-badge">One-time consent</span><h3>Human review</h3><p>Requests go to an in-app queue for the designated approver. Queue submission requires access to the shared submissions Volume.</p><button id="support-human-review" type="button" class="secondary" disabled aria-describedby="support-human-reason">Request human review</button><p id="support-human-reason" class="support-footnote">Select and read a completed saved run, then check the one-time consent above.</p></section>
          </div>
          <div class="support-callout"><strong>Already available:</strong> Proposal review provides feedback on a proposal, not a request for a human review of run actions or costs.</div>
          <div class="support-actions"><button class="secondary" type="button" data-support-nav="agents">Open Agent activity</button><button class="secondary" type="button" data-support-nav="proposals">Open proposal review</button><button class="secondary" type="button" data-support-nav="application-log-analytics">Open log analytics</button></div>
        </article>
        <article class="panel support-card" aria-labelledby="support-skills-heading">
          <span class="support-kicker">03 / SHARED KNOWLEDGE</span><h2 id="support-skills-heading">Skills and guidance</h2><p>Explore the existing read-only tools and knowledge catalog. A new skill must be vetted and explicitly promoted by an administrator before it becomes available to all users.</p>
          <button id="support-open-catalog" class="secondary" type="button">Open tools and knowledge catalog</button>
          <div class="support-field"><label for="support-skill-file">Markdown skill file (maximum 128 KiB)</label><input id="support-skill-file" type="file" accept=".md,text/markdown" aria-describedby="support-skill-reason"></div><label class="support-consent"><input id="support-skill-consent" type="checkbox"> I consent to staging this Markdown file in my user Volume and submitting a copy for administrator vetting. It will not be available to agents until explicitly promoted.</label><button id="support-skill-submit" type="button" class="secondary" disabled aria-describedby="support-skill-reason">Submit for vetting</button><p id="support-skill-reason" class="support-footnote">Uploads are untrusted Markdown. They are never executed. Shared queue submission may be unavailable until Volume permissions are configured.</p>
        </article>
        <article class="panel support-card" aria-labelledby="support-admin-heading"><span class="support-kicker">04 / ADMINISTRATOR</span><h2 id="support-admin-heading">Review queue and approved skills</h2><p>Only ssuman@bpcs.com can inspect requests, record a decision, and promote vetted skills.</p><div class="support-actions"><button id="support-load-human-queue" type="button" class="secondary">Load human requests</button><button id="support-load-skill-queue" type="button" class="secondary">Load pending skills</button><button id="support-load-approved-skills" type="button" class="secondary">Refresh approved skills</button></div><p id="support-admin-status" class="support-status" role="status" aria-live="polite">No administrator queue requested.</p><div id="support-human-queue" class="support-detail"></div><div id="support-skill-queue" class="support-detail"></div><div id="support-approved-skills" class="support-detail"></div>
        </article>
      </div>
      <aside class="support-side" aria-label="Documentation and search">
        <article class="panel support-card" aria-labelledby="support-docs-heading"><span class="support-kicker">DOCUMENTATION FIRST</span><h2 id="support-docs-heading">Find trusted guidance</h2><p>Start with official documentation. These links open the documentation sites without sending your run details or a search query.</p><div class="support-doc-links"><a href="https://docs.databricks.com/" target="_blank" rel="noopener noreferrer">Databricks documentation <span aria-hidden="true">↗</span><span class="support-sr-only"> (opens in a new tab)</span></a><a href="https://docs.informatica.com/" target="_blank" rel="noopener noreferrer">Informatica documentation <span aria-hidden="true">↗</span><span class="support-sr-only"> (opens in a new tab)</span></a></div></article>
        <article class="panel support-card" aria-labelledby="support-search-heading"><span class="support-kicker">OPTIONAL / OFFICIAL SOURCES</span><h2 id="support-search-heading">Search from ORION</h2><p>This is a manual one-off search that matches question words against official sitemap URL paths, not page contents. Results can be incomplete. Never enter secrets or sensitive customer data.</p><p class="support-footnote">To let the main six analysis agents and code writer search during a conversion, use the separate unchecked “Optional online documentation search” control on Agent activity. Each conversion needs its own consent.</p><p class="support-footnote">Your question stays in ORION. With consent, ORION fetches public sitemaps and up to two matching pages. The sites can see the page URLs requested, which may reveal the topic.</p><div class="support-field"><label for="support-search-query">Search question</label><input id="support-search-query" type="search" minlength="2" maxlength="160" placeholder="For example, streaming checkpoints" aria-describedby="support-search-reason"></div><label class="support-consent"><input id="support-search-consent" type="checkbox"> I consent to fetching official public documentation for this search.</label><button id="support-search-submit" type="button" class="secondary">Search official sources</button><p id="support-search-reason" class="support-footnote" role="status" aria-live="polite">No search requested. Documentation is untrusted reference data, not instructions.</p><div id="support-search-results" class="support-detail" aria-live="polite"></div></article>
      </aside>
    </div>`;

  const get = id => document.getElementById(id);
  const validId = value => /^[a-f0-9]{32}$/.test(value || '');
  const number = value => Number.isSafeInteger(value) && value >= 0;
  let generation = 0, offset = 0, selectedFromAgent = null, pageKey = null, selectedRun = null;

  function selection() {
    const project = get('project-select')?.value || '';
    const hash = get('project-package')?.value || '';
    if (project === '__legacy__') return {key: 'legacy', headers: {}, label: 'Selected legacy workspace'};
    if (validId(project) && /^[a-f0-9]{64}$/.test(hash))
      return {key: `${project}:${hash}`, hash, headers: {'X-Workbench-Project': project, 'X-Workbench-Package': hash}, label: 'Selected project and XML'};
    return null;
  }
  function reset(message = 'No history requested.') {
    generation++;
    offset = 0;
    selectedRun = null;
    get('support-ai-consent').checked = false;
    get('support-human-consent').checked = false;
    get('support-ai-review-id').value = '';
    get('support-load-ai-review').disabled = true;
    get('support-ai-review').disabled = true;
    get('support-human-review').disabled = true;
    get('support-load-runs').disabled = false;
    get('support-run').replaceChildren(new Option('Load saved runs first', ''));
    get('support-run').disabled = true;
    get('support-read-run').disabled = true;
    get('support-previous').disabled = true;
    get('support-next').disabled = true;
    get('support-run-detail').replaceChildren();
    get('support-run-status').textContent = message;
    const context = selection(); pageKey = context?.key || null;
    get('support-context').textContent = context ? `${context.label}. History is limited to this selection.` : 'Select a project and completed XML, or a legacy workspace, before reading history.';
  }
  async function readJson(path, context) {
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json', ...context.headers}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function stillCurrent(ticket, context) {
    return ticket === generation && !page.hidden && selection()?.key === context.key;
  }
  function dateLabel(value) {
    const time = Date.parse(value || '');
    return Number.isFinite(time) ? new Date(time).toLocaleString() : 'time not recorded';
  }
  async function loadRuns(start = 0) {
    const context = selection();
    reset();
    if (!context) { get('support-run-status').textContent = 'Choose a project and completed XML, or a legacy workspace, first.'; return; }
    const ticket = generation;
    get('support-load-runs').disabled = true;
    get('support-run-status').textContent = 'Reading saved runs for the selected context…';
    try {
      const data = await readJson(`/api/agents/runs?status_only=true&offset=${start}&limit=50`, context);
      if (!stillCurrent(ticket, context)) return;
      if (!Array.isArray(data.items)) throw new Error('invalid history response');
      offset = start;
      const select = get('support-run');
      select.replaceChildren(new Option('Choose a saved run', ''));
      for (const run of data.items) {
        if (!validId(run.id) || (context.hash && run.package_hash !== context.hash)) continue;
        select.add(new Option(`${run.workflow_name || 'Workflow name unavailable'} · ${run.status || 'status not reported'} · ${dateLabel(run.created_at)} · ${run.id}`, run.id));
      }
      if (selectedFromAgent?.key === context.key && [...select.options].some(option => option.value === selectedFromAgent.id)) select.value = selectedFromAgent.id;
      select.disabled = select.options.length === 1;
      get('support-read-run').disabled = !select.value;
      get('support-previous').disabled = start === 0;
      get('support-next').disabled = data.truncated !== true;
      get('support-run-status').textContent = `${select.options.length - 1} selectable runs in this page${number(data.total) ? `, ${data.total} saved in this context` : ''}. ${select.value ? 'The run selected in Agent activity is selected here. Read it to see its receipt.' : 'Select a run and read it to see its receipt.'}`;
    } catch (error) {
      if (stillCurrent(ticket, context)) get('support-run-status').textContent = `Saved history could not be read (${error.message}). No absence of runs is inferred.`;
    } finally { if (ticket === generation) get('support-load-runs').disabled = false; }
  }
  function addFact(list, label, value) {
    const box = document.createElement('div'), term = document.createElement('dt'), description = document.createElement('dd');
    term.textContent = label; description.textContent = value; box.append(term, description); list.append(box);
  }
  function verifiedReceipt(run) {
    const saved = run.observation_snapshot;
    if (saved) {
      if (saved.schema_version !== 1 || saved.mode !== 'historical_not_live' || saved.public_run_id !== run.id
          || saved.package_hash !== run.package_hash || saved.workflow_id !== run.workflow_id
          || saved.scenario !== run.scenario || saved.run_status !== run.status || saved.finished_at !== run.finished_at) return null;
      return saved.status === 'verified' ? saved : null;
    }
    return [run.live_activity, run.current_usage_receipt].find(item => item?.status === 'verified' && item.public_run_id === run.id) || null;
  }
  function tokenLabel(receipt, key) {
    const usage = receipt.usage;
    if (number(usage?.[key])) return usage[key].toLocaleString();
    if (number(usage?.[`known_${key}`]) && Array.isArray(receipt.turns) && receipt.turns.some(turn => number(turn?.[key])))
      return `${usage[`known_${key}`].toLocaleString()} recorded, incomplete`;
    return 'Not available';
  }
  function renderRun(run) {
    selectedRun = run;
    const host = get('support-run-detail'), heading = document.createElement('h3'), facts = document.createElement('dl');
    heading.textContent = 'Selected run'; facts.className = 'support-facts';
    addFact(facts, 'Workflow', run.workflow_name || 'Name not recorded');
    addFact(facts, 'Scenario', run.scenario || 'Not recorded');
    addFact(facts, 'Status', run.status || 'Not recorded');
    addFact(facts, 'Run ID', run.id);
    addFact(facts, 'Started', dateLabel(run.started_at || run.created_at));
    addFact(facts, 'Finished', dateLabel(run.finished_at));
    const receipt = verifiedReceipt(run);
    addFact(facts, 'Activity receipt', receipt ? (receipt.mode === 'historical_not_live' ? 'Verified historical observation' : 'Verified recorded activity') : 'Unavailable or unverified');
    const docs = Array.isArray(run.official_docs_receipts) ? run.official_docs_receipts : [];
    if (receipt) {
      addFact(facts, 'Recorded model calls', number(receipt.turn_count) && Array.isArray(receipt.turns) && receipt.turn_count === receipt.turns.length ? receipt.turn_count.toLocaleString() : 'Not available');
      addFact(facts, 'Recorded tool calls', receipt.tool_activity_status === 'recorded' && number(receipt.tool_turn_count) ? receipt.tool_turn_count.toLocaleString() : 'Not available');
      addFact(facts, 'Input tokens', tokenLabel(receipt, 'input_tokens'));
      addFact(facts, 'Output tokens', tokenLabel(receipt, 'output_tokens'));
      addFact(facts, 'Total tokens', tokenLabel(receipt, 'total_tokens'));
    }
    addFact(facts, 'Actual cost', 'Not available from this receipt');
    const note = document.createElement('p'); note.className = 'support-footnote';
    note.textContent = 'Amounts are not inferred from tokens. Missing usage is unavailable, and this view does not establish billing, review, approval, or current input freshness.';
    host.replaceChildren(heading, facts, note);
    if (docs.length) {
      const section = document.createElement('section'), title = document.createElement('h4');
      title.textContent = 'Official documentation actually fetched for this run'; section.append(title);
      for (const item of docs.slice(0, 2)) {
        if (!item || typeof item.url !== 'string' || !/^[a-f0-9]{64}$/.test(item.sha256 || '')) continue;
        let url; try { url = new URL(item.url); } catch { continue; }
        if (url.protocol !== 'https:' || !['docs.databricks.com', 'docs.informatica.com', 'spark.apache.org'].includes(url.hostname)) continue;
        const link = document.createElement('a'), receiptText = document.createElement('p');
        link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.textContent = item.title || url.pathname;
        receiptText.className = 'support-footnote'; receiptText.textContent = `${item.source || 'Official source'} · SHA-256 ${item.sha256} · ${dateLabel(item.retrieved_at)}`;
        section.append(link, receiptText);
      }
      host.append(section);
    }
    updateAIReviewButton();
  }
  function updateAIReviewButton() {
    const terminal = selectedRun && ['completed', 'partial', 'complete', 'succeeded', 'success', 'failed', 'error', 'cancelled', 'canceled', 'rejected'].includes(selectedRun.status);
    get('support-ai-review').disabled = !terminal || !get('support-ai-consent').checked;
    get('support-human-review').disabled = !terminal || !get('support-human-consent').checked;
  }
  async function readRun() {
    const context = selection(), id = get('support-run').value;
    if (!context || !validId(id)) return;
    const ticket = ++generation;
    get('support-run-detail').replaceChildren(); get('support-read-run').disabled = true;
    get('support-run-status').textContent = 'Reading the selected saved run…';
    try {
      const run = await readJson(`/api/agents/runs/${encodeURIComponent(id)}?status_only=true`, context);
      if (!stillCurrent(ticket, context) || get('support-run').value !== id) return;
      if (run.id !== id || (context.hash && run.package_hash !== context.hash)) throw new Error('run does not match the selected context');
      renderRun(run);
      get('support-run-status').textContent = 'Saved run read. Activity is shown only when its receipt is verified.';
    } catch (error) {
      if (stillCurrent(ticket, context)) get('support-run-status').textContent = `Run details could not be read (${error.message}). No activity or cost is inferred.`;
    } finally { if (ticket === generation) get('support-read-run').disabled = !get('support-run').value; }
  }
  get('support-load-runs').addEventListener('click', () => loadRuns(0));
  get('support-previous').addEventListener('click', () => loadRuns(Math.max(0, offset - 50)));
  get('support-next').addEventListener('click', () => loadRuns(offset + 50));
  get('support-read-run').addEventListener('click', readRun);
  get('support-ai-consent').addEventListener('change', updateAIReviewButton);
  get('support-human-consent').addEventListener('change', updateAIReviewButton);
  get('support-ai-review-id').addEventListener('input', () => {
    get('support-load-ai-review').disabled = !selectedRun || get('support-ai-review-id').value.trim() !== get('support-ai-review-id').value.trim().toLowerCase()
      || !/^[a-f0-9]{32}$/.test(get('support-ai-review-id').value.trim());
  });
  get('support-load-ai-review').addEventListener('click', async () => {
    const context = selection(), run = selectedRun, reviewId = get('support-ai-review-id').value.trim();
    if (!context || !run || !/^[a-f0-9]{32}$/.test(reviewId)) return;
    const ticket = generation; get('support-load-ai-review').disabled = true;
    try {
      const saved = await readJson(`/api/support/ai-reviews/${encodeURIComponent(run.id)}/${encodeURIComponent(reviewId)}`, context);
      if (ticket !== generation || selectedRun?.id !== run.id || saved.review_id !== reviewId || saved.run_id !== run.id) return;
      const section = document.createElement('section'), heading = document.createElement('h3'), text = document.createElement('p'), meta = document.createElement('p');
      heading.textContent = saved.status === 'completed' ? 'AI review (saved advisory result)' : 'AI review status (no completed result confirmed)';
      text.textContent = saved.status === 'completed' ? (saved.review_text || 'No review text saved.')
        : saved.status === 'not_started_storage_unavailable'
          ? 'Status: request was not sent to the model because durable staging failed. Resolve storage access before creating a new review ID.'
          : `Status: ${saved.status || 'not reported'}. Do not repeat this ID; the request may have reached the model.`;
      const estimate = saved.analysis_cost_estimate?.amount_usd;
      const price = typeof estimate === 'string' ? (estimate.startsWith('<') ? `, estimated <$${estimate.slice(1)}` : `, estimated $${estimate}`) : '';
      meta.className = 'support-footnote'; meta.textContent = `Review ${saved.review_id}. Created ${dateLabel(saved.created_at)}. Cost status: ${saved.analysis_cost_estimate?.status || 'not reported'}${price}.`;
      section.append(heading, text, meta); get('support-run-detail').append(section);
    } catch (error) { if (ticket === generation) get('support-run-status').textContent = `Review not found or unavailable (${error.message}); no new model request was made.`; }
    finally { get('support-load-ai-review').disabled = false; }
  });
  get('support-ai-review').addEventListener('click', async () => {
    const context = selection(), id = get('support-run').value, status = get('support-run-status');
    if (!context || !selectedRun || selectedRun.id !== id || !get('support-ai-consent').checked) return;
    const confirmed = window.confirm('Send this run’s recorded findings (which may contain source names), model/tool receipt, and available token-price estimate to the already configured validation model for one AI review? Raw source XML and generated code are excluded. This may incur model charges; list prices are not a bill.');
    if (!confirmed) return;
    const button = get('support-ai-review'), ticket = generation;
    let reviewId = get('support-ai-review-id').value.trim();
    if (!reviewId) {
      if (!globalThis.crypto?.getRandomValues) { status.textContent = 'A secure review ID could not be generated; no model request was sent.'; return; }
      const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
      reviewId = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
      get('support-ai-review-id').value = reviewId;
      get('support-load-ai-review').disabled = false;
    }
    button.disabled = true; status.textContent = 'Sending one consented AI review request…';
    get('support-ai-consent').checked = false;
    try {
      const response = await fetch('/api/support/ai-review', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Workbench-Request': '1', 'Accept': 'application/json', ...context.headers},
        body: JSON.stringify({run_id: id, review_id: reviewId, consent: true})});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (ticket !== generation || get('support-run').value !== id) return;
      get('support-ai-review-id').value = result.review_id || '';
      get('support-load-ai-review').disabled = !/^[a-f0-9]{32}$/.test(result.review_id || '');
      const section = document.createElement('section'), heading = document.createElement('h3'), text = document.createElement('p'), meta = document.createElement('p');
      heading.textContent = 'AI review (advisory)'; text.textContent = result.review_text || 'No review text returned.';
      const estimate = result.analysis_cost_estimate?.amount_usd;
      const price = typeof estimate === 'string' ? (estimate.startsWith('<') ? `, estimated <$${estimate.slice(1)}` : `, estimated $${estimate}`) : '';
      meta.className = 'support-footnote'; meta.textContent = `Review ${result.review_id}. Cost status: ${result.analysis_cost_estimate?.status || 'not reported'}${price}. Not a final bill; no approval, execution, or deployment is granted.`;
      section.append(heading, text, meta); get('support-run-detail').append(section);
      status.textContent = 'The separate AI review was saved. It did not modify the original run.';
    } catch (error) {
      if (ticket === generation) status.textContent = `AI review not confirmed (${error.message}). Check request history before retrying.`;
    } finally { get('support-ai-consent').checked = false; updateAIReviewButton(); }
  });
  get('support-human-review').addEventListener('click', async () => {
    const context = selection(), id = get('support-run').value, status = get('support-run-status');
    if (!context || !selectedRun || selectedRun.id !== id || !get('support-human-consent').checked) return;
    if (!window.confirm('Share this selected run’s bounded findings (which may contain source names), recorded model/tool actions, and available token-price estimate with ssuman@bpcs.com for human review? Raw source XML and generated code are excluded.')) return;
    const ticket = generation;
    get('support-human-review').disabled = true; status.textContent = 'Submitting one human-review request…';
    get('support-human-consent').checked = false;
    try {
      const response = await fetch('/api/support/human-review', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Workbench-Request': '1', 'Accept': 'application/json', ...context.headers},
        body: JSON.stringify({run_id: id, consent: true})});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      if (ticket !== generation || get('support-run').value !== id) return;
      status.textContent = result.status === 'pending'
        ? `Human review request ${result.request_id} is pending in the approver queue.`
        : `Run details were staged privately as ${result.request_id}, but were not sent to the shared queue. ${result.detail || ''}`;
    } catch (error) {
      if (ticket === generation) status.textContent = `Human review submission not confirmed (${error.message}). Check your Volume before retrying.`;
    } finally { get('support-human-consent').checked = false; updateAIReviewButton(); }
  });
  get('support-skill-consent').addEventListener('change', () => {
    const file = get('support-skill-file').files?.[0];
    get('support-skill-submit').disabled = !file || !get('support-skill-consent').checked || file.size < 1 || file.size > 128 * 1024;
  });
  get('support-skill-file').addEventListener('change', () => {
    get('support-skill-consent').checked = false;
    const file = get('support-skill-file').files?.[0];
    get('support-skill-submit').disabled = !file || file.size < 1 || file.size > 128 * 1024;
    get('support-skill-reason').textContent = file && file.size > 128 * 1024
      ? 'This file exceeds the 128 KiB limit.' : 'Review the file and give one-time consent before staging.';
  });
  get('support-skill-submit').addEventListener('click', async () => {
    const file = get('support-skill-file').files?.[0];
    if (!file || !get('support-skill-consent').checked || file.size > 128 * 1024) return;
    get('support-skill-submit').disabled = true; get('support-skill-consent').checked = false;
    get('support-skill-reason').textContent = 'Validating and staging this untrusted Markdown for administrator vetting…';
    try {
      const content = new TextDecoder('utf-8', {fatal: true}).decode(await file.arrayBuffer());
      const response = await fetch('/api/support/skills', {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Workbench-Request': '1', 'Accept': 'application/json'},
        body: JSON.stringify({filename: file.name, content, consent: true})});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const result = await response.json();
      get('support-skill-reason').textContent = result.status === 'pending'
        ? `Submitted for vetting: ${result.request_id}. The skill is not yet available to agents.`
        : `Private staging only, not submitted or published: ${result.request_id}. ${result.detail || ''}`;
      get('support-skill-file').value = '';
    } catch (error) {
      get('support-skill-reason').textContent = `Upload not confirmed (${error.message}). Check the current user's Volume before retrying.`;
    } finally { get('support-skill-consent').checked = false; get('support-skill-submit').disabled = true; }
  });
  async function adminJson(path, options = {}) {
    const context = selection();
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options,
      headers: {'Accept': 'application/json', ...(options.body ? {'Content-Type': 'application/json', 'X-Workbench-Request': '1'} : {}), ...(context?.headers || {}), ...(options.headers || {})}});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function showQueueError(error) { get('support-admin-status').textContent = `Queue unavailable (${error.message}); no permission or resource absence is inferred.`; }
  get('support-load-human-queue').addEventListener('click', async () => {
    const host = get('support-human-queue'); host.replaceChildren(); get('support-admin-status').textContent = 'Reading human-review queue…';
    try {
      const data = await adminJson('/api/support/human-review/queue');
      get('support-admin-status').textContent = `${data.total} human-review request(s) visible to the designated approver.`;
      for (const item of data.items || []) {
        const row = document.createElement('section'), title = document.createElement('h3'), info = document.createElement('p'), open = document.createElement('button');
        title.textContent = `Run ${item.run_id} · ${item.status}`; info.textContent = `${item.requestor?.email || 'Requester unavailable'} · ${dateLabel(item.created_at)}`;
        open.type = 'button'; open.className = 'secondary'; open.textContent = 'Inspect request';
        open.addEventListener('click', async () => {
          try {
            const detail = await adminJson(`/api/support/human-review/queue/${encodeURIComponent(item.id)}`);
            const pre = document.createElement('pre'); pre.textContent = JSON.stringify(detail.context, null, 2);
            const comment = document.createElement('textarea'); comment.maxLength = 4000; comment.setAttribute('aria-label', 'Reviewer note');
            for (const decision of ['reviewed', 'needs_information', 'declined']) {
              const act = document.createElement('button'); act.type = 'button'; act.className = 'secondary'; act.textContent = decision.replace('_', ' ');
              act.addEventListener('click', async () => {
                try { await adminJson(`/api/support/human-review/queue/${encodeURIComponent(item.id)}/decision`, {method: 'POST', body: JSON.stringify({decision, comment: comment.value})});
                  get('support-admin-status').textContent = `Request ${item.id} marked ${decision}.`; }
                catch (error) { showQueueError(error); }
              }); row.append(act);
            }
            row.append(pre, comment);
          } catch (error) { showQueueError(error); }
        });
        row.append(title, info, open); host.append(row);
      }
    } catch (error) { showQueueError(error); }
  });
  get('support-load-skill-queue').addEventListener('click', async () => {
    const host = get('support-skill-queue'); host.replaceChildren(); get('support-admin-status').textContent = 'Reading pending skill submissions…';
    try {
      const data = await adminJson('/api/support/skills/pending');
      get('support-admin-status').textContent = `${data.total} pending skill submission(s) visible to the designated approver.`;
      for (const item of data.items || []) {
        const row = document.createElement('section'), title = document.createElement('h3'), open = document.createElement('button');
        title.textContent = `${item.filename} · SHA-256 ${item.content_sha256}`;
        open.type = 'button'; open.className = 'secondary'; open.textContent = 'Inspect and vet';
        open.addEventListener('click', async () => {
          try {
            const detail = await adminJson(`/api/support/skills/pending/${encodeURIComponent(item.id)}`);
            const pre = document.createElement('pre'); pre.textContent = detail.content;
            const note = document.createElement('textarea'); note.maxLength = 4000; note.setAttribute('aria-label', 'Skill vetting note');
            const promote = document.createElement('button'); promote.type = 'button'; promote.className = 'secondary'; promote.textContent = 'Approve and promote to shared skills';
            promote.addEventListener('click', async () => {
              if (!window.confirm('Approve this exact SHA-256 skill and make it available in the approved skills Volume?')) return;
              try { const result = await adminJson(`/api/support/skills/pending/${encodeURIComponent(item.id)}/promote`, {method: 'POST', body: JSON.stringify({approved: true, review_note: note.value})});
                get('support-admin-status').textContent = `Skill ${result.skill.slug} promoted. Other users' access still depends on READ VOLUME grants.`; }
              catch (error) { showQueueError(error); }
            });
            row.append(pre, note, promote);
          } catch (error) { showQueueError(error); }
        });
        row.append(title, open); host.append(row);
      }
    } catch (error) { showQueueError(error); }
  });
  get('support-load-approved-skills').addEventListener('click', async () => {
    const host = get('support-approved-skills'); host.replaceChildren();
    try {
      const data = await adminJson('/api/support/skills/approved');
      for (const item of data.items || []) {
        const row = document.createElement('p'); row.textContent = `${item.name} · approved ${dateLabel(item.approved_at)} · ${item.sha256}`; host.append(row);
      }
      get('support-admin-status').textContent = data.status === 'available'
        ? `Approved skill inventory: ${data.items?.length || 0}. This view reflects only the current authenticated user's Volume access.`
        : 'Approved skill Volume unavailable to this identity. No empty-registry conclusion is inferred.';
    } catch (error) { showQueueError(error); }
  });
  get('support-run').addEventListener('change', () => {
    generation++; selectedRun = null; get('support-ai-consent').checked = false; get('support-human-consent').checked = false;
    get('support-ai-review-id').value = ''; get('support-load-ai-review').disabled = true; updateAIReviewButton();
    get('support-run-detail').replaceChildren(); get('support-read-run').disabled = !get('support-run').value;
    get('support-run-status').textContent = get('support-run').value ? 'Read the selected run to see its receipt.' : 'Select a saved run.';
  });
  for (const id of ['project-select', 'project-package']) get(id)?.addEventListener('change', () => { selectedFromAgent = null; reset('Selection changed. Load history for the current context.'); });
  document.addEventListener('orion:agent-run', event => {
    const context = selection(), run = event.detail;
    selectedFromAgent = context && validId(run?.id) && (!context.hash || run.package_hash === context.hash) ? {id: run.id, key: context.key} : null;
  });
  get('support-open-catalog').addEventListener('click', () => get('agent-tools')?.click());
  get('support-search-submit').addEventListener('click', async () => {
    const query = get('support-search-query').value.trim();
    const consent = get('support-search-consent');
    const status = get('support-search-reason'), results = get('support-search-results');
    if (!consent.checked || query.length < 2 || query.length > 160) {
      status.textContent = 'Enter a question of 2 to 160 characters and select consent first.';
      return;
    }
    get('support-search-submit').disabled = true;
    consent.checked = false;
    results.replaceChildren();
    status.textContent = 'Reading official sitemap paths and up to two matching pages…';
    try {
      const response = await fetch('/api/support/official-search', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-Workbench-Request': '1', 'Accept': 'application/json'},
        body: JSON.stringify({query, consent: true})
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data.sources) || !Array.isArray(data.matches)) throw new Error('invalid response');
      for (const source of data.sources) {
        const note = document.createElement('p');
        note.textContent = `${source.name}: ${source.status}. ${source.detail}`;
        results.append(note);
      }
      const fetchedMatches = [];
      const otherMatches = [];
      for (const item of data.matches) {
        let url;
        try { url = new URL(item.url); } catch { continue; }
        if (url.protocol !== 'https:' || !['docs.databricks.com', 'docs.informatica.com', 'spark.apache.org'].includes(url.hostname)
            || url.username || url.password || url.port || url.search || url.hash) continue;
        (item.page_status === 'retrieved' ? fetchedMatches : otherMatches).push({item, url});
      }
      for (const {item, url} of fetchedMatches) {
        const section = document.createElement('section'), link = document.createElement('a'), detail = document.createElement('p');
        link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
        link.textContent = item.title || item.path_label;
        detail.textContent = `Retrieved ${item.retrieved_at}. SHA-256: ${item.sha256}.`;
        section.append(link, detail);
        if (item.text) {
          const preview = document.createElement('p'); preview.className = 'support-search-excerpt';
          preview.textContent = `${item.text.slice(0, 360).trimEnd()}${item.text.length > 360 ? '…' : ''}`;
          const article = document.createElement('details'), summary = document.createElement('summary'), fullText = document.createElement('p');
          article.className = 'support-search-article'; summary.textContent = 'Expand extracted article text';
          fullText.className = 'support-search-fulltext'; fullText.textContent = item.text;
          article.append(summary, fullText); section.append(preview, article);
        }
        results.append(section);
      }
      if (otherMatches.length) {
        const more = document.createElement('details'), summary = document.createElement('summary');
        more.className = 'support-search-more'; summary.textContent = `Show ${otherMatches.length} other path matches`;
        more.append(summary);
        for (const {item, url} of otherMatches) {
          const link = document.createElement('a'), note = document.createElement('p');
          link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
          link.textContent = item.path_label || item.title || url.pathname;
          note.textContent = `Page ${item.page_status.replace('_', ' ')}.`; more.append(link, note);
        }
        results.append(more);
      }
      status.textContent = `${data.matches.length} path matches. Not a full-text search; missing results do not prove documentation is absent.`;
    } catch (error) {
      status.textContent = `Search unavailable (${error.message}). No documentation availability is inferred.`;
    } finally { get('support-search-submit').disabled = false; }
  });
  for (const button of root.querySelectorAll('[data-support-nav]')) {
    const nav = document.querySelector(`.navigation [data-page="${button.dataset.supportNav}"]`);
    if (!nav) { button.disabled = true; continue; }
    const sync = () => { button.disabled = nav.disabled; button.title = nav.disabled ? nav.title : ''; };
    new MutationObserver(sync).observe(nav, {attributes: true, attributeFilter: ['disabled', 'title']});
    sync(); button.addEventListener('click', () => { if (!nav.disabled) nav.click(); });
  }
  new MutationObserver(() => {
    if (page.hidden) reset('Return and load history for the current context.');
    else if (pageKey !== (selection()?.key || null)) reset('Selection changed. Load history for the current context.');
  }).observe(page, {attributes: true, attributeFilter: ['hidden']});
  reset();
})();