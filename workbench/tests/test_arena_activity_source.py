"""Receipt-only arena checks. Node minimal DOM, no browser, network or models.

These tests execute the complete production renderer only when explicitly run.
The DOM double checks content/structure, not browser layout or theme appearance.
"""
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / "static"


class ArenaActivitySourceTests(unittest.TestCase):
    def test_role_activity_text_is_smaller_than_shared_arena_copy(self):
        css = (STATIC / 'app.css').read_text(encoding='utf-8')
        rule = css.split('#agent-arena .arena-role span.arena-role-signal {', 1)[1].split('}', 1)[0]
        self.assertIn('font-size: 10px;', rule)
        self.assertIn('line-height: 1.4;', rule)

    def test_activity_is_additive_and_has_no_side_effect_channels(self):
        source = (STATIC / "agent-arena.js").read_text(encoding="utf-8")
        activity = source.split("  const activityPanel =", 1)[1].split(
          "  stage.append(core, grid);", 1
        )[0]
        self.assertIn("stage.append(core, grid); middle.append(stage, progressPanel);", source)
        self.assertIn("layout.append(telemetry, middle, inspector, activityPanel)", source)
        self.assertIn("arena.append(header, status, outcome, stack, layout)", source)
        self.assertIn("renderActivity(live, active, historical);", source)
        self.assertIn("activityPanel.replaceChildren();", activity)
        self.assertIn("Number.isSafeInteger(value) && value >= 0", activity)
        for forbidden in (
            "fetch(", "XMLHttpRequest", "setTimeout(", "setInterval(",
            "addEventListener(", "innerHTML", "outerHTML", "insertAdjacentHTML",
            "localStorage", "sessionStorage", "current?.usage", "current?.output",
        ):
            self.assertNotIn(forbidden, activity)
        css = (STATIC / "agent-arena.css").read_text(encoding="utf-8")
        activity_css = css.split("/* Receipt-only activity", 1)[1]
        for forbidden in ("overflow-y:", "max-height:", "min-height:", "height: 300"):
            self.assertNotIn(forbidden, activity_css)
        self.assertIn('grid-column: 1 / -1', activity_css)
        arena_css = (STATIC / "agent-arena.css").read_text(encoding="utf-8")
        self.assertIn('#page-agents .arena-middle { grid-column: 2;', arena_css)
        self.assertIn('height: 600px;', arena_css)
        self.assertIn('block-size: 88px;', arena_css)
        self.assertIn('#page-agents .arena-roles { gap: 14px 14px; }', arena_css)
        self.assertIn('#page-agents .arena-elapsed-timer { font-size: 16px; text-align: center; }', arena_css)
        self.assertIn('#page-agents .arena-inspector .arena-findings { max-height: 400px; }', arena_css)
        self.assertIn('#page-agents .arena-progress { box-sizing: border-box;', arena_css)
        self.assertIn('#page-agents .arena-platform { inset: 0;', activity_css)
        self.assertIn("var(--surface)", activity_css)
        self.assertIn('grid-template-columns: repeat(auto-fit, minmax(min(100%, 170px), 1fr))', activity_css)
        self.assertIn('.arena-current-skills[data-active="true"]::after', activity_css)
        self.assertIn("content: '➜'", activity_css)
        self.assertIn('animation: arena-skill-orbit 16s linear infinite', activity_css)
        self.assertNotIn('visibility: hidden', activity_css)
        self.assertNotIn('overflow-y:', activity_css)
        self.assertIn("@media (max-width: 760px)", activity_css)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the minimal-DOM renderer check")
    def test_full_renderer_with_receipt_snapshots(self):
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attributes = {};
    this.dataset = {}; this.className = ''; this.listeners = {}; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw Error('HTML injection sink'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = [...children]; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  removeAttribute(key) { delete this.attributes[key]; }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  dispatchEvent(event) { for (const fn of this.listeners[event.type] ?? []) fn(event); }
}
const mount = new Element('div');
const document = new Element('document');
document.getElementById = id => id === 'agent-arena' ? mount : null;
document.createElement = tag => new Element(tag);
document.createElementNS = (_, tag) => new Element(tag);
document.querySelectorAll = () => [];
const window = new Element('window');
window.orionRedaction = {text: value => String(value)};
const forbidden = () => { throw Error('Unexpected side effect'); };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window, fetch: forbidden, setTimeout: forbidden, setInterval: forbidden,
  localStorage: new Proxy({}, {get: forbidden}), sessionStorage: new Proxy({}, {get: forbidden})
});
const walk = root => [root, ...root.children.flatMap(walk)];
const hasClass = (node, name) => (node.className || node.attributes.class || '').split(' ').includes(name);
const find = name => walk(mount).filter(node => hasClass(node, name));
const panel = () => find('arena-activity')[0];
const emit = record => document.dispatchEvent({type: 'orion:agent-run', detail: record});
const rows = () => walk(panel()).filter(node => node.tagName === 'tbody')[0].children;
assert.match(panel().textContent, /Select a run/);
assert.equal(find('arena-role').length, 6);
assert.equal(find('arena-inspector').length, 1);
const stage = find('arena-stage')[0];
assert.deepEqual(stage.children.slice(-2).map(node => node.className), ['arena-core', 'arena-roles']);
assert(!walk(stage).includes(find('arena-progress')[0]));
assert.deepEqual(find('arena-middle')[0].children.map(node => node.className), ['arena-stage', 'arena-progress']);
assert.deepEqual(find('arena-layout')[0].children.map(node => node.className), ['arena-telemetry', 'arena-middle', 'arena-inspector', 'arena-activity']);
assert.deepEqual(find('agent-arena')[0].children.slice(-1).map(node => node.className), ['arena-layout']);
assert.equal(find('arena-progress-value')[0].textContent, '0%');
assert.equal(find('arena-progress-control')[0].disabled, true);
assert.equal(find('arena-elapsed-timer')[0].textContent, '00:00:00');
assert.match(find('arena-run-cost')[0].textContent, /cost unavailable.*not connected/i);
assert.equal(find('arena-progress')[0].children.filter(node => ['arena-progress-value','arena-cost-box','arena-timer-box'].some(name=>hasClass(node,name))).length, 3);
assert(!walk(stage).includes(panel()));
assert.equal(find('agent-arena')[0].children.at(-1), find('arena-layout')[0]);
const turns = [
  {turn: 1, role: 'discovery', endpoint: 'model_a', status: 'completed', started_at: '2026-11-17T10:00:00Z', completed_at: '2026-11-17T10:01:00Z', payload: 'PRIVATE_PAYLOAD'},
  {turn: 2, role: 'discovery', endpoint: 'model_a', status: 'started', started_at: '2026-11-17T10:02:00Z'}
];
const receipt = {
  status: 'verified', public_run_id: 'r1', turn_count: 2, turns,
  counts: {model_started: 2, model_completed: 1, tool_started: 3, tool_completed: 2, tool_failed: 1},
  per_role: [{role: 'discovery', model_started: 2, model_completed: 1, model_failed: 0}],
  last_event: {role: 'artifact', event: 'tool_completed', tool_name: 'check_generation_code', created_at: '2026-11-17T10:03:00Z', payload: 'PRIVATE_PAYLOAD'}
};
const run = {id: 'r1', status: 'running', workflow_id: 'wf', package_hash: 'pkg', scenario: 'bronze',
  live_activity: receipt, usage: {total_tokens: 987654},
  current_usage_receipt: {status: 'verified', counts: {model_started: 9999}}};
