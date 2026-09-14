"""Full report replay: cached equities + fresh public macro/options, no orders/mail/AI."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'core')]
import pandas as pd
from bs4 import BeautifulSoup
import yfinance as yf
import cash_entry_plan
import covered_call_advisor
import daily_watch
import macro_analyzer
import options_research
import report_generator
from report_format import EMAIL_BUDGET, email_digest, render_html
import scorer
import sell_put_advisor
import stock_signals
import strategy
import technical_analyzer
import trend_analyzer
from configs import ANALYZER_WEIGHTS


def validate(markdown, html, rows):
    assert 'Section failed:' not in markdown
    assert not re.search(r'(?i)\b(?:nan|inf)\b', markdown)
    assert markdown.index('## Executive Summary') < markdown.index('## General')
    assert markdown.index('## General') < markdown.index('## 今日建议买入')
    assert '现金入场计划' in markdown and '$400k' not in markdown and '候选分（未启用）' in markdown
    assert '## SaaS Watch' in markdown and markdown.index('## AI Portfolio') < markdown.index('## SaaS Watch') < markdown.index('## 今日建议买入')
    assert '## AI Portfolio' in markdown and markdown.index('## AI Portfolio') < markdown.index('## 今日建议买入')
    assert 'Bottom 20 — Sell/Avoid' not in markdown
    width = None
    for line in markdown.splitlines():
        if line.startswith('|') and line.endswith('|'):
            cells = re.split(r'(?<!\\)\|', line)[1:-1]
            width = width or len(cells)
            assert len(cells) == width, f'Malformed table: {line}'
        else:
            width = None
    soup = BeautifulSoup(html, 'html.parser')
    assert len(soup.select('details')) == len(rows)
    assert len(soup.select('table')) >= 10
    assert not soup.select('script')
    ids = [tag['id'] for tag in soup.select('[id]')]
    assert len(ids) == len(set(ids))
    assert all(a['href'][1:] in ids for a in soup.select('nav a'))
    for row in rows:
        assert f"### {row['ticker']} - " in markdown
        assert not row['score_result'].get('research_promoted')
    email = BeautifulSoup(render_html(markdown, email=True), 'html.parser')
    assert not email.select('details')
    assert len(email.select('table')) == len(soup.select('table'))
    # The mailed body must stay under Gmail's clip limit and keep the decision desks.
    digest, omitted = email_digest(markdown)
    assert len(digest.encode('utf-8')) <= EMAIL_BUDGET, len(digest)
    for key in ('Executive Summary', 'General', 'AI Portfolio', '今日建议买入'):
        assert key in digest and not any(key in o for o in omitted), key
    assert 'Detailed Analysis' in omitted
    return {'stocks':len(rows), 'tables':len(soup.select('table')),
            'sections':len(ids), 'collapsed_stock_details':len(soup.select('details')),
            'email_digest_bytes':len(digest.encode('utf-8')), 'email_omitted':omitted}


def run(replay=None):
    yf.set_tz_cache_location('/tmp/trading-bot-yf-validation')
    output = ROOT / 'reports' / 'report_validation' / datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True, exist_ok=True)
    if replay:
        inputs = json.loads(Path(replay).read_text())
        rows, macro, snapshots = inputs['rows'], inputs['macro'], inputs['snapshots']
    else:
        cache = ROOT / 'reports' / 'score_validation_cache'
        spy = pd.read_csv(cache/'SPY.csv', index_col=0, parse_dates=True)
        vix = pd.read_csv(cache/'^VIX.csv', index_col=0, parse_dates=True)
        rows = []
        for symbol in ['AAPL','MSFT','NVDA','AMD','AMZN','META','JPM','BAC',
                       'XOM','CVX','JNJ','UNH','PG','KO','CAT','WMT']:
            history = pd.read_csv(cache/f'{symbol}.csv', index_col=0, parse_dates=True).tail(500)
            day = history.index[-1]
            technical = technical_analyzer.analyze(history)
            trend = trend_analyzer.analyze(history, spy.loc[:day].tail(500), vix_data=vix.loc[:day])
            score = scorer.score_stock(technical, {'score':.5,'confidence':0}, trend,
                         alpha_score=strategy.compute_alpha_score(history), weights=ANALYZER_WEIGHTS,
                         research_result=stock_signals.analyze(history, spy.loc[:day].tail(500)))
            rows.append({'ticker':symbol,'name':symbol,'sector':'Unknown',
                         'score_result':score,'indicators':technical['indicators'],
                         'price_as_of':str(day.date())})
        print('Equity scoring ready: 16 cached names; fetching public macro data.', flush=True)
        market = macro_analyzer._fetch_market_data()
        assert len(market) == 17, f'Missing macro series: {len(market)}/17'
        assert market['^GSPC']['cash_plan_snapshot']['status'] == 'ok'
        quant, signals = macro_analyzer._quantitative_macro_score(market)
        trend = macro_analyzer.classify_market_trend(market)
        macro = {'score':quant,'quant_score':quant,'quant_signals':signals,
                 'recommendation':macro_analyzer._get_macro_recommendation(quant),
                 'market_data':market,'analysis':{},'news':[], 'trend':trend,
                 'trend_outlook':macro_analyzer.fallback_trend_outlook(trend,quant,market),
                 'macro_tape':macro_analyzer.classify_macro_tape(market),
                 'cash_entry_plan':cash_entry_plan.from_market_data(market)}
        print('Macro ready; fetching option snapshots.', flush=True)
        with patch.object(daily_watch, 'held_tickers', return_value=set()):
            _, snapshots = options_research.build_section(rows, macro)
        import ai_sell_put_plan
        print('Fetching AI sell-put plan.', flush=True)
        sell_put_plan = ai_sell_put_plan.build_plan(rows, macro_result=macro, held=set())
        inputs = {'rows':rows,'macro':macro,'snapshots':snapshots,'sell_put_plan':sell_put_plan,
                  'scope':'16 cached equity histories; fresh public macro/options; neutral missing sentiment. '
                          'No AI, broker holdings, discovery or email sending. Holdings excluded from validation.'}
    (output/'inputs.json').write_text(json.dumps(inputs,ensure_ascii=False,indent=2,allow_nan=False,default=str))
    # Replayed plans carry ISO strings where the live run had dates; the renderer accepts both.
    sell_put_plan = inputs.get('sell_put_plan') or {'error':'saved sample predates the AI sell-put plan'}
    lookup = {s['ticker']:s for s in snapshots}
    assert any('decision' in s for s in snapshots), 'No usable options: inspect saved inputs'
    def replay_option(symbol, **kwargs):
        result = lookup[symbol]
        if 'decision' not in result:
            raise ValueError('Saved snapshot unavailable')
        return dict(result)
    decisions = {}
    # Holdings stay out of the validation artifact; the panic-drop radar keeps
    # its section but must not fetch chains during a replay.
    with patch.object(daily_watch,'held_tickers',return_value=set()), \
         patch.object(report_generator,'_build_alpaca_performance',return_value=''), \
         patch.object(covered_call_advisor,'build_covered_call_ladders',return_value=''), \
         patch.object(sell_put_advisor,'analyze_put_ladder',
                      side_effect=ValueError('validation replay: chain fetch disabled')), \
         patch.object(options_research,'analyze_ticker',side_effect=replay_option):
        path, markdown = report_generator.generate_report(rows,output_dir=str(output),macro_result=macro,
                                                          options_decisions=decisions,
                                                          sell_put_plan=sell_put_plan)
    from options_decision import label
    cards = [{**r,'options_decision':decisions.get(r['ticker'],
              {'status':'wait','action':'wait','reasons':['本次期权链未评估或数据不可用']})} for r in rows]
    for card in cards:
        # A per-name email check avoids mistaking another stock's matching label for consistency.
        lines = daily_watch.email_watch_lines([card],held=set(),macro_result=macro)
        assert label(card['options_decision']) in markdown
        if lines:
            assert any(label(card['options_decision']) in line for line in lines)
    # Make the replay scope explicit in the artifact, not only in a test log.
    note = ('> 验证样本：16 只股票的缓存日线（' + rows[0]['price_as_of'] +
            '）；宏观与期权来自保存的公开行情快照。新闻情绪缺失，未接入 AI、账户持仓或发现新股。'
            '本次验证报告生成和规则一致性，不验证收益表现。\n\n')
    markdown = markdown.replace('\n\n', '\n\n' + note, 1)
    html = render_html(markdown)
    Path(path).write_text(markdown)
    Path(path).with_suffix('.html').write_text(html)
    Path(path).with_name(Path(path).stem + '_email.html').write_text(email_digest(markdown)[0])
    checks = validate(markdown, html, rows)
    checks.update(report=path,scope=inputs['scope'],options_snapshots=len(snapshots),
                  options_usable=len(decisions), options_unavailable=[s['ticker'] for s in snapshots if 'decision' not in s])
    (output/'validation.json').write_text(json.dumps(checks,indent=2,ensure_ascii=False))
    print(json.dumps(checks,indent=2,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay',help='Previously saved inputs.json; avoids network requests')
    run(parser.parse_args().replay)
