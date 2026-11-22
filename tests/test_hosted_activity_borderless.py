"""Visual contracts for the hosted progress panel and next-action halo."""
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / 'workbench' / 'static'


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [390, 1440])
def test_activity_has_no_panel_or_details_borders_and_keeps_focus(theme, width):
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page(viewport={'width': width, 'height': 850})
        page.set_content('''<main><section class="page"><aside id="orion-hosted-intake-activity" class="activity-status is-active"><div class="hosted-activity-header"><strong>Current request</strong><button class="hosted-activity-window-control">×</button></div><div class="hosted-activity-content"><p>Preparing saved workspace</p><progress value="21" max="28"></progress><details><summary>Operation details</summary></details></div></aside><aside id="orion-hosted-intake-summary">Current request</aside></section></main>''')
        page.add_style_tag(path=str(STATIC / 'app.css'))
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        for selector in ('#orion-hosted-intake-activity', '#orion-hosted-intake-summary', '.hosted-activity-content details'):
            borders = page.locator(selector).evaluate("el => { const s = getComputedStyle(el); return ['Top','Right','Bottom','Left'].map(side => s['border' + side + 'Width']); }")
            assert borders == ['0px'] * 4
        assert page.locator('progress').is_visible()
        button = page.locator('.hosted-activity-window-control')
        button.focus()
        assert button.evaluate("el => getComputedStyle(el).outlineWidth") == '2px'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


def test_pulse_styles_retain_animation_and_reduced_motion():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()
        page.set_content('<main><section class="page"><button class="orion-next-action">Next</button></section></main>')
        page.add_style_tag(path=str(STATIC / 'next-action.css'))
        target = page.locator('button')
        assert target.evaluate('el => getComputedStyle(el).animationName') == 'orion-next-step'
        page.emulate_media(reduced_motion='reduce')
        assert target.evaluate('el => getComputedStyle(el).animationName') == 'none'
        assert target.evaluate('el => getComputedStyle(el).filter') != 'none'
        browser.close()