emit(run);
assert.equal(find('arena-progress-value')[0].textContent, '1%');
assert.match(find('arena-progress-basis')[0].textContent, /Estimated progress.*1\/50 model calls.*2\/125 tool calls/);
emit({...run, live_activity: {...receipt, counts: {model_completed: 25, tool_completed: 63}}});
assert.equal(find('arena-progress-value')[0].textContent, '50%');
emit({...run, live_activity: {...receipt, counts: {model_completed: 8, tool_completed: 26}}});
assert.equal(find('arena-progress-value')[0].textContent, '50%');
emit({...run, live_activity: {...receipt, counts: {model_completed: 100, tool_completed: 300}}});
assert.equal(find('arena-progress-value')[0].textContent, '99%');
emit(run);
assert.equal(find('arena-elapsed-timer')[0].textContent.length, 8);
document.dispatchEvent({type: 'orion:agent-clock', detail: {id: 'r1', elapsed: 3661}});
assert.equal(find('arena-elapsed-timer')[0].textContent, '01:01:01');
document.dispatchEvent({type: 'orion:agent-clock', detail: {id: 'other', elapsed: 9}});
assert.equal(find('arena-elapsed-timer')[0].textContent, '01:01:01');
assert.match(panel().textContent, /Active run receipt/);
assert.equal(find('arena-chart-help').length, 1);
assert(walk(panel()).some(node => node.tagName === 'h4' && node.textContent === 'Model calls by role'));
const skillStats = {per_role: [
  {role: 'discovery', loaded: [{path: '/library/review/SKILL.md'}], tool_reads: [{path: '/library/review/SKILL.md', count: 2}]},
  {role: 'semantics', loaded: [{path: '/CONTEXT.md'}], tool_reads: []}
]};
emit({...run, live_activity: {...receipt, active_calls: [{role: 'discovery'}], skill_stats: skillStats}});
assert.equal(find('arena-current-skills').length, 1);
assert.equal(find('arena-current-skill').length, 1);
assert.equal(find('arena-current-skill')[0].dataset.active, 'true');
assert.equal(find('arena-current-skills')[0].dataset.active, 'true');
assert.equal(find('arena-current-skill')[0].children.length, 2);
assert.match(find('arena-current-skill')[0].textContent, /DiscoveryReview/);
assert.doesNotMatch(find('arena-current-skill')[0].textContent, /SKILL\.md|Loaded into context|successful reads|\/library/);
assert.match(find('arena-current-skills')[0].textContent, /Receipts show context, not execution/);
emit(run);
assert.equal(find('arena-activity-flow').length, 0);
assert.equal(find('arena-activity-totals').length, 1);
assert.match(panel().textContent, /Recorded model turns: 2/);
assert.match(panel().textContent, /rejected: Unknown/);
assert.match(panel().textContent, /Total check calls unknown/);
assert.match(panel().textContent, /Correction activity not recorded/);
assert.equal(find('arena-turn-note')[0].attributes.role, 'note');
assert.match(find('arena-turn-info')[0].attributes['aria-label'], /not a whole ReAct cycle/);
assert(!find('arena-turn-note')[0].textContent.includes('One turn is'));
assert(!panel().textContent.includes('PRIVATE_PAYLOAD'));
assert(!panel().textContent.includes('987654'));
assert(!panel().textContent.includes('9999'));
assert.equal(rows().length, 6);
assert.deepEqual(rows()[1].children.slice(1).map(node => node._text), ['2', '1', '0']);
assert.deepEqual(rows()[0].children.slice(1).map(node => node._text), ['Unknown', 'Unknown', 'Unknown']);
const bars = find('arena-activity-bar');
assert.equal(bars.length, 6);
assert.equal(bars[1].attributes.role, 'img');
assert.match(bars[1].attributes['aria-label'], /1 completed model calls/);
assert.equal(bars[1].children[1].attributes.width, '100');
assert.equal(bars[0].children.length, 1); // Unknown is not a zero-valued bar.
assert.equal(find('arena-activity-turns')[0].children[2].children.length, 2);
// Every invalid/missing counter stays unknown, but an explicit numeric zero survives.
emit({...run, live_activity: {...receipt, turn_count: '2', counts: {
  model_started: true, model_completed: -1, model_failed: 0, tool_started: 1.5,
  tool_completed: '2', tool_failed: Number.MAX_SAFE_INTEGER + 1
}, per_role: [{role: 'artifact', model_started: null, model_completed: false, model_failed: 0}],
last_event: {role: 'alien', event: '<script>', endpoint: '<img>', created_at: 'bad-time'}}});
assert.match(panel().textContent, /Recorded model turns: Unknown/);
assert.match(panel().textContent, /started: Unknown · completed: Unknown · rejected: Unknown · failed: 0/);
assert.match(panel().textContent, /Role unknown · Name unknown · State unknown · Time unknown/);
assert.equal(rows()[5].children[3]._text, '0');
assert(!panel().textContent.includes('<script>'));
// Matching terminal snapshot wins over a conflicting current live receipt.
const saved = {...receipt, skill_stats: skillStats, schema_version: 1, mode: 'historical_not_live', package_hash: 'pkg',
  workflow_id: 'wf', scenario: 'bronze', run_status: 'completed', finished_at: '2026-11-17T10:04:00Z'};
