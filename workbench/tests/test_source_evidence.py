"""Offline source-understanding regressions. No providers, Jobs or SQL execution."""
from copy import deepcopy
from pathlib import Path
import json
from xml.sax.saxutils import quoteattr

import pytest

from workbench.agents import build_agents
from workbench.migration_planning import PlanningIndex
from workbench.source_evidence import embedded_java, workflow_invocations
from workbench.spark_codegen import _Evidence as WriterEvidence
from workbench.sql_artifacts import compile_sql_artifacts
from workbench.tests.test_redesign import chat
from workbench.understanding import analyze_package, _INLINE_SECRET


JAVA = '''String connString = "jdbc:oracle:thin:@private-host:1521:db";
String password =
    "private-secret";
String sqlStr = "select count(1) from " + SCHEMA + ".dependency where template_name = '" + TEMPLATE_NAME + "'";
int total = 0, timeoutcnt = 0;
do {
    rs = stmt.executeQuery(sqlStr);
    while (rs.next()) { total = rs.getInt(1); }
    if (total > 0) {
        Thread.sleep(300000);
        timeoutcnt++;
        if (timeoutcnt == MAX_WAIT_COUNT) { break; }
    }
} while (total > 0);
out_VALUE = total;
'''


@pytest.fixture
def package(tmp_path):
    command = 'pmcmd startworkflow -sv service -d domain -uv USER_VARIABLE -pv PASSWORD_VARIABLE -f F -nowait Child'
    xml = f'''<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">
    <MAPPING NAME="Check"><TRANSFORMATION NAME="Java" TYPE="Custom Transformation">
      <METADATAEXTENSION DOMAINNAME="Java_Transform_Domain" NAME="OnInputRow_Method_Snippet" VALUE={quoteattr(JAVA)}/>
    </TRANSFORMATION></MAPPING>
    <SESSION NAME="CheckSession" MAPPINGNAME="Check"/>
    <WORKFLOW NAME="Child"><TASKINSTANCE NAME="check" TASKNAME="CheckSession" TASKTYPE="Session"/></WORKFLOW>
    <WORKFLOW NAME="Parent"><TASK NAME="call" TYPE="Command"><VALUEPAIR NAME="command" VALUE={quoteattr(command)}/></TASK>
      <TASKINSTANCE NAME="call" TASKNAME="call" TASKTYPE="Command"/></WORKFLOW>
    <WORKFLOW NAME="Unrelated"/>
    </FOLDER><FOLDER NAME="Other"><WORKFLOW NAME="Child"/></FOLDER>
    </REPOSITORY><REPOSITORY NAME="OtherRepository"><FOLDER NAME="F"><WORKFLOW NAME="Child"/></FOLDER></REPOSITORY></POWERMART>'''
    path = tmp_path / 'fixture.xml'
    path.write_text(xml, encoding='utf-8')
    model = analyze_package(path)
    ids = {n['name']: n['id'] for n in model['nodes']
           if n.get('scope', {}).get('repository') == 'R' and n.get('scope', {}).get('folder') == 'F'}
    return model, ids


def test_java_sidecar_preserves_controls_not_secrets_or_executable_sql(package):
    model, ids = package
    prop = next(n for n in model['nodes'] if n['id'] == ids['Java'])['properties'][0]
    assert prop['attributes']['VALUE'] == '[REDACTED]'
    code = prop['embedded_code']
    assert code['structural_source'].count('\n') == JAVA.count('\n')
    for text in ('do {', 'while (total > 0)', 'Thread.sleep(300000)', 'timeoutcnt == MAX_WAIT_COUNT', 'out_VALUE = total'):
        assert text in code['structural_source']
    assert len(code['sql_expressions']) == 1
    query = code['sql_expressions'][0]
    assert 'SCHEMA' in query['expression'] and 'TEMPLATE_NAME' in query['expression']
    assert not query['translation_allowed'] and not code['executable']
    encoded = json.dumps(model)
    assert not _INLINE_SECRET.search(encoded)
    for secret in ('private-host', 'private-secret', 'jdbc:oracle'):
        assert secret not in encoded
    report = PlanningIndex(model).agent_contract(ids['Child'], 'embedded_code')
    assert report['total'] == 1 and report['items'][0]['record']['embedded_code'] == code
    behavior = PlanningIndex(model).agent_contract(ids['Child'], 'behavior')
    assert behavior['total'] == 1
    assert PlanningIndex(model).agent_contract(ids['Child'], 'embedded_code', text_limit=10)['text_truncations']


@pytest.mark.parametrize('source', [
    'String s = """private-secret""";', 'String s = "unterminated private-secret;',
    '/* unterminated private-secret', r'String s = "\u0070rivate-secret";',
])
def test_unsupported_java_forms_fail_closed(source):
    code = embedded_java(source)
    assert code['status'] == 'unsupported_java_lexical_form'
    assert code['structural_source'] == '[REDACTED]' and code['sql_expressions'] == []


def test_comments_literals_and_multiline_numeric_credentials_are_not_exposed():
    code = embedded_java('/* private-secret */\nString s = "private-secret";\nint password =\n987654;\n'
                         '// sqlStr = "select secret from hidden";\nwhile (total > 0) { break; }')
    assert 'private-secret' not in json.dumps(code) and '987654' not in json.dumps(code)
    assert code['sql_expressions'] == []
    assert 'while (total > 0)' in code['structural_source']


