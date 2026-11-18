"""Publish an immutable draft bundle inside the existing hosted snapshot.

This writes no workspace notebooks or Jobs and is not a deployment. The hosted
dispatcher publishes these files only when its enclosing generation commits.
An incomplete directory is never repaired or replayed automatically.
"""
from hashlib import sha256
import json
import re

from workbench.native_job_draft import build, graph_metadata
from workbench.spark_codegen import ContractError, _directory, _path


def _encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def _bundle_root(service, record):
    output = record['output']
    parts = ('generated-drafts', record['package_hash'], record['design_id'],
             str(record['design_version']), output['run_id'])
    path = service.root
    for part in parts:
        path = path / part
        if path.is_symlink() or path.resolve() != path.absolute():
            raise ContractError('Draft storage path is invalid')
    if not path.is_dir() or not path.resolve().is_relative_to(service.root.resolve()):
        raise ContractError('Draft storage is unavailable')
    return path


def prepare(record, design, result, workspace_root):
    manifest, artifacts = result['manifest'], result['artifacts']
    yaml = build(design['design'], manifest, artifacts, workspace_root)
    selected = {}
    kinds = {'notebooks': 'notebook', 'modules': 'module', 'sql': 'sql', 'tests': 'test'}
    for path, content in artifacts.items():
        kind = kinds.get(path.split('/')[0])
        if kind is None:
            raise ContractError('Unknown writer artifact type')
        _path(path, kind)
        selected[path] = content.encode('utf-8')
    selected['migration-job.yaml'] = yaml.encode('utf-8')
    receipt = {'schema_version': 1, 'job_id': record['id'],
               'package_hash': record['package_hash'], 'workflow_id': record['workflow_id'],
               'design_id': record['design_id'], 'design_version': record['design_version'],
               'input_version': record['input_version'], 'run_id': manifest['run_id'],
               'source_manifest_sha256': record['manifest_sha256'],
               'workspace_root': workspace_root,
               'files': {path: sha256(content).hexdigest() for path, content in sorted(selected.items())},
               'semantic_parity': 'not_established', 'execution_allowed': False,
               'deployment_allowed': False}
    bundle_id = sha256(_encoded(receipt)).hexdigest()
    return bundle_id, receipt, selected


def publish(service, record, design, result, workspace_root):
    bundle_id, receipt, files = prepare(record, design, result, workspace_root)
    root = _bundle_root(service, record)
    parent = _directory(root, 'native-bundles')
    directory = parent / bundle_id
    if directory.exists():
        # A previous complete publication is idempotent. A partial write is
        # uncertain and must never be silently overwritten.
        return read(service, record, design, result, bundle_id)
    directory.mkdir(exist_ok=False)
    for path, content in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(content)
    # Commit marker written last; no partially written bundle is readable.
    with (directory / 'bundle-receipt.json').open('xb') as stream:
        stream.write(_encoded(receipt))
    return read(service, record, design, result, bundle_id)


def read(service, record, design, result, bundle_id):
    if not re.fullmatch(r'[a-f0-9]{64}', bundle_id):
        raise ContractError('Invalid draft bundle ID')
    directory = _bundle_root(service, record) / 'native-bundles' / bundle_id
    if directory.is_symlink() or directory.resolve() != directory.absolute():
        raise ContractError('Draft bundle storage path is invalid')
    try:
        marker = directory / 'bundle-receipt.json'
        if marker.is_symlink() or marker.stat().st_size > 256_000:
            raise ValueError('Invalid receipt')
        raw = marker.read_bytes()
        receipt = json.loads(raw)
        if _encoded(receipt) != raw or sha256(raw).hexdigest() != bundle_id:
            raise ValueError('Receipt mismatch')
        expected_id, expected, files = prepare(record, design, result, receipt['workspace_root'])
        if expected_id != bundle_id or expected != receipt:
            raise ValueError('Bundle scope mismatch')
        for path, content in files.items():
            target = directory / path
            if any(part.is_symlink() for part in (target, *target.parents) if part != directory.parent.parent):
                raise ValueError('Symlink in bundle')
            if not target.resolve().is_relative_to(directory.resolve()):
                raise ValueError('Bundle path escaped storage')
            if target.stat().st_size != len(content) or target.read_bytes() != content:
                raise ValueError('Bundle content mismatch')
        return {'bundle_id': bundle_id, 'receipt': receipt,
            'native_jobs': graph_metadata(files['migration-job.yaml'].decode('utf-8'),
                              result['manifest'], result['artifacts'], receipt['workspace_root']),
                'artifacts': {path: content.decode('utf-8') for path, content in files.items()},
                'status': 'draft_unverified', 'remote_validation': False,
                'semantic_parity': 'not_established', 'execution_allowed': False,
                'deployment_allowed': False}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        raise ContractError('Draft bundle is incomplete, corrupted or outside the current scope; inspect before retrying') from None