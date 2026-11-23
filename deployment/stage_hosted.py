"""Build an allowlisted, reviewable Apps source directory. Never deploys.

Run with the workspace venv. Destination MUST be new and outside the checkout.
Uses only stdlib and copies exactly reviewed source/static/skills, pinned
requirements and runtime/bundle templates. No glob of the repository root.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSET_SUFFIXES = {".js", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg", ".woff", ".woff2", ".ico"}


def official_skill_sources():
    """Use the catalog's single reviewed source and safety policy for staging."""
    spec = importlib.util.spec_from_file_location(
        "_staged_official_skill_library", ROOT / "workbench" / "official_skill_library.py")
    if spec is None or spec.loader is None:
        raise ValueError("External skill catalog is unavailable")
    library = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(library)
    return library, library.packaging_sources()


def origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port not in {None, 443}
            or "__" in value or any(c.isspace() for c in value)):
        raise ValueError("An explicit HTTPS origin is required")
    return "https://" + parsed.hostname.lower()


def stage(destination, workspace_host, app_origin, catalog_prefix="orion_dev_", *, warehouse="e3a0fc2c08db05bb"):
    destination = Path(destination).resolve()
    workspace_host, app_origin = origin(workspace_host), origin(app_origin)
    if ROOT == destination or ROOT in destination.parents or destination.exists():
        raise ValueError("Destination must be new and outside the source checkout")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", catalog_prefix):
        raise ValueError("Unsafe catalog prefix")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", warehouse):
        raise ValueError("Explicit SQL warehouse ID required")
    sources = list((ROOT / "workbench").glob("*.py"))
    # Reviewed sanitized lineage reference, never arbitrary output/demo exports.
    sources.append(ROOT / "workbench" / "static" / "lineage-example.json")
    for folder, suffixes in (("static", ASSET_SUFFIXES), ("skills", {".md", ".py", ".json"})):
        sources.extend(path for path in (ROOT / "workbench" / folder).rglob("*") if path.suffix.lower() in suffixes)
    for source in sources:
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents):
            raise ValueError("Packaging symlinks is forbidden")
        if not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Invalid or oversized deployment source")
    skill_library, official_sources = official_skill_sources()
    destination.mkdir(parents=True)
    try:
        for source in sources:
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for source, relative_target in official_sources:
            target = destination / "workbench" / "official_skills" / relative_target
            target.parent.mkdir(parents=True, exist_ok=True)
            # Revalidate on copy, preserve source bytes, and never execute skill code.
            target.write_bytes(skill_library._read_bytes(source))
        shutil.copyfile(ROOT / "workbench" / "requirements.txt", destination / "requirements.txt")
        provider_notices = ROOT / "workbench" / "skill_notices" / "databricks"
        for notice_name in ("LICENSE", "NOTICE", "SOURCE.json"):
            source = provider_notices / notice_name
            data = skill_library._read_bytes(source)
            target = destination / "workbench" / "official_skills" / "databricks" / notice_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for filename in ("app.yaml", "databricks.yml"):
            config = (ROOT / "deployment" / "hosted" / filename).read_text(encoding="utf-8")
            for key, value in (("__WORKSPACE_HOST__", workspace_host), ("__APP_ORIGIN__", app_origin),
                               ("__CATALOG_PREFIX__", catalog_prefix), ("__SQL_WAREHOUSE_ID__", warehouse)):
                config = config.replace('"' + key + '"', json.dumps(value))
            (destination / filename).write_text(config, encoding="utf-8")
        # Packaged operator seam only. Never added to app command/startup/resources.
        (destination / "empty-fixture-operator.json").write_text(json.dumps({
            "module": "workbench.package_fixture_bootstrap",
            "entry": ["python", "-m", "workbench.package_fixture_bootstrap"],
            "automatic": False, "deployment_orchestrator_integrated": False,
            "pending": "A separately authorized deployment wrapper must invoke this operator explicitly.",
            "instructions": [
                "Run separately under the explicit labs human user profile, never the Apps service principal.",
                "Plan with --xml external-path --output NEW-external-directory --user-label exact-login.",
                "Optional --catalog must retain the deterministic user+package namespace.",
                "Optional --assignments JSON preserves exact kind/full-XML-identity layer decisions.",
                "Review creation-manifest.json, source-tables.sql and target-tables.sql.",
                "Apply the saved plan with --apply --xml same-path --output same-directory --warehouse approved-id",
                "and --expected-plan-sha reviewed-SHA --consent 'CREATE EMPTY FIXTURES reviewed-SHA'.",
                "No data loading, auto retry, overwrite or cleanup. Reconcile uncertain receipts manually.",
                "XML and output remain external and are not shipped in Apps source."
            ]}, indent=2), encoding="utf-8")
        manifest = [{"path": path.relative_to(destination).as_posix(), "size": path.stat().st_size}
                    for path in sorted(destination.rglob("*")) if path.is_file()]
        (destination / "package-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    except Exception:
        # Only the brand-new staging directory created by this invocation.
        shutil.rmtree(destination)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    parser.add_argument("--workspace-host", required=True)
    parser.add_argument("--app-origin", required=True)
    parser.add_argument("--catalog-prefix", default="orion_dev_")
    parser.add_argument("--warehouse", default="e3a0fc2c08db05bb", help="Existing user-approved warehouse, never provisioned")
    args = parser.parse_args()
    manifest = stage(args.destination, args.workspace_host, args.app_origin, args.catalog_prefix, warehouse=args.warehouse)
    official = sum(item["path"].startswith("workbench/official_skills/") for item in manifest)
    print(f"HOSTED_PACKAGE=READY FILES={len(manifest)} OFFICIAL_SKILL_FILES={official} DEPLOYED=NO")


if __name__ == "__main__":
    main()