def test_invocation_scope_async_semantics_and_ambiguity(package):
    model, ids = package
    before = deepcopy(model)
    rows = workflow_invocations(model)
    assert len(rows) == 1
    row = rows[0]
    assert row['candidate_workflow_ids'] == [ids['Child']]
    assert row['caller_workflow_id'] == ids['Parent'] and row['wait_mode'] == 'nowait'
    assert not row['control_edge_created'] and not row['runtime_verified']
    assert row['resolution'] == 'scoped_literal_candidate'
    assert model == before
    assert 'PASSWORD_VARIABLE' not in json.dumps(rows)
    assert PlanningIndex(model).agent_contract(ids['Child'], 'invocations')['items'] == rows
    duplicate = deepcopy(next(n for n in model['nodes'] if n['id'] == ids['Child']))
    duplicate['id'] = 'another-scoped-child'
    model['nodes'].append(duplicate)
    assert workflow_invocations(model)[0]['resolution'] == 'ambiguous'


@pytest.mark.parametrize('command', [
    'pmcmd startworkflow -f $$FOLDER -nowait Child',
    'pmcmd startworkflow -f F -nowait Child; echo hidden',
    'pmcmd startworkflow -f F -nowait -wait Child',
    'pmcmd startworkflow -f F -unknown value Child',
])
def test_unresolved_command_does_not_guess_or_expose_raw_text(package, command):
    model, ids = package
    owner = next(n for n in model['nodes'] if n['name'] == 'call' and n['kind'] == 'task')
    owner['properties'][0]['attributes']['VALUE'] = command
    row = workflow_invocations(model)[0]
    assert row['candidate_workflow_ids'] == []
    assert row['resolution'] == 'unresolved_or_unsupported'
    assert 'workflow_name' not in row


def test_agents_explicitly_retrieve_child_java_with_role_local_access(package, tmp_path):
    model, ids = package
    with build_agents(tmp_path, model, ids['Parent'], 'original', chat_model=chat()) as bundle:
        semantics = bundle.tools['semantics']['workflow_planning_contract']
        payload = {'section': 'embedded_code', 'related_workflow_id': ids['Child']}
        assert semantics.invoke(payload)['status'] == 'related_workflow_not_retrieved'
        assert semantics.invoke({'section': 'invocations'})['total'] == 1
        report = semantics.invoke(payload)
        assert report['items'][0]['record']['embedded_code']['sql_expressions']
        assert ids['Java'] in bundle.role_retrieved['semantics']
        assert bundle.tools['architecture']['workflow_planning_contract'].invoke(payload)['status'] == 'related_workflow_not_retrieved'
        assert semantics.invoke({'related_workflow_id': ids['Unrelated']})['status'] == 'related_workflow_not_retrieved'


def test_sql_draft_blocks_embedded_java_and_writer_reads_supplemental_evidence(package):
    model, ids = package
    compiled = compile_sql_artifacts(model, ids['Child'], source_dialect='oracle')
    assert any(b['code'] == 'EMBEDDED_JAVA_REQUIRES_REVIEW' and b['node_id'] == ids['Java']
               for b in compiled.manifest['blockers'])
    assert not any('select count' in item.original_sql.lower() for item in compiled.sql)
    record = {'workflow_id': ids['Child'], 'design': {'tasks': [{'id': 'check'}],
              'coverage': [{'original_task_id': ids['check'], 'proposed_task_ids': ['check']}]}}
    evidence = WriterEvidence(model, record)
    offset, found = 0, False
    while True:
        page = evidence.page('check', offset)
        assert not page['incomplete']
        found |= any('embedded_code' in row['value'] for row in page['items'])
        if page['next_offset'] is None:
            break
        offset = page['next_offset']
    assert found


def test_actual_lumen_dependency_mapping_and_callers():
    """Fresh local parse, no mutation/re-ingestion of saved package artifacts."""
    path = Path(__file__).resolve().parents[2] / 'input' / 'LumenExample.XML'
    model = analyze_package(path)
    nodes = {n['id']: n for n in model['nodes']}
    child = next(n for n in model['nodes'] if n['kind'] == 'workflow'
                 and n['name'] == 'wkf_Load_AE2E_WILTEL_NETWORK_DISCO')
    rows = PlanningIndex(model).agent_contract(child['id'], 'invocations')['items']
    assert {nodes[r['caller_workflow_id']]['name'] for r in rows} == {
        'wkf_AE2E_DUMMY_CALL_WIL_NET_DIS', 'wkf_LOAD_AE2E_MASTER_PREP'}
    assert all(r['wait_mode'] == 'nowait' for r in rows)
    code = PlanningIndex(model).agent_contract(child['id'], 'embedded_code', limit=20)
    row = next(r for r in code['items'] if r['owner_name'] == 'jtx_Check_Cntrl_Table'
               and r['record']['attributes']['NAME'] == 'OnInputRow_Method_Snippet')
    embedded = row['record']['embedded_code']
    assert 'timeoutcnt == MAX_WAIT_COUNT' in embedded['structural_source']
    assert 'Thread.sleep(300000)' in embedded['structural_source']
    assert 'ae2e_template_job_dependency' in embedded['sql_expressions'][0]['expression']
    assert 'minus select template_id' in embedded['sql_expressions'][0]['expression']
    assert not _INLINE_SECRET.search(json.dumps(embedded))