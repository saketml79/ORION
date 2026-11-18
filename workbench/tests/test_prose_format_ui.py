"""Exact-text presentation checks using real app markup and offline Chromium."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from workbench.tests.test_redesign_ui import browser, ui


@pytest.mark.parametrize('theme,width', [('light',1440), ('dark',1440), ('light',390), ('dark',390)])
def test_agent_intro_and_next_hint_use_available_width_without_changing_text(ui, theme, width):
    page, _, _ = ui
    static = Path(__file__).resolve().parents[1] / 'static'
    page.route('**/static/next-action.css', lambda route: route.fulfill(
        content_type='text/css', path=str(static/'next-action.css')))
    page.set_viewport_size({'width':width, 'height':1000})
    page.locator('html').evaluate('(node,theme)=>node.dataset.theme=theme', theme)
    page.add_style_tag(url='/static/next-action.css')
    page.locator('[data-page="agents"]').click()
    intro = page.locator('#page-agents > .page-heading p')
    original = intro.text_content()
    markup = intro.inner_html()
    hint_text = ('Suggested next step: Connect workspace models: '
                 'https://adb-1866518241053589.9.azuredatabricks.net. '
                 'Discover endpoints first. No inference runs.')
    page.locator('#page-agents').evaluate('''(panel,text) => {
        const hint=document.createElement('p'); hint.className='next-action-hint';
        hint.textContent=text; panel.querySelector('.page-heading').after(hint);
    }''', hint_text)
    expect(intro).to_be_visible()
    dimensions = page.locator('#page-agents').evaluate('''panel => {
        const heading=panel.querySelector('.page-heading');
        const intro=heading.querySelector('p'); const hint=panel.querySelector('.next-action-hint');
        return {intro:intro.getBoundingClientRect().width, heading:heading.getBoundingClientRect().width,
            hint:hint.getBoundingClientRect().width, panel:panel.getBoundingClientRect().width,
            overflow:document.documentElement.scrollWidth>innerWidth+2,
            inline:[...intro.querySelectorAll('strong')].every(x=>getComputedStyle(x).display==='inline')};
    }''')
    assert dimensions['intro'] >= dimensions['heading'] - 2
    assert dimensions['hint'] >= dimensions['panel'] - 2
    assert dimensions['inline'] and not dimensions['overflow']
    assert intro.text_content() == original and intro.inner_html() == markup
    expect(page.locator('#page-agents .next-action-hint')).to_have_text(hint_text)


@pytest.mark.parametrize('theme,width', [('light',1440), ('dark',1440), ('light',390), ('dark',390)])
def test_all_pages_prose_emphasis_preserves_text_and_excludes_code_controls(ui, theme, width):
    page, state, _ = ui
    static = Path(__file__).resolve().parents[1] / 'static'
    page.route('**/static/prose-format.js', lambda route: route.fulfill(content_type='text/javascript', path=str(static/'prose-format.js')))
    page.route('**/static/prose-format.css', lambda route: route.fulfill(content_type='text/css', path=str(static/'prose-format.css')))
    page.set_viewport_size({'width':width,'height':1000})
    page.locator('html').evaluate('(node,theme)=>node.dataset.theme=theme', theme)
    text = ('Source reads from Unity Catalog. MERGE updates the destination while preserving NULL values. '
            'Recovery uses the workflow parameters and a 300 seconds timer. ' * 3)
    page.evaluate('''text => {
        for(const panel of document.querySelectorAll('main .page')) {
            const p=document.createElement('p');p.className='format-test';p.textContent=text;panel.append(p);
            const pre=document.createElement('pre');pre.className='format-code-test';pre.textContent='MERGE source NULL <script>';panel.append(pre);
            const input=document.createElement('input');input.value='source MERGE';input.className='format-input-test';panel.append(input);
            const status=document.createElement('p');status.setAttribute('role','status');status.className='format-status-test';status.textContent='Source status';panel.append(status);
        }
    }''', text)
    page.add_style_tag(url='/static/prose-format.css')
    page.add_script_tag(url='/static/prose-format.js')
    expect(page.locator('.format-test').first.locator('.prose-focus').first).to_have_text('Source')
    result = page.evaluate('''() => [...document.querySelectorAll('.format-test')].map(p=>({
        text:p.textContent,focus:p.querySelectorAll('.prose-focus').length,
        sentences:p.querySelectorAll('.prose-sentence').length,font:getComputedStyle(p).fontSize,
        code:p.parentElement.querySelector('.format-code-test').textContent,
        codeMarkup:p.parentElement.querySelector('.format-code-test').children.length,
        input:p.parentElement.querySelector('.format-input-test').value,
        statusMarkup:p.parentElement.querySelector('.format-status-test').children.length}))''')
    assert len(result) >= 8
    for item in result:
        assert item['text'] == text
        assert item['focus'] > 5 and item['sentences'] == 9
        assert item['font'] == '13px'
        assert item['code'] == 'MERGE source NULL <script>' and item['codeMarkup'] == 0
        assert item['input'] == 'source MERGE' and item['statusMarkup'] == 0
    # Async service rerenders retain their new text and get the same formatting.
    page.locator('.format-test').first.evaluate("node=>node.textContent='Destination changed. INSERT preserves source rows.'")
    expect(page.locator('.format-test').first.locator('.prose-focus').first).to_have_text('Destination')
    expect(page.locator('.format-test').first).to_have_text('Destination changed. INSERT preserves source rows.')
    assert all(request['method']=='GET' for request in state['requests'])


@pytest.mark.parametrize('theme,width', [('light',1440), ('dark',1440), ('light',390), ('dark',390)])
def test_static_saved_status_focus_is_explicit_and_live_nodes_are_untouched(ui, theme, width):
    page, state, _ = ui
    static = Path(__file__).resolve().parents[1] / 'static'
    page.route('**/static/prose-format.js', lambda route: route.fulfill(
        content_type='text/javascript', path=str(static/'prose-format.js')))
    page.route('**/static/prose-format.css', lambda route: route.fulfill(
        content_type='text/css', path=str(static/'prose-format.css')))
    page.set_viewport_size({'width':width,'height':1000})
    page.locator('html').evaluate('(node,theme)=>node.dataset.theme=theme', theme)
    text = '4 generated notebooks for this workflow. Select a task above. Runtime correctness and Informatica parity are unverified. '
    page.evaluate('''text => {
        const panel=document.querySelector('#page-proposals');
        const saved=document.createElement('p'); saved.id='saved-design-code-state';
        saved.setAttribute('role','status'); saved.setAttribute('aria-live','polite');
        saved.textContent=text.repeat(4); panel.append(saved);
        window.statusIdentity=saved; window.statusClicks=0;
        saved.addEventListener('click',()=>window.statusClicks++);
        window.liveNodes=[];
        for (const attrs of [{role:'status'}, {role:'log'}, {'aria-live':'polite'}]) {
            const p=document.createElement('p'); p.className='live-preserve-test';
            for (const [name,value] of Object.entries(attrs)) p.setAttribute(name,value);
            const span=document.createElement('span'); span.textContent='Source timer 300 seconds';
            span.addEventListener('click',()=>window.statusClicks++);
            p.append(span); panel.append(p); window.liveNodes.push({p,span});
        }
    }''', text)
    page.add_style_tag(url='/static/prose-format.css')
    page.add_script_tag(url='/static/prose-format.js')
    saved = page.locator('#saved-design-code-state')
    expect(saved.locator('.prose-focus').first).to_have_text('notebooks')
    assert saved.text_content() == text * 4
    # Long static status subtext receives focus only, not sentence/live-node restructuring.
    expect(saved.locator('.prose-sentence')).to_have_count(0)
    page.evaluate('''() => {
        statusIdentity.textContent='Source workflow uses Unity Catalog. MERGE preserves NULL.';
        liveNodes.forEach(({span})=>span.textContent='Source timer 299 seconds');
    }''')
    expect(saved.locator('.prose-focus')).to_have_count(0)
    page.evaluate('() => orionProseFormat.focusStatic(statusIdentity)')
    expect(saved.locator('.prose-focus').first).to_have_text('Source')
    expect(saved).to_have_text('Source workflow uses Unity Catalog. MERGE preserves NULL.')
    assert page.evaluate('''() => {
        statusIdentity.click(); liveNodes.forEach(({span})=>span.click());
        return statusIdentity===document.querySelector('#saved-design-code-state') && statusClicks===4 &&
            liveNodes.every(({p,span})=>p.firstChild===span && p.children.length===1 &&
                span.textContent==='Source timer 299 seconds' && !p.querySelector('.prose-focus'));
    }''')
    expect(saved).to_have_attribute('role', 'status')
    expect(saved).to_have_attribute('aria-live', 'polite')
    # An allowlisted container with an action is not plain static subtext.
    page.evaluate('''() => {
        const button=document.createElement('button'); button.textContent='Download source';
        statusIdentity.replaceChildren(button); window.statusAction=button;
        orionProseFormat.focusStatic(statusIdentity);
    }''')
    assert page.evaluate('() => statusIdentity.firstChild===statusAction && statusAction.textContent==="Download source"')
    assert all(request['method']=='GET' for request in state['requests'])