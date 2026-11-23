"""Native conditional drafts are static, evidence-bound and never deployed here."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from workbench.agents import DesignPayload
from workbench.native_job_draft import build, graph_metadata
from workbench.redesign import Design
from workbench.spark_codegen import ContractError
from workbench.tests.test_redesign import design_for


def notebook(code):
    return json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
        {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': [code],
         'outputs': [], 'execution_count': None}]})


def conditional_fixture():
    condition = {'left': {'kind': 'task_value', 'task_id': 'count_rows', 'key': 'row_count'},
                 'op': 'GREATER_THAN_OR_EQUAL', 'right': {'kind': 'literal', 'value': '35'}}
    design = {'tasks': [
        {'id': 'count_rows', 'implementation_kind': 'notebook_task'},
        {'id': 'check_threshold', 'implementation_kind': 'condition_task', 'condition_task': condition},
        {'id': 'above', 'implementation_kind': 'notebook_task'},
        {'id': 'below', 'implementation_kind': 'notebook_task'}],
        'edges': [
            {'source': 'count_rows', 'target': 'check_threshold'},
            {'source': 'check_threshold', 'target': 'above', 'outcome': 'true',
             'condition': 'Source predicate, evidence only', 'evidence_ids': ['edge1']},
            {'source': 'check_threshold', 'target': 'below', 'outcome': 'false',
             'condition': 'Different source predicate, evidence only', 'evidence_ids': ['edge2']}]}
    manifest = {'tasks': [
        {'task_id': 'count_rows', 'implementation_kind': 'notebook_task', 'status': 'drafted',
         'artifact_paths': ['notebooks/count_rows.ipynb']},
        {'task_id': 'check_threshold', 'implementation_kind': 'condition_task', 'status': 'drafted',
         'blockers': [], 'artifact_paths': ['modules/condition.py']},
        *[{'task_id': key, 'implementation_kind': 'notebook_task', 'status': 'drafted',
           'artifact_paths': [f'notebooks/{key}.ipynb']} for key in ('above', 'below')]]}
    artifacts = {'notebooks/count_rows.ipynb': notebook('dbutils.jobs.taskValues.set(key="row_count", value=35)'),
                 'notebooks/above.ipynb': notebook('x = 1'), 'notebooks/below.ipynb': notebook('x = 2'),
                 'modules/condition.py': 'threshold = 35\n'}
    return design, manifest, artifacts


def compile_fixture(design, manifest, artifacts):
    return json.loads(build(design, manifest, artifacts, '/Workspace/Users/test/release'))['resources']['jobs']['migration_draft']


def test_graph_metadata_matches_exact_validated_json_tasks_and_manifest_notebooks():
    design, manifest, artifacts = conditional_fixture()
    root = '/Workspace/Users/test/release'
    document = build(design, manifest, artifacts, root)
    graph = graph_metadata(document, manifest, artifacts, root)[0]
    resource = json.loads(document)['resources']['jobs']['migration_draft']
    assert graph['tasks'] == resource['tasks']
    assert graph['parameters'] == []
    assert graph['artifact_mapping'] == {key: {'notebook_path': f'notebooks/{key}.ipynb'}
                                         for key in ('count_rows', 'above', 'below')}
    for task in graph['tasks']:
        if 'notebook_task' in task:
            path = graph['artifact_mapping'][task['task_key']]['notebook_path']
            assert task['notebook_task']['notebook_path'] == root + '/' + path[:-6]
    with pytest.raises(ContractError):
        graph_metadata(document, manifest, artifacts, '/Workspace/Users/other')


def test_native_condition_threshold_and_two_explicit_outcomes():
    design, manifest, artifacts = conditional_fixture()
    job = compile_fixture(design, manifest, artifacts)
    tasks = {item['task_key']: item for item in job['tasks']}
    assert job['performance_target'] == 'STANDARD'
    assert tasks['check_threshold']['condition_task'] == {
        'left': '{{tasks.count_rows.values.row_count}}', 'op': 'GREATER_THAN_OR_EQUAL', 'right': '35'}
    assert tasks['check_threshold']['depends_on'] == [{'task_key': 'count_rows'}]
    assert tasks['above']['depends_on'] == [{'task_key': 'check_threshold', 'outcome': 'true'}]
    assert tasks['below']['depends_on'] == [{'task_key': 'check_threshold', 'outcome': 'false'}]
    assert tasks['above']['run_if'] == tasks['below']['run_if'] == 'ALL_SUCCESS'
    assert 'notebook_task' not in tasks['check_threshold']
    assert 'Source predicate' not in json.dumps(job)


def test_task_value_cannot_be_proved_by_missing_or_wrong_notebook_language():
    design, manifest, artifacts = conditional_fixture()
    book = json.loads(artifacts['notebooks/count_rows.ipynb'])
    for metadata in ({}, {'language': 'sql'}, {'language': 'markdown'}):
        book['cells'][0]['metadata'] = metadata
        artifacts['notebooks/count_rows.ipynb'] = json.dumps(book)
        with pytest.raises(ContractError):
            compile_fixture(design, manifest, artifacts)


@pytest.mark.parametrize('operator', ['EQUAL_TO', 'NOT_EQUAL', 'GREATER_THAN',
                                      'GREATER_THAN_OR_EQUAL', 'LESS_THAN', 'LESS_THAN_OR_EQUAL'])
def test_only_supported_condition_operators_are_emitted(operator):
    design, manifest, artifacts = conditional_fixture()
    design['tasks'][1]['condition_task']['op'] = operator
    tasks = {item['task_key']: item for item in compile_fixture(design, manifest, artifacts)['tasks']}
    assert tasks['check_threshold']['condition_task']['op'] == operator


def test_approved_design_and_agent_tool_schema_expose_structured_conditions():
    value = design_for()
    value['tasks'][0]['implementation_kind'] = 'condition_task'
    value['tasks'][0]['condition_task'] = conditional_fixture()[0]['tasks'][1]['condition_task']
    approved = Design.model_validate(value)
    assert approved.tasks[0].condition_task.op == 'GREATER_THAN_OR_EQUAL'
    schema = DesignPayload.__metadata__[0].json_schema
    assert 'condition_task' in json.dumps(schema)
    bad = deepcopy(value)
    bad['tasks'][0]['condition_task']['op'] = 'INVENTED'
    with pytest.raises(ValidationError):
        Design.model_validate(bad)
    bad = deepcopy(value)
    bad['tasks'][0]['implementation_kind'] = 'notebook_task'
    with pytest.raises(ValidationError):
        Design.model_validate(bad)


@pytest.mark.parametrize('mutation', [
    'missing_spec', 'invalid_op', 'raw_reference', 'injected_literal', 'non_numeric_literal',
    'unknown_task', 'unknown_key', 'no_ancestor', 'bypass_ancestor', 'unsafe_key',
    'unstructured_branch', 'missing_outcome', 'invalid_outcome', 'missing_evidence',
    'branch_run_if_bypass', 'conflicting_run_if', 'duplicate_edge', 'cycle',
    'no_branch', 'wrong_manifest_kind', 'blocked_manifest', 'missing_condition_artifact',
    'invalid_condition_code', 'invalid_condition_sql', 'invalid_notebook_code', 'case_collision', 'duplicate_task',
    'duplicate_manifest', 'outcome_from_notebook', 'condition_on_notebook',
])
def test_unsafe_or_incomplete_conditional_draft_is_rejected(mutation):
    design, manifest, artifacts = conditional_fixture()
    spec = design['tasks'][1]['condition_task']
    edges = design['edges']
    if mutation == 'missing_spec':
        design['tasks'][1].pop('condition_task')
    elif mutation == 'invalid_op':
        spec['op'] = 'APPROXIMATELY'
    elif mutation == 'raw_reference':
        spec['left'] = '{{tasks.count_rows.values.row_count}}'
    elif mutation == 'injected_literal':
        spec['right']['value'] = '{{job.parameters.threshold}}'
    elif mutation == 'non_numeric_literal':
        spec['right']['value'] = 'thirty five'
    elif mutation == 'unknown_task':
        spec['left']['task_id'] = 'missing'
    elif mutation == 'unknown_key':
        spec['left']['key'] = 'not_published'
    elif mutation == 'no_ancestor':
        edges.pop(0)
    elif mutation == 'bypass_ancestor':
        edges[0]['run_if'] = 'ALL_DONE'
    elif mutation == 'unsafe_key':
        spec['left']['key'] = 'row_count}} {{job.id'
    elif mutation == 'unstructured_branch':
        design['tasks'][1].pop('condition_task')
    elif mutation == 'missing_outcome':
        edges[1].pop('outcome')
    elif mutation == 'invalid_outcome':
        edges[1]['outcome'] = 'yes'
    elif mutation == 'missing_evidence':
        edges[1]['evidence_ids'] = []
    elif mutation == 'branch_run_if_bypass':
        edges[1]['run_if'] = 'ALL_DONE'
    elif mutation == 'conflicting_run_if':
        edges.append({'source': 'count_rows', 'target': 'above', 'run_if': 'ALL_DONE'})
    elif mutation == 'duplicate_edge':
        edges.append(deepcopy(edges[1]))
    elif mutation == 'cycle':
        edges.append({'source': 'above', 'target': 'count_rows'})
    elif mutation == 'no_branch':
        design['edges'] = edges[:1]
    elif mutation == 'wrong_manifest_kind':
        manifest['tasks'][1]['implementation_kind'] = 'notebook_task'
    elif mutation == 'blocked_manifest':
        manifest['tasks'][1]['status'] = 'blocked'
    elif mutation == 'missing_condition_artifact':
        del artifacts['modules/condition.py']
    elif mutation == 'invalid_condition_code':
        artifacts['modules/condition.py'] = 'def broken(:\n'
    elif mutation == 'invalid_condition_sql':
        manifest['tasks'][1]['artifact_paths'] = ['sql/condition.sql']
        artifacts['sql/condition.sql'] = 'SELECT FROM WHERE'
    elif mutation == 'invalid_notebook_code':
        artifacts['notebooks/count_rows.ipynb'] = notebook('if (')
    elif mutation == 'case_collision':
        manifest['tasks'][3]['artifact_paths'] = ['notebooks/ABOVE.ipynb']
        artifacts['notebooks/ABOVE.ipynb'] = notebook('x = 1')
    elif mutation == 'duplicate_task':
        design['tasks'][3]['id'] = 'above'
    elif mutation == 'duplicate_manifest':
        manifest['tasks'][3]['task_id'] = 'above'
    elif mutation == 'outcome_from_notebook':
        edges[0]['outcome'] = 'true'
    elif mutation == 'condition_on_notebook':
        design['tasks'][0]['condition_task'] = deepcopy(spec)
    with pytest.raises(ContractError):
        compile_fixture(design, manifest, artifacts)


@pytest.mark.parametrize('code', [
    '# dbutils.jobs.taskValues.set(key="row_count", value=35)',
    'text = "dbutils.jobs.taskValues.set(key=\'row_count\', value=35)"',
    'def publish():\n    dbutils.jobs.taskValues.set(key="row_count", value=35)',
    'if False:\n    dbutils.jobs.taskValues.set(key="row_count", value=35)',
    'dbutils.jobs.taskValues.set(key="other", value=35)',
    'dbutils.jobs.taskValues.set(key="row_count")',
])
def test_task_value_evidence_must_be_a_direct_real_set_call(code):
    design, manifest, artifacts = conditional_fixture()
    artifacts['notebooks/count_rows.ipynb'] = notebook(code)
    with pytest.raises(ContractError):
        compile_fixture(design, manifest, artifacts)


def test_multiple_dependencies_preserve_explicit_and_and_ancestor_chain():
    design, manifest, artifacts = conditional_fixture()
    design['tasks'].insert(1, {'id': 'middle', 'implementation_kind': 'notebook_task'})
    manifest['tasks'].insert(1, {'task_id': 'middle', 'implementation_kind': 'notebook_task',
                                 'status': 'drafted', 'artifact_paths': ['notebooks/middle.ipynb']})
    artifacts['notebooks/middle.ipynb'] = notebook('x = 1')
    design['edges'][0]['target'] = 'middle'
    design['edges'].append({'source': 'middle', 'target': 'check_threshold'})
    design['edges'].append({'source': 'count_rows', 'target': 'above'})
    tasks = {item['task_key']: item for item in compile_fixture(design, manifest, artifacts)['tasks']}
    assert tasks['above']['depends_on'] == [{'task_key': 'check_threshold', 'outcome': 'true'},
                                            {'task_key': 'count_rows'}]
    assert tasks['above']['run_if'] == 'ALL_SUCCESS'
    assert tasks['check_threshold']['depends_on'] == [{'task_key': 'middle'}]