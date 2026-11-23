"""Exact response parity and failure-closed scoped hosted canonical reads."""
from copy import deepcopy
from hashlib import sha256
import json

from databricks.sdk.errors import PermissionDenied
import pytest

from workbench import delta_state as ds
from workbench.delta_runtime import DeltaRuntime
from workbench.hosted_object_reads import object_read_response
from workbench.object_views import object_response, migration_response
from workbench.package_context import workflow_invocation_summary
from workbench.tests.test_hosted import backend, browser, headers, offline, TOKEN_A2, TOKEN_B, create
from workbench.tests.test_hosted_registry import fresh, uploaded, committed, rewrite_manifest
from workbench.tests.test_source_analysis import JAVA_XML


def selected(project, package, token=TOKEN_A2):
    return headers(token, **{"X-Workbench-Project": project, "X-Workbench-Package": package})


def clear_artifact_cache():
    import workbench.delta_runtime as runtime
    import workbench.hosted_object_reads as reads
    with runtime._VERIFIED_ARTIFACT_CACHE_LOCK:
        runtime._VERIFIED_ARTIFACT_CACHE.clear()
        runtime._VERIFIED_ARTIFACT_CACHE_BYTES = 0
    with reads._REVIEW_INDEX_LOCK:
        reads._REVIEW_INDEX_CACHE.clear()


def test_warm_design_index_reuses_verified_hash_but_reauthorizes_volume(backend, uploaded, tmp_path):
    import workbench.hosted_object_reads as reads
    clear_artifact_cache()
    reads._REVIEW_INDEX_CACHE.clear()
    from workbench.delta_runtime import _artifact_cache_clear_user
    _artifact_cache_clear_user('https://workspace.example.com', 'alice-id')
    project, package, index = full_index(backend, uploaded, tmp_path / 'cache-baseline')
    clear_artifact_cache()
    reads._REVIEW_INDEX_CACHE.clear()
    canonical = (index.directory / 'understanding.json.gz').read_bytes()
    backend.calls.clear()
    with browser(backend) as client:
        for _ in range(2):
            response = client.get('/api/redesigns', headers=selected(project, package))
            assert response.status_code == 200, response.text
        downloads = [path for _, action, path in backend.calls if action == 'download']
        assert sum(backend.objects[path] == canonical for path in downloads) == 1
        # A warm graph cannot grant access to another actor or package.
        assert client.get('/api/redesigns', headers=selected(project, '0' * 64)).status_code == 403
        create(client, 'Bob', TOKEN_B)
        assert client.get('/api/redesigns', headers=selected(project, package, TOKEN_B)).status_code == 404
    reads._REVIEW_INDEX_CACHE.clear()
    _artifact_cache_clear_user('https://workspace.example.com', 'alice-id')


