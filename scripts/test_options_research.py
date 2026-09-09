"""Offline numerical, missing-data and report integration tests."""
import sys
from pathlib import Path
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
import json
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import options_research as opt
import report_generator as report


def chain(iv=.3):
    rows = [dict(strike=k, bid=2, ask=2.1, impliedVolatility=iv,
                 openInterest=100, volume=20) for k in [95, 100, 105]]
    return SimpleNamespace(calls=pd.DataFrame(rows), puts=pd.DataFrame(rows))


def test_metrics_and_bad_quotes():
    c = chain()
    c.puts.loc[0, 'impliedVolatility'] = .4
    m = opt.chain_metrics(c, 100, 30)
    assert m['atm_iv'] == pytest.approx(.3)
    assert m['expected_move_pct'] == pytest.approx(.3*np.sqrt(30/365)*100)
    assert m['skew_5pct'] == pytest.approx(.1)
    assert m['put_call_volume'] == 1
    for col, bad in [('bid', 0), ('ask', 1), ('ask', 8),
                     ('openInterest', 0), ('impliedVolatility', np.nan),
                     ('impliedVolatility', np.inf)]:
        c = chain()
        c.calls.loc[1, col] = bad
        with pytest.raises(ValueError):
            opt.chain_metrics(c, 100, 30)


def test_missing_volume_is_not_zero():
    c = chain()
    c.puts['volume'] = np.nan
    assert opt.chain_metrics(c, 100, 30)['put_call_volume'] is None


class FakeTicker:
    options = ['2026-10-08', '2026-11-07']
    calendar = {'Earnings Date': [date(2026, 12, 1)]}
    def history(self, **kwargs):
        return pd.DataFrame({'Close': [100*np.exp(.01*np.sin(i)) for i in range(63)] + [100]})
    def option_chain(self, expiry):
        return chain(.3 if expiry == self.options[0] else .35)


def test_snapshot_and_events():
    r = opt.analyze_ticker('ABC', date(2026, 9, 8), lambda _: FakeTicker())
    assert r['term_slope'] == pytest.approx(.05)
    assert r['earnings_status'] == 'clear'
    assert r['rv21'] > 0
    json.dumps(r, allow_nan=False)
    card = {'options_action': 'sell_put'}
    r['iv_rv_ratio'] = 1.4
    assert '方向数据不足' in opt.research_stance(r, card, {'status':'open'})
    assert '冻结' in opt.research_stance(r, card, {'status':'blackout'})
    for state in ['unknown', 'crosses']:
        r['earnings_status'] = state
        assert '等待' in opt.research_stance(r, card, {'status':'open'})


def test_missing_far_and_earnings():
    class Missing(FakeTicker):
        calendar = {}
        def option_chain(self, expiry):
            if expiry == self.options[1]:
                raise ValueError('missing')
            return chain()
    r = opt.analyze_ticker('ABC', date(2026, 9, 8), lambda _: Missing())
    assert r['term_slope'] is None
    assert r['earnings_status'] == 'unknown'


def test_news_compact_and_nonmutating():
    row = {'score_result': {'sentiment_detail': {'reasoning':'x'*1000}},
           'news_data': [{'title':'A', 'link':'https://a'},
                         {'title':'a', 'link':'https://b'},
                         {'title':'B', 'link':'https://c'},
                         {'title':'C', 'link':'https://d'}]}
    text = report._compact_stock_news(row)
    assert text.count('\n- ') == 2
    assert 'https://b' not in text and 'https://d' not in text
    assert 'x'*181 not in text
    assert len(row['news_data']) == 4


def test_bounded_failure_and_hold_priority():
    rows = [{'ticker':f'T{i}', 'score_result': {'score':90-i}} for i in range(15)]
    calls = []
    def failed(symbol, **kwargs):
        calls.append(symbol)
        raise ValueError('unavailable')
    text, results = opt.build_section(rows, analyzer=failed, held={'T14'}, today=date(2026, 9, 8))
    assert calls[0] == 'T14'
    assert len(calls) == len(results) == 8
    assert '数据不足' in text


def test_report_writes_snapshot(tmp_path):
    with patch.object(opt, 'build_section', return_value=('RESEARCH', [{'ticker':'ABC'}])), \
         patch.object(report, '_build_report', return_value='REPORT') as build:
        path, content = report.generate_report([], output_dir=str(tmp_path))
    assert Path(path).read_text() == content == 'REPORT'
    assert build.call_args.kwargs['options_research_section'] == 'RESEARCH'
    assert json.loads(next(tmp_path.glob('options_research_*.json')).read_text()) == [{'ticker':'ABC'}]


def test_infinite_volume_does_not_poison_json():
    c = chain()
    c.puts['volume'] = np.inf
    metrics = opt.chain_metrics(c, 100, 30)
    assert metrics['put_call_volume'] is None
    json.dumps(metrics, allow_nan=False)


def test_verified_etf_does_not_require_company_earnings():
    class ETF(FakeTicker):
        def get_info(self):
            return {'quoteType': 'ETF'}
        @property
        def calendar(self):
            raise AssertionError('ETF should not request company earnings')
    r = opt.analyze_ticker('SPY', date(2026, 9, 8), lambda _: ETF())
    assert r['earnings_status'] == 'not_applicable'
    assert '财报' not in opt.research_stance(r, {}, {'status': 'open'})
    assert '冻结' in opt.research_stance(r, {}, {'status': 'blackout'})


def test_raw_spot_and_adjusted_returns_are_separate():
    class Dividend(FakeTicker):
        def history(self, **kwargs):
            assert kwargs['auto_adjust'] is False
            frame = super().history(**kwargs)
            frame['Adj Close'] = frame['Close'] * .99
            return frame
    r = opt.analyze_ticker('ABC', date(2026, 9, 8), lambda _: Dividend())
    assert r['spot'] == 100
    ref = opt.analyze_ticker('ABC', date(2026, 9, 8), lambda _: FakeTicker())
    assert r['rv21'] == pytest.approx(ref['rv21'])


def test_null_macro_tape_and_no_new_short():
    rows = [{'ticker':'ABC', 'score_result':{'score':75},
             'indicators':{'change_1d':-6, 'change_5d':-8, 'rsi':30}}]
    def analyze(symbol, **kwargs):
        r = opt.analyze_ticker(symbol, date(2026, 9, 8), lambda _: FakeTicker())
        r['iv_rv_ratio'] = 1.4
        return r
    _, results = opt.build_section(rows, {'macro_tape':None}, analyzer=analyze,
                                   held=set(), today=date(2026, 9, 8))
    assert len(results) == 1 and 'status' not in results[0]
    _, results = opt.build_section(rows, {'macro_tape':{'options_action':'no_new_short'}},
                                   analyzer=analyze, held=set(), today=date(2026, 9, 8))
    assert results[0]['decision']['status'] == 'wait'
    assert results[0]['decision']['candidates'] == []
