"""Offline Statement API contract tests with real SDK responses and SQLite files."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
from threading import Event
from types import SimpleNamespace as NS

import pytest
from databricks.sdk.errors import AlreadyExists, NotFound
from databricks.sdk.service.catalog import ColumnInfo, DataSourceFormat, TableInfo
from databricks.sdk.service.sql import (
    ResultData, ResultManifest, StatementResponse, StatementState, StatementStatus,
)

from workbench import delta_state as ds


class StatementClient:
    def __init__(self):
        self.config = NS(host="https://test.cloud.databricks.com")
        self.user = NS(id="user-123", user_name="legacy@example.com")
        self.current_user = NS(me=lambda: self.user)
        self.catalog_owner = self.schema_owner = self.volume_owner = self.user.user_name
        self.catalogs = NS(get=lambda name: NS(owner=self.catalog_owner))
        self.schemas = NS(get=lambda name: NS(owner=self.schema_owner))
        self.volumes = NS(read=lambda name: NS(owner=self.volume_owner))
        self.infos, self.rows, self.objects, self.calls = {}, {}, {}, []
        self.tables = NS(get=self.table)
        self.statement_execution = NS(execute_statement=self.execute, get_statement=self.get_statement)
        self.files = NS(create_directory=lambda path: None, upload=self.upload, download=self.download)
        self.response_override = None
        self.pending = None

    def table(self, name):
        if name not in self.infos:
            raise NotFound("not found")
        return self.infos[name]

    def upload(self, path, stream, overwrite=False):
        assert overwrite is False
        if path in self.objects:
            raise AlreadyExists("exists")
        self.objects[path] = stream.read()

    def download(self, path):
        if path not in self.objects:
            raise NotFound("not found")
        return NS(contents=io.BytesIO(self.objects[path]))

    def get_statement(self, statement_id):
        return self.pending

    @staticmethod
    def result(rows=()):
        return StatementResponse(status=StatementStatus(state=StatementState.SUCCEEDED),
                                 result=ResultData(data_array=list(rows)), manifest=ResultManifest(truncated=False))

    def execute(self, **kw):
        self.calls.append(kw)
        if self.response_override is not None:
            return self.response_override
        sql = kw["statement"]
        parameters = {p.name: p.value for p in kw["parameters"]}
        assert all(p.type == "STRING" for p in kw["parameters"])
        matches = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`\.`([A-Za-z_][A-Za-z0-9_]*)`\.`([A-Za-z_][A-Za-z0-9_]*)`", sql)
        assert len(matches) == 1
        name = ".".join(matches[0])
        assert kw["warehouse_id"] == "warehouse"
        if sql.startswith("CREATE TABLE"):
            self.infos[name] = TableInfo(owner=self.user.user_name, data_source_format=DataSourceFormat.DELTA,
                                        columns=[ColumnInfo(name=k, type_text=v, nullable=False) for k, v in ds.COLUMNS.items()])
            self.rows[name] = []
        elif sql.startswith("MERGE INTO"):
            assert " AS source_rows(" in sql and ")) AS s ON " in sql
            assert ") AS s(" not in sql
            count = len(parameters) // len(ds.COLUMNS)
            assert count <= ds.BATCH_ROWS
            for i in range(count):
                row = {k: (int(parameters[f"r{i}_{k}"]) if typ == "BIGINT" else parameters[f"r{i}_{k}"])
                       for k, typ in ds.COLUMNS.items()}
                key = lambda r: tuple(r[k] for k in ("workspace_id", "user_id", "project_id", "package_hash", "context_id", "record_id", "content_hash", "migration_id"))
                if key(row) not in [key(r) for r in self.rows[name]]:
                    self.rows[name].append(row)
        elif sql.startswith("SELECT"):
            rows = [r for r in self.rows[name] if all(r[k] == v for k, v in parameters.items() if not k.startswith("cursor_"))]
            if sql.startswith("SELECT count(*)"):
                return self.result([[str(len(rows))]])
            key = lambda r: (r["record_id"], r["content_hash"], r["migration_id"])
            rows.sort(key=key)
            if "cursor_id" in parameters:
                cursor = tuple(parameters[k] for k in ("cursor_id", "cursor_hash", "cursor_migration"))
                rows = [r for r in rows if key(r) > cursor]
            limit = int(re.search(r"LIMIT (\d+)$", sql)[1])
            return self.result([[str(row[k]) for k in ds.COLUMNS] for row in rows[:limit]])
        else:
            raise AssertionError(sql)
        return self.result()


@pytest.fixture
def state():
    client = StatementClient()
    store = ds.DeltaStateStore(client, "warehouse", "user_catalog", "workbench", "user-123")
    return client, store, ds.VolumeArtifacts(store)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_merge_has_constant_exact_target_predicates_for_every_mixed_scope_row(state):
    client, store, _ = state
    store.ensure_tables()
    records = [store.make_record('jobs', project_id=p, package_hash='package',
        context_id=c, source_path='progress', source_table='events',
        source_key='1', payload={'stage': 'running'})
        for p, c in [('project-a', 'operation-a'), ('project-b', 'operation-b')]]
    store.upsert_records('jobs', records, migration_id='journal')
    merge = client.calls[-1]
    target, join = merge['statement'].split(' AS s ON ', 1)[1].split(') AND t.', 1)
    for i in range(2):
        for key in ('workspace_id', 'user_id', 'project_id', 'package_hash',
                    'context_id', 'record_id', 'content_hash', 'migration_id'):
            assert f't.`{key}`=:r{i}_{key}' in target
    assert ' OR ' in target and 'WHEN MATCHED' not in join
    store.upsert_records('jobs', records, migration_id='journal')
    assert len(client.rows['user_catalog.workbench.jobs']) == 2


def test_independent_adapter_writers_serialize_same_table_and_release_after_failure(state, monkeypatch):
    client, first, _ = state
    first.ensure_tables()
    second = ds.DeltaStateStore(client, 'warehouse', 'user_catalog', 'workbench', 'user-123')
    entered, attempted, release, second_entered = Event(), Event(), Event(), Event()
    original = first._execute

    def execute(sql, values=None):
        entered.set()
        assert release.wait(10)
        raise RuntimeError('Controlled statement failure')

    def execute_second(sql, values=None):
        second_entered.set()
        return original(sql, values)

    monkeypatch.setattr(first, '_execute', execute)
    monkeypatch.setattr(second, '_execute', execute_second)
    row = first.make_record('jobs', project_id='project', package_hash='package',
        context_id='operation-a', source_path='progress', source_table='events',
        source_key='1', payload={'stage': 'running'})

    def second_records():
        attempted.set()
        yield second.make_record('jobs', project_id='project', package_hash='package',
            context_id='operation-b', source_path='progress', source_table='events',
            source_key='1', payload={'stage': 'running'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.upsert_records, 'jobs', [row], migration_id='journal')
        try:
            assert entered.wait(10)
            b = pool.submit(second.upsert_records, 'jobs', second_records(), migration_id='journal')
            assert attempted.wait(10)
            assert not second_entered.is_set()
        finally:
            release.set()
        with pytest.raises(RuntimeError, match='Controlled statement failure'):
            a.result(timeout=10)
        assert b.result(timeout=10) == 1
    assert second_entered.is_set()
    assert not ds._WRITERS


def database(path, statements):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        for sql, params in statements:
            db.execute(sql, params)


def estate(root):
    """All existing persistence families and repeated package copies."""
    package = "a" * 64
    database(root / "projects/projects.sqlite", [
        ("CREATE TABLE projects(id TEXT PRIMARY KEY,record TEXT NOT NULL)", ()),
        ("CREATE TABLE packages(project_id TEXT,hash TEXT,record TEXT,PRIMARY KEY(project_id,hash))", ()),
        ("CREATE TABLE jobs(project_id TEXT,id TEXT,record TEXT,PRIMARY KEY(project_id,id))", ()),
        ("INSERT INTO projects VALUES (?,?)", ("p1", '{"id":"p1","name":"One"}')),
        ("INSERT INTO projects VALUES (?,?)", ("p2", '{"id":"p2","name":"Two"}')),
        ("INSERT INTO packages VALUES (?,?,?)", ("p1", package, json.dumps({"sha256": package, "status": "completed"}))),
        ("INSERT INTO packages VALUES (?,?,?)", ("p2", package, json.dumps({"sha256": package, "status": "completed"}))),
        ("INSERT INTO jobs VALUES (?,?,?)", ("p1", "job", '{"status":"completed"}')),
    ])
    contexts = [root, root / f"projects/p1/package-contexts/{package}", root / f"projects/p2/package-contexts/{package}"]
    for context in contexts:
        database(context / "state/reviews.sqlite", [
            ("CREATE TABLE proposals(package_hash TEXT,proposal_id TEXT,version INTEGER,payload TEXT,PRIMARY KEY(package_hash,proposal_id,version))", ()),
            ("CREATE TABLE events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,package_hash TEXT,proposal_id TEXT,kind TEXT,payload TEXT)", ()),
            ("CREATE TABLE documents(package_hash TEXT,document_id TEXT,payload TEXT,PRIMARY KEY(package_hash,document_id))", ()),
            ("CREATE TABLE settings(package_hash TEXT PRIMARY KEY,payload TEXT)", ()),
            ("INSERT INTO proposals VALUES (?,?,?,?)", (package, "proposal", 2, '{"status":"draft","version":2}')),
            ("INSERT INTO events VALUES (?,?,?,?,?)", (1, package, "proposal", "review", '{"actor":"raw-local-actor","actor_authenticated":false,"action":"comment"}')),
            ("INSERT INTO documents VALUES (?,?,?)", (package, "document", json.dumps({"text": "evidence" * 3000}))),
            ("INSERT INTO settings VALUES (?,?)", (package, '{"github_owner":"example"}')),
        ])
        database(context / "state/audit.sqlite", [
            ("CREATE TABLE audit_events(sequence INTEGER PRIMARY KEY,payload TEXT,previous_hash TEXT,event_hash TEXT)", ()),
            ("INSERT INTO audit_events VALUES (?,?,?,?)", (1, '{"actor":"original"}', "prev", "hash")),
        ])
        loops = []
        for table in ("runs", "attempts", "events", "lessons", "comparisons"):
            loops.extend([
                (f"CREATE TABLE {table}(id TEXT PRIMARY KEY,package_hash TEXT,workflow_id TEXT,payload TEXT)", ()),
                (f"INSERT INTO {table} VALUES (?,?,?,?)", (table, package, "workflow", '{"status":"advisory"}')),
            ])
        database(context / "state/loops.sqlite", loops)
        agents = context / "agents/identities/identity"
        database(agents / "agents.sqlite3", [
            ("CREATE TABLE agent_runs(run_id TEXT PRIMARY KEY,namespace TEXT,input_hashes TEXT,state TEXT,result TEXT,created_at TEXT)", ()),
            ("INSERT INTO agent_runs VALUES (?,?,?,?,?,?)", ("run", "legacy-ns", "{}", "advisory", '{"status":"advisory"}', "2026-01-01")),
            ("CREATE TABLE agent_findings(id INTEGER PRIMARY KEY,run_id TEXT,namespace TEXT,role TEXT,input_hashes TEXT,payload TEXT,created_at TEXT)", ()),
            ("INSERT INTO agent_findings VALUES (?,?,?,?,?,?,?)", (1, "run", "legacy-ns", "discovery", "{}", "{}", "2026-01-01")),
            ("CREATE TABLE agent_memory(id INTEGER PRIMARY KEY,namespace TEXT,role TEXT,run_id TEXT,payload TEXT,created_at TEXT)", ()),
            ("INSERT INTO agent_memory VALUES (?,?,?,?,?,?)", (1, "legacy-ns", "discovery", "run", "{}", "2026-01-01")),
            ("CREATE TABLE agent_usage(id INTEGER PRIMARY KEY,run_id TEXT,namespace TEXT,role TEXT,call_id TEXT,event TEXT,payload TEXT,created_at TEXT)", ()),
            ("INSERT INTO agent_usage VALUES (?,?,?,?,?,?,?,?)", (1, "run", "legacy-ns", "discovery", "call", "model_completed", '{"input_tokens":12}', "2026-01-01")),
        ])
        database(agents / "checkpoint_discovery.sqlite3", [
            ("CREATE TABLE checkpoints(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,type TEXT,checkpoint BLOB,metadata BLOB,PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id))", ()),
            ("CREATE TABLE writes(thread_id TEXT,checkpoint_ns TEXT,checkpoint_id TEXT,task_id TEXT,idx INTEGER,channel TEXT,type TEXT,value BLOB,PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id,task_id,idx))", ()),
            ("INSERT INTO checkpoints VALUES (?,?,?,?,?,?)", ("thread", "ns", "cp", "msgpack", b"\x00\xffcheckpoint", b"metadata")),
            ("INSERT INTO writes VALUES (?,?,?,?,?,?,?,?)", ("thread", "ns", "cp", "task", 0, "channel", "msgpack", b"write")),
        ])
        write_json(context / "state/jobs/local-job.json", {"id": "local-job", "package_hash": package, "status": "completed"})
        write_json(context / "state/agent-runs/run.json", {"id": "run", "package_hash": package})
        write_json(context / f"packages/{package}/manifest.json", {"sha256": package})
        write_json(context / f"packages/{package}/proposals-original.json", [{"id": "proposal", "version": 1}])
        (context / f"packages/{package}/source.xml").write_bytes(b"<source>source bytes</source>")
        (context / f"packages/{package}/understanding.json.gz").write_bytes(b"\x1f\x8blarge-compressed-evidence")
    write_json(root / "projects/p1/documents/evidence.json", {"text": "project evidence", "metadata": {"actor_authenticated": False}})
    write_json(root / "models/model-routing.json", {"version": 1, "identities": {"identity": {
        "workspace": "https://test.cloud.databricks.com", "user_id": "user-123", "routes": {"discovery": {"endpoint": "model"}}}}})
    write_json(root / "state/active.json", {"sha256": package})
    return package


def test_full_inventory_idempotency_provenance_and_recovery(state, tmp_path):
    client, store, artifacts = state
    source = tmp_path / "source"
    package = estate(source)
    before = {p.relative_to(source).as_posix(): sha256(p.read_bytes()).hexdigest()
              for p in source.rglob("*") if p.is_file()}
    snapshot = ds.snapshot_local(source, tmp_path / "snapshot")
    result = ds.migrate(store, snapshot, artifacts)
    expected = {
        "projects": 2, "packages": 5, "jobs": 4, "proposal_versions": 6,
        "review_events": 3, "documents": 4, "settings": 4, "agent_runs": 6,
        "agent_findings": 3, "agent_memory": 3, "agent_usage": 3,
        "agent_checkpoints": 6, "loop_runs": 3, "loop_attempts": 3,
        "loop_events": 3, "loop_lessons": 3, "loop_comparisons": 3,
        "audit_events": 3, "artifact_manifest": len(snapshot.files), "model_routes": 1,
    }
    assert set(result["tables"]) == set(ds.TABLES) - {"migration_receipts"}
    assert {t: v["actual"] for t, v in result["tables"].items()} == expected
    assert all(v["hashes_verified"] for v in result["tables"].values())
    assert result["hosted_authority_integrated"] is False
    counts = {name: len(rows) for name, rows in client.rows.items()}
    assert ds.migrate(store, snapshot, artifacts) == result
    assert {name: len(rows) for name, rows in client.rows.items()} == counts
    reviews = client.rows["user_catalog.workbench.review_events"]
    assert len({r["record_id"] for r in reviews}) == 3
    assert {r["project_id"] for r in reviews} == {"p1", "p2", "__legacy__"}
    assert all(r["package_hash"] == package and r["actor"] == "raw-local-actor" for r in reviews)
    assert all(json.loads(r["payload"])["payload_decoded"]["actor_authenticated"] is False for r in reviews)
    assert all(len(r["payload"].encode()) <= ds.MAX_PAYLOAD for rows in client.rows.values() for r in rows)
    assert any("$artifact" in r["payload"] for r in client.rows["user_catalog.workbench.documents"])
    plan = json.loads(artifacts.get(result["plan"]["sha256"]))
    assert len(plan["objects"]) > len({item["sha256"] for item in snapshot.files})
    for item in plan["objects"]:
        assert len(artifacts.get(item["sha256"], max_bytes=item["byte_size"])) == item["byte_size"]
    assert ds.validate_live(store, plan["inventory"], result["migration_id"]) == result["tables"]
    export = tmp_path / "restore"
    assert ds.restore_snapshot(artifacts, plan, export) == len(snapshot.files)
    for item in snapshot.files:
        assert sha256((export / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    with closing(sqlite3.connect(export / f"projects/p1/package-contexts/{package}/state/reviews.sqlite")) as db:
        assert json.loads(db.execute("SELECT payload FROM events").fetchone()[0])["actor"] == "raw-local-actor"
    assert before == {p.relative_to(source).as_posix(): sha256(p.read_bytes()).hexdigest()
                      for p in source.rglob("*") if p.is_file()}
    sql = "\n".join(c["statement"] for c in client.calls)
    assert not re.search(r"\b(DROP|DELETE|TRUNCATE|UPDATE)\b", sql)
    assert "raw-local-actor" not in sql


def record(store, i=0, **changes):
    return store.make_record("jobs", project_id="p", package_hash="package", context_id="context",
                             source_path="state/jobs/job.json", source_table="json", source_key=str(i),
                             payload={"value": "quote ' ; DROP TABLE projects; --", **changes})


def test_parameterized_batches_scoped_reads_versions_and_pagination(state):
    client, store, _ = state
    store.ensure_tables()
    rows = [record(store, i) for i in range(117)]
    assert store.upsert_records("jobs", rows + rows, migration_id="migration") == 117
    calls = [c for c in client.calls if c["statement"].startswith("MERGE")]
    assert len(calls) == 3
    assert all("DROP TABLE projects" not in c["statement"] for c in calls)
    inventory = ds._inventory({"jobs": rows})
    assert ds.validate_live(store, inventory, "migration")["jobs"]["actual"] == 117
    read_calls = [c for c in client.calls if c["statement"].startswith("SELECT")]
    assert all("workspace_id=:workspace_id" in c["statement"] or "`workspace_id`=:workspace_id" in c["statement"] for c in read_calls)
    assert all("user_id=:user_id" in c["statement"] or "`user_id`=:user_id" in c["statement"] for c in read_calls)
    changed = record(store, 0, changed=True)
    assert changed["record_id"] == rows[0]["record_id"]
    assert changed["content_hash"] != rows[0]["content_hash"]
    store.upsert_records("jobs", [changed], migration_id="migration")
    assert store.migration_count("jobs", "migration") == 118
    with pytest.raises(ValueError, match="inventory"):
        ds.validate_live(store, inventory, "migration")
    bad = dict(rows[0], user_id="other")
    with pytest.raises(PermissionError):
        store.upsert_records("jobs", [bad], migration_id="migration")
    assert store.read_table("jobs", project_id="another-project", package_hash="package", context_id="context") == []
    for limit in (0, 51, True, "10"):
        with pytest.raises(ValueError):
            store.read_table("jobs", project_id="p", package_hash="package", context_id="context", limit=limit)


@pytest.mark.parametrize("identifier", ["x.y", "x`", "x;DROP", "", "a b", "a--", "../schema"])
def test_unsafe_identifiers_rejected_before_sql(state, identifier):
    client, store, _ = state
    with pytest.raises(ValueError):
        ds.DeltaStateStore(client, "warehouse", identifier, "workbench", "user-123")
    with pytest.raises(ValueError):
        store.read_table(identifier, project_id="p", package_hash="a", context_id="c")
    assert client.calls == []


@pytest.mark.parametrize("field", ["catalog_owner", "schema_owner"])
def test_ownership_fail_closed(state, field):
    client, store, _ = state
    setattr(client, field, "someone-else")
    with pytest.raises(PermissionError):
        store.ensure_tables()
    assert client.calls == []


def test_identity_table_schema_and_volume_ownership(state):
    client, store, artifacts = state
    client.user = NS(id="another-user", user_name="legacy@example.com")
    with pytest.raises(PermissionError):
        store.ensure_tables()
    client.user.id = "user-123"
    store.ensure_tables()
    n = len(client.calls)
    store.ensure_tables()
    assert len(client.calls) == n
    info = client.infos["user_catalog.workbench.projects"]
    info.owner = "other"
    with pytest.raises(PermissionError):
        store.ensure_tables()
    info.owner = client.user.user_name
    info.columns[0].type_text = "INT"
    with pytest.raises(ValueError, match="schema"):
        store.ensure_tables()
    client.volume_owner = "other"
    with pytest.raises(PermissionError):
        artifacts.put(b"evidence")
    assert client.objects == {}


def test_live_hash_corruption_extra_scope_and_empty_inventory(state):
    client, store, _ = state
    store.ensure_tables()
    row = record(store)
    store.upsert_records("jobs", [row], migration_id="m")
    expected = ds._inventory({"jobs": [row]})
    remote = client.rows["user_catalog.workbench.jobs"][0]
    remote["payload"] = '{"tampered":true}'
    with pytest.raises(ValueError, match="hash"):
        ds.validate_live(store, expected, "m")
    remote["payload"] = row["payload"]
    with pytest.raises(ValueError, match="inventory"):
        ds.validate_live(store, {"jobs": []}, "m")
    other = deepcopy(remote)
    other["project_id"] = "unexpected-scope"
    client.rows["user_catalog.workbench.jobs"].append(other)
    with pytest.raises(ValueError, match="inventory"):
        ds.validate_live(store, expected, "m")


def test_sqlite_wal_backup_binary_checkpoint_and_snapshot_guards(state, tmp_path):
    client, store, artifacts = state
    source = tmp_path / "source"
    source.mkdir()
    path = source / "reviews.sqlite"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE events(sequence INTEGER PRIMARY KEY,package_hash TEXT,proposal_id TEXT,kind TEXT,payload TEXT)")
        db.execute("INSERT INTO events VALUES (1,'package','p','review','{}')")
        db.commit()
        snapshot = ds.snapshot_local(source, tmp_path / "copy")
        with closing(sqlite3.connect(snapshot.root / "reviews.sqlite")) as copy:
            assert copy.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert [f["path"] for f in snapshot.files] == ["reviews.sqlite"]
    with pytest.raises(ValueError):
        ds.snapshot_local(source, source / "nested")
    with pytest.raises(ValueError):
        ds.snapshot_local(source, snapshot.root)


@pytest.mark.parametrize("secret", [{"access_token": "secret-material"}, {"password": "hidden"}, {"nested": "Bearer abcdefghijklmnopqrstuvwxyz"}])
def test_no_credentials_or_partial_upload_on_preflight_failure(state, tmp_path, secret):
    client, store, artifacts = state
    source = tmp_path / "source"
    write_json(source / "state/z-secret.json", secret)
    write_json(source / "state/a-good.json", {"safe": True})
    # Snapshot rejects recognizable byte tokens; otherwise logical preflight
    # rejects credential fields before any table creation or artifact upload.
    with pytest.raises(ValueError):
        snapshot = ds.snapshot_local(source, tmp_path / "snap")
        ds.migrate(store, snapshot, artifacts)
    assert client.calls == []
    assert client.objects == {}


def test_unknown_sqlite_table_and_route_identity_fail_before_writes(state, tmp_path):
    client, store, artifacts = state
    source = tmp_path / "source"
    database(source / "reviews.sqlite", [("CREATE TABLE surprise(id TEXT PRIMARY KEY)", ())])
    snap = ds.snapshot_local(source, tmp_path / "snap")
    with pytest.raises(ValueError, match="Unrecognized"):
        ds.migrate(store, snap, artifacts)
    assert client.calls == [] and client.objects == {}
    source2 = tmp_path / "source2"
    write_json(source2 / "models/model-routing.json", {"identities": {"x": {"user_id": "someone-else", "workspace": store.workspace_id}}})
    with pytest.raises(PermissionError):
        ds.migrate(store, ds.snapshot_local(source2, tmp_path / "snap2"), artifacts)
    assert client.calls == [] and client.objects == {}


def test_namespace_mismatch_and_restore_path_traversal(state, tmp_path):
    client, store, artifacts = state
    with pytest.raises(ValueError, match="package-context"):
        ds._scope("projects/p/package-contexts/" + "a" * 64 + "/state/file.json", {"package_hash": "b" * 64})
    with pytest.raises(ValueError, match="project-context"):
        ds._scope("projects/p/state/file.json", {"project_id": "other"})
    for path in ("../escape", "/absolute", "C:/absolute", "..\\escape"):
        plan = {"workspace_id": store.workspace_id, "user_id": store.user_id,
                "files": [{"path": path, "sha256": "a" * 64, "byte_size": 0}]}
        with pytest.raises(ValueError):
            ds.restore_snapshot(artifacts, plan, tmp_path / ("target" + str(len(path))))
    with pytest.raises(PermissionError):
        ds.restore_snapshot(artifacts, {"user_id": "other"}, tmp_path / "cross-user")


def test_statement_status_chunking_and_sanitized_errors(state, monkeypatch):
    client, store, _ = state
    client.response_override = StatementResponse(statement_id="s", status=StatementStatus(state=StatementState.PENDING))
    client.pending = client.result([["1"]])
    monkeypatch.setattr(ds.time, "sleep", lambda seconds: None)
    assert store._execute("unused") == [["1"]]
    client.response_override = StatementResponse(status=StatementStatus(state=StatementState.FAILED))
    with pytest.raises(RuntimeError, match="unavailable"):
        store._execute("unused")
    client.response_override = client.result()
    client.response_override.manifest.truncated = True
    with pytest.raises(RuntimeError, match="unavailable"):
        store._execute("unused")
    def fail(**kw):
        raise RuntimeError("Bearer secret-sensitive-provider-error")
    client.statement_execution.execute_statement = fail
    with pytest.raises(RuntimeError) as error:
        store._execute("unused")
    assert "secret-sensitive" not in str(error.value)


def test_offline_cli_and_safe_error_output(tmp_path, monkeypatch, capsys):
    import databricks.sdk
    def forbidden(*args, **kwargs):
        raise AssertionError("must not instantiate SDK client")
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", forbidden)
    write_json(tmp_path / "source/state/active.json", {"sha256": "a" * 64})
    assert ds.main(["inventory", "--source", str(tmp_path / "source")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 1 and result["remote_writes"] is False
    assert ds.main(["ensure"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "must not instantiate" not in captured.err


def test_actual_estate_sized_proposal_json_is_externalized(state, tmp_path):
    client, store, artifacts = state
    source = tmp_path / "source"
    write_json(source / ("packages/" + "a" * 64 + "/proposals-bronze.json"),
               [{"id": "proposal", "version": 1, "evidence": "e" * (17 * 1024 * 1024)}])
    snapshot = ds.snapshot_local(source, tmp_path / "snapshot")
    records = ds.build_records(store, snapshot, artifacts)
    assert len(records["proposal_versions"]) == 1
    row = records["proposal_versions"][0]
    assert row["version"] == 1 and row["entity_id"] == "proposal"
    payload = json.loads(row["payload"])
    assert payload["evidence"]["$artifact"]["byte_size"] == 17 * 1024 * 1024
    assert len(row["payload"].encode()) < ds.MAX_PAYLOAD


def test_cli_migrate_validate_and_export_use_returned_plan(state, tmp_path, monkeypatch, capsys):
    import databricks.sdk
    client, store, artifacts = state
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **kw: client)
    source = tmp_path / "source"
    write_json(source / "state/active.json", {"sha256": "a" * 64})
    common = ["--warehouse", "warehouse", "--catalog", "user_catalog"]
    assert ds.main(["migrate", "--source", str(source), *common]) == 0
    result = json.loads(capsys.readouterr().out)
    checksum = result["plan"]["sha256"]
    assert ds.main(["validate", "--plan-sha256", checksum, *common]) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["migration_id"] == result["migration_id"]
    assert verification["artifact_hashes_verified"] >= 1
    assert ds.main(["export", "--plan-sha256", checksum, "--destination", str(tmp_path / "recovered"), *common]) == 0
    assert json.loads(capsys.readouterr().out)["exported_files"] == 1
    plan = json.loads(artifacts.get(checksum))
    blob = artifacts._path(plan["objects"][0]["sha256"])
    client.objects[blob] = b"corrupt"
    assert ds.main(["validate", "--plan-sha256", checksum, *common]) == 1
    assert "failed" in capsys.readouterr().err


def test_parallel_artifacts_verify_all_bytes_and_bound_authorization(state, tmp_path, monkeypatch):
    client, store, artifacts = state
    checks = []
    original = artifacts.authorize
    def authorize():
        checks.append(True)
        return original()
    monkeypatch.setattr(artifacts, "authorize", authorize)
    paths = []
    for i in range(ds.UPLOAD_BATCH + 3):
        data = ("checkpoint-" + str(i)).encode()
        path = tmp_path / sha256(data).hexdigest()
        path.write_bytes(data)
        paths.append(path)
    events = []
    assert artifacts.put_many(paths, progress=events.append) == len(paths)
    assert len(checks) == 4
    assert events[-1] == {"stage": "ARTIFACTS_VERIFIED", "completed": len(paths), "total": len(paths)}
    assert len(client.objects) == len(paths)
    for path in paths:
        assert artifacts.get(path.name, max_bytes=path.stat().st_size) == path.read_bytes()
    assert artifacts.put_many(paths) == len(paths)
    assert len(client.objects) == len(paths)


@pytest.mark.parametrize("failure", ["upload", "corrupt", "owner", "client"])
def test_parallel_artifacts_fail_closed_without_secret_diagnostics(state, tmp_path, monkeypatch, failure):
    from databricks.sdk.errors import PermissionDenied
    client, store, artifacts = state
    raw = b"checkpoint-value"
    path = tmp_path / sha256(raw).hexdigest()
    path.write_bytes(raw)
    events = []
    if failure == "upload":
        def fail(*args, **kwargs):
            raise PermissionDenied("Bearer do-not-display-provider-secret", error_code="PERMISSION_DENIED")
        monkeypatch.setattr(client.files, "upload", fail)
    elif failure == "corrupt":
        client.objects[artifacts._path(path.name)] = raw
        monkeypatch.setattr(client.files, "download", lambda name: NS(contents=io.BytesIO(b"corrupt")))
    elif failure == "owner":
        client.volume_owner = "other"
    else:
        original = artifacts.authorize
        def changed():
            original()
            client.config.host = "https://different.cloud.databricks.com"
        monkeypatch.setattr(artifacts, "authorize", changed)
    with pytest.raises((PermissionError, ValueError, PermissionDenied)):
        artifacts.put_many([path], progress=events.append)
    assert "do-not-display" not in json.dumps(events)
    assert "checkpoint-value" not in json.dumps(events)
    if failure != "owner":
        assert events[-1]["stage"] == "ARTIFACT_FAILED"
        assert events[-1]["sha256"] == path.name
        assert events[-1]["byte_size"] == len(raw)
    if failure == "upload":
        assert events[-1]["error_type"] == "PermissionDenied"
        assert events[-1]["error_code"] == "PERMISSION_DENIED"


def test_verified_batch_rechecks_owner_after_upload(state, tmp_path, monkeypatch):
    client, store, artifacts = state
    raw = b"evidence"
    path = tmp_path / sha256(raw).hexdigest()
    path.write_bytes(raw)
    original = client.upload
    def upload(*args, **kwargs):
        original(*args, **kwargs)
        client.volume_owner = "changed-owner"
    monkeypatch.setattr(client.files, "upload", upload)
    with pytest.raises(PermissionError):
        artifacts.put_many([path])


def test_migration_progress_and_receipt_follow_all_artifact_checks(state, tmp_path, monkeypatch):
    client, store, artifacts = state
    write_json(tmp_path / "source/state/active.json", {"safe": True})
    snapshot = ds.snapshot_local(tmp_path / "source", tmp_path / "snapshot")
    events = []
    result = ds.migrate(store, snapshot, artifacts, progress=events.append)
    stages = [event["stage"] for event in events]
    assert stages.index("ARTIFACTS_VERIFIED") < stages.index("DELTA_WRITE_BEGIN")
    assert stages[-1] == "RECEIPT_VERIFIED"
    assert events[-1]["migration_id"] == result["migration_id"]
    assert all("payload" not in event for event in events)
    monkeypatch.setattr(client.files, "download", lambda path: NS(contents=io.BytesIO(b"corrupt")))
    before = len(client.rows["user_catalog.workbench.migration_receipts"])
    with pytest.raises(ValueError):
        ds.migrate(store, snapshot, artifacts)
    assert len(client.rows["user_catalog.workbench.migration_receipts"]) == before


def test_safe_error_never_echoes_arbitrary_codes_or_messages(state):
    client, store, _ = state
    error = RuntimeError("Bearer private-provider-message")
    error.error_code = "dapi" + "a" * 32
    assert ds.safe_error(error) == {"error_type": "RuntimeError", "error_code": None}
    def fail(**kwargs):
        from databricks.sdk.errors import BadRequest
        raise BadRequest("https://signed-url/private?token=secret", error_code="INVALID_PARAMETER_VALUE")
    client.statement_execution.execute_statement = fail
    with pytest.raises(ds.DeltaStatementError) as caught:
        store._execute("unused")
    assert ds.safe_error(caught.value)["error_code"] == "INVALID_PARAMETER_VALUE"
    assert "signed-url" not in str(caught.value) and "secret" not in str(caught.value)


def test_existing_large_blob_is_verified_without_put(state, monkeypatch):
    client, store, artifacts = state
    raw = b"e" * 3175554
    digest = sha256(raw).hexdigest()
    client.objects[artifacts._path(digest)] = raw
    def forbidden(*args, **kwargs):
        raise AssertionError("Existing immutable content must not be PUT again")
    monkeypatch.setattr(client.files, "upload", forbidden)
    assert artifacts.put(raw)["sha256"] == digest


def test_missing_blob_race_conflict_still_verifies_content(state, monkeypatch):
    client, store, artifacts = state
    raw = b"racing-evidence"
    digest = sha256(raw).hexdigest()
    def racing_upload(path, stream, overwrite=False):
        assert overwrite is False
        client.objects[path] = stream.read()
        raise AlreadyExists("racing writer")
    monkeypatch.setattr(client.files, "upload", racing_upload)
    assert artifacts.put(raw)["sha256"] == digest