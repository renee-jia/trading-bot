"""Cash ladder boundaries, cumulative-state behavior and no-future data tests."""
from datetime import date
from pathlib import Path
import sys
import pandas as pd
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cash_entry_plan as cp


def snap(vix=15.72,dd=-1.61,day='2026-09-08',dip=False):
    return {'status':'ok','vix':vix,'drawdown_pct':dd,'as_of':day,
            'dip_seen_since_start':dip}


@pytest.mark.parametrize('vix,dd,target',[(15.72,-1.61,0),(18,-3,12.5),(20,-5,31.25),
    (23,-8,56.25),(27,-10,81.25),(30,-15,100),(32,-12,81.25),
    (22,-7.5,31.25),(40,-1,0),(16,-12,0),(23,-5,31.25)])
def test_joint_boundaries(vix,dd,target):
    p=cp.evaluate(snap(vix,dd),today=date(2026,9,8))
    assert p['target_pct']==target
    assert p['target_cash_pct']+p['target_pct']==100
    assert 0<=p['next_batch_cap_pct']<=25


def test_repeat_and_jump_do_not_double_buy():
    s=snap(30,-16)
    first=cp.evaluate(s,today=date(2026,9,8))
    assert first==cp.evaluate(s,today=date(2026,9,8))
    assert first['gap_pct']==100 and first['next_batch_cap_pct']==25
    funded=cp.evaluate(s,deployed_pct=81.25,today=date(2026,9,8))
    assert funded['gap_pct']==funded['next_batch_cap_pct']==18.75
    done=cp.evaluate(s,deployed_pct=100,today=date(2026,9,8))
    assert done['status']=='funded' and done['gap_pct']==0


def test_rebound_does_not_recommend_selling_to_restore_cash():
    p=cp.evaluate(snap(),deployed_pct=31.25,today=date(2026,9,8))
    assert p['gap_pct']==0 and p['target_pct']==31.25 and p['target_cash_pct']==68.75


def test_time_rule_and_prior_dip():
    p=cp.evaluate(snap(day='2026-10-15'),today=date(2026,10,15))
    assert p['target_pct']==12.5 and p['capital_needs_confirmation']
    p=cp.evaluate(snap(day='2026-10-22'),today=date(2026,10,22),deployed_pct=12.5)
    assert p['target_pct']==25 and p['gap_pct']==12.5
    p=cp.evaluate(snap(day='2026-10-15',dip=True),today=date(2026,10,15))
    assert p['gap_pct']==0
    p=cp.evaluate(snap(day='2026-10-14'),today=date(2026,10,14))
    assert p['gap_pct']==0


def test_missing_stale_or_invalid_inputs_never_trigger():
    assert cp.evaluate({'status':'unavailable'},today=date(2026,9,8))['gap_pct']==0
    p=cp.evaluate(snap(35,-20),today=date(2026,10,1),deployed_pct=12.5)
    assert p['status']=='unavailable' and p['target_cash_pct']==87.5 and p['gap_pct']==0
    for value in [float('nan'),-1,101]:
        with pytest.raises(ValueError):
            cp.evaluate(snap(),deployed_pct=value)


def histories():
    dates=pd.bdate_range('2026-05-01','2026-10-15')
    s=pd.Series(100.,index=dates)
    s.loc['2026-08-13']=110
    v=pd.Series(16.,index=dates)
    return pd.DataFrame({'Close':s}),pd.DataFrame({'Close':v})


def test_peak_frozen_and_no_future_leakage():
    s,v=histories()
    before=cp.snapshot_from_history(s,v,as_of=date(2026,9,8))
    s.loc['2026-09-09':,'Close']=300
    after=cp.snapshot_from_history(s,v,as_of=date(2026,9,8))
    assert before==after
    assert before['peak']==110 and before['drawdown_pct']==pytest.approx((100/110-1)*100)


def test_peak_never_rolls_down_and_prior_dip_is_remembered():
    dates=pd.bdate_range('2026-05-01','2026-12-15')
    s=pd.DataFrame({'Close':100.},index=dates);v=pd.DataFrame({'Close':16.},index=dates)
    s.loc['2026-08-13','Close']=110
    result=cp.snapshot_from_history(s,v,as_of=date(2026,12,15))
    assert result['peak']==110 and result['rolling_63_drawdown_pct']==0
    assert result['dip_seen_since_start'] is True


def test_mismatched_quote_days_are_unavailable():
    s,v=histories()
    r=cp.snapshot_from_history(s,v.iloc[:-1],as_of=date(2026,10,15))
    assert r['status']=='unavailable'


def test_cash_plan_is_in_general_and_email_without_spy_proxy():
    import report_generator as report
    import daily_watch
    s,v=histories()
    snapshot=cp.snapshot_from_history(s,v,as_of=date(2026,9,8))
    plan=cp.evaluate(snapshot,today=date(2026,9,8))
    macro={'cash_entry_plan':plan,'market_data':{'^GSPC':{'cash_plan_snapshot':snapshot}}}
    text=report._build_stock_trend_section([],macro)
    assert '现金入场计划' in text and '87.5%' in text and '10/15' in text
    assert '$' not in cp.render(plan) and '400' not in cp.render(plan)   # pool size is never written out
    assert 'VIX 16.00' in text
    email=daily_watch.email_macro_lines(macro,today=date(2026,9,8))
    assert any('现金入场计划' in line for line in email)
    assert not any('$' in line for line in cp.email_lines(plan))
    assert cp.from_market_data({'SPY':{'price':100,'from_high':-10}},today=date(2026,9,8))['status']=='unavailable'


def test_intraday_bar_is_not_treated_as_confirmed_close():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    frame=pd.DataFrame({'Close':[100,90]},index=pd.to_datetime(['2026-09-04','2026-09-08']))
    morning=datetime(2026,9,8,10,tzinfo=ZoneInfo('America/New_York'))
    assert len(cp.completed_daily_history(frame,morning))==1
    evening=datetime(2026,9,8,17,tzinfo=ZoneInfo('America/New_York'))
    assert len(cp.completed_daily_history(frame,evening))==2


def test_csv_dates_spanning_dst_preserve_sessions():
    from io import StringIO
    dates=pd.bdate_range('2026-01-02','2026-09-08',tz='America/New_York')
    s=pd.DataFrame({'Close':100.},index=dates)
    v=pd.DataFrame({'Close':16.},index=dates)
    expected=cp.snapshot_from_history(s,v,as_of=date(2026,9,8))
    cached=[pd.read_csv(StringIO(frame.to_csv()),index_col=0) for frame in (s,v)]
    assert cp.snapshot_from_history(*cached,as_of=date(2026,9,8))==expected
    assert cp._session_dates(cached[0].index).equals(dates.tz_localize(None))
