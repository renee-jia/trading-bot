"""Report readability and HTML/email conversion regressions."""
from pathlib import Path
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from report_format import format_markdown, render_html
import report_generator


def test_external_text_is_escaped_and_links_lists_tables_render():
    md = '''# Report

## Signals

<script>alert(1)</script>

[Unsafe](javascript:alert(1)) and [Source](https://example.com)

- **First**
- Second

| Name | Value |
|:---|---:|
| A & B | <18 |
'''
    soup = BeautifulSoup(render_html(md), 'html.parser')
    assert not soup.select('script')
    assert not soup.select('a[href^="javascript:"]')
    assert soup.select_one('a[href="https://example.com"]')
    assert len(soup.select('main > ul > li')) == 2
    assert len(soup.select('table')) == 1
    assert soup.select_one('tbody td:last-child').text == '<18'


def test_web_details_and_navigation_email_expanded():
    md = '# Report\n\n## Detailed Analysis\n\n### AAPL - Apple\n\nBody\n\n## Disclaimer\n\nEnd\n'
    web = BeautifulSoup(render_html(md), 'html.parser')
    email = BeautifulSoup(render_html(md, email=True), 'html.parser')
    assert web.select_one('details summary').text == 'AAPL - Apple'
    assert not email.select('details')
    assert 'Body' in email.text
    for link in web.select('nav a'):
        assert web.find(id=link['href'][1:])


def test_summary_first_and_methodology_after_decisions():
    md = '# Report\n\n## Scoring Methodology\n\nMethod\n\n## General\n\nGeneral\n\n## Executive Summary\n\nSummary\n\n## 今日建议买入\n\nBuy\n'
    formatted = format_markdown(md)
    assert formatted.index('## Executive Summary') < formatted.index('## General')
    assert formatted.index('## 今日建议买入') < formatted.index('## Scoring Methodology')
    assert format_markdown(formatted) == formatted


def test_low_rank_does_not_label_buy_stock_as_sell():
    row = {'ticker':'AAPL','score_result':{'score':65,'grade':'B','recommendation':'Buy',
           'components':{k:{'score':65} for k in ('technical','trend','alpha','sentiment')}}}
    section = report_generator._build_bottom_20([row])
    assert 'Sell/Avoid' not in section and '| Buy |' in section
    assert '相对后列' in section
