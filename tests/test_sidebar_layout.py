"""Actual static layout, no backend calls or navigation behavior changes."""
import re
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

STATIC = Path(__file__).resolve().parents[1] / 'workbench/static'


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        yield browser
        browser.close()


@pytest.mark.parametrize('width', [1024, 1440])
@pytest.mark.parametrize('height', [600, 720, 768, 900])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_desktop_all_eleven_controls_fit_and_stay_below_header(browser, width, height, theme):
    page = browser.new_page(viewport={'width': width, 'height': height})
    markup = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>', '',
                    (STATIC / 'index.html').read_text(encoding='utf-8'), flags=re.S)
    page.set_content(markup)
    page.add_style_tag(content=(STATIC / 'app.css').read_text(encoding='utf-8'))
    page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
    assert page.locator('.navigation .nav-item').count() == 11
    assert page.locator('[data-page=validate] > span').inner_text() == '✓'
    for scroll in (0, 1000):
        page.evaluate('(y) => scrollTo(0, y)', scroll)
        geometry = page.evaluate('''() => {
          const sidebar = document.querySelector('.sidebar'), s = sidebar.getBoundingClientRect();
          const header = document.querySelector('.topbar').getBoundingClientRect();
          return {top: s.top, header: header.bottom, bottom: s.bottom,
            border: getComputedStyle(sidebar).borderRightWidth,
            fits: sidebar.scrollHeight <= sidebar.clientHeight + 1,
            items: [...document.querySelectorAll('.nav-item')].map(n => {
              const r = n.getBoundingClientRect(); return {top:r.top, bottom:r.bottom};
            })};
        }''')
        assert abs(geometry['top'] - geometry['header']) <= 1, geometry
        assert geometry['bottom'] <= height + 1 and geometry['fits'], geometry
        assert geometry['border'] == '0px'
        assert all(r['top'] >= geometry['top'] and r['bottom'] <= height for r in geometry['items']), geometry
    page.close()


@pytest.mark.parametrize('width', [390, 900])
def test_mobile_navigation_retains_natural_flow(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 720})
    markup = re.sub(r'<script\b[^>]*>.*?</script>|<link\b[^>]*>|<img\b[^>]*>', '',
                    (STATIC / 'index.html').read_text(encoding='utf-8'), flags=re.S)
    page.set_content(markup)
    page.add_style_tag(content=(STATIC / 'app.css').read_text(encoding='utf-8'))
    assert page.locator('.sidebar').evaluate('n => getComputedStyle(n).position') == 'static'
    assert page.locator('.navigation').evaluate('n => getComputedStyle(n).display') == 'grid'
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.close()