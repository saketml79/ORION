"""Local domain regressions, using canonical parser output rather than invented IR."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from workbench.domain import Store, build_proposals, common_error_candidates, impact_analysis
from workbench.understanding import analyze_package


@pytest.fixture
def model(tmp_path):
    # A and B share M. B and C share another mapping; C must NOT become an
    # impact of A merely by flooding the undirected shared-resource component.
    # A has a nested worklet binding through an explicitly qualified shortcut.
    path = tmp_path / "sample.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="Shared">
      <SOURCE NAME="Input"><SOURCEFIELD NAME="x"/></SOURCE>
      <TARGET NAME="Output"><TARGETFIELD NAME="x"/></TARGET>
      <MAPPING NAME="M">
        <INSTANCE NAME="src" TRANSFORMATION_NAME="Input" TRANSFORMATION_TYPE="Source Definition"/>
        <INSTANCE NAME="dst" TRANSFORMATION_NAME="Output" TRANSFORMATION_TYPE="Target Definition"/>
        <CONNECTOR FROMINSTANCE="src" FROMFIELD="x" TOINSTANCE="dst" TOFIELD="x"/>
      </MAPPING>
      <SESSION NAME="S" MAPPINGNAME="M"/>
      <MAPPING NAME="OnlyBC"/><SESSION NAME="BC" MAPPINGNAME="OnlyBC"/>
      <WORKFLOW NAME="B"><TASKINSTANCE NAME="run" TASKNAME="S" TASKTYPE="Session"/>
        <TASKINSTANCE NAME="bc" TASKNAME="BC" TASKTYPE="Session"/></WORKFLOW>
      <WORKFLOW NAME="C"><TASKINSTANCE NAME="bc" TASKNAME="BC" TASKTYPE="Session"/></WORKFLOW>
      </FOLDER><FOLDER NAME="Local">
      <SHORTCUT NAME="AliasS" REFERENCETYPE="SESSION" REFOBJECTNAME="S" REFFOLDERNAME="Shared" REFREPOSITORYNAME="R"/>
      <WORKLET NAME="Nested"><TASKINSTANCE NAME="run" TASKNAME="AliasS" TASKTYPE="Session"/></WORKLET>
      <WORKFLOW NAME="A"><TASKINSTANCE NAME="nested" TASKNAME="Nested" TASKTYPE="Worklet" ISENABLED="NO"/>
        <TASK NAME="cmd" TYPE="Command"><ATTRIBUTE NAME="Command" VALUE="call C"/></TASK>
        <TASKINSTANCE NAME="cmd" TASKNAME="cmd" TASKTYPE="Command"/>
        <WORKFLOWLINK FROMTASK="nested" TOTASK="cmd" CONDITION="SUCCESS"/>
      </WORKFLOW>
      <WORKFLOW NAME="Caller"><TASKINSTANCE NAME="call" TASKNAME="A" TASKTYPE="Workflow"/></WORKFLOW>
      <MAPPING NAME="M"/><SESSION NAME="OwnS" MAPPINGNAME="M"/>
      <WORKFLOW NAME="Unrelated"><TASKINSTANCE NAME="own" TASKNAME="OwnS" TASKTYPE="Session"/></WORKFLOW>
      <WORKFLOW NAME="Unknown"><TASKINSTANCE NAME="missing" TASKNAME="Missing" TASKTYPE="Session"/></WORKFLOW>
      </FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    return analyze_package(path)


def node(model, kind, name):
    return next(n for n in model["nodes"] if n["kind"] == kind and n["name"] == name)


def proposal(model, name="A", scenario="original"):
    return next(p for p in build_proposals(model, scenario) if p["name"] == name)


@pytest.fixture
def saved(tmp_path, model):
    path = tmp_path / "local.sqlite3"
    store = Store(path)
    draft = proposal(model)
    package_hash = model["package"]["sha256"]
    store.save_proposals(package_hash, [draft])
    return path, store, package_hash, draft


def test_actual_definition_graph_discovers_nested_mapping(model):
    before = deepcopy(model)
    draft = proposal(model)
    shared = next(n for n in model["nodes"] if n["kind"] == "mapping" and n["name"] == "M"
                  and n["scope"]["folder"] == "Shared")
    assert draft["mapping_ids"] == [shared["id"]]
    assert draft["sources"][0]["name"] == "Input"
    assert draft["targets"][0]["name"] == "Output"
    assert draft["version"] == 1 and draft["status"] == "provisional"
    assert draft["coverage"]["lifecycle"] == "unknown"
    assert len(draft["blockers"]) >= 4
    assert not draft["generation_allowed"] and not draft["deployment_allowed"]
    assert any(e["kind"] == "workflow_control" and e["attributes"]["CONDITION"] == "SUCCESS"
               for e in draft["graph"]["edges"])
    assert any(n["attributes"].get("ISENABLED") == "NO" for n in draft["graph"]["nodes"])
    assert not any(n["kind"] in {"folder", "repository"} for n in draft["graph"]["nodes"])
    assert draft == proposal(model)
    assert model == before
    bronze = proposal(model, scenario="bronze")
    assert bronze["id"] != draft["id"]
    assert bronze["mapping_ids"] == draft["mapping_ids"]
    for endpoint in draft["sources"] + draft["targets"] + bronze["sources"]:
        assert endpoint["binding_status"] == "unbound"
        assert endpoint["bound_table"] is None
        assert endpoint["suggested_logical_name"].startswith(("bronze.", "silver."))
        assert endpoint["evidence"]["locator"]
    assert "not bound tables" in draft["summary"]
    with pytest.raises(ValueError):
        build_proposals(model, "pretend_optimized")


def test_identical_fingerprints_never_claim_safe_merge(tmp_path):
    path = tmp_path / "identical.xml"
    path.write_text('<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">' + ''.join(
        f'<MAPPING NAME="{name}"/><SESSION NAME="S{name}" MAPPINGNAME="{name}"/>'
        for name in ("One", "Two")) + '<WORKFLOW NAME="W">'
        '<TASKINSTANCE NAME="run" TASKNAME="SOne" TASKTYPE="Session"/>'
        '</WORKFLOW></FOLDER></REPOSITORY></POWERMART>', encoding="utf-8")
    draft = build_proposals(analyze_package(path))[0]
    reuse = [a for a in draft["alternatives"] if a["kind"] == "identical_metadata"]
    assert len(reuse) == 1
    assert reuse[0]["safe_merge"] is False
    assert reuse[0]["status"] == "candidate_only_not_semantic_equivalence"
    assert len(reuse[0]["mapping_ids"]) == 2


def test_impact_exact_shared_definitions_not_whole_estate(model):
    aid = node(model, "workflow", "A")["id"]
    report = impact_analysis(model, aid)
    assert {r["name"] for r in report["shared"]} == {"B", "Caller"}
    assert not report["direct"]
    assert report["counts"]["shared"] == 2
    assert report["total_related_workflows"] == 2
    assert report["workflow_count"] == 6
    bid = node(model, "workflow", "B")["id"]
    shared = next(r for r in report["shared"] if r["workflow_id"] == bid)
    assert shared["path"]["forward"][0]["source"] == aid
    assert shared["path"]["reverse"][-1]["source"] == bid
    assert all(n not in {r["name"] for k in ("direct", "shared", "transitive") for r in report[k]}
               for n in ("C", "Unrelated", "Unknown"))
    caller = impact_analysis(model, node(model, "workflow", "Caller")["id"])
    assert {r["name"] for r in caller["direct"]} == {"A"}
    unknown = impact_analysis(model, node(model, "workflow", "Unknown")["id"])
    assert unknown["counts"]["unknown"] == 1
    assert unknown["unknown"][0]["path"]
    with pytest.raises(ValueError):
        impact_analysis(model, node(model, "source", "Input")["id"])


def test_transitive_callers_without_shared_mappings(tmp_path):
    path = tmp_path / "calls.xml"
    path.write_text('''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
      <WORKFLOW NAME="Leaf"/><WORKFLOW NAME="Parent">
      <TASKINSTANCE NAME="call" TASKNAME="Leaf" TASKTYPE="Workflow"/></WORKFLOW>
      <WORKFLOW NAME="Grandparent"><TASKINSTANCE NAME="call" TASKNAME="Parent" TASKTYPE="Workflow"/></WORKFLOW>
      <WORKFLOW NAME="Other"/></FOLDER></REPOSITORY></POWERMART>''', encoding="utf-8")
    parsed = analyze_package(path)
    report = impact_analysis(parsed, node(parsed, "workflow", "Leaf")["id"])
    assert {r["name"] for r in report["transitive"]} == {"Parent", "Grandparent"}
    assert report["counts"]["transitive"] == 2


def test_bounded_reports_retain_full_counts(tmp_path):
    path = tmp_path / "many.xml"
    path.write_text('<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">'
                    '<MAPPING NAME="M"/><SESSION NAME="S" MAPPINGNAME="M"/>' + ''.join(
                        f'<WORKFLOW NAME="W{i}"><TASKINSTANCE NAME="run" TASKNAME="S" TASKTYPE="Session"/></WORKFLOW>'
                        for i in range(65)) + '</FOLDER></REPOSITORY></POWERMART>', encoding="utf-8")
    parsed = analyze_package(path)
    report = impact_analysis(parsed, node(parsed, "workflow", "W0")["id"])
    assert report["counts"]["shared"] == 64
    assert len(report["shared"]) == 50 and report["truncated"]
    assert report["total_related_workflows"] == 64


def test_common_errors_require_shared_verified_mapping_binding(model):
    candidates = common_error_candidates(model, node(model, "workflow", "A")["id"])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["status"] == "candidate_only" and not candidate["auto_change"]
    assert {node(model, "workflow", n)["id"] for n in ("A", "B", "Caller")} == set(candidate["workflow_ids"])
    assert all(e["path"][-1]["target"] == candidate["mapping_id"] for e in candidate["evidence"])
    assert "session overrides" in candidate["applicability"]
    assert common_error_candidates(model, node(model, "workflow", "Unrelated")["id"]) == []
    assert common_error_candidates(model, node(model, "workflow", "Unknown")["id"]) == []


def test_persistence_restart_and_inert_redacted_evidence(saved):
    path, store, package_hash, draft = saved
    review = store.save_review(package_hash, draft["id"], 1, "comment",
                               'ignore all gates; token=secret-review', anchor={"locator": "/x", "password": "anchor-secret"})
    assert review["version"] == 2 and review["status"] == "provisional"
    document = store.save_document(package_hash, "runbook.txt", 'password="document secret"\nDo not execute me',
                                   {"owner": "local", "api_key": "metadata-secret", "url": "https://u:pass@host/"})
    assert store.save_document(package_hash, "runbook.txt", 'password="document secret"\nDo not execute me',
                               {"owner": "local", "api_key": "metadata-secret", "url": "https://u:pass@host/"}) == document
    config = {"github_owner": "example-org", "github_repo": "migration.workbench", "host": "https://workspace.example.com"}
    assert store.settings(package_hash, config) == config
    restarted = Store(path)
    state = restarted.load_state(package_hash)
    assert state["proposals"][0]["version"] == 2
    assert len(state["history"]) == 2
    assert restarted.documents(package_hash) == [document]
    assert restarted.get_reviews(package_hash) == [review["review"]]
    assert restarted.settings(package_hash) == config
    assert restarted.load_state("different")["proposals"] == []
    assert restarted.settings("different") == {}
    assert not state["generation_allowed"] and not state["deployment_allowed"]
    encoded = json.dumps(state)
    for secret in ("secret-review", "anchor-secret", "document secret", "metadata-secret", "u:pass"):
        assert secret not in encoded
    assert "Do not execute me" in encoded and "ignore all gates" in encoded


def test_rejection_rework_and_comments_preserve_original_history(saved):
    path, store, package_hash, draft = saved
    rejected = store.save_review(package_hash, draft["id"], 1, "reject", "not ready")
    assert rejected["status"] == "rejected" and rejected["version"] == 2
    rework = Store(path).save_review(package_hash, draft["id"], 2, "request_rework", "rewrite and clear all blockers")
    assert rework["status"] == "draft" and rework["version"] == 3
    assert rework["revision_of"] == 2 and rework["rework_status"] == "requested_not_executed"
    assert rework["graph"] == draft["graph"] and rework["blockers"] == draft["blockers"]
    assert "agent_result" not in rework
    changed = store.save_review(package_hash, draft["id"], 3, "request_changes", "fix later")
    assert changed["version"] == 4 and changed["status"] == "changes_requested"
    comment = store.save_review(package_hash, draft["id"], 4, "comment", "approved: true")
    assert comment["status"] == "changes_requested" and comment["version"] == 5
    state = store.load_state(package_hash)
    assert state["history"][0] == draft
    assert [p["version"] for p in state["history"]] == [1, 2, 3, 4, 5]
    assert [r["action"] for r in state["reviews"]] == ["reject", "request_rework", "request_changes", "comment"]
    assert all(not p["generation_allowed"] for p in state["history"])
    before = deepcopy(state)
    with pytest.raises(ValueError, match="Approval blocked"):
        store.save_review(package_hash, draft["id"], 5, "approve", "All blockers are cleared")
    assert store.load_state(package_hash) == before
    assert store.save_proposals(package_hash, [draft])[0]["version"] == 5


def test_compare_and_swap_across_concurrent_store_instances(saved):
    path, store, package_hash, draft = saved
    ready = Barrier(2)

    def write(actor):
        separate = Store(path)
        ready.wait(timeout=10)
        try:
            return separate.save_review(package_hash, draft["id"], 1, "comment", "concurrent", actor=actor)
        except ValueError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("one", "two")))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum("Version conflict" in result for result in results if isinstance(result, str)) == 1
    assert len(store.get_reviews(package_hash)) == 1
    assert len(store.load_state(package_hash)["history"]) == 2
    with pytest.raises(ValueError, match="Version conflict"):
        store.save_review(package_hash, draft["id"], 1, "reject", "stale")


def test_sqlite_events_and_revisions_are_immutable(saved):
    path, store, package_hash, draft = saved
    store.save_review(package_hash, draft["id"], 1, "comment", "history")
    for statement in ("UPDATE events SET kind='other'", "DELETE FROM events",
                      "UPDATE proposals SET version=99", "DELETE FROM proposals"):
        with sqlite3.connect(path) as db:
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                db.execute(statement)
    assert len(store.get_reviews(package_hash)) == 1


def test_no_imported_approval_or_gate_bypass_and_atomic_registration(saved):
    _, store, package_hash, draft = saved
    with pytest.raises(ValueError, match="Unknown proposal"):
        store.save_review("other", draft["id"], 1, "comment", "wrong namespace")
    other = deepcopy(draft)
    other["id"] = "new"
    altered = deepcopy(draft)
    altered["summary"] = "overwrite immutable original"
    with pytest.raises(ValueError, match="immutable"):
        store.save_proposals(package_hash, [other, altered])
    assert len(store.load_state(package_hash)["proposals"]) == 1
    other.update(status="approved", version=2)
    with pytest.raises(ValueError):
        store.save_proposals(package_hash, [other])
    other.update(status="provisional", version=1, blockers=[], generation_allowed=True, deployment_allowed=True)
    imported = store.save_proposals("other", [other])[0]
    assert len(imported["blockers"]) >= 4
    assert not imported["generation_allowed"] and not imported["deployment_allowed"]
    with pytest.raises(ValueError, match="Approval blocked"):
        store.save_review("other", "new", 1, "approve", "clear blockers")


@pytest.mark.parametrize("values", [
    {"token": "anything"}, {"github_repo": "owner/repo"}, {"github_owner": "bad--owner"},
    {"github_owner": "-bad"}, {"github_repo": ".."}, {"github_repo": "ghp_secretToken"},
    {"host": "http://example.com"}, {"host": "https://u:p@example.com"},
    {"host": "https://example.com?token=hidden"}, {"host": "https://example.com/path"},
    {"host": "https://example.com#fragment"}, {"host": "https://example.com:invalid"},
    {"host": "https://example.com\\evil"}, {"host": "https://example.com\nevil"},
])
def test_settings_reject_credentials_and_invalid_configuration(saved, values):
    _, store, package_hash, _ = saved
    before = store.load_state(package_hash)
    with pytest.raises(ValueError):
        store.settings(package_hash, values)
    assert store.load_state(package_hash) == before


def test_settings_all_supported_keys_survive_restart(saved):
    path, store, package_hash, _ = saved
    config = {
        "github_owner": "example-org", "github_repo": "migration.workbench",
        "default_branch": "feature/migration-v2", "host": "https://legacy.example.com",
        "dev_host": "https://dev.example.com:443/", "test_host": "https://test.example.com:8443",
        "prod_host": "https://prod.example.com/",
    }
    assert store.settings(package_hash, config) == config
    restarted = Store(path)
    assert restarted.settings(package_hash) == config
    assert restarted.settings(package_hash, {"default_branch": "release/2026.10"}) == {
        **config, "default_branch": "release/2026.10"}
    empty = dict.fromkeys(config, "")
    assert restarted.settings(package_hash, empty) == empty
    assert Store(path).settings(package_hash) == empty
    assert Store(path).settings("other-package") == {}


@pytest.mark.parametrize("branch", ["main", "feature/add-settings", "release/2026.10", "a/b/c", "fix_name", "topic@name"])
def test_settings_accept_safe_git_branches(saved, branch):
    _, store, package_hash, _ = saved
    assert store.settings(package_hash, {"default_branch": branch})["default_branch"] == branch


@pytest.mark.parametrize("branch", [
    "-main", "--help", "a..b", "topic@{1}", "@", "a b", "a\tb", "a\nb", "a\rb",
    "a\x00b", "a\x1fb", "a\x7fb", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b",
    "main.", "main/", "/main", "a//b", ".main", "a/.hidden", "main.lock", "a/b.lock/c",
    "a/b./c", "main\n--upload-pack=evil", "main:other", " ", None, 123,
])
def test_settings_reject_unsafe_branches_atomically(saved, branch):
    _, store, package_hash, _ = saved
    before = store.load_state(package_hash)
    with pytest.raises(ValueError):
        store.settings(package_hash, {"github_owner": "valid-owner", "default_branch": branch})
    assert store.load_state(package_hash) == before


@pytest.mark.parametrize("key", ["host", "dev_host", "test_host", "prod_host"])
@pytest.mark.parametrize("host", [
    "http://example.com", "https://user:pass@example.com", "https://user@example.com",
    "https://@example.com", "https://:pass@example.com", "https://example.com/path",
    "https://example.com//", "https://example.com?token=hidden", "https://example.com?",
    "https://example.com#fragment", "https://example.com#", "https://example.com:invalid",
    "https://example.com:", "https://example.com:0", "https://example.com:65536",
    "https://example.com:-1", "https://example.com\\evil", "https://example.com\nevil",
    "https://example.com\x00", "https://example.com\x7f", "https://example%2ecom",
    "https://example..com", "https://-example.com", "https://example-.com", "https://",
])
def test_settings_reject_invalid_origins_for_every_host_key(saved, key, host):
    _, store, package_hash, _ = saved
    before = store.load_state(package_hash)
    with pytest.raises(ValueError):
        store.settings(package_hash, {key: host})
    assert store.load_state(package_hash) == before


@pytest.mark.parametrize("key", ["host", "dev_host", "test_host", "prod_host"])
@pytest.mark.parametrize("host", ["https://localhost", "https://127.0.0.1:1/", "https://workspace.example.com:65535"])
def test_settings_accept_https_origin_ports(saved, key, host):
    _, store, package_hash, _ = saved
    assert store.settings(package_hash, {key: host}) == {key: host}


@pytest.mark.parametrize("values", [
    {"unknown": ""}, {"github_token": "anything"}, {"dev_token": "anything"},
    {"default_branch": "ghp_secretToken"}, {"dev_host": "https://github_pat_someToken.example.com"},
])
def test_settings_reject_unknown_keys_and_token_material(saved, values):
    _, store, package_hash, _ = saved
    before = store.load_state(package_hash)
    with pytest.raises(ValueError):
        store.settings(package_hash, values)
    assert store.load_state(package_hash) == before


def test_redaction_secret_patterns_and_document_validation(saved):
    _, store, package_hash, _ = saved
    document = store.save_document(package_hash, "notes.txt", '\n'.join([
        "Bearer abcdef", "ghp_someToken", "github_pat_someToken", "api_key=xyz", "--password abc",
        "jdbc:oracle:thin:credentials", "https://user:pass@example.com/",
        "-----BEGIN PRIVATE KEY-----\nmaterial\n-----END PRIVATE KEY-----",
    ]), {"nested": [{"client_secret": "hidden"}], "safe": "retained"})
    assert "material" not in document["text"] and "abcdef" not in document["text"]
    assert "someToken" not in document["text"] and "credentials" not in document["text"]
    assert document["metadata"]["nested"][0]["client_secret"] == "[REDACTED]"
    assert document["metadata"]["safe"] == "retained"
    from workbench.domain import _redact
    assert _redact({"input_tokens": 12, "total_tokens": 15, "api_token": "secret"}) == {
        "input_tokens": 12, "total_tokens": 15, "api_token": "[REDACTED]"}
    assert _redact({"input_tokens": "not-a-counter"}) == {"input_tokens": "[REDACTED]"}
    for filename in ("../escape.txt", "C:\\escape.txt", "..", "folder/name"):
        with pytest.raises(ValueError):
            store.save_document(package_hash, filename, "text", {})
    with pytest.raises(ValueError):
        Store(":memory:")


@pytest.mark.slow
def test_actual_sample_first_workflow_contract():
    path = Path(__file__).resolve().parents[2] / "input" / "LumenExample.XML"
    if not path.exists():
        pytest.skip("Source export unavailable")
    parsed = analyze_package(path)
    first = next(n for n in parsed["nodes"] if n["kind"] == "workflow")
    # Independent reference closure over the actual parser's directed relations.
    reached = {first["id"]}
    out = {}
    for edge in parsed["edges"]:
        if edge["kind"] in {"contains", "task_definition", "session_mapping", "instance_of", "shortcut_reference",
                            "session_instance", "instance_reference", "config_reference", "component_reference",
                            "connection_reference", "schedule_reference"}:
            out.setdefault(edge["source"], []).append(edge["target"])
    todo = list(reached)
    while todo:
        for target in out.get(todo.pop(), []):
            if target not in reached:
                reached.add(target)
                todo.append(target)
    expected = {n["id"] for n in parsed["nodes"] if n["kind"] == "mapping" and n["id"] in reached}
    draft = next(p for p in build_proposals(parsed) if p["workflow_id"] == first["id"])
    assert set(draft["mapping_ids"]) == expected
    assert {n["id"] for n in draft["graph"]["nodes"]} == reached
    report = impact_analysis(parsed, first["id"])
    assert report["scoped_object_count"] == len(reached)
    assert report["total_related_workflows"] < report["workflow_count"]
    print(f"Sample first workflow: {first['name']}; mappings={len(expected)}; "
          f"scoped_objects={len(reached)}; related={report['total_related_workflows']}/{report['workflow_count']}")