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


def test_soft_line_breaks_stay_visible():
    md = '# R\n\n## A\n\n**读数一** 1\n**读数二** 2\n'
    html = render_html(md)
    assert html.count('<br') == 1
    assert '读数一' in html and '读数二' in html


def test_email_short_cells_do_not_wrap_but_notes_do():
    md = '# R\n\n## A\n\n| 代码 | 说明 |\n|---|---|\n| **AVGO** | ' + '很长的说明' * 12 + ' |\n'
    html = render_html(md, email=True)
    soup = BeautifulSoup(html, 'html.parser')
    cells = soup.select('tbody td')
    assert 'nowrap' in cells[0].get('style', '') and 'nowrap' not in cells[1].get('style', '')


def test_email_digest_respects_gmail_budget_and_lists_the_rest():
    from report_format import email_digest, EMAIL_BUDGET
    table = '| 列 | 值 |\n|---|---|\n' + ''.join(f'| 行{i} | {"数据" * 20} |\n' for i in range(60))
    md = '# R\n\n' + ''.join(f'## Section {n}\n\n开头。\n\n{table}\n### 明细 {n}\n\n{table}\n---\n' for n in range(40))
    md += '## Detailed Analysis\n\n### AAPL - Apple\n\n' + table + '\n## Disclaimer\n\nEnd\n'
    html, omitted = email_digest(md)
    assert len(html.encode('utf-8')) <= EMAIL_BUDGET
    assert 'Section 0' in html and '本栏仅摘要' in html and '明细 0' not in html
    assert 'Detailed Analysis' in omitted and 'Disclaimer' in omitted and 'Section 39' in omitted
    assert '未放入正文' in html and '仅摘要' in html
    small = '# R\n\n## A\n\nBody\n'
    html, omitted = email_digest(small)
    assert omitted == [] and '邮件正文说明' not in html


def test_ai_portfolio_sorts_between_macro_and_buy_list():
    md = ('# R\n\n## 今日建议买入\n\nBuy\n\n## SaaS Watch — 软件 SaaS 名单\n\nSaaS\n\n'
          '## AI Portfolio — 核心 AI 名单\n\nAI\n\n'
          '## Macro Desk — 今日宏观\n\nMacro\n\n## Sell Put 雷达（大跌收租机会）\n\nRadar\n\n'
          '## Cash-secured Put — 统一评分候选\n\nPut\n\n## Covered Call — 统一评分候选\n\nCC2\n\n'
          '## Covered Call Advisor（写 call 到期日 / 行权价参考）\n\nCC1\n')
    out = format_markdown(md)
    order = [out.index(k) for k in ('## Macro Desk', '## AI Portfolio', '## SaaS Watch', '## 今日建议买入',
                                    '## Covered Call Advisor', '## Covered Call — 统一',
                                    '## Sell Put 雷达', '## Cash-secured Put')]
    assert order == sorted(order)
