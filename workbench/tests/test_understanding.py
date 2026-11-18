"""Independent XML inventory plus structural/security regression tests."""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from defusedxml.ElementTree import iterparse
from defusedxml.common import DefusedXmlException


# Load only the requested standalone module, without depending on package init or
# altering any other workbench files created concurrently by the parent agent.
MODULE_PATH = Path(__file__).resolve().parents[1] / "understanding.py"
SPEC = importlib.util.spec_from_file_location("package_understanding_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
understanding = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = understanding
SPEC.loader.exec_module(understanding)
analyze_package = understanding.analyze_package


def package(tmp_path, body, header=""):
    path = tmp_path / "export.xml"
    path.write_text(header + '<POWERMART><REPOSITORY NAME="R">' + body + '</REPOSITORY></POWERMART>', encoding="utf-8")
    return path


def objects(model, kind, name=None):
    return [n for n in model["nodes"] if n["kind"] == kind and (name is None or n["name"] == name)]


def relations(model, kind):
    return [e for e in model["edges"] if e["kind"] == kind]


def assert_canonical(model):
    ids = {n["id"] for n in model["nodes"]}
    assert len(ids) == len(model["nodes"])
    assert len({e["id"] for e in model["edges"]}) == len(model["edges"])
    assert model["nodes"] == sorted(model["nodes"], key=lambda n: n["id"])
    assert model["edges"] == sorted(model["edges"], key=lambda e: e["id"])
    assert all(e["source"] in ids and e["target"] in ids for e in model["edges"])
    assert all(n["parent_id"] is None or n["parent_id"] in ids for n in model["nodes"])
    assert dict(Counter(n["kind"] for n in model["nodes"])) == model["counts"]
    assert model["coverage"]["captured_objects"] == len(model["nodes"])
    assert all(n["summary"] and n["evidence"]["locator"].startswith("/POWERMART[1]") for n in model["nodes"])
    assert all("line" not in n["evidence"] for n in model["nodes"])


def test_external_doctype_and_deterministic_canonical_json(tmp_path, monkeypatch):
    import socket

    def no_network(*args, **kwargs):
        raise AssertionError("DTD fetch is forbidden")

    monkeypatch.setattr(socket, "create_connection", no_network)
    path = package(tmp_path, '<FOLDER NAME="F"><SOURCE NAME="S"/></FOLDER>',
                   '<!DOCTYPE POWERMART SYSTEM "http://127.0.0.1:9/no.dtd">')
    first = analyze_package(path)
    assert json.dumps(first, sort_keys=True) == json.dumps(analyze_package(path), sort_keys=True)
    assert first["schema_version"] == "1.0"
    assert first["package"] == {"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                "size_bytes": path.stat().st_size}
    assert_canonical(first)


@pytest.mark.parametrize("declaration", [
    '<!ENTITY attack "payload">',
    '<!ENTITY attack SYSTEM "file:///not-to-be-read">',
    '<!ENTITY % attack SYSTEM "http://127.0.0.1:9/no">%attack;',
])
def test_entity_declarations_are_rejected(tmp_path, declaration):
    path = package(tmp_path, '<FOLDER NAME="F"/>', f'<!DOCTYPE POWERMART [{declaration}]>')
    with pytest.raises(DefusedXmlException):
        analyze_package(path)


def test_same_names_are_folder_repository_and_instance_qualified(tmp_path):
    body = ''.join(f'''<FOLDER NAME="{folder}"><MAPPING NAME="M"/>
        <SESSION NAME="S" MAPPINGNAME="M"/><WORKFLOW NAME="W">
        <TASKINSTANCE NAME="S1" TASKNAME="S" TASKTYPE="Session"/>
        <TASKINSTANCE NAME="S2" TASKNAME="S" TASKTYPE="Session"/>
        </WORKFLOW></FOLDER>''' for folder in ("A", "B"))
    model = analyze_package(package(tmp_path, body))
    lookup = {n["id"]: n for n in model["nodes"]}
    assert len(objects(model, "session")) == 2
    assert len(objects(model, "taskinstance")) == 4
    for kind in ("session_mapping", "task_definition"):
        for edge in relations(model, kind):
            assert lookup[edge["source"]]["scope"]["folder_id"] == lookup[edge["target"]]["scope"]["folder_id"]
    assert not model["diagnostics"]
    assert_canonical(model)
    # Identically named folders in distinct repositories cannot collide.
    path = tmp_path / "repositories.xml"
    path.write_text('<POWERMART>' + ''.join(
        f'<REPOSITORY NAME="{repo}"><FOLDER NAME="F"><MAPPING NAME="M"/>'
        '<SESSION NAME="S" MAPPINGNAME="M"/></FOLDER></REPOSITORY>' for repo in ("R1", "R2")) + '</POWERMART>')
    model = analyze_package(path)
    lookup = {n["id"]: n for n in model["nodes"]}
    for edge in relations(model, "session_mapping"):
        assert lookup[edge["source"]]["scope"]["repository_id"] == lookup[edge["target"]]["scope"]["repository_id"]


def test_missing_and_ambiguous_never_guess_cross_folder(tmp_path):
    path = package(tmp_path, '''<FOLDER NAME="A"><MAPPING NAME="Dup"/><MAPPING NAME="Dup"/>
        <SESSION NAME="Ambiguous" MAPPINGNAME="Dup"/><SESSION NAME="Missing" MAPPINGNAME="Elsewhere"/>
        </FOLDER><FOLDER NAME="B"><MAPPING NAME="Elsewhere"/></FOLDER>''')
    model = analyze_package(path)
    assert {d["code"] for d in model["diagnostics"]} == {"AMBIGUOUS_REFERENCE", "UNRESOLVED_REFERENCE"}
    assert len(objects(model, "mapping", "Dup")) == 2
    assert {n["attributes"]["status"] for n in objects(model, "unresolved_reference")} == {"ambiguous", "unresolved"}
    assert_canonical(model)


def test_control_bridge_disabled_exact_conditions_task_definitions_and_cycles(tmp_path):
    path = package(tmp_path, '''<FOLDER NAME="F"><MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>
      <WORKFLOW NAME="W"><TASK NAME="Start" TYPE="Start"/><TASK NAME="Bridge" TYPE="Command">
      <ATTRIBUTE NAME="Command" VALUE="echo ok"/></TASK><TASK NAME="Bridge" TYPE="Timer"/>
      <TASKINSTANCE NAME="Start" TASKNAME="Start" TASKTYPE="Start"/>
      <TASKINSTANCE NAME="Bridge" TASKNAME="Bridge" TASKTYPE="Command" ISENABLED="NO" TREAT_INPUTLINK_AS_AND="YES"/>
      <TASKINSTANCE NAME="Run" TASKNAME="S" TASKTYPE="Session"/>
      <WORKFLOWLINK FROMTASK="Start" TOTASK="Bridge" CONDITION="TRUE"/>
      <WORKFLOWLINK FROMTASK="Bridge" TOTASK="Run" CONDITION="$Bridge.Status = SUCCEEDED"/>
      <WORKFLOWLINK FROMTASK="Run" TOTASK="Bridge" CONDITION="retry"/>
      <SCHEDULER NAME="Scheduler"><SCHEDULEINFO SCHEDULETYPE="ONDEMAND"/></SCHEDULER>
      </WORKFLOW></FOLDER>''')
    model = analyze_package(path)
    bridge = objects(model, "taskinstance", "Bridge")[0]
    assert bridge["attributes"]["ISENABLED"] == "NO"
    assert bridge["attributes"]["TREAT_INPUTLINK_AS_AND"] == "YES"
    assert len(relations(model, "workflow_control")) == 3
    assert {e["attributes"]["CONDITION"] for e in relations(model, "workflow_control")} == {
        "TRUE", "$Bridge.Status = SUCCEEDED", "retry"}
    assert model["coverage"]["cycles"]["component_count"] == 1
    graph = understanding.to_networkx(model)
    assert graph.number_of_nodes() == len(model["nodes"])
    assert graph.number_of_edges() == len(model["edges"])
    assert len(objects(model, "scheduler")) == 1
    assert not model["diagnostics"]


def test_fields_connectors_sql_roles_reusable_and_mapplet(tmp_path):
    path = package(tmp_path, '''<FOLDER NAME="F"><SOURCE NAME="Src"><SOURCEFIELD NAME="x" DATATYPE="integer"/></SOURCE>
      <TARGET NAME="Tgt"><TARGETFIELD NAME="y" DATATYPE="integer"/></TARGET>
      <TRANSFORMATION NAME="Reuse" TYPE="Expression" REUSABLE="YES"><TRANSFORMFIELD NAME="p" EXPRESSION="x+1" PORTTYPE="INPUT/OUTPUT"/></TRANSFORMATION>
      <MAPPLET NAME="Mini"><TRANSFORMATION NAME="Inside" TYPE="Expression"><TRANSFORMFIELD NAME="p" EXPRESSION="1"/></TRANSFORMATION></MAPPLET>
      <MAPPING NAME="M"><TRANSFORMATION NAME="Local" TYPE="Source Qualifier">
      <TRANSFORMFIELD NAME="q" EXPRESSION="x"/><TABLEATTRIBUTE NAME="Sql Query" VALUE="select x from src"/>
      <TABLEATTRIBUTE NAME="Pre SQL" VALUE="delete from stage"/><TABLEATTRIBUTE NAME="Post SQL" VALUE="insert into log values (1)"/>
      <TABLEATTRIBUTE NAME="Lookup Sql Override" VALUE="select id from ref"/></TRANSFORMATION>
      <INSTANCE NAME="s" TRANSFORMATION_NAME="Src" TRANSFORMATION_TYPE="Source Definition"/>
      <INSTANCE NAME="r" TRANSFORMATION_NAME="Reuse" TRANSFORMATION_TYPE="Expression"/>
      <INSTANCE NAME="t" TRANSFORMATION_NAME="Tgt" TRANSFORMATION_TYPE="Target Definition"/>
      <INSTANCE NAME="mp" TRANSFORMATION_NAME="Mini" TRANSFORMATION_TYPE="Mapplet"/>
      <CONNECTOR FROMINSTANCE="s" FROMFIELD="x" TOINSTANCE="r" TOFIELD="p"/>
      <CONNECTOR FROMINSTANCE="r" FROMFIELD="p" TOINSTANCE="t" TOFIELD="y"/>
      </MAPPING><SESSION NAME="S" MAPPINGNAME="M"><SESSIONEXTENSION NAME="Reader" SINSTANCENAME="s">
      <CONNECTIONREFERENCE CONNECTIONNAME="db-prod-sensitive" CONNECTIONTYPE="Relational"/>
      </SESSIONEXTENSION></SESSION></FOLDER>''')
    model = analyze_package(path)
    assert len(objects(model, "source")[0]["fields"]) == 1
    assert len(objects(model, "target")[0]["fields"]) == 1
    local = objects(model, "transformation", "Local")[0]
    assert len(local["properties"]) == 4
    assert {p["attributes"]["NAME"] for p in local["properties"]} == {
        "Sql Query", "Pre SQL", "Post SQL", "Lookup Sql Override"}
    assert objects(model, "transformation", "Reuse")[0]["fields"][0]["attributes"]["EXPRESSION"] == "x+1"
    assert len(relations(model, "instance_of")) == 4
    assert len(relations(model, "field_flow")) == 2
    assert len(objects(model, "instance_field")) == 3
    assert all(n["attributes"]["status"] == "resolved" for n in objects(model, "instance_field"))
    assert len(relations(model, "session_instance")) == 1
    assert objects(model, "connectionreference")[0]["attributes"]["CONNECTIONNAME"] == "db-prod-sensitive"
    assert not relations(model, "connection_reference")
    assert not model["diagnostics"]
    assert_canonical(model)


def test_shortcuts_require_explicit_qualified_definition(tmp_path):
    path = package(tmp_path, '''<FOLDER NAME="Shared"><SOURCE NAME="Src"><SOURCEFIELD NAME="x"/></SOURCE></FOLDER>
      <FOLDER NAME="Local"><SHORTCUT NAME="Alias" REFERENCETYPE="SOURCE" REFOBJECTNAME="Src" REFFOLDERNAME="Shared" REFREPOSITORYNAME="R"/>
      <SHORTCUT NAME="Missing" REFERENCETYPE="SOURCE" REFOBJECTNAME="Src"/>
      <MAPPING NAME="M"><INSTANCE NAME="a" TRANSFORMATION_NAME="Alias" TRANSFORMATION_TYPE="Source Definition"/>
      <CONNECTOR FROMINSTANCE="a" FROMFIELD="x" TOINSTANCE="a" TOFIELD="x"/></MAPPING></FOLDER>''')
    model = analyze_package(path)
    lookup = {n["id"]: n for n in model["nodes"]}
    resolved = [e for e in relations(model, "shortcut_reference") if lookup[e["target"]]["kind"] == "source"]
    assert len(resolved) == 1
    assert len(objects(model, "unresolved_reference")) == 1
    assert objects(model, "instance_field")[0]["attributes"]["status"] == "resolved"


def test_secrets_redacted_everywhere_without_removing_sql_or_controls(tmp_path):
    path = package(tmp_path, '''<FOLDER NAME="F"><TASK NAME="Cmd" TYPE="Command" PASSWORD="super-secret-a">
      <ATTRIBUTE NAME="API token" VALUE="super-secret-b"/><ATTRIBUTE NAME="Connection String" VALUE="super-secret-c"/>
      <ATTRIBUTE NAME="Command" VALUE="curl --token super-secret-d"/>
      <ATTRIBUTE NAME="Command" VALUE="echo password=super-secret-e"/>
      <ATTRIBUTE NAME="Command" VALUE="echo https://user:super-secret-f@host/"/>
      <TABLEATTRIBUTE NAME="Sql Query" VALUE="select ordinary_column from ordinary_table"/>
      <CONNECTION USERNAME="super-secret-g" HOST="super-secret-h"/>
    <CONNECTIONREFERENCE CONNECTIONNAME="password=super-secret-i" VARIABLE="super-secret-j"/>
      <CUSTOM>super-secret-k</CUSTOM></TASK></FOLDER>''')
    model = analyze_package(path)
    encoded = json.dumps(model)
    assert "super-secret-" not in encoded
    assert "select ordinary_column from ordinary_table" in encoded
    assert len(objects(model, "task")[0]["properties"]) == 6
    assert set(model["coverage"]["lifecycle_dimensions"].values()) == {"unknown"}


def test_unknown_tags_retained_and_reported(tmp_path):
    model = analyze_package(package(tmp_path, '<FOLDER NAME="F"><FUTURE NAME="X" FLAG="on"/></FOLDER>'))
    assert model["coverage"]["unsupported_tags"] == {"FUTURE": 1}
    assert objects(model, "future")[0]["attributes"]["FLAG"] == "on"
    assert "not_semantically_translated" in model["coverage"]["semantic_status"]


@pytest.mark.parametrize("key", ["CONNECTIONNAME", "CONNECTIONVALUE"])
@pytest.mark.parametrize("value,safe", [
    ("Oracle_Prod-01", True), ("$$SourceConnection", True),
    ("password=credential-canary", False), ("Bearer credential-canary", False),
    ("jdbc:oracle:thin:@private-host:1521/db", False),
    ("odbc:private-dsn", False), ("DSN=private-dsn", False),
    ("https://private-host/db", False), ("https://user:credential-canary@host/db", False),
])
def test_connection_alias_classification_preserves_names_not_endpoints(tmp_path, key, value, safe):
    model = analyze_package(package(tmp_path, f'''<FOLDER NAME="F">
      <CONNECTIONREFERENCE {key}="{value}" PASSWORD="credential-canary"/>
      <ATTRIBUTE NAME="Connection {key.removeprefix('CONNECTION').title()}" VALUE="{value}"/>
    </FOLDER>'''))
    reference = objects(model, "connectionreference")[0]
    expected = value if safe else understanding.REDACTED
    assert reference["attributes"][key] == expected
    folder = objects(model, "folder")[0]
    assert folder["properties"][0]["attributes"]["VALUE"] == expected
    assert reference["attributes"]["PASSWORD"] == understanding.REDACTED
    assert reference["attributes"]["status_reason"] == (
        "external_connection_definition_not_exported" if safe else "connection_value_redacted")
    assert not objects(model, "unresolved_reference")
    assert not relations(model, "connection_reference")
    assert not model["diagnostics"]
    assert "credential-canary" not in json.dumps(model)
    if not safe:
        assert value not in json.dumps(model)
    assert_canonical(model)


def test_bounded_scc_and_multiedges(tmp_path):
    body = '<FOLDER NAME="F"><WORKFLOW NAME="W">'
    for i in range(6):
        body += f'<TASK NAME="t{i}" TYPE="Command"/><TASKINSTANCE NAME="t{i}" TASKNAME="t{i}" TASKTYPE="Command"/>'
        body += f'<WORKFLOWLINK FROMTASK="t{i}" TOTASK="t{i}" CONDITION="one"/>'
        body += f'<WORKFLOWLINK FROMTASK="t{i}" TOTASK="t{i}" CONDITION="two"/>'
    model = analyze_package(package(tmp_path, body + '</WORKFLOW></FOLDER>'))
    report = understanding.cycle_report(model, limit=2, member_limit=1)
    assert report["component_count"] == 6
    assert len(report["components"]) == 2 and report["truncated"]
    assert len(relations(model, "workflow_control")) == 12
    with pytest.raises(ValueError):
        understanding.cycle_report(model, member_limit=0)


def test_fingerprints_are_conservative_candidates_only(tmp_path):
    body = '<FOLDER NAME="F">' + ''.join(
        f'<MAPPING NAME="{name}"><TRANSFORMATION NAME="E" TYPE="Expression">'
        '<TRANSFORMFIELD NAME="x" EXPRESSION="1"/><TABLEATTRIBUTE NAME="Pre SQL" VALUE="select 1"/>'
        '</TRANSFORMATION><INSTANCE NAME="e" TRANSFORMATION_NAME="E" TRANSFORMATION_TYPE="Expression"/></MAPPING>'
        for name in ("A", "B")) + '</FOLDER>'
    model = analyze_package(package(tmp_path, body))
    groups = understanding.mapping_fingerprints(model)
    assert len(groups) == 1
    assert len(groups[0]["mapping_ids"]) == 2
    assert groups[0]["status"] == "candidate_only_not_semantic_equivalence"
    objects(model, "transformation")[0]["fields"][0]["attributes"]["EXPRESSION"] = "2"
    assert understanding.mapping_fingerprints(model) == []


REAL_XML = MODULE_PATH.parents[1] / "input" / "LumenExample.XML"


@pytest.fixture(scope="module")
def real_inventory():
    if not REAL_XML.exists():
        pytest.skip("Full source XML is not present")
    # This independent streaming scan intentionally does not import analyzer tag
    # classification or resolution rules. It also removes each finished element.
    counts = Counter()
    stack = []
    control_attributes = []
    with REAL_XML.open("rb") as stream:
        for event, elem in iterparse(stream, events=("start", "end"),
                                     forbid_dtd=False, forbid_entities=True, forbid_external=True):
            if event == "start":
                counts[elem.tag] += 1
                stack.append(elem)
                if elem.tag == "TASKINSTANCE":
                    control_attributes.append(dict(elem.attrib))
            else:
                stack.pop()
                elem.clear()
                if stack:
                    stack[-1].remove(elem)
    return counts, control_attributes


@pytest.fixture(scope="module")
def real_model():
    if not REAL_XML.exists():
        pytest.skip("Full source XML is not present")
    return analyze_package(REAL_XML)


@pytest.mark.slow
def test_real_source_counts_independent_streaming(real_inventory, real_model):
    counts, _ = real_inventory
    assert real_model["coverage"]["xml_elements"] == sum(counts.values())
    for tag in ("SOURCE", "TARGET", "MAPPING", "MAPPLET", "TRANSFORMATION", "INSTANCE", "SESSION",
                "WORKFLOW", "WORKLET", "TASK", "TASKINSTANCE", "SESSIONEXTENSION", "SCHEDULER", "CONNECTIONREFERENCE"):
        assert real_model["counts"].get(tag.lower(), 0) == counts[tag], tag
    assert len(relations(real_model, "data_flow")) == counts["CONNECTOR"]
    assert len(relations(real_model, "field_flow")) == counts["CONNECTOR"]
    assert len(relations(real_model, "workflow_control")) == counts["WORKFLOWLINK"]
    assert sum(len(n["fields"]) for n in real_model["nodes"]) == sum(counts[t] for t in ("SOURCEFIELD", "TARGETFIELD", "TRANSFORMFIELD"))
    assert real_model["coverage"]["unsupported_tags"] == {}


@pytest.mark.slow
def test_real_full_graph_control_preservation_and_coverage(real_inventory, real_model):
    _, originals = real_inventory
    assert_canonical(real_model)
    actual = [n["attributes"] for n in objects(real_model, "taskinstance")]
    assert Counter(json.dumps(a, sort_keys=True) for a in actual) == Counter(json.dumps(a, sort_keys=True) for a in originals)
    assert all(v == "unknown" for v in real_model["coverage"]["lifecycle_dimensions"].values())
    assert real_model["coverage"]["cycles"]["component_count"] >= 0
    assert len(real_model["coverage"]["cycles"]["components"]) <= 20
    assert all(len(c["members"]) <= 50 for c in real_model["coverage"]["cycles"]["components"])
    print(json.dumps({"package": real_model["package"], "counts": real_model["counts"],
                      "coverage": real_model["coverage"], "edges": len(real_model["edges"]),
                      "diagnostics": len(real_model["diagnostics"])}, sort_keys=True))