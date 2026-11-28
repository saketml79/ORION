"""Exact-source GitHub release of the original app; never manages or runs Jobs."""
import argparse
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import uuid

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.apps import AppDeployment
from databricks.sdk.service.workspace import ExportFormat, ImportFormat


ROOT = Path(__file__).resolve().parents[1]
APP = "orion-migration-workbench"
CHANGES = ("workbench/hosted.py", "workbench/static/app.js",
           "workbench/static/app.css", "workbench/static/agent-arena.js")
IDENTITY = ("name", "id", "url", "compute_min_instances", "compute_max_instances",
            "compute_size", "service_principal_id", "service_principal_client_id",
            "user_api_scopes", "effective_user_api_scopes", "resources")


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(app):
    data = app.as_dict()
    return fingerprint({key: data.get(key) for key in IDENTITY})


def entry(root, name):
    parts = PurePosixPath(name)
    if parts.is_absolute() or ".." in parts.parts or "\\" in name:
        raise ValueError("Invalid reviewed package path")
    return root.joinpath(*parts.parts)


def content(client, source, name):
    result = client.workspace.export_(source.rstrip("/") + "/" + name, format=ExportFormat.SOURCE)
    if result.content is None:
        raise ValueError(f"Source export unavailable: {name}")
    return base64.b64decode(result.content, validate=True)


def active_app(client, baseline):
    app = client.apps.get(APP)
    active = app.active_deployment
    if (app.name != APP or active is None or app.pending_deployment is not None
            or active.status.state.value != "SUCCEEDED" or app.app_status.state.value != "RUNNING"
            or active.deployment_id != baseline["deployment_id"]
            or identity(app) != baseline["identity_sha256"]):
        raise ValueError("Original app changed since review; reconcile before any upload")
    if not active.source_code_path or not active.source_code_path.startswith("/Workspace/"):
        raise ValueError("Existing Workspace source path is unavailable")
    return app, active.source_code_path


def capture(client, folder, target):
    if target.exists():
        raise ValueError("Baseline already exists; do not overwrite reviewed evidence")
    app = client.apps.get(APP)
    if app.active_deployment is None or app.active_deployment.status.state.value != "SUCCEEDED":
        raise ValueError("Original deployment is not successful")
    manifest = json.loads((folder / "package-manifest.json").read_bytes())
    files = {}
    for item in manifest:
        data = entry(folder, item["path"]).read_bytes()
        if len(data) != item["size"] or item["path"] in files:
            raise ValueError("Reviewed baseline manifest mismatch")
        files[item["path"]] = {"size": len(data), "sha256": sha256(data).hexdigest()}
    for name in CHANGES:
        if name not in files:
            raise ValueError("Repair file missing from deployed baseline")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"format": "orion-original-app-source-v1",
        "app_name": APP, "deployment_id": app.active_deployment.deployment_id,
        "identity_sha256": identity(app), "files": files}, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(files)} reviewed source hashes; no credentials or raw identity records")


def prepare(client, baseline, output, offline):
    if output.exists():
        raise ValueError("Use a new output directory; do not overwrite a reviewed candidate")
    source = None if offline else active_app(client, baseline)[1]
    output.mkdir(parents=True)

    def copy(item):
        name, expected = item
        data = entry(offline, name).read_bytes() if offline else content(client, source, name)
        if len(data) != expected["size"] or sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Deployed baseline differs from review: {name}")
        path = entry(output, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(copy, baseline["files"].items()))
    for name in CHANGES:
        data = entry(ROOT, name).read_bytes()
        if name.endswith(".py"):
            ast.parse(data.decode("utf-8"), feature_version=(3, 11))
        entry(output, name).write_bytes(data)
    manifest = [{"path": name, "size": entry(output, name).stat().st_size,
                 "sha256": sha256(entry(output, name).read_bytes()).hexdigest()}
                for name in sorted(baseline["files"])]
    (output / "package-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    changed = {item["path"] for item in manifest
               if item["sha256"] != baseline["files"][item["path"]]["sha256"]}
    if changed != set(CHANGES):
        raise ValueError(f"Unexpected candidate change boundary: {sorted(changed)}")
    print(f"Verified {len(manifest)} source files: exactly {len(CHANGES)} repairs; all others unchanged")


def activate(client, baseline, output, approved):
    if not approved or os.environ.get("GITHUB_ACTIONS") != "true":
        raise ValueError("Activation requires GitHub Actions and explicit active-work replacement approval")
    if os.environ.get("GITHUB_SHA") != os.environ.get("ORION_REVIEWED_SHA"):
        raise ValueError("GitHub source revision is not the reviewed revision")
    app, source = active_app(client, baseline)
    manifest = json.loads((output / "package-manifest.json").read_bytes())
    reviewed = {item["path"]: item for item in manifest}
    if reviewed.keys() != baseline["files"].keys():
        raise ValueError("Candidate package inventory changed")
    for name, expected in reviewed.items():
        data = entry(output, name).read_bytes()
        if len(data) != expected["size"] or sha256(data).hexdigest() != expected["sha256"]:
            raise ValueError(f"Candidate changed after checks: {name}")
        if name not in CHANGES and expected != {"path": name, **baseline["files"][name]}:
            raise ValueError(f"Unapproved source modification: {name}")
        if name in CHANGES and data != entry(ROOT, name).read_bytes():
            raise ValueError(f"Repair source changed after review: {name}")

    def verify_baseline(item):
        name, expected = item
        if sha256(content(client, source, name)).hexdigest() != expected["sha256"]:
            raise ValueError(f"Workspace source changed before upload: {name}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(verify_baseline, baseline["files"].items()))
    for name in (*CHANGES, "package-manifest.json"):
        client.workspace.upload(source.rstrip("/") + "/" + name, entry(output, name).read_bytes(),
                                format=ImportFormat.RAW, overwrite=True)
    deployment = client.apps.deploy(APP, deployment=AppDeployment(
        deployment_id=uuid.uuid4().hex, source_code_path=source)).result(timeout=timedelta(minutes=45))
    final = client.apps.get(APP)
    if (final.active_deployment is None or final.active_deployment.deployment_id != deployment.deployment_id
            or final.active_deployment.status.state.value != "SUCCEEDED"
            or final.app_status.state.value != "RUNNING" or identity(final) != identity(app)
            or final.active_deployment.source_code_path != source):
        raise ValueError("Original-app deployment or identity verification failed")

    def verify_deployed(item):
        name, expected = item
        if sha256(content(client, source, name)).hexdigest() != expected["sha256"]:
            raise ValueError(f"Deployed source verification failed: {name}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(verify_deployed, reviewed.items()))
    print(f"Verified original app RUNNING/SUCCEEDED; deployment {deployment.deployment_id}")
    print(f"Verified all {len(reviewed)} deployed source hashes; app identity and external Job unchanged by this release")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("capture", "prepare", "activate"))
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline-base", type=Path)
    parser.add_argument("--approve-active-work-replacement", action="store_true")
    args = parser.parse_args()
    client = None if args.offline_base else WorkspaceClient()
    if args.mode == "capture":
        capture(client, args.output, args.baseline)
    else:
        baseline = json.loads(args.baseline.read_bytes())
        if baseline.get("format") != "orion-original-app-source-v1" or baseline.get("app_name") != APP:
            raise ValueError("Invalid original-app baseline")
        if args.mode == "prepare":
            prepare(client, baseline, args.output, args.offline_base)
        else:
            activate(client, baseline, args.output, args.approve_active_work_replacement)
