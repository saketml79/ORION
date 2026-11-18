/* Static implementation boundaries, not runtime availability or approval evidence. */
(() => {
  'use strict';

  function render() {
    const main = document.getElementById('main');
    if (!main || document.getElementById('implementation-status')) return;

    const groups = [
      ['Observed implementation components, with limits', [
        ['XML inventory evidence', 'partial',
          'XML inventory, structural relationships, and evidence inspection have implementation paths. Parsed structure does not establish business intent, active use, physical connectivity, or semantic parity.'],
        ['Advisory agents', 'needs verification',
          'Agent roles, model routing, and review tools are configured, not verified here. Configuration does not prove a successful live run, valid design submission, provider compatibility, or specialist coverage. Findings remain advisory.'],
        ['Versioned logical review', 'partial',
          'Versioned logical designs and review records have implementation paths. Outline review does not authorize executable artifacts, clear independent validation gates, or approve deployment.'],
        ['Captured SQL drafts', 'partial',
           'Captured SQL has a limited syntax-draft path. It does not convert general non-SQL transformations, establish data parity, or produce a complete validated workflow.'],
        ['Isolated demonstration', 'partial',
          'A constrained workflow demonstration has a dedicated compiler and test replacements. It is not general conversion or production equivalence. Mocked email, shortened waits, and child-workflow replacements do not verify original side effects or child completion.'],
      ]],
      ['Remaining work and verification', [
        ['General non-SQL workflow conversion', 'not implemented',
          'General conversion must preserve transformation graphs, parameters, control conditions, recovery, transactions, and external side effects. The isolated demonstration and SQL drafts do not supply this capability.'],
        ['Semantic parity', 'needs verification',
          'Independent schema, transformation, data, control-flow, recovery, and end-to-end equivalence evidence is still required. Inventory counts, generated files, and successful demonstration runs are not parity evidence.'],
        ['Physical connection binding and tests', 'not implemented',
          'Read-only XML connection inventory is not a physical source or target binding. Authorized environment-specific bindings, credential references, network checks, and live connection tests remain required.'],
        ['Durable hosted operations', 'needs verification',
          'Durable upload and operation-journal implementation paths exist, but deployment of the new hosted operations path is pending. Independent worker completion, recovery, and live hosted behavior still need verification. A responsive page is not durable execution evidence.'],
        ['Approval-bound general artifacts and independent validation', 'not implemented',
          'General artifact generation must be bound to the exact approved design and artifact hashes, with independent validation before release. Logical review, captured SQL drafts, and demonstration exports do not complete this lifecycle. Deployment and execution require separate authorization.'],
        ['GitHub CI/CD', 'not implemented',
          'Repository publication, pull requests, protected environment approvals, and customer-artifact promotion remain work. Display settings and hosted-app packaging do not establish an integrated delivery pipeline.'],
        ['LangSmith activation', 'needs verification',
           'Tracing integration paths do not prove activation. Enablement, safe configuration, and an observed remote trace receipt must be verified for the intended environment; this panel does not inspect or activate tracing.'],
        ['Skills improvement', 'not implemented',
          'Existing role skills are not a reviewed improvement lifecycle. Evidence-linked lessons, human approval, regression evaluation, and controlled promotion of reusable skills remain work.'],
      ]],
    ];

    function element(tag, text, className) {
      const node = document.createElement(tag);
      if (text) node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    const panel = element('section', '', 'panel section-gap');
    panel.id = 'implementation-status';
    panel.setAttribute('aria-labelledby', 'implementation-status-heading');
    panel.setAttribute('aria-describedby', 'implementation-status-boundary');
    const heading = element('h2', 'Implementation status and remaining work');
    heading.id = 'implementation-status-heading';
    const boundary = element('p',
      'Static implementation notes, independent of backend and overview availability. These describe observed components and remaining boundaries, not current service availability, completed features, or permission to execute. No live checks are performed.');
    boundary.id = 'implementation-status-boundary';
    panel.append(heading, boundary);

    for (const [title, entries] of groups) {
      panel.append(element('h3', title));
      for (const [label, status, description] of entries) {
        const detail = element('details');
        detail.open = true;
        detail.dataset.implementationStatus = status;
        detail.append(element('summary', `${label}: ${status}`), element('p', description));
        panel.append(detail);
      }
    }

    const evolution = element('button', 'Open Evolution roadmap', 'secondary');
    evolution.type = 'button';
    evolution.addEventListener('click', () => {
      const existing = document.querySelector('.navigation button[data-page="evolution"]');
      if (existing) existing.click();
    });
    panel.append(evolution);
    // Outside .page sections and the overview-owned capability container.
    const footer = main.querySelector('.workspace-footer');
    main.insertBefore(panel, footer);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', render, { once: true });
  } else {
    render();
  }
})();