/* Source contracts only: no browser, network, application execution, or model calls. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const js = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function section(start, end) {
  const first = js.indexOf(start);
  const last = js.indexOf(end, first + start.length);
  assert.ok(first >= 0 && last > first, `${start} source boundaries`);
  return js.slice(first, last);
}

test('package readiness awaits a bounded Explorer page and exposes retry on failure', () => {
  const overview = section('  async function loadOverview(', '  async function ingest(');
  assert.match(overview, /state\.inventoryPreload = 'loading'/);
  assert.match(overview, /const loaded = await loadInventory\(\)/);
  assert.match(overview, /if \(ticket !== state\.overviewTicket\) return/);
  assert.match(overview, /state\.inventoryPreload = loaded \? 'ready' : 'failed'/);
  const inventory = section('  async function loadInventory(', '  async function selectObject(');
  assert.match(inventory, /limit: '50'/);
  assert.match(inventory, /ticket !== state\.inventoryTicket/);
  assert.match(inventory, /Retry|inventoryRetry/);
  const gate = section('  function navigationPrerequisite(', '  function navigate(');
  assert.match(gate, /state\.inventoryPreload !== 'ready'/);
  assert.match(gate, /state\.inventoryPreload === 'ready'/);
});

test('ready package starts a display-only history prefetch without awaiting it', () => {
  const overview = section('  async function loadOverview(', '  async function ingest(');
  assert.match(overview, /renderOverview\(data\)/);
  assert.match(overview, /\(changed \|\| becameReady\) && assessmentReady\(\)/);
  assert.match(overview, /\(initializingNavigation \|\| state\.page !== 'agents'\)/);
  assert.match(overview, /void readRuns\(0, true\)/);
  assert.ok(overview.indexOf('renderOverview(data)') < overview.indexOf('void readRuns(0, true)'));
});

test('prefetch cannot publish an idle verdict or select and poll an active run', () => {
  const source = section('  async function readRuns(', "  $('refresh-runs').addEventListener");
  assert.match(source, /if \(!prefetch\) \{ state\.agentRunsLoaded = false; updateControls\(\); \}/);
  const liveOnly = source.match(/if \(!prefetch\)\s*\{\s*recordedRunStates\.clear\(\);[\s\S]*?state\.runsRowsLoaded = true;/)?.[0];
  assert.ok(liveOnly, 'live-only status update boundaries');
  assert.match(liveOnly, /state\.agentRunning =/);
  assert.match(liveOnly, /state\.agentRunsLoaded = true/);
  assert.ok(source.indexOf('if (prefetch) return;') < source.indexOf('selectRun(activeRuns[0].id)'));
  assert.match(source, /Saved history is not a current active-status check/);
});

test('actual agent entry starts a fresh status read and locks invocation until confirmed', () => {
  const navigation = section('  function navigate(', '  function agentModelAvailable(');
  assert.match(navigation, /const entering = !initializingNavigation && main\.dataset\.currentPage !== page/);
  assert.match(navigation, /loadRuns\(0, entering\)/);
  const loader = section('  function loadRuns(', "  $('refresh-runs').addEventListener");
  assert.match(loader, /!freshEntry && runsPending\?\.epoch === state\.epoch && runsPending\.offset === offset/);
  assert.match(loader, /if \(!prefetch\) \{ state\.agentRunsLoaded = false; updateControls\(\); \}/);
  assert.match(loader, /run_status_verified === false/);
  assert.match(loader, /data\.truncated && typeof data\.active_run_present !== 'boolean'/);
  const controls = section('  function updateControls(', "  document.addEventListener('orion:external-model-state'");
  assert.match(controls, /state\.agentRunning \|\| !state\.agentRunsLoaded/);
  assert.match(js, /state\.agentRunning \|\| !state\.agentRunsLoaded\) \{ formStatus/);
});

test('history and documents reject stale scope and superseded responses without clearing visible rows first', () => {
  const history = section('  async function readRuns(', "  $('refresh-runs').addEventListener");
  assert.match(history, /const epoch = state\.epoch, ticket = \+\+state\.runsListTicket/);
  assert.match(history, /epoch !== state\.epoch \|\| ticket !== state\.runsListTicket/);
  assert.ok(history.indexOf('await api(') < history.indexOf("clear($('runs-list'))"));
  assert.match(history, /if \(!state\.runsRowsLoaded\) message/);
  const docs = section('  async function loadDocuments(', "  $('refresh-documents').addEventListener");
  assert.match(docs, /epoch !== state\.epoch \|\| ticket !== state\.documentsTicket/);
  assert.match(docs, /if \(!state\.documentsLoaded\) message/);
  assert.ok(docs.indexOf('await api(') < docs.indexOf("clear($('documents-list'))"));
  const invalidation = section('  function invalidatePackage(', '  async function loadOverview(');
  assert.match(invalidation, /state\.runsRowsLoaded = false; state\.runsListTicket \+= 1/);
  assert.match(invalidation, /state\.documentsLoaded = false; state\.documentsLoading = false; state\.documentsTicket \+= 1/);
});

test('navigation reuses loaded views, while explicit refreshes and mutations still read', () => {
  const pages = section('  function loadPage(', '  function agentModelAvailable(');
  for (const token of ['!state.projectsLoaded && !state.projectsLoading',
    '!state.inputsLoaded && !state.inputsLoading', '!state.project && !state.projectLoading',
    '!state.documentsLoaded && !state.documentsLoading']) assert.ok(pages.includes(token), token);
  assert.match(pages, /force \|\| entering \|\| !state\.agentRunsLoaded/);
  assert.match(js, /\$\('refresh-projects'\)\.addEventListener\('click', \(\) => \{ loadProjects\(\); if \(state\.projectId\) loadProject\(\); \}\)/);
  assert.match(js, /\$\('refresh-inputs'\)\.addEventListener\('click', loadInputs\)/);
  assert.match(js, /\$\('refresh-documents'\)\.addEventListener\('click', loadDocuments\)/);
  assert.match(js, /form\.reset\(\); await loadDocuments\(\)/);
  assert.match(js, /form\.reset\(\); await loadProject\(\)/);
  assert.match(js, /loadPage\(state\.page, state\.page !== 'explorer'\)/);
  assert.match(js, /if \(page === 'explorer' && \(force \|\| !state\.inventoryLoaded\)\) loadInventory\(\)/);
});

test('explicit ready project selection opens Estate without redirecting startup', () => {
  const project = section('  async function switchProject(', '  async function loadProjects(');
  assert.match(project, /!initializingNavigation && id && state\.projectId === id && state\.page === 'projects' && selectedCompletedPackage\(\)/);
  assert.match(project, /navigate\('estate', true\)/);
});

test('hosted package warm-up is serialized and failure remains locked until explicit retry', () => {
  const warm = section('  async function warmHostedPackage(', '  async function loadOverview(');
  assert.match(warm, /api\('\/api\/package\/warm'\)/);
  assert.match(warm, /result\.cache_ready === true \? 'ready' : 'unavailable'/);
  assert.match(warm, /!baseAssessmentReady\(\)/);
  assert.match(warm, /\['loading', 'ready', 'unavailable'\]\.includes\(state\.runtimeWarmStatus\)/);
  assert.match(warm, /state\.runtimeWarmStatus !== 'unavailable'/);
  assert.match(warm, /warmRetry\.addEventListener\('click', retryHostedPackageWarm\)/);
  assert.match(warm, /agent-runtime-cache-status/);
  assert.match(js, /pathname === '\/api\/package\/warm'/);
  const controls = section('    const runtimeWarmBlocked =', "    $('agent-submit').textContent");
  assert.match(controls, /!hostedWarmReady\(\)/);
  assert.match(controls, /\|\| runtimeWarmBlocked/);
  const gate = section('  function navigationPrerequisite(', '  function navigate(');
  assert.match(gate, /!hostedWarmReady\(\)/);
  assert.match(gate, /state\.runtimeWarmKey === `\$\{state\.epoch\}:\$\{state\.projectId\}:\$\{state\.memberHash\}`/);
  assert.match(js, /loadingWarm \? foreground\.find\(\(item\) => item\.warm && item\.operation\)/);
  assert.match(js, /const activeRead = foreground\.find\(\(item\) => item\.operation\)/);
  assert.match(js, /descriptor\.warm && state\.runtimeWarmStatus === 'loading'/);
});

test('hosted startup gates on scoped readiness without gating on mutable history', () => {
  const preload = section('  async function preloadHostedReadiness(', '  async function warmHostedPackage(');
  assert.match(preload, /state\.readinessPreload = 'loading'/);
  assert.ok(preload.indexOf("state.readinessPreload = 'loading'") < preload.indexOf('await '));
  assert.match(preload, /document\.readyState === 'loading'/);
  assert.match(preload, /DOMContentLoaded/);
  assert.match(preload, /typeof window\.orionReadiness\?\.preload !== 'function'/);
  assert.match(preload, /await window\.orionReadiness\.preload\(scope\)/);
  assert.match(preload, /ticket === state\.readinessTicket && selectionEpoch === state\.epoch/);
  assert.match(preload, /result\?\.status !== 'ready'/);
  assert.match(preload, /result\.scope\?\.scenario !== scope\.scenario/);
  assert.match(preload, /state\.readinessPreload = 'failed'/);
  assert.match(preload, /readinessRetry\.addEventListener\('click'/);
  const gate = section('  function navigationPrerequisite(', '  function navigate(');
  assert.match(gate, /state\.readinessPreload !== 'ready'/);
  assert.doesNotMatch(gate, /getStatus|agentRunsLoaded|recordedRunStates/);
  const warm = section('  async function warmHostedPackage(', '  async function loadOverview(');
  assert.ok(warm.indexOf('startHostedReadiness()') < warm.indexOf('updateControls();\n    }).catch'));
  assert.match(js, /Step \$\{step\} of 3/);
});

test('Invoke stays disabled while the hosted operation lock survives page navigation', () => {
  const controls = section('  function updateControls()', '  document.addEventListener(\'orion:external-model-state\'');
  assert.match(controls, /window\.orionHostedIntake\?\.hasActiveOperation\(\)/);
  assert.match(controls, /\|\| hostedOperationBusy/);
  assert.match(js, /orion:hosted-operation-state', updateControls/);
});