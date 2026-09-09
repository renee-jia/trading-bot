"""Scenario and payoff tests for explainable option-entry decisions."""
import sys
from pathlib import Path
from copy import deepcopy
from datetime import date
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import options_decision as od
import daily_watch as dw


def fixture(bull=True, rich=False):
    def quote(k, bid):
        return {'strike':k,'bid':bid,'ask':bid+.1,'openInterest':200,
                'standard_contract':True, 'contract_symbol':str(k)}
    ratio = 1.4 if rich else .8
    snap = {'spot':100,'expiry':'2026-10-09','expected_move_pct':5,
            'atm_iv':.3,'iv_rv_ratio':ratio,'rv63':.3/ratio,
            'earnings_status':'clear','price_age_days':0,'term_slope':.02,
            'calls':[quote(100,3),quote(105,1),quote(110,.5)],
            'puts':[quote(100,3),quote(95,1),quote(90,.5)]}
    row = {'ticker':'ABC','score_result':{'score':95 if bull else 10, 'components':{
        'technical':{'score':90 if bull else 10,'confidence':.8},
        'trend':{'score':90 if bull else 10,'confidence':.8}}},
        'indicators':{'price':100,'sma_50':90 if bull else 110,
                      'change_1m':10 if bull else -10,'rsi':55 if bull else 45}}
    return snap,row,{'held':False,'levered':False,'stock_action':'hold_watch'},{'status':'open'}


@pytest.mark.parametrize('bull,rich,expected',[(True,False,'bull_call_debit'),
    (False,False,'bear_put_debit'),(True,True,'bull_put_credit'),(False,True,'bear_call_credit')])
def test_direction_and_volatility_choose_correct_strategy(bull,rich,expected):
    args=fixture(bull,rich)
    d=od.evaluate(*args)
    assert d['status']=='conditional'
    assert expected in [t['strategy'] for t in d['candidates']]
    assert 0 <= d['score'] <= 100
    for t in d['candidates']:
        assert t['max_loss']>0 and t['checks_required']


def test_high_composite_score_alone_cannot_buy_options():
    args=list(fixture())
    args[1]['score_result']['components']={}
    assert od.evaluate(*args)['status']=='wait'


def test_price_contradiction_blocks_bullish_score():
    args=list(fixture())
    args[1]['indicators']['sma_50']=110
    d=od.evaluate(*args)
    assert d['direction']['bias']=='neutral' and d['status']=='wait'


@pytest.mark.parametrize('mutation', ['earnings','stale','rv','confidence','nan','rsi','macro'])
def test_data_and_event_gates_override_high_score(mutation):
    args=list(fixture())
    if mutation=='earnings': args[0]['earnings_status']='unknown'
    if mutation=='stale': args[0]['price_age_days']=7
    if mutation=='rv': args[0]['rv63']=None
    if mutation=='confidence': args[1]['score_result']['components']['trend']['confidence']=.1
    if mutation=='nan': args[1]['score_result']['components']['technical']['score']=float('nan')
    if mutation=='rsi': args[1]['indicators']['rsi']=None
    if mutation=='macro': args[3]['status']='unknown'
    assert od.evaluate(*args)['status']=='wait'


def test_dual_horizon_blocks_false_cheap_iv():
    args=list(fixture())
    args[0]['rv63']=.2
    assert od.evaluate(*args)['status']=='wait'


def test_pcr_and_skew_do_not_fabricate_direction():
    args=fixture()
    before=od.evaluate(*args)
    args[0].update(put_call_volume=1000,skew_5pct=1)
    assert od.evaluate(*args)==before


def test_no_new_short_is_not_a_ban_on_debit_spreads():
    tape={'options_action':'no_new_short'}
    assert od.evaluate(*fixture(),tape=tape)['status']=='conditional'
    assert od.evaluate(*fixture(rich=True),tape=tape)['status']=='wait'


def test_held_bull_does_not_stack_puts_or_cap_upside():
    args=list(fixture(rich=True));args[2]['held']=True
    assert od.evaluate(*args)['status']=='wait'
    args[1]['score_result']['components']['technical']['score']=50
    args[1]['score_result']['components']['trend']['score']=50
    d=od.evaluate(*args)
    assert d['action']=='covered_call'
    assert any('100股' in x for x in d['candidates'][0]['checks_required'])


@pytest.mark.parametrize('strategy,breakeven,max_loss,max_gain',[
    ('bull_call_debit',102.1,210,290),('bear_put_debit',97.9,210,290),
    ('bull_put_credit',94.6,460,40),('bear_call_credit',105.4,460,40),
    ('cash_secured_put',94,9400,100),('covered_call',99,9900,600)])
def test_exact_bid_ask_payoffs(strategy,breakeven,max_loss,max_gain):
    t=od.ticket(fixture()[0],strategy)
    assert t['breakeven']==pytest.approx(breakeven)
    assert t['max_loss']==pytest.approx(max_loss)
    assert t['max_gain']==pytest.approx(max_gain)
    if strategy not in ('cash_secured_put','covered_call'):
        assert t['max_loss']+t['max_gain']==pytest.approx(500)


def test_nonstandard_or_wide_leg_never_recommended():
    args=list(fixture())
    args[0]['calls'][0]['standard_contract']=False
    assert od.evaluate(*args)['status']=='wait'
    args=list(fixture());args[0]['calls'][0]['ask']=8
    assert od.evaluate(*args)['status']=='wait'


def test_expired_or_empty_calendar_is_not_open():
    assert dw.options_event_status(date(2027,1,1))['status']=='unknown'
    assert dw.options_event_status(date(2026,9,8),events=[])['status']=='unknown'


def test_report_overlays_cannot_resurrect_rejected_trade():
    row=fixture()[1]
    row['indicators'].update(change_1d=-6,change_5d=-8)
    row['options_decision']={'status':'wait','action':'wait','reasons':['财报未确认']}
    cards,_=dw.annotate([row],held=set(),today=date(2026,9,8))
    assert cards[0]['options_action']=='wait'
    assert '财报未确认' in cards[0]['options_label']
    import covered_call_advisor as cc
    import sell_put_advisor as sp
    from unittest.mock import patch
    decisions={'ABC':row['options_decision']}
    with patch.object(cc,'analyze_ticker',side_effect=AssertionError('must not refetch')):
        assert '没有通过' in cc.build_covered_call_section(decisions=decisions)
    with patch.object(sp,'analyze_put_ladder',side_effect=AssertionError('must not refetch')):
        assert '没有通过' in sp.build_sell_put_section([row],decisions=decisions)


def test_conflicting_components_cannot_become_covered_call():
    args=list(fixture(rich=True));args[2]['held']=True
    args[1]['score_result']['components']['trend']['score']=10
    d=od.evaluate(*args)
    assert d['direction']['bias']=='conflict' and d['status']=='wait'


def test_credit_expiry_on_event_is_blocked_even_when_today_open():
    args=list(fixture(bull=False,rich=True));args[0]['expiry']='2026-09-18'
    d=od.evaluate(*args)
    assert d['status']=='wait' and any('到期日' in r for r in d['reasons'])


def test_legacy_builders_do_not_invent_signals_without_scoring():
    import covered_call_advisor as cc
    import sell_put_advisor as sp
    from unittest.mock import patch
    with patch.object(cc,'_risk_free_rate',side_effect=AssertionError('no network')):
        assert '缺少统一' in cc.build_covered_call_section({'ABC':100})
    with patch.object(sp,'_risk_free_rate',side_effect=AssertionError('no network')):
        assert '缺少统一' in sp.build_sell_put_section([fixture()[1]])
