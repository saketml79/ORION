"""Offline regressions plus an explicitly invoked, isolated real-model demo runner.

No live invocation occurs during pytest. The runner requires an existing explicit
route file for the resolved labs user, and never configures or chooses endpoints.
"""
from copy import deepcopy
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
import pytest

from workbench.agents import AgentRuntime, Finding, ROLES, _Evidence, _POLICY, _design_submission_schema, _safe_validation_issues, build_agents
from workbench.migration_planning import PlanningIndex
from workbench.redesign import Design, RedesignStore, validate_submission
from workbench.tests.test_redesign import chat, design_for, model, tool_call


def test_typed_artifact_scoped_email_cadence_and_administrative_start():
    source = model()
    value = design_for(source)
    value['coverage'][0].update(disposition='removed', proposed_task_ids=[],
                                rationale='Administrative Start represented by job entry, not a notebook')
    value['artifact_plan'] = [{'relative_path': 'notebooks/load.ipynb', 'artifact_kind': 'notebook',
        'proposed_task_ids': ['logical-load'], 'mapping_ids': [],
        'description': 'Preserve session transform and write boundary', 'evidence_ids': ['b']}]
    value['scoped_unresolved'] = [{'proposed_task_ids': ['logical-load'], 'category': 'notification',
        'impact': 'peripheral_note', 'description': 'Email configuration unavailable; retain notification intent',
        'evidence_ids': ['b']}]
    value['cadence'] = {'launch_mode': 'unknown', 'preservation': 'No invented recurring schedule',
                        'evidence_ids': ['a']}
    valid = validate_submission(value, source, 'wf', {'a', 'b'}, _Evidence(source, 'wf', [], []).records)
    assert valid.blockers == [] and valid.scoped_unresolved[0].impact == 'peripheral_note'
    assert valid.coverage[0].proposed_task_ids == []
    assert valid.cadence is not None
    assert valid.cadence.operational_activation == 'unverified'
    for path in ('../escape.py', '/absolute.py', 'C:/drive.py', 'notebooks//load.py', 'x.sql'):
        bad = deepcopy(value)
        bad['artifact_plan'][0]['relative_path'] = path
        with pytest.raises(ValueError):
            Design.model_validate(bad)
    for key in ('artifact_plan', 'scoped_unresolved'):
        bad = deepcopy(value)
        bad[key][0]['proposed_task_ids'] = ['invented']
        with pytest.raises(ValueError):
            Design.model_validate(bad)
    bad = deepcopy(value)
    bad['artifact_plan'][0]['mapping_ids'] = ['a']
    with pytest.raises(ValueError, match='canonical mapping'):
        validate_submission(bad, source, 'wf', {'a', 'b'}, _Evidence(source, 'wf', [], []).records)


def test_optional_empty_design_sections_do_not_consume_model_repair_calls():
    value = design_for(model())
    for key in ('evidence_ids', 'reuse_groups', 'parameters', 'proposed_constraints', 'blockers'):
        value.pop(key)
    design = Design.model_validate(value)
    assert design.evidence_ids == []
    assert design.reuse_groups == []
    assert design.parameters == []
    assert design.proposed_constraints == []
    assert design.blockers == []


def test_candidate_design_does_not_require_artifact_plan(tmp_path):
    source = model()
    candidate = design_for(source)
    candidate['artifact_plan'] = []
    with build_agents(tmp_path, source, 'wf', 'bronze', chat_model=chat()) as bundle:
        bundle.design_requested = True
        bundle.tools['architecture']['workflow_planning_contract'].invoke({'section': 'tasks'})
        accepted = bundle.tools['architecture']['submit_redesign'].invoke({'design': candidate})
        assert accepted['recorded'] is True
        candidate['coverage'][0]['evidence_ids'] = ['not-retrieved']
        rejected = bundle.tools['architecture']['submit_redesign'].invoke({'design': candidate})
        assert rejected['recorded'] is False


