"""Focused offline checks. Run with -m workbench.tests.folder_migration_direct_checks.

No pytest suite, network, inference, cloud access or saved package mutations.
The optional --saved-package directory verifies an actual existing saved export.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

from fastapi import FastAPI, HTTPException
import httpx

from workbench import folder_migration as fm
from workbench.understanding import analyze_package


XML = '''<POWERMART REPOSITORY_VERSION="1"><REPOSITORY NAME="R">
<FOLDER NAME="Before"><MAPPING NAME="Ignore"/></FOLDER>
<FOLDER NAME="AnyFolder" DESCRIPTION="Captured payment processing description">
<SOURCE NAME="PAY" DATABASETYPE="Microsoft SQL Server" OWNERNAME="SAPDEV" DBDNAME="SAP">
<SOURCEFIELD NAME="ID" DATATYPE="integer" KEYTYPE="PRIMARY KEY"/>
<SOURCEFIELD NAME="VALUE" DATATYPE="string"/></SOURCE>
<TARGET NAME="TGT_PAY" DATABASETYPE="Microsoft SQL Server"><TARGETFIELD NAME="ID"/></TARGET>
<MAPPING NAME="Load_PAY">
<TRANSFORMATION NAME="SQ_PAY" TYPE="Source Qualifier"><TRANSFORMFIELD NAME="ID" PORTTYPE="INPUT/OUTPUT"/>
<TABLEATTRIBUTE NAME="Sql Query" VALUE="SELECT p.ID, p.VALUE FROM dbo.PAY p WHERE p.ID &gt; 0"/>
<TABLEATTRIBUTE NAME="Source Filter" VALUE="ID &gt; 0"/>
<TABLEATTRIBUTE NAME="Pre SQL" VALUE="DELETE FROM dbo.TGT_PAY WHERE ID &lt; 0"/>
<TABLEATTRIBUTE NAME="Post SQL" VALUE="CALL missing_proc()"/>
</TRANSFORMATION>
<TRANSFORMATION NAME="EXP_HASH" TYPE="Expression">
<TRANSFORMFIELD NAME="ID" PORTTYPE="INPUT/OUTPUT" EXPRESSION="ID"/>
<TRANSFORMFIELD NAME="VALUE" PORTTYPE="INPUT"/>
<TRANSFORMFIELD NAME="HASH_VALUE" PORTTYPE="OUTPUT" EXPRESSION="MD5(LTRIM(RTRIM(VALUE))||CHR(3)||LTRIM(RTRIM(ID)))"/>
</TRANSFORMATION>
<TRANSFORMATION NAME="JNR_PAY" TYPE="Joiner"><TABLEATTRIBUTE NAME="Join Condition" VALUE="ID=OTHER_ID"/>
<TABLEATTRIBUTE NAME="Join Type" VALUE="Master Outer Join"/></TRANSFORMATION>
<TRANSFORMATION NAME="UPD_PAY" TYPE="Update Strategy"><TABLEATTRIBUTE NAME="Update Strategy Expression" VALUE="DD_UPDATE"/></TRANSFORMATION>
<INSTANCE NAME="PAY" TRANSFORMATION_NAME="PAY" TRANSFORMATION_TYPE="Source Definition" TYPE="SOURCE"/>
<INSTANCE NAME="SQ_PAY" TRANSFORMATION_NAME="SQ_PAY" TRANSFORMATION_TYPE="Source Qualifier" TYPE="TRANSFORMATION"/>
<CONNECTOR FROMINSTANCE="PAY" FROMFIELD="ID" TOINSTANCE="SQ_PAY" TOFIELD="ID"/>
</MAPPING>
<MAPPING NAME="UnusedParameterized"><TRANSFORMATION NAME="SQ_PARAMS" TYPE="Source Qualifier">
<TABLEATTRIBUTE NAME="Sql Query" VALUE="SELECT ID FROM dbo.PAY WHERE MODE='INCREMENTAL' AND ID &gt; $$WATERMARK"/>
</TRANSFORMATION></MAPPING>
<SESSION NAME="LoadSession" MAPPINGNAME="Load_PAY">
<SESSTRANSFORMATIONINST SINSTANCENAME="TGT_PAY" TRANSFORMATIONTYPE="Target Definition">
<ATTRIBUTE NAME="Truncate target table option" VALUE="YES"/>
<ATTRIBUTE NAME="Insert" VALUE="YES"/>
</SESSTRANSFORMATIONINST></SESSION>
<TASK NAME="ChildCall" TYPE="Command"><ATTRIBUTE NAME="Command" VALUE="pmcmd startworkflow -f MissingFolder -nowait Child"/></TASK>
<WORKFLOW NAME="Parent"><TASKINSTANCE NAME="session" TASKNAME="LoadSession" TASKTYPE="Session"/>
<TASKINSTANCE NAME="child" TASKNAME="ChildCall" TASKTYPE="Command"/></WORKFLOW>
</FOLDER><FOLDER NAME="After"><MAPPING NAME="IgnoreAlso"/></FOLDER>
</REPOSITORY></POWERMART>'''


def index_for(model, directory):
    kinds = defaultdict(list)
    for node in model["nodes"]:
        kinds[node["kind"]].append(node)
    return SimpleNamespace(model=model, directory=Path(directory),
                           nodes={n["id"]: n for n in model["nodes"]}, kinds=kinds)


def check_fixture(directory):
    source = directory / "source.xml"
    source.write_text(XML, encoding="utf-8")
    model = analyze_package(source)
    index = index_for(model, directory)
    folder_id = next(n["id"] for n in model["nodes"] if n["kind"] == "folder" and n["name"] == "AnyFolder")
    before = deepcopy(model)
    source_before = source.read_bytes()
    assert fm.folder_list(index)["total"] == 3
    result = fm.generate_artifacts(index, folder_id)
    files = {a["path"]: a["content"] for a in result["artifacts"]}
    plan = json.loads(files["plan.json"])
    assert plan["counts"]["mapping"] == 2, "Unused mapping must not disappear"
    assert plan["counts"]["source"] == 1
    assert plan["business_purpose"]["captured_description"] == "Captured payment processing description"
    assert plan["table_summary"]["observed_data_row_count"] is None
    assert plan["tables"][0]["declared_source_keys"] == ["ID"]
    assert plan["tables"][0]["verified_cdc_keys"] is None
    assert plan["tables"][0]["source_qualifier_ids"], "Use actual source connectors"
    assert plan["tables"][0]["acquisition_mode"] == "unknown", "Unrelated mapping predicates must not leak"
    nonempty = [s for s in plan["sql_inventory"] if s["original_sql"].strip()]
    assert {s["role"] for s in nonempty} >= {"source_query", "pre_sql", "post_sql"}
    assert any("missing_proc" in s["original_sql"] and s["status"] == "blocked" for s in nonempty)
    assert any("$$WATERMARK" in s["original_sql"] and s["status"] == "blocked" for s in nonempty)
    assert len([p for p in files if p.startswith("captured_sql/")]) == len(nonempty)
    assert any(s["status"] == "draft_syntax_only" for s in nonempty)
    assert plan["workflow_invocations"][0]["workflow_name"] == "Child"
    assert plan["workflow_invocations"][0]["resolution"] == "unresolved_or_unsupported"
    assert {p["category"] for p in plan["patterns"]} >= {"md5_expression", "join_setting", "filter_setting", "write_behavior_setting", "pre_post_sql"}
    assert not result["deployment_allowed"] and not result["execution_permitted"] and not result["persisted"]
    assert not plan["evidence_flags"]["genie_equivalent"]
    for path, content in files.items():
        if path.endswith(".py"):
            ast.parse(content)
        if path.startswith("captured_sql/"):
            assert all(line.startswith("-- ") for line in content.splitlines())
    for record in result["manifest"]["artifacts"]:
        assert sha256(files[record["path"]].encode()).hexdigest() == record["sha256"]
    assert model == before and source.read_bytes() == source_before
    assert sorted(p.name for p in directory.iterdir()) == ["source.xml"]

    namespace = {}
    exec(compile(files["layers/stage.py"], "candidate", "exec"), namespace)
    query = next(c for c in plan["expression_candidates"] if c["name"] == "EXP_HASH")
    assert query["status"] == "candidate_syntax_only", query
    assert "MD5" in query["sql"] and "CHR(3)" in query["sql"]
    observed = []
    frame = SimpleNamespace(createOrReplaceTempView=lambda name: observed.append(("view", name)))
    spark = SimpleNamespace(table=lambda name: SimpleNamespace(columns=["ID", "VALUE"]),
                            sql=lambda sql: (observed.append(("query", sql)), frame)[1])
    try:
        namespace["candidate_frame"](spark, query["node_id"], bindings={"upstream": "ports"})
        raise AssertionError("Implicit candidate execution permitted")
    except ValueError:
        pass
    assert not observed
    namespace["register_candidate_view"](spark, query["node_id"], bindings={"upstream": "ports"}, view_name="candidate", consent=True)
    assert observed[-1] == ("view", "candidate")
    assert "__REQUIRED_UPSTREAM_VIEW__" not in observed[0][1]
    before_calls = len(observed)
    callbacks = []
    dp = SimpleNamespace(temporary_view=lambda **kw: lambda func: (callbacks.append(func), func)[1])
    namespace["register_lakeflow_candidate"](dp, spark, query["node_id"], bindings={"upstream": "ports"}, view_name="sdp_candidate", consent=True)
    assert len(observed) == before_calls
    callbacks[0]()
    assert len(observed) == before_calls + 1 and observed[-1][0] == "query"
    # Strictly unsupported expression forms retain evidence, not runnable substitutes.
    node = next(n for n in model["nodes"] if n["name"] == "EXP_HASH" and n["kind"] == "transformation")
    bad = deepcopy(node)
    bad["fields"][0]["attributes"]["EXPRESSION"] = "SELECT secret FROM table"
    assert fm._expression_candidate(bad)["status"] == "blocked"
    bad["fields"][0]["attributes"]["PORTTYPE"] = "LOCAL VARIABLE"
    assert fm._expression_candidate(bad)["status"] == "blocked"
    unknown = "f" * 64
    try:
        fm.folder_plan(index, unknown)
        raise AssertionError("Unknown folder accepted")
    except HTTPException as error:
        assert error.status_code == 404
    source.write_bytes(source_before + b" ")
    try:
        fm.folder_plan(index, folder_id)
        raise AssertionError("Corrupt saved XML accepted")
    except HTTPException as error:
        assert error.status_code == 409
    source.write_bytes(source_before)
    return index, folder_id


async def check_api(index, folder_id):
    app = FastAPI()
    service = SimpleNamespace(require=lambda: index)
    fm.install_folder_migration(app, service)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        assert (await client.get("/api/folder-migration/folders")).json()["total"] == 3
        assert (await client.get("/api/folder-migration/folders?limit=0")).status_code == 422
        assert (await client.get(f"/api/folder-migration/folders/{folder_id}")).status_code == 200
        url = f"/api/folder-migration/folders/{folder_id}/artifacts"
        for body, status in (({}, 422), ({"consent": False}, 403), ({"consent": "true"}, 422),
                             ({"consent": True, "deploy": True}, 422), ({"consent": True}, 200)):
            response = await client.post(url, json=body)
            assert response.status_code == status, response.text[:300]
            if status == 200:
                assert response.json()["artifacts"]
        def no_package():
            raise HTTPException(409, "No package is ready")
        service.require = no_package
        assert (await client.get("/api/folder-migration/folders")).status_code == 409


def check_actual(directory):
    """Use actual saved canonical folder ID, not a hardcoded test graph count."""
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    source = directory / "source.xml"
    artifacts = [directory / name for name in ("manifest.json", "source.xml", "understanding.json.gz")]
    before = {p.name: fm._digest(p) for p in artifacts}
    folder = None
    with gzip.open(directory / "understanding.json.gz", "rt", encoding="utf-8") as stream:
        for line in stream:
            if '"kind":"folder"' in line and '"name":"B_AP_INVOICELOG"' in line:
                folder = json.loads(line.strip().removesuffix(","))
                break
    assert folder is not None, "Actual folder missing in saved canonical model"
    index = index_for({"nodes": [folder], "package": {"sha256": manifest["sha256"]}}, directory)
    output = fm.generate_artifacts(index, folder["id"])
    plan = json.loads(next(a["content"] for a in output["artifacts"] if a["path"] == "plan.json"))
    counts = Counter()
    sap_sources = []
    raw_sql = []
    active = False
    # Independent streaming evidence count, not the implementation's model.
    from defusedxml.ElementTree import iterparse
    for event, elem in iterparse(source, events=("start", "end"), forbid_dtd=False,
                                 forbid_entities=True, forbid_external=True):
        if event == "start":
            if elem.tag == "FOLDER":
                active = elem.get("NAME") == "B_AP_INVOICELOG"
            if active:
                counts[elem.tag.lower()] += 1
                if elem.tag == "SOURCE" and elem.get("DATABASETYPE") == "ODBC" and "SAP" in elem.get("OWNERNAME", ""):
                    sap_sources.append(elem.get("NAME"))
                if elem.tag in {"ATTRIBUTE", "TABLEATTRIBUTE"} and "sql" in elem.get("NAME", "").lower() and elem.get("VALUE"):
                    raw_sql.append(elem.get("VALUE"))
        elif elem.tag == "FOLDER" and active:
            break
        elem.clear()
    for kind in ("mapping", "workflow", "source", "target", "session", "transformation"):
        assert plan["counts"][kind] == counts[kind], (kind, plan["counts"][kind], counts[kind])
    actual_sap = [t["name"] for t in plan["tables"] if t["database_type"] == "ODBC" and t["sap_metadata_hint"]]
    assert sorted(actual_sap) == sorted(sap_sources)
    assert len(actual_sap) == 11, "Actual source expectation, never a production hardcode"
    inventory = {s["original_sql"] for s in plan["sql_inventory"]}
    assert all(sql in inventory for sql in raw_sql), "A source SQL occurrence was omitted"
    assert all(t["row_count"] is None and t["verified_cdc_keys"] is None for t in plan["tables"])
    assert all(plan["table_summary"][k] is not None for k in ("incremental_predicate_rows", "initial_or_snapshot_predicate_rows"))
    assert any(p["category"] == "md5_expression" for p in plan["patterns"])
    assert any(p["category"] == "join_setting" for p in plan["patterns"])
    for artifact in output["artifacts"]:
        if artifact["path"].endswith(".py"):
            ast.parse(artifact["content"])
    assert before == {p.name: fm._digest(p) for p in artifacts}
    print("ACTUAL_SAVED_XML_PASS", json.dumps({"folder_id": folder["id"], "counts": {k: counts[k] for k in
        ("source", "target", "mapping", "session", "workflow")}, "sap_odbc_sources": sorted(actual_sap),
        "table_summary": plan["table_summary"], "nonempty_sql_records": sum(bool(s["original_sql"].strip()) for s in plan["sql_inventory"]),
        "translated_sql_records": sum(s["status"] == "draft_syntax_only" for s in plan["sql_inventory"]),
        "expression_candidates": sum(c["status"] == "candidate_syntax_only" for c in plan["expression_candidates"]),
        "child_invocations": len(plan["workflow_invocations"]), "artifacts": len(output["artifacts"]),
        "saved_artifact_hashes_unchanged": True}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--saved-package", type=Path)
    args = parser.parse_args()
    import asyncio
    # Initialize Windows' internal event-loop socketpair BEFORE the network guard.
    with asyncio.Runner() as runner:
        runner.get_loop()
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("Network forbidden")):
            with TemporaryDirectory(prefix="orion-folder-check-") as temporary:
                index, folder_id = check_fixture(Path(temporary))
                runner.run(check_api(index, folder_id))
            if args.saved_package:
                check_actual(args.saved_package)
    print("PASS: fixture semantics, candidate guards, strict APIs, no network or saved mutations")


if __name__ == "__main__":
    main()