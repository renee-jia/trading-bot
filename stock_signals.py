"""Point-in-time stock differentiation signals and bounded score overlay.

OHLCV-only research features; no claims of observed institutional money flow.
"""
import math
import numpy as np
import pandas as pd

VERSION = 'stock-signals-v2'
WEIGHTS = {'residual_strength':.5, 'downside_resilience':.25, 'close_location_volume':.25}
MAX_ADJUSTMENT = 12.0


def _daily(frame):
    if frame is None or frame.empty:
        return pd.DataFrame()
    data = frame.copy()
    data.index = pd.to_datetime(data.index).tz_localize(None).normalize()
    return data[~data.index.duplicated(keep='last')].sort_index()


def _score(value, scale):
    return float(50+50*np.tanh(value/scale))


def analyze(data, benchmark=None):
    prices, bench = _daily(data), _daily(benchmark)
    result = {'version':VERSION, 'signals':{}, 'coverage':0.0, 'adjustment':0.0}
    if prices.empty or 'Close' not in prices:
        return result
    result['as_of'] = prices.index[-1].date().isoformat()
    close = pd.to_numeric(prices.Close, errors='coerce').where(lambda x:x>0)
    rets = np.log(close/close.shift(1)).replace([np.inf,-np.inf],np.nan)
    signals = result['signals']
    if not bench.empty and 'Close' in bench:
        bc = pd.to_numeric(bench.Close,errors='coerce').where(lambda x:x>0)
        br = np.log(bc/bc.shift(1))
        aligned = pd.concat([rets.rename('stock'),br.rename('market')],axis=1).loc[:prices.index[-1]].dropna()
        # Beta estimation and signal measurement use disjoint historical windows.
        if len(aligned) >= 189 and aligned.index[-1] == prices.index[-1]:
            fit, recent = aligned.iloc[-189:-63], aligned.iloc[-63:]
            var = float(fit.market.var())
            if var > 1e-10:
                beta = float(fit.stock.cov(fit.market)/var)
                residual = recent.stock-beta*recent.market
                risk = float(np.sqrt(np.square(residual).sum()))
                if risk > 1e-8:
                    strength = float(residual.sum()/risk)
                    signals['residual_strength'] = {
                        'score':_score(strength,2), 'value':strength, 'beta':beta,
                        'description':'63日 beta调整收益 / 残差风险；beta用此前126日估计'}
        if len(aligned) >= 63 and aligned.index[-1] == prices.index[-1]:
            recent = aligned.iloc[-63:]
            down = recent[recent.market < -.0015]
            if len(down) >= 10:
                capture = float(down.stock.mean()/down.market.mean())
                signals['downside_resilience'] = {
                    'score':_score(1-capture,.75), 'value':capture, 'observations':len(down),
                    'description':'SPY跌幅超过0.15%的日子：个股平均收益 / SPY平均收益'}
    cols = ['High','Low','Close','Volume']
    if all(c in prices for c in cols) and len(prices) >= 21:
        p = prices[cols].tail(21).apply(pd.to_numeric,errors='coerce')
        valid = (np.isfinite(p).all(axis=1) & (p.Volume > 0) & (p.High >= p.Low)
                 & (p.Close >= p.Low) & (p.Close <= p.High))
        if valid.all():
            span = p.High-p.Low
            location = ((2*p.Close-p.High-p.Low)/span.where(span>0)).fillna(0)
            flow = float((location*p.Volume).sum()/p.Volume.sum())
            signals['close_location_volume'] = {
                'score':_score(flow,.25), 'value':flow,
                'description':'21日成交量加权收盘位置；不等于真实资金净流入'}
    result['coverage'] = sum(WEIGHTS[k] for k in signals)
    # No missing-factor reweighting and no universe-dependent absolute score.
    result['adjustment'] = float(MAX_ADJUSTMENT/50*sum(
        WEIGHTS[k]*(v['score']-50) for k,v in signals.items()))
    return result


def apply(base, research, promote=False):
    """Compute an experimental score; production keeps baseline ratings by default."""
    from copy import deepcopy
    result = deepcopy(base)
    if not research or not research.get('signals'):
        result['score_version'] = 'legacy'
        result['research_signals'] = research or {}
        return result
    delta = max(-MAX_ADJUSTMENT,min(MAX_ADJUSTMENT,float(research['adjustment'])))
    score = round(max(0,min(100,base['score']+delta)),1)
    result.update(legacy_score=base['score'],score=score,raw_score=round(score/100,4),
                  score_version=VERSION,score_adjustment=round(score-base['score'],1),
                  research_signals=research)
    result['recommendation'] = ('Strong Buy' if score>=75 else 'Buy' if score>=60 else
                                'Hold' if score>=45 else 'Reduce' if score>=30 else 'Avoid')
    result['grade'] = ('A' if score>=80 else 'B+' if score>=70 else 'B' if score>=60 else
                       'C+' if score>=50 else 'C' if score>=40 else 'D' if score>=30 else 'F')
    # Model confidence has not been recalibrated; do not boost it for new factors.
    result['reasoning'] = [r for r in result.get('reasoning',[]) if r not in (
        'Strong multi-factor alignment for buy-and-hold',
        'Weak multi-factor profile, consider avoiding')]
    result['reasoning'].append(f"Stock-specific research adjustment {score-base['score']:+.1f} points; not a win probability")
    if not promote:
        result = deepcopy(base)
        result.update(legacy_score=base['score'], score_version='legacy+research-shadow',
                      score_adjustment=0.0, research_signals=research)
    result['research_score'] = score
    result['research_adjustment'] = round(score-base['score'],1)
    result['research_promoted'] = bool(promote)
    return result


def add_peer_ranks(rows, min_names=10):
    """Attach relative percentiles without altering scores/actions or input rows."""
    from copy import deepcopy
    result = deepcopy(rows)
    values = {}
    for row in result:
        try:
            value = float(row['score_result']['score'])
            if math.isfinite(value):
                values[row['ticker']] = value
        except (ValueError,TypeError,KeyError):
            pass
    series = pd.Series(values,dtype=float)
    if len(series)>=min_names:
        percentile = (series.rank(method='average')-1)/(len(series)-1)*100
    else:
        percentile = pd.Series(dtype=float)
    for row in result:
        sr = row['score_result']
        value = percentile.get(row['ticker'])
        sr['peer_percentile'] = round(float(value),1) if pd.notna(value) else None
        sr['peer_count'] = len(series)
    return result