def test_artifact_plan_feedback_specifies_notebook_path_and_mapping_ids(tmp_path):
    from workbench.agents import _DESIGN_GUIDE
    assert "notebook path MUST end in .ipynb" in _DESIGN_GUIDE
    invalid = design_for(model())
    invalid['artifact_plan'][0]['relative_path'] = 'notebooks/load.py'
    normalized = Design.model_validate(invalid)
    assert normalized.artifact_plan[0].relative_path == 'notebooks/load.ipynb'
    invalid['artifact_plan'][0]['relative_path'] = 'notebooks/load'
    assert Design.model_validate(invalid).artifact_plan[0].relative_path == 'notebooks/load.ipynb'
    invalid['artifact_plan'][0]['relative_path'] = 'notebooks/load.sql'
    with pytest.raises(Exception) as caught:
        Design.model_validate(invalid)
    feedback = _safe_validation_issues(caught.value)
    assert feedback and 'relative .ipynb or .py paths' in feedback[0]['issue']
    assert 'mapping_ids must be retrieved canonical mapping IDs only' in feedback[0]['issue']


def test_long_edge_predicate_feedback_is_specific_and_payload_free():
    value = design_for(model())
    value['edges'] = [{'source': 'logical-load', 'target': 'logical-load',
                       'evidence_ids': ['b'], 'condition': 'x' * 2401}]
    with pytest.raises(Exception) as caught:
        Design.model_validate(value)
    issues = _safe_validation_issues(caught.value)
    assert issues == [{'field': 'edges.0.condition',
                       'issue': 'Provide only the exact short predicate, within the 2400-character limit; do not repeat the workflow rationale. max_length=2400 characters; actual_length=2401.',
                       'code': 'string_too_long', 'max_length': 2400, 'actual_length': 2401}]
    assert 'x' * 100 not in json.dumps(issues)


def test_paged_exact_fields_no_cross_role_citation_laundering(tmp_path):
    source = model()
    source['schema_version'] = '1.0'
    source['nodes'][1]['fields'] = [
        {'kind': 'transformfield', 'attributes': {'NAME': f'port_{i}', 'EXPRESSION': 'x' * 1500 + str(i)}}
        for i in range(25)]
    index = PlanningIndex(source)
    first = index.agent_contract('wf', 'mapping_fields', limit=12)
    assert first['total'] == 25 and first['has_more'] and first['next_offset'] == 12
    assert first['items'][0]['record']['attributes']['EXPRESSION'] == 'x' * 1500 + '0'
    assert not first['text_truncations']
    assert index.agent_contract('wf', 'mapping_fields', offset=24)['returned'] == 1
    assert index.agent_contract('wf', 'mapping_fields', text_limit=10)['text_truncations']
    with build_agents(tmp_path, source, 'wf', 'original', chat_model=chat()) as bundle:
        for role in ROLES:
            assert 'workflow_planning_contract' in bundle.tools[role]
            planning_docs = bundle.tools[role]['workflow_planning_contract'].description
            for semantic in ('exact expressions', 'session settings', 'connections and parameters',
                             'sanitized Java control', 'inbound/outbound pmcmd', 'Pagination/truncation'):
                assert semantic in planning_docs
            sql_docs = bundle.tools[role]['sql_role_inventory'].description
            assert 'task ownership' in sql_docs and 'overrides have distinct semantics' in sql_docs
        design_schema = bundle.tools['architecture']['submit_redesign'].args_schema.model_json_schema()
        design_payload = design_schema['properties']['design']
        assert {'title', 'tasks', 'edges', 'alternatives', 'coverage'} <= set(design_payload['required'])
        assert 'evidence_ids' not in design_payload['required']
        # Planning keeps the Design API, not the writer's provenance schema.
        assert set(design_payload['properties']) == set(Design.model_json_schema()['properties'])
        assert {'artifact_plan', 'scoped_unresolved', 'cadence'} <= set(design_payload['properties'])
        assert not {'translation_coverage', 'input_data_basis', 'behavior_basis'} & set(design_payload['properties'])
        task_schema = design_payload['properties']['tasks']['items']['properties']
        assert {'id', 'label', 'implementation_kind', 'purpose', 'evidence_ids'} <= set(task_schema)
        assert 'pipeline_task' in task_schema['implementation_kind']['enum']
        finding_schema = bundle.tools['discovery']['submit_finding'].args_schema.model_json_schema()
        assert {'role', 'summary', 'status'} <= set(finding_schema['properties']['finding']['required'])
        report = bundle.tools['architecture']['workflow_planning_contract'].invoke({'section': 'tasks'})
        assert set(report['evidence_ids']) == {'a', 'b'}
        bundle.tools['architecture']['submit_redesign'].invoke({'design': design_for(source)})
        assert bundle.tools['supervisor']['redesign_handoffs'].invoke({})['total'] == 1
        rejected = bundle.tools['supervisor']['submit_redesign'].invoke({'design': design_for(source)})
        assert rejected['recorded'] is False and rejected['error'] == 'design_evidence_not_retrieved'


