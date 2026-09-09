"""Read public Yahoo data, verify signal math, and render a report sample.

Run: .venv_trading/bin/python scripts/validate_options_research.py
No trading, email, broker-account reads or LLM calls. Writes under reports/validation.
The macro and news-sentiment scoring services are outside this smoke test.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import data_fetcher
import daily_watch
import options_research as opt
import report_generator
import sell_put_advisor
import covered_call_advisor


def run():
    yf.set_tz_cache_location('/tmp/trading-bot-yf-validation')
    output = Path('reports/validation') / datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True, exist_ok=True)
    snapshots, rows, checks = {}, [], []

    class Capture:
        def __init__(self, symbol):
            self.symbol = symbol
            self.source = yf.Ticker(symbol)
            self.prices = None
        def history(self, **kwargs):
            self.prices = self.source.history(**kwargs)
            self.prices.to_csv(output / f'{self.symbol}_prices.csv')
            return self.prices
        def option_chain(self, expiry):
            chain = self.source.option_chain(expiry)
            chain.calls.to_csv(output / f'{self.symbol}_{expiry}_calls.csv', index=False)
            chain.puts.to_csv(output / f'{self.symbol}_{expiry}_puts.csv', index=False)
            return chain
        def __getattr__(self, name):
            return getattr(self.source, name)

    benchmark = data_fetcher.fetch_benchmark(period='2y')
    for symbol in ['AAPL', 'NVDA', 'SPY']:
        captured = Capture(symbol)
        r = opt.analyze_ticker(symbol, ticker_factory=lambda _: captured)
        prices = captured.prices
        adjusted = prices.get('Adj Close', prices['Close']).to_numpy(dtype=float)[-22:]
        returns = [math.log(adjusted[i]/adjusted[i-1]) for i in range(1, len(adjusted))]
        mean = sum(returns)/21
        rv_reference = math.sqrt(sum((v-mean)**2 for v in returns)/20)*math.sqrt(252)
        assert math.isclose(r['rv21'], rv_reference, rel_tol=1e-10)
        assert math.isclose(r['expected_move_pct'], r['atm_iv']*math.sqrt(r['dte']/365)*100)
        assert r['atm_spread_pct'] <= opt.MAX_SPREAD*100
        assert all(not isinstance(v, float) or math.isfinite(v) for v in r.values())
        snapshots[symbol] = r
        daily = data_fetcher.fetch_price_data(symbol)
        assert daily is not None, f'{symbol}: missing equity history'
        score, technical = main.analyze_stock(symbol, daily, benchmark,
                                              alpha_score=main.compute_alpha_score(daily))
        rows.append({'ticker':symbol, 'name':symbol, 'sector':'Unknown',
                     'score_result':score, 'indicators':technical['indicators'],
                     'news_data':data_fetcher.fetch_news(symbol)})
        checks.append({'ticker':symbol, 'rv_reference_verified':True,
                       'iv_rv_ratio':r['iv_rv_ratio'], 'earnings_status':r['earnings_status'],
                       'news_input_count':len(rows[-1]['news_data'])})
        print(json.dumps(checks[-1]), flush=True)

    # Unified advisors consume the same decisions; only broker reads are excluded.
    with patch.object(opt, 'analyze_ticker', side_effect=lambda symbol, **kw: dict(snapshots[symbol])), \
         patch.object(daily_watch, 'held_tickers', return_value=set()), \
         patch.object(report_generator, '_build_alpaca_performance', return_value=''):
        decisions = {}
        path, text = report_generator.generate_report(rows, output_dir=str(output), options_decisions=decisions)
        email_lines = daily_watch.email_watch_lines(
            [{**row,'options_decision':decisions[row['ticker']]} for row in rows], held=set())
    from options_decision import label
    for symbol, decision in decisions.items():
        assert label(decision) in text
        assert any(label(decision) in line for line in email_lines)
    assert 'Options Research' in text
    assert 'Section failed:' not in text
    assert '数据不足 / 获取失败' not in text
    for row in rows:
        assert report_generator._compact_stock_news(row).count('\n- ') <= 2
    summary = {'validated_at':datetime.now(timezone.utc).isoformat(), 'checks':checks,
               'report':path, 'scope':'Public live option and equity data; full report renderer. '
               'Macro service, sentiment AI and broker excluded; unified advisors and email summary checked.'}
    summary['decisions'] = {s:{'action':d['action'],'score':d['score'],'status':d['status']} for s,d in decisions.items()}
    (output/'validation.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print('PASS: ' + str(output), flush=True)


if __name__ == '__main__':
    run()
