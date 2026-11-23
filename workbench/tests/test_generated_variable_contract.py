"""Focused static contracts using local helpers/fakes, never a provider or CLI."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from workbench.generated_code_review import GeneratedCodeReviewer, unresolved_informatica_variables
from workbench.native_job_draft import build, parameter_contract
from workbench.spark_codegen import (
    ContractError, GenerationArtifactSet, _GenerationReceipts, _StagedArtifacts,
    _notebook, _python, _sql, _validate,
)


FIX = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests' / 'test_spark_codegen.py'))


@pytest.fixture
def scope():
    # Pure in-memory record, no repository creation or review/history mutations.
    record = dict(id='variable-contract', version=1, design=FIX['design'](),
        input_version='b' * 64, package_hash='a' * 64,
        workflow_id='wf', scenario='original')
    model = FIX['source_model']()
    evidence = FIX['retrieved'](model, record)
    return record, evidence


def book(source='', language='python', markdown='Source notation: $$SCHEMA'):
    cells = [dict(cell_type='markdown', metadata=dict(language='markdown'), source=markdown)]
    if source:
        cells.append(dict(cell_type='code', metadata=dict(language=language), source=source,
            execution_count=None, outputs=[]))
    return json.dumps(dict(nbformat=4, nbformat_minor=5, metadata={}, cells=cells))


@pytest.mark.parametrize('source', [
    "spark.sql('SELECT * FROM $$SCHEMA.events')\n",
    'spark.sql(f"SELECT * FROM $$SCHEMA.{table}")\n',
    'message = f"Loading $$TABLE: {count}"\n',
    'dbutils.widgets.text("schema", "$$SCHEMA")\n',
    'dbutils.widgets.text(name="schema", defaultValue="$$SCHEMA")\n',
    'path = "/Volumes/$$CATALOG/data"\n',
    'value = "$$" "SCHEMA"\n',
    'value = "\\x24\\x24SCHEMA"\n',
    'value = b"$$SCHEMA"\n',
    'value = f"{lookup(\'$$SCHEMA\')}"\n',
    'spark.sql("SELECT \'-- $$SCHEMA\'")\n',
])
def test_python_executable_variables_rejected_before_staging(scope, source):
    record, _ = scope
    before = source
    assert unresolved_informatica_variables(source, 'python')
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        _python(source)
    staged = _StagedArtifacts(record)
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        staged.stage('modules/generated.py', 'module', source)
    assert not staged.files and source == before


@pytest.mark.parametrize('source', [
    '# Source evidence: $$SCHEMA\nvalue = 1\n',
    'value = "# ordinary text"  # original $$TABLE\n',
    'value = """ordinary multiline\nstring"""\n# $$PARAM\n',
    'spark.sql("SELECT 1 -- original $$SCHEMA\\n")\n',
    'spark.sql("SELECT 1 /* original $$SCHEMA */")\n',
    'spark.sql(f"SELECT {count} /* original $$SCHEMA */")\n',
    'value = "$SCHEMA and $$ and $$9invalid"\n',
    'dbutils.widgets.text("schema", "")\nschema = dbutils.widgets.get("schema")\n',
])
def test_python_provenance_and_runtime_bindings_preserved(source):
    assert unresolved_informatica_variables(source, 'python') == []
    assert _python(source).body


@pytest.mark.parametrize('source', [
    'SELECT * FROM $$SCHEMA.events',
    "SELECT '$$VALUE' AS message",
    'SELECT 1 /* $$COMMENT */ FROM $$SCHEMA.events -- $$IGNORED',
    "SELECT '-- $$VALUE' AS message",
    "SELECT 'it''s $$VALUE' AS message",
    'SELECT `$$COLUMN` FROM events',
    'SELECT 1 -- $$COMMENT\rFROM $$SCHEMA.events',
])
def test_sql_executable_tokens_are_not_hidden_by_quotes_or_comments(source):
    assert unresolved_informatica_variables(source, 'sql')
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        _sql(source)


@pytest.mark.parametrize('source', [
    'SELECT 1 -- $$SCHEMA\n',
    '/* $$SCHEMA */ SELECT 1',
    'SELECT 1 /* outer $$A /* nested $$B */ $$C */',
    "SELECT '-- ordinary literal' /* $$SCHEMA */",
])
def test_sql_comments_are_retained_not_substituted(source):
    assert unresolved_informatica_variables(source, 'sql') == []
    _sql(source)


@pytest.mark.parametrize('language,source', [
    ('python', "spark.sql('SELECT * FROM $$SCHEMA.events')\n"),
    ('python', 'dbutils.widgets.text("schema", "$$SCHEMA")\n'),
    ('sql', 'SELECT * FROM $$SCHEMA.events'),
])
@pytest.mark.parametrize('list_source', [False, True])
def test_notebook_code_sources_checked_but_markdown_ignored(language, source, list_source):
    raw = json.loads(book(source, language))
    if list_source:
        raw['cells'][1]['source'] = source.splitlines(keepends=True)
    text = json.dumps(raw)
    assert unresolved_informatica_variables(text, 'notebook') == ['$$SCHEMA']
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        _notebook(text, 'variable-contract')
    assert raw['cells'][0]['source'] == 'Source notation: $$SCHEMA'


@pytest.mark.parametrize('source,language', [
    ('', 'python'),
    ('# Canonical provenance $$SCHEMA\nvalue = 1\n', 'python'),
    ('SELECT 1 -- Canonical provenance $$SCHEMA\n', 'sql'),
])
def test_whole_markdown_notebooks_and_comments_preserve_original_source(source, language):
    original = book(source, language, '```python\nspark.sql("SELECT * FROM $$SCHEMA.events")\n```')
    normalized = _notebook(original, 'variable-contract')
    assert unresolved_informatica_variables(normalized, 'notebook') == []
    assert [cell['source'] for cell in json.loads(normalized)['cells']] == [
        cell['source'] for cell in json.loads(original)['cells']]


def test_rejected_notebook_revision_preserves_exact_staged_bytes(scope):
    record, _ = scope
    staged = _StagedArtifacts(record)
    path = 'notebooks/load.ipynb'
    receipt = staged.stage(path, 'notebook', book('value = 1\n'))
    before = deepcopy(staged.files)
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        staged.stage(path, 'notebook', book('value = "$$VALUE"\n'), receipt['content_sha256'])
    assert staged.files == before


def review_value(scope, kind, source, *, invoke=None, maximum=160_000, allow_inference=False):
    record, evidence = scope
    raw = FIX['payload'](record)
    paths = dict(module='modules/generated.py', sql='sql/generated.sql', notebook='notebooks/generated.ipynb')
    raw['artifacts'] = [dict(relative_path=paths[kind], kind=kind, content=source,
        task_ids=['shared'], evidence_ids=['mapping'])]
    raw['tasks'][0].update(artifact_paths=[paths[kind]], obligations=[])
    value = GenerationArtifactSet.model_validate(raw)
    bodies = {artifact.relative_path: artifact.content for artifact in value.artifacts}
    receipt = GeneratedCodeReviewer(invoke, max_characters=maximum).review(
        value, bodies, record, evidence, allow_inference=allow_inference)
    return receipt


@pytest.mark.parametrize('kind,source', [
    ('module', 'value = f"$$SCHEMA.{table}"\n'),
    ('sql', 'SELECT * FROM $$SCHEMA.events'),
    ('notebook', book('dbutils.widgets.text("schema", "$$SCHEMA")\n')),
])
def test_review_reports_semantic_bug_without_provider(scope, kind, source):
    record, evidence = scope
    before = deepcopy((record, evidence.rows, evidence.sql))
    receipt = review_value(scope, kind, source)
    assert receipt['status'] == 'needs_correction' and not receipt['ai_reviewed']
    assert receipt['deterministic_variable_contract']
    issue = next(issue for issue in receipt['issues'] if issue['kind'] == 'semantic_bug')
    assert issue['task_id'] == 'shared' and issue['relative_path']
    assert '$$IDENT' in issue['detail']
    assert record == before[0]
    assert evidence.rows == before[1] and evidence.sql == before[2]
    assert not receipt['execution_allowed'] and not receipt['deployment_allowed']


def test_provider_review_ready_cannot_override_deterministic_variable_bug(scope):
    calls = []
    def invoke(messages, size):
        calls.append((messages, size))
        return SimpleNamespace(content='{"status":"review_ready","issues":[]}')
    receipt = review_value(scope, 'module', 'message = "$$MESSAGE"\n',
        invoke=invoke, allow_inference=True)
    assert len(calls) == 1
    assert receipt['ai_reviewed'] and receipt['status'] == 'needs_correction'
    assert receipt['issues'][0]['kind'] == 'semantic_bug'


def test_source_original_sql_and_markdown_are_not_executable_review_inputs(scope):
    record, evidence = scope
    evidence.sql['shared'][0]['original_sql'] = 'SELECT * FROM $$SCHEMA.original'
    before = deepcopy(evidence.sql)
    calls = []
    def invoke(messages, size):
        calls.append((messages, size))
        return SimpleNamespace(content='{"status":"review_ready","issues":[]}')
    receipt = review_value(scope, 'notebook', book('value = 1\n'),
        invoke=invoke, allow_inference=True)
    assert receipt['status'] == 'review_ready' and receipt['ai_reviewed']
    assert not receipt['deterministic_variable_contract']
    assert evidence.sql == before
    assert json.loads(calls[0][0][-1].content)['sql_phases'][0]['original_sql'] == before['shared'][0]['original_sql']


@pytest.mark.parametrize('source', [book(), book('# $$SCHEMA provenance only\nvalue = 1\n')])
def test_review_retains_whole_markdown_and_python_provenance_without_variable_bug(scope, source):
    receipt = review_value(scope, 'notebook', source)
    assert receipt['status'] == 'needs_information' and not receipt['ai_reviewed']
    assert not receipt['deterministic_variable_contract']
    assert all(issue['kind'] == 'review_unavailable' for issue in receipt['issues'])


def test_exact_total_review_bound_includes_system_and_all_serialized_scope(scope):
    source = '# ' + 'x' * 65_000 + '\nvalue = 1\n'
    calls = []
    def invoke(messages, size):
        calls.append((messages, size))
        return SimpleNamespace(content='{"status":"review_ready","issues":[]}')
    receipt = review_value(scope, 'module', source, invoke=invoke, allow_inference=True)
    assert receipt['status'] == 'review_ready' and len(calls) == 1
    messages, actual_total = calls[0]
    assert actual_total == sum(len(message.content) for message in messages)
    assert 64_000 < actual_total <= 160_000
    assert json.loads(messages[-1].content)['artifacts'][0]['content'] == source
    expected_hashes = {'modules/generated.py': sha256(source.encode('utf-8')).hexdigest()}
    assert receipt['tasks'][0]['artifact_hashes'] == expected_hashes
    calls.clear()
    at_bound = review_value(scope, 'module', source, invoke=invoke,
        maximum=actual_total, allow_inference=True)
    assert at_bound['ai_reviewed'] and len(calls) == 1
    for maximum in (64_000, actual_total - 1):
        calls.clear()
        blocked = review_value(scope, 'module', source, invoke=invoke,
            maximum=maximum, allow_inference=True)
        assert not calls and not blocked['ai_reviewed']
        assert blocked['status'] == 'needs_information' and blocked['runtime_verified'] is False
        assert blocked['tasks'][0]['artifact_hashes'] == expected_hashes
        assert blocked['issues'][0]['kind'] == 'review_unavailable'
        assert 'bounded review context' in blocked['issues'][0]['detail']
        assert 'no truncated review claimed' in blocked['issues'][0]['detail']
        assert blocked['execution_allowed'] is False and blocked['deployment_allowed'] is False


def candidate(scope):
    record, evidence = scope
    staged = _StagedArtifacts(record)
    file = staged.stage('notebooks/load.ipynb', 'notebook', FIX['notebook']())
    metadata = _GenerationReceipts(record, staged, evidence, 'variable-contract', candidate=True)
    metadata.binding(file['relative_path'], ['shared'], ['mapping'], file['content_sha256'])
    return metadata


@pytest.mark.parametrize('defect', ['owner', 'path', 'key', 'kind', 'hash', 'bytes', 'chunk', 'citation', 'duplicate_owner', 'duplicate_citation'])
def test_candidate_projection_rejects_corrupt_binding_before_unsafe_indexing(scope, defect):
    metadata = candidate(scope)
    path = 'notebooks/load.ipynb'
    ref = metadata.bindings[path]['value']
    if defect == 'owner':
        ref['task_ids'] = ['outside']
    elif defect == 'path':
        ref['relative_path'] = 'notebooks/../escape.ipynb'
    elif defect == 'key':
        metadata.bindings['notebooks/other.ipynb'] = metadata.bindings.pop(path)
    elif defect == 'kind':
        ref['kind'] = 'module'
    elif defect == 'hash':
        ref['content_sha256'] = 'f' * 64
    elif defect == 'bytes':
        metadata.staged.files[path]['content'] += ' '
    elif defect == 'chunk':
        metadata.staged.chunks[path] = dict(relative_path=path, kind='notebook')
    elif defect == 'citation':
        ref['evidence_ids'] = ['outside']
    elif defect == 'duplicate_owner':
        ref['task_ids'] *= 2
    else:
        ref['evidence_ids'] *= 2
    before = deepcopy(metadata.bindings)
    for project in (metadata.candidate_projection, metadata.assemble):
        with pytest.raises(ContractError):
            project()
    assert metadata.bindings == before


def test_projection_preserves_generated_vs_blocked_and_completion_contract(scope):
    metadata = candidate(scope)
    projected = metadata.candidate_projection()
    assert projected.tasks[0].status == 'drafted'
    assert projected.tasks[0].blockers == [] and projected.translation_coverage is None
    metadata.bindings.clear()
    projected = metadata.candidate_projection()
    assert projected.tasks[0].status == 'blocked' and projected.tasks[0].blockers
    for status in ('generation_complete', 'drafts_saved', 'blocked'):
        availability = metadata.candidate_availability(status, {})
        assert availability['generation_status'] == status
        assert availability['generation_complete'] == (status == 'generation_complete')
        assert not availability['execution_allowed'] and not availability['deployment_allowed']


def test_fully_authored_candidate_keeps_strict_generation_contract(scope):
    record, evidence = scope
    raw = FIX['payload'](record)
    # Distinct, canonical file citations make the projected provenance union
    # observable, rather than only testing an unchanged single citation.
    raw['artifacts'][0]['evidence_ids'] = ['mapping']
    expected = GenerationArtifactSet.model_validate(raw).tasks[0].model_dump()
    staged = _StagedArtifacts(record)
    metadata = _GenerationReceipts(record, staged, evidence, 'variable-contract', candidate=True)
    for artifact in raw['artifacts']:
        file = staged.stage(artifact['relative_path'], artifact['kind'], artifact['content'])
        metadata.binding(file['relative_path'], artifact['task_ids'], artifact['evidence_ids'], file['content_sha256'])
    metadata.task(raw['tasks'][0])
    authored = deepcopy(metadata.tasks)
    bindings = deepcopy(metadata.bindings)
    value = metadata.assemble()
    invariant_fields = ('task_id', 'implementation_kind', 'status', 'artifact_paths', 'blockers', 'obligations')
    actual = value.tasks[0].model_dump()
    assert {key: actual[key] for key in invariant_fields} == {key: expected[key] for key in invariant_fields}
    assert actual['evidence_ids'] == expected['evidence_ids']
    assert value.tasks[0].status == 'drafted' and metadata.recovery['validated']
    _validate(value, record, evidence, candidate=False)
    projected = metadata.candidate_projection()
    task = projected.tasks[0].model_dump()
    assert {key: task[key] for key in invariant_fields if key != 'obligations'} == {
        key: expected[key] for key in invariant_fields if key != 'obligations'}
    assert task['obligations'] == []  # authored obligations remain review evidence, not certification
    fetched = evidence.fetched['shared'] | evidence.sql_fetched['shared']
    canonical_ids = {identifier for row in evidence.rows['shared'] + evidence.sql['shared']
                     if row['key'] in fetched for identifier in row['evidence_ids']}
    expected_union = sorted({identifier for row in bindings.values()
                             if 'shared' in row['value']['task_ids']
                             for identifier in row['value']['evidence_ids']})
    assert expected_union == ['expression', 'mapping']
    assert set(expected_union) <= canonical_ids and 'outside' not in canonical_ids
    assert task['evidence_ids'] == expected_union
    assert metadata.tasks == authored and metadata.bindings == bindings
    assert projected.translation_coverage is None
    assert FIX['_translation_assessment'](projected, evidence)['status'] == 'unverified'
    assert [item['submitted'] for item in metadata.recovery['review_issues']
            if item['kind'] == 'obligation_pending_review'] == expected['obligations']


def test_projected_code_does_not_clear_incomplete_retrieval_gate(scope):
    record, evidence = scope
    metadata = candidate(scope)
    evidence.sql_fetched['shared'].clear()
    value = metadata.candidate_projection()
    assert value.tasks[0].status == 'drafted' and value.translation_coverage is None
    assert not evidence.complete('shared')
    assert any(issue['kind'] == 'source_retrieval_incomplete' and issue['task_id'] == 'shared'
               for issue in metadata.recovery['review_issues'])
    with pytest.raises(ContractError, match='complete exact source and SQL-phase retrieval'):
        _validate(value, record, evidence, candidate=False)


@pytest.mark.parametrize('source', [
    'if enabled:\n    dbutils.widgets.text("schema", "")\n',
    'dbutils.widgets.text("schema", runtime_default)\n',
    'schema = dbutils.widgets.get("schema")\n',
    'dbutils.widgets.text("schema", "one")\ndbutils.widgets.text("schema", "two")\n',
])
def test_existing_widget_contract_remains_strict(source):
    assert parameter_contract({}, {'notebooks/load.ipynb': book(source)})['missing_defaults']


def test_yaml_uses_only_literal_declared_defaults_not_design_guesses(scope):
    record, _ = scope
    design = deepcopy(record['design'])
    design['parameters'] = [dict(name='unused', purpose='Design evidence only')]
    source = ('dbutils.widgets.text(name="schema", defaultValue="")\n'
              'schema = dbutils.widgets.get("schema")\n')
    path = 'notebooks/load.ipynb'
    contract = parameter_contract(design, {path: book(source)})
    assert contract['parameters'] == [dict(name='schema', default='')]
    assert contract['design_only_parameters'] == ['unused'] and not contract['missing_defaults']
    manifest = dict(tasks=[dict(task_id='shared', implementation_kind='notebook_task',
        status='drafted', blockers=[], artifact_paths=[path])])
    document = json.loads(build(design, manifest, {path: book(source)}, '/Workspace/review'))
    job = document['resources']['jobs']['migration_draft']
    assert job['parameters'] == contract['parameters']
    assert job['tasks'][0]['notebook_task']['notebook_path'] == '/Workspace/review/notebooks/load'
    with pytest.raises(ContractError, match='Unresolved Informatica'):
        build(design, manifest, {path: book('dbutils.widgets.text("schema", "$$SCHEMA")\n')}, '/Workspace/review')