def test_bronze_source_contract_is_an_input_assumption_not_a_missing_table(tmp_path):
    source = model()
    source_node = {'id': 'source-definition', 'kind': 'source', 'name': 'ARC_SO_HEADER',
                   'parent_id': 'wf', 'scope': {}, 'attributes': {'NAME': 'ARC_SO_HEADER'},
                   'evidence': {'locator': '/source'}, 'fields': [], 'properties': []}
    source['nodes'].append(source_node)
    source['edges'].append({'id': 'contains-source', 'source': 'wf', 'target': 'source-definition',
                            'kind': 'contains', 'attributes': {}, 'evidence': {}})
    with build_agents(tmp_path, source, 'wf', 'bronze', chat_model=chat()) as bundle:
        result = bundle.tools['discovery']['source_contracts'].invoke({})
        contract = next(item for item in result['contracts'] if item['id'] == 'source-definition')
        assert contract['input_basis'] == 'assumed_available_in_unity_catalog_for_selected_bronze_scenario'
        assert contract['table_identity'] == 'use_captured_informatica_source_identity'
        assert contract['metadata_validation'] == 'not_performed_by_agent_review'
        assert 'as a blocker' in contract['design_guidance']


def test_single_visible_structured_completion_pass(tmp_path):
    routes = {}
    for role in ROLES:
        finding = {'role': role, 'summary': 'Unverified runtime contracts', 'evidence_ids': [],
                   'assumptions': [], 'blockers': [], 'proposed_changes': [], 'alternatives': [],
                   'status': 'needs_information'}
        routes[role] = chat([AIMessage(content='Initial analysis lacks structured handoff'),
                            tool_call('submit_finding', {'finding': finding}, role + '-completion'),
                            AIMessage(content='Typed completion recorded')])
    result = AgentRuntime(tmp_path, role_models=routes).run(model(), 'wf', 'original', 'Propose design', [], [])
    assert len(result['findings']) == 6 and result['redesigns'] == []
    assert all(r['completion_pass'] for r in result['role_completion'])
    assert result['status'] == 'proposal_failed'
    assert result['design_submission']['status'] == 'not_produced'
    assert not result['approved'] and not result['generation_allowed']
    assert all(any('structured_completion_pass' in str(m.content) for m in r._seen[-1])
               for r in routes.values())
    for role, route in routes.items():
        completion = next(json.loads(message.content) for message in route._seen[-1]
                          if isinstance(message.content, str) and message.content.startswith('{')
                          and 'structured_completion_pass' in message.content)
        assert isinstance(completion['instruction'], str) and completion['instruction']
        assert 'valid SHAPE only' in completion['schema_usage']
        if role in {'architecture', 'supervisor'}:
            assert completion['design_submission_schema'] == _design_submission_schema()
            shape = completion['design_submission_schema']
            assert shape['required'] == ['design'] and not shape['additionalProperties']
            assert set(shape['properties']['design']['properties']) == set(Design.model_fields)
            assert 'defaults' not in shape  # No populated reference design or semantic values.
        else:
            assert completion['design_submission_schema'] is None
        assert completion['finding_submission_schema'] == Finding.model_json_schema()
        envelope = next(json.loads(message.content) for message in route._seen[0]
                        if isinstance(message.content, str) and message.content.startswith('{')
                        and '"contract_version"' in message.content)
        instruction = envelope['instruction']
        assert 'call submit_finding' in instruction
        if role == 'architecture':
            assert 'submit_finding and submit_redesign' in instruction
            assert 'Informatica TASKTYPE strings' in instruction
            assert 'Every proposed edge must use proposed task IDs at both ends' in instruction
            assert 'artifact_plan is optional' in instruction
        elif role == 'supervisor':
            assert any('prior_design_rejections' in str(message.content) for message in route._seen[-1])
        else:
            assert 'do not submit or imply a redesign' in instruction


