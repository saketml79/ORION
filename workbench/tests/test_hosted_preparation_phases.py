"""Offline barriers around the real hosted dispatcher and durable progress reporter."""
import asyncio
import json
import logging
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench import hosted
from workbench.delta_runtime import RequestStateStore
from workbench.hosted_operations import API, HostedOperations, current_progress
from workbench.hosted_storage import provision
from workbench.tests.test_hosted import Backend, HOST, ORIGIN, TOKEN_A, offline  # noqa: F401


@pytest.mark.parametrize(
    ("blocked", "trace", "not_started"),
    [
        ("preparing_package", "preparing_package_reporter", "user_app"),
        ("overlay", "model_settings_overlay_root", "create_package_app_root"),
        ("create", "create_package_app_root", "service_start"),
        ("service", "service_start", "projects_start"),
        ("indexing_nodes", "index_progress_reporter_indexing_nodes", "projects_start"),
        ("projects", "projects_start", "app_dispatch"),
    ],
)
def test_durable_dispatch_blocks_at_measured_boundary_without_leaking(
        blocked, trace, not_started, monkeypatch, caplog):
    """The real journal writes to an in-memory Delta fake, never to a remote service."""
    caplog.set_level(logging.INFO, logger="workbench.hosted")
    backend = Backend()
    identity = backend.resolve(HOST, TOKEN_A, trusted_proxy=True, deployment_host=HOST)
    remote = provision(identity, "orion_dev_")
    parts = remote.split("/")
    store = RequestStateStore(identity.client, "warehouse", parts[2], parts[3], identity.user_id)
    store.ensure_tables()
    entered, release = Event(), Event()
    reached = []

    def gate(name):
        reached.append(name)
        if name == blocked:
            entered.set()
            if not release.wait(15):
                raise TimeoutError("test barrier was not released")

    statement_api = identity.client.statement_execution
    original_execute = statement_api.execute_statement

    def gated_execute(**kwargs):
        if kwargs["statement"].startswith("MERGE INTO") and ".`jobs`" in kwargs["statement"]:
            parameters = {item.name: item.value for item in kwargs["parameters"]}
            if parameters.get("r0_source_table") == "operations":
                value = json.loads(parameters["r0_payload"])
                if value["status"] == "running":
                    gate(value["progress"]["stage"])
        return original_execute(**kwargs)

    monkeypatch.setattr(statement_api, "execute_statement", gated_execute)

    class Snapshots:
        def __init__(self, identity_factory, remote_path, local, **kwargs):
            self.local = Path(local)
            self.store, self.artifacts = object(), object()

        def restore(self, **kwargs):
            pass

        def restore_audit(self):
            pass

        def flush(self):
            reached.append("flush")

        def publish_source(self, *args, **kwargs):
            raise AssertionError("source publication not expected")

        def reference(self, *args, **kwargs):
            raise AssertionError("source registration not expected")

    class Service:
        def __init__(self, root):
            self.root = Path(root)
            self.runtime = SimpleNamespace(knowledge_loader=None)
            self.active = None

        def start(self, progress):
            gate("service")
            for _ in range(3):
                progress("indexing_nodes", completed=1, total=1, unit="nodes")

        def close(self):
            reached.append("service_close")

    class Projects:
        def start(self):
            gate("projects")

        def close(self):
            reached.append("projects_close")

    class FakeApp:
        def __init__(self, root):
            self.state = SimpleNamespace(service=Service(root), audit=SimpleNamespace(append=lambda entry: None))

        async def __call__(self, scope, receive, send):
            reached.append("dispatch")
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b'{"ok":true}'})

    def create_package(data_dir, sample, *, model_installer, **kwargs):
        gate("create")
        app = FakeApp(data_dir)
        model_installer(app)
        return app

    def install_projects(app, *args, **kwargs):
        app.state.projects = Projects()

    def model_status(*args, **kwargs):
        assert kwargs["validate_ready"] is False
        gate("overlay")

    monkeypatch.setattr(hosted, "DeltaRuntime", Snapshots)
    monkeypatch.setattr(hosted, "provision", lambda *args, **kwargs: remote)
    monkeypatch.setattr(hosted, "_create_package_app", create_package)
    monkeypatch.setattr(hosted, "install_hosted_models", lambda app: None)
    monkeypatch.setattr(hosted, "install_projects", install_projects)
    monkeypatch.setattr(hosted, "install_volume_intake", lambda *args, **kwargs: None)
    monkeypatch.setattr("workbench.hosted_model_reads.model_status", model_status)
    monkeypatch.setattr("workbench.external_models.current_for_root", lambda root: None)

    dispatcher = hosted.HostedDispatcher(hosted.HostedConfig(HOST, ORIGIN, warehouse="warehouse"),
                                         identity_resolver=backend.resolve)
    operations = HostedOperations("warehouse", "offline-preparation-test")
    operation = uuid4().hex
    scope = {"type": "http", "method": "POST", "path": "/api/hosted/operations",
             "query_string": b"", "headers": [(b"host", b"workbench.example.com"),
                 (b"x-forwarded-access-token", TOKEN_A.encode()), (b"x-workbench-request", b"1")]}

    def execute(method, path, query, headers, body):
        return asyncio.run(dispatcher.execute_operation(scope, method, path, query, headers, body,
                                                        current_progress()))

    request = {"id": operation, "method": "POST", "path": "/api/agents/run", "body": {}}
    try:
        status, _ = operations.handle(identity, remote, "POST", API, "", {}, request, execute)
        assert status == 202
        assert entered.wait(15), (blocked, reached, caplog.text)
        messages = [record.getMessage() for record in caplog.records if record.name == "workbench.hosted"]
        assert f"Hosted preparation phase={trace} state=started" in messages
        assert not any(f"phase={trace} state=completed" in message for message in messages)
        assert not any(f"phase={not_started} state=started" in message for message in messages)
    finally:
        release.set()
        operations.close(wait=True)
        dispatcher.operations.close(wait=True)
    status, result = operations.handle(identity, remote, "GET", API + "/" + operation, "", {}, b"", None)
    assert status == 200 and result["status"] == "completed" and result["result"] == {"ok": True}
    assert reached.index("dispatch") < reached.index("flush")
    assert "service_close" in reached and "projects_close" in reached
    messages = [record.getMessage() for record in caplog.records if record.name == "workbench.hosted"]
    assert any(f"phase={trace} state=completed elapsed_seconds=" in message for message in messages)
    preparation_logs = [message for message in messages if message.startswith("Hosted preparation phase=")]
    assert sum("phase=index_progress_reporter_indexing_nodes state=started" in message
               for message in preparation_logs) == 1
    assert preparation_logs and all(TOKEN_A not in message and "alice" not in message for message in preparation_logs)
    assert any(call[1] == "sql" and call[2].startswith("MERGE INTO") and ".`jobs`" in call[2]
               for call in backend.calls)


