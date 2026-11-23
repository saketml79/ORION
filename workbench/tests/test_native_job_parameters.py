"""Offline parameter propagation checks. No notebook execution or remote calls."""
from copy import deepcopy
import json

from workbench.native_job_draft import build, graph_metadata, parameter_contract
from workbench.spark_codegen import ContractError


ROOT = '/Workspace/Shared/oriondemo/parameter_check'


def fixture(*programs):
    design = {'tasks': [], 'edges': [], 'parameters': [{'name': '$$logical_source'}]}
    manifest = {'tasks': []}
    artifacts = {}
    for index, program in enumerate(programs):
        key, path = f'task_{index}', f'notebooks/task_{index}.ipynb'
        design['tasks'].append({'id': key, 'implementation_kind': 'notebook_task'})
        manifest['tasks'].append({'task_id': key, 'status': 'drafted', 'artifact_paths': [path]})
        artifacts[path] = json.dumps({'nbformat': 4, 'nbformat_minor': 5, 'metadata': {}, 'cells': [
            {'cell_type': 'code', 'metadata': {'language': 'python'}, 'source': program,
             'execution_count': None, 'outputs': []}]})
    return design, manifest, artifacts


def test_literal_defaults_propagate_exactly_and_graph_rejects_tampering():
    inputs = fixture('''dbutils.widgets.text("$$source", "main.raw.source")
dbutils.widgets.dropdown(name="mode", defaultValue="append", choices=["append", "overwrite"])
dbutils.widgets.combobox("choice", "", ["a"])
dbutils.widgets.multiselect("flags", "a,b", ["a", "b"])
dbutils.widgets.text("secret_scope", "")
dbutils.widgets.text("secret_key", "")
dbutils.widgets.text("unicode", "caf\u00e9")
dbutils.widgets.text("whitespace", "  ")
''', 'dbutils.widgets.text("mode", "append")')
    design, manifest, artifacts = inputs
    original = deepcopy(inputs)
    document = build(*inputs, ROOT)
    resource = json.loads(document)['resources']['jobs']['migration_draft']
    expected = {'$$source': 'main.raw.source', 'mode': 'append', 'choice': '', 'flags': 'a,b',
                'secret_scope': '', 'secret_key': '', 'unicode': 'caf\u00e9', 'whitespace': '  '}
    assert len(resource['parameters']) == len(expected)
    assert {row['name']: row['default'] for row in resource['parameters']} == expected
    assert all('base_parameters' not in task['notebook_task'] for task in resource['tasks'])
    assert inputs == original
    report = parameter_contract(design, artifacts)
    assert report['missing_defaults'] == []
    assert report['design_only_parameters'] == ['$$logical_source']
    assert graph_metadata(document, manifest, artifacts, ROOT)[0]['parameters'] == resource['parameters']
    for wrong_root in ('/Workspace/Shared/other', '/Workspace/Users/name@example.com/demo', '/Workspace/../escape'):
        try:
            if wrong_root == '/Workspace/Shared/other':
                graph_metadata(document, manifest, artifacts, wrong_root)
            else:
                build(*inputs, wrong_root)
        except ContractError:
            pass
        else:
            raise AssertionError('Wrong namespace or invalid workspace root accepted')
    for mutation in ('missing', 'changed', 'extra'):
        changed = json.loads(document)
        job = changed['resources']['jobs']['migration_draft']
        if mutation == 'missing':
            job.pop('parameters')
        elif mutation == 'changed':
            job['parameters'][0]['default'] = 'invented'
        else:
            job['parameters'].append({'name': 'invented', 'default': ''})
        try:
            graph_metadata(json.dumps(changed), manifest, artifacts, ROOT)
        except ContractError:
            pass
        else:
            raise AssertionError('Unproven graph parameters accepted')


def test_literal_helper_arguments_across_cells_and_declared_defaults():
    inputs = fixture('''def parameter(name, default=""):
    dbutils.widgets.text(name, default)
    return dbutils.widgets.get(name)
if parameter("ack", "REQUIRED") != "APPROVED":
    raise RuntimeError("Not approved")
value = parameter("empty")
''')
    artifacts = inputs[2]
    path = next(iter(artifacts))
    book = json.loads(artifacts[path])
    source = book['cells'][0]['source']
    definition, calls = source.split('if parameter', 1)
    book['cells'][0]['source'] = definition
    book['cells'].append({**deepcopy(book['cells'][0]), 'source': 'if parameter' + calls})
    artifacts[path] = json.dumps(book)
    report = parameter_contract(inputs[0], artifacts)
    assert report['missing_defaults'] == []
    assert report['parameters'] == [{'name': 'ack', 'default': 'REQUIRED'}, {'name': 'empty', 'default': ''}]
    build(*inputs, ROOT)


def test_unresolved_or_conflicting_defaults_block_without_execution():
    programs = [
        'dbutils.widgets.text("x")',
        'dbutils.widgets.text("x", calculate_default())',
        'dbutils.widgets.text("x", 1)',
        'dbutils.widgets.text(variable_name, "")',
        'dbutils.widgets.text(*args)',
        'dbutils.widgets.text(name="x", default="wrong_keyword")',
        'dbutils.widgets.get("undeclared")',
        'dbutils.widgets.text("password", "unsafe_literal")',
        'dbutils.widgets.text("target", "[REDACTED]")',
        'if False:\n    dbutils.widgets.text("x", "hidden")',
        'def parameter(name, default):\n    dbutils.widgets.text(name, default)\n    return dbutils.widgets.get(name)\nparameter("x", calculate_default())',
        'def parameter(name):\n    default = calculate_default()\n    dbutils.widgets.text(name, default)\n    return dbutils.widgets.get(name)\nparameter("x")',
    ]
    for code in programs:
        inputs = fixture(code)
        assert parameter_contract(inputs[0], inputs[2])['missing_defaults']
        try:
            build(*inputs, ROOT)
        except ContractError as error:
            assert 'blocked' in str(error)
            assert 'calculate_default()' not in str(error)
        else:
            raise AssertionError('Unknown default was invented or silently omitted')
    inputs = fixture('dbutils.widgets.text("x", "one")', 'dbutils.widgets.text("x", "two")')
    assert parameter_contract(inputs[0], inputs[2])['missing_defaults']
    try:
        build(*inputs, ROOT)
    except ContractError as error:
        assert 'conflict' in str(error)
    else:
        raise AssertionError('Cross-notebook conflict accepted')


def test_no_widgets_preserves_legacy_resource_shape():
    inputs = fixture('# dbutils.widgets.text("fake", "fake")\nvalue = 1')
    job = json.loads(build(*inputs, ROOT))['resources']['jobs']['migration_draft']
    assert 'parameters' not in job
    assert parameter_contract(inputs[0], inputs[2])['parameters'] == []