def test_schema_shape_and_field_feedback_repair_actual_architecture_submission(tmp_path):
    source = model()
    valid = design_for(source)
    invalid = deepcopy(valid)
    invalid['alternatives'] = ['Not an object']
    invalid['parameters'] = [{'name': 'catalog', 'purpose': 'Runtime binding',
                              'required': 'true', 'value_type': 'string'}]
    invalid['release_secret'] = 'DO_NOT_ECHO_REJECTED_VALUE'
    route = chat([
        tool_call('workflow_planning_contract', {'section': 'tasks'}, 'retrieve'),
        tool_call('submit_redesign', {'design': invalid}, 'bad-shape'),
        tool_call('submit_redesign', {'design': valid}, 'repaired'),
        AIMessage(content='Typed design submitted'),
    ])
    with build_agents(tmp_path, source, 'wf', 'original', chat_model=route) as bundle:
        messages = bundle.specialists['architecture'].invoke(
            {'messages': [{'role': 'user', 'content': 'Design from retrieved task evidence'}]},
            {'configurable': {'thread_id': 'schema-repair'}, 'recursion_limit': 32})['messages']
        results = [json.loads(message.content) for message in messages
                   if message.type == 'tool' and message.name == 'submit_redesign']
        assert len(results) == 2 and not results[0]['recorded'] and results[1]['recorded']
        issues = {issue['field']: issue['issue'] for issue in results[0]['validation_issues']}
        assert 'alternatives.0' in issues and 'parameters.0.required' in issues
        assert 'an object with the declared required fields' in issues['alternatives.0']
        assert 'a boolean (true/false, not quoted text)' in issues['parameters.0.required']
        assert 'Remove this undeclared field' in issues['release_secret']
        assert 'DO_NOT_ECHO_REJECTED_VALUE' not in json.dumps(results[0])
        assert len(bundle.redesigns) == 1
        assert bundle.redesigns[0]['design_origin'] == 'agent_submitted'
        assert not results[1]['approved'] and not results[1]['generation_allowed']
        assert not results[1]['deployment_allowed']
        actual_schema = bundle.tools['architecture']['submit_redesign'].args_schema.model_json_schema()
        assert _design_submission_schema()['properties'] == actual_schema['properties']


def test_pre_cancelled_agent_run_does_not_invoke_any_model(tmp_path):
    routes = {role: chat() for role in ROLES}
    cancellation = Event()
    cancellation.set()
    runtime = AgentRuntime(tmp_path, role_models=routes)
    result = runtime.run(model(), 'wf', 'bronze', 'Design a candidate', [], [],
                         cancellation_event=cancellation)
    assert result['status'] == 'cancelled'
    assert result['redesigns'] == []
    assert all(not route._seen for route in routes.values())


def test_provider_failure_not_retried_and_other_roles_still_observed(tmp_path, monkeypatch):
    from workbench.tests.test_redesign import ToolModel
    calls = []
    def fail(self, *args, **kwargs):
        calls.append(True)
        raise RuntimeError('private provider details')
    monkeypatch.setattr(ToolModel, '_generate', fail)
    result = AgentRuntime(tmp_path, role_models={r: chat() for r in ROLES}).run(
        model(), 'wf', 'original', 'Propose design', [], [])
    assert len(calls) == 6
    assert all(r['status'] == 'failed' and not r['completion_pass'] for r in result['role_completion'])
    assert 'private provider' not in json.dumps(result)


def test_normal_agent_runtime_cleanup_does_not_signal_user_cancellation(tmp_path):
    from workbench.tests.test_redesign import scripted_runtime
    runtime, _ = scripted_runtime(tmp_path, model(), 'wf')
    cancellation = Event()
    result = runtime.run(model(), 'wf', 'original', 'Redesign this workflow', [], [],
                         cancellation_event=cancellation)
    assert result['status'] == 'draft_available'
    assert not cancellation.is_set()


def test_normal_call_limit_has_one_reserved_completion_not_provider_retry(tmp_path):
    routes = {}
    for role in ROLES:
        finding = {'role': role, 'summary': 'Limited review, remaining evidence unverified',
                   'evidence_ids': [], 'status': 'needs_information'}
        routes[role] = chat([tool_call('package_summary', {}, f'{role}-{i}') for i in range(6)]
                           + [tool_call('submit_finding', {'finding': finding}, role + '-reserved'),
                              AIMessage(content='Done')])
    result = AgentRuntime(tmp_path, role_models=routes).run(model(), 'wf', 'original', 'Propose design', [], [])
    assert len(result['findings']) == 6
    assert all(r['normal_call_limit_reached'] and r['completion_pass'] for r in result['role_completion'])
    assert all(len(r._seen) == 8 for r in routes.values())
    assert not result['approved']


