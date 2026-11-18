"""Conservative Jobs document for approved, fully drafted notebook and condition DAGs.

JSON is valid YAML 1.2. The returned document is a draft, not a deployment,
remote workspace validation, semantic equivalence, or a release authorization.
"""
import ast
import json
import re
from typing import Any, TypedDict

from pydantic import ValidationError

from workbench.redesign import ConditionSpec
from workbench.redaction import is_secret_key
from workbench.spark_codegen import ContractError, _notebook, _path, _python, _sql
from workbench.understanding import _INLINE_SECRET


def _widget_method(node):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == 'widgets'
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == 'dbutils'):
        return node.func.attr
    return None


def _call_arguments(call, names):
    """Bind explicit arguments only, never evaluate expressions or unpacking."""
    if len(call.args) > len(names) or any(isinstance(arg, ast.Starred) for arg in call.args):
        return None
    bound = dict(zip(names, call.args))
    for keyword in call.keywords:
        if keyword.arg not in names or keyword.arg in bound:
            return None
        bound[keyword.arg] = keyword.value
    return bound


def parameter_contract(design, notebooks):
    """Static widget/default evidence from validated notebooks, without execution.

    Supports direct declarations and undecorated two-statement widget/get helpers
    called with literal arguments. No constant folding, loops, alias guessing,
    dynamic defaults, or arbitrary function execution. Design-only names are
    diagnostic, not invented runtime parameters.
    """
    declarations = {}
    reads = set()
    missing = []
    methods = {'text', 'dropdown', 'combobox', 'multiselect'}

    def problem(path, cell, reason):
        missing.append({'notebook_path': path, 'cell': cell, 'reason': reason})

    def literal(node, bindings):
        if isinstance(node, ast.Name):
            node = bindings.get(node.id)
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def declaration(call, bindings, path, cell):
        names = ['name', 'defaultValue']
        if _widget_method(call) != 'text':
            names.append('choices')
        names.append('label')
        args = _call_arguments(call, names)
        name = literal(args.get('name'), bindings) if args is not None else None
        default = literal(args.get('defaultValue'), bindings) if args is not None else None
        if not name or default is None:
            problem(path, cell, 'Declare widget name and defaultValue as explicit string literals; '
                    'regenerate the notebook rather than infer a dynamic or missing default.')
            return
        if '[REDACTED]' in default or (is_secret_key(name.lstrip('$')) and default):
            problem(path, cell, 'Credential-bearing or redacted widget defaults cannot be published; '
                'regenerate using empty credential defaults and runtime secret lookups.')
            return
        if name in declarations and declarations[name] != default:
            problem(path, cell, 'Widget defaults conflict across declarations; regenerate with one '
                    'consistent literal default per exact widget name.')
        else:
            declarations[name] = default

    for path, notebook in sorted(notebooks.items()):
        normalized = _notebook(notebook, path)
        if _INLINE_SECRET.search(notebook):
            raise ContractError('Credential-bearing artifacts are prohibited; use required parameter bindings')
        statements = []
        for number, cell in enumerate(json.loads(normalized)['cells'], 1):
            if cell['cell_type'] == 'code' and cell['metadata']['language'] == 'python':
                source = cell['source'] if isinstance(cell['source'], str) else ''.join(cell['source'])
                statements.extend((statement, number) for statement in ast.parse(source).body)
        functions = {}
        for statement, number in statements:
            if isinstance(statement, ast.FunctionDef):
                functions.setdefault(statement.name, []).append(statement)
        helpers = {}
        for name, definitions in functions.items():
            function = definitions[0]
            body = function.body
            if (len(definitions) == 1 and not function.decorator_list
                    and not function.args.vararg and not function.args.kwarg
                    and len(body) == 2 and isinstance(body[0], ast.Expr)
                    and _widget_method(body[0].value) in methods
                    and isinstance(body[1], ast.Return)
                    and _widget_method(body[1].value) == 'get'):
                helpers[name] = function
        for statement, number in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for node in ast.walk(statement):
                if not isinstance(node, ast.Call):
                    continue
                method = _widget_method(node)
                if method in methods:
                    if not isinstance(statement, ast.Expr) or statement.value is not node:
                        problem(path, number, 'Move conditional or nested widget declarations to explicit '
                                'top-level literal declarations before generating the job.')
                    else:
                        declaration(node, {}, path, number)
                elif method == 'get':
                    args = _call_arguments(node, ['name'])
                    name = literal(args.get('name'), {}) if args is not None else None
                    if name:
                        reads.add(name)
                    else:
                        problem(path, number, 'Widget reads require an exact literal name and a literal declaration.')
                elif isinstance(node.func, ast.Name) and node.func.id in functions:
                    function = helpers.get(node.func.id)
                    if function is None:
                        if any(_widget_method(child) in methods for definition in functions[node.func.id]
                               for child in ast.walk(definition)):
                            problem(path, number, 'Unsupported widget helper; regenerate using top-level literal '
                                    'declarations or a simple widget declaration/get helper with literal call arguments.')
                        continue
                    positional = function.args.posonlyargs + function.args.args
                    names = [arg.arg for arg in positional + function.args.kwonlyargs]
                    # Keyword-only arguments cannot be supplied positionally.
                    args = _call_arguments(node, names) if len(node.args) <= len(positional) else None
                    if args is None or any(keyword.arg in {arg.arg for arg in function.args.posonlyargs}
                                           for keyword in node.keywords):
                        problem(path, number, 'Widget helper arguments must be explicit literal bindings.')
                        continue
                    defaults = dict(zip([arg.arg for arg in positional][-len(function.args.defaults):],
                                        function.args.defaults)) if function.args.defaults else {}
                    defaults.update({arg.arg: value for arg, value in zip(function.args.kwonlyargs,
                                     function.args.kw_defaults) if value is not None})
                    bindings = {**defaults, **args}
                    declaration(function.body[0].value, bindings, path, number)
                    get_args = _call_arguments(function.body[1].value, ['name'])
                    read = literal(get_args.get('name'), bindings) if get_args is not None else None
                    if read:
                        reads.add(read)
                    else:
                        problem(path, number, 'Widget helper must read an explicitly bound literal widget name.')
    for name in sorted(reads - declarations.keys()):
        missing.append({'name': name, 'reason': 'Widget is read without a literal default declaration; '
                        'regenerate the notebook with dbutils.widgets.text(name, defaultValue) or another supported declaration.'})
    parameters = [{'name': name, 'default': default} for name, default in sorted(declarations.items())]
    design_names = {item.get('name') for item in design.get('parameters', [])
                    if isinstance(item, dict) and isinstance(item.get('name'), str)}
    return {'parameters': parameters, 'missing_defaults': missing,
            'design_only_parameters': sorted(design_names - declarations.keys())}


