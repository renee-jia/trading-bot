"""Historical paired score validation, public Yahoo data only; no trading/LLMs.

Monthly cross-sections from 2022 with T+1 close entries and 20-session labels.
Fixed present-day survivors; a ranking diagnostic, not a production backtest.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'core')]
import scorer
import strategy
import stock_signals
import technical_analyzer
import trend_analyzer
from configs import ANALYZER_WEIGHTS

SYMBOLS = ['AAPL','MSFT','NVDA','AMD','AMZN','META','JPM','BAC',
           'XOM','CVX','JNJ','UNH','PG','KO','CAT','WMT']
CACHE = ROOT/'reports'/'score_validation_cache'


def fetch(symbol):
    path = CACHE/f'{symbol}.csv'
    if path.exists():
        return symbol,pd.read_csv(path,index_col=0,parse_dates=True)
    history = yf.Ticker(symbol).history(start='2020-01-01',auto_adjust=True)
    if history.empty:
        raise RuntimeError(f'{symbol}: no data')
    history.index = history.index.tz_localize(None).normalize()
    history.to_csv(path)
    return symbol,history


def metrics(frame):
    records=[]
    for day,g in frame.groupby('date'):
        if len(g)<10:
            continue
        row={'date':day,'n':len(g)}
        for version in ['old','new']:
            s=g[version]
            row[version+'_ic']=float(s.rank().corr(g.forward.rank()))
            row[version+'_dispersion']=float(s.std(ddof=0))
            row[version+'_iqr']=float(s.quantile(.75)-s.quantile(.25))
            row[version+'_unique_integer']=int(s.round().nunique())
            # Average returns for score ties; never break ties by ticker order.
            q=max(1,len(g)//4)
            ranked=g.sort_values(version)
            lo,hi=ranked.iloc[q-1][version],ranked.iloc[-q][version]
            row[version+'_top_bottom']=float(g[g[version]>=hi].forward.mean()-g[g[version]<=lo].forward.mean())
        records.append(row)
    return pd.DataFrame(records)


def summarize(months):
    if months.empty:
        return {}
    result={'cross_sections':len(months)}
    for col in months.columns:
        if col not in ('date','n'):
            result[col]=float(months[col].mean())
    delta=(months.new_ic-months.old_ic).dropna().to_numpy()
    if len(delta)>=6:
        rng=np.random.default_rng(42)
        # Paired circular blocks preserve some serial dependence; not model selection.
        starts=rng.integers(0,len(delta),size=(2000,int(np.ceil(len(delta)/3))))
        samples=np.stack([delta[(starts+i)%len(delta)] for i in range(3)],axis=-1).reshape(2000,-1)
        samples=samples[:,:len(delta)].mean(axis=1)
        result['ic_delta_ci95']=np.quantile(samples,[.025,.975]).tolist()
    return result


def run(download_only=False):
    CACHE.mkdir(parents=True,exist_ok=True)
    yf.set_tz_cache_location('/tmp/trading-bot-yf-validation')
    with ThreadPoolExecutor(max_workers=4) as pool:
        data=dict(pool.map(fetch,SYMBOLS+['SPY','^VIX']))
    print(f'Data ready: {len(data)} symbols',flush=True)
    if download_only:
        return
    output=ROOT/'reports'/'score_validation'/datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True,exist_ok=True)
    spy=data['SPY']
    dates=spy.index[spy.index >= pd.Timestamp('2022-01-01')]
    month_ends=pd.Series(dates,index=dates).groupby(dates.to_period('M')).last()
    records=[]
    started=time.monotonic()
    for day in month_ends:
        idx=spy.index.get_loc(day)
        if idx+21>=len(spy):
            continue
        entry,finish=spy.index[idx+1],spy.index[idx+21]
        for symbol in SYMBOLS:
            prices=data[symbol]
            history=prices.loc[:day].tail(500)
            if len(history)<252 or history.index[-1]!=day or entry not in prices.index or finish not in prices.index:
                continue
            benchmark=spy.loc[:day].tail(500)
            tech=technical_analyzer.analyze(history)
            trend=trend_analyzer.analyze(history,benchmark,vix_data=data['^VIX'].loc[:day])
            alpha=strategy.compute_alpha_score(history)
            sent={'score':.5,'confidence':0}
            old=scorer.score_stock(tech,sent,trend,alpha_score=alpha,weights=ANALYZER_WEIGHTS)
            research=stock_signals.analyze(history,benchmark)
            new=stock_signals.apply(old,research,promote=True)
            record={'date':str(day.date()),'ticker':symbol,'old':old['score'],'new':new['score'],
                    'adjustment':research['adjustment'],'coverage':research['coverage'],
                    'forward':float(prices.loc[finish,'Close']/prices.loc[entry,'Close']-1),
                    'entry_date':str(entry.date()),'exit_date':str(finish.date()),
                    'vix_as_of':str(day.date())}
            for name,signal in research['signals'].items():
                record[name]=signal['score']
            records.append(record)
        print(f'{day.date()}: {len(records)} observations, {time.monotonic()-started:.0f}s',flush=True)
        pd.DataFrame(records).to_csv(output/'observations.csv',index=False)
    frame=pd.DataFrame(records)
    monthly=metrics(frame)
    monthly.to_csv(output/'cross_sections.csv',index=False)
    summary={
        'all':summarize(monthly),
        'development_2022_2024':summarize(monthly[monthly.date<'2025-01-01']),
        'holdout_2025_plus':summarize(monthly[monthly.date>='2025-01-01']),
        'coverage_mean':float(frame.coverage.mean()),'observations':len(frame),
        'feature_ic':{},
        'limitations':['Fixed current-survivor universe; 16 names; no historical constituents',
                      'No historical sentiment, hourly bars, macro AI or options',
                      'Top-minus-bottom forward spread is gross, not tradable portfolio P&L',
                      'No threshold fitting; holdout is a diagnostic, not proof of alpha']}
    for feature in stock_signals.WEIGHTS:
        vals=[]
        for _,g in frame.groupby('date'):
            if g[feature].notna().sum()>=10:
                vals.append(g[feature].rank().corr(g.forward.rank()))
        summary['feature_ic'][feature]=float(np.nanmean(vals))
    (output/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False))
    # Current cross-section uses the exact same functions and historical-only VIX.
    current=[]
    for symbol in SYMBOLS:
        history=data[symbol].tail(500)
        day=history.index[-1]
        tech=technical_analyzer.analyze(history)
        trend=trend_analyzer.analyze(history,spy.loc[:day].tail(500),vix_data=data['^VIX'].loc[:day])
        old=scorer.score_stock(tech,{'score':.5,'confidence':0},trend,
                               alpha_score=strategy.compute_alpha_score(history),weights=ANALYZER_WEIGHTS)
        sr=stock_signals.apply(old,stock_signals.analyze(history,spy.loc[:day].tail(500)),promote=True)
        current.append({'ticker':symbol,'score_result':sr})
    current=stock_signals.add_peer_ranks(current)
    current.sort(key=lambda r:r['score_result']['score'],reverse=True)
    (output/'current.json').write_text(json.dumps(current,indent=2,allow_nan=False))
    lines=['# Stock score validation','', 'Candidate scores are experimental and do not replace active buy/sell ratings.', '',f'Observations: {len(frame)}; monthly cross-sections: {len(monthly)}.',
           '', '| Sample | Old IC | New IC | Old score SD | New score SD | Old top-bottom | New top-bottom |',
           '|---|---|---|---|---|---|---|']
    for label in ['all','development_2022_2024','holdout_2025_plus']:
        m=summary[label]
        lines.append(f"| {label} | {m['old_ic']:.3f} | {m['new_ic']:.3f} | {m['old_dispersion']:.2f} | {m['new_dispersion']:.2f} | {m['old_top_bottom']:.2%} | {m['new_top_bottom']:.2%} |")
    lines+=['','## Current cross-section','', '| Ticker | Original | Experimental | Hypothetical change | Experimental percentile |',
            '|---|---|---|---|---|']
    for row in current:
        sr=row['score_result']
        lines.append(f"| {row['ticker']} | {sr['legacy_score']:.1f} | {sr['score']:.1f} | {sr['score_adjustment']:+.1f} | {sr['peer_percentile']:.1f} |")
    lines+=['','## Limits','']+['- '+x for x in summary['limitations']]
    (output/'validation.md').write_text('\n'.join(lines))
    print(json.dumps(summary,indent=2),flush=True)
    print('RESULT: '+str(output),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--download-only',action='store_true')
    run(parser.parse_args().download_only)
