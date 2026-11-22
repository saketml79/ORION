"""Read-only source contracts, no browser, server, models, or cloud.

These assertions are intentionally not a runtime/visual verification claim.
"""
from pathlib import Path
import re
import unittest


STATIC = Path(__file__).resolve().parents[1] / "static"
JS = (STATIC / "redesign-animation.js").read_text(encoding="utf-8")
CSS = (STATIC / "redesign-animation.css").read_text(encoding="utf-8")


class RedesignAnimationSourceTests(unittest.TestCase):
    def test_isolated_public_contract(self):
        self.assertIn("window.orionRedesignAnimation = Object.freeze({open, close})", JS)
        self.assertIn("function open(options)", JS)
        self.assertIn("function close()", JS)
        self.assertIn("dialog.showModal()", JS)
        self.assertIn("return true;", JS)
        self.assertIn("Main owns asset/button wiring", JS)

    def test_no_network_execution_or_html_parsing(self):
        for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "EventSource", "innerHTML", "outerHTML",
                          "insertAdjacentHTML", "DOMParser", "eval(", "new Function", "import(", "requestAnimationFrame"):
            self.assertNotIn(forbidden, JS)
        for forbidden in ("@import", "url("):
            self.assertNotIn(forbidden, CSS)
        self.assertNotRegex(CSS, r"(?:^|[;{])\s*animation\s*:")
        self.assertIn("textContent = safe(value)", JS)
        self.assertIn("window.orionRedaction?.text", JS)
        for body in ("'code'", "'cells'", "'notebook'", "'content'", "'artifacts'"):
            self.assertNotIn(f"field(task, {body})", JS)
            self.assertNotIn(f"field(design, {body})", JS)

    def test_detached_whitelist_and_bad_identity_handling(self):
        self.assertIn("Object.getOwnPropertyDescriptor(value, key)", JS)
        self.assertIn("Object.freeze(targets)", JS)
        self.assertIn("tasks: frozen(tasks), edges: frozen(edges), coverage: frozen(coverage)", JS)
        self.assertIn("supplied instanceof Map", JS)
        self.assertIn("Map.prototype.forEach.call", JS)
        self.assertIn("!key || seen.has(key)", JS)
        self.assertIn("!seen.has(source) || !seen.has(target)", JS)
        self.assertIn("!seen.has(key) || unique.has(key)", JS)
        self.assertIn("malformed or duplicate entries ignored", JS)
        self.assertNotIn("JSON.parse(JSON.stringify", JS)

    def test_evidence_only_topology_and_full_coverage(self):
        self.assertIn("for (const row of data.coverage)", JS)
        self.assertIn("sourceRows.get(row.original_task_id).push(row)", JS)
        self.assertIn("for (const key of bridge.proposed_task_ids)", JS)
        self.assertIn("for (const edge of data.edges)", JS)
        self.assertIn("Source dependency links not shown", JS)
        self.assertIn("not proof of semantic or data parity", JS)
        self.assertIn("No workflow merge is inferred", JS)
        self.assertIn("workflow?.kind === 'workflow' && workflow.label", JS)
        self.assertIn("/^(removed|disabled)$/iu", JS)
        self.assertIn("No coverage correspondence supplied", JS)
        self.assertIn("No valid proposed correspondence supplied", JS)
        self.assertNotRegex(JS, r"(?:sources|targets|bridges|coverage|tasks)\.slice\(")
        self.assertIn("offset < chars.length", JS)
        self.assertIn("viewBox: `0 0 1280 ${height}`", JS)
        self.assertIn("All task labels, correspondences and proposed edges (text)", JS)

    def test_single_owned_timer_and_lifecycle_cleanup(self):
        self.assertEqual(len(re.findall(r"\bsetTimeout\(", JS)), 1)
        self.assertNotIn("setInterval", JS)
        self.assertIn("const STEP_MS = 2800", JS)
        self.assertIn("clearTimeout(state.timer); state.timer = null", JS)
        self.assertIn("state.paused || media.matches || document.hidden", JS)
        self.assertIn("listen(document, 'visibilitychange', schedule)", JS)
        self.assertIn("listen(window, 'pagehide', close)", JS)
        self.assertIn("state.observer?.disconnect()", JS)
        self.assertIn("for (const dispose of state.listeners) dispose()", JS)
        self.assertIn("!dialog.isConnected || !dialog.open", JS)
        self.assertIn("isCurrent() === true", JS)
        self.assertIn("catch { fresh = false; }", JS)

    def test_native_modal_focus_and_reduced_motion(self):
        self.assertNotIn("preventDefault", JS)
        self.assertIn("listen(dialog, 'close'", JS)
        self.assertIn("backdropDown && outside(event)", JS)
        self.assertIn("state.trigger?.isConnected", JS)
        self.assertIn("dismiss.focus({preventScroll: true})", JS)
        self.assertIn("listen(media, 'change'", JS)
        self.assertIn("state.paused = true; schedule()", JS)
        self.assertIn("play.disabled = media.matches", JS)
        self.assertIn("'Next stage'", JS)
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        self.assertIn(":focus-visible", CSS)
        self.assertIn("[data-reduced-motion=\"true\"] .ora-traveler { display: none; }", CSS)
        # CSS transform:none would override SVG card positioning attributes.
        self.assertNotIn("transform: none", CSS)

    def test_real_block_motion_and_borderless_scoped_theme(self):
        self.assertIn("wrapper.append(card({...source, x: 0, y: 0}", JS)
        for position in ("from", "bridge", "to"):
            self.assertIn(f"--ora-{position}-x", JS)
            self.assertIn(f"translate(var(--ora-{position}-x), var(--ora-{position}-y))", CSS)
        for phase in range(4):
            self.assertIn(f'[data-phase="{phase}"]', CSS)
        self.assertIn("transform .8s", CSS)
        self.assertIn("perspective: 1600px", CSS)
        self.assertRegex(CSS, r"\.orion-redesign-animation \.ora-viewport[\s\S]*?box-shadow: 0 14px 34px")
        self.assertIn("border: 0", CSS)
        self.assertIn("stroke: none", CSS)
        self.assertIn("overflow: auto", CSS)
        self.assertIn(':root[data-theme="dark"]', CSS)
        self.assertNotRegex(CSS, r"border:\s*[1-9]")


if __name__ == "__main__":
    unittest.main()