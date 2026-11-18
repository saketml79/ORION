"""Export exact reviewed drafts. No remote upload, inference or execution."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile, ZIP_DEFLATED

from pydantic import BaseModel, ConfigDict, Field


class BundleSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    job_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    bundle_id: str = Field(pattern=r'^[a-f0-9]{64}$')


class ReleaseSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bundles: list[BundleSelection] = Field(min_length=1, max_length=200)
    cohort_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    consent: bool


def encoded(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'


def preview(bundles, member_ids=None):
    """Bundles have already passed current approval, manifest and byte checks."""
    files, jobs, members, scopes = {}, {}, [], set()
    for bundle in bundles:
        receipt = bundle['receipt']
        wid = receipt['workflow_id']
        if any(member['workflow_id'] == wid for member in members):
            raise ValueError('Select one exact saved design bundle per workflow')
        scopes.add(receipt['package_hash'])
        prefix = 'members/' + wid + '/'
        for path, content in bundle['artifacts'].items():
            if path != 'migration-job.yaml':
                files[prefix + path] = content
        resources = json.loads(bundle['artifacts']['migration-job.yaml'])['resources']['jobs']
        for metadata in bundle['native_jobs']:
            resource = deepcopy(resources[metadata['job_resource']])
            # Local artifact paths replace proposed remote destinations only.
            for task in resource['tasks']:
                if 'notebook_task' in task:
                    path = metadata['artifact_mapping'][task['task_key']]['notebook_path']
                    if prefix + path not in files:
                        raise ValueError('Committed task notebook missing')
                    task['notebook_task']['notebook_path'] = './' + prefix + path
                    task['notebook_task']['source'] = 'WORKSPACE'
            if any(key in resource for key in ('schedule', 'continuous', 'trigger', 'run_as')):
                raise ValueError('Automatic triggers or alternate run identity are not supported')
            resource['performance_target'] = 'STANDARD'
            resource['max_concurrent_runs'] = 1
            resource['tags'] = {**resource.get('tags', {}), 'orion_workflow': wid,
                'orion_bundle': bundle['bundle_id'], 'aidevkit_project': 'orion-reviewed-migration'}
            key = 'workflow_' + wid + '_' + metadata['job_resource']
            jobs[key] = resource
        members.append({'workflow_id': wid, 'design_id': receipt['design_id'],
            'design_version': receipt['design_version'], 'job_id': receipt['job_id'],
            'input_version': receipt['input_version'],
            'bundle_id': bundle['bundle_id'], 'source_manifest_sha256': receipt['source_manifest_sha256'],
            'status': 'exact_reviewed_draft_exported_not_executed'})
    if len(scopes) != 1:
        raise ValueError('Release bundles must share the exact package')
    selected = {row['workflow_id'] for row in members}
    expected = set(member_ids) if member_ids is not None else selected
    if not selected <= expected:
        raise ValueError('Bundle workflow is outside the chosen review scope')
    for wid in sorted(expected - selected):
        members.append({'workflow_id': wid, 'status': 'missing_exact_reviewed_bundle',
            'action': 'Review and draft this member, then select its committed bundle. No mock is substituted.'})
    package = next(iter(scopes))
    version = sha256(encoded(sorted((row['workflow_id'], row['input_version']) for row in members if 'input_version' in row)).encode()).hexdigest()
    report = {'package_hash': package, 'input_version': version, 'members': members,
        'member_count': len(expected), 'exported_member_count': len(selected),
        'artifact_scope_complete': selected == expected, 'architecture_approved': False,
        'execution_allowed': False, 'deployment_allowed': False, 'production_ready': False,
        'source_output_parity': 'not_established',
        'blockers': ['Separate artifact, binding, data-write and test-execution approval is required.',
            'No arbitrary-design deployment or output-equivalence gate exists in this interface.']}
    files['databricks.yml'] = encoded({'bundle': {'name': 'orion-reviewed-' + package[:12]},
        'resources': {'jobs': jobs}, 'targets': {'review': {'mode': 'development'}}})
    files['release-review.json'] = encoded(report)
    files['README.md'] = ('# Exact reviewed migration draft export\n\n'
        'Native Jobs and original committed notebooks, not a production release.\n'
        'STANDARD serverless: batch review, no latency SLA. No schedules or identity override.\n'
        'Use Databricks CLI bundle validate with target review and your explicit profile.\n'
        'Do not deploy or run before separate code, bindings and data-write approval.\n'
        'GitHub publication and production promotion are not performed by this export.\n'
        'Missing cohort members remain in release-review.json, never duplicated mock code.\n')
    files['.github/workflows/validate.yml'] = ('name: Validate reviewed DAB\n'
        'on: workflow_dispatch\njobs:\n  validate:\n    environment: migration-review\n'
        '    runs-on: ubuntu-latest\n    permissions:\n      contents: read\n'
        '    steps:\n      - uses: actions/checkout@v4\n'
        '      - uses: databricks/setup-cli@main\n'
        '      - run: databricks bundle validate -t review\n'
        '        env:\n          DATABRICKS_HOST: ${{ vars.DATABRICKS_HOST }}\n'
        '          DATABRICKS_TOKEN: ${{ secrets.DATABRICKS_TOKEN }}\n')
    hashes = {path: sha256(content.encode('utf-8')).hexdigest() for path, content in sorted(files.items())}
    files['export-manifest.json'] = encoded({'format': 'reviewed-dab-export-v1', 'files': hashes,
        'package_hash': package, 'input_version': version, 'execution_allowed': False})
    return {'report': report, 'artifacts': files, 'export_sha256': sha256(encoded(hashes).encode()).hexdigest()}


def archive(result):
    stream = BytesIO()
    with ZipFile(stream, 'w', ZIP_DEFLATED) as output:
        for path, content in sorted(result['artifacts'].items()):
            output.writestr(path, content.encode('utf-8'))
    return stream.getvalue()