def _checked_parameters(design, notebooks):
    contract = parameter_contract(design, notebooks)
    if contract['missing_defaults']:
        first = contract['missing_defaults'][0]
        location = (f"{first['notebook_path']} cell {first['cell']}: "
                    if 'notebook_path' in first else '')
        raise ContractError('Job parameter defaults blocked: ' + location + first['reason']
                    + ' Inspect parameter_contract.missing_defaults for all unresolved bindings.')
    from databricks.sdk.service.jobs import JobParameterDefinition
    for parameter in contract['parameters']:
        if JobParameterDefinition.from_dict(parameter).as_dict() != parameter:
            raise ContractError('Jobs SDK rejected or changed a job parameter definition')
    return contract['parameters']


def _published_keys(notebook):
    """Recognize direct top-level calls only; presence is not runtime proof."""
    result = set()
    for cell in json.loads(notebook)['cells']:
        if cell['cell_type'] != 'code' or cell['metadata'].get('language') != 'python':
            continue
        source = cell['source'] if isinstance(cell['source'], str) else ''.join(cell['source'])
        for statement in ast.parse(source).body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            call = statement.value
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr == 'set'
                    and isinstance(func.value, ast.Attribute) and func.value.attr == 'taskValues'
                    and isinstance(func.value.value, ast.Attribute) and func.value.value.attr == 'jobs'
                    and isinstance(func.value.value.value, ast.Name) and func.value.value.value.id == 'dbutils'):
                continue
            if any(keyword.arg is None for keyword in call.keywords) or len(call.args) > 2:
                continue
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            if len(keywords) != len(call.keywords) or set(keywords) - {'key', 'value'}:
                continue
            if (call.args and 'key' in keywords) or (len(call.args) > 1 and 'value' in keywords):
                continue
            key = call.args[0] if call.args else keywords.get('key')
            value = call.args[1] if len(call.args) > 1 else keywords.get('value')
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and value is not None:
                result.add(key.value)
    return result


