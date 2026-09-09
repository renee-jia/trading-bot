"""Signal invariants, no future leakage, rank semantics and score integration."""
from pathlib import Path
import sys
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'core')]
import stock_signals as ss
import scorer
import trend_analyzer


def candles(returns, location=.5):
    close=100*np.exp(np.cumsum(returns))
    return pd.DataFrame({'Open':close,'High':close+2*(1-location),'Low':close-2*location,
                         'Close':close,'Volume':1000000},index=pd.bdate_range('2023-01-02',periods=len(close)))


def inputs():
    rng=np.random.default_rng(18)
    market=rng.normal(.0003,.01,300)
    noise=rng.normal(0,.004,300)
    return candles(1.3*market+noise),candles(market)


def test_future_benchmark_cannot_change_past_signal():
    prices,benchmark=inputs()
    expected=ss.analyze(prices.iloc[:250],benchmark.iloc[:250])
    benchmark.iloc[250:,benchmark.columns.get_loc('Close')]*=20
    assert ss.analyze(prices.iloc[:250],benchmark)==expected


def test_stock_specific_strength_distinguishes_same_beta():
    stock,bench=inputs()
    strong=stock.copy()
    multiplier=np.ones(300);multiplier[-63:]=np.exp(np.arange(1,64)*.003)
    strong[['Open','High','Low','Close']]=strong[['Open','High','Low','Close']].mul(multiplier,axis=0)
    a,b=ss.analyze(stock,bench),ss.analyze(strong,bench)
    assert b['signals']['residual_strength']['score'] > a['signals']['residual_strength']['score']+20
    assert b['signals']['residual_strength']['beta']==pytest.approx(a['signals']['residual_strength']['beta'])


def test_volume_location_is_not_just_return():
    r=np.random.default_rng(1).normal(0,.01,300)
    accumulation=ss.analyze(candles(r,.9))
    distribution=ss.analyze(candles(r,.1))
    assert accumulation['signals']['close_location_volume']['score']>90
    assert distribution['signals']['close_location_volume']['score']<10


def test_scale_invariance():
    p,b=inputs()
    changed=p.copy();changed[['Open','High','Low','Close']]*=7;changed.Volume*=3
    a,c=ss.analyze(p,b),ss.analyze(changed,b)
    for key in a['signals']:
        assert a['signals'][key]['score']==pytest.approx(c['signals'][key]['score'])


def test_missing_data_is_not_reweighted():
    p,_=inputs()
    r=ss.analyze(p,None)
    assert r['coverage']==.25 and abs(r['adjustment'])<=3
    p['Volume']=0
    r=ss.analyze(p,None)
    assert r['coverage']==0 and r['adjustment']==0


def test_bad_candle_does_not_become_volume_signal():
    p,b=inputs();p.iloc[-1,p.columns.get_loc('High')]=0
    assert 'close_location_volume' not in ss.analyze(p,b)['signals']


def test_flat_returns_no_infinities():
    p=candles(np.zeros(300))
    r=ss.analyze(p,p)
    import json
    json.dumps(r,allow_nan=False)
    assert r['coverage']==.25


def test_adjustment_bounded_and_base_not_mutated():
    base={'score':57,'raw_score':.57,'confidence':.6,'grade':'C+',
          'recommendation':'Hold','reasoning':[],'components':{}}
    research={'signals':{'residual_strength':{}},'adjustment':500}
    new=ss.apply(base,research,promote=True)
    assert new['score']==69 and new['recommendation']=='Buy'
    assert base['score']==57 and base['reasoning']==[]
    assert new['confidence']==base['confidence']


def test_peer_rank_does_not_turn_bearish_universe_into_buys():
    rows=[{'ticker':str(i),'score_result':{'score':10+i,'recommendation':'Avoid'}} for i in range(10)]
    ranked=ss.add_peer_ranks(rows)
    assert ranked[-1]['score_result']['peer_percentile']==100
    assert ranked[-1]['score_result']['recommendation']=='Avoid'
    assert ranked[-1]['score_result']['score']==19
    assert 'peer_percentile' not in rows[0]['score_result']


