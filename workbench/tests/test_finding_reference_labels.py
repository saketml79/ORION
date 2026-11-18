"""Offline Chromium regressions for the current finding renderer and call site.

Only source slices are executed, never app startup, a workbench server, or models.
The run fixture uses the public detail response's output.findings and top-level
evidence_references contract, not an alternate implementation of the renderer.
"""
import copy
import json
from pathlib import Path
import re

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser  # noqa: F401


STATIC = Path(__file__).resolve().parents[1] / "static"
RAW_ID = "93c7e7" + "a" * 58
LOCATOR = "/POWERMART/REPOSITORY/FOLDER/MAPPING/SOURCE[@NAME='AE2E_JOB_DEP_CHK_COUNT']"


@pytest.fixture
def run_record():
    return {
        "id": "f0690f1fe2234c42950ada7d80e2c279",
        "workflow_id": "00ebb24274f4376443ff0c7b721fda8ade928a58bca2d118d42fe5f741522f38",
        "workflow_name": "wkf_WIL_NET_DIS_DUMMY",
        "scenario": "original",
        "status": "completed",
        "output": {"findings": [{
            "role": "discovery", "status": "needs_information",
            "summary": "Flow: Inspect the captured dependency counter source.\nSession: Keep the recorded source contract for review.",
            "assumptions": ["Captured XML describes the source object."],
            "blockers": [],
            "proposed_changes": ["Review the counter source before accepting a design."],
            "alternatives": [], "evidence_ids": [RAW_ID],
            "knowledge_citations": [],
        }]},
        "evidence_references": [{"id": RAW_ID, "kind": "source_definition",
                                 "label": "AE2E_JOB_DEP_CHK_COUNT", "locator": LOCATOR}],
    }


@pytest.fixture
def finding_page(browser):
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    # Explicit neighboring declarations fail loudly if production moves a slice.
    helpers = source[source.index("  const array = (value)"):
                     source.index("  const roleSkillDescription =")]
    json_helper = source[source.index("  const json = (value)"):
                         source.index("  const numberFormat =")]
    element = source[source.index("  function el(tag, className, content)"):
                     source.index("  function clear(node)")]
    renderer = source[source.index("  function findingText(node, content)"):
                      source.index("  function jsonDetails(node,")]
    list_helper = source[source.index("  function list(node, values,"):
                         source.index("  function action(label,")]
    call_sites = re.findall(r"^[ \t]*renderAgentFinding\(review, finding,.*?;", source, re.M)
    assert len(call_sites) == 1, "Expected one production finding-render call site"
    call_site = call_sites[0].strip()
    assert call_site == "renderAgentFinding(review, finding, displayFinding(finding), count, run.evidence_references);"
    assert "references = []" in renderer
    script = helpers + json_helper + element + renderer + list_helper + """
        const paragraph = (node, content) => node.append(el('p', '', content));
        const displayFinding = finding => finding.summary;
        window.renderFindingRun = (run, omitReferences = false) => {
            const before = JSON.stringify(run);
            const review = document.getElementById('review');
            review.replaceChildren();
            const count = 1;
            for (const finding of run.output.findings) {
                if (omitReferences) {
                    renderAgentFinding(review, finding, displayFinding(finding), count);
                } else {
    """ + call_site + """
                }
            }
            return {before, after: JSON.stringify(run)};
        };
    """
    context = browser.new_context(offline=True, service_workers="block")
    requests, errors, dialogs = [], [], []

    def block_request(route):
        requests.append(route.request.url)
        route.abort()

    context.route("**/*", block_request)
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))

    def dismiss_dialog(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss_dialog)
    try:
        page.set_content('<!doctype html><html><body><main id="review"></main></body></html>')
        page.add_script_tag(content=(STATIC / "redaction.js").read_text(encoding="utf-8"))
        page.add_script_tag(content=script)
        yield page
        assert requests == [], "The isolated renderer must not make any network request"
        assert errors == []
        assert dialogs == []
    finally:
        context.close()


def render(page, record, *, omit_references=False):
    original = copy.deepcopy(record)
    receipt = page.evaluate(
        "args => window.renderFindingRun(args.record, args.omit)",
        {"record": record, "omit": omit_references},
    )
    assert receipt["before"] == receipt["after"]
    assert json.loads(receipt["after"]) == original
    assert record == original
    return page.locator(".agent-finding")


def source_references(card):
    return card.locator("details.agent-finding-field").filter(
        has=card.page.get_by_text("Source references (1, technical details)", exact=True)
    )