@pytest.mark.parametrize('kind,scenario', [('agent', 'original'), ('loop', 'original'), ('loop', 'bronze')])
def test_redesign_projection_uses_verified_design_families_without_runtime_restore(
    backend, uploaded, tmp_path, monkeypatch, kind, scenario):
    from types import SimpleNamespace
    from workbench.readiness import local_evidence, current_versions
    from workbench.redesign import RedesignStore
    from workbench.tests.test_redesign import design_for
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    workflow = index.kinds['workflow'][0]['id']
    runtime = fresh(backend, tmp_path / 'seed'); runtime.restore(initialize=False)
    state = runtime.local / f'projects/{project}/package-contexts/{package}/state'
    evidence, complete = local_evidence(SimpleNamespace(state=state), package, project)
    prototype = {'workflow_id': workflow, 'scenario': scenario, 'source': {'kind': kind}}
    evidence['designs'].append(prototype)
    base = current_versions(index, evidence)[workflow, scenario]
    if kind == 'loop':
        from workbench.loops import LoopService
        loops = LoopService(SimpleNamespace(state=state), None)
        for number in range(25):
            loops._save('lessons', {'id': f'{number:032x}', 'package_hash': package,
                'workflow_id': workflow, 'status': 'approved' if number != 7 else 'rejected',
                'lesson': f'Observed feedback {number}'})
        runtime.flush()
        # Full restored ordering is the existing authority for lesson selection,
        # deliberately not insertion time, created_at or lexical lesson ID.
        runtime = fresh(backend, tmp_path / 'ordered'); runtime.restore(initialize=False)
        state = runtime.local / f'projects/{project}/package-contexts/{package}/state'
        service = SimpleNamespace(state=state, agent_inputs=lambda *args: {
            'snapshot': json.dumps({'package_hash': package}), 'input_version': base, 'input_hashes': {}})
        version = LoopService(service, None)._inputs(index, workflow, scenario)[0]['input_version']
    else:
        version = base
    saved = RedesignStore(state / 'redesigns.sqlite').append(package, workflow, scenario,
        {'role': 'architecture', 'design': design_for(index.model, workflow), 'retrieved_evidence_ids': []},
        {'record_id': 'a' * 32, 'runtime_run_id': 'b' * 32, 'submission_index': 0,
         'kind': kind, 'input_version': version}, 0)
    runtime.flush()
    before_rows, before_objects = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    def forbidden(*args, **kwargs):
        pytest.fail('Redesign read must not restore execution history, parse XML or publish')
    for method in ('restore', 'restore_audit', 'flush', 'flush_audit', '_records', '_database'):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    monkeypatch.setattr('workbench.understanding.analyze_package', forbidden)
    monkeypatch.setattr('workbench.hosted.user_app', forbidden)
    with browser(backend) as client:
        response = client.get('/api/redesigns', params={'scenario': scenario, 'workflow_id': workflow,
            'offset': 0, 'limit': 20}, headers=selected(project, package))
        assert response.status_code == 200, response.text
        listing = response.json()
        assert listing['total'] == 1 and set(listing['items'][0]['design']) == {'title'}
        assert listing['limit'] == 20 and listing['items'][0]['current_input_version'] == version
        assert not listing['items'][0]['stale']
        response = client.get('/api/redesigns/' + saved['id'], params={'version': 1}, headers=selected(project, package))
        assert response.status_code == 200, response.text
        detail = response.json()
        assert detail['source']['record_id'] == 'a' * 32 and detail['version'] == 1
        assert detail['design'] == saved['design'] and not detail['stale']
        assert not detail['artifact_completeness']['execution_ready']
        assert client.get('/api/redesigns/' + saved['id'], params={'version': 9}, headers=selected(project, package)).status_code == 404
    assert backend.rows == before_rows and backend.objects == before_objects
    statements = [sql for _, action, sql in backend.calls if action == 'sql']
    assert not any(sql.startswith(('CREATE', 'MERGE', 'INSERT', 'UPDATE', 'DELETE')) for sql in statements)
    assert not any('.`agent_runs`' in sql or '.`jobs`' in sql or '.`model_routes`' in sql
                   or '.`audit_events`' in sql or '.`loop_attempts`' in sql for sql in statements)
    if kind == 'loop':
        # A fresh committed lesson revision must invalidate the historical design,
        # without an execution-cache restore or changing the saved design itself.
        updates = {}
        for name, rows in backend.rows.items():
            if not name.endswith('.loop_lessons'):
                continue
            for row in rows:
                if row['migration_id'] != runtime.generation:
                    continue
                packed = json.loads(row['payload'])
                lesson = json.loads(packed['payload'])
                lesson['lesson'] += ' revised'
                packed['payload'] = ds._json(lesson)
                old = row['record_id'], row['content_hash']
                row['payload'] = ds._json(packed)
                row['content_hash'] = ds._content_hash(row)
                updates[old] = row['content_hash']
        def revision(manifest):
            for item in manifest['inventory']['loop_lessons']:
                key = item['record_id'], item['content_hash']
                if key in updates:
                    item['content_hash'] = updates[key]
        rewrite_manifest(runtime, backend, revision)
        revised_rows, revised_objects = deepcopy(backend.rows), deepcopy(backend.objects)
        with browser(backend) as client:
            response = client.get('/api/redesigns/' + saved['id'], headers=selected(project, package))
            assert response.status_code == 200, response.text
            value = response.json()
            assert value['current_input_version'] != version and value['stale']
            assert value['design'] == saved['design'] and not value['outline_drafting_allowed']
        assert backend.rows == revised_rows and backend.objects == revised_objects


