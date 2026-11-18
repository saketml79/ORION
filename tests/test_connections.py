"""Offline connection inventory: real XML parsing, scoped HTTP, real browser UI."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re

from fastapi.testclient import TestClient
import pytest

from workbench.app import PackageIndex, create_app
from workbench.connections import inventory
from workbench.understanding import analyze_package


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ("private-user", "private-password", "private-schema",
           "private-endpoint", "private-property", "private-token")


def xml(session="Session_A", extra=""):
    return f'''<POWERMART><REPOSITORY NAME="Repo"><FOLDER NAME="Folder">
      <SOURCE NAME="Source_Def"/><TARGET NAME="Target_Def"/>
      <CONNECTION NAME="Declared_alias" TYPE="Relational" CONNECTIONSUBTYPE="Oracle"
          USERNAME="private-user" PASSWORD="private-password" SCHEMA="private-schema" HOST="private-endpoint">
        <ATTRIBUTE NAME="Driver setting" VALUE="private-property"/>
      </CONNECTION>
      <MAPPING NAME="Mapping_A">
        <INSTANCE NAME="Source_alias" TRANSFORMATION_NAME="Source_Def" TRANSFORMATION_TYPE="Source Definition" TYPE="SOURCE"/>
        <INSTANCE NAME="Target_alias" TRANSFORMATION_NAME="Target_Def" TRANSFORMATION_TYPE="Target Definition" TYPE="TARGET"/>
      </MAPPING>
      <SESSION NAME="{session}" MAPPINGNAME="Mapping_A">
        <SESSIONEXTENSION NAME="Relational Reader" TYPE="READER" SUBTYPE="Relational Reader"
            SINSTANCENAME="Source_alias" DSQINSTNAME="SQ_Source" TRANSFORMATIONTYPE="Source Definition">
          <CONNECTIONREFERENCE NAME="Read_alias" CNXREFNAME="DB Connection" CONNECTIONTYPE="Relational"
              CONNECTIONSUBTYPE="Oracle" CONNECTIONNUMBER="1" CONNECTIONNAME="private-physical-db"
              SCHEMA="private-schema" TOKEN="private-token"/>
        </SESSIONEXTENSION>
        <SESSIONEXTENSION NAME="Relational Writer" TYPE="WRITER" SINSTANCENAME="Target_alias" TRANSFORMATIONTYPE="Target Definition">
          <CONNECTIONREFERENCE CNXREFNAME="DB Connection" CONNECTIONTYPE="Relational" CONNECTIONSUBTYPE="Sybase"
              CONNECTIONNUMBER="1" CONNECTIONNAME="private-physical-db"/>
        </SESSIONEXTENSION>
        <SESSIONEXTENSION NAME="File Reader" TYPE="READER" SINSTANCENAME="missing"/>
        {extra}
      </SESSION>
    </FOLDER></REPOSITORY></POWERMART>'''.encode()


def parse(tmp_path, data=None):
    path = tmp_path / "source.xml"
    path.write_bytes(data or xml())
    return PackageIndex(analyze_package(path), tmp_path)


def test_actual_xml_associations_and_irreversible_redactions(tmp_path):
    index = parse(tmp_path)
    before = deepcopy(index.model)
    result = inventory(index)
    assert result["total"] == 4
    assert result["source"] == "canonical_xml"
    assert not result["live_connections_implemented"]
    assert not result["connection_testing_implemented"]
    assert not result["automatic_connections"]
    reader = next(r for r in result["items"] if "NAME" in r["declared_aliases"] and r["declared_aliases"]["NAME"]["value"] == "Read_alias")
    assert reader["role"] == "reader" and reader["source_target"] == "source"
    assert reader["declared_aliases"]["CNXREFNAME"] == {"value": "DB Connection", "status": "captured"}
    assert reader["session"]["name"] == "Session_A"
    assert reader["component"]["name"] == "Relational Reader"
    assert reader["mapping"]["name"] == "Mapping_A"
    assert reader["mapping_instance"]["name"] == "Source_alias"
    assert reader["declared_aliases"]["CONNECTIONNAME"] == {"value": "private-physical-db", "status": "captured"}
    assert reader["reference_status"] == "external"
    assert reader["status_reason"] == "external_connection_definition_not_exported"
    assert reader["physical_binding"] == {"value": "unknown", "status": "not_captured"}
    assert reader["source_schema"] == {"value": "unknown", "status": "value_redacted"}
    assert any(m == {"name": "CONNECTIONSUBTYPE", "value": "Oracle", "status": "captured"} for m in reader["metadata"])
    writer = next(r for r in result["items"] if r["role"] == "writer")
    assert writer["source_target"] == "target" and writer["mapping_instance"]["name"] == "Target_alias"
    assert writer["source_schema"]["status"] == "not_captured"
    missing = next(r for r in result["items"] if r["kind"] == "sessionextension")
    assert missing["component"]["name"] == "File Reader"
    assert missing["mapping_instance"] is None
    assert missing["physical_binding"]["status"] == "not_captured"
    declaration = next(r for r in result["items"] if r["kind"] == "connection")
    assert declaration["role"] == "unknown" and declaration["session"] is None
    assert declaration["declared_aliases"]["NAME"]["value"] == "Declared_alias"
    assert all(m["value"] == "[REDACTED]" and m["status"] == "value_redacted"
               for m in declaration["metadata"] if m["name"] in {"USERNAME", "PASSWORD", "HOST", "SCHEMA", "Driver setting"})
    serialized = json.dumps(result)
    assert all(private not in serialized for private in PRIVATE)
    assert index.model == before


def test_duplicate_aliases_are_not_resolved_to_connections(tmp_path):
    extra = '''<SESSIONEXTENSION NAME="Other Reader" TYPE="READER" SINSTANCENAME="Source_alias" TRANSFORMATIONTYPE="Source Definition">
      <CONNECTIONREFERENCE CNXREFNAME="DB Connection" CONNECTIONTYPE="Relational" CONNECTIONNAME="other-private-db"/>
      <CONNECTIONREFERENCE CNXREFNAME="DB Connection" CONNECTIONTYPE="Relational" CONNECTIONNAME="third-private-db"/>
    </SESSIONEXTENSION>'''
    result = inventory(parse(tmp_path, xml(extra=extra)))
    references = [r for r in result["items"] if r["kind"] == "connectionreference"]
    assert len(references) == 4 and len({r["id"] for r in references}) == 4
    assert all(r["physical_binding"]["value"] == "unknown" for r in references)
    assert "other-private-db" in json.dumps(result)
    assert "third-private-db" in json.dumps(result)
    assert all(r["reference_status"] == "external" and r["connection_definition"] is None for r in references)


def test_inline_credential_aliases_never_serialized(tmp_path):
    data = xml().replace(b'NAME="Read_alias"', b'NAME="password=alias-private"').replace(
        b'CNXREFNAME="DB Connection"', b'CNXREFNAME="Bearer opaque-private"')
    result = inventory(parse(tmp_path, data))
    serialized = json.dumps(result)
    assert "alias-private" not in serialized and "opaque-private" not in serialized
    reference = next(r for r in result["items"] if r["kind"] == "connectionreference")
    assert reference["declared_aliases"]["CNXREFNAME"]["status"] == "value_redacted"


@pytest.mark.parametrize("key", ["NAME", "CNXREFNAME", "CONNECTIONNAME", "CONNECTIONVALUE"])
@pytest.mark.parametrize("value", ["password=inline-private", "jdbc:oracle:thin:@private-host/db",
                                   "odbc:private-dsn", "DSN=private-dsn", "https://private-host/db"])
def test_secret_and_endpoint_connection_values_remain_irreversibly_redacted(tmp_path, key, value):
    data = f'<POWERMART><CONNECTIONREFERENCE {key}="{value}"/></POWERMART>'.encode()
    index = parse(tmp_path, data)
    result = inventory(index)
    reference = result["items"][0]
    assert value not in json.dumps(index.model)
    assert value not in json.dumps(result)
    assert reference["declared_aliases"][key] == {"value": "[REDACTED]", "status": "value_redacted"}
    assert reference["physical_binding"]["status"] == (
        "value_redacted" if key in {"CONNECTIONNAME", "CONNECTIONVALUE"} else "not_captured")
    assert reference["connection_definition"] is None
    assert not index.model["diagnostics"]


def test_inventory_uses_canonical_declaration_and_preserves_runtime_alias(tmp_path):
    data = xml().replace(b'CONNECTIONNAME="private-physical-db"', b'CONNECTIONNAME="Declared_alias"', 1)
    data = data.replace(b'CONNECTIONNAME="private-physical-db"', b'CONNECTIONVALUE="$$TargetDb"')
    result = inventory(parse(tmp_path, data))
    reader = next(record for record in result["items"] if record["role"] == "reader")
    declaration = next(record for record in result["items"] if record["kind"] == "connection")
    assert reader["reference_status"] == "resolved"
    assert reader["connection_definition"] == {"id": declaration["id"], "name": "Declared_alias"}
    assert reader["physical_binding"] == {"value": "unknown", "status": "not_captured"}
    writer = next(record for record in result["items"] if record["role"] == "writer")
    assert writer["declared_aliases"]["CONNECTIONVALUE"] == {"value": "$$TargetDb", "status": "captured"}
    assert writer["reference_status"] == "external" and writer["connection_definition"] is None
    assert all(private not in json.dumps(result) for private in PRIVATE)


def test_pagination_empty_and_metadata_bounds(tmp_path):
    extra = ''.join(f'<CONNECTIONREFERENCE NAME="alias_{i}" CNXREFNAME="DB"/>' for i in range(110))
    index = parse(tmp_path, xml(extra=extra))
    first, second = inventory(index), inventory(index, 100)
    assert len(first["items"]) == 100 and second["total"] == first["total"] == 114
    assert len(second["items"]) == 14
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert inventory(index, 999)["items"] == []
    for offset, limit in [(-1, 1), (0, 0), (0, 101)]:
        with pytest.raises(ValueError):
            inventory(index, offset, limit)
    declaration = next(n for n in index.model["nodes"] if n["kind"] == "connection")
    declaration["properties"] = [{"attributes": {"NAME": f"property_{i}", "VALUE": "private-property"}} for i in range(130)]
    record = next(r for r in inventory(index, 0, 100)["items"] if r["kind"] == "connection")
    assert len(record["metadata"]) == 100 and record["metadata_truncated"]
    empty = inventory(parse(tmp_path, b'<POWERMART/>'))
    assert empty["items"] == [] and empty["total"] == 0


def test_http_requires_package_validates_bounds_and_has_no_live_action(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data", tmp_path / "unused.xml")
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        assert client.get('/api/connections').status_code == 409
        index = parse(tmp_path)
        calls = []
        def require():
            calls.append(True)
            return index
        monkeypatch.setattr(app.state.service, "require", require)
        result = client.get('/api/connections?offset=1&limit=2')
        assert result.status_code == 200 and result.json()["offset"] == 1
        assert len(result.json()["items"]) == 2 and len(calls) == 1
        for query in ('limit=101', 'limit=0', 'offset=-1', 'limit=invalid'):
            assert client.get('/api/connections?' + query).status_code == 422
        assert len(calls) == 1
        assert client.post('/api/connections', headers={"X-Workbench-Request": "1"}).status_code == 405
        assert client.get('/static/connections.js').status_code == 200


def test_existing_project_dispatch_isolates_each_selected_package(tmp_path):
    app = create_app(tmp_path / "data", tmp_path / "unused.xml")
    mutation = {"X-Workbench-Request": "1"}
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        project = client.post('/api/projects', headers=mutation, json={"name": "Inventory project"}).json()["id"]
        for name in ("Only_A", "Only_B"):
            data = xml(session=name)
            job = client.post(f'/api/projects/{project}/packages/upload', headers=mutation,
                              files=[("files", (name + '.xml', data, 'application/xml'))])
            assert job.status_code == 202
            app.state.projects.futures[job.json()["id"]].result(timeout=30)
            response = client.get('/api/connections', headers={"X-Workbench-Project": project,
                                 "X-Workbench-Package": sha256(data).hexdigest()})
            assert response.status_code == 200
            assert {r["session"]["name"] for r in response.json()["items"] if r["session"]} == {name}
            assert all(private not in response.text for private in PRIVATE)
        assert client.get('/api/connections').status_code == 409
        assert client.get('/api/connections', headers={"X-Workbench-Project": project}).status_code == 409
        assert client.get('/api/connections', headers={"X-Workbench-Package": 'a' * 64}).status_code == 400


def test_evidence_card_lazy_scoped_read_only_paging_and_stale_response(tmp_path):
    from playwright.sync_api import expect, sync_playwright
    index = parse(tmp_path)
    result = inventory(index)
    markup = (ROOT / 'workbench/static/index.html').read_text(encoding='utf-8')
    assert markup.count('src="/static/connections.js"') == 1
    markup = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>', '', markup, flags=re.S)
    # Real card markup, minimal navigation harness, no unrelated scripts/network.
    markup = markup.replace('</head>', '<script src="/static/connections.js" defer></script></head>')
    requests, pending, errors = [], [], []
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        context = browser.new_context(offline=True, service_workers='block')
        context.add_init_script('window.AbortController = class extends AbortController { abort() {} };')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request_route):
            request = request_route.request
            path = request.url.split('http://inventory.test', 1)[-1]
            if path == '/':
                request_route.fulfill(content_type='text/html', body=markup)
            elif path == '/static/connections.js':
                request_route.fulfill(content_type='text/javascript', body=(ROOT / 'workbench/static/connections.js').read_text(encoding='utf-8'))
            elif path.startswith('/api/connections?'):
                requests.append(request)
                if len(requests) == 4:
                    pending.append(request_route)
                else:
                    data = {**result, 'total': 104}
                    request_route.fulfill(json=data)
            else:
                request_route.abort()
        page.route('**/*', route)
        page.goto('http://inventory.test/')
        assert requests == []
        page.evaluate("document.getElementById('page-evidence').hidden = false")
        expect(page.locator('#connections-status')).to_contain_text('of 104')
        assert len(requests) == 1
        expect(page.get_by_role('heading', name='Source & target connections', exact=True)).to_be_visible()
        expect(page.locator('#connections-list')).to_contain_text('Read_alias')
        expect(page.locator('#connections-list')).to_contain_text('Session_A')
        expect(page.locator('#connections-list')).to_contain_text('value_redacted')
        assert all(private not in page.locator('#connections-card').inner_text() for private in PRIVATE)
        page.get_by_role('button', name='Next connections', exact=True).click()
        expect(page.locator('#connections-status')).to_contain_text('101-104')
        assert 'offset=100&limit=100' in requests[-1].url
        page.evaluate("""() => {
          for (const [id, value] of [['project-select', 'project1'], ['estate-package-select', 'package1']]) {
                        let select = document.getElementById(id);
                        if (!select) { select = document.createElement('select'); select.id = id; document.body.append(select); }
                        select.append(new Option(value, value)); select.value = value;
          }
          document.getElementById('project-select').dispatchEvent(new Event('change', {bubbles: true}));
        }""")
        expect(page.locator('#connections-list')).to_be_empty()
        assert len(requests) == 2  # Context changes invalidate, never infer or fetch.
        page.get_by_role('button', name='Refresh connections', exact=True).click()
        expect(page.locator('#connections-status')).to_contain_text('of 104')
        assert requests[-1].headers['x-workbench-project'] == 'project1'
        assert requests[-1].headers['x-workbench-package'] == 'package1'
        assert all(request.method == 'GET' and request.post_data is None for request in requests)
        with page.expect_request(lambda request: '/api/connections?' in request.url):
            page.get_by_role('button', name='Refresh connections', exact=True).click()
        expect(page.locator('#connections-status')).to_contain_text('Reading captured')
        page.evaluate("document.getElementById('project-select').value = ''; document.getElementById('project-select').dispatchEvent(new Event('change', {bubbles:true}))")
        assert pending
        pending.pop().fulfill(json=result)
        expect(page.locator('#connections-status')).to_contain_text('Context changed')
        expect(page.locator('#connections-list')).to_be_empty()
        assert errors == []
        context.close()
        browser.close()