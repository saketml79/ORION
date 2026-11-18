"""Read-only source/design coverage, not a semantic or runtime parity certificate."""
from collections import defaultdict
import re

from workbench.domain import _RELATIONS, _redact

TOKEN = re.compile(r"\$\$?[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")
WIDGET = re.compile(r"dbutils\.widgets\.(?:get|text|dropdown|combobox|multiselect)\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")


def assess(index, record, manifest, artifacts, *, native_available=False):
    """Compare actual selected canonical scope with a checksum-read saved design.

    Presence of a declaration or code token never proves correct translation.
    No models, SQL, filesystem writes, or cross-workflow authority expansion.
    """
    model, design = index.model, record['design']
    nodes = index.nodes
    workflow = record['workflow_id']
    if nodes.get(workflow, {}).get('kind') != 'workflow':
        raise ValueError('Evaluation requires an exact canonical workflow')
    out = defaultdict(list)
    for edge in model['edges']:
        if edge['kind'] in _RELATIONS:
            out[edge['source']].append(edge['target'])
    reached, pending = {workflow}, [workflow]
    while pending:
        for target in out.get(pending.pop(), ()):
            if target not in reached and nodes[target]['kind'] != 'workflow':
                reached.add(target)
                pending.append(target)
    planned = {task['id']: task for task in design['tasks']}
    coverage = defaultdict(list)
    for row in design.get('coverage', []):
        coverage[row['original_task_id']].append(row)
    tasks = []
    for node in model['nodes']:
        if node['kind'] != 'taskinstance' or node.get('parent_id') != workflow:
            continue
        rows = coverage[node['id']]
        mapped = rows[0] if len(rows) == 1 else None
        targets = mapped.get('proposed_task_ids', []) if mapped else []
        tasks.append(dict(source_id=node['id'], name=node['name'],
            source_type=node.get('attributes', {}).get('TASKTYPE'),
            disposition=mapped.get('disposition') if mapped else 'missing_or_duplicate',
            proposed_tasks=targets,
            coverage_record_valid=bool(mapped and all(target in planned for target in targets)),
            rationale=mapped.get('rationale', '') if mapped else ''))
    definitions, uses = {}, defaultdict(set)
    for identifier in reached:
        node = nodes[identifier]
        if node['kind'] in {'workflowvariable', 'mappingvariable'}:
            definitions[identifier] = node
            continue
        values = list(node.get('attributes', {}).values())
        for prop in node.get('properties', []):
            values.extend(prop.get('attributes', {}).values())
            values.append(prop.get('text', ''))
        for field in node.get('fields', []):
            values.extend(field.get('attributes', {}).values())
        for value in values:
            if isinstance(value, str):
                for token in TOKEN.findall(value):
                    uses[token].add(identifier)
    parameters = design.get('parameters', [])
    widgets = set()
    for path, content in artifacts.items():
        if path.endswith('.ipynb'):
            import json
            notebook = json.loads(content)
            content = '\n'.join(''.join(cell['source']) if isinstance(cell['source'], list) else cell['source']
                for cell in notebook['cells'] if cell['cell_type'] == 'code')
        widgets.update(WIDGET.findall(content))
    variables = []
    names = {node.get('name', '') for node in definitions.values()} | set(uses)
    for name in sorted(names):
        declared = [node for node in definitions.values() if node.get('name') == name]
        mentions = [p['name'] for p in parameters if name and name in p.get('purpose', '')]
        variables.append(dict(source_name=name,
            category='source_parameter' if name.startswith('$$') else 'system_or_runtime_reference',
            declaration_ids=[node['id'] for node in declared],
            declared_scopes=[node.get('scope', {}) for node in declared],
            referenced=bool(uses.get(name)), consuming_evidence_ids=sorted(uses.get(name, ())),
            design_parameter_mentions=mentions,
            widgets_present=[p for p in mentions if p in widgets],
            translation_verified=False))
    review = manifest.get('generated_code_review') or {}
    return _redact(dict(schema_version=1, basis='selected_canonical_XML_vs_saved_design_and_code',
        workflow_id=workflow, workflow_name=nodes[workflow]['name'], design_version=record['version'],
        direct_task_count=len(tasks), task_coverage_count=sum(t['coverage_record_valid'] for t in tasks),
        tasks=tasks,
        workflow_variable_count=sum(node['kind'] == 'workflowvariable' for node in definitions.values()),
        mapping_variable_count=sum(node['kind'] == 'mappingvariable' for node in definitions.values()),
        variables=variables, parameters=parameters, notebook_widget_names=sorted(widgets),
        native_job_available=native_available,
        generation_status=manifest.get('status'),
        ai_review_status=review.get('status', 'unavailable'),
        ai_review_tasks=[{key: task.get(key) for key in ('task_id', 'status', 'ai_reviewed', 'issues')}
                         for task in review.get('tasks', [])],
        parameter_translation_verified=False, business_logic_verified=False, runtime_parity_verified=False,
        limitations=['Task coverage checks source-task disposition, not behavior equivalence.',
            'Parameter purpose mentions and widget presence do not prove correct use or precedence.',
            'System task status/timing variables are not automatically user parameters.',
            'Called workflows are outside single-workflow conversion authority.',
            'Model review is an opinion, not independent runtime parity.']))