def test_invalid_citation_feedback_allows_actual_graph_to_correct(tmp_path):
    invalid = {'role': 'semantics', 'summary': 'Unverified', 'evidence_ids': ['invented'],
               'status': 'needs_information'}
    valid = {**invalid, 'evidence_ids': ['wf']}
    responses = [tool_call('submit_finding', {'finding': invalid}, 'bad'),
                 tool_call('lookup_evidence', {'evidence_id': 'wf'}, 'lookup'),
                 tool_call('submit_finding', {'finding': valid}, 'corrected'), AIMessage(content='Done')]
    route = chat(responses)
    with build_agents(tmp_path, model(), 'wf', 'original', chat_model=route) as bundle:
        result = bundle.specialists['semantics'].invoke(
            {'messages': [{'role': 'user', 'content': 'Submit only retrieved citations'}]},
            {'configurable': {'thread_id': 'repair'}, 'recursion_limit': 20})
        responses = [json.loads(m.content) for m in result['messages']
                     if m.type == 'tool' and m.name == 'submit_finding']
        rejected = [item for item in responses if item.get('recorded') is False]
        assert len(rejected) == 1 and rejected[0]['error'] == 'finding_evidence_not_retrieved'
        assert 'invented' not in json.dumps(rejected[0])
        assert len(bundle.findings) == 1 and bundle.findings[0]['evidence_ids'] == ['wf']
        assert not bundle.redesigns


def test_live_observed_malformed_designs_stay_rejected_with_actionable_contract(tmp_path):
    from workbench.agents import _DESIGN_GUIDE
    assert 'at least one OBJECT, not a string' in _DESIGN_GUIDE
    assert 'canonical ID returned by tasks' in _DESIGN_GUIDE
    assert 'Both edge endpoints must exactly equal id values in this design' in _DESIGN_GUIDE
    value = design_for()
    for alternatives in ([], ['keep separate']):
        with pytest.raises(ValueError):
            Design.model_validate({**value, 'alternatives': alternatives})
    malformed = deepcopy(value)
    malformed['edges'] = [{'source': 'Start', 'target': 'logical-load', 'evidence_ids': ['a']}]
    with pytest.raises(ValueError, match='Unknown proposed edge endpoint'):
        Design.model_validate(malformed)
    malformed_schema = deepcopy(value)
    malformed_schema['tasks'][0]['implementation_kind'] = 'invented_kind'
    malformed_schema['alternatives'] = []
    route = chat([tool_call('lookup_evidence', {'evidence_id': 'a'}, 'evidence-a'),
                  tool_call('lookup_evidence', {'evidence_id': 'b'}, 'evidence-b'),
                  tool_call('submit_redesign', {'design': malformed_schema}, 'bad-schema'),
                  tool_call('submit_redesign', {'design': malformed}, 'bad-edge'),
                  tool_call('submit_redesign', {'design': value}, 'corrected-edge'),
                  AIMessage(content='Corrected graph submitted')])
    with build_agents(tmp_path, model(), 'wf', 'original', chat_model=route) as bundle:
        response = bundle.specialists['architecture'].invoke(
            {'messages': [{'role': 'user', 'content': 'Propose a design'}]},
            {'configurable': {'thread_id': 'bad-edge'}, 'recursion_limit': 32})
        submissions = [json.loads(m.content) for m in response['messages']
                   if m.type == 'tool' and m.name == 'submit_redesign']
        rejected = [item for item in submissions if item.get('recorded') is False]
        assert len(rejected) == 2
        schema_error = next(item for item in rejected if item['error'] == 'design_schema_validation_failed')
        assert any(issue['field'] == 'tasks.0.implementation_kind' for issue in schema_error['validation_issues'])
        assert 'invented_kind' not in json.dumps(schema_error)
        edge_error = next(item for item in rejected if item['error'] == 'unknown_proposed_edge_endpoint')
        assert 'exact IDs from the tasks array' in edge_error['instruction']
        assert 'recorded=false is a rejected tool result' in _POLICY
        assert len(bundle.redesigns) == 1 and bundle.redesigns[0]['design']['edges'] == []


@pytest.fixture(scope='module')
def actual_xml():
    from workbench.understanding import analyze_package
    return analyze_package(Path(__file__).resolve().parents[2] / 'input' / 'LumenExample.XML')


