"""Hard allocation invariants for the shared private strategy engine."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import strategy


def stocks(n):
    return [{'ticker':f'T{i}', 'score':70, 'mom':n-i} for i in range(n)]


@pytest.mark.parametrize('n', range(2, 21))
@pytest.mark.parametrize('macro', [30, 50, 80])
def test_cap_and_cash_conservation(n, macro):
    weights, cash = strategy.compute_target_weights(stocks(n), macro_score=macro)
    assert max(weights.values()) <= strategy.MAX_POS + 1e-10
    assert sum(weights.values()) + cash*100 == pytest.approx(100)
    assert cash >= strategy.macro_cash_pct(macro) - 1e-10
    assert len(weights) <= strategy.TOP_N


def test_two_names_leave_unallocatable_budget_in_cash():
    weights, cash = strategy.compute_target_weights(stocks(2), macro_score=50)
    assert weights == {'T0':25, 'T1':25}
    assert cash == .5


def test_normal_ten_name_blend_is_preserved():
    weights, cash = strategy.compute_target_weights(stocks(10), macro_score=50)
    raw = [.95*(3-2.8*i/9)+.05*.7**2 for i in range(10)]
    for i, value in enumerate(raw):
        assert weights[f'T{i}'] == pytest.approx(90*value/sum(raw))
    assert cash == pytest.approx(.1)


def test_buffer_keeps_eligible_holding():
    weights, _ = strategy.compute_target_weights(stocks(20), held={'T12'})
    assert 'T12' in weights and 'T9' not in weights


def test_empty_or_single_universe_still_skips_allocation():
    for n in (0,1):
        weights, cash = strategy.compute_target_weights(stocks(n))
        assert weights == {} and cash == 1


def test_report_displays_cash_left_by_cap():
    from unittest.mock import patch
    import report_generator
    rows = [{'ticker':f'T{i}', 'name':f'T{i}',
             'score_result':{'score':70, 'recommendation':'Buy'},
             'indicators':{'change_1m':5-i, 'change_3m':10-i}} for i in range(2)]
    with patch.dict(sys.modules, {'strategy':strategy}):
        rendered = report_generator._build_portfolio_weights(rows)
    assert '**Cash:** 50%' in rendered
    assert '**Total Invested:** 50.0%' in rendered
    assert 'existing-holding rank buffer' in rendered


# ---------------------------------------------------------------------------
# V7: 6-1 momentum signal + portfolio vol targeting
# ---------------------------------------------------------------------------
import math
import pandas as pd


def test_momentum_signal_prefers_6_1_and_falls_back_to_legacy():
    ind = {'change_1m': 10.0, 'change_3m': 20.0, 'change_6m_skip1': 50.0}
    assert strategy.momentum_signal(ind, mode='6_1') == pytest.approx(0.50)
    assert strategy.momentum_signal(ind, mode='legacy') == pytest.approx(.3*.10 + .7*.20)
    # missing 6-1 field (short history) -> legacy blend, never a crash
    assert strategy.momentum_signal({'change_1m': 10.0, 'change_3m': 20.0}, mode='6_1') \
        == pytest.approx(.3*.10 + .7*.20)
    assert strategy.momentum_signal({}, mode='6_1') == 0.0


def test_momentum_6_1_from_close_skips_latest_month():
    n = strategy.MOM_FORMATION_DAYS + 1
    close = pd.Series([100.0] * (n - strategy.MOM_SKIP_DAYS) + [200.0] * strategy.MOM_SKIP_DAYS)
    # the last 21 bars doubled but are skipped -> flat 6-1 momentum
    assert strategy.momentum_6_1_from_close(close) == pytest.approx(0.0)
    close2 = pd.Series([100.0] + [150.0] * (n - 1))
    assert strategy.momentum_6_1_from_close(close2) == pytest.approx(0.5)
    assert strategy.momentum_6_1_from_close(close.iloc[:50]) is None


def test_basket_realized_vol_and_exposure():
    lb = strategy.VOL_LOOKBACK
    calm = [0.001] * lb
    wild = [0.05 * (-1) ** i for i in range(lb)]
    rv = strategy.basket_realized_vol({'A': 50, 'B': 50}, {'A': calm, 'B': wild}, lookback=lb)
    expected = pd.Series([0.5 * c + 0.5 * w for c, w in zip(calm, wild)]).std() * math.sqrt(252)
    assert rv == pytest.approx(expected)
    # names without history are ignored; no history at all -> None
    assert strategy.basket_realized_vol({'A': 50}, {'A': calm[:3]}, lookback=lb) is None
    assert strategy.vol_target_exposure(0.70, target=0.35) == pytest.approx(0.5)
    assert strategy.vol_target_exposure(0.20, target=0.35) == 1.0      # never levers up
    assert strategy.vol_target_exposure(None, target=0.35) == 1.0
    assert strategy.vol_target_exposure(0.70, target=0) == 1.0         # disabled


def test_vol_managed_weights_scale_exposure_and_report_cash():
    lb = strategy.VOL_LOOKBACK
    wild = [0.05 * (-1) ** i for i in range(lb)]           # ~79% annualized
    returns = {f'T{i}': wild for i in range(10)}
    w_full, cash_full = strategy.compute_target_weights(stocks(10), macro_score=50)
    w, cash, info = strategy.compute_target_weights_vol_managed(
        stocks(10), macro_score=50, returns_by_ticker=returns, vol_target=0.35, lookback=lb)
    assert 0 < info['exposure'] < 1
    assert sum(w.values()) == pytest.approx(sum(w_full.values()) * info['exposure'])
    assert sum(w.values()) + cash * 100 == pytest.approx(100)
    assert max(w.values()) <= strategy.MAX_POS + 1e-10
    # ordering / membership unchanged by scaling
    assert set(w) == set(w_full)
    # targeting disabled -> identical to V6
    w0, cash0, info0 = strategy.compute_target_weights_vol_managed(
        stocks(10), macro_score=50, returns_by_ticker=returns, vol_target=0)
    assert w0 == w_full and cash0 == cash_full and info0['exposure'] == 1.0


def test_buffer_is_twenty():
    assert strategy.BUFFER_N == 20
    weights, _ = strategy.compute_target_weights(stocks(25), held={'T18'})
    assert 'T18' in weights
    weights, _ = strategy.compute_target_weights(stocks(25), held={'T21'})
    assert 'T21' not in weights
