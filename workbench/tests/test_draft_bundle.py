"""Offline integrity checks for draft publication, never remote execution."""
from types import SimpleNamespace

import pytest

from workbench.draft_bundle import publish, read
from workbench.spark_codegen import ContractError
from workbench.tests.test_native_job_draft_conditions import conditional_fixture


def test_bundle_reopen_idempotency_and_tamper_detection(tmp_path):
    design, manifest, artifacts = conditional_fixture()
    manifest['run_id'] = 'c' * 32
    record = {'id': 'd' * 32, 'package_hash': 'a' * 64, 'workflow_id': 'e' * 64,
              'design_id': 'b' * 64, 'design_version': 1, 'input_version': 'f' * 64,
              'manifest_sha256': '1' * 64, 'output': {'run_id': manifest['run_id']}}
    service = SimpleNamespace(root=tmp_path)
    root = tmp_path / 'generated-drafts' / record['package_hash'] / record['design_id'] / '1' / manifest['run_id']
    root.mkdir(parents=True)
    approved = {'design': design}
    result = {'manifest': manifest, 'artifacts': artifacts}
    first = publish(service, record, approved, result, '/Workspace/Users/test')
    assert first['status'] == 'draft_unverified' and not first['deployment_allowed']
    assert publish(service, record, approved, result, '/Workspace/Users/test') == first
    assert read(service, record, approved, result, first['bundle_id']) == first
    (root / 'native-bundles' / first['bundle_id'] / 'notebooks' / 'above.ipynb').write_text('{}', encoding='utf-8')
    with pytest.raises(ContractError):
        read(service, record, approved, result, first['bundle_id'])
    with pytest.raises(ContractError):
        publish(service, record, approved, result, '/Workspace/Users/test')