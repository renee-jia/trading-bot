"""Macro-first briefing: factor signals, verdict, and report order."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macro_brief
from report_format import format_markdown, email_digest


def _macro(phase='uptrend', rates='stable_tight', vol='calm', vix=16.0, **extra):
    return {'score': 57, 'recommendation': 'Neutral', 'quant_score': 57,
            'trend': {'phase': phase, 'label': '上升趋势', 'structure': 'broad', 'structure_label': '涨跌比较同步',
                      'spy_from_high': -2.0, 'qqq_from_high': -3.0, 'smh_from_high': -9.0},
            'trend_outlook': {'stance': 'hold', 'stance_label': '持有观望，不追不砸'},
            'macro_tape': {'rates': rates, 'rates_label': '利率高位稳定', 'vol': vol, 'vol_label': '正常偏低',
                           'vix': vix, 'tnx': 4.8, 'tyx': 5.2, 'irx': 3.8, 'tnx_5d': 0.5, 'dollar': 'flat',
                           'dollar_label': '美元平稳', 'uup_5d': 0.1, 'hyg_5d': 0.0, 'oil_5d': 1.0, 'gld_5d': 0.2,
                           'risk_label': '风险偏好分化', 'stock_action': 'hold', 'options_action': 'selective',
                           'implication': '宏观信号打架。'},
            'cash_entry_plan': {'tier': '等待', 'target_pct': 0, 'reason': '未到第一档'}, **extra}


def _signals(rows):
    return {r['name']: r['signal'] for r in rows}


def test_missing_inputs_are_no_votes_and_still_render():
    rows = macro_brief.factors({}, None, today=date(2026, 11, 19))
    assert set(_signals(rows).values()) == {'na'}
    md, verdict = macro_brief.build({}, None)
    assert md.startswith('## 宏观研判') and '| 宏观因素 |' in md
    assert verdict['temperature'] == '信号不足' and verdict['count']['na'] == 8


def test_stress_inputs_turn_factors_red_and_name_the_pressure_points():
    regime = {'data': {
        'tape': {'SPY': {'vs_sma200': -3.0}},
        'breadth_sp500': {'n': 500, 'above_50': 15.0, 'above_200': 35.0, 'pct_20_below_high': 40.0},
        'rates': {'^TNX': 5.30, '^TNX_5d': 0.15, '^TYX': 5.6, '^IRX': 4.0},
        'fred': {'hy_oas': {'value': 4.8, 'change_5d': 0.4}, 'real10y': {'value': 2.4, 'change_5d': 0.1}},
        'vol': {'^VIX': 29.0, '^VIX3M': 27.0, 'vix_pct_rank_1y': 98.0},
        'other': {'BZ=F': {'last': 125.0, 'ret_5d': 9.0, 'ret_1m': 20.0},
                  'JPY=X': {'last': 140.0, 'ret_10d': -6.0}},
        'reduce_triggers': [('高收益利差 >450bp', 'HY OAS > 4.50%', '4.80%', '🔴 触发'),
                            ('Brent >120', 'Brent > $120', '$125', '🔴 触发'),
                            ('10 年期 >5.40%', '10y > 5.40%', '5.30%', '🟡 接近')],
        'events': [('2026-11-20', 'CPI', 3, '')], 'events_coverage_days': 40}}
    macro = _macro(phase='correction', rates='tighter', vol='panic', vix=29.0)
    rows = macro_brief.factors(macro, regime, today=date(2026, 11, 19))
    assert set(_signals(rows).values()) == {'bad'}
    md, verdict = macro_brief.build(macro, regime, today=date(2026, 11, 19), cash_reserve_pct=20)
    assert verdict['temperature'] == '逆风为主' and len(verdict['triggered']) == 2 and len(verdict['near']) == 1
    assert '期限结构倒挂' in md and '| 宏观因素 |' in md
    # The section states the situation; the action list is rendered once, by the summary.
    assert '今日操作' not in md and verdict['cash_reserve_pct'] == 20
    assert verdict['stock_line'] and verdict['options_line']
    assert '### ' not in md[:md.index('| 宏观因素 |')]


def test_calm_inputs_are_green_and_event_window_grades_by_distance():
    regime = {'data': {'breadth_sp500': {'n': 500, 'above_50': 70.0, 'above_200': 75.0, 'pct_20_below_high': 8.0},
                       'events': [('2026-11-25', 'PCE', 3, '')], 'events_coverage_days': 40}}
    signals = _signals(macro_brief.factors(_macro(), regime, today=date(2026, 11, 19)))
    assert signals['指数趋势'] == signals['市场广度'] == signals['波动率'] == 'good'
    assert signals['利率与曲线'] == 'watch' and signals['事件窗口'] == 'good'
    near = _signals(macro_brief.factors(_macro(), regime, today=date(2026, 11, 22)))
    assert near['事件窗口'] == 'watch'
    eve = macro_brief.factors(_macro(), regime, today=date(2026, 11, 24),
                              option_event={'status': 'unknown', 'reason': '日历超出范围'})
    assert _signals(eve)['事件窗口'] == 'bad' and '期权事件日历超出已核验范围' in eve[-1]['judgment']


def test_regime_tables_follow_the_lead_and_llm_narrative_is_included():
    regime_md = '## 宏观回撤与加仓时机 — Regime Desk\n\n**环境：x**\n\n### 盘面与广度\n\n表格\n'
    macro = _macro(analysis={'summary': '今日摘要', 'bull_case': '多头', 'key_risks': ['风险一']})
    md, _v = macro_brief.build(macro, {'data': {}, 'synthesis': {'regime_label': '紧缩避险', 'regime_summary': '概述',
                                                                 'entry_view': '等第二档'}}, regime_md)
    assert '**环境：紧缩避险**' in md and md.count('**环境：') == 1
    assert '等第二档' in md
    # Raw tape tables are evidence: handed to the appendix, not repeated under the factor table.
    assert '### 盘面与广度' not in md and '### 盘面与广度' in _v['evidence']
    # A searched synthesis is the authority; the unsearched narrative only fills in when it is missing.
    assert '多空逻辑' not in md and '风险一' not in md
    fallback, _ = macro_brief.build(macro, {'data': {}, 'synthesis': None}, regime_md)
    assert '### 多空逻辑与催化剂' in fallback and '风险一' in fallback


def test_macro_section_sorts_first_and_survives_the_email_digest():
    md = ('# R\n\n## 今日建议买入\n\nBuy\n\n## General — 美股趋势判断\n\nG\n\n'
          '## Executive Summary — 今日结论\n\nS\n\n## 波段抄底 — TQQQ / SOXL\n\nW\n\n'
          + macro_brief.build(_macro(), None)[0])
    out = format_markdown(md)
    order = [out.index(k) for k in ('## 宏观研判', '## Executive Summary', '## General',
                                    '## 今日建议买入', '## 波段抄底')]
    assert order == sorted(order)
    digest, omitted = email_digest(out)
    assert '宏观研判' in digest and '宏观因素' in digest and omitted == []