def test_ties_and_small_universes():
    rows=[{'ticker':str(i),'score_result':{'score':50}} for i in range(10)]
    assert {r['score_result']['peer_percentile'] for r in ss.add_peer_ranks(rows)}=={50}
    assert ss.add_peer_ranks(rows[:3])[0]['score_result']['peer_percentile'] is None


def test_scorer_optional_overlay_and_weight_defaults():
    a={'score':.7,'confidence':.8}
    from configs import ANALYZER_WEIGHTS
    old=scorer.score_stock(a,a,a,alpha_score=.6)
    configured=scorer.score_stock(a,a,a,alpha_score=.6,weights=ANALYZER_WEIGHTS)
    assert old['score']==configured['score']
    r=ss.analyze(*inputs())
    updated=scorer.score_stock(a,a,a,alpha_score=.6,research_result=r)
    assert updated['score']==old['score']
    assert updated['research_score']==pytest.approx(round(old['score']+r['adjustment'],1))
    assert updated['score_version']=='legacy+research-shadow'


def test_vix_cannot_see_future_rows():
    prices=pd.DataFrame({'Close':[18.0]*60+[80.0]*10},index=pd.bdate_range('2023-01-02',periods=70))
    cutoff=prices.index[59]
    full=trend_analyzer._vix_analysis(as_of=cutoff,data=prices)
    truncated=trend_analyzer._vix_analysis(as_of=cutoff,data=prices.iloc[:60])
    assert full==truncated
    assert full['signals']['vix_current']==18
    assert trend_analyzer._vix_analysis(as_of='2022-01-01',data=prices)['signals']['vix_available'] is False


def test_report_explains_absolute_and_relative_scores():
    import report_generator
    base={'score':55,'confidence':.6,'recommendation':'Hold','reasoning':[],'components':{}}
    research=ss.analyze(*inputs())
    rows=ss.add_peer_ranks([{'ticker':str(i),'score_result':ss.apply({**base,'score':45+i},research)}
                           for i in range(10)])
    text=report_generator._build_score_research(rows)
    assert '同池百分位' in text and '不是胜率' in text
    assert '市场调整强度' in text and '抗跌表现' in text
    assert f"{rows[0]['score_result']['score']:.1f}" in text


def test_live_analyze_uses_research_but_preserves_option_direction_components():
    import main
    p,b=inputs()
    analyzer={'score':.65,'confidence':.8,'components':{},'indicators':{'price':100}}
    with patch.object(main.technical_analyzer,'analyze',return_value=analyzer), \
         patch.object(main.trend_analyzer,'analyze',return_value=analyzer):
        sr,_=main.analyze_stock('ABC',p,b,alpha_score=.6)
    assert sr['score_version']=='legacy+research-shadow'
    assert sr['components']['technical']['score']==65
    assert sr['components']['trend']['score']==65


def test_backtest_slices_benchmark_by_date_not_stock_position():
    import backtest_strategy as bt
    p,b=inputs()
    # Benchmark starts later, so matching iloc positions would include future dates.
    benchmark=b.iloc[20:]
    analyzer={'score':.6,'confidence':.8,'components':{}}
    with patch.object(bt.technical_analyzer,'analyze',return_value=analyzer), \
         patch.object(bt.trend_analyzer,'analyze',return_value=analyzer) as trend, \
         patch.object(bt,'compute_alpha_score',return_value=.5):
        bt.score_stock_at_date('ABC',p,benchmark,260)
    assert trend.call_args.args[1].index.max()==p.index[259]


def test_unvalidated_candidate_never_changes_execution_rating():
    base={'score':58,'raw_score':.58,'confidence':.7,'grade':'C+',
          'recommendation':'Hold','reasoning':['original'],'components':{}}
    research={'signals':{'residual_strength':{'score':100}},'adjustment':12}
    result=ss.apply(base,research)
    assert result['research_score']==70
    for field in ('score','raw_score','confidence','grade','recommendation','reasoning'):
        assert result[field]==base[field]
    assert result['research_promoted'] is False
