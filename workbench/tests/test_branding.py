"""ORION identity contracts, no backend or model invocation."""
from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET

from playwright.sync_api import expect, sync_playwright


STATIC = Path(__file__).resolve().parents[1] / "static"


def test_original_svg_logo_is_valid_portable_and_has_accessible_description():
    root = ET.parse(STATIC / "orion-mark.svg").getroot()
    assert root.attrib["viewBox"] == "0 0 64 64"
    ns = {"svg": "http://www.w3.org/2000/svg"}
    assert "ORION" in root.find("svg:title", ns).text
    assert root.find("svg:desc", ns).text
    assert not root.findall(".//svg:script", ns)
    assert not root.findall(".//svg:image", ns)


def test_brand_acronym_favicon_and_navigation_title():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'aria-label="ORION home"' in html
    assert 'class="orion-descriptor">Informatica Modernization</span>' in html
    assert 'ORION | Informatica Modernization Workbench' in html
    assert "Optimized Refactoring of Informatica Operations to Next-gen (Databricks) Lakehouse" in html
    assert 'class="product-signature-word"><strong>O</strong>ptimized' in html
    assert 'class="product-signature-word"><strong>R</strong>efactoring' in html
    assert 'class="product-signature-word"><strong>I</strong>nformatica' in html
    assert 'class="product-signature-word"><strong>O</strong>perations' in html
    assert 'class="product-signature-word"><strong>N</strong>ext-gen' in html
    css = (STATIC / 'app.css').read_text(encoding='utf-8')
    descriptor_style = css.split('.orion-descriptor {', 1)[1].split('}', 1)[0]
    assert 'Bricolage Grotesque' in descriptor_style and 'font: 600 12px/1.2' in descriptor_style
    assert ':root { --control-fill: #fff;' in css
    assert ':root[data-theme="dark"] { --control-fill: #344665;' in css
    assert 'border: 0 !important; border-radius: 10px; background: var(--control-fill) !important' in css
    assert 'box-shadow: var(--control-shadow) !important;' in css
    graph_trigger = css.split('#graph-view-trigger {', 2)[2].split('}', 1)[0]
    assert 'border: 0 !important' in graph_trigger and 'var(--control-shadow)' in graph_trigger
    platform_style = css.split('.product-signature-platform {', 1)[1].split('}', 1)[0]
    assert 'font-size: 1.08em;' in platform_style and 'font-weight: 700;' in platform_style
    assert html.count('href="/static/orion-mark.svg"') == 1
    assert 'src="/static/orion-mark.svg"' in html
    assert "| ORION`" in js
    assert "Lakeflow Studio" not in html
    assert "Lakeflow Studio" not in js
    # Existing saved preferences remain compatible; rebranding must not clear state.
    assert "lakeflow-studio-theme" in js


def test_package_and_workflow_have_distinct_icons():
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (mark) clear(mark).append(icon('package'))" in js
    assert "workflow: 'graph'" in js
    assert "package: 'M12 3 3 8" in js


def test_official_databricks_assets_are_exact_transparent_first_party_variants():
    provenance = (STATIC.parents[1] / "docs" / "brand-assets.md").read_text(encoding="utf-8")
    assert "https://www.databricks.com" in provenance
    assert "2026-10-30" in provenance
    for filename, digest, text_color in (
        ("databricks-official.svg", "8c6b1fbc7609d5246f85629ca4bf729585694e030245676fa49c309f9673f42f", "#000"),
        ("databricks-official-dark.svg", "216bdd6d49257083e697b39834766f69e29de0285c73c199d1f7d2a9a044899f", "#fff"),
    ):
        raw = (STATIC / filename).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest
        assert digest in provenance
        root = ET.fromstring(raw)
        assert root.attrib["width"] == "132"
        assert root.attrib["height"] == "22"
        assert root.attrib["viewBox"] == "0 0 132 22"
        assert root.attrib["fill"] == "none"
        assert {node.tag.rsplit("}", 1)[-1] for node in root.iter()} == {"svg", "path"}
        assert {node.attrib["fill"] for node in root} == {"#EE3D2C", text_color}


def test_databricks_logo_is_transparent_theme_correct_and_announced_once_in_chromium():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()

        def serve(route):
            path = route.request.url.removeprefix("http://branding.test")
            if path == "/":
                route.fulfill(body=html, content_type="text/html")
            elif path in (
                "/static/app.css", "/static/databricks-official.svg",
                "/static/databricks-official-dark.svg",
            ):
                route.fulfill(
                    body=(STATIC / path.rsplit("/", 1)[-1]).read_bytes(),
                    content_type="text/css" if path.endswith(".css") else "image/svg+xml",
                )
            else:
                route.abort()  # No application scripts, backend, fonts or external network.

        page.route("**/*", serve)
        try:
            page.goto("http://branding.test/")
            badge = page.get_by_role("img", name="Databricks modernization target", exact=True)
            light = badge.locator(".databricks-official-logo-light")
            dark = badge.locator(".databricks-official-logo-dark")
            for theme in (None, "light", "dark", "light"):
                page.evaluate("""theme => {
                    if (theme === null) document.documentElement.removeAttribute('data-theme');
                    else document.documentElement.dataset.theme = theme;
                }""", theme)
                expect(dark if theme == "dark" else light).to_be_visible()
                expect(light if theme == "dark" else dark).to_be_hidden()
                expect(badge).to_have_count(1)
                assert badge.aria_snapshot() == '- img "Databricks modernization target"'
                expect(badge).to_have_css("background-color", "rgba(0, 0, 0, 0)")
                expect(badge).to_have_css("background-image", "none")
                expect(badge).to_have_css("border-top-style", "none")
                expect(badge).to_have_css("filter", "none")
                for image in (light, dark):
                    expect(image).to_have_attribute("alt", "")
                    expect(image).to_have_attribute("aria-hidden", "true")
                    expect(image).to_have_css("filter", "none")
                    assert image.evaluate("img => img.complete && img.naturalWidth === 132 && img.naturalHeight === 22")
        finally:
            browser.close()