"""TQQQ / SOXL swing desk: tier rules, confirmation, guards, replay, rendering."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import leveraged_swing as ls


def _frame(**cols):
    base = {'close': 90.0, 'dd': -12.0, 'rsi': 30.0, 'pctb': 0.02, 'ret5': -6.0, 'vix': 26.0,
            'vix_rank': 95.0, 'vix_off_peak': -8.0, 'vix_ratio': 0.98, 'sma20': 95.0, 'sma50': 99.0,
            'sma200': 85.0, 'upper': 102.0, 'low10': 89.0}
    base.update(cols)
    return pd.DataFrame([base])


def test_tiers_need_drawdown_fear_and_a_vix_rollover():
    assert int(ls.tier_series(_frame(), 'TQQQ').iloc[0]) == 2
    # Fear still at its 10-day high: price tier is there, tradable tier is not.
    rising = _frame(vix_off_peak=0.0)
    assert int(ls.tier_series(rising, 'TQQQ').iloc[0]) == 0
    assert int(ls.tier_series(rising, 'TQQQ', confirmed=False).iloc[0]) == 2
    # Capitulation needs no confirmation.
    assert int(ls.tier_series(_frame(dd=-20.0, vix=32.0, vix_off_peak=0.0), 'TQQQ').iloc[0]) == 3
    # Calm VIX or a shallow drawdown never triggers; SOXL needs a deeper drawdown than TQQQ.
    assert int(ls.tier_series(_frame(vix=17.0, vix_rank=50.0), 'TQQQ').iloc[0]) == 0
    assert int(ls.tier_series(_frame(dd=-4.0), 'TQQQ').iloc[0]) == 0
    assert int(ls.tier_series(_frame(dd=-12.0), 'SOXL').iloc[0]) == 1


def test_decision_waits_for_confirmation_and_context_only_shrinks():
    row = _frame(vix_off_peak=0.0).iloc[0].to_dict()
    wait = ls.decide('TQQQ', row, 0, {}, raw_tier=2)
    assert wait['cap'] == 0 and '等待确认' in wait['action'] and 'VIX 收盘 ≤' in wait['notes'][0]
    row = _frame().iloc[0].to_dict()
    assert ls.decide('TQQQ', row, 2, {})['cap'] == 60
    below = _frame(sma200=100.0).iloc[0].to_dict()
    assert ls.decide('TQQQ', below, 2, {})['cap'] == 30
    event = (date(2026, 11, 20), 'CPI', 1)
    assert ls.decide('TQQQ', row, 2, {}, event)['cap'] == 25
    assert ls.decide('TQQQ', row, 2, {'hy_oas': 4.8})['cap'] == 25
    assert ls.decide('TQQQ', row, 3, {'reduce_hits': ['a', 'b']})['cap'] == 25
    idle = ls.decide('TQQQ', _frame(dd=-2.0, vix=15.0).iloc[0].to_dict(), 0, {}, raw_tier=0)
    assert idle['action'] == '观望，不建仓' and len(idle['notes']) == 2


def test_sentiment_is_read_contrarian_and_missing_data_is_unknown():
    assert ls.sentiment_label({})[0] == 'unknown'
    assert ls.sentiment_label({'news': 35, 'breadth': 20})[0] == 'washed'
    assert ls.sentiment_label({'news': 70, 'breadth': 80})[0] == 'complacent'
    assert ls.sentiment_label({'news': 50, 'breadth': 20})[0] == 'cautious'


def _history():
    """Uptrend, a 20% slide with a VIX spike that then rolls over, and a recovery."""
    days = pd.bdate_range('2020-01-01', periods=700)
    under = np.concatenate([np.linspace(100, 160, 500), np.linspace(160, 128, 30),
                            np.linspace(128, 170, 170)])
    vix = np.concatenate([np.full(500, 14.0), np.linspace(14, 34, 30), np.linspace(34, 15, 170)])
    under = pd.Series(under, index=days)
    etf = (1 + under.pct_change().fillna(0) * 3).cumprod() * 50
    return under, etf, pd.Series(vix, index=days), pd.Series(np.full(700, 22.0), index=days)


def test_replay_buys_the_slide_and_exits_into_the_recovery():
    under, etf, vix, vix3m = _history()
    f = ls.features(under, vix, vix3m)
    episodes = ls.simulate(f, etf, 'TQQQ')
    assert episodes and all(e['end'] > e['start'] for e in episodes)
    assert episodes[-1]['ret'] > 0 and episodes[-1]['max_tier'] >= 1
    stats = ls.episode_stats(episodes)
    assert stats['n'] == len(episodes) and 0 <= stats['win'] <= 100
    # Signals are point-in-time: truncating the future must not change past tiers.
    cut = ls.tier_series(ls.features(under.iloc[:520], vix.iloc[:520], vix3m.iloc[:520]), 'TQQQ')
    assert cut.equals(ls.tier_series(f, 'TQQQ').iloc[:520])


def test_build_renders_and_fails_safe_without_personal_figures():
    under, etf, vix, vix3m = _history()
    series = {'^VIX': vix, '^VIX3M': vix3m, 'QQQ': under, 'SOXX': under, 'TQQQ': etf, 'SOXL': etf}
    data = ls.build(today=date(2022, 9, 6), loader=lambda symbol, period='max': series.get(symbol))
    md = ls.build_section(data)
    assert md.startswith('## 波段抄底') and '| **TQQQ**' in md and '| **SOXL**' in md
    assert '入场阶梯' in md and '离场计划' in md and '历史验证' in md
    assert '持仓' not in md and '持有' not in md and 'nan' not in md.lower()
    assert 'TQQQ' in ls.headline(data)
    empty = ls.build(loader=lambda symbol, period='max': None)
    assert empty['error'] and '数据不可用' in ls.build_section(empty)
