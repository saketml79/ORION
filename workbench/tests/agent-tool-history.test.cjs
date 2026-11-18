/* Nonbrowser unit/source contracts only. No DOM, network, timers or model calls. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const js = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../static/app.css'), 'utf8');
const section = (start, end) => {
  const first = js.indexOf(`  function ${start}(`);
  const last = js.indexOf(`  function ${end}(`, first + 1);
  assert.ok(first >= 0 && last > first, `${start} source boundaries`);
  return js.slice(first, last);
};
// Execute only the pure receipt selector, never the application or a browser harness.
const select = vm.runInNewContext(`(${section('agentToolObservation', 'renderAgentToolTurns').trim()})`);
const historical = () => ({status: 'verified', public_run_id: 'public-A', run_id: 'runtime-A',
  mode: 'historical_not_live', turns: [{turn: 1}], usage: {input_tokens: 17}});
const ledger = () => ({status: 'verified', public_run_id: 'public-A', run_id: 'runtime-A',
  tool_activity_status: 'recorded', tool_turns: [], usage: {input_tokens: 999}});

test('historical tool readback requires both exact verified run pins and does not mutate either receipt', () => {
  const receipt = historical();
  const current = ledger();
  const run = {id: 'public-A', current_usage_receipt: current};
  const before = JSON.stringify({receipt, current, run});
  const result = select(run, receipt);
  assert.equal(result.receipt, current);
  assert.equal(result.ledgerReadback, true);
  assert.equal(JSON.stringify({receipt, current, run}), before);
  assert.equal(receipt.usage.input_tokens, 17);
  assert.equal(Object.hasOwn(receipt, 'tool_turns'), false);
});

test('readback fails closed for missing, unverified, live or mismatched identities', () => {
  const cases = [
    [null, ledger()],
    [{...historical(), status: 'unverified'}, ledger()],
    [{...historical(), public_run_id: 'public-B'}, ledger()],
    [{...historical(), run_id: ''}, {...ledger(), run_id: ''}],
    [{...historical(), run_id: undefined}, {...ledger(), run_id: undefined}],
    [{...historical(), mode: 'live'}, ledger()],
    [historical(), null],
    [historical(), {...ledger(), status: 'unverified'}],
    [historical(), {...ledger(), public_run_id: 'public-B'}],
    [historical(), {...ledger(), run_id: 'runtime-B'}],
    [historical(), {...ledger(), run_id: undefined}],
  ];
  for (const [receipt, current] of cases)
    assert.equal(select({id: 'public-A', current_usage_receipt: current}, receipt), null);
});

test('recorded or unavailable tool fields already in selected observation are never replaced', () => {
  for (const fields of [
    {tool_activity_status: 'recorded', tool_turns: []},
    {tool_activity_status: 'unavailable', tool_turns: []},
    {tool_turns: null},
  ]) {
    const receipt = {...historical(), ...fields};
    const result = select({id: 'public-A', current_usage_receipt: ledger()}, receipt);
    assert.equal(result.receipt, receipt);
    assert.equal(result.ledgerReadback, false);
  }
});

test('tool display uses actual backend fields, exact duration and separate explicit statuses', () => {
  const source = section('renderAgentToolTurns', 'renderAgentSkills');
  assert.match(source, /tool_activity_status === 'recorded' && Array\.isArray\(tools\.tool_turns\)/);
  assert.match(source, /\['completed', 'failed', 'rejected', 'started'\]/);
  assert.match(source, /semanticStatus\(status\)/);
  assert.match(source, /status === 'rejected'.*classList\.replace\('failed', 'caution'\)/);
  assert.match(source, /typeof turn\.duration_ms === 'number' && Number\.isFinite\(turn\.duration_ms\) && turn\.duration_ms >= 0/);
  assert.match(source, /Duration: \$\{duration\} ms/);
  assert.doesNotMatch(source, /toFixed|Date\.now|Date\.parse|duration_ms\s*\|\|/);
  assert.match(source, /turn\.retry_status === 'not_recorded'/);
  assert.match(source, /Retry status: not_recorded \(retries unknown\)/);
  assert.match(source, /Retry status: unknown/);
  assert.match(source, /Separate ledger readback/);
  assert.match(source, /not part of the immutable historical snapshot/);
  assert.match(source, /Tool-call list unavailable from this receipt or older backend/);
  assert.match(source, /No tool calls recorded in this verified receipt/);
});

test('tool rendering never exposes raw arguments, payloads, errors or inferred retry counts', () => {
  const source = section('renderAgentToolTurns', 'renderAgentSkills');
  assert.doesNotMatch(source, /innerHTML|JSON\.stringify|jsonDetails|turn\.(?:args|arguments|input|output|error|payload|result|recorded_retry_count)\b/);
  assert.doesNotMatch(source, /fetch\(|api\(|setInterval|setTimeout/);
  assert.match(source, /\^\[A-Za-z0-9\]\[A-Za-z0-9_-\]\{0,99\}\$/);
  assert.match(source, /status !== 'started' && status !== 'Status unknown'/);
});

test('model history retains all turns, timestamps, tokens, duration bars and live clock dataset', () => {
  const source = section('renderAgentUsageCharts', 'agentToolObservation');
  for (const token of ['for (const turn of receipt.turns)', 'roleLabel(turn.role)',
    'turn.endpoint', 'activityTime(turn.started_at)', 'turn.failed_at ?? turn.completed_at',
    'elapsed.dataset.agentTurnStart = turn.started_at', 'duration / 1000',
    'activityBar([duration], durationMaximum', 'actualCounter(turn[key])',
    'disclosure.open = previous ? previous.open : true',
    'turns.scrollTop = previous?.scrollTop ?? 0']) assert.ok(source.includes(token), token);
  assert.match(source, /history\.append\(disclosure, renderAgentToolTurns\(run, receipt, previous\)\)/);
  assert.doesNotMatch(source, /receipt\.turns\.(?:slice|filter|sort)\(/);
});

test('independent model and tool disclosure/scroll state is captured before detail clearing', () => {
  const start = js.indexOf("const oldCharts = $('run-detail').querySelector('.agent-usage-charts')");
  const end = js.indexOf("clear($('run-detail'))", start);
  assert.ok(start >= 0 && end > start);
  const capture = js.slice(start, end);
  assert.match(capture, /oldCharts\?\.dataset\.runId === run\.id/);
  for (const selector of ['.agent-model-turns', '.agent-turns', '.agent-tool-turns', '.agent-tool-turn-list'])
    assert.ok(capture.includes(`querySelector('${selector}')`), selector);
  const tools = section('renderAgentToolTurns', 'renderAgentSkills');
  assert.match(tools, /disclosure\.open = previous \? previous\.toolOpen \?\? true : true/);
  assert.match(tools, /turns\.scrollTop = previous\?\.toolScrollTop \?\? 0/);
});

test('run detail has equal desktop columns and mobile stacking with independent bounded scroll', () => {
  assert.match(css, /#run-detail \.agent-call-history \{[^}]*display: grid;[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /@media \(max-width: 760px\) \{ #run-detail \.agent-call-history \{ grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(css, /#run-detail \.agent-tool-turn-list \{ max-height: 480px; overflow: auto; overscroll-behavior: contain/);
  assert.match(css, /\.agent-turns \{ max-height: 480px; overflow: auto/);
});

test('candidate writer gets separate status badges after rows and known manifest label is human-readable', () => {
  const start = js.indexOf("const writerRow = [...runSummary.querySelectorAll('.detail-row')]");
  const end = js.indexOf("$('run-detail').append(runSummary)", start);
  assert.ok(start >= 0 && end > start);
  const source = js.slice(start, end);
  assert.match(source, /textContent === 'CANDIDATE WRITER'/);
  assert.match(source, /semanticStatus\(run\.candidate_writer\.status\)/);
  assert.match(source, /semanticStatus\(run\.candidate_writer\.manifest_status\)/);
  assert.match(source, /document\.createTextNode\(' · '\)/);
  assert.doesNotMatch(source, /heading\(/);
  assert.match(section('semanticStatus', 'recordHeader'), /key === 'generation_complete' \? 'Generation complete'/);
});