@pytest.mark.parametrize('scenario', ['original', 'bronze'])
def test_wil_reference_tool_remains_optional_registered_and_evidence_checked(actual_xml, tmp_path, scenario):
    workflow = next(n for n in actual_xml['nodes']
                    if n['kind'] == 'workflow' and n['name'] == 'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS')
    direct = {n['id'] for n in actual_xml['nodes']
              if n['kind'] == 'taskinstance' and n.get('parent_id') == workflow['id']}
    with build_agents(tmp_path, actual_xml, workflow['id'], scenario, chat_model=chat()) as bundle:
        assert bundle.redesigns == []
        for role in ROLES:
            assert ('submit_wil_net_dis_outline' in bundle.tools[role]) == (role in {'architecture', 'supervisor'})
        for role in ('architecture', 'supervisor'):
            helper = bundle.tools[role]['submit_wil_net_dis_outline']
            assert 'Optional reference' in helper.description
            assert helper.args_schema.model_json_schema()['properties'] == {}
            receipt = helper.invoke({})
            assert receipt['recorded'] is True
            assert receipt['design_origin'] == 'deterministic_workflow_compiler'
            assert not receipt['approved'] and not receipt['generation_allowed'] and not receipt['deployment_allowed']
            submission = bundle.redesigns[-1]
            validated = validate_submission(submission['design'], actual_xml, workflow['id'],
                bundle.role_retrieved[role], bundle.evidence.records, scenario=scenario)
            assert len(validated.coverage) == len(direct)
            assert {row.original_task_id for row in validated.coverage} == direct
            assert submission['design_origin'] == 'deterministic_workflow_compiler'
            # Optional reference use does not bypass the ordinary semantic gate.
            invalid = validated.model_dump()
            invalid['cadence'] = None
            rejected = bundle.tools[role]['submit_redesign'].invoke({'design': invalid})
            assert rejected['recorded'] is False
            assert rejected['error'] == 'design_semantic_validation_failed'
            assert any(issue['field'] == 'cadence' for issue in rejected['validation_issues'])


def test_wil_normal_run_does_not_replace_missing_agent_output_with_reference(actual_xml, tmp_path):
    workflow = next(n for n in actual_xml['nodes']
                    if n['kind'] == 'workflow' and n['name'] == 'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS')
    routes = {}
    for role in ROLES:
        finding = {'role': role, 'summary': 'Source behavior remains unverified',
                   'evidence_ids': [], 'status': 'needs_information'}
        routes[role] = chat([tool_call('submit_finding', {'finding': finding}, role + '-finding'),
                            AIMessage(content='No typed design submitted')])
    output = AgentRuntime(tmp_path, role_models=routes).run(
        actual_xml, workflow['id'], 'original', 'Propose a Lakeflow design', [], [])
    assert output['status'] == 'proposal_failed'
    assert output['redesigns'] == []
    assert output['design_submission']['accepted_count'] == 0
    assert {item['role'] for item in output['findings']} == set(ROLES)
    assert not output['approved'] and not output['generation_allowed'] and not output['deployment_allowed']
    for role in ('architecture', 'supervisor'):
        system = str(routes[role]._seen[0][0].content)
        assert 'No fixed task count' in system
        assert 'ALL direct original workflow taskinstance IDs' in system
        assert 'Call submit_wil_net_dis_outline' not in system
        receipt = next(item for item in output['role_completion'] if item['role'] == role)
        assert receipt['completion_pass'] and 'submit_redesign' in receipt['missing_outputs']


@pytest.mark.parametrize('name', ['wkf_Load_EWH_CIRCUIT_INVENTORY_PRODUCT_GROUP',
                                 'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS'])
def test_actual_xml_contract_preserves_every_task_and_exact_expression(actual_xml, name):
    workflow = next(n for n in actual_xml['nodes'] if n['kind'] == 'workflow' and n['name'] == name)
    index = PlanningIndex(actual_xml)
    overview = index.agent_contract(workflow['id'])
    assert overview['english']['basis'] == 'canonical_xml_evidence'
    tasks = []
    offset = 0
    while True:
        page = index.agent_contract(workflow['id'], 'tasks', offset=offset)
        tasks.extend(page['items'])
        if not page['has_more']:
            break
        offset = page['next_offset']
    direct = {n['id'] for n in actual_xml['nodes'] if n['kind'] == 'taskinstance'
              and n.get('parent_id') == workflow['id']}
    assert set(overview['direct_task_ids']) == direct
    assert direct <= {t['id'] for t in tasks}
    nodes = index.nodes
    fields = index.agent_contract(workflow['id'], 'mapping_fields', limit=20)
    assert fields['total'] > 0
    for row in fields['items']:
        assert row['record'] == nodes[row['id']]['fields'][row['ordinal']]
    links = index.agent_contract(workflow['id'], 'control_links', limit=20)
    assert links['total'] > 0
    assert not overview['generation_allowed']


