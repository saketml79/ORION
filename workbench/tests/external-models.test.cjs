/* Nonbrowser unit and source-contract tests. No DOM, HTTP, credentials or model calls. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const {availability, blocksDatabricks, canChange, validStatus, providerName, validModel} = require('../static/external-models.js');
const db = {mode: 'databricks', configured: false, inference_ready: false, status: 'default'};
const external = {mode: 'external', provider: 'anthropic', model: 'claude-test', configured: true,
  inference_ready: true, status: 'configured', expires_in_seconds: 3600};
const state = changes => ({desired: 'databricks', known: true, busy: false, scopeReady: true,
  status: db, dbReady: null, expiresAt: 2000, ...changes});

test('default Databricks retains existing six-role availability, not an external override', () => {
  assert.equal(availability(state(), 1000), null);
  assert.equal(blocksDatabricks(state()), false);
  assert.equal(availability(state({dbReady: false}), 1000), false);
  assert.equal(availability(state({dbReady: true}), 1000), true);
});
test('choosing external is not connecting and cannot use a saved Databricks route', () => {
  assert.equal(availability(state({desired: 'external'}), 1000), false);
  assert.equal(blocksDatabricks(state({desired: 'external'})), true);
});
for (const provider of ['anthropic', 'openai']) {
  test(`${provider}: configured means ready for explicit invocation only until expiry`, () => {
    const selected = state({desired: 'external', status: {...external, provider}});
    assert.equal(validStatus(selected.status), true);
    assert.equal(availability(selected, 1999), true);
    assert.equal(availability(selected, 2000), false);
    assert.equal(availability({...selected, busy: true}, 1000), false);
    assert.equal(availability({...selected, known: false}, 1000), false);
    assert.equal(availability({...selected, scopeReady: false}, 1000), false);
    assert.equal(blocksDatabricks(selected), true);
  });
}
test('expired and lost process sessions stay external, with explicit disconnect available', () => {
  for (const expired of [
    {...external, status: 'expired', configured: false, inference_ready: false, expires_in_seconds: 0},
    {mode: 'external', status: 'expired', configured: false, inference_ready: false,
      provider: null, model: null, expires_in_seconds: null}
  ]) {
    assert.equal(validStatus(expired), true);
    const selected = state({desired: 'external', status: expired});
    assert.equal(availability(selected, 1000), false);
    assert.equal(blocksDatabricks(selected), true);
    assert.equal(canChange(selected, true, false), true);
    assert.equal(availability({...selected, desired: 'databricks'}, 1000), false);
  }
});
test('every readiness, active-run, save and in-flight guard fails closed for changes', () => {
  assert.equal(canChange(state(), true, false), true);
  for (const [changes, idle, saving] of [
    [{scopeReady: false}, true, false], [{known: false}, true, false], [{busy: true}, true, false],
    [{}, false, false], [{}, true, true]
  ]) assert.equal(canChange(state(changes), idle, saving), false);
  assert.equal(availability(state({known: false}), 1000), false);
  assert.equal(blocksDatabricks(state({known: false})), true);
  assert.equal(blocksDatabricks(state({busy: true})), true);
});
test('status contract rejects malformed, contradictory and invented providers/models', () => {
  assert.equal(validStatus(db), true);
  for (const bad of [null, {}, {...external, mode: 'codex'}, {...external, provider: 'codex'},
    {...external, model: '<script>'}, {...external, model: 'https://other.invalid'},
    {...external, status: 'expired'}, {...external, inference_ready: false},
    {...external, expires_in_seconds: -1}, {...external, expires_in_seconds: '3600'},
    {...db, configured: true}, {...db, inference_ready: true}]) assert.equal(validStatus(bad), false);
  assert.equal(providerName('anthropic'), 'Claude (Anthropic)');
  assert.equal(providerName('openai'), 'OpenAI');
});
test('model is explicit, bounded and never a URL, key, blank or whitespace', () => {
  for (const bad of ['', ' ', 'claude test', 'https://other.invalid', 'sk-fabricated', 'dapi-fabricated', 'a'.repeat(129), null])
    assert.equal(validModel(bad), false);
  for (const good of ['claude-test', 'gpt-test', 'model_version.1', 'a'.repeat(128)]) assert.equal(validModel(good), true);
});
test('Step 1 has shared intro, Databricks left and external right, preserving assets and IDs', () => {
  const html = read('static/index.html');
  for (const asset of ['external-models.js', 'external-models.css']) assert.equal(html.split(`/static/${asset}`).length - 1, 1);
  const intro = html.slice(html.indexOf('<div class="workspace-model-copy">'), html.indexOf('<div id="workspace-model-controls"'));
  assert.doesNotMatch(intro, /id="external-models-mount"/);
  assert.ok(html.indexOf('<div id="workspace-model-controls"') < html.indexOf('<div id="external-models-mount"'));
  assert.equal(html.split('id="external-models-mount"').length - 1, 1);
  assert.ok(html.indexOf('id="workspace-provider-left"') < html.indexOf('id="model-provider-mode-mount"'));
  assert.ok(html.indexOf('id="model-provider-mode-mount"') < html.indexOf('id="workspace-model-controls"'));
  const providerUI = read('static/external-models.js');
  assert.ok(providerUI.includes("$('model-provider-mode-mount').append(modeLabel, mode)"));
  const css = read('static/external-models.css');
  assert.ok(css.includes('grid-template-columns: repeat(2, minmax(0, 1fr))'));
  assert.ok(css.includes('@media (max-width: 760px)'));
  for (const id of ['workspace-model-panel', 'connect-workspace-models', 'agent-endpoint', 'endpoint-tool-support', 'workspace-model-status'])
    assert.equal(html.split(`id="${id}"`).length - 1, 1);
});
test('external API setup stays visible but disabled outside editable External mode', () => {
  const css = read('static/external-models.css');
  const js = read('static/external-models.js');
  assert.match(css, /:is\(#workspace-provider-left, #external-models-mount\) \{ align-self: stretch;/);
  assert.match(css, /grid-template-rows: auto repeat\(2, 1fr\) auto/);
  assert.match(js, /fields\.className = 'external-fields'; fields\.disabled = true/);
  assert.match(js, /fields\.disabled = state\.desired !== 'external' \|\| !editable/);
  assert.match(js, /model\.required = secret\.required = state\.desired === 'external'/);
  assert.doesNotMatch(js, /fields\.hidden\s*=/);
  assert.doesNotMatch(css, /external-fields.*visibility:\s*hidden/);
  assert.match(css, /#external-models-mount input \{ background: #fff !important; color: #172033; border: 0 !important;/);
  assert.match(css, /#workspace-model-panel select \{ background: #fff !important; color: #15203a; border: 0 !important;/);
  assert.match(css, /:root\[data-theme="dark"\] #workspace-model-panel select \{ background: #344665 !important; color: #f4f6ff;/);
  assert.match(css, /\.external-fields \{[^}]*max-height: none; overflow: visible;/);
  assert.match(js, /fields.disabled = state\.desired !== 'external' \|\| !editable/);
  assert.match(js, /model\.required = secret\.required = state\.desired === 'external'/);
  assert.doesNotMatch(js, /fields\.hidden\s*=/);
  assert.match(css, /\.external-status > span \{ grid-area: 1 \/ 1;/);
  assert.match(js, /sizingText.setAttribute\('aria-hidden', 'true'\)/);
});
test('credential request stays direct and scoped, never envelopes, storage, URLs or diagnostics', () => {
  const js = read('static/external-models.js');
  assert.match(js, /nativeFetch = window.fetch.bind\(window\)/);
  assert.match(js, /nativeFetch\(`\/api\/models\/external\/\$\{action\}`/);
  for (const header of ['X-Workbench-Request', 'X-Workbench-Project', 'X-Workbench-Package']) assert.ok(js.includes(header));
  assert.match(js, /credentials: 'same-origin', cache: 'no-store', redirect: 'error'/);
  assert.match(js, /secret.type = 'password'/);
  assert.match(js, /secret.autocomplete = 'new-password'/);
  assert.match(js, /const body = \{provider: provider.value, model: model.value, api_key: secret.value\};\s*secret.value = ''; operate\('connect', body\)/);
  assert.match(js, /if \(body\) body.api_key = ''/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB|console\.|innerHTML|orionHostedIntake|orionScopedReads|URLSearchParams/);
  assert.doesNotMatch(js, /response\.text\(|data\.detail|secret\.name\s*=/);
  assert.match(js, /ticket !== scopeKey\(\) \|\| generation !== revision/);
  assert.match(js, /revision \+= 1; clearTimeout\(expiryTimer\); secret.value = ''; model.value = ''/);
  assert.match(js, /state.dbReady = false/);
  assert.match(js, /nativeFetch\('\/api\/models\/status'/);
  assert.match(js, /publish\(restoredDatabricks\)/);
  assert.match(js, /pagehide.*secret.value = ''/);
  assert.match(js, /subscriptions include no API credits/);
  assert.match(js, /Responses-only Codex models are not supported/);
  assert.match(js, /Hosted access and Unity Catalog require Databricks sign-in/);
});
test('Invoke and cohort use provider readiness in addition to original package and run guards', () => {
  const app = read('static/app.js');
  assert.match(app, /function agentModelAvailable\(\) \{\s*return window.orionExternalModels\?\.availability\(\) \?\? \(state.overview\?\.agent\?\.available === true\)/);
  assert.equal(app.split('!assessmentReady() || !agentModelAvailable()').length - 1, 3);
  assert.match(app, /\|\| state.agentRunning \|\| !state.agentRunsLoaded/);
  assert.match(app, /consent: true, generate_code: true/);
  const workspace = read('static/workspace-access.js');
  assert.match(workspace, /if \(window.orionExternalModels\?\.blocksDatabricks\(\)\)/);
  assert.match(workspace, /connect.disabled = endpoint.disabled = confirmation.disabled = apply.disabled = true/);
  assert.match(workspace, /orionWorkspaceModelBusy = \(\) => modelBusy \|\| Boolean\(pendingSave\(\)\)/);
  assert.match(workspace, /if \(event.detail\?\.refreshDatabricks\) \{ resetModels\(\); modelState\(\); \}/);
});