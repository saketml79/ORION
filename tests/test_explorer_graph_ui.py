"""Current full frontend in offline Chromium. No backend, models or business writes."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from test_navigation_prerequisites import navigation, tab, choose_project
from test_project_package_autoload import (
    browser, member, settle, PROJECT_A, PROJECT_B, PACKAGE_A, PACKAGE_B,
)


ROOT_ID = "wf/selected"
CHILD_ID = "source/chosen"


@pytest.fixture
def explorer(navigation):
    def start(*, hosted=False, hold_assessment=False, hold_object=False, standalone=False):
        page, state, fulfill = navigation(fragment="projects", legacy=not hosted)
        state["projects"][PROJECT_B] = [member(PACKAGE_A)]
        state["objects"] = [{"id": ROOT_ID, "name": "Selected workflow", "kind": "workflow"}]
        state.update(trace=[], pending_assessment=[], pending_object=[],
                     hold_assessment=hold_assessment, hold_object=hold_object, fail_object=False)
        wire = []
        page.on("request", lambda req: wire.append((req.method, urlsplit(req.url).path)))

        def scoped(route):
            req = route.request
            path = urlsplit(req.url).path
            operation = None
            if path == "/api/hosted/operations" and req.method == "POST":
                body = req.post_data_json
                if body["method"] != "GET":
                    route.fallback()
                    return
                operation, path = body["id"], body["path"]
            if not (path.startswith("/api/object/") or "/migration-context" in path
                    or path == "/api/agents/runs"):
                route.fallback()
                return
            entry = {"path": path, "method": req.method, "headers": req.all_headers()}
            state["trace"].append(entry)

            def result(data):
                return {"operation_id": operation, "status": "completed", "result": data} if operation else data

            if path.startswith("/api/object/"):
                if state["fail_object"]:
                    route.fulfill(status=503, content_type="application/json",
                                  body=json.dumps({"detail": "offline object read failed"}))
                    return
                digest = entry["headers"].get("x-workbench-package", "legacy")
                root = {"id": ROOT_ID, "name": f"Selected workflow {digest[:1]}", "kind": "workflow",
                        "fields": [{"attributes": {"NAME": "ROOT_ONLY"}}]}
                child = {"id": CHILD_ID, "name": "Chosen source", "kind": "source", "layer": 4,
                         "fields": [{"attributes": {"NAME": "CHOSEN_FIELD", "DATATYPE": "string"},
                                     "kind": "sourcefield", "evidence": {"locator": "/SOURCE/FIELD"}}],
                         "properties": [{"attributes": {"NAME": "Chosen property", "VALUE": "Captured"}}],
                         "evidence": {"locator": "/SOURCE"}}
                data = result({"node": root, "graph": {"nodes": [root, child], "edges": [
                    {"source": ROOT_ID, "target": CHILD_ID, "kind": "contains"}]}, "children": []})
                if state["hold_object"]:
                    state["pending_object"].append((route, data))
                    return
            elif path == "/api/agents/runs":
                data = result({"items": []})
                if state["hold_assessment"]:
                    state["pending_assessment"].append((route, data))
                    return
            else:
                route.fulfill(status=503, content_type="application/json",
                              body=json.dumps({"detail": "offline context not supplied"}))
                return
            fulfill(route, data)

        page.route("**/api/**", scoped)
        if not standalone:
            choose_project(page)
        expect(page.locator("#package-ready")).to_have_text("Package ready")
        tab(page, "explorer").click()
        expect(page.locator("#object-list button")).to_have_count(1)
        settle(page)
        return page, state, fulfill, wire

    return start


def selected_object(page):
    return page.locator(f'#object-list [data-object-id="{ROOT_ID}"]')


def test_english_description_leads_with_workflow_purpose(explorer):
    page, state, fulfill, wire = explorer()
    def object_route(route):
        root = {'id': ROOT_ID, 'name': 'Selected workflow', 'kind': 'workflow'}
        fulfill(route, {'node': root, 'graph': {'nodes': [root], 'edges': []}, 'children': [],
                        'behavior_explanation': {'sections': [
                            {'title': 'What this workflow does', 'items': ['Launches the child without waiting.']},
                            {'title': 'What the captured code does', 'items': ['The SQL inserts rows.']}],
                            'coverage': {'truncated': False}}})
    page.route('**/api/object/*', object_route)
    before = len(wire)
    selected_object(page).click()
    loaded(page)
    page.locator('#tab-english').click()
    content = page.locator('#english-content')
    expect(content.locator('h3').first).to_have_text('What this workflow does')
    expect(content).to_contain_text('Launches the child without waiting.')
    expect(content).to_contain_text('The SQL inserts rows.')
    assert all(method == 'GET' or path == '/api/hosted/heartbeat' for method, path in wire[before:])


def test_package_context_explains_callers_and_nonblocking_children(explorer):
        page, _, _, _ = explorer()
        page.evaluate('''() => {
            const panel = document.createElement('section'); panel.id = 'test-context'; document.body.append(panel);
            const empty = {items: [], total: 0, offset: 0, limit: 40, truncated: false, has_more: false};
            window.renderPackageMigrationContext(panel, {
                selected: {name: 'Wrapper', direct_sources: 1, direct_targets: 1},
                cohort: {...empty, total: 32, direct_source_count_sum: 0, direct_target_count_sum: 0},
                referenced_children: {...empty, source_count_sum: 0, target_count_sum: 0},
                invocations: {...empty, total: 2, truncated: true, items: [
                    {direction: 'inward', caller_name: 'Master', candidate_names: ['Wrapper'], wait_mode: 'wait'},
                    {direction: 'outward', caller_name: 'Wrapper', candidate_names: ['Child'], wait_mode: 'nowait'}]},
                outside_references: empty, cycles: empty, potential_data_dependencies: empty,
                blockers: [], human_questions: [], convert_together: 'Review together.', keep_external_explicitly: 'Keep external explicit.'
            });
        }''')
        panel = page.locator('#test-context')
        expect(panel).to_contain_text('group of 32 workflow(s)')
        expect(panel).to_contain_text('Master can call this workflow, with a wait requested')
        expect(panel).to_contain_text('This workflow can call Child, without waiting for completion')
        expect(panel).to_contain_text('The call list is paged')
        page.locator('#test-context').evaluate('(element) => element.remove()')


@pytest.fixture
def graph_coverage(explorer, tmp_path):
    from workbench.app import PackageIndex
    from workbench.package_graph import package_graph
    from workbench.understanding import analyze_package
    # 243 distinct definitions parsed from XML, not cloned model nodes. Exact
    # workflow references cross the 60/100 page boundaries.
    path = tmp_path / "coverage.xml"
    workflows = ''.join(f'<WORKFLOW NAME="W{i:03d}">' +
                        (f'<TASKINSTANCE NAME="Next" TASKTYPE="Workflow" TASKNAME="W{i+1:03d}"/>' if i < 242 else '') +
                        '</WORKFLOW>' for i in range(243))
    path.write_text('<POWERMART><REPOSITORY NAME="R"><FOLDER NAME="F">' + workflows +
                    '</FOLDER></REPOSITORY></POWERMART>', encoding="utf-8")
    index = PackageIndex(analyze_package(path), tmp_path)
    page, state, fulfill, wire = explorer()
    state.update(graph_reads=[], held_graphs=[], hold_graph=False)

    def route_graph(route):
        query = parse_qs(urlsplit(route.request.url).query)
        state["graph_reads"].append(query)
        graph = package_graph(index, kind=query.get("kind", ["workflow"])[0],
                              offset=int(query.get("offset", [0])[0]),
                              limit=int(query.get("limit", [60])[0]),
                              all_nodes=query.get("all") == ["true"],
                              chunk=urlsplit(route.request.url).path.endswith('/chunk'))
        if state["hold_graph"]:
            state["held_graphs"].append((route, graph))
        else:
            fulfill(route, graph)

    page.route("**/api/package-graph?*", route_graph)
    page.route("**/api/package-graph/chunk?*", route_graph)
    tab(page, "estate").click()
    return page, state, fulfill, wire, package_graph(index, all_nodes=True)


def test_explicit_graph_coverage_all_243_default_paging_and_no_inference(graph_coverage):
    page, state, _, wire, full = graph_coverage
    assert state["graph_reads"] == []
    expect(page.locator("#package-graph-size")).to_have_value("all")
    before = len(wire)
    page.locator("#show-package-graph").click()
    nodes = page.locator("#package-graph-area g.node")
    expect(nodes).to_have_count(243)
    expect(page.locator("#package-graph-area path.edge")).to_have_count(len(full["edges"]))
    expect(page.locator("#package-graph-summary")).to_contain_text("243 workflows")
    expect(page.locator("#package-graph-size option[value=all]")).to_have_text("All (243)")
    assert set(nodes.evaluate_all("nodes => nodes.map(n => n.dataset.nodeId)")) == {n["id"] for n in full["nodes"]}
    expect(page.locator("#package-graph-area .node-platform")).to_have_count(0)
    assert json.loads(page.locator("#package-graph-assessment").text_content())["nodes"] == full["nodes"]
    for size in ("60", "100"):
        page.locator("#package-graph-size").select_option(size)
        expect(nodes).to_have_count(int(size))
        expect(page.locator("#package-graph-prev")).to_be_disabled()
        expect(page.locator("#package-graph-next")).to_be_enabled()
        page.locator("#package-graph-next").click()
        expect(page.locator("#package-graph-area")).to_have_attribute("aria-busy", "false")
        assert state["graph_reads"][-1]["offset"] == [size]
        expect(page.locator("#package-graph-prev")).to_be_enabled()
        page.locator("#package-graph-prev").click()
        expect(page.locator("#package-graph-prev")).to_be_disabled()
    page.locator("#package-graph-size").select_option("all")
    expect(nodes).to_have_count(243)
    settle(page)
    expect(page.locator("#package-graph-prev")).to_be_disabled()
    expect(page.locator("#package-graph-next")).to_be_disabled()
    assert state["graph_reads"][-3:] == [
        {'offset': ['0'], 'limit': ['100']}, {'offset': ['100'], 'limit': ['100']}, {'offset': ['200'], 'limit': ['100']}]
    assert all(method == "GET" and path in ("/api/package-graph", "/api/package-graph/chunk") for method, path in wire[before:])
    assert json.loads(page.locator('#package-graph-assessment').text_content())['has_cycles'] is None
    # Other kinds remain an explicit selection, not full-field XML lineage.
    page.locator('#graph-view-trigger').click()
    page.locator('#graph-view-menu [data-value="source"]').click()
    expect(nodes).to_have_count(0)
    expect(page.locator("#package-graph-size-label")).to_have_text("sources shown")
    expect(page.locator("#package-graph-size option[value=all]")).to_have_text("All (0)")


def test_late_paged_graph_cannot_overwrite_explicit_all(graph_coverage):
    page, state, fulfill, _, _ = graph_coverage
    page.locator("#show-package-graph").click()
    expect(page.locator("#package-graph-area g.node")).to_have_count(243)
    state["hold_graph"] = True
    page.locator("#package-graph-size").select_option("60")
    expect(page.locator("#package-graph-area")).to_have_attribute("aria-busy", "true")
    page.wait_for_function("document.querySelector('#package-graph-summary').textContent.includes('Loading')")
    # Continue processing route callbacks until the actual request is held.
    expect(page.locator("#activity-status")).not_to_have_text("Idle")
    state["hold_graph"] = False
    page.locator("#package-graph-size").select_option("all")
    expect(page.locator("#package-graph-area g.node")).to_have_count(243)
    assert len(state["held_graphs"]) == 1
    route, graph = state["held_graphs"].pop()
    fulfill(route, graph)
    settle(page)
    expect(page.locator("#package-graph-area g.node")).to_have_count(243)
    expect(page.locator("#package-graph-summary")).to_contain_text("Complete workflow overview")
    expect(page.locator("#package-graph-next")).to_be_disabled()


def loaded(page):
    expect(selected_object(page)).to_have_attribute("data-load-state", "loaded")
    expect(page.locator("#graph-area .graph-svg")).to_have_count(1)
    settle(page)


def object_reads(state):
    return [entry for entry in state["trace"] if entry["path"].startswith("/api/object/")]


@pytest.mark.parametrize("hosted", [False, True])
@pytest.mark.parametrize("activation", ["click", "Enter", "Space"])
def test_graph_opens_actual_fields_without_requests_or_diagram_rebuild(explorer, hosted, activation):
    page, state, _, wire = explorer(hosted=hosted)
    selected_object(page).click()
    loaded(page)
    expect(page.locator("#operation-banner")).to_be_hidden()
    page.get_by_role("button", name="Zoom graph in", exact=True).click()
    page.evaluate("""() => {
      window.diagram = document.querySelector('#graph-area .graph-svg');
      window.graphScroll = document.querySelector('#graph-area .graph-scroll');
    graphScroll.style.width = '300px'; graphScroll.style.height = '160px';
    graphScroll.scrollLeft = 350;
      window.graphSnapshot = {width: diagram.getAttribute('width'), height: diagram.getAttribute('height'),
        left: graphScroll.scrollLeft, top: graphScroll.scrollTop, viewBox: diagram.getAttribute('viewBox')};
            // Playwright scrolls an actionable SVG node before pointerdown. Measure
            // the actual user-activation viewport, not the automation's pre-scroll state.
            diagram.addEventListener('pointerdown', () => {
                graphSnapshot.left = graphScroll.scrollLeft; graphSnapshot.top = graphScroll.scrollTop;
            }, {once: true, capture: true});
      window.objectJSON = document.getElementById('object-json').textContent;
      window.graphMutations = [];
      window.graphObserver = new MutationObserver(records => graphMutations.push(...records.map(r => r.type)));
      graphObserver.observe(document.getElementById('graph-area'), {childList: true, subtree: true});
    }""")
    before = len(wire)
    node = page.locator(f'#graph-area [data-node-id="{CHILD_ID}"]')
    if activation == "click":
        node.click()
    else:
        node.focus()
        node.press(activation)
    expect(page.locator("#tab-fields")).to_have_attribute("aria-selected", "true")
    expect(page.locator("#tab-fields")).to_be_focused()
    expect(page.locator("#model-fields")).to_be_visible()
    expect(page.locator("#fields-content")).to_contain_text("Selected node: Chosen source")
    expect(page.locator("#fields-content")).to_contain_text("CHOSEN_FIELD")
    expect(page.locator("#fields-content")).not_to_contain_text("ROOT_ONLY")
    settle(page)
    assert wire[before:] == []  # Includes raw POST envelopes, not just normalized GETs.
    assert page.evaluate("document.getElementById('object-json').textContent === objectJSON")
    assert not page.evaluate("graphMutations")
    page.locator("#tab-graph").click()
    assert page.evaluate("""() => diagram === document.querySelector('#graph-area .graph-svg') &&
      graphScroll === document.querySelector('#graph-area .graph-scroll') &&
      JSON.stringify(graphSnapshot) === JSON.stringify({width: diagram.getAttribute('width'),
        height: diagram.getAttribute('height'), left: graphScroll.scrollLeft, top: graphScroll.scrollTop,
        viewBox: diagram.getAttribute('viewBox')})""")
    expect(node).to_have_attribute("aria-pressed", "true")
    page.locator("#tab-json").click()
    expect(page.locator("#model-json")).to_be_visible()
    assert json.loads(page.locator("#object-json").inner_text())["node"]["id"] == ROOT_ID


@pytest.mark.parametrize("hosted", [False, True])
def test_same_loaded_inventory_selection_keeps_pending_read_and_fields(explorer, hosted):
    page, state, fulfill, wire = explorer(hosted=hosted, hold_assessment=True)
    selected_object(page).click()
    loaded(page)
    assert len(state["pending_assessment"]) == 1
    before = len(wire)
    page.locator(f'#graph-area [data-node-id="{CHILD_ID}"]').click()
    selected_object(page).click()
    settle(page)
    assert wire[before:] == []
    assert len(object_reads(state)) == 1
    expect(page.locator("#fields-content")).to_contain_text("CHOSEN_FIELD")
    expect(selected_object(page)).to_have_attribute("aria-busy", "false")
    expect(page.locator("#object-title")).not_to_contain_text("Loading")
    expect(page.locator("#operation-banner")).to_be_visible()  # The original read is genuinely pending.
    assert not page.locator("dialog[open], [inert]").count()
    route, data = state["pending_assessment"].pop()
    state["hold_assessment"] = False
    fulfill(route, data)
    expect(page.locator("#activity-status")).to_have_text("Idle")
    expect(page.locator("#operation-banner")).to_be_hidden()


@pytest.mark.parametrize("retry", ["inventory", "retry-button"])
def test_failed_selection_remains_fetchable(explorer, retry):
    page, state, _, _ = explorer()
    state["fail_object"] = True
    selected_object(page).click()
    expect(selected_object(page)).to_have_attribute("data-load-state", "failed")
    state["fail_object"] = False
    if retry == "inventory":
        selected_object(page).click()
    else:
        page.get_by_role("button", name="Retry selected object", exact=True).click()
    loaded(page)
    assert len(object_reads(state)) == 2


def test_tab_order_labels_controls_roving_focus_and_dom_keyboard_order(explorer):
    page, _, _, _ = explorer()
    selected_object(page).click()
    loaded(page)
    expect(page.locator("#model-tabs [role=tab]")).to_have_text([
        "Graph", "Fields & evidence", "English Description", "JSON Definition"])
    page.locator("#tab-graph").focus()
    for key, view in [("ArrowRight", "fields"), ("ArrowRight", "english"),
                      ("ArrowRight", "json"), ("ArrowRight", "graph"),
                      ("ArrowLeft", "json"), ("Home", "graph"), ("End", "json")]:
        page.keyboard.press(key)
        active = page.locator(f"#tab-{view}")
        expect(active).to_be_focused()
        expect(active).to_have_attribute("aria-selected", "true")
        expect(active).to_have_attribute("aria-controls", f"model-{view}")
        expect(active).to_have_attribute("tabindex", "0")
        expect(page.locator(f"#model-{view}")).to_be_visible()
        expect(page.locator("#model-tabs [aria-selected=false][tabindex='-1']")).to_have_count(3)


@pytest.mark.parametrize("change", ["package", "project", "readiness"])
def test_current_context_invalidation_does_not_reuse_same_id(explorer, change):
    page, state, _, _ = explorer(hosted=True)
    selected_object(page).click()
    loaded(page)
    if change == "package":
        state["projects"][PROJECT_A] = [member(), member(PACKAGE_B)]
        tab(page, "projects").click()
        page.locator("#refresh-projects").click()
        expect(page.locator(f'#project-package option[value="{PACKAGE_B}"]')).to_have_count(1)
        page.locator("#project-package").select_option(PACKAGE_B)
    elif change == "project":
        tab(page, "projects").click()
        page.locator("#project-select").select_option(PROJECT_B)
    else:
        state["ready"] = False
        page.locator("#refresh").click()
        expect(page.locator("#package-ready")).not_to_have_text("Package ready")
        expect(page.locator("#object-json")).to_have_text("No object selected.")
        state["ready"] = True
        page.locator("#refresh").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(page.locator("#object-json")).to_have_text("No object selected.")
    tab(page, "explorer").click()
    expect(selected_object(page)).to_have_attribute("data-load-state", "idle")
    selected_object(page).click()
    loaded(page)
    reads = object_reads(state)
    assert len(reads) == 2
    assert reads[-1]["headers"]["x-workbench-project"] == (PROJECT_B if change == "project" else PROJECT_A)
    assert reads[-1]["headers"]["x-workbench-package"] == (PACKAGE_B if change == "package" else PACKAGE_A)


def test_late_old_context_response_cannot_replace_new_same_id(explorer):
    page, state, fulfill, _ = explorer(hosted=True, hold_object=True)
    selected_object(page).click()
    expect(selected_object(page)).to_have_attribute("data-load-state", "loading")
    assert len(state["pending_object"]) == 1
    tab(page, "projects").click()
    state["projects"][PROJECT_B] = [member(PACKAGE_B)]
    page.locator("#project-select").select_option(PROJECT_B)
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    tab(page, "explorer").click()
    expect(selected_object(page)).to_have_attribute("data-load-state", "idle")
    state["hold_object"] = False
    selected_object(page).click()
    loaded(page)
    expect(page.locator("#object-title")).to_have_text("Selected workflow b")
    route, data = state["pending_object"].pop()
    fulfill(route, data)
    settle(page)
    expect(page.locator("#object-title")).to_have_text("Selected workflow b")
    assert json.loads(page.locator("#object-json").inner_text())["node"]["name"] == "Selected workflow b"
    assert len(object_reads(state)) == 2


def test_explicit_refresh_data_still_reloads_object(explorer):
    page, state, _, _ = explorer(standalone=True)
    selected_object(page).click()
    loaded(page)
    with page.expect_request("**/api/object/**"):
        page.locator("#refresh").click()
    expect(page.locator("#package-ready")).to_have_text("Package ready")
    expect(selected_object(page)).to_have_attribute("data-load-state", "loaded")
    expect(page.locator("#activity-status")).to_have_text("Idle")
    assert len(object_reads(state)) == 2