def run_live(route_file: Path, xml: Path, output_parent: Path, workflow_name: str):
    """Explicit single workflow, real tools/model, new isolated per-user ledger only."""
    from datetime import datetime, timezone
    from hashlib import sha256
    from workbench.databricks_models import WorkspaceModels, profile_identity
    from workbench.understanding import analyze_package

    identity = profile_identity()
    router = WorkspaceModels(route_file.parent)
    if route_file.name != 'model-routing.json' or not route_file.is_file() or not router.load(identity):
        raise ValueError('Existing explicit model routes for the current user are required')
    models: dict[str, BaseChatModel] = {r: m for r, m in router.chat_models(identity).items()}
    source = analyze_package(xml)
    workflows = [n for n in source['nodes'] if n['kind'] == 'workflow' and n['name'] == workflow_name]
    if len(workflows) != 1:
        raise ValueError('Workflow must resolve uniquely in the actual XML')
    workflow = workflows[0]
    scope = identity.host + '/' + identity.user_id
    owner_hash = sha256(scope.encode()).hexdigest()
    destination = output_parent / owner_hash / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    def write(name, value):
        (destination / name).write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding='utf-8')
    write('input-receipt.json', {'started_at': datetime.now(timezone.utc).isoformat(),
        'xml_sha256': source['package']['sha256'], 'workflow_id': workflow['id'],
        'workflow_name': workflow_name, 'owner_hash': owner_hash,
        'model_routes': {r: v['endpoint'] for r, v in router.load(identity).items()},
        'source': 'fresh_analyze_package_actual_XML', 'consent': True})
    planning = PlanningIndex(source)
    write('xml-english.json', planning.agent_contract(workflow['id']))
    runtime = AgentRuntime(destination / 'runtime', role_models=models, identity_scope=scope,
                           budget_seconds=600)
    result = runtime.run(source, workflow['id'], 'original',
        'Propose the optimal justified Lakeflow logical DAG for this actual XML workflow. '
        'Submit a real typed design, not only findings. Preserve cadence, sequencing, '
        'connections, parameter scope, exact mapping expressions and SQL phase order. '
        'Plan relative notebook/module artifacts with English descriptions, no code. '
        'Cover Start administratively without a Start notebook. No production data is '
        'required. Missing email settings are task-scoped peripheral notes. Do not approve, '
        'generate executable files, deploy or claim runtime parity. Compare alternatives.', [], [])
    write('runtime-result.json', result)
    evidence = _Evidence(source, workflow['id'], [], [])
    for page in result['neighbor_pages']:
        evidence.package_neighbors(**page)
    saved = []
    store = RedesignStore(destination / 'redesigns.sqlite')
    for number, submission in enumerate(result['redesigns']):
        validated = validate_submission(submission['design'], source, workflow['id'],
                                         submission['retrieved_evidence_ids'], evidence.records)
        write(f'agent-design-{number + 1}.json', validated.model_dump())
        saved.append(store.append(source['package']['sha256'], workflow['id'], 'original', submission,
            {'record_id': 'isolated-live-' + result['run_id'], 'runtime_run_id': result['run_id'],
             'submission_index': number, 'kind': 'agent', 'input_version': result['input_hashes']['model'],
             'runtime_input_hashes': result['input_hashes']}, number))
    write('design-receipts.json', saved)
    print(json.dumps({'output': str(destination), 'status': result['status'],
        'run_id': result['run_id'], 'design_count': len(saved), 'usage': result['usage'],
        'role_completion': result['role_completion'], 'ui_connected': False,
        'finished_at': datetime.now(timezone.utc).isoformat()}), flush=True)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Explicit consented single-workflow runtime demonstration')
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--route-file', type=Path, required=True)
    parser.add_argument('--xml', type=Path, required=True)
    parser.add_argument('--output-parent', type=Path, required=True)
    parser.add_argument('--workflow', default='wkf_Load_EWH_CIRCUIT_INVENTORY_PRODUCT_GROUP')
    args = parser.parse_args()
    run_live(args.route_file, args.xml, args.output_parent, args.workflow)