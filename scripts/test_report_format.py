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


def test_preclose_note_only_before_the_us_close():
    from datetime import datetime
    from unittest.mock import patch
    import market_bars

    class Morning(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 21, 11, 0, tzinfo=tz or market_bars.NY)

    class Closed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 21, 16, 0, tzinfo=tz or market_bars.NY)

    with patch('report_generator.datetime', Morning):
        assert '2026-09-18' in report_generator._preclose_data_note('2026-09-18')
    with patch('report_generator.datetime', Closed):
        assert report_generator._preclose_data_note('2026-09-18') == ''


def test_ai_portfolio_sorts_between_macro_and_buy_list():
    md = ('# R\n\n## 今日建议买入\n\nBuy\n\n## 电力加仓 — Power Portfolio\n\nPower\n\n'
          '## 抄底布局 — Diversified Portfolio\n\nDip\n\n'
          '## SaaS Watch — 软件 SaaS 名单\n\nSaaS\n\n'
          '## 光学互联 — Optics Portfolio\n\nOptics\n\n'
          '## 半导体加仓 — Chip Portfolio\n\nChip\n\n'
          '## AI Portfolio — 核心 AI 名单\n\nAI\n\n'
          '## Macro Desk — 今日宏观\n\nMacro\n\n## Sell Put 雷达（大跌收租机会）\n\nRadar\n\n'
          '## Cash-secured Put — 统一评分候选\n\nPut\n\n## Covered Call — 统一评分候选\n\nCC2\n\n'
          '## Covered Call Advisor（写 call 到期日 / 行权价参考）\n\nCC1\n')
    out = format_markdown(md)
    order = [out.index(k) for k in ('## Macro Desk', '## AI Portfolio', '## 半导体加仓',
                                    '## 光学互联', '## SaaS Watch',
                                    '## 抄底布局', '## 电力加仓', '## 今日建议买入',
                                    '## Covered Call Advisor', '## Covered Call — 统一',
                                    '## Sell Put 雷达', '## Cash-secured Put')]
    assert order == sorted(order)


def test_watch_rollup_preserves_overlapping_memberships_and_fixed_references():
    from report_sections import theme_section
    base = {'ticker':'PWR', 'in_run':True, 'price':580., 'score':67.,
            'from_high':-12., 'add_verdict':'分批', 'buy_action':'scale_in'}
    books = [('AI', {'names':[], 'watch':[base]}),
             ('半导体', {'names':[], 'priority':[{**base, 'priority':3, 'reason':'测试优先级'}]}),
             ('SaaS', {'names':[{**base, 'stars':'★★★★', 'risk':'估值风险'}]}),
             ('抄底布局', {'names':[{**base, 'module':'电网', 'view':'长期底仓'}]}),
             ('电力', {'names':[{**base, 'open_px':600., 'add_px':560., 'ladder':'已到开仓区间'}]})]
    md = theme_section(books)
    assert md.count('| **PWR** |') == 1
    for reference in ('AI观察', '半导体', 'SaaS', '抄底布局', '电力', '★★★★',
                      '优先级 3', '测试优先级', '估值风险', '$600', '$560', '长期底仓'):
        assert reference in md
    assert '| $580.00 |' in md and '| 67.0 |' in md


def test_missing_watch_score_cannot_appear_actionable():
    from report_sections import theme_section
    md = theme_section([('电力', {'names':[{'ticker':'GEV', 'in_run':False,
                                         'open_px':920., 'add_px':865., 'add_verdict':'抄底'}]})])
    row = next(line for line in md.splitlines() if '| **GEV** |' in line)
    assert '未评分' in row and '抄底' not in row and '$920' in row and '$865' in row


def test_appendices_collapse_on_web_and_stay_out_of_both_email_formats():
    from report_format import email_digest
    from report_sections import plain_summary
    md = ('# R\n\n## 今日建议买入\n\n今日动作\n\n'
          '## 附录 — 期权数据\n\n附录原始数据\n\n### 合约\n\n最大亏损 $200\n\n'
          '## Detailed Analysis\n\n### AAPL - Apple\n\n个股因素\n')
    web = BeautifulSoup(render_html(md), 'html.parser')
    assert '最大亏损 $200' in web.select_one('details.appendix').text
    assert '个股因素' in web.select_one('details.stock-detail').text
    digest, omitted = email_digest(md)
    assert '今日动作' in digest and '附录原始数据' not in digest
    assert '附录 — 期权数据' in omitted
    plain = plain_summary(md)
    assert '今日动作' in plain and '附录原始数据' not in plain and '个股因素' not in plain