@pytest.mark.parametrize('bad_scope', ['project', 'package', 'user'])
def test_redesign_projection_retains_fresh_scope_authority(backend, uploaded, tmp_path, bad_scope):
    project, package, index = full_index(backend, uploaded, tmp_path / 'full')
    with browser(backend) as client:
        assert client.get('/api/redesigns', headers=selected(project, package)).status_code == 200
        if bad_scope == 'user':
            create(client, 'Bob', TOKEN_B)
        response = client.get('/api/redesigns', headers=selected(
            '0' * 32 if bad_scope == 'project' else project,
            '0' * 64 if bad_scope == 'package' else package,
            TOKEN_B if bad_scope == 'user' else TOKEN_A2))
        assert response.status_code == (403 if bad_scope == 'package' else 404)


def full_index(backend, uploaded, tmp_path):
    from workbench.app import PackageIndex
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    runtime = fresh(backend, tmp_path)
    runtime.restore(initialize=False)
    directory = runtime.local / f"projects/{project}/package-contexts/{package}/packages/{package}"
    index = PackageIndex.load(directory)
    return project, package, index


def test_hosted_package_cache_is_lru_bounded_and_evicts_before_cold_load(tmp_path, monkeypatch):
    import workbench.hosted_object_reads as reads
    from types import SimpleNamespace
    reads._REVIEW_INDEX_CACHE.clear()
    evicted = []
    monkeypatch.setattr(reads, "_container_memory_state", lambda: None)
    monkeypatch.setattr("workbench.app._evict_hosted_package_indexes", lambda: evicted.append(True))
    first_key = ("scope", "volume", "project", "a" * 64)
    second_key = ("scope", "volume", "project", "b" * 64)
    first = reads._immutable_index(first_key, tmp_path / "first", lambda: SimpleNamespace(directory=None), 1024)
    second = reads._immutable_index(second_key, tmp_path / "second", lambda: SimpleNamespace(directory=None), 1024)
    assert first.directory == tmp_path / "first" and second.directory == tmp_path / "second"
    assert list(reads._REVIEW_INDEX_CACHE) == [second_key]
    assert evicted == [True, True]


def test_hosted_package_cache_reserves_container_memory(monkeypatch):
    import workbench.hosted_object_reads as reads
    gib = 1024 ** 3
    monkeypatch.setattr(reads, "_container_memory_state", lambda: (5 * gib, 6 * gib))
    with pytest.raises(Exception) as failure:
        reads._admit_package_memory(gib)
    assert getattr(failure.value, "status_code", None) == 503
    monkeypatch.setattr(reads, "_container_memory_state", lambda: (2 * gib, 6 * gib))
    reads._admit_package_memory(2 * gib)
    with pytest.raises(Exception) as oversized:
        reads._admit_package_memory(4 * gib)
    assert getattr(oversized.value, "status_code", None) == 503


