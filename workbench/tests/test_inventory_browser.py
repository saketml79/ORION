"""Complete source inventory, explicit evidence pages, no conversion or writes."""
from copy import deepcopy
import json

import pytest

from workbench.app import PackageIndex, create_app
from workbench.inventory_browser import KINDS, inventory_response, inventory_detail, suggested_equivalent
from workbench.understanding import analyze_package
from workbench.tests.test_app import client_for, ready, small_xml


@pytest.fixture
def inventory_index(tmp_path):
    fields = ''.join(f'<TARGETFIELD NAME="c{i}" DATATYPE="decimal" PRECISION="18" SCALE="4" KEYTYPE="PRIMARY KEY" NULLABLE="NOTNULL"/>' for i in range(237))
    targets = ''.join(f'<TARGET NAME="T{i:03}" DATABASETYPE="Oracle" DBDNAME="DB_A">{fields if i == 0 else ""}</TARGET>' for i in range(306))
    kinds = '<MAPPING NAME="M"/><MAPPLET NAME="MP"/><WORKFLOW NAME="W"/><WORKLET NAME="WL"/><SESSION NAME="S"><ATTRIBUTE NAME="Pre SQL" VALUE="first"/><SESSIONEXTENSION NAME="Config"><ATTRIBUTE NAME="Post SQL" VALUE="last"/></SESSIONEXTENSION></SESSION><SOURCE NAME="Src" DATABASETYPE="Flat File"/><CONNECTIONREFERENCE CONNECTIONNAME="SAFE_ALIAS" CONNECTIONVALUE="jdbc:secret-host"/>'
    path = tmp_path / 'inventory.xml'
    path.write_text(f'<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="A">{targets}{kinds}</FOLDER><FOLDER NAME="B"><TARGET NAME="T000" DBDNAME="DB_B" DATABASETYPE="Flat File"/></FOLDER></REPOSITORY></POWERMART>', encoding='utf-8')
    index = PackageIndex(analyze_package(path), tmp_path)
    (tmp_path / 'manifest.json').write_text(json.dumps({'understanding_sha256': 'd' * 64}), encoding='utf-8')
    return index


@pytest.mark.parametrize('kind', sorted(KINDS))
def test_every_kind_full_identity_and_review_only_equivalent(inventory_index, kind):
    index = inventory_index
    before = deepcopy(index.model)
    page = inventory_response(index, kind)
    assert page['total'] > 0 and len(page['items']) <= 100
    record = page['items'][0]
    detail = inventory_detail(index, kind, record['id'], input_version=page['input_version'])
    assert detail['node']['id'] == record['id'] and detail['node']['scope'] == record['scope']
    assert detail['suggested_equivalent']['label'] == 'Suggested Databricks equivalent'
    assert 'Requires review' in detail['suggested_equivalent']['limitations']
    assert index.model == before
    assert 'jdbc:secret-host' not in json.dumps(detail)


def test_all_307_stable_pages_full_columns_and_duplicate_names(inventory_index):
    index = inventory_index
    first = inventory_response(index, 'target')
    pages = [first] + [inventory_response(index, 'target', offset=n, input_version=first['input_version']) for n in (100, 200, 300)]
    rows = [r for p in pages for r in p['items']]
    assert [len(p['items']) for p in pages] == [100, 100, 100, 7]
    assert len({r['id'] for r in rows}) == 307
    equal_names = [r for r in rows if r['name'] == 'T000']
    assert len(equal_names) == 2 and {r['scope']['folder'] for r in equal_names} == {'A', 'B'}
    assert {r['attributes']['DBDNAME'] for r in equal_names} == {'DB_A', 'DB_B'}
    chosen = next(r for r in equal_names if r['counts']['fields'] == 237)
    fields = [inventory_detail(index, 'target', chosen['id'], 'fields', n)['items'] for n in (0, 100, 200)]
    assert [len(p) for p in fields] == [100, 100, 37]
    assert fields[-1][-1]['attributes'] == {'DATATYPE': 'decimal', 'KEYTYPE': 'PRIMARY KEY', 'NAME': 'c236', 'NULLABLE': 'NOTNULL', 'PRECISION': '18', 'SCALE': '4'}
    assert all('fields' not in r and 'properties' not in r for r in rows)


def test_unbounded_values_and_nested_configuration_are_reachable(inventory_index):
    index = inventory_index
    session = index.kinds['session'][0]
    session['properties'][0]['attributes']['VALUE'] = 'SELECT ' + 'x' * 14000
    detail = inventory_detail(index, 'session', session['id'], 'properties')
    assert len(detail['items'][0]['attributes']['VALUE']) == 14007
    evidence = inventory_detail(index, 'session', session['id'], 'evidence')
    assert any(n['kind'] == 'sessionextension' and n['properties'][0]['attributes']['VALUE'] == 'last' for n in evidence['items'])
    assert 'order' in detail['suggested_equivalent']['rationale']


def test_file_delivery_not_automatic_delta_and_source_metadata(inventory_index):
    index = inventory_index
    file_target = next(n for n in index.kinds['target'] if n['attributes']['DATABASETYPE'] == 'Flat File')
    assert 'file delivery' in suggested_equivalent(file_target)['suggestion']
    assert 'not automatically' in suggested_equivalent(file_target)['rationale']
    assert 'Volume' in suggested_equivalent(index.kinds['source'][0])['suggestion']


