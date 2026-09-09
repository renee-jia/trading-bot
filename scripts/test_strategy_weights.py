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