@pytest.mark.parametrize("kind", ["object", "context", "target", "invocation_summary"])
def test_projection_exact_response_parity_no_full_restore_or_unrelated_artifacts(
        backend, uploaded, tmp_path, monkeypatch, kind):
    clear_artifact_cache()
    project, package, index = full_index(backend, uploaded, tmp_path / "full")
    clear_artifact_cache()
    import workbench.hosted_object_reads as reads
    reads._REVIEW_INDEX_CACHE.clear()
    identifier = index.kinds["workflow"][0]["id"]
    from workbench.target_review import target_review
    path = (f"/api/object/{identifier}" if kind == "object" else
        f"/api/workflows/{identifier}/migration-context" if kind == "context" else
        f"/api/workflows/{identifier}/target-review" if kind == "target" else
        "/api/workflow-invocation-summary")
    expected = (object_response(index, identifier, 1, 2) if kind == "object" else
        migration_response(index, identifier, 1, 2) if kind == "context" else
        target_review(index, identifier, "workflow", 1, 2) if kind == "target" else
        workflow_invocation_summary(index))
    if kind == "invocation_summary":
        with browser(backend) as setup_client:
            create(setup_client, "Summary reader", TOKEN_B)
    rows_before, objects_before = deepcopy(backend.rows), deepcopy(backend.objects)
    backend.calls.clear()
    def forbidden(*args, **kwargs):
        pytest.fail("Selected read must not restore execution/audit state, parse XML or publish")
    for method in ("restore", "restore_audit", "flush", "flush_audit", "_records", "_database"):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    verify_source = DeltaRuntime._verify_source
    verified_source = []
    def track_source_verification(runtime, reference, target=None):
        verified_source.append(reference["sha256"])
        return verify_source(runtime, reference, target)
    monkeypatch.setattr(DeltaRuntime, "_verify_source", track_source_verification)
    monkeypatch.setattr("workbench.understanding.analyze_package", forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)
    with browser(backend) as client:
        for _ in range(2):
            response = client.get(path, params={} if kind == "invocation_summary" else {"offset": 1, "limit": 2},
                                 headers=selected(project, package))
            assert response.status_code == 200, response.text
            assert response.json() == expected
    assert backend.rows == rows_before and backend.objects == objects_before
    statements = [sql for _, action, sql in backend.calls if action == "sql"]
    assert not any(sql.startswith(("CREATE", "MERGE", "INSERT", "UPDATE", "DELETE")) for sql in statements)
    assert not any(".`audit_events`" in sql or ".`jobs`" in sql or ".`model_routes`" in sql for sql in statements)
    # Each request verifies a fresh generation manifest. The same canonical gzip
    # and in-memory session artifacts are hydrated once after fresh authorization.
    downloads = [path for _, action, path in backend.calls if action == "download"]
    assert len(downloads) == 4
    assert all(backend.objects[path] != b"" for path in downloads)
    xml_downloads = [path for path in downloads if path.endswith(".xml")]
    assert len(xml_downloads) == 1
    assert len(verified_source) == 1
    import workbench.hosted_object_reads as reads
    cached = next(iter(reads._REVIEW_INDEX_CACHE.values()))
    artifacts = cached.hosted_session_artifacts
    assert {"manifest.json", "state/active.json", "source.xml", "english.md", "understanding.json.gz"} <= set(artifacts)
    assert cached.hosted_session_artifact_bytes == sum(map(len, artifacts.values()))
    prefix = f"projects/{project}/package-contexts/{package}/packages/{package}/"
    active = f"projects/{project}/package-contexts/{package}/state/active.json"
    identities = {("state/active.json" if name == active else name[len(prefix):]): digest
                  for name, _, digest, _ in cached.hosted_session_identity}
    assert set(artifacts) == set(identities)
    # These two files are reconstructed from verified Delta JSON rows and may
    # have different whitespace from their originally published bytes.
    assert json.loads(artifacts["manifest.json"]) == json.loads((index.directory / "manifest.json").read_bytes())
    assert json.loads(artifacts["state/active.json"])["sha256"] == package
    assert all(sha256(raw).hexdigest() == identities[name] for name, raw in artifacts.items()
               if name not in {"manifest.json", "state/active.json"})
    assert backend.objects[xml_downloads[0]] == artifacts["source.xml"]


def test_invocation_summary_retains_fresh_scope_authority(backend, uploaded, tmp_path, monkeypatch):
    project, package, _ = full_index(backend, uploaded, tmp_path / "full")
    with browser(backend) as setup_client:
        create(setup_client, "Summary reader", TOKEN_B)

    def forbidden(*args, **kwargs):
        pytest.fail("A denied summary read must not fall through to runtime restoration")

    monkeypatch.setattr(DeltaRuntime, "restore", forbidden)
    monkeypatch.setattr("workbench.hosted.user_app", forbidden)
    rows_before, objects_before = deepcopy(backend.rows), deepcopy(backend.objects)
    with browser(backend) as client:
        assert client.get("/api/workflow-invocation-summary",
                          headers=selected(project, package)).status_code == 200
        assert client.get("/api/workflow-invocation-summary",
                          headers=selected("0" * 32, package)).status_code == 404
        assert client.get("/api/workflow-invocation-summary",
                          headers=selected(project, "0" * 64)).status_code == 403
        assert client.get("/api/workflow-invocation-summary",
                          headers=selected(project, package, TOKEN_B)).status_code == 404
    assert backend.rows == rows_before and backend.objects == objects_before