@pytest.mark.parametrize('kwargs', [{'kind': 'anything'}, {'kind': 'target', 'q': 'x' * 201}, {'kind': 'target', 'limit': 101}, {'kind': 'target', 'offset': -1}, {'kind': 'target', 'input_version': 'old'}])
def test_invalid_or_stale_pages_fail_explicitly(inventory_index, kwargs):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        inventory_response(inventory_index, **kwargs)
    assert error.value.status_code == (409 if 'input_version' in kwargs else 422)


def test_local_http_inventory_is_additive_and_complete(ready):
    client, app = ready
    listing = client.get('/api/objects', params={'kind': 'source', 'inventory': True})
    assert listing.status_code == 200
    value = listing.json(); identifier = value['items'][0]['id']
    path = '/api/inventory/source/' + identifier
    fields = client.get(path, params={'section': 'fields', 'offset': 100, 'input_version': value['input_version']})
    assert fields.status_code == 200 and len(fields.json()['items']) == 30
    assert client.get(path, params={'input_version': 'old'}).status_code == 409
    assert client.get(path.replace('/source/', '/target/')).status_code == 404
    assert client.get('/api/objects?kind=transformation').status_code == 200
    assert client.get('/api/objects?inventory=true&kind=transformation').status_code == 422
    assert client.get(path, params={'section': 'fields', 'limit': 101}).status_code == 422


def test_hosted_inventory_reuses_projection_with_no_full_restore(backend, uploaded, tmp_path, monkeypatch):
    from workbench.tests.test_hosted_object_reads import full_index, selected
    from workbench.tests.test_hosted import browser
    from workbench.delta_runtime import DeltaRuntime
    project, package, index = full_index(backend, uploaded, tmp_path / 'baseline')
    expected = inventory_response(index, 'workflow')
    identifier = expected['items'][0]['id']
    before = deepcopy(backend.rows), deepcopy(backend.objects)
    def forbidden(*args, **kwargs):
        pytest.fail('Inventory must not restore, parse XML, construct execution runtime or write')
    for method in ('restore', 'restore_audit', 'flush', 'flush_audit', '_database', '_verify_source'):
        monkeypatch.setattr(DeltaRuntime, method, forbidden)
    monkeypatch.setattr('workbench.hosted.user_app', forbidden)
    monkeypatch.setattr('workbench.understanding.analyze_package', forbidden)
    with browser(backend) as client:
        scope = selected(project, package)
        for _ in range(2):
            response = client.get('/api/objects', params={'kind': 'workflow', 'inventory': 'true', 'limit': 100}, headers=scope)
            assert response.status_code == 200, response.text
            assert response.json() == expected
            detail = client.get('/api/inventory/workflow/' + identifier, headers=scope)
            assert detail.status_code == 200, detail.text
            assert detail.json() == inventory_detail(index, 'workflow', identifier)
        assert client.get('/api/objects?inventory=true&kind=workflow', headers=selected(project, '0' * 64)).status_code == 403
        assert client.get('/api/objects?inventory=true&kind=workflow', headers=selected('0' * 32, package)).status_code == 404
    assert (backend.rows, backend.objects) == before


@pytest.mark.parametrize('case', ['cross_user', 'revoked', 'tampered', 'incomplete'])
def test_hosted_warm_inventory_never_caches_authority(backend, uploaded, tmp_path, monkeypatch, case):
    from databricks.sdk.errors import PermissionDenied
    from workbench.tests.test_hosted_object_reads import full_index, selected
    from workbench.tests.test_hosted import browser, create, TOKEN_B, TOKEN_A2
    from workbench.tests.test_hosted_registry import fresh
    project, package, index = full_index(backend, uploaded, tmp_path / 'baseline')
    path = '/api/objects?kind=workflow&inventory=true'
    with browser(backend) as client:
        assert client.get(path, headers=selected(project, package)).status_code == 200
        token = TOKEN_A2
        if case == 'cross_user':
            create(client, 'Bob', TOKEN_B); token = TOKEN_B
        elif case == 'revoked':
            original = backend.client
            def denied_client(token):
                result = original(token)
                def denied(**kwargs):
                    raise PermissionDenied('private revoked access')
                result.statement_execution.execute_statement = denied
                return result
            monkeypatch.setattr(backend, 'client', denied_client)
        else:
            runtime = fresh(backend, tmp_path / 'corruption')
            # Resolve the current committed generation, not a local execution cache.
            generation = runtime._latest()['migration_id']
            row = next(r for name, rows in backend.rows.items() if name.endswith('.artifact_manifest') for r in rows
                       if r['migration_id'] == generation and r['source_path'].endswith('understanding.json.gz'))
            if case == 'tampered': row['payload'] = '{}'
            else: next(rows for name, rows in backend.rows.items() if name.endswith('.artifact_manifest')).remove(row)
        response = client.get(path, headers=selected(project, package, token))
        assert response.status_code == (404 if case == 'cross_user' else 503), response.text
        assert TOKEN_A2 not in response.text and 'private revoked' not in response.text


# Real OBO/Delta/Volume fixtures, no network services.
from workbench.tests.test_hosted import backend, offline
from workbench.tests.test_hosted_registry import uploaded