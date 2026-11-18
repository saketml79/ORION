"""Read-only frontend contract checks, no model invocation or service mutation."""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "app.css").read_text(encoding="utf-8")


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def elements():
    parsed = Elements()
    parsed.feed(HTML)
    return parsed.tags


def test_unique_ids_and_literal_javascript_targets():
    ids = [attrs["id"] for _, attrs in elements() if "id" in attrs]
    assert len(ids) == len(set(ids))
    assert set(re.findall(r"\$\('([^']+)'\)", JS)) <= set(ids)


def test_navigation_is_additive_and_legacy_default_is_retained():
    nav = [attrs["data-page"] for _, attrs in elements() if "data-page" in attrs]
    assert nav[0] == "projects"
    assert nav == ['projects', 'estate', 'explorer', 'agents', 'proposals', 'evidence', 'validate', 'readiness', 'release', 'logs', 'evolution']
    assert set(nav) == {"projects", "estate", "readiness", "explorer", "proposals", "evidence", "agents", "validate", "release", "logs", "evolution"}
    assert "page: 'estate'" in JS
    assert "Legacy package workspace" in HTML
    assert "if (state.projectId || state.jobBusy || state.jobId) return" in JS


def test_migration_proposal_labels_are_consistent_and_professional():
    assert 'id="proposals-heading">Agentic Migration Proposals</h1>' in HTML
    assert '◇</span>Agentic Migration Proposals</button>' in HTML
    assert "proposals: 'Agentic Migration Proposals'" in JS
    assert "Proposals, not promises." not in HTML


def test_validate_navigation_has_registered_icon_and_metadata_read_is_explained():
    assert 'data-page="validate"><span aria-hidden="true">✓</span>Validate</button>' in HTML
    assert "validate: 'check'" in JS
    assert "readiness: 'help'" in JS
    assert "It does not save a destination mapping, copy rows, run conversion or approve a binding." in JS


def test_delivery_label_and_health_are_distinct_from_package_readiness():
    assert 'id="release-heading">CI CD (DAB + Github)</h1>' in HTML
    assert "release: 'CI CD (DAB + Github)'" in JS
    assert "'API connected · loading overview'" in JS
    assert "'API connected · overview delayed'" in JS
    assert "else state.overviewTimer = setTimeout(() => loadOverview(), 2000)" not in JS


def test_recorded_run_completion_and_review_actions_are_not_fake_artifacts():
    poll = JS.split('async function pollRun(ticket)', 1)[1].split('const settingNames', 1)[0]
    assert "formStatus($('agent-form')" in poll
    assert "Review the recorded findings and generated draft below." in poll
    assert "run.output?.usage" in poll
    for label in ('Download run JSON', 'View original workflow graph', 'Prepare revised request', 'Review migration candidates'):
        assert label in poll
    assert "$('agent-consent').checked = false" not in poll
    assert "run.delivery_summary" in poll
    assert "post(" not in poll  # Preparing feedback must never invoke inference or approval.


def test_nonterminal_run_exposes_cooperative_stop_control():
    poll = JS.split('async function pollRun(ticket)', 1)[1].split('const settingNames', 1)[0]
    assert "Stop this run" in poll
    assert "cancellation_requested_at" in poll
    assert "/cancel" in poll
    assert "in-flight model request may finish" in poll


def test_explorer_explains_assessment_and_execution_edges_without_duplicate_summary():
    assert "'Needs assessment'" not in JS
    assert "Raw canonical references · deterministic layered layout · arrows preserve supplied direction." in JS
    assert "workflow_control arrows capture original task ordering and branch conditions" in JS
    assert "shared definitions do not by themselves establish workflow run order" in JS
    assert "highlightedText($('object-summary'), model.summary" not in JS
    assert "English Description for behavior" in JS


def test_explorer_tab_order_preserves_panel_ids_and_aria_controls():
    tabs = [attrs for _, attrs in elements() if "data-view" in attrs]
    assert [tab["data-view"] for tab in tabs] == ["graph", "fields", "english", "json"]
    for tab in tabs:
        assert tab["id"] == "tab-" + tab["data-view"]
        assert tab["aria-controls"] == "model-" + tab["data-view"]
    panels = [attrs for _, attrs in elements() if attrs.get("role") == "tabpanel"
              and attrs.get("id", "").startswith("model-")]
    assert [panel["id"] for panel in panels] == ["model-graph", "model-english", "model-json", "model-fields"]
    assert '>English Description</button>' in HTML
    assert '>JSON Definition</button>' in HTML


def test_graph_legend_rows_and_requests_have_no_client_deadline():
    assert "'Graph assessment legend'" in JS
    assert "graph-legend-${color}" in JS
    assert ".graph-status-legend { display: grid; gap: 8px;" in CSS
    assert "110000" not in JS and "30000" not in JS
    assert "Never invent a client deadline" in JS
    assert "Request cancelled because the selected context changed" in JS