@pytest.mark.parametrize("case", ["cross_user", "stale_package", "missing_project", "denied", "tampered", "missing_row", "artifact_corrupt"])
def test_selected_projection_fails_closed_fresh_each_request(backend, uploaded, tmp_path, monkeypatch, case):
    clear_artifact_cache()
    project, package, index = full_index(backend, uploaded, tmp_path / "full")
    clear_artifact_cache()
    import workbench.hosted_object_reads as reads
    reads._REVIEW_INDEX_CACHE.clear()
    path = "/api/object/" + index.kinds["workflow"][0]["id"]
    with browser(backend) as client:
        assert client.get(path, headers=selected(project, package)).status_code == 200
        token = TOKEN_A2
        if case == "cross_user":
            create(client, "Bob", TOKEN_B)
            token = TOKEN_B
        elif case == "stale_package":
            package = "0" * 64
        elif case == "missing_project":
            project = "0" * 32
        else:
            runtime = fresh(backend, tmp_path / "corruption")
            generation = committed(runtime)["migration_id"]
            row = next(r for name, rows in backend.rows.items() if name.endswith(".artifact_manifest") for r in rows
                       if r["migration_id"] == generation and r["source_path"].endswith("understanding.json.gz"))
            if case == "tampered":
                row["payload"] = "{}"
            elif case == "missing_row":
                next(rows for name, rows in backend.rows.items() if name.endswith(".artifact_manifest")).remove(row)
            elif case == "artifact_corrupt":
                backend.objects[row["artifact_path"]] = b"corrupt"
                # Exercise a cold materialization. A warm immutable verified
                # copy is not overwritten by later corrupt remote bytes.
                from workbench.hosted_object_reads import _REVIEW_INDEX_CACHE
                _REVIEW_INDEX_CACHE.clear()
                clear_artifact_cache()
            else:
                original = backend.client
                def revoked(token):
                    result = original(token)
                    def denied(**kwargs):
                        raise PermissionDenied("sensitive revoked privilege " + token)
                    result.statement_execution.execute_statement = denied
                    return result
                monkeypatch.setattr(backend, "client", revoked)
        response = client.get(path, headers=selected(project, package, token))
        expected = 404 if case in {"cross_user", "missing_project"} else 403 if case == "stale_package" else 503
        assert response.status_code == expected, response.text
        assert TOKEN_A2 not in response.text and "sensitive revoked" not in response.text


@pytest.mark.parametrize("field,value", [("user_id", "bob-id"), ("context_id", "wrong"), ("record_id", "0" * 64)])
def test_projection_envelopes_do_not_trust_store_filters(backend, uploaded, tmp_path, monkeypatch, field, value):
    project, package, index = full_index(backend, uploaded, tmp_path / "full")
    runtime = fresh(backend, tmp_path / "read")
    original = runtime.store.read_table
    def untrusted(table, **kwargs):
        rows = deepcopy(original(table, **kwargs))
        if table == "artifact_manifest" and rows:
            rows[0][field] = value
            rows[0]["content_hash"] = ds._content_hash(rows[0])
        return rows
    monkeypatch.setattr(runtime.store, "read_table", untrusted)
    with pytest.raises((ValueError, PermissionError)):
        object_read_response(runtime, "/api/object/" + index.kinds["workflow"][0]["id"], project, package, {})


