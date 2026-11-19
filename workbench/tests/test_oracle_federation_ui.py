"""Targeted explorer checks. No browser, real network, or model calls."""
from pathlib import Path
import shutil
import subprocess
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "static" / "redesign-review.js"


class OracleFederationUITests(unittest.TestCase):
    def test_existing_target_action_and_direct_scoped_transport_remain_separate(self):
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("button('source-target-read', 'Inspect source targets', targets)", source)
        self.assertIn("await loadTargets();", source)
        self.assertIn("scopeActions.after(oracleExplorer.root);", source)
        self.assertIn("await request(`/api/oracle-federation/metadata?${query}`, ticket)", source)
        self.assertIn("headers['X-Workbench-Project'] = ticket.scope.project", source)
        self.assertIn("headers['X-Workbench-Package'] = ticket.scope.package", source)
        self.assertIn("window.orionScopedReads?.fetch", source)
        self.assertIn("oracleExplorer.reset();", source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_explorer_dom_and_mocked_requests(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const sourceText = fs.readFileSync(process.argv[1], 'utf8');
const begin = sourceText.indexOf('  function createOracleSourceExplorer() {');
const end = sourceText.indexOf('  const oracleExplorer = createOracleSourceExplorer();', begin);
assert(begin >= 0 && end > begin);
const ids = new Map(), calls = [], queue = [];
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {}; this.disabled = false; this._value = ''; this._text = ''; }
  set id(value) { this._id = value; ids.set(value, this); }
  get id() { return this._id; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw Error('HTML sink'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; if (this.tag === 'select') this._value = children[0]?.value || ''; }
  setAttribute() {}
  addEventListener(name, fn) { (this.listeners[name] ??= []).push(fn); }
  get options() { return this.children; }
  get selectedOptions() { return this.children.filter(item => item.value === this.value); }
  set value(value) { this._value = value; }
  get value() { return this._value; }
  click() { if (!this.disabled) for (const fn of this.listeners.click || []) fn(); }
  change(value) { this.value = value; for (const fn of this.listeners.change || []) fn(); }
}
class Option extends Element { constructor(text, value) { super('option'); this.textContent = text; this.value = value; } }
const node = (tag, text = '', className = '') => { const e = new Element(tag); e.textContent = text; e.className = className; return e; };
const button = (id, text, parent) => { const e = node('button', text); e.id = id; parent.append(e); return e; };
const field = (tag, id, label, parent) => { const e = node(tag); e.id = id; parent.append(node('label', label), e); return e; };
let scope = 'wf1', epoch = 0, visible = true;
const valid = () => true, workflowKnown = value => !!value, source = () => ({workflow: scope});
const scopeActive = () => visible;
const capture = () => ({epoch, scope: source()});
const current = ticket => ticket.epoch === epoch && ticket.scope.workflow === scope;
const request = (path, ticket) => {
  calls.push({url: new URL(path, 'http://local'), ticket});
  if (!queue.length) throw Error('No mocked response');
  return queue.shift();
};
const safe = value => String(value ?? 'Unknown');
const context = {node, button, field, Option, valid, workflowKnown, source, scopeActive,
  capture, current, request, safe, URLSearchParams};
vm.runInNewContext(sourceText.slice(begin, end) + '\nthis.explorer = createOracleSourceExplorer();', context);
const explorer = context.explorer;
const get = id => ids.get('oracle-source-' + id);
const status = () => get('status').textContent;
const response = (operation, items, extra = {}) => ({format: 'oracle-federation-metadata-v1', operation,
  status: 'observed', exists: null, items, binding_approved: false, execution_allowed: false,
  deployment_allowed: false, offset: 0, limit: 20, has_more: false, scan_limit_reached: false, ...extra});
const tick = () => new Promise(resolve => setImmediate(resolve));
async function click(id, data) { queue.push(Promise.resolve(data)); get(id).click(); await tick(); }
(async () => {
  explorer.sync();
  assert.equal(calls.length, 0);
  assert.equal(explorer.root.tag, 'details');
  assert.equal(explorer.root.children[0].tag, 'summary');
  assert.match(explorer.root.children[0].textContent, /Optional.*Oracle source metadata/);
  assert.match(explorer.root.textContent, /does not bind it to the workflow/i);
  await click('catalogs-load', response('catalogs', [], {has_more: true, next_offset: 20}));
  assert.match(status(), /No visible items.*partial/);
  assert.equal(calls[0].url.searchParams.get('limit'), '20');
  await click('next', response('catalogs', [{name: 'mystery', foreign: null}, {name: 'foreign', foreign: true}], {offset: 20}));
  assert.equal(calls[1].url.searchParams.get('offset'), '20');
  get('catalog').change('mystery');
  assert.match(status(), /unknown/); assert.equal(get('schemas-load').disabled, true);
  get('catalog').change('foreign');
  await click('schemas-load', response('schemas', [{name: 'APP', catalog_name: 'foreign'}]));
  assert.equal(calls[2].url.searchParams.get('catalog'), 'foreign');
  get('schema').change('APP');
  await click('tables-load', response('tables', [{name: 'ORDERS', catalog_name: 'foreign', schema_name: 'APP', oracle_format: null}]));
  assert.match(get('table').textContent, /format unknown/);
  get('table').change('ORDERS');
  await click('table-load', response('table', [{name: 'ORDERS', catalog_name: 'foreign', schema_name: 'APP',
    full_name: 'foreign.APP.ORDERS', oracle_format: true, columns: [{name: '<img>', type_text: 'DECIMAL(10,0)', nullable: false}]}], {exists: true}));
  assert.equal(calls[4].url.searchParams.has('limit'), false);
  assert.match(status(), /Oracle format observed/);
  assert.match(get('output').textContent, /<img>.*DECIMAL/);
  assert.equal(get('output').children.some(child => child.tag === 'img'), false);
  await click('table-load', response('table', [], {status: 'unknown'}));
  assert.match(status(), /unknown.*permission denial/i);
  await click('table-load', response('table', [], {status: 'table_not_found', exists: false}));
  assert.match(status(), /exact table.*not found/i);
  queue.push(Promise.reject(Error('GET /api/oracle-federation/metadata: HTTP 403')));
  get('table-load').click(); await tick(); assert.match(status(), /denied/i);
  let resolveOld;
  queue.push(new Promise(resolve => { resolveOld = resolve; }));
  get('table-load').click();
  get('schema').change('');
  resolveOld(response('table', [{name: 'OLD', columns: []}], {exists: true}));
  await tick(); assert.doesNotMatch(get('output').textContent, /OLD/);
  queue.push(new Promise(resolve => { resolveOld = resolve; }));
  get('catalogs-load').click(); scope = 'wf2'; epoch++; explorer.reset();
  resolveOld(response('catalogs', [{name: 'OLD', foreign: true}]));
  await tick(); assert.doesNotMatch(get('catalog').textContent, /OLD/);
  assert.match(status(), /Previous source metadata cleared/);
  await click('catalogs-load', response('catalogs', [], {has_more: true, scan_limit_reached: true}));
  assert.match(status(), /scan limit.*incomplete/i); assert.equal(get('next').disabled, true);
  assert(calls.every(call => call.url.pathname === '/api/oracle-federation/metadata'));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        subprocess.run([shutil.which("node"), "-e", script, str(SOURCE)], check=True)