def test_root_and_project_factories_have_distinct_safe_labels(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="workbench.hosted")
    identity = SimpleNamespace(host=HOST, user_id="alice-id", mode="obo")
    snapshots = SimpleNamespace(publish_source=lambda *args: None, reference=lambda *args: None,
                                store=object(), artifacts=object(), model_project="a" * 32)
    calls = []

    def create_package(data_dir, sample, *, model_installer, **kwargs):
        app = SimpleNamespace(state=SimpleNamespace(
            service=SimpleNamespace(root=Path(data_dir), runtime=SimpleNamespace(knowledge_loader=None)),
            audit=SimpleNamespace(append=lambda entry: None)))
        model_installer(app)
        return app

    def install_projects(app, factory, *args, **kwargs):
        app.state.projects = SimpleNamespace(factory=factory)

    def model_status(runtime, actual_identity, project, package, *, validate_ready):
        calls.append((project, package, validate_ready))

    monkeypatch.setattr(hosted, "_create_package_app", create_package)
    monkeypatch.setattr(hosted, "install_hosted_models", lambda app: None)
    monkeypatch.setattr(hosted, "install_projects", install_projects)
    monkeypatch.setattr(hosted, "install_volume_intake", lambda *args, **kwargs: None)
    monkeypatch.setattr("workbench.hosted_model_reads.model_status", model_status)
    monkeypatch.setattr("workbench.external_models.current_for_root", lambda root: None)
    token = hosted._CURRENT.set(identity)
    try:
        app = hosted.user_app(tmp_path, snapshots, read_only=False, execution_models=True)
        app.state.projects.factory(tmp_path / "projects" / ("a" * 32) / "package-contexts" / ("b" * 64))
        assert calls == [("a" * 32, None, False), ("a" * 32, "b" * 64, False)]
        calls.clear()
        hosted.user_app(tmp_path, snapshots, read_only=True, execution_models=False)
        assert calls == []
    finally:
        hosted._CURRENT.reset(token)
    messages = [record.getMessage() for record in caplog.records]
    assert any("phase=model_settings_overlay_root state=completed" in message for message in messages)
    assert any("phase=model_settings_overlay_project_context state=completed" in message for message in messages)
    assert any("phase=create_package_app_project_context state=completed" in message for message in messages)
    assert all("alice-id" not in message and TOKEN_A not in message for message in messages)


def test_trace_failure_preserves_exception_without_message_or_credentials(caplog):
    caplog.set_level(logging.INFO, logger="workbench.hosted")
    with pytest.raises(ValueError, match="sensitive"):
        with hosted._preparation_trace("service_start"):
            raise ValueError("sensitive " + TOKEN_A)
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "state=failed" in messages[-1] and "exception_class=ValueError" in messages[-1]
    assert "sensitive" not in messages[-1] and TOKEN_A not in messages[-1]

    class SensitiveCustomError(Exception):
        pass

    with pytest.raises(SensitiveCustomError):
        with hosted._preparation_trace("projects_start"):
            raise SensitiveCustomError(TOKEN_A)
    assert "exception_class=Exception" in caplog.records[-1].getMessage()
    assert TOKEN_A not in caplog.text
