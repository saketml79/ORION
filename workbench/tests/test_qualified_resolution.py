"""Qualifier-aware references must not confuse source, target or computed ports."""
import pytest

from workbench.understanding import analyze_package


def parse(tmp_path, database="ATLDEV", duplicate=False):
    source = '<SOURCE NAME="LOG" DBDNAME="ATLDEV"><SOURCEFIELD NAME="X" DATATYPE="number"/></SOURCE>'
    path = tmp_path / "qualified.xml"
    path.write_text(f'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      {source}{source if duplicate else ''}
      <SOURCE NAME="LOG" DBDNAME="OTHER"><SOURCEFIELD NAME="X" DATATYPE="varchar"/></SOURCE>
      <TARGET NAME="LOG"><TARGETFIELD NAME="X" DATATYPE="number"/></TARGET>
      <MAPPING NAME="M">
        <TRANSFORMATION NAME="expr" TYPE="Expression"><TRANSFORMFIELD NAME="Y" EXPRESSION="X + 1"/></TRANSFORMATION>
        <INSTANCE NAME="LOG" TYPE="TARGET" TRANSFORMATION_NAME="LOG" TRANSFORMATION_TYPE="Target Definition"/>
        <INSTANCE NAME="LOG" TYPE="SOURCE" DBDNAME="{database}" TRANSFORMATION_NAME="LOG" TRANSFORMATION_TYPE="Source Definition"/>
        <INSTANCE NAME="expr" TYPE="TRANSFORMATION" TRANSFORMATION_NAME="expr" TRANSFORMATION_TYPE="Expression"/>
        <TARGETLOADORDER TARGETINSTANCE="LOG" ORDER="1"/>
        <ASSOCIATED_SOURCE_INSTANCE NAME="LOG"/>
        <CONNECTOR FROMINSTANCE="LOG" FROMINSTANCETYPE="Source Definition" FROMFIELD="X" TOINSTANCE="expr" TOINSTANCETYPE="Expression" TOFIELD="Y"/>
        <CONNECTOR FROMINSTANCE="expr" FROMINSTANCETYPE="Expression" FROMFIELD="Y" TOINSTANCE="LOG" TOINSTANCETYPE="Target Definition" TOFIELD="X"/>
      </MAPPING>
      <SESSION NAME="S" MAPPINGNAME="M">
        <SESSIONEXTENSION SINSTANCENAME="LOG" TRANSFORMATIONTYPE="Source Definition" TYPE="READER"/>
        <SESSIONEXTENSION SINSTANCENAME="LOG" TRANSFORMATIONTYPE="Target Definition" TYPE="WRITER"/>
      </SESSION>
    </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return analyze_package(path)


def test_database_and_roles_resolve_and_computed_port_stays_transformation(tmp_path):
    model = parse(tmp_path)
    nodes = {n["id"]: n for n in model["nodes"]}
    assert not model["diagnostics"]
    for edge in model["edges"]:
        if edge["kind"] == "instance_of" and nodes[edge["source"]]["attributes"].get("TYPE") == "SOURCE":
            assert nodes[edge["target"]]["attributes"]["DBDNAME"] == "ATLDEV"
        if edge["kind"] == "session_instance":
            owner, instance = nodes[edge["source"]], nodes[edge["target"]]
            assert owner["attributes"]["TRANSFORMATIONTYPE"] == instance["attributes"]["TRANSFORMATION_TYPE"]
    calculated = [n for n in nodes.values() if n["kind"] == "instance_field" and n["name"] == "Y"]
    assert calculated and all(n["attributes"]["field"]["EXPRESSION"] == "X + 1" for n in calculated)


@pytest.mark.parametrize("database,duplicate,status", [
    ("MISSING", False, "unresolved"), ("ATLDEV", True, "ambiguous"),
    ("$$DATABASE", False, "ambiguous"),
])
def test_missing_conflicting_or_runtime_qualifiers_never_pick_first(tmp_path, database, duplicate, status):
    model = parse(tmp_path, database, duplicate)
    refs = [n for n in model["nodes"] if n["kind"] == "unresolved_reference"
            and n["attributes"].get("expected_kinds") == ["shortcut", "source"]]
    assert len(refs) == 1 and refs[0]["attributes"]["status"] == status


@pytest.mark.parametrize("key", ["CONNECTIONNAME", "CONNECTIONVALUE"])
def test_connections_resolve_exact_local_or_repository_definitions(tmp_path, key):
    path = tmp_path / "connections.xml"
    path.write_text(f'''<POWERMART><REPOSITORY NAME="R">
      <CONNECTION NAME="RepoDb"/>
      <FOLDER NAME="A"><CONNECTION NAME="Db"/>
        <CONNECTIONREFERENCE {key}="Db"/><CONNECTIONREFERENCE {key}="RepoDb"/>
      </FOLDER>
      <FOLDER NAME="B"><CONNECTION NAME="Db"/>
        <CONNECTIONREFERENCE {key}="Db"/>
      </FOLDER>
    </REPOSITORY><REPOSITORY NAME="Other"><CONNECTION NAME="RepoDb"/></REPOSITORY></POWERMART>''')
    model = analyze_package(path)
    nodes = {node["id"]: node for node in model["nodes"]}
    edges = [edge for edge in model["edges"] if edge["kind"] == "connection_reference"]
    assert len(edges) == 3 and not model["diagnostics"]
    for edge in edges:
        reference, definition = nodes[edge["source"]], nodes[edge["target"]]
        assert definition["kind"] == "connection" and reference["attributes"][key] == definition["name"]
        assert definition["scope"]["repository_id"] == reference["scope"]["repository_id"]
        if definition["name"] == "Db":
            assert definition["scope"]["folder_id"] == reference["scope"]["folder_id"]


def test_connections_keep_external_runtime_and_missing_identity_distinct_from_ambiguity(tmp_path):
    path = tmp_path / "connections.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R">
      <FOLDER NAME="A"><CONNECTION NAME="Duplicate"/><CONNECTION NAME="Duplicate"/>
        <CONNECTION NAME="CaseDb"/>
        <CONNECTIONREFERENCE NAME="duplicate" CONNECTIONNAME="Duplicate"/>
        <CONNECTIONREFERENCE NAME="external" CONNECTIONNAME="Elsewhere"/>
        <CONNECTIONREFERENCE NAME="case" CONNECTIONNAME="casedb"/>
        <CONNECTIONREFERENCE NAME="parameter" CONNECTIONVALUE="$$RuntimeDb"/>
        <CONNECTIONREFERENCE NAME="missing" CNXREFNAME="DB Connection"/>
      </FOLDER><FOLDER NAME="B"><CONNECTION NAME="Elsewhere"/></FOLDER>
    </REPOSITORY></POWERMART>''')
    model = analyze_package(path)
    refs = {node["name"]: node for node in model["nodes"] if node["kind"] == "connectionreference"}
    for name in ("external", "case", "parameter"):
        assert refs[name]["attributes"]["status"] == "external"
        assert refs[name]["attributes"]["status_reason"] == "external_connection_definition_not_exported"
    assert refs["missing"]["attributes"]["status_reason"] == "connection_identifier_not_captured"
    unresolved = [node for node in model["nodes"] if node["kind"] == "unresolved_reference"]
    assert len(unresolved) == 1
    assert unresolved[0]["attributes"]["status"] == "ambiguous"
    assert len(unresolved[0]["attributes"]["candidate_ids"]) == 2
    assert [diagnostic["code"] for diagnostic in model["diagnostics"]] == ["AMBIGUOUS_REFERENCE"]