def test_english_separates_recorded_agents_from_xml_and_guards_stale_responses():
    assert "'Recorded agent assessment'" in JS
    assert "'XML-derived behavior'" in JS
    selected = JS.split('async function selectObject(', 1)[1].split('function syncObjectSelection', 1)[0]
    assert "if (model.node.kind === 'workflow') {" in selected
    assert "loadWorkflowAssessment(id, ticket);" in selected
    assessment = JS.split('async function loadWorkflowAssessment', 1)[1].split('function selectView', 1)[0]
    assert "r.workflow_id === id" in assessment and "!r.stale" in assessment
    assert "ticket !== state.objectTicket" in assessment
    assert "No current completed agent assessment" in assessment
    assert "Container statistics are not a substitute" in JS
    render = JS.split('function renderObject()', 1)[1].split('function renderInspector', 1)[0]
    assert 'model.summary ?? model.node.summary' not in render
    assert 'Captured configuration properties' in render


def test_project_contract_is_server_authoritative_and_multi_file():
    assert 'No projects exist for this signed-in user yet.' in JS
    assert 'No XML packages have been added to this project.' in JS
    assert 'none has completed analysis' in JS
    assert '#workspace-volume-panel { grid-column: 2; grid-row: 2 / span 2;' in CSS
    tags = dict((attrs["id"], (tag, attrs)) for tag, attrs in elements() if "id" in attrs)
    assert "multiple" in tags["project-files"][1]
    assert "body.append('files', file)" in JS
    for path in ["/api/projects", "/api/input-packages", "/packages/${suffix}", "/jobs/${encodeURIComponent(job.id)}"]:
        assert path in JS
    assert "'X-Workbench-Project': state.projectId" in JS
    assert "'X-Workbench-Package': state.memberHash" in JS
    assert "member.status === 'completed'" in JS
    assert "state.projectId = id; state.memberHash = ''" in JS
    assert "epoch !== state.epoch" in JS
    assert "'partial'" in JS


def test_native_dialog_has_names_six_roles_and_keyboard_handling():
    dialogs = [attrs for tag, attrs in elements() if tag == "dialog" and attrs.get("id") == "agent-dialog"]
    assert len(dialogs) == 1
    assert dialogs[0]["aria-labelledby"] == "agent-dialog-heading"
    assert dialogs[0]["aria-describedby"] == "agent-dialog-description"
    assert "dialog.showModal()" in JS
    assert "event.key === 'Escape'" in JS
    assert "event.key === 'Tab'" in JS
    assert "dialog.addEventListener('close', () => $('agent-help').focus())" in JS
    for role in ["Supervisor", "Discovery", "Semantics", "Architecture", "Validation", "Artifact"]:
        assert f"<strong>{role}</strong>" in HTML
    assert "Findings are advisory and are not deployment approval." in HTML


def test_evolution_is_separate_from_implemented_features_and_has_both_designs():
    assert 'id="page-evolution"' in HTML
    assert 'id="evidence-design-dialog"' in HTML
    assert 'aria-labelledby="evidence-design-heading"' in HTML
    for label in ('dbt transformation target', 'Provider routing and economical models', 'No silent provider fallback',
                  'Deploy unscheduled jobs', 'Agent mesh design', 'Evidence-to-decision design'):
        assert label in HTML
    assert "evidenceDialog.showModal()" in JS


def test_logs_are_scoped_real_downloads_with_unknown_usage():
    for control in ["logs-level", "logs-role", "logs-run", "logs-export"]:
        assert f'id="{control}"' in HTML
    assert "/api/logs?${logParams()}" in JS
    assert "/api/logs/export?${logParams(false)}" in JS
    assert "item.total_tokens ?? 'Unknown'" in JS
    assert "data.chain_valid === true" in JS
    assert "if (state.page === 'logs') state.logsTimer = setTimeout(loadLogs, 4000)" in JS
    assert JS.count("await fetch(") == 2  # API requests plus the bounded hosted loading diagnostic.


def test_dropdowns_have_compact_type_and_label_separation():
    assert "font-size: 13px" in re.search(r"(?m)^input, select, textarea \{([^}]+)\}", CSS).group(1)
    assert "margin-bottom: 5px" in CSS.split("label {", 1)[1].split("}", 1)[0]
    assert ".context-picker { margin-top: 5px; }" in CSS


@pytest.mark.parametrize("status", ["green", "amber", "red"])
def test_graph_status_colors_and_evidence_are_accessible(status):
    assert f".node.status-{status} rect" in CSS
    assert "'data-assessment-status': status" in JS
    assert "assessmentPresentation(node)" in JS
    assert "reason: assessment.reason" in JS
    assert "node.assessment?.evidence_ids" in JS
    assert "assessment: assessmentPresentation(node).summary" in JS
    assert "return {status: 'neutral', reason, summary: reason}" in JS
    assert 'id="package-graph-heading">Package graph' in HTML
    assert "not field lineage or an execution schedule" in HTML
    assert 'id="package-graph-size"' in HTML and '<option value="all" selected>All</option>' in HTML
    assert "params.set('all', 'true')" in JS and "params.set('limit', size)" in JS
    assert "rawNodes.slice(0, 100)" in JS
    assert "/api/package-graph?${params}" in JS


def test_activity_uses_real_work_and_respects_reduced_motion():
    assert "state.requests += 1; updateActivity()" in JS
    assert "state.requests -= 1; updateActivity()" in JS
    assert "const busy = state.requests > 0 || jobs > 0" in JS
    assert "prefers-reduced-motion: reduce" in CSS
    assert "animation: none !important" in CSS
    assert '<html lang="en" data-theme="light">' in HTML
    assert "innerHTML" not in JS