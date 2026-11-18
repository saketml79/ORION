"""Deployment staging is offline, allowlisted and never deploys."""
import json
from pathlib import Path
import re

import pytest

from deployment.stage_hosted import ROOT, stage


def test_staging_excludes_secrets_runtime_state_and_large_inputs(tmp_path):
    destination = tmp_path / "review-package"
    manifest = stage(destination, "https://workspace.example.com", "https://app.example.com")
    paths = {entry["path"] for entry in manifest}
    assert {"workbench/architecture_knowledge.py", "workbench/knowledge_store.py",
            "workbench/knowledge_curation.py"} <= paths
    assert {"workbench/hosted.py", "workbench/hosted_storage.py", "workbench/static/app.css",
            "workbench/static/orion-mark.svg", "app.yaml", "databricks.yml", "requirements.txt"} <= paths
    assert 'workbench/static/lineage-example.json' in paths
    reference = json.loads((destination / 'workbench/static/lineage-example.json').read_bytes())
    assert reference['example_only'] and not reference['current_user_correlation']
    assert reference['task_correspondences'] == [] and reference['execution_result'] == 'historical_mock_success'
    assert 'not a live status feed' in reference['execution_note']
    assert reference['execution_evidence']['informatica_parity'] is False
    assert not any(part in {".env", ".venv", "output", "input", "tests", "__pycache__"}
                   for path in paths for part in Path(path).parts)
    assert all(entry["size"] <= 10 * 1024 * 1024 for entry in manifest)
    requirements = (destination / "requirements.txt").read_text()
    assert "databricks-sdk==0.157.0" in requirements
    assert "langchain-openai==1.7.3" in requirements
    assert all(re.fullmatch(r'[A-Za-z0-9_-]+==[A-Za-z0-9.+-]+(?:; python_version (?:>=|<) "3\.12")?', line)
               for line in requirements.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    config = (destination / "app.yaml").read_text()
    assert "__APP_ORIGIN__" not in config and "https://app.example.com" in config
    assert "workbench.hosted" in config
    assert 'WORKBENCH_SQL_WAREHOUSE_ID' in config and 'e3a0fc2c08db05bb' in config
    assert '__SQL_WAREHOUSE_ID__' not in config
    bundle = (destination / "databricks.yml").read_text()
    assert "__APP_ORIGIN__" not in bundle and "https://app.example.com" in bundle
    assert "compute_max_instances: 1" in bundle and "config:" in bundle
    assert "unity-catalog" not in bundle and "catalog.volumes" not in bundle
    assert "- catalog.tables" in bundle and "- files" in bundle
    assert "- sql\n" in bundle and "- catalog.tables\n" in bundle
    assert "sql.statement-execution" not in bundle
    assert 'e3a0fc2c08db05bb' in bundle and '__SQL_WAREHOUSE_ID__' not in bundle
    assert json.loads((destination / "package-manifest.json").read_text()) == manifest


def test_staging_rejects_checkout_existing_folder_and_unsafe_configuration(tmp_path):
    with pytest.raises(ValueError):
        stage(ROOT / "unsafe-staging", "https://workspace.example.com", "https://app.example.com")
    with pytest.raises(ValueError):
        stage(tmp_path, "https://workspace.example.com", "https://app.example.com")
    with pytest.raises(ValueError):
        stage(tmp_path / "bad-host", "http://workspace.example.com", "https://app.example.com")
    with pytest.raises(ValueError):
        stage(tmp_path / "bad-prefix", "https://workspace.example.com", "https://app.example.com", "../bad")
    with pytest.raises(ValueError):
        stage(tmp_path / "bad-warehouse", "https://workspace.example.com", "https://app.example.com", warehouse="")


def test_staging_explicit_existing_warehouse_override(tmp_path):
    destination = tmp_path / "review-package"
    stage(destination, "https://workspace.example.com", "https://app.example.com", warehouse="reviewed-existing-id")
    for filename in ("app.yaml", "databricks.yml"):
        config = (destination / filename).read_text()
        assert 'reviewed-existing-id' in config
        assert 'e3a0fc2c08db05bb' not in config


def test_staged_python_compiles_and_every_static_asset_matches_latest_source(tmp_path):
    from hashlib import sha256
    import py_compile
    from deployment.stage_hosted import ASSET_SUFFIXES
    destination = tmp_path / "release-candidate"
    manifest = stage(destination, "https://workspace.example.com", "https://app.example.com")
    paths = {entry["path"] for entry in manifest}
    assert {"workbench/static/next-action.js", "workbench/static/next-action.css",
            "workbench/source_analysis.py", "workbench/hosted_object_reads.py", "workbench/object_views.py",
            "workbench/readiness.py", "workbench/static/readiness.js", "workbench/static/readiness.css"} <= paths
    assert {"workbench/static/migration-lineage.js", "workbench/static/migration-lineage.css",
            "workbench/static/lineage-example.json", "workbench/migration_lineage.py",
            "workbench/lineage_example.py", "workbench/demo_conversion.py", "workbench/package_parity.py"} <= paths
    python_files = sorted((destination / "workbench").glob("*.py"))
    assert python_files
    scratch = tmp_path / "compiled"
    scratch.mkdir()
    for source in python_files:
        py_compile.compile(str(source), cfile=str(scratch / (source.name + "c")), doraise=True)
    assets = [p for p in (ROOT / "workbench" / "static").rglob("*") if p.suffix.lower() in ASSET_SUFFIXES]
    assert assets
    for source in assets:
        relative = source.relative_to(ROOT).as_posix()
        assert relative in paths
        assert sha256(source.read_bytes()).digest() == sha256((destination / relative).read_bytes()).digest()
    html = (destination / "workbench" / "static" / "index.html").read_text(encoding="utf-8")
    for asset in ("/static/next-action.js", "/static/next-action.css", "/static/readiness.js", "/static/readiness.css"):
        assert asset in html
    assert not list(destination.rglob("*.pyc"))