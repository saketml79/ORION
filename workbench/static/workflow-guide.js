/* Presentation-only workflow guide. Existing controls and recorded states remain authoritative. */
(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const make = (tag, cls, content) => {
    const node = document.createElement(tag);
    node.className = cls;
    if (content) node.textContent = content;
    return node;
  };
  const card = document.querySelector('.hero-aside');
  card.parentElement.prepend(card);
  const project = make('div', 'current-project');
  project.append(make('span', 'eyebrow', 'CURRENT PROJECT'));
  const projectName = make('strong', 'current-project-name');
  const projectHelp = make('p', 'small muted');
  const projectAction = make('button', 'secondary compact', 'Choose or create project');
  projectAction.type = 'button';
  projectAction.addEventListener('click', () => document.querySelector('[data-page="projects"]').click());
  project.append(projectName, projectHelp, projectAction);
  card.prepend(project);

  const approach = make('section', 'approach-map');
  approach.setAttribute('aria-label', 'ORION evidence-to-decision approach');
  approach.append(make('span', 'eyebrow', 'THE ORION APPROACH'), make('h3', '', 'Evidence first. A reviewed design next.'),
    make('p', 'muted', 'Combine exported logic with operational evidence and human knowledge. Agents propose changes; people decide what moves forward.'));
  const stages = [
    ['Legacy Data Integration Artifacts', 'Informatica packages + design documents + owner knowledge', 'M4 3h11l5 5v13H4z M14 3v6h6 M8 13h8 M8 17h6'],
    ['Understand', 'Sources, rules, dependencies, and gaps', 'M4 4h6v6H4z M14 14h6v6h-6z M10 7h7v7'],
    ['Propose', 'Lakeflow options with cited rationale', 'M4 20l4-1L20 7l-4-4L4 15z M13 6l4 4'],
    ['Human review & deployment', 'Review the design and candidate code; approve implementation and release separately', 'M8 7a4 4 0 1 0 8 0 4 4 0 0 0-8 0 M4 21v-3a8 8 0 0 1 16 0v3'],
  ];
  const flow = make('ol', 'approach-stages');
  for (const [name, description, pathData] of stages) {
    const item = make('li', 'approach-stage');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', pathData); svg.append(path);
    item.append(svg, make('strong', '', name), make('p', '', description)); flow.append(item);
  }
  const boundary = make('div', 'approach-boundary');
  boundary.append(make('strong', '', 'From design to deployment'), make('p', '', 'Review the proposed design and candidate artifacts, then validate them against your business requirements. Implementation and deployment require their own review and approval.'));
  approach.append(flow, boundary, make('p', 'small muted', 'Follow current activity in the status banner and package workflow below.'));
  document.querySelector('.hero > div:not(.hero-aside)').append(approach);

  const guide = make('section', 'intake-guide');
  guide.setAttribute('aria-label', 'Package analysis workflow');
  guide.append(make('span', 'eyebrow', 'FROM LEGACY EXPORT TO REVIEWED DESIGN'), make('h3', '', 'What happens after upload?'),
    make('p', 'muted', 'ORION reads Informatica packages incrementally, preserves their original logic and dependencies, and builds an evidence-linked model. Missing business context stays unknown, not guessed.'));
  const steps = [
    ['Project context', 'Choose the project that owns the Informatica packages and supporting documents.'],
    ['Select existing or optionally add artifacts', 'Select a completed XML already in the project. Upload another Informatica package (.xml) only if needed; supporting documents and owner knowledge can be added separately.'],
    ['Analyze package', 'Read objects and field flows, resolve references, and record diagnostics.'],
    ['Inspect & assess', 'Explore the graph, sources, transformations, and evidence-linked summaries.'],
    ['Agent design & review', 'Explicitly invoke configured agents; review rationale, open questions, and proposed changes.'],
    ['Review & prepare for deployment', 'Review the design and generated drafts. Validate coverage and business behavior, then approve any implementation and deployment separately.'],
  ];
  const list = make('ol', 'intake-steps');
  const rows = steps.map(([name, description], index) => {
    const row = make('li', 'intake-step');
    const number = make('span', 'intake-number', String(index + 1));
    const copy = make('div', 'intake-copy');
    copy.append(make('strong', '', name), make('p', '', description));
    const status = make('span', 'intake-step-status');
    copy.append(status); row.append(number, copy); list.append(row);
    return { row, status };
  });
  const current = make('p', 'intake-current');
  current.setAttribute('role', 'status');
  const details = make('p', 'small muted intake-fineprint');
  guide.append(list, current, details);
  byId('package-form').closest('article').append(guide);

  function refresh() {
    const selector = byId('project-select');
    const selected = selector.value && selector.selectedOptions[0];
    const packageSelector = byId('estate-package-select');
    const selectedPackage = packageSelector?.selectedOptions[0];
    const completedSelected = Boolean(packageSelector?.dataset.projectId && selectedPackage?.value && !selectedPackage.disabled && selectedPackage.dataset.completed === 'true');
    const projectSelected = Boolean(selected || packageSelector?.dataset.projectId);
    const name = selected ? selected.textContent.replace(/\s*\([\d,]+ ready\)$/, '') : packageSelector?.dataset.projectName || (projectSelected ? packageSelector.dataset.projectId : 'Standalone package');
    projectName.textContent = name;
    projectName.title = name;
    projectHelp.textContent = projectSelected ? 'Package views belong to this selected project.' : 'Choose or create a project to organize your packages.';
    projectAction.textContent = projectSelected ? 'Manage project & packages' : 'Choose or create project';
    const ready = byId('package-ready').textContent === 'Package ready';
    const understanding = byId('package-ready').dataset.understanding;
    const loadingExisting = completedSelected && understanding === 'loading';
    const loadingFailed = completedSelected && understanding === 'failed';
    const activity = byId('activity-status');
    const busy = activity.classList.contains('is-active');
    const activityText = activity.textContent.trim();
    const ingestText = byId('ingest-status').textContent;
    const analyzing = busy && /package job|analyz|parsing|XML batch/i.test(activityText);
    const agentRunning = busy && /agent run/i.test(activityText);
    const failed = loadingFailed || (!analyzing && !ready && /failed|could not|unavailable/i.test(ingestText));
    const active = agentRunning ? 4 : analyzing || (completedSelected && !ready) ? 2 : ready ? 3 : projectSelected ? 1 : 0;
    rows.forEach(({ row, status }, index) => {
      row.classList.toggle('is-current', index === active);
      row.classList.toggle('is-running', index === active && (loadingExisting || analyzing || agentRunning));
      row.classList.toggle('is-recorded', (index === 0 && projectSelected) || (index === 1 && completedSelected) || (ready && index === 2));
      row.classList.toggle('is-failed', failed && index === 2);
      row.removeAttribute('aria-current');
      if (index === active) row.setAttribute('aria-current', 'step');
      status.textContent = failed && index === 2 ? 'Needs attention' : index === 1 && completedSelected ? 'Completed XML selected; upload optional' : index === active ? (loadingExisting ? 'Loading existing package understanding' : analyzing || agentRunning ? 'Running' : 'Current step') : index === 0 && projectSelected ? 'Project selected' : index === 2 && ready ? 'Structural analysis available' : index >= 4 ? 'Separate authorization required' : 'Available when prerequisites are met';
    });
    current.textContent = loadingExisting ? `Loading existing package understanding: ${selectedPackage.dataset.packageName}. No new upload is required.` : loadingFailed ? `${byId('package-ready').textContent}. Existing XML remains selected. Use Refresh data to retry; no new upload is required.` : analyzing || agentRunning ? `Currently running: ${activityText}` : failed ? 'Analysis needs attention. Inspect the job status and Logs before retrying.' : ready ? 'Current stage: inspect package understanding and resolve evidence gaps.' : completedSelected ? 'Load the existing selected package understanding. No new upload is required.' : `Current stage: ${steps[active][0]}.`;
    current.classList.toggle('is-running', loadingExisting || analyzing || agentRunning);
    details.textContent = busy ? `Current activity: ${activityText}.` : 'Choose a package to explore its structure. Agent analysis and migration approval are separate steps.';
  }
  const observer = new MutationObserver(refresh);
  for (const id of ['project-select', 'estate-package-select', 'package-ready', 'ingest-status', 'activity-status']) {
    observer.observe(byId(id), { childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'data-understanding', 'data-project-id', 'data-project-name'] });
  }
  byId('project-select').addEventListener('change', refresh);
  byId('estate-package-select').addEventListener('change', refresh);
  refresh();
})();