def _condition_artifacts(row, artifacts):
    paths = row.get('artifact_paths')
    # A native condition is implemented by its validated ConditionSpec and
    # dependency branches, not a notebook. Producer task-value checks in build
    # still require the exact committed upstream notebook and published key.
    if paths == []:
        return
    if (not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths)
            or len(paths) != len(set(paths))):
        raise ContractError('Condition task requires a committed writer artifact inventory')
    implemented = False
    kinds = {'notebooks': 'notebook', 'modules': 'module', 'sql': 'sql', 'tests': 'test'}
    for path in paths:
        if not isinstance(path, str) or path not in artifacts:
            raise ContractError('Condition task writer artifact is missing')
        kind = kinds.get(path.split('/')[0])
        if kind is None:
            raise ContractError('Condition task writer artifact has an invalid type')
        _path(path, kind)
        if kind == 'notebook':
            _notebook(artifacts[path], path)
        elif kind == 'sql':
            _sql(artifacts[path])
        else:
            _python(artifacts[path])
        implemented |= kind != 'test'
    if not implemented:
        raise ContractError('Condition task requires a real drafted writer artifact')


def build(design, manifest, artifacts, workspace_root):
    if (not isinstance(workspace_root, str)
            or not re.fullmatch(r'/Workspace/[A-Za-z0-9_./-]{1,200}', workspace_root)
            or any(part in ('', '.', '..') for part in workspace_root.split('/')[1:])):
        raise ContractError('Provide an explicit absolute /Workspace destination without traversal')
    tasks = design.get('tasks', [])
    rows = manifest.get('tasks', [])
    if not tasks or len(rows) != len(tasks):
        raise ContractError('Every approved task must have one drafted result')
    if any(not isinstance(task, dict) for task in tasks) or any(not isinstance(row, dict) for row in rows):
        raise ContractError('Invalid approved tasks or task manifest')
    ids = [task.get('id') for task in tasks]
    if (any(not isinstance(i, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise ContractError('Job task keys must be unique Databricks identifiers')
    by_id = {task['id']: task for task in tasks}
    if any(not isinstance(row.get('task_id'), str) for row in rows):
        raise ContractError('Task manifest does not match the approved design')
    indexed = {row.get('task_id'): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != set(ids):
        raise ContractError('Task manifest does not match the approved design')
    dependencies = {key: [] for key in ids}
    run_if = {}
    for edge in design.get('edges', []):
        if not isinstance(edge, dict):
            raise ContractError('Invalid dependency edge')
        source, target = edge.get('source'), edge.get('target')
        if not isinstance(source, str) or not isinstance(target, str) or source not in dependencies or target not in dependencies or source == target:
            raise ContractError('Invalid dependency endpoint')
        source_is_condition = by_id[source].get('implementation_kind') == 'condition_task'
        outcome = edge.get('outcome')
        if source_is_condition:
            if outcome not in ('true', 'false'):
                raise ContractError('Condition task dependencies need an explicit true or false outcome')
            evidence = edge.get('evidence_ids')
            if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item for item in evidence):
                raise ContractError('Condition task branches require source evidence')
        elif edge.get('condition') is not None or outcome is not None:
            raise ContractError('Conditional edges need an explicitly implemented condition task')
        if any(dep['task_key'] == source for dep in dependencies[target]):
            raise ContractError('Duplicate task dependency')
        rule = edge.get('run_if', 'ALL_SUCCESS')
        if not isinstance(rule, str) or rule not in {'ALL_SUCCESS', 'AT_LEAST_ONE_SUCCESS', 'NONE_FAILED', 'ALL_DONE',
                        'AT_LEAST_ONE_FAILED', 'ALL_FAILED'} or (target in run_if and run_if[target] != rule):
            raise ContractError('Ambiguous task run condition')
        if source_is_condition and rule != 'ALL_SUCCESS':
            raise ContractError('Conditional branches require ALL_SUCCESS to prevent bypass')
        run_if[target] = rule
        dependency = {'task_key': source}
        if source_is_condition:
            dependency['outcome'] = outcome
        dependencies[target].append(dependency)
    remaining = {key: {dep['task_key'] for dep in value} for key, value in dependencies.items()}
    while remaining:
        ready = {key for key, parents in remaining.items() if not parents}
        if not ready:
            raise ContractError('Task dependency graph contains a cycle')
        remaining = {key: parents - ready for key, parents in remaining.items() if key not in ready}
    output = []
    notebook_bodies = {}
    notebook_paths = {}
    notebook_sources = {}
    conditions = {}
    for task in tasks:
        key = task['id']
        row = indexed[key]
        kind = task.get('implementation_kind')
        if row.get('status') != 'drafted' or (row.get('implementation_kind') is not None and row['implementation_kind'] != kind):
            raise ContractError('Only fully drafted notebook tasks are supported; other task kinds require implementation')
        if kind == 'condition_task':
            if row.get('implementation_kind') != 'condition_task' or row.get('blockers'):
                raise ContractError('Condition task needs its own accepted drafted manifest row')
            try:
                spec = ConditionSpec.model_validate(task.get('condition_task'))
            except ValidationError:
                raise ContractError('Condition task needs explicit validated operands and operator') from None
            if not any(dep['task_key'] == key for values in dependencies.values() for dep in values):
                raise ContractError('Condition task must have an explicit outcome branch')
            _condition_artifacts(row, artifacts)
            conditions[key] = spec
            item = {'task_key': key, 'condition_task': {}}
        elif kind == 'notebook_task':
            if task.get('condition_task') is not None:
                raise ContractError('Structured conditions require a condition task')
            paths = row.get('artifact_paths')
            if not isinstance(paths, list):
                raise ContractError('Task artifact inventory is invalid')
            notebooks = [path for path in paths if isinstance(path, str) and path.endswith('.ipynb')]
            if len(notebooks) != 1 or notebooks[0] not in artifacts:
                raise ContractError('Every notebook task needs exactly one committed notebook')
            path = _path(notebooks[0], 'notebook')
            folded = path.casefold()
            if folded in notebook_paths and notebook_paths[folded] != path:
                raise ContractError('Notebook destination paths collide')
            notebook_paths[folded] = path
            notebook_bodies[key] = _notebook(artifacts[path], path)
            notebook_sources[path] = notebook_bodies[key]
            item = {'task_key': key, 'notebook_task': {'notebook_path': workspace_root.rstrip('/') + '/' + path[:-6]}}
        else:
            raise ContractError('Only fully drafted notebook or explicitly implemented condition tasks are supported')
        if dependencies[key]:
            item['depends_on'] = dependencies[key]
            item['run_if'] = run_if[key]
        output.append(item)
    output_by_id = {item['task_key']: item for item in output}
    for key, spec in conditions.items():
        # A reference must be on an ALL_SUCCESS dependency chain, not merely
        # reachable through an alternative failure/any-success route.
        ancestors = set()
        pending = [key]
        while pending:
            current = pending.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            if run_if.get(current, 'ALL_SUCCESS') == 'ALL_SUCCESS':
                pending.extend(dep['task_key'] for dep in dependencies[current])
        compiled = {'op': spec.op}
        for side in ('left', 'right'):
            operand = getattr(spec, side)
            if operand.kind == 'literal':
                if spec.op not in {'EQUAL_TO', 'NOT_EQUAL'} and not re.fullmatch(
                        r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', operand.value):
                    raise ContractError('Numeric comparison requires numeric literal operands')
                compiled[side] = operand.value
            else:
                producer = operand.task_id
                if producer == key or producer not in ancestors or producer not in notebook_bodies:
                    raise ContractError('Task value must come from a guaranteed upstream notebook')
                if operand.key not in _published_keys(notebook_bodies[producer]):
                    raise ContractError('Upstream notebook lacks an explicit task value set for this key')
                compiled[side] = '{{tasks.' + producer + '.values.' + operand.key + '}}'
        output_by_id[key]['condition_task'] = compiled
    from databricks.sdk.service.jobs import Task
    for task in output:
        if Task.from_dict(task).as_dict() != task:
            raise ContractError('Jobs SDK rejected or changed a task definition')
    resource = {'resources': {'jobs': {'migration_draft': {
        'name': 'Migration draft', 'performance_target': 'STANDARD',
        'tags': {'aidevkit_project': 'ai-dev-kit'}, 'tasks': output}}}}
    parameters = _checked_parameters(design, notebook_sources)
    if parameters:
        resource['resources']['jobs']['migration_draft']['parameters'] = parameters
    return json.dumps(resource, ensure_ascii=True, indent=2, allow_nan=False) + '\n'


class NotebookArtifactBinding(TypedDict):
    notebook_path: str


class NativeJobGraph(TypedDict):
    job_resource: str
    workspace_root: str
    tasks: list[dict[str, Any]]
    parameters: list[dict[str, Any]]
    artifact_mapping: dict[str, NotebookArtifactBinding]


def graph_metadata(document: str, manifest: dict[str, Any], artifacts: dict[str, str],
                   workspace_root: str) -> list[NativeJobGraph]:
    """Project this builder's JSON-as-YAML, never infer tasks from artifact names.

    The resource remains a draft. Paths bind each emitted task to its exact
    committed manifest notebook, not a basename or another task's artifact.
    """
    from databricks.sdk.service.jobs import Task
    resource = json.loads(document)['resources']['jobs']['migration_draft']
    tasks = resource['tasks']
    rows = {row['task_id']: row for row in manifest['tasks']}
    mapping: dict[str, NotebookArtifactBinding] = {}
    notebooks = {}
    for task in tasks:
        if Task.from_dict(task).as_dict() != task:
            raise ContractError('Jobs graph task differs from the validated document')
        if 'notebook_task' not in task:
            continue
        paths = [path for path in rows[task['task_key']]['artifact_paths'] if path.endswith('.ipynb')]
        if len(paths) != 1:
            raise ContractError('Jobs graph needs one exact manifest notebook')
        path = _path(paths[0], 'notebook')
        _notebook(artifacts[path], path)
        if task['notebook_task']['notebook_path'] != workspace_root.rstrip('/') + '/' + path[:-6]:
            raise ContractError('Jobs graph notebook destination does not match the manifest')
        mapping[task['task_key']] = {'notebook_path': path}
        notebooks[path] = artifacts[path]
    if resource.get('parameters', []) != _checked_parameters({}, notebooks):
        raise ContractError('Jobs graph parameters do not match the accepted notebook literal defaults')
    return [{'job_resource': 'migration_draft', 'workspace_root': workspace_root,
             'tasks': tasks, 'parameters': resource.get('parameters', []),
             'artifact_mapping': mapping}]