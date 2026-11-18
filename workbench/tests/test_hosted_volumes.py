from types import SimpleNamespace
from unittest.mock import patch

import pytest
from databricks.sdk.errors import NotFound
from workbench.hosted_volumes import SqlVolumes


def adapter():
    identity = SimpleNamespace(client=SimpleNamespace(config=SimpleNamespace(host="https://workspace.example.com")), user_id="u1")
    return SqlVolumes(identity, "warehouse")


def test_actual_show_and_describe_volume_sql_shapes():
    api = adapter()
    with patch.object(api.store, "_execute", side_effect=[[["workbench", "artifacts"]],
            [["artifacts", "catalog", "workbench", "alice", "location", "MANAGED"]]]):
        assert api.read("catalog.workbench.artifacts").owner == "alice"
    with patch.object(api.store, "_execute", return_value=[["workbench", "artifacts"]]):
        assert [v.name for v in api.list("catalog", "workbench")] == ["artifacts"]


def test_permission_failure_is_not_missing_volume():
    api = adapter()
    with patch.object(api.store, "_execute", side_effect=RuntimeError("denied")):
        with pytest.raises(RuntimeError):
            api.read("catalog.workbench.artifacts")
    with patch.object(api.store, "_execute", return_value=[]):
        with pytest.raises(NotFound):
            api.read("catalog.workbench.artifacts")