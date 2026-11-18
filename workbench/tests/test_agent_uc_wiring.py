"""Offline parent/provider/writer integration. No cloud, generated-code execution or deployment."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import socket
from threading import BoundedSemaphore
from types import SimpleNamespace as NS

from databricks.sdk.service.catalog import CatalogsAPI, SchemasAPI, TablesAPI
from fastapi import FastAPI
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
import pytest

from workbench import hosted, model_access
from workbench.agent_uc_metadata import UCMetadata, digest
from workbench.agents import ROLES, _hash
from workbench.databricks_models import AuthenticationUnavailable
from workbench.generated_code_review import GeneratedCodeReviewer
from workbench.redesign import store_for
from workbench.spark_codegen import (
    GenerationArtifactSet, SparkCodegen, _Evidence, _inherited_uc_metadata, _uc_review_scope,
    _uc_task_scope,
)


FIX = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tests' / 'test_spark_codegen.py'))
HOST = 'https://selected.example.com'
SOURCE = digest('selected-source')
TARGET = digest('selected-target')
FOREIGN = digest('outside-source')
PARENT_TABLE = 'chosen.sap.actual_source'
TARGET_TABLE = 'chosen.curated.actual_target'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    monkeypatch.setenv('WORKBENCH_LANGSMITH_ENABLED', '0')
    monkeypatch.delenv('WORKBENCH_MODEL', raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('Live network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


class ReadOnlyApi:
    """Actual SDK metadata services, transport accepts only GET metadata endpoints."""
    _cfg = NS(workspace_id=None)

    def __init__(self):
        self.calls = []

    def do(self, method, path, **kwargs):
        assert method == 'GET'
        self.calls.append((method, path, kwargs))
        if path.endswith('/catalogs'):
            return {'catalogs': [{'name': 'chosen'}]}
        if path.endswith('/schemas'):
            catalog = kwargs['query']['catalog_name']
            return {'schemas': [{'name': 'sap', 'catalog_name': catalog, 'full_name': catalog + '.sap'}]}
        if path.endswith('/tables'):
            query = kwargs['query']
            return {'tables': [self.table(query['catalog_name'] + '.' + query['schema_name'] + '.actual_source')]}
        prefix = '/api/2.1/unity-catalog/tables/'
        assert path.startswith(prefix)
        return self.table(path[len(prefix):])

    @staticmethod
    def table(name):
        catalog, schema, short = name.split('.')
        return dict(full_name=name, name=short, catalog_name=catalog, schema_name=schema,
            columns=[dict(name='amount', type_text='DECIMAL(18,2)', type_name='DECIMAL',
                          nullable=True, position=0, type_precision=18, type_scale=2)],
            properties={'password': 'NEVER_PROPERTIES'}, comment='NEVER_COMMENT',
            storage_location='NEVER_LOCATION', view_definition='NEVER_VIEW_SQL')


def identity(api=None, *, host=HOST, user='user'):
    api = api or ReadOnlyApi()
    return NS(host=host, user_id=user, mode='obo',
              client=NS(catalogs=CatalogsAPI(api), schemas=SchemasAPI(api), tables=TablesAPI(api)))


def chat():
    return FIX['ScriptedChat'](responses=[AIMessage(content='unused')])


def test_local_selected_runtime_lazy_fresh_labs_provider_and_host_pin(tmp_path, monkeypatch):
    first, second = identity(), identity()
    calls = []
    current = [first]
    def labs(profile):
        assert profile == 'labs'
        calls.append(profile)
        return current[0]
    monkeypatch.setattr(model_access, 'profile_identity', labs)
    routes = {role: chat() for role in ROLES}
    monkeypatch.setattr(model_access.WorkspaceModels, 'discover', lambda self, who: {'endpoints': []})
    monkeypatch.setattr(model_access.WorkspaceModels, 'configure', lambda *a, **k: None)
    monkeypatch.setattr(model_access.WorkspaceModels, 'app_chat_models', lambda self, who: routes)
    service = NS(root=tmp_path, agent_slot=BoundedSemaphore(1))
    app = FastAPI()
    app.state.service = service
    model_access.install_model_access(app, identity_factory=lambda: first)
    # Invoke the installed real handlers without Windows asyncio socketpair setup.
    endpoints = {route.path: route.endpoint for route in app.routes if hasattr(route, 'endpoint')}
    assert endpoints['/api/models/connect']()['connected'] is True
    selected = endpoints['/api/models/select'](model_access.RouteSelection(endpoint='chat', tool_support_confirmed=True))
    assert selected['runtime_verified'] is False
    assert calls == [] and all(not model._calls for model in routes.values())
    provider = service.runtime.uc_identity_provider
    assert provider() is first
    current[0] = second
    assert provider() is second
    current[0] = identity(host='https://other.example.com')
    with pytest.raises(AuthenticationUnavailable):
        provider()
    state = UCMetadata(provider, 'writer')
    assert state.observe('artifact', 'tables.get', full_name=PARENT_TABLE)['status'] == 'unknown'
    assert calls == ['labs'] * 4
    assert not list(tmp_path.rglob('manifest.json'))


def test_hosted_runtime_current_provider_context_isolated_and_no_model_side_effects(tmp_path, monkeypatch):
    routes = {role: chat() for role in ROLES}
    monkeypatch.setattr(hosted.WorkspaceModels, 'load', lambda self, who: {'artifact': {'endpoint': 'chat'}})
    monkeypatch.setattr(hosted.WorkspaceModels, 'app_chat_models', lambda self, who: routes)
    app = FastAPI()
    app.state.service = NS(root=tmp_path, knowledge_loader=lambda: {})
    app.state.hosted_execution_models = True
    alice, bob = identity(user='alice'), identity(user='bob')
    token = hosted._CURRENT.set(alice)
    try:
        hosted.install_hosted_models(app)
        runtime = app.state.service.runtime
        assert runtime.uc_identity_provider is hosted.current_identity
        assert runtime.uc_identity_provider() is alice
        with hosted.ActionExecutor(max_workers=1) as executor:
            first = executor.submit(runtime.uc_identity_provider)
            other = hosted._CURRENT.set(bob)
            try:
                second = executor.submit(runtime.uc_identity_provider)
            finally:
                hosted._CURRENT.reset(other)
            assert first.result() is alice and second.result() is bob
    finally:
        hosted._CURRENT.reset(token)
    with pytest.raises(AuthenticationUnavailable):
        runtime.uc_identity_provider()
    assert UCMetadata(runtime.uc_identity_provider, 'outside-action').observe(
        'artifact', 'tables.get', full_name=PARENT_TABLE)['status'] == 'unknown'
    assert all(not model._calls for model in routes.values())


@pytest.fixture
def ctx(tmp_path):
    service, old, model, inputs = FIX['context'].__wrapped__(tmp_path)
    template = deepcopy(next(node for node in model['nodes'] if node['id'] == 'expression'))
    for canonical_id, kind, owner in ((SOURCE, 'source', 'mapping'), (TARGET, 'target', 'mapping'),
                                       (FOREIGN, 'source', 'outside')):
        node = deepcopy(template)
        node.update(id=canonical_id, kind=kind, name='same_captured_name', parent_id=owner,
                    attributes={'NAME': 'same_captured_name', 'OWNERNAME': 'SAP'}, properties=[])
        model['nodes'].append(node)
        model['edges'].append(dict(id=digest(canonical_id + '-edge'), source=owner, target=canonical_id,
                                   kind='contains', attributes={}, evidence={}))
    runtime_hashes = dict(model=_hash(model), documents=digest([]), feedback=digest([]))
    origin = {**old['source'], 'record_id': 'origin-parent', 'runtime_run_id': '2' * 32,
              'runtime_input_hashes': runtime_hashes}
    record = store_for(service).append(old['package_hash'], 'wf', 'original',
        dict(design=old['design'], role='architecture', retrieved_evidence_ids=old['retrieved_evidence_ids']),
        origin, old['version'])
    api, factories = ReadOnlyApi(), []
    def provider():
        factories.append('fresh')
        return identity(api)
    service.runtime.identity_scope = HOST + ':user'
    service.runtime.uc_identity_provider = provider
    parent_uc = UCMetadata(provider, origin['runtime_run_id'])
    observation = parent_uc.observe('discovery', 'tables.get', full_name=PARENT_TABLE)
    saved = parent_uc.record('discovery', dict(canonical_id=SOURCE, full_name=PARENT_TABLE,
        observation_id=observation['observation_id'], observation_sha256=observation['observation_sha256'],
        reason='Captured source owner and amount column identify this candidate.', evidence_refs=[SOURCE]),
        {SOURCE: 'source'}, {SOURCE})
    assert saved['recorded']
    parent = dict(id='origin-parent', workflow_id='wf', scenario='original', package_hash=record['package_hash'],
        input_version=inputs['input_version'], input_hashes=inputs['input_hashes'], input_versions=inputs['input_versions'],
        consent=True, generate_code=True, status='running',
        output=dict(run_id=origin['runtime_run_id'], input_hashes=runtime_hashes, **parent_uc.snapshot()))
    def read(record_id, agent=False):
        assert record_id == 'origin-parent' and agent is True
        return deepcopy(parent)
    service.record = read
    api.calls.clear()
    factories.clear()
    return service, record, model, inputs, parent, api, factories


def inherited(ctx):
    service, record, model, inputs, *_ = ctx
    return _inherited_uc_metadata(service, record, _Evidence(model, record), 'origin-parent', inputs)


def test_parent_exact_output_receipts_and_source_scope_keys(ctx):
    result = inherited(ctx)
    assert result == {key: ctx[4]['output'][key] for key in result}
    assert result['uc_table_bindings'][0]['run_id'] == ctx[4]['output']['run_id']
    assert result['uc_table_bindings'][0]['canonical_id'] == SOURCE
    assert ctx[-1] == [] and ctx[-2].calls == []
    evidence = _Evidence(ctx[2], ctx[1])
    allowed, _ = _uc_task_scope(evidence, 'shared')
    assert allowed == {SOURCE: 'source', TARGET: 'target'}
    assert FOREIGN not in allowed
    # A foreign ID on a connector citation cannot masquerade as an owned header.
    evidence.rows['shared'].append(dict(key='foreign-edge', evidence_ids=[FOREIGN],
                                       value={'id': FOREIGN, 'kind': 'source'}))
    assert FOREIGN not in _uc_task_scope(evidence, 'shared')[0]


@pytest.mark.parametrize('change', ['id', 'workflow_id', 'scenario', 'package_hash', 'input_version',
    'input_hashes', 'input_versions', 'runtime_run_id', 'runtime_input_hashes', 'output_run',
    'output_hashes', 'observation_run', 'observation_hash', 'observation_id', 'observation_workspace',
    'binding_hash', 'binding_run', 'binding_scope', 'binding_refs', 'binding_kind', 'missing_output'])
def test_parent_stale_unrelated_or_tampered_claims_not_inherited(ctx, change):
    parent, record = ctx[4], ctx[1]
    if change in {'id', 'workflow_id', 'scenario', 'package_hash', 'input_version'}:
        parent[change] = 'unrelated'
    elif change in {'input_hashes', 'input_versions'}:
        parent[change] = {'other': 'unrelated'}
    elif change in {'runtime_run_id', 'runtime_input_hashes'}:
        record['source'][change] = 'unrelated'
    elif change == 'missing_output':
        parent.pop('output')
    elif change in {'output_run', 'output_hashes'}:
        parent['output']['run_id' if change == 'output_run' else 'input_hashes'] = 'unrelated'
    elif change.startswith('observation_'):
        key = {'observation_run': 'run_id', 'observation_hash': 'observation_sha256',
               'observation_id': 'observation_id', 'observation_workspace': 'workspace'}[change]
        parent['output']['uc_metadata_observations'][0][key] = 'unrelated'
    else:
        row = parent['output']['uc_table_bindings'][0]
        key = {'binding_hash': 'binding_sha256', 'binding_run': 'run_id', 'binding_scope': 'canonical_id',
               'binding_refs': 'evidence_refs', 'binding_kind': 'kind'}[change]
        row[key] = [SOURCE, FOREIGN] if change == 'binding_refs' else FOREIGN if change == 'binding_scope' else 'unrelated'
        if change != 'binding_hash':
            row['binding_sha256'] = digest({key: value for key, value in row.items() if key != 'binding_sha256'})
    assert inherited(ctx) == dict(uc_metadata_observations=[], uc_table_bindings=[])
    assert ctx[-1] == []


def test_parent_observation_ordinal_verified_before_filtering(ctx):
    parent = ctx[4]
    observation = parent['output']['uc_metadata_observations'][0]
    unrelated = deepcopy(observation)
    unrelated['observation_id'] = digest(['2' * 32, 1, unrelated['observation_sha256']])
    parent['output']['uc_metadata_observations'].append(unrelated)
    assert len(inherited(ctx)['uc_metadata_observations']) == 1
    parent['output']['uc_metadata_observations'].reverse()
    assert not inherited(ctx)['uc_table_bindings']


class Writer(FIX['ScriptedChat']):
    binding_source: str = TARGET
    _step: int = PrivateAttr(default=0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls.append(messages)
        step = self._step
        self._step += 1
        if step == 0:
            envelope = next(json.loads(m.content) for m in messages
                            if m.type == 'human' and isinstance(m.content, str))
            self._envelope = envelope
            assert [b['canonical_id'] for b in envelope['uc_table_bindings']] == [SOURCE]
            assert envelope['uc_table_bindings'][0]['full_name'] == PARENT_TABLE
            actions = [('fetch_source_task', {'task_id': 'shared', 'offset': offset}) for offset in (0, 12, 24)] + [
                       ('uc_list_catalogs', {}), ('uc_list_schemas', {'catalog': 'chosen'}),
                       ('uc_list_tables', {'catalog': 'chosen', 'schema_name': 'sap'}),
                       ('uc_get_table', {'full_name': TARGET_TABLE})]
        elif step == 1:
            observation = next(json.loads(m.content) for m in reversed(messages)
                               if isinstance(m, ToolMessage) and m.name == 'uc_get_table')
            actions = [('record_uc_binding', dict(task_id='shared', binding=dict(
                canonical_id=self.binding_source, full_name=TARGET_TABLE,
                observation_id=observation['observation_id'], observation_sha256=observation['observation_sha256'],
                reason='Captured separate target identity and amount column evidence.', evidence_refs=[self.binding_source])))]
        elif step == 2:
            actions = [('uc_metadata_state', {})]
        else:
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='Stop without inventing code'))])
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            dict(name=name, args=args, id=str(step) + '-' + str(i)) for i, (name, args) in enumerate(actions)]))])

    _envelope: dict = PrivateAttr(default_factory=dict)


@pytest.mark.parametrize('canonical_id,recorded', [(TARGET, True), (FOREIGN, False)])
def test_actual_writer_sdk_metadata_tools_parent_prompt_and_combined_report(ctx, canonical_id, recorded):
    service, record, _, _, parent, api, factories = ctx
    writer = Writer(binding_source=canonical_id, responses=[AIMessage(content='unused')])
    service.runtime.role_models = {'artifact': writer}
    agent = SparkCodegen(service, record, agent_run_id=parent['id'])
    assert factories == [] and writer._calls == []
    before = (service.state / 'redesigns.sqlite').read_bytes()
    report = agent.draft(consent=True)
    assert report['failure'] is None
    assert (service.state / 'redesigns.sqlite').read_bytes() == before
    assert len(report['uc_table_bindings']) == (2 if recorded else 1)
    assert report['uc_table_bindings'][0]['run_id'] == parent['output']['run_id']
    assert report['uc_metadata_observations'][0] == parent['output']['uc_metadata_observations'][0]
    assert {r['run_id'] for r in report['uc_metadata_observations'][1:]} == {report['run_id']}
    if recorded:
        assert report['uc_table_bindings'][1]['kind'] == 'target'
        assert report['uc_table_bindings'][1]['run_id'] == report['run_id']
    names = set(agent.graph.nodes['tools'].bound.tools_by_name)
    assert {'uc_get_table', 'uc_list_catalogs', 'uc_list_schemas', 'uc_list_tables', 'record_uc_binding', 'uc_metadata_state'} <= names
    assert not names & {'execute_sql', 'query', 'write_file'}
    assert len(factories) == 4 and len(api.calls) == 4
    assert all(method == 'GET' for method, _, _ in api.calls)
    prompt = str(writer._calls[0][0].content)
    assert 'Source read identity and target write identity' in prompt
    assert 'SQL variables' in prompt and 'Destinations are optional' in prompt
    assert 'No static naming rules' in prompt
    manifest = json.loads((service.root / report['relative_directory'] / 'manifest.json').read_text())
    assert manifest['uc_table_bindings'] == report['uc_table_bindings']
    encoded = json.dumps(report)
    assert all(secret not in encoded for secret in ['NEVER_PROPERTIES', 'NEVER_COMMENT', 'NEVER_LOCATION', 'NEVER_VIEW_SQL'])


def test_writer_absent_provider_unavailable_no_fallback_or_new_gate(ctx):
    service, record, _, _, parent, api, factories = ctx
    service.runtime.uc_identity_provider = None
    writer = Writer(responses=[AIMessage(content='unused')])
    service.runtime.role_models = {'artifact': writer}
    # Absent provider still gives a same-writer unavailable observation and no SDK call.
    writer.responses = [AIMessage(content='unused')]
    report = SparkCodegen(service, record, agent_run_id=parent['id']).draft(consent=True)
    assert report['failure'] is None and factories == [] and api.calls == []
    assert {r['status'] for r in report['uc_metadata_observations'][1:]} == {'provider_unavailable'}
    assert len(report['uc_table_bindings']) == 1
    assert writer._envelope['uc_provider_available'] is False


def test_reviewer_supplied_metadata_is_task_scoped_hash_sensitive_and_not_parity(ctx):
    _, record, model, *_ = ctx
    evidence = FIX['retrieved'](model, record)
    value, bodies = GenerationArtifactSet.model_validate(FIX['payload'](record)), {}
    for artifact in value.artifacts:
        bodies[artifact.relative_path] = artifact.content
    metadata = _uc_review_scope(inherited(ctx), evidence)
    calls = []
    def invoke(messages, size):
        calls.append(messages)
        return AIMessage(content=json.dumps(dict(status='review_ready', issues=[])))
    reviewer = GeneratedCodeReviewer(invoke)
    receipt = reviewer.review(value, bodies, record, evidence, uc_metadata=metadata)
    scope = json.loads(calls[0][-1].content)
    assert scope['uc_metadata']['uc_table_bindings'][0]['canonical_id'] == SOURCE
    assert 'Metadata alone cannot establish behavioral correctness' in calls[0][0].content
    assert receipt['runtime_verified'] is False and receipt['execution_allowed'] is False
    reviewer.review(value, bodies, record, evidence, uc_metadata=metadata)
    assert len(calls) == 1
    reviewer.review(value, bodies, record, evidence, uc_metadata={'shared': {}})
    assert len(calls) == 2
    # Backward-compatible omission still uses the unchanged XML/code scope contract.
    reviewer.review(value, bodies, record, evidence)
    assert len(calls) == 3
    assert 'uc_metadata' not in json.loads(calls[-1][-1].content)


def test_actual_saved_writer_code_reviewer_receives_verified_parent_only(ctx):
    service, record, model, _, parent, api, factories = ctx
    captured = []
    class ReviewChat(FIX['ScriptedChat']):
        def bind_tools(self, tools, **kwargs):
            raise AssertionError('Reviewer remains tool-free')
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            captured.append(json.loads(messages[-1].content))
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content=json.dumps(dict(status='review_ready', issues=[]))))])
    writer = FIX['script'](model, record)
    reviewer = ReviewChat(responses=[AIMessage(content='unused')])
    service.runtime.role_models = {'artifact': writer, 'validation': reviewer}
    report = SparkCodegen(service, record, agent_run_id=parent['id']).draft(consent=True)
    assert report['generated_code_review']['ai_reviewed'] is True
    assert captured[0]['uc_metadata']['uc_table_bindings'][0]['full_name'] == PARENT_TABLE
    assert captured[0]['uc_metadata']['uc_metadata_observations'][0]['run_id'] == parent['output']['run_id']
    assert not factories and not api.calls
    assert report['validation']['runtime_verified'] is False