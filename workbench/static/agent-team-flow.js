/* Describes the configured agent sequence; observed run activity is shown separately. */
(() => {
  'use strict';
  const steps = [
    ['01', 'Informatica package analyst', 'discovery', 'informatica-understanding', 'Examines the package, workflow and source evidence.'],
    ['02', 'Transformation semantics analyst', 'semantics', 'transformation-semantics', 'Reviews transformations, SQL and control behavior.'],
    ['03', 'Lakeflow workflow designer', 'architecture', 'lakeflow-design', 'Proposes a task graph from the available evidence.'],
    ['04', 'Independent validation analyst', 'validation', 'independent-validation', 'Reviews the design, coverage and unresolved questions.'],
    ['05', 'Artifact requirements analyst', 'artifact', 'artifact-requirements', 'Reviews notebook, testing and delivery requirements; code drafting is a separate phase.'],
    ['06', 'Lead migration design coordinator', 'supervisor', 'migration-coordination', 'Combines the migration handoffs and can submit or correct the Databricks design for code generation.'],
  ];
  const make = (tag, className, text) => {
    const el = document.createElement(tag); el.className = className; el.textContent = text; return el;
  };
  const svgNS = 'http://www.w3.org/2000/svg';
  const svgNode = (tag, attributes, parent, text = '') => {
    const el = document.createElementNS(svgNS, tag);
    for (const [name, value] of Object.entries(attributes)) el.setAttribute(name, String(value));
    if (text) el.textContent = text;
    parent.append(el); return el;
  };
  function graph(index) {
    const container = make('div', 'agent-communication-scroll', '');
    container.tabIndex = 0;
    container.setAttribute('role', 'region');
    container.setAttribute('aria-label', 'Agent communication graph. Scroll horizontally to view the complete diagram.');
    const svg = svgNode('svg', {viewBox: '0 0 1120 1100', class: 'agent-communication-graph', role: 'img',
      'aria-label': 'Configured migration flow: the workbench invokes six agents in order and passes their source analysis and migration handoffs to subsequent agents. The Lakeflow designer or lead coordinator submits a migration design. When requested, the Spark and SQL writer generates code from the selected design and source evidence. Checks and available AI review can send feedback for bounded revisions.'}, container);
    const defs = svgNode('defs', {}, svg);
    for (const [name, color] of [['call', 'var(--indigo)'], ['result', 'var(--teal)']]) {
      const marker = svgNode('marker', {id: `agent-flow-${index}-${name}`, viewBox: '0 0 10 10', refX: '9', refY: '5',
        markerWidth: '7', markerHeight: '7', orient: 'auto-start-reverse'}, defs);
      svgNode('path', {d: 'M 0 0 L 10 5 L 0 10 z', fill: color}, marker);
    }
    const line = (d, type, optional = false) => svgNode('path', {d, class: `agent-communication-edge ${type}${optional ? ' optional' : ''}`,
      'marker-end': `url(#agent-flow-${index}-${type})`}, svg);
    // The host invokes each role, and role findings are carried into later invocations.
    steps.forEach((step, i) => {
      const y = 52 + i * 95;
      line(`M 195 320 H 228 V ${y + 36} H 266`, 'call');
      line(`M 786 ${y + 36} H 826 V 320 H 850`, 'result');
    });
    line('M 970 275 V 10 H 105 V 275', 'result');
    line('M 786 563 H 816 V 645 H 230 V 563', 'call', true);
    line('M 105 365 V 701 H 266', 'call', true);
    line('M 526 737 V 760', 'call');
    line('M 526 832 V 900', 'call');
    line('M 266 936 H 220 V 796 H 266', 'call', true);
    line('M 786 936 H 850', 'result');
    svgNode('text', {x: 560, y: 22, class: 'agent-communication-edge-label', 'text-anchor': 'middle'}, svg, 'Source analysis and migration handoffs inform the next agents');
    svgNode('text', {x: 520, y: 636, class: 'agent-communication-edge-label', 'text-anchor': 'middle'}, svg, 'Optional: lead analyst delegates a focused specialist follow-up');
    svgNode('text', {x: 230, y: 870, class: 'agent-communication-edge-label', 'text-anchor': 'end'}, svg, 'Feedback → revise');
    const box = (x, y, width, height, css, title, subtitle) => {
      const group = svgNode('g', {class: css}, svg);
      svgNode('rect', {x, y, width, height, rx: '12'}, group);
      svgNode('text', {x: x + width / 2, y: y + 32, 'text-anchor': 'middle', class: 'agent-communication-title'}, group, title);
      svgNode('text', {x: x + width / 2, y: y + 54, 'text-anchor': 'middle', class: 'agent-communication-subtitle'}, group, subtitle);
    };
    box(15, 275, 180, 90, 'agent-communication-hub', 'Workbench', 'invokes agents');
    box(850, 275, 240, 90, 'agent-communication-hub result', 'Migration handoffs', 'source logic · open questions');
    steps.forEach(([number, label, role, skill], i) => {
      const y = 52 + i * 95;
      const group = svgNode('g', {class: 'agent-communication-agent'}, svg);
      svgNode('rect', {x: 266, y, width: 520, height: 72, rx: '11'}, group);
      svgNode('circle', {cx: 293, cy: y + 36, r: 17}, group);
      svgNode('text', {x: 293, y: y + 40, 'text-anchor': 'middle', class: 'agent-communication-number'}, group, number);
      svgNode('text', {x: 322, y: y + 25, class: 'agent-communication-title'}, group, label);
      svgNode('text', {x: 322, y: y + 47, class: 'agent-communication-subtitle'}, group, `Agent: ${role}  ·  Skill: ${skill}`);
    });
    box(266, 665, 520, 72, 'agent-communication-writer', 'Databricks migration design', 'Selected design and source evidence guide code generation');
    box(266, 760, 520, 72, 'agent-communication-writer', '07  Spark and SQL code writer', 'Skill: spark-code-writer  ·  generates migration code');
    box(266, 900, 520, 72, 'agent-communication-writer', '08  Check and improve generated code', 'Static checks · AI review when available');
    box(850, 900, 240, 72, 'agent-communication-hub result', 'Migration code', 'saved for user review');
    return container;
  }
  for (const [index, mount] of [...document.querySelectorAll('#agent-dialog [data-agent-team-flow]')].entries()) {
    const intro = make('p', 'agent-team-intro', 'The agents understand the Informatica workflow, develop a Databricks design and generate migration code. Each specialist receives the migration handoffs recorded by earlier agents.');
    const legend = make('p', 'agent-communication-legend', 'Animated arrows show the configured flow. Lighter dotted paths are optional; this is not live run activity.');
    const list = make('ol', 'agent-team-steps', '');
    for (const [number, label, role, skill, description] of steps) {
      const item = make('li', 'agent-team-step', '');
      const badge = make('span', 'agent-team-number', number); badge.setAttribute('aria-hidden', 'true');
      const content = make('div', 'agent-team-step-content', '');
      content.append(make('strong', 'agent-team-name', label), make('span', 'agent-team-role', `Agent: ${role}`),
        make('p', 'agent-team-call', 'Workbench → agent · prior migration handoffs included'),
        make('p', 'agent-team-description', description), make('p', 'agent-team-skill', `Skill: ${skill}`));
      item.append(badge, content); list.append(item);
    }
    const writer = make('aside', 'agent-team-writer', '');
    writer.append(make('span', 'agent-team-number', '07'), make('div', '', ''));
    writer.lastElementChild.append(make('strong', 'agent-team-name', 'Spark and SQL code writer'),
      make('span', 'agent-team-role', 'Separate code-generation phase'),
      make('p', 'agent-team-description', 'When code generation is requested and an eligible design is available, this writer generates migration notebooks and code using the configured artifact-role model.'),
      make('p', 'agent-team-skill', 'Skill: spark-code-writer'));
    const note = make('p', 'agent-team-note', 'Checks and available AI review can return feedback to the writer for bounded revisions. The user can review and change the generated code. Automatic scored optimization across multiple generated versions is planned separately.');
    const details = make('details', 'agent-team-text-details', '');
    details.append(make('summary', '', 'Agent roles and responsibilities'), list, writer);
    mount.replaceChildren(intro, legend, graph(index), details, note);
    if (mount.parentElement?.id === 'agent-dialog') mount.parentElement.classList.add('agent-team-enhanced');
  }
})();
