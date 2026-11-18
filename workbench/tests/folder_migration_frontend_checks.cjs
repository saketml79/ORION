/* Focused offline DOM contract checks. No Python, backend, models or cloud. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../static');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'folder-migration.js'), 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.dataset = {}; this.listeners = {}; this.attrs = {}; this.hidden = false; this.value = ''; this.checked = false; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  setAttribute(key, value) { this.attrs[key] = value; }
  getAttribute(key) { return this.attrs[key]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  fire(name) { for (const fn of this.listeners[name] || []) fn({ target: this }); }
  click() { this.fire('click'); }
  remove() { this.removed = true; }
  get firstElementChild() { return this.children[0]; }
}
const all = (n, tag) => [...n.children.flatMap(c => [c, ...all(c, tag)])].filter(c => !tag || c.tagName === tag);
const tick = async () => { for (let i = 0; i < 20; ++i) await Promise.resolve(); };
const ids = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m => [m[1], new Element()]));
const get = id => ids.get(id);
get('fm-type').value = 'all';
get('page-folder-migration').hidden = true;
const events = {}, observers = [], calls = [], copies = [], downloads = [];
const document = { getElementById: get, createElement: tag => new Element(tag), addEventListener: (key, fn) => (events[key] ||= []).push(fn) };
const page = get('page-folder-migration');
const hash = 'package-A';
function makePlan(id = 'preferred', packageHash = hash) {
  return { folder_id: id, package_hash: packageHash, input_version: `v-${id}`, candidate_generation_available: true,
    folder: { name: id === 'preferred' ? 'B_AP_INVOICELOG' : id, scope: { repository: 'R' } },
    business_purpose: { summary: 'Captured structural purpose', status: 'structural_summary_not_inferred_business_validation', domain_intent: 'unverified' },
    counts: { source: 11, target: 2, mapping: 2, workflow: 1 }, table_summary: { observed_data_row_count: null, sap_odbc_source_definitions: 11 },
    tables: Array.from({ length: 11 }, (_, i) => ({ id: `source-${i}`, name: `SOURCE_${i}`, database_type: 'ODBC', sap_metadata_hint: true,
      field_count: 2, incremental_query_count: 1, initial_or_snapshot_query_count: 1, declared_source_keys: ['ID'],
      verified_cdc_keys: null, verified_sequence_by: null, snapshot_completeness: 'unknown', acquisition_mode: 'initial_and_incremental_predicates',
      mapping_ids: ['map-1'], mapping_names: ['EXACT_MAPPING'], proposed_options: ['Verify CDC keys and sequence', 'Verify complete snapshots'], sql_ids: [] })),
    workflows: [{ id: 'wf', name: 'EXACT_WORKFLOW' }], layers: [{ name: 'Source capture', status: 'proposed', purpose: 'Capture predicates' }],
    patterns: [{ category: 'md5_expression', name: 'Hash', value: 'MD5(ID)' }], blockers: [{ code: 'CDC_CONTRACT_UNVERIFIED', detail: 'Verify keys' }],
    execution_permitted: false, deployment_allowed: false, notice: 'Partial candidate, not Genie equivalent' };
}
const fileContent = 'SELECT EXACT_NAME, \'<script>alert(1)</script>\', \'$$PASSWORD\';\n\n';
const graph = { package: { sha256: hash }, nodes: [
  { id: 'map-1', name: 'EXACT_MAPPING', kind: 'mapping', scope: { folder_id: 'preferred' } },
  { id: 'map-2', name: 'UNUSED_MAPPING', kind: 'mapping', scope: { folder_id: 'preferred' } },
  { id: 'ti', name: 'Target instance', kind: 'instance', parent_id: 'map-1', scope: { folder_id: 'preferred' } },
  { id: 'target', name: 'EXACT_TARGET', kind: 'target', scope: { folder_id: 'preferred' } }
], edges: [{ kind: 'instance_of', source: 'ti', target: 'target' }] };
const artifacts = [
  { path: 'captured_sql/source.sql', content: fileContent, language: 'sql', sha256: 'abc' },
  { path: 'stage/candidate.sql', content: 'SELECT ID FROM __REQUIRED_UPSTREAM_VIEW__;\n', language: 'sql', sha256: 'def' },
  { path: 'layers/stage.py', content: 'def candidate():\n    pass\n', language: 'python' },
  { path: 'evidence/folder_graph.json', content: JSON.stringify(graph), language: 'json' },
  { path: 'README.md', content: '# Candidate\n', language: 'markdown' }
];
const receipt = { folder_id: 'preferred', package_hash: hash, input_version: 'v-preferred', status: 'candidate_partial_not_runtime_complete',
  execution_permitted: false, deployment_allowed: false, persisted: false, artifacts };
let pending = null, badVersion = false, responseStatus = 200, packageMismatch = false;
const fetch = async (url, options) => {
  calls.push({ url, options });
  if (pending && url.endsWith(pending.suffix)) return new Promise(resolve => { pending.resolve = resolve; });
  let data;
  if (url.includes('?offset=0')) data = { package_hash: hash, offset: 0, total: 51, has_more: true, items: Array.from({ length: 50 }, (_, i) => ({ id: `folder-${i}`, name: `F_${i}`, scope: { repository: 'R' } })) };
  else if (url.includes('?offset=50')) data = { package_hash: hash, offset: 50, total: 51, has_more: false, items: [{ id: 'preferred', name: 'B_AP_INVOICELOG', scope: { repository: 'R' } }] };
  else if (options.method === 'POST') data = { ...receipt, input_version: badVersion ? 'wrong-version' : receipt.input_version };
  else data = makePlan(url.split('/').pop());
  if (packageMismatch) data = { ...data, package_hash: 'other-package' };
  return { ok: responseStatus === 200, status: responseStatus, json: async () => data };
};
const sandbox = { document, window: { fetch }, AbortController, Blob,
  MutationObserver: class { constructor(fn) { observers.push(fn); } observe() {} },
  navigator: { clipboard: { writeText: async text => copies.push(text) } },
  URL: { createObjectURL: blob => { downloads.push(blob); return 'blob:offline'; }, revokeObjectURL() {} } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, 'redaction.js'), 'utf8'), sandbox);
vm.runInContext(source, sandbox);
const sync = async () => { observers.forEach(fn => fn()); await tick(); };
const event = async (id, name) => { get(id).fire(name); await tick(); };
const consent = async () => { get('fm-consent').checked = true; await event('fm-consent', 'change'); };
const response = data => ({ ok: true, status: 200, json: async () => data });
(async () => {
  assert.equal(calls.length, 0, 'Inactive page must not fetch');
  const nav = [...html.matchAll(/data-page="([^"]+)"/g)].map(m => m[1]);
  assert.equal(nav[nav.indexOf('estate') + 1], 'folder-migration');
  assert.match(fs.readFileSync(path.join(root, 'app.js'), 'utf8'), /'folder-migration': 'Folder migration'/);
  assert(!/innerHTML|outerHTML|setTimeout|https?:\/\//.test(source), 'No unsafe HTML, elapsed deadline or external resources');
  Object.assign(page.dataset, { projectId: 'project-A', packageHash: hash, packageReady: 'true' });
  page.hidden = false; await sync();
  assert.equal(calls.length, 3, 'All pages then exactly one report');
  assert.equal(get('fm-folder').children.length, 51);
  assert.equal(get('fm-folder').value, 'preferred', 'Exact preferred name on later page');
  assert.equal(all(get('fm-report')).filter(n => n.dataset.sourceId).length, 11);
  assert.match(get('fm-report').textContent, /SOURCE_10/);
  assert.match(get('fm-report').textContent, /Target names in linked mappings/);
  assert.match(get('fm-report').textContent, /Unknown/);
  assert.equal(get('fm-generate').disabled, true);
  for (const call of calls) {
    assert.equal(call.options.headers['X-Workbench-Project'], 'project-A');
    assert.equal(call.options.headers['X-Workbench-Package'], hash);
    assert.equal(call.options.credentials, 'same-origin'); assert(call.options.signal);
  }
  await sync(); await sync(); assert.equal(calls.length, 3, 'Observers must not duplicate reads');
  await event('fm-generate', 'click'); assert.equal(calls.length, 3, 'No implicit consent');
  await consent(); await event('fm-generate', 'click');
  const post = calls.at(-1);
  assert.equal(post.options.method, 'POST'); assert.equal(post.options.body, '{"consent":true}');
  assert.equal(post.options.headers['X-Workbench-Request'], '1');
  assert.match(get('fm-report').textContent, /EXACT_TARGET/);
  for (const detail of all(get('fm-report'), 'details')) { detail.open = true; detail.fire('toggle'); }
  assert.match(get('fm-report').textContent, /UNUSED_MAPPING/);
  assert.equal(get('fm-files').children.length, artifacts.length);
  assert.equal(all(get('fm-file-view'), 'pre')[0].textContent, fileContent);
  assert.equal(all(get('fm-file-view'), 'script').length, 0);
  const buttons = all(get('fm-file-view'), 'button'); buttons.find(b => b.textContent === 'Copy contents').click(); await tick();
  assert.equal(copies[0], fileContent);
  buttons.find(b => b.textContent === 'Download file').click();
  assert.equal(await downloads[0].text(), fileContent);
  assert.equal(get('fm-consent').checked, false, 'Consent consumed by generation');
  get('fm-type').value = 'python'; await event('fm-type', 'change');
  assert.equal(get('fm-files').children.length, 1); assert.match(get('fm-file-view').textContent, /not a complete runnable pipeline/);
  get('fm-type').value = 'json'; await event('fm-type', 'change'); assert.equal(get('fm-files').children.length, 1);
  get('fm-type').value = 'sql'; await event('fm-type', 'change'); assert.equal(get('fm-files').children.length, 2);
  get('fm-file-search').value = 'no-match'; await event('fm-file-search', 'input'); assert.equal(get('fm-files').children.length, 0);
  assert.match(get('fm-file-view').textContent, /No files match/);
  get('fm-file-search').value = ''; await event('fm-file-search', 'input'); assert.equal(get('fm-files').children.length, 2);
  badVersion = true; await consent(); await event('fm-generate', 'click');
  assert.match(get('fm-artifact-status').textContent, /version changed/); assert(get('fm-artifacts').hidden); badVersion = false;
  pending = { suffix: '/folder-1' }; get('fm-folder').value = 'folder-1'; await event('fm-folder', 'change');
  const firstSignal = calls.at(-1).options.signal, oldPending = pending; pending = null;
  get('fm-folder').value = 'folder-2'; await event('fm-folder', 'change'); assert(firstSignal.aborted);
  oldPending.resolve(response(makePlan('folder-1'))); await tick();
  assert.match(get('fm-status').textContent, /folder-2/); assert(!get('fm-report').textContent.includes('v-folder-1'));
  // Changing package scope invalidates an in-flight POST, even if a transport
  // resolves after abort. Its old files must never be shown in the new scope.
  get('fm-folder').value = 'preferred'; await event('fm-folder', 'change');
  pending = { suffix: '/artifacts' }; await consent(); get('fm-generate').click(); await tick();
  const postSignal = calls.at(-1).options.signal, oldPost = pending; pending = null;
  page.dataset.packageReady = 'false'; page.dataset.packageHash = 'package-B'; await sync();
  assert(postSignal.aborted); oldPost.resolve(response(receipt)); await tick();
  assert.equal(get('fm-report').textContent, ''); assert(get('fm-artifacts').hidden);
  Object.assign(page.dataset, { packageHash: hash, packageReady: 'true' });
  packageMismatch = true; await sync(); assert.match(get('fm-status').textContent, /package does not match/); assert.equal(get('fm-report').textContent, '');
  packageMismatch = false; responseStatus = 404; await event('fm-refresh', 'click');
  assert.match(get('fm-status').textContent, /parent-managed restart/);
  assert.equal(get('fm-report').textContent, '');
  assert.equal(sandbox.window.orionRedaction.text("password='actual-secret'"), "password='[REDACTED]'");
  assert.equal(sandbox.window.orionRedaction.text('SELECT $$PASSWORD, token_count, EXACT_TABLE'), 'SELECT $$PASSWORD, token_count, EXACT_TABLE');
  console.log('PASS: navigation placement, inactive gating, all folder pages, exact default scope, 11 source cards, exact graph names, explicit consent and mutation headers, file filters/copy/download, inert HTML, version/package rejection, epoch/abort races, no duplicate reads, API-unavailable guidance, credential-only redaction.');
})().catch(error => { console.error(error); process.exitCode = 1; });