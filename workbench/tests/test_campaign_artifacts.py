"""Offline Option B protocol: no live auth, business SQL, Jobs or models."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from workbench import delta_state as ds
from workbench.validation_campaigns import CampaignStore, CampaignPublicationError, decode_report
from workbench.tests.test_validation_campaigns import index, report_for, FakeDelta


def saved(index):
    delta = FakeDelta()
    report = report_for(index)
    store = CampaignStore(delta=delta)
    receipt = store.save(report)
    return delta, store, report, receipt


def read(store, report):
    return store.read(report["project_id"], report["pins"]["package_hash"], report["id"])


def summary(store, report):
    return store.summary(report["project_id"], report["pins"]["package_hash"], report["id"])


def test_manifest_full_bytes_exact_retry_conflict_and_scoped_paths(index):
    delta, store, report, receipt = saved(index)
    raw = ds._json(report).encode()
    manifest = json.loads(delta.records[0]["payload"])
    assert delta.objects[receipt["artifact"]["volume_path"]] == raw
    assert manifest["artifact"]["sha256"] == sha256(raw).hexdigest()
    assert manifest["artifact"]["byte_size"] == len(raw)
    assert manifest["revision"] == report["report_sha256"]
    assert manifest["summary"]["pins"] == report["pins"]
    assert receipt["serialized_record_bytes"] == len(ds._json(delta.records[0]).encode()) < 48 * 1024
    assert len(delta.uploads) == 1 and len(delta.records) == 1
    assert store.save(report)["status"] == "published_exact_retry"
    assert len(delta.uploads) == 1 and len(delta.records) == 1
    changed = deepcopy(report)
    changed["limitations"].append("Different immutable revision")
    changed["report_sha256"] = ds._hash({k: v for k, v in changed.items() if k != "report_sha256"})
    with pytest.raises(ValueError, match="Conflicting"):
        store.save(changed)
    assert read(store, report) == report


@pytest.mark.parametrize("case", ["missing", "tampered", "truncated"])
def test_explicit_read_never_accepts_missing_or_changed_bytes(index, case):
    delta, store, report, receipt = saved(index)
    path = receipt["artifact"]["volume_path"]
    if case == "missing":
        del delta.objects[path]
    elif case == "tampered":
        delta.objects[path] = b"X" + delta.objects[path][1:]
    else:
        delta.objects[path] = delta.objects[path][:-1]
    from databricks.sdk.errors import NotFound
    with pytest.raises((ValueError, NotFound)):
        read(store, report)
    value = summary(store, report)
    if case != "tampered":
        assert value["artifact_availability"] == "unavailable"
    # Same-length tampering cannot be detected with HEAD. Never claim otherwise.
    assert value["artifact_content_verified_this_read"] is False


@pytest.mark.parametrize("case", ["owner", "workspace", "project", "package", "path", "revision", "summary", "envelope"])
def test_manifest_scope_corruption_denied_before_download(index, case):
    delta, store, report, _ = saved(index)
    row = delta.records[0]
    manifest = json.loads(row["payload"])
    if case in ("owner", "workspace", "project", "package"):
        key = {"owner": "user_id", "workspace": "workspace_id", "project": "project_id"}.get(case)
        if key:
            manifest["summary"][key] = "other"
        else:
            manifest["summary"]["pins"]["package_hash"] = "f" * 64
    elif case == "path":
        manifest["artifact"]["volume_path"] = "/Volumes/other/metadata/artifacts/blobs/" + manifest["artifact"]["sha256"]
    elif case == "revision":
        manifest["revision"] = "f" * 64
    elif case == "summary":
        manifest["summary"]["status"] = "passing"
    else:
        row["user_id"] = "other"
    row["payload"] = ds._json(manifest)
    row["content_hash"] = ds._content_hash(row)
    before = len(delta.downloads)
    with pytest.raises((ValueError, PermissionError)):
        read(store, report)
    assert len(delta.downloads) == before


def test_unauthorized_volume_read_upload_and_changed_root(index, monkeypatch):
    delta = FakeDelta()
    store = CampaignStore(delta=delta)
    report = report_for(index)
    monkeypatch.setattr(delta.client.volumes, "read", lambda n: {"owner": "other"})
    with pytest.raises(PermissionError):
        store.save(report)
    assert delta.uploads == [] and delta.records == []
    delta, store, report, _ = saved(index)
    monkeypatch.setattr(delta.client.volumes, "read", lambda n: {"owner": "other"})
    with pytest.raises(PermissionError):
        read(store, report)


@pytest.mark.parametrize("committed", [False, True])
def test_publish_failure_orphan_and_exact_retry_no_overwrite(index, monkeypatch, committed):
    delta = FakeDelta()
    store = CampaignStore(delta=delta)
    report = report_for(index)
    original = delta.upsert_records
    def fail(*args, **kwargs):
        if committed:
            original(*args, **kwargs)
        raise RuntimeError("secret=do-not-export-provider-message")
    monkeypatch.setattr(delta, "upsert_records", fail)
    with pytest.raises(CampaignPublicationError) as failed:
        store.save(report)
    assert failed.value.receipt["status"] == "manifest_publish_unconfirmed"
    assert failed.value.receipt["orphan_possible"] is True
    assert failed.value.receipt["automatic_delete_allowed"] is False
    assert "do-not-export" not in str(failed.value)
    assert len(delta.objects) == 1
    assert (read(store, report) == report) if committed else (read(store, report) is None)
    monkeypatch.setattr(delta, "upsert_records", original)
    assert store.save(report)["status"] in {"published", "published_exact_retry"}
    assert len(delta.records) == len(delta.objects) == len(delta.uploads) == 1


def test_summary_is_bounded_metadata_only_and_missing_metadata_unavailable(index, monkeypatch):
    delta, store, report, _ = saved(index)
    monkeypatch.setattr(delta.client.files, "download", lambda p: pytest.fail("Summary must not download full report"))
    assert summary(store, report)["artifact_availability"] == "available_metadata_only"
    monkeypatch.setattr(delta.client.files, "get_metadata", lambda p: {})
    assert summary(store, report)["artifact_availability"] == "unavailable"


def test_legacy_inline_and_gzip_revision_readable_with_bounds(index):
    from workbench.validation_campaigns import report_payload
    delta = FakeDelta()
    store = CampaignStore(delta=delta)
    report = report_for(index)
    delta.upsert_records("jobs", [store._record(report, report)], migration_id="validation:" + report["id"])
    assert read(store, report) == report
    delta.records.clear()
    report["historical_evidence"] = ["ref-" + str(i % 100) for i in range(15000)]
    report["report_sha256"] = ds._hash({k: v for k, v in report.items() if k != "report_sha256"})
    payload = report_payload(report)
    delta.upsert_records("jobs", [store._record(report, payload)], migration_id="validation:" + report["id"])
    assert read(store, report) == report
    with pytest.raises(ValueError):
        decode_report({"unbounded": "x" * ds.MAX_PAYLOAD})


def test_secret_rejected_before_artifact_or_manifest(index):
    delta = FakeDelta()
    report = report_for(index)
    report["secret"] = "must-not-persist"
    report["report_sha256"] = ds._hash({k: v for k, v in report.items() if k != "report_sha256"})
    with pytest.raises(ValueError):
        CampaignStore(delta=delta).save(report)
    assert not delta.objects and not delta.records


def test_bound_and_mutated_namespace_preflight_before_upload(index):
    delta = FakeDelta()
    report = report_for(index)
    store = CampaignStore(delta=delta)
    store.artifacts.root = "/Volumes/other/metadata/artifacts/users/other"
    with pytest.raises(PermissionError):
        store.save(report)
    assert not delta.objects and not delta.records
    store = CampaignStore(delta=delta)
    report['checks'].append({'pillar': 'accuracy', 'status': 'blocked', 'basis': 'x' * ds.MAX_PAYLOAD})
    report['report_sha256'] = ds._hash({k: v for k, v in report.items() if k != 'report_sha256'})
    with pytest.raises(ValueError, match='48 KiB'):
        store.save(report)
    assert not delta.objects and not delta.records


def test_failed_upload_readback_never_publishes_manifest(index, monkeypatch):
    delta = FakeDelta()
    store = CampaignStore(delta=delta)
    report = report_for(index)
    upload = delta.client.files.upload
    def truncated(path, stream, overwrite=False):
        upload(path, stream, overwrite=overwrite)
        delta.objects[path] = delta.objects[path][:-1]
    monkeypatch.setattr(delta.client.files, 'upload', truncated)
    with pytest.raises(CampaignPublicationError) as error:
        store.save(report)
    assert error.value.receipt['status'] == 'artifact_upload_unconfirmed'
    assert error.value.receipt['automatic_delete_allowed'] is False
    assert not delta.records and len(delta.objects) == 1


def test_local_backend_scope_tamper_and_legacy(index, tmp_path):
    report = report_for(index)
    store = CampaignStore(local=tmp_path / "service" / "validation-campaigns")
    store.save(report)
    assert read(store, report) == report
    with pytest.raises(ValueError):
        store.read("other", report["pins"]["package_hash"], report["id"])
    path = store.local / report["pins"]["package_hash"] / (report["id"] + ".json")
    path.write_text(ds._json(report), encoding="utf-8")
    assert read(store, report) == report