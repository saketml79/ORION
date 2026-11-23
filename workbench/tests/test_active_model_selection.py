from pathlib import Path


def test_active_model_selection_is_retained_and_read_only():
    source = (Path(__file__).resolve().parents[1] / 'static' / 'workspace-access.js').read_text(encoding='utf-8')
    assert "dataset.agentRunStatus === 'busy'" in source
    assert 'connect.disabled = modelBusy || runLocked()' in source
    assert 'endpoint.disabled = modelBusy || runLocked()' in source
    assert 'confirmation.disabled = modelBusy || runLocked()' in source
    assert 'apply.disabled = modelBusy || runLocked()' in source
    assert "runLocked() ? 'Run endpoint (locked)'" in source
    assert "endpoint.disabled ? 'Not selected'" not in source
    assert "live?.status === 'verified' && live.public_run_id === run.id" in source
    assert 'endpoint.append(new Option(name, name))' in source
    assert 'endpoint.value = name' in source
    assert "attributeFilter: ['data-agent-run-status']" in source