const terminal = {...run, status: 'completed', finished_at: saved.finished_at,
  observation_snapshot: saved, live_activity: {...receipt, turn_count: 9999}};
emit(terminal);
assert.match(panel().textContent, /Historical snapshot, not live/);
assert.match(panel().textContent, /Recorded model turns: 2/);
assert.equal(find('arena-current-skill').length, 2);
assert(find('arena-current-skill').every(card => card.dataset.active === 'false'));
assert(!panel().textContent.includes('9999'));
// An unverified selected snapshot cannot borrow current usage or live counters.
emit({...terminal, observation_snapshot: {...saved, status: 'unverified'}});
assert.match(panel().textContent, /Verified activity unavailable/);
assert.match(panel().textContent, /Recorded model turns: Unknown/);
// A mismatched historical snapshot and unrelated live receipt are not accepted.
emit({...terminal, observation_snapshot: {...saved, workflow_id: 'other'},
  live_activity: {...receipt, public_run_id: 'other'}});
assert.match(panel().textContent, /Verified activity unavailable/);
// Duplicate per-role rows cannot silently select one receipt as authoritative.
emit({...run, live_activity: {...receipt, per_role: [receipt.per_role[0], receipt.per_role[0]]}});
assert.equal(rows()[1].children[1]._text, 'Unknown');
// Actual metadata is inserted as text, never interpreted as HTML.
emit({...run, live_activity: {...receipt, last_event: {...receipt.last_event,
  tool_name: '<img src=x>', event: 'tool_failed', created_at: 'bad'}}});
assert.match(panel().textContent, /Name unknown/);
assert(!walk(panel()).some(node => node.tagName === 'img'));
assert(!panel().textContent.includes('Total check calls unknown'));
// A different run without activity must not retain previous counters or turns.
emit({id: 'r2', status: 'running'});
assert.match(panel().textContent, /Verified activity unavailable/);
assert.match(panel().textContent, /Model-call turn records unavailable/);
assert(!panel().textContent.includes('model_a'));
emit(null);
assert.match(panel().textContent, /Select a run/);
assert.equal(find('arena-activity-bar').length, 0);
emit(run);
document.dispatchEvent({type: 'change', target: {id: 'agent-workflow'}});
assert.match(panel().textContent, /Select a run/);
assert.equal(find('arena-role').length, 6);
assert.equal(find('arena-inspector').length, 1);
console.log('Full arena renderer receipt checks passed (minimal DOM, no browser).');
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script, str(STATIC / "agent-arena.js")],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()