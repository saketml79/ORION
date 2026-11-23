from pathlib import Path


def test_proposal_dropdowns_have_consistent_scoped_small_type():
    css = (Path(__file__).resolve().parents[1] / 'static' / 'app.css').read_text(encoding='utf-8')
    rule = css.split('#page-proposals #redesign-review select,', 1)[1].split('}', 1)[0]
    for selector in ('#page-proposals select,', '#page-proposals select option,',
                     '#page-proposals .context-option,',
                     '#page-proposals .context-option[aria-selected="true"]'):
        assert selector in rule
    assert 'font-size: 12px;' in rule
    assert 'font-weight: 400;' in rule