"""Read live public SPX/VIX closes and render the General cash-plan section."""
from datetime import date,datetime
import json
from pathlib import Path
import sys
import yfinance as yf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cash_entry_plan as cp
import report_generator as report


def run():
    yf.set_tz_cache_location('/tmp/trading-bot-yf-validation')
    spx=yf.Ticker('^GSPC').history(period='1y',auto_adjust=False)
    vix=yf.Ticker('^VIX').history(period='1y',auto_adjust=False)
    spx=cp.completed_daily_history(spx);vix=cp.completed_daily_history(vix)
    snapshot=cp.snapshot_from_history(spx,vix)
    assert snapshot['status']=='ok',snapshot
    plan=cp.evaluate(snapshot)
    output=Path('reports/cash_plan_validation')/datetime.now().strftime('%Y%m%d_%H%M%S')
    output.mkdir(parents=True,exist_ok=True)
    spx.to_csv(output/'SPX.csv');vix.to_csv(output/'VIX.csv')
    (output/'plan.json').write_text(json.dumps(plan,indent=2,ensure_ascii=False,allow_nan=False))
    # No LLM or broker call: General gracefully marks other absent macro inputs.
    md={'^GSPC':{'price':snapshot['spx'],'cash_plan_snapshot':snapshot},
        '^VIX':{'price':snapshot['vix']}}
    section=report._build_stock_trend_section([],{'market_data':md,'cash_entry_plan':plan})
    assert '现金入场计划' in section and '$' not in cp.render(plan) and snapshot['as_of'] in section
    (output/'general.md').write_text(section)
    print(json.dumps(snapshot,indent=2),flush=True)
    print(f"Tier: {plan['tier']}; cumulative target: {plan['target_pct']}% of pool; target cash: {plan['target_cash_pct']}%",flush=True)
    print('PASS: '+str(output),flush=True)


if __name__=='__main__':
    run()
