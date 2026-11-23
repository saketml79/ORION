"""Source-contract guards; live presentation is checked in the shared local app."""
from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / 'static'


def test_call_labels_describe_xml_not_run_history():
    app = (STATIC / 'app.js').read_text(encoding='utf-8')
    picker = (STATIC / 'context-picker.js').read_text(encoding='utf-8')
    for label in ('Calls other workflows', 'May be called by another workflow',
                  'Call target not confirmed', 'No workflow calls found in XML',
                  'XML call information unavailable'):
        assert label in app
    assert 'not previous runs' in picker
    assert "labels.push('Unresolved')" not in app
    assert "|| 'No captured call'" not in app


def test_visible_workflow_picker_precedes_scope_with_compatibility_bridge():
    source = (STATIC / 'redesign-review.js').read_text(encoding='utf-8')
    assert "document.getElementById('agent-workflow-roller').parentElement.after(scopePanel)" in source
    assert 'agentScopeLabel.hidden = true; agentScope.hidden = true;' in source
    assert "new Option('Cohort', 'cohort')" in source
    assert "agentScope.addEventListener('change'" in source