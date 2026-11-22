import pytest


def test_action_executor_propagates_only_initiating_context():
    from workbench.databricks_models import AuthenticationUnavailable
    from workbench.hosted import ActionExecutor, _CURRENT, current_identity

    first, second = object(), object()
    with ActionExecutor(max_workers=1) as executor:
        token = _CURRENT.set(first)
        try:
            assert executor.submit(current_identity).result(timeout=5) is first
        finally:
            _CURRENT.reset(token)
        token = _CURRENT.set(second)
        try:
            assert executor.submit(current_identity).result(timeout=5) is second
        finally:
            _CURRENT.reset(token)
        with pytest.raises(AuthenticationUnavailable):
            executor.submit(current_identity).result(timeout=5)