def test_hosted_rebuild_is_explicit_full_mutation_and_projection_reads_versioned_evidence(backend, tmp_path, monkeypatch):
    with browser(backend) as client:
        project = create(client)
        response = client.post(f"/api/projects/{project}/packages/upload", params={"stage_only": "true"},
                               headers=headers(), files={"files": ("java.xml", JAVA_XML, "application/xml")})
        assert response.status_code == 202, response.text
        assert client.post(f"/api/projects/{project}/analyze", headers=headers()).status_code == 202
        package = sha256(JAVA_XML).hexdigest()
        scope = selected(project, package)
        status = client.get("/api/source-analysis", headers=scope).json()
        listing = client.get("/api/objects", params={"kind": "transformation"}, headers=scope).json()
        identifier = listing["items"][0]["id"]
        assert client.get('/api/object/' + identifier, headers=scope).status_code == 200
        from workbench.hosted_object_reads import _REVIEW_INDEX_CACHE
        previous_keys = tuple(_REVIEW_INDEX_CACHE)
        assert previous_keys
        assert all(index.model.get('source_analysis_version') is None for index in _REVIEW_INDEX_CACHE.values())
        body = {"package_hash": package, "expected_understanding_sha256": status["understanding_sha256"],
                "expected_analysis_version": None, "consent": True}
        before = fresh(backend, tmp_path / "before")._latest()["migration_id"]
        response = client.post("/api/source-analysis/rebuild", headers=scope, json=body)
        assert response.status_code == 200, response.text
        analysis_version = response.json()['analysis_version']
        after = fresh(backend, tmp_path / "after")._latest()["migration_id"]
        assert before != after
        listing = client.get("/api/objects", params={"kind": "transformation"}, headers=scope).json()
        identifier = listing["items"][0]["id"]
        canonical = client.get("/api/object/" + identifier, headers=scope)
        assert canonical.status_code == 200, canonical.text
        assert tuple(_REVIEW_INDEX_CACHE) != previous_keys
        assert {index.model.get('source_analysis_version') for index in _REVIEW_INDEX_CACHE.values()} == {analysis_version}
        assert "Thread.sleep(300000)" in canonical.text and "private-secret" not in canonical.text
        monkeypatch.setattr(DeltaRuntime, "restore", lambda *a, **k: pytest.fail("No full restore on selected read"))
        projected = client.get("/api/object/" + identifier, headers=scope)
        assert projected.status_code == 200 and projected.json() == canonical.json()


def test_selected_activation_mismatch_is_not_hidden_by_projection(backend, uploaded, tmp_path):
    project, package, index = full_index(backend, uploaded, tmp_path / "full")
    runtime = fresh(backend, tmp_path / "update")
    generation = committed(runtime)["migration_id"]
    active = f"projects/{project}/package-contexts/{package}/state/active.json"
    row = next(r for name, rows in backend.rows.items() if name.endswith(".settings") for r in rows
               if r["migration_id"] == generation and r["source_path"] == active)
    old_pair = row["record_id"], row["content_hash"]
    row["payload"] = ds._json({"sha256": "0" * 64})
    row["content_hash"] = ds._content_hash(row)
    def update(manifest):
        item = next(i for i in manifest["inventory"]["settings"]
                    if (i["record_id"], i["content_hash"]) == old_pair)
        item["content_hash"] = row["content_hash"]
    rewrite_manifest(runtime, backend, update)
    with browser(backend) as client:
        response = client.get("/api/object/" + index.kinds["workflow"][0]["id"], headers=selected(project, package))
        assert response.status_code == 409, response.text


@pytest.mark.parametrize("suffix,status", [("?offset=-1", 422), ("?limit=101", 422), ("?limit=bad", 422), ("", 404)])
def test_projection_pagination_and_unknown_object_response_semantics(backend, uploaded, suffix, status):
    project, _, detail = uploaded
    package = detail["packages"][0]["sha256"]
    with browser(backend) as client:
        response = client.get("/api/object/" + "0" * 64 + suffix, headers=selected(project, package))
        assert response.status_code == status, response.text


def test_durable_envelope_accepts_only_exact_context_read_and_explicit_rebuild_routes():
    from workbench.hosted_operations import HostedOperations
    for method, path, body in (("GET", "/api/workflows/" + "a" * 64 + "/migration-context", {}),
                               ("GET", "/api/redesigns", {}),
                               ("GET", "/api/redesigns/" + "a" * 64, {}),
                               ("POST", "/api/source-analysis/rebuild", {"consent": True})):
        request = {"id": "b" * 32, "method": method, "path": path, "body": body}
        identifier, accepted, digest = HostedOperations._request(request, ())
        assert identifier == request["id"] and accepted["path"] == path
        assert digest == ds._hash(accepted)
        for invalid in (path + "/extra", path + "?unsafe=1"):
            with pytest.raises(ValueError):
                HostedOperations._request({**request, "path": invalid}, ())