def test_buy_summary_keeps_event_sizing_and_wait_conditions_once():
    from unittest.mock import patch
    import daily_watch
    card = {'ticker':'TEST', 'score':68., 'change_1d':-3., 'from_high':-10.,
            'from_sma50':-2., 'buy_size':'预算 10%', 'buy_note':'等回踩确认后分批'}
    with patch.object(daily_watch, '_load_cards', return_value=([card], {})), \
         patch.object(daily_watch, 'dip_candidates', return_value=[card]), \
         patch.object(daily_watch, 'add_candidates', return_value=[card]), \
         patch.object(daily_watch, 'buy_picks', return_value=[]):
        md = daily_watch.build_buy_section([], compact=True)
    assert md.count('**TEST**') == 1
    assert '预算 10%' in md and '等回踩确认后分批' in md


def test_compacted_watch_email_keeps_summary_and_omits_large_stock_table():
    from report_format import email_digest
    md = ('# R\n\n## 主题观察 — 合并名单\n\n固定参考。\n\n'
          '| 主题 | 触发 |\n|---|---|\n| 电力 | PWR |\n\n'
          '### 逐股观察\n\n| 股票 | 点位 |\n|---|---|\n| PWR | 600/560 |\n')
    email, omitted = email_digest(md)
    assert 'PWR' in email and '600/560' not in email and '本栏仅摘要' in email
    assert omitted == []
    web = BeautifulSoup(render_html(md), 'html.parser')
    assert '600/560' in web.select_one('details.watch-book').text


def test_unified_contract_is_rendered_once_with_payoff_and_checks():
    from unittest.mock import patch
    import ai_sell_put_plan, covered_call_advisor, leveraged_swing, macro_regime, sell_put_advisor
    from core import strategy
    from options_decision import render_tickets
    ticket = {'strategy':'cash_secured_put', 'score':72, 'expiry':'2026-11-20',
              'legs':[{'side':'sell', 'strike':95, 'type':'put'}], 'premium':1.5,
              'premium_type':'credit', 'max_gain':150, 'max_loss':9350,
              'breakeven':93.5, 'capital_required':9500, 'components':{'direction':70},
              'checks_required':['核验最新报价'], 'payoff_basis':'到期每组100股'}
    decision = {'version':'options-entry-v2', 'status':'conditional',
                'action':'cash_secured_put', 'score':72, 'direction':{'score':70},
                'candidates':[ticket], 'reasons':[]}
    row = {'ticker':'TEST', 'name':'Example', 'indicators':{'price':100, 'rsi':50},
           'score_result':{'score':70, 'grade':'B', 'recommendation':'Buy', 'confidence':.8,
                           'components':{}, 'reasoning':['独特因素', '独特因素']},
           'options_decision':decision}
    research = ('## Options Research\n\n链信号数据\n\n### 操作评分与候选合约\n\n' +
                render_tickets({'TEST':decision}) + '\nIV÷RV 是研究比较，不保证收益。\n')
    with patch.dict(sys.modules, {'strategy':strategy}), \
         patch.object(report_generator, '_build_stock_trend_section', return_value='## General\n\n市场'), \
         patch.object(report_generator, '_build_portfolio_weights', return_value=''), \
         patch.object(report_generator, '_build_alpaca_performance', return_value=''), \
         patch.object(macro_regime, 'build', return_value={'data':{}, 'synthesis':{'regime_label':'测试'}}), \
         patch.object(macro_regime, 'build_section', return_value='## Regime\n\n依据'), \
         patch.object(leveraged_swing, 'build', return_value={'error': 'offline'}), \
         patch.object(ai_sell_put_plan, 'build_section', return_value='## Put 观察\n\n方案'), \
         patch.object(covered_call_advisor, 'build_covered_call_ladders', return_value=''), \
         patch.object(sell_put_advisor, 'build_sell_put_radar', return_value=''):
        md = report_generator._build_report([row], options_research_section=research,
               sell_put_plan={}, ai_portfolio={}, saas_watch={}, dip_layout={},
               chip_layout={}, optics_layout={}, power_layout={})
    assert md.count('### TEST — 卖出现金担保 put') == 1
    main = md.split('## 附录', 1)[0]
    for key in ('2026-11-20', '卖 95 put', '$150.00/组', '最大亏损 $9350.00',
                '保本 $93.50', '资金参考 $9500.00', '核验最新报价'):
        assert key in main
    assert '链信号数据' in md and 'IV÷RV 是研究比较' in md
    assert md.count('- 独特因素') == 1
