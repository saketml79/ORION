"""One receipt-backed notebook surface, exercised offline with production scripts."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui, CURRENT, HASH


@pytest.fixture(autouse=True)
def prose_assets(ui):
    page, _, _ = ui
    static = Path(__file__).resolve().parents[1] / "static"
    for name, content_type in [("prose-format.js", "text/javascript"), ("prose-format.css", "text/css")]:
        page.route("**/static/" + name, lambda route, request, name=name, content_type=content_type:
                   route.fulfill(content_type=content_type, path=str(static / name)))
    page.add_style_tag(url="/static/prose-format.css")
    page.add_script_tag(url="/static/prose-format.js")


@pytest.fixture
def notebook_workspace(ui, request):
    page, state, fulfill = ui
    variant = getattr(request, "param", False)
    completed = variant is True or variant in ("both", "overlap")
    duplicate_inventory = variant in ("both", "overlap")
    record = state["records"][0]
    record["id"] = "d" * 64
    record["design"]["tasks"][0]["id"] = "t_wait"
    record["design"]["edges"][0]["source"] = "t_wait"
    state["records"] = [record]
    job_id, run_id = "e" * 32, "f" * 32
    path = "notebooks/selected/t_wait.ipynb"
    record["design"]["artifact_plan"] = [{"artifact_kind": "notebook", "relative_path": path,
        "proposed_task_ids": ["t_wait"]}]
    source = '# exact café\r\nprint("one notebook surface")\r\n'
    notebook = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": [
        {"cell_type": "markdown", "metadata": {"id": "intro", "language": "markdown"}, "source": ["# Exact saved candidate"]},
        {"cell_type": "code", "metadata": {"id": "code", "language": "python"}, "source": [source],
         "execution_count": None, "outputs": []}]}, ensure_ascii=False)
    artifacts = {path: notebook, "tests/test_wait.py": "def test_wait():\n    assert True\n"}
    if duplicate_inventory:
        for key in ("t_counter", "t_child_launch", "t_email"):
            artifacts[f"notebooks/selected/{key}.ipynb"] = notebook
        record["design"]["tasks"] = [{"id": key, "label": key, "implementation_kind": "notebook_task",
            "purpose": "Capture source", "evidence_ids": ["original/task"]}
            for key in ("t_wait", "t_counter", "t_child_launch", "t_email")]
        record["design"]["edges"] = [{"source": a["id"], "target": b["id"], "evidence_ids": ["edge/evidence"]}
            for a, b in zip(record["design"]["tasks"], record["design"]["tasks"][1:])]
    manifest = {"run_id": run_id, "design_id": record["id"], "design_version": 2,
        "package_hash": HASH, "input_version": CURRENT,
        "status": "generation_complete" if completed else "blocked",
        "failure": None if completed else "writer_protocol_no_progress",
        "tasks": [{"task_id": "t_wait", "artifact_paths": [path], "status": "drafted" if completed else "blocked"}],
        "human_review_items": [], "execution_allowed": False, "deployment_allowed": False}
    if duplicate_inventory:
        manifest["tasks"] = [{"task_id": item["id"], "artifact_paths": [f'notebooks/selected/{item["id"]}.ipynb'],
            "status": "drafted"} for item in record["design"]["tasks"]]
    job = {"id": job_id, "kind": "spark_draft", "status": "completed",
        "design_id": record["id"], "design_version": 2, "package_hash": HASH,
        "workflow_id": "wf/one", "scenario": "original", "input_version": CURRENT,
        "created_at": "2026-11-14T05:10:23+00:00", "output": {"run_id": run_id, "status": manifest["status"]}}
    root = "/Workspace/Users/test/notebooks"
    tasks = [{"task_key": p.split("/")[-1][:-6], "notebook_task": {"notebook_path": root + "/" + p[:-6]},
              "depends_on": []} for p in artifacts if p.endswith(".ipynb")]
    yaml_text = json.dumps({"resources": {"jobs": {"selected": {"tasks": tasks, "parameters": []}}}})
    native_jobs = [{"job_resource": "selected", "tasks": tasks, "parameters": [], "workspace_root": root,
        "artifact_mapping": {p.split("/")[-1][:-6]: {"notebook_path": p, "source_path": p[:-6] + ".py"}
                     for p in artifacts if p.endswith(".ipynb")}}]
    bundle_id = "a" * 64
    bundle = {"bundle_id": bundle_id, "receipt": {"job_id": job_id, "design_id": record["id"],
        "design_version": 2, "package_hash": HASH, "workflow_id": "wf/one", "input_version": CURRENT,
        "run_id": run_id, "workspace_root": root}, "execution_allowed": False, "deployment_allowed": False,
        "native_jobs": native_jobs, "artifacts": {**artifacts, "migration-job.yaml": yaml_text}}
    reads = []

    def selected_design(route):
        query = parse_qs(urlsplit(route.request.url).query)
        fulfill(route, {**record, "latest_version": 2, **({"draft_jobs": [job]} if "draft_jobs" in query else {})})

    def writer_read(route):
        reads.append((urlsplit(route.request.url).path, route.request.method))
        url = urlsplit(route.request.url)
        if url.path.endswith("/spark-drafts/download"):
            selected = parse_qs(url.query)["path"][0]
            route.fulfill(content_type="application/octet-stream", body=artifacts[selected].encode("utf-8"),
                          headers={"Content-Disposition": 'attachment; filename="t_wait.ipynb"'})
        elif url.path.endswith("/spark-drafts"):
            committed = artifacts if completed else {}
            staged = artifacts if duplicate_inventory or not completed else {}
            if variant == "overlap":
                committed = {p: text for p, text in artifacts.items() if not p.endswith("t_email.ipynb")}
                staged = {p: text for p, text in artifacts.items() if not p.endswith("t_counter.ipynb")}
            fulfill(route, {"job_id": job_id, "manifest": manifest,
                "artifacts": committed, "staged_artifacts": staged,
                **({"native_job": {"yaml": yaml_text, "execution_allowed": False, "deployment_allowed": False}}
                   if completed else {})})
        elif url.path.endswith("/spark-drafts/attach"):
            assert route.request.post_data_json == {"consent": True, "artifact_path": path,
                "expected_workspace": "https://workspace.example"}
            fulfill(route, {"job_id": job_id, "artifact_path": path, "status": "attached",
                "notebook_url": "https://workspace.example/editor/notebooks/123", "execution_started": False,
                "message": "Notebook attached. Select Serverless in the Databricks editor."})
        elif url.path.endswith("/native-bundles"):
            fulfill(route, {"job_id": job_id, "bundles": [{"bundle_id": bundle_id, "status": "draft_unverified"}]})
        elif url.path.endswith("/" + bundle_id):
            fulfill(route, bundle)
        elif url.path == "/api/jobs/" + job_id:
            fulfill(route, job)
        else:
            pytest.fail("Unexpected writer request: " + url.path)

    page.route("**/api/redesigns/" + record["id"] + "*", selected_design)
    page.route("**/api/jobs/" + job_id + "**", writer_read)
    page.route("**/api/models/workspace", lambda route: fulfill(route, {"workspace": "https://workspace.example"}))
    page.context.route("https://workspace.example/editor/notebooks/123", lambda route: route.fulfill(
        content_type="text/html", body="<h1>Offline native notebook editor destination</h1>"))
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#spark-draft-status")).to_have_text("Saved notebooks loaded.")
    if completed:
        expect(page.locator("#saved-design-graph h4").first).to_have_text("Databricks task view")
    yield page, state, reads, path, notebook, source
    assert all(method == "GET" or method == "POST" and url.endswith(("/spark-drafts/attach", "/spark-drafts/edit-preview", "/spark-drafts/edit-return", "/spark-drafts/edit-cleanup")) for url, method in reads)
    assert all(item["method"] == "GET" for item in state["requests"])


@pytest.mark.parametrize("notebook_workspace", [False, True, "both"], indirect=True,
                         ids=["staged", "native-bundle", "duplicate-inventory"])
def test_notebook_review_download_run_exist_only_in_selected_task(notebook_workspace):
    page, _, _, path, notebook, source = notebook_workspace
    workspace = page.get_by_role("region", name="Notebook review, download and run", exact=True)
    expect(workspace).to_have_count(1)
    expect(workspace.locator(".workflow-native-canvas")).to_have_count(1)
    expect(page.locator("#spark-draft-output .notebook-cell")).to_have_count(0)
    expect(page.locator("#spark-draft-output .workflow-native-canvas")).to_have_count(0)
    plan_link = workspace.get_by_role("button", name=f"Open task Ingest actual for planned notebook {path}", exact=True)
    expect(plan_link).to_be_visible()
    plan_link.click()
    inspector = workspace.get_by_role("region", name="Selected task details", exact=True)
    expect(inspector).to_contain_text("What this task does")
    workspace.locator('[data-task-key="t_wait"]').click()
    expect(inspector).to_contain_text("What this task does")
    assert inspector.inner_text().count("Capture source") == 1
    expect(inspector.locator('.task-business-description li')).to_have_text(['Capture source'])
    expect(inspector.get_by_text('This notebook’s parameters', exact=True)).to_be_visible()
    expect(inspector).not_to_contain_text('Recovery key')
    expect(inspector.locator(".notebook-cell")).to_have_count(2)
    expect(page.locator("#page-proposals .notebook-cell")).to_have_count(2)
    assert inspector.locator("pre.proposal-code").nth(1).text_content() == source
    download = page.get_by_role("button", name="Download " + path, exact=True)
    expect(download).to_have_count(1)
    with page.expect_download() as downloaded:
        download.click()
    assert downloaded.value.path().read_bytes() == notebook.encode("utf-8")
    expect(inspector.get_by_role("button", name="Run through cell 2 on serverless", exact=True)).to_have_count(0)
    inspector.get_by_role("button", name="Open in Databricks", exact=True).click()
    expect(inspector.get_by_role("link", name="Open attached notebook")).to_have_attribute(
        "href", "https://workspace.example/editor/notebooks/123")
    expect(inspector).to_contain_text("Select Serverless in the Databricks editor")
    # Expanding every auxiliary disclosure must never produce a second notebook.
    page.locator("#saved-design-artifacts details").evaluate_all("nodes => nodes.forEach(node => node.open = true)")
    expect(page.locator("#page-proposals .notebook-cell")).to_have_count(2)
    expect(page.locator("#spark-draft-output .notebook-cell")).to_have_count(0)
    expect(workspace.locator("#spark-draft-saved-run")).to_have_count(1)


def test_notebook_artifact_disclosures_and_rows_are_flat(notebook_workspace):
        page, _, _, path, _, _ = notebook_workspace
        page.get_by_text("Saved files and writer report", exact=True).click()
        page.locator("#saved-design-artifacts details").evaluate_all("nodes => nodes.forEach(item => item.open = true)")
        rows = page.locator("#saved-design-artifacts .notebook-plan-item")
        expect(rows).to_have_count(1)
        expect(rows.get_by_role("button", name=f"Open task Ingest actual for planned notebook {path}", exact=True)).to_have_count(1)
        geometry = page.locator("#saved-design-artifacts").evaluate("""root => {
            const row = root.querySelector('.notebook-plan-item');
            const section = root.querySelector('.notebook-plan-section');
            const nested = [...root.querySelectorAll('.review-disclosure .review-disclosure')];
            return {
                rowBorder: getComputedStyle(row).borderTopStyle,
                rowRadius: getComputedStyle(row).borderRadius,
                sectionBorder: getComputedStyle(section).borderTopStyle,
                nestedRadii: nested.map(item => getComputedStyle(item).borderRadius),
                nestedBodyInsets: nested.map(item => getComputedStyle(item.querySelector('.review-disclosure-body')).borderInlineStartWidth)
            };
        }""")
        assert geometry["rowBorder"] == "none"
        assert geometry["rowRadius"] == "0px"
        assert geometry["sectionBorder"] == "none"
        assert all(radius == "0px" for radius in geometry["nestedRadii"])
        assert all(width == "0px" for width in geometry["nestedBodyInsets"])


def test_writer_history_is_not_a_user_selection_or_notebook_blocker(notebook_workspace):
    page, _, _, _, _, _ = notebook_workspace
    summary = page.locator("#saved-design-code-state")
    expect(summary).to_contain_text("1 generated notebook(s) available")
    expect(summary).not_to_contain_text("writer_protocol_no_progress")
    expect(page.locator("#spark-draft-saved-run")).to_be_hidden()
    expect(page.locator("#spark-draft-open-saved")).to_be_hidden()
    expect(page.locator("#spark-draft-find")).to_have_text("Refresh notebooks")
    assert page.locator("#spark-draft-find").bounding_box()["width"] < 240
    expect(page.get_by_text("Some task results are incomplete", exact=False)).to_have_count(0)
    expect(page.get_by_text("Generation diagnostics (technical)", exact=True)).to_be_hidden()


@pytest.mark.parametrize("notebook_workspace", ["both", "overlap"], indirect=True)
def test_notebook_count_is_unique_path_union_and_guidance_is_below_graph(notebook_workspace):
    page, _, _, _, _, _ = notebook_workspace
    summary = page.locator("#saved-design-code-state")
    expect(summary).to_contain_text("4 generated notebook(s) available")
    expect(summary).not_to_contain_text("8 generated")
    expect(summary).to_contain_text("Select a task above")
    expect(summary).not_to_contain_text("writer")
    expect(summary.locator(".prose-focus").first).to_be_visible()
    assert summary.evaluate("node => node.previousElementSibling.id") == "saved-design-graph"
    assert summary.get_attribute("role") == "status"
    expect(page.locator("#spark-draft-status .prose-focus")).to_have_count(0)


@pytest.mark.parametrize('theme,width', [('light', 1440), ('dark', 1440), ('light', 390), ('dark', 390)])
def test_notebook_actions_have_gaps_and_wrapped_buttons(notebook_workspace, theme, width):
    page, _, _, _, _, _ = notebook_workspace
    page.set_viewport_size({'width': width, 'height': 1000})
    page.locator('html').evaluate('(element, theme) => element.dataset.theme = theme', theme)
    page.locator('#saved-design-graph [data-task-key="t_wait"]').click()
    row = page.get_by_role('group', name='Notebook actions', exact=True)
    expect(row.locator('button')).to_have_count(2)
    geometry = row.evaluate('''element => {
        const [a,b] = [...element.querySelectorAll('button')].map(n => n.getBoundingClientRect());
        const note = element.parentElement.querySelector('.notebook-attachment-note').getBoundingClientRect();
        return {gap: b.top >= a.bottom ? b.top-a.bottom : b.left-a.right,
            noteGap: note.top-Math.max(a.bottom,b.bottom), right: Math.max(a.right,b.right), viewport:innerWidth};
    }''')
    assert geometry['gap'] >= 11
    assert geometry['noteGap'] >= 15
    assert geometry['right'] <= geometry['viewport']


@pytest.mark.parametrize('theme,width', [('light', 1440), ('dark', 1440), ('light', 390), ('dark', 390)])
@pytest.mark.parametrize('hidden', [False, True], ids=['visible', 'hidden-first-open'])
def test_graph_initially_fits_container_and_keeps_manual_zoom(ui, theme, width, hidden):
    page, state, _ = ui
    page.set_viewport_size({'width':width,'height':1000})
    page.locator('html').evaluate('(node, theme) => node.dataset.theme = theme', theme)
    design = state['records'][0]['design']
    design['tasks'] = [{'id': f'task_{i}', 'label': f'Task {i}', 'implementation_kind': 'notebook_task',
                        'purpose': 'Capture source', 'evidence_ids': ['original/task']} for i in range(5)]
    design['edges'] = [{'source': f'task_{i}', 'target': f'task_{i+1}', 'evidence_ids': ['edge/evidence']}
                       for i in range(4)]
    # Exercise both canvas padding and the real optional-example SVG sizing rule.
    style = page.add_style_tag(content='#saved-design-graph .workflow-native-canvas {padding: 13px 17px;} '
        '#saved-design-graph .workflow-native-canvas svg {max-width: 760px;}'
        + ('#saved-design-graph {display:none;}' if hidden else ''))
    page.locator('[data-page="proposals"]').click()
    canvas = page.locator('#saved-design-graph .workflow-native-canvas')
    expect(canvas).to_have_count(1)
    toolbar = page.locator('#saved-design-graph .workflow-native-toolbar')
    if hidden:
        expect(toolbar.locator('output')).to_have_text('Fitting…')
        style.evaluate("node => node.sheet.deleteRule(node.sheet.cssRules.length - 1)")
    expect(canvas).to_be_visible()
    page.wait_for_function('''() => {
        const canvas=document.querySelector('#saved-design-graph .workflow-native-canvas');
        if (!canvas) return false;
        const style=getComputedStyle(canvas), available=canvas.clientWidth-parseFloat(style.paddingLeft)-parseFloat(style.paddingRight);
        return Math.abs(canvas.querySelector('svg').getBoundingClientRect().width-available)<1;
    }''')
    geometry = canvas.evaluate('''node => {
        const bounds=node.getBoundingClientRect(), svg=node.querySelector('svg').getBoundingClientRect();
        return {left:bounds.left, right:bounds.right, svgRight:svg.right, viewport:innerWidth,
            overflow:node.scrollWidth-node.clientWidth,
            cards:[...node.querySelectorAll('.workflow-native-card')].map(card=>{
                const b=card.getBoundingClientRect();return {left:b.left,right:b.right};})};
    }''')
    assert len(geometry['cards']) == 5
    assert geometry['overflow'] <= 1
    assert geometry['svgRight'] <= geometry['right'] <= geometry['viewport']
    assert all(geometry['left'] <= card['left'] < card['right'] <= geometry['svgRight']
               for card in geometry['cards'])
    toolbar.get_by_role('button', name='Reset zoom', exact=True).click()
    expect(toolbar.locator('output')).to_have_text('100%')
    assert canvas.locator('svg').bounding_box()['width'] == 1630
    page.set_viewport_size({'width':width-20,'height':1000})
    expect(toolbar.locator('output')).to_have_text('100%')
    toolbar.get_by_role('button', name='Fit to view', exact=True).click()
    assert canvas.evaluate("node=>node.scrollWidth-node.clientWidth <= 1")


def test_pending_fit_disconnects_replaced_graph_and_cannot_override_manual_zoom(ui):
    page, _, _ = ui
    page.evaluate('''() => {
        const Native=window.ResizeObserver;
        window.graphObservers=[];
        window.ResizeObserver=class extends Native {
            constructor(callback) {
                super(callback);
                this.entry={observer:this, callback, active:false};
                window.graphObservers.push(this.entry);
            }
            observe(node) {this.entry.active=true; super.observe(node);}
            disconnect() {this.entry.active=false; super.disconnect();}
        };
    }''')
    style = page.add_style_tag(content='#saved-design-graph .workflow-native-canvas {display:none;}')
    page.locator('[data-page="proposals"]').click()
    expect(page.locator('#saved-design-graph .workflow-native-toolbar output')).to_have_text('Fitting…')
    page.locator('#redesign-version').select_option(json.dumps(['real-redesign', 1], separators=(',', ':')))
    expect(page.locator('#saved-design-graph')).to_have_attribute('data-design-version', '1')
    page.wait_for_function('''() => graphObservers.length === 2 &&
        !graphObservers[0].active && graphObservers[1].active''')
    toolbar = page.locator('#saved-design-graph .workflow-native-toolbar')
    toolbar.get_by_role('button', name='Reset zoom', exact=True).click()
    expect(toolbar.locator('output')).to_have_text('100%')
    assert page.evaluate('() => graphObservers.every(entry => !entry.active)')
    style.evaluate('node => node.remove()')
    expect(page.locator('#saved-design-graph .workflow-native-canvas')).to_be_visible()
    # Even a late queued callback must not restore automatic fitting after Reset.
    page.evaluate('() => graphObservers.forEach(entry => entry.callback([], entry.observer))')
    expect(toolbar.locator('output')).to_have_text('100%')
    assert page.locator('#saved-design-graph svg').bounding_box()['width'] == 670
    toolbar.get_by_role('button', name='Fit to view', exact=True).click()
    assert page.locator('#saved-design-graph .workflow-native-canvas').evaluate(
        'node => node.scrollWidth-node.clientWidth <= 1')


def test_agent_description_sentence_points_preserve_exact_business_text(ui):
    page, state, _ = ui
    text = 'Wait for the dependency. This timer has no tables. Do not invent an output table.'
    state['records'][0]['design']['tasks'][0]['purpose'] = text
    page.locator('[data-page="proposals"]').click()
    page.locator('#saved-design-graph [data-task-key="ingest"]').click()
    bullets = page.locator('.workflow-task-inspector .task-business-description li')
    expect(bullets).to_have_text(['Wait for the dependency.', 'This timer has no tables.', 'Do not invent an output table.'])
    assert ' '.join(bullets.all_text_contents()) == text


def test_task_before_receipt_load_does_not_claim_notebook_is_missing(ui):
    page, _, _ = ui
    page.locator('[data-page="proposals"]').click()
    expect(page.locator("#saved-design-graph")).to_be_visible()
    page.locator('#saved-design-graph [data-task-key="ingest"]').click()
    expect(page.locator(".workflow-task-inspector")).to_contain_text("Saved notebook code has not been loaded")
    expect(page.locator(".workflow-task-inspector")).not_to_contain_text("No notebook file was saved")


@pytest.mark.parametrize("theme,width", [("light", 1440), ("dark", 1440), ("light", 390), ("dark", 390)])
def test_reference_sections_show_indented_parent_child_hierarchy(notebook_workspace, theme, width):
    page, _, _, _, _, _ = notebook_workspace
    page.set_viewport_size({"width": width, "height": 1000})
    page.locator("html").evaluate("(element, theme) => element.dataset.theme = theme", theme)
    parent = page.locator("#redesign-review details.review-disclosure").filter(
        has=page.get_by_text("Source reference and optional tools", exact=True)).first
    parent.locator(":scope > summary").click()
    body = parent.locator(":scope > .review-disclosure-body")
    expect(body).to_contain_text("Background source information")
    child = body.locator("details.review-disclosure").filter(
        has=page.get_by_text("Source workflow explanation and XML evidence", exact=True)).first
    child.locator(":scope > summary").click()
    child_body = child.locator(":scope > .review-disclosure-body")
    expect(child_body).to_contain_text("Exact XML control flow")
    geometry = child.evaluate("""element => {
        const parent = element.closest('.review-disclosure-body').getBoundingClientRect();
        const header = element.querySelector('summary').getBoundingClientRect();
        const body = element.querySelector('.review-disclosure-body').getBoundingClientRect();
        return {parentLeft: parent.left, headerLeft: header.left, bodyLeft: body.left,
            bodyRight: body.right, viewport: innerWidth,
            border: getComputedStyle(element.querySelector('.review-disclosure-body')).borderInlineStartWidth};
    }""")
    assert geometry["headerLeft"] > geometry["parentLeft"]
    assert geometry["bodyLeft"] > geometry["headerLeft"]
    assert geometry["bodyRight"] <= geometry["viewport"]
    assert geometry["border"] == "2px"
    expect(page.get_by_text("Saved files and writer report", exact=True)).to_be_visible()
    expect(page.get_by_text("Design explanation and feedback", exact=True)).to_be_visible()
    expect(page.locator("#spark-draft-output .notebook-cell")).to_have_count(0)