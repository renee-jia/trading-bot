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


@pytest.mark.parametrize('vix,dd,target',[(15.72,-1.61,0),(18,-3,50000),(20,-5,125000),
    (23,-8,225000),(27,-10,325000),(30,-15,400000),(32,-12,325000),
    (22,-7.5,125000),(40,-1,0),(16,-12,0),(23,-5,125000)])
def test_joint_boundaries(vix,dd,target):
    p=cp.evaluate(snap(vix,dd),today=date(2026,9,8))
    assert p['target_deployed']==target
    assert p['target_cash']+p['target_deployed']==400000
    assert 0<=p['next_batch_cap']<=100000


def test_repeat_and_jump_do_not_double_buy():
    s=snap(30,-16)
    first=cp.evaluate(s,today=date(2026,9,8))
    assert first==cp.evaluate(s,today=date(2026,9,8))
    assert first['gap']==400000 and first['next_batch_cap']==100000
    funded=cp.evaluate(s,deployed=325000,today=date(2026,9,8))
    assert funded['gap']==funded['next_batch_cap']==75000
    done=cp.evaluate(s,deployed=400000,today=date(2026,9,8))
    assert done['status']=='funded' and done['gap']==0


def test_rebound_does_not_recommend_selling_to_restore_cash():
    p=cp.evaluate(snap(),deployed=125000,today=date(2026,9,8))
    assert p['gap']==0 and p['target_deployed']==125000 and p['target_cash']==275000


def test_time_rule_and_prior_dip():
    p=cp.evaluate(snap(day='2026-10-15'),today=date(2026,10,15))
    assert p['target_deployed']==50000 and p['capital_needs_confirmation']
    p=cp.evaluate(snap(day='2026-10-22'),today=date(2026,10,22),deployed=50000)
    assert p['target_deployed']==100000 and p['gap']==50000
    p=cp.evaluate(snap(day='2026-10-15',dip=True),today=date(2026,10,15))
    assert p['gap']==0
    p=cp.evaluate(snap(day='2026-10-14'),today=date(2026,10,14))
    assert p['gap']==0


def test_missing_stale_or_invalid_inputs_never_trigger():
    assert cp.evaluate({'status':'unavailable'},today=date(2026,9,8))['gap']==0
    p=cp.evaluate(snap(35,-20),today=date(2026,10,1),deployed=50000)
    assert p['status']=='unavailable' and p['target_cash']==350000 and p['gap']==0
    for value in [float('nan'),-1,400001]:
        with pytest.raises(ValueError):
            cp.evaluate(snap(),deployed=value)


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
    assert '$400k Cash' in text and '$350k' in text and '10/15' in text
    assert 'VIX 16.00' in text
    email=daily_watch.email_macro_lines(macro,today=date(2026,9,8))
    assert any('$400k Cash' in line for line in email)
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