def assert_original(card, finding):
    original = card.locator("details.agent-finding-original")
    expect(original).to_have_count(1)
    assert original.evaluate("node => node.open") is False
    expect(original.locator("pre")).to_be_hidden()
    raw_text = original.locator("pre").text_content()
    assert json.loads(raw_text) == finding
    assert raw_text == json.dumps(finding, ensure_ascii=False, indent=2)
    original.locator("summary").click()
    expect(original.locator("pre")).to_be_visible()
    assert RAW_ID in original.inner_text()
    original.locator("summary").click()
    return original


def test_readable_label_and_xml_locator_replace_raw_id_in_source_references(finding_page, run_record):
    card = render(finding_page, run_record)
    section = source_references(card)
    assert section.evaluate("node => node.open") is False
    assert RAW_ID not in card.inner_text()
    section.locator("summary").click()
    expect(section.locator("strong")).to_have_text("AE2E_JOB_DEP_CHK_COUNT")
    expect(section).to_contain_text("source definition · " + LOCATOR)
    assert RAW_ID not in section.inner_text()
    assert RAW_ID not in section.text_content()
    assert RAW_ID not in card.inner_text()
    assert_original(card, run_record["output"]["findings"][0])
    # The sole retained occurrence is inside the original technical record.
    assert card.text_content().count(RAW_ID) == 1
    assert RAW_ID not in card.inner_text()


@pytest.mark.parametrize("missing", ["absent", "unrelated", "empty-label"])
def test_missing_label_is_honest_and_does_not_leak_raw_id(finding_page, run_record, missing):
    if missing == "absent":
        run_record["evidence_references"] = []
    elif missing == "unrelated":
        run_record["evidence_references"][0]["id"] = "b" * 64
        run_record["evidence_references"][0]["label"] = "Unrelated source, not a match"
    else:
        run_record["evidence_references"][0]["label"] = ""
    card = render(finding_page, run_record)
    section = source_references(card)
    section.locator("summary").click()
    expect(section.locator("strong")).to_have_text("Source reference label unavailable")
    if missing == "empty-label":
        expect(section).to_contain_text(LOCATOR)
    else:
        expect(section).to_contain_text("This historical record does not provide a readable source label.")
        expect(section).to_contain_text("The exact reference is retained in the original technical record.")
        expect(section).not_to_contain_text("Unrelated source, not a match")
    assert RAW_ID not in section.text_content()
    assert RAW_ID not in card.inner_text()
    assert_original(card, run_record["output"]["findings"][0])


def test_legacy_four_argument_call_uses_empty_reference_default(finding_page, run_record):
    del run_record["evidence_references"]
    card = render(finding_page, run_record, omit_references=True)
    section = source_references(card)
    section.locator("summary").click()
    expect(section.locator("strong")).to_have_text("Source reference label unavailable")
    assert RAW_ID not in section.text_content()
    assert_original(card, run_record["output"]["findings"][0])


def test_missing_xml_locator_does_not_invent_a_location(finding_page, run_record):
    del run_record["evidence_references"][0]["locator"]
    card = render(finding_page, run_record)
    section = source_references(card)
    section.locator("summary").click()
    expect(section).to_contain_text("source definition · XML location not supplied")
    assert LOCATOR not in section.text_content()
    assert RAW_ID not in section.text_content()
    assert_original(card, run_record["output"]["findings"][0])


def test_reference_labels_locators_and_original_fields_are_text_not_xss(finding_page, run_record):
    attack = '<img src="https://invalid.example/x" onerror="window.findingXss=1"><svg onload="window.findingXss=2"></svg><script>window.findingXss=3</script>'
    finding = run_record["output"]["findings"][0]
    finding["summary"] = "Flow: " + attack
    for key in ("assumptions", "blockers", "proposed_changes", "alternatives", "knowledge_citations"):
        finding[key] = [attack]
    run_record["evidence_references"][0].update(label=attack, locator=attack, kind=attack)
    card = render(finding_page, run_record)
    section = source_references(card)
    section.locator("summary").click()
    expect(section.locator("strong")).to_have_text(attack)
    assert attack in section.inner_text()
    assert RAW_ID not in section.text_content()
    for disclosure in card.locator("details").all():
        disclosure.evaluate("node => node.open = true")
    expect(card.locator("img, svg, script, iframe, [onerror], [onload]")).to_have_count(0)
    assert finding_page.evaluate("() => window.findingXss === undefined")
    original = card.locator(".agent-finding-original pre")
    assert json.loads(original.text_content()) == finding
    assert original.text_content() == json.dumps(finding, ensure_ascii=False, indent=2)