"""Bounded option-chain research overlay; never supplies execution weights.

IV/RV is a backward-looking richness proxy, not a forecast variance premium.
Yahoo snapshots have no reliable bid/ask timestamp; all outputs need live checks.
"""
from datetime import date, datetime, timezone
import math
import os

import numpy as np
import pandas as pd
import yfinance as yf

MAX_NAMES = 8
MAX_SPREAD = 0.20
MIN_OI = 50
_FALLBACK_WATCH = (
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "AVGO",
    "TSM", "SMH",
)


def liquid_chain(frame):
    """Only finite, two-sided, liquid quotes in a plausible IV range."""
    cols = ['strike', 'bid', 'ask', 'impliedVolatility', 'openInterest', 'volume']
    data = frame.reindex(columns=cols).apply(pd.to_numeric, errors='coerce').copy()
    mid = (data.bid + data.ask) / 2
    valid = (np.isfinite(data[cols[:5]]).all(axis=1)
             & (data.strike > 0) & (data.bid > 0) & (data.ask >= data.bid)
             & data.impliedVolatility.between(0.01, 5)
             & (data.openInterest >= MIN_OI)
             & ((data.ask - data.bid) / mid <= MAX_SPREAD))
    data['volume'] = data['volume'].where(np.isfinite(data['volume']) & (data['volume'] >= 0))
    return data.loc[valid].copy()


def _near(frame, strike, tolerance):
    if frame.empty:
        return None
    row = frame.loc[(frame.strike - strike).abs().idxmin()]
    return row if abs(row.strike / strike - 1) <= tolerance else None


def chain_metrics(chain, spot, dte):
    calls, puts = liquid_chain(chain.calls), liquid_chain(chain.puts)
    call, put = _near(calls, spot, 0.03), _near(puts, spot, 0.03)
    if call is None or put is None:
        raise ValueError('缺少流动性合格的 ATM 双边报价')
    iv = float((call.impliedVolatility + put.impliedVolatility) / 2)
    # Symmetric moneyness proxy, deliberately not labelled 25-delta skew.
    downside, upside = _near(puts, spot * 0.95, 0.015), _near(calls, spot * 1.05, 0.015)
    skew = (float(downside.impliedVolatility - upside.impliedVolatility)
            if downside is not None and upside is not None else None)
    cv, pv = calls.volume.clip(lower=0).sum(min_count=1), puts.volume.clip(lower=0).sum(min_count=1)
    def records(filtered, raw):
        result = []
        for idx, row in filtered.iterrows():
            item = {k: float(row[k]) for k in ('strike','bid','ask','impliedVolatility','openInterest')}
            item['standard_contract'] = raw.loc[idx].get('contractSize') == 'REGULAR'
            item['contract_symbol'] = str(raw.loc[idx].get('contractSymbol') or '')
            result.append(item)
        return result
    return {
        'calls': records(calls, chain.calls), 'puts': records(puts, chain.puts),
        'atm_iv': iv, 'expected_move_pct': iv * math.sqrt(dte / 365) * 100,
        'skew_5pct': skew,
        'put_call_volume': float(pv / cv) if cv > 0 and pd.notna(pv) else None,
        'contracts_used': len(calls) + len(puts),
        'atm_spread_pct': max(float((r.ask-r.bid)/((r.ask+r.bid)/2)*100) for r in (call, put)),
    }


def _is_monthly(day):
    """Third Friday (standard monthly expiry)."""
    return day.weekday() == 4 and 15 <= day.day <= 21


def analyze_ticker(symbol, today=None, ticker_factory=None):
    today = today or date.today()
    if ticker_factory is None:
        import option_data
        ticker_factory = lambda s: option_data.ticker(s, today=today)
    ticker = ticker_factory(symbol)
    history = ticker.history(period='6mo', auto_adjust=False)
    closes = pd.to_numeric(history.get('Adj Close', history['Close']), errors='coerce')
    if len(closes) < 22 or not np.isfinite(closes.tail(22)).all() or (closes.tail(22) <= 0).any():
        raise ValueError('不足 21 个有效日收益率')
    spot = float(history['Close'].iloc[-1])
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError('无有效标的价格')
    returns = np.log(closes / closes.shift(1))
    rv = float(returns.tail(21).std(ddof=1) * np.sqrt(252))
    rv63 = float(returns.tail(63).std(ddof=1)*np.sqrt(252)) if len(closes) >= 64 and np.isfinite(returns.tail(63)).all() else None
    price_date = history.index[-1].date() if isinstance(history.index, pd.DatetimeIndex) else None
    expiries = [(e, (date.fromisoformat(e)-today).days) for e in (ticker.options or ())]
    if not expiries:
        raise ValueError('期权链不可用：数据源没有返回到期日')
    near = [x for x in expiries if 25 <= x[1] <= 45]
    if not near:
        raise ValueError('无 25–45 DTE 期权链')
    # Monthlies carry the open interest; a weekly at 30 DTE often fails the
    # OI>=50 ATM gate even on very liquid names.
    monthlies = [x for x in near if _is_monthly(date.fromisoformat(x[0]))]
    expiry, dte = min(monthlies or near, key=lambda x: abs(x[1]-30))
    result = chain_metrics(ticker.option_chain(expiry), spot, dte)
    result.update(ticker=symbol, expiry=expiry, dte=dte, spot=spot, rv21=rv, rv63=rv63,
                  price_date=price_date.isoformat() if price_date else None,
                  price_age_days=(today-price_date).days if price_date else None,
                  iv_rv_ratio=result['atm_iv']/rv if rv > 0 else None,
                  hv21_percentile=realized_vol_percentile(closes),
                  term_slope=None, far_expiry=None, earnings_status='unknown',
                  fetched_at=datetime.now(timezone.utc).isoformat())
    far = [x for x in expiries if 55 <= x[1] <= 90]
    if far:
        far_exp, far_dte = min(far, key=lambda x: abs(x[1]-60))
        try:
            far_metrics = chain_metrics(ticker.option_chain(far_exp), spot, far_dte)
            result.update(term_slope=far_metrics['atm_iv']-result['atm_iv'], far_expiry=far_exp)
        except Exception:
            pass  # Missing far quotes do not invalidate the front snapshot.
    try:
        if ticker.get_info().get('quoteType') == 'ETF':
            result['earnings_status'] = 'not_applicable'
            result['signals'] = snapshot_signals(result)
            return result
    except Exception:
        pass
    try:
        earnings = ticker.calendar.get('Earnings Date') or []
        dates = [pd.Timestamp(e).date() for e in earnings]
        dates = [e for e in dates if e >= today]
        if dates:
            result['earnings_status'] = ('crosses' if (min(dates)-date.fromisoformat(expiry)).days < 2
                                         else 'clear')
    except Exception:
        pass
    result['signals'] = snapshot_signals(result)
    return result


def realized_vol_percentile(closes, window=21):
    """Where the latest 21-day HV sits in this history. Not an IV rank."""
    closes = pd.to_numeric(closes, errors='coerce')
    if len(closes) < window + 20:
        return None
    hv = np.log(closes / closes.shift(1)).rolling(window).std(ddof=1) * np.sqrt(252)
    series = hv[np.isfinite(hv) & (hv > 0)]
    if len(series) < 20:
        return None
    last = float(series.iloc[-1])
    return round(float((series <= last).mean() * 100), 1)


def snapshot_signals(result):
    """Label the current chain snapshot. Screening tags, not order tickets."""
    signals = []
    iv, rv21, rv63 = result.get('atm_iv'), result.get('rv21'), result.get('rv63')
    if all(isinstance(x, (int, float)) and x > 0 for x in (iv, rv21, rv63)):
        cheap = max(iv / rv21, iv / rv63) <= 0.9
        rich = min(iv / rv21, iv / rv63) >= 1.2
        if cheap:
            signals.append({'id': 'iv_cheap', 'label': 'IV偏便宜'})
        elif rich:
            signals.append({'id': 'iv_rich', 'label': 'IV偏贵'})
    hv = result.get('hv21_percentile')
    if isinstance(hv, (int, float)):
        if hv >= 80:
            signals.append({'id': 'hv_elevated', 'label': 'HV近半年偏高'})
        elif hv <= 20:
            signals.append({'id': 'hv_suppressed', 'label': 'HV近半年偏低'})
    slope = result.get('term_slope')
    if isinstance(slope, (int, float)) and slope < -0.03:
        signals.append({'id': 'term_inverted', 'label': '近月倒挂'})
    skew = result.get('skew_5pct')
    if isinstance(skew, (int, float)) and skew >= 0.04:
        signals.append({'id': 'put_skew', 'label': '下行偏斜陡'})
    pc = result.get('put_call_volume')
    if isinstance(pc, (int, float)) and pc >= 1.5:
        signals.append({'id': 'pc_heavy', 'label': 'P/C量比高'})
    return signals


def priority_tickers():
    """CORE_WATCH then covered-call watch names; first-seen order, no sizes."""
    names = []
    try:
        from configs import CORE_WATCH
        names.extend(CORE_WATCH)
    except ImportError:
        names.extend(_FALLBACK_WATCH)
    raw = os.environ.get('CC_WATCH', '').strip() or os.environ.get('CC_POSITIONS', '').strip()
    for part in raw.split(','):
        token = part.strip().split(':')[0].upper()
        if token:
            names.append(token)
    seen, ordered = set(), []
    for name in names:
        ticker = str(name).strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


def select_research_cards(cards):
    """Scan the watch list first, then the strongest remaining directional names."""
    by_ticker = {}
    for card in cards:
        if card.get('levered'):
            continue
        ticker = card['ticker']
        if ticker not in by_ticker:
            by_ticker[ticker] = card
    selected, seen = [], set()
    for ticker in priority_tickers():
        card = by_ticker.get(ticker)
        if card is None:
            continue
        selected.append(card)
        seen.add(ticker)
        if len(selected) == MAX_NAMES:
            return selected
    rest = sorted(
        (card for ticker, card in by_ticker.items() if ticker not in seen),
        key=lambda card: -abs((card.get('score') if card.get('score') is not None else 50) - 50),
    )
    for card in rest:
        selected.append(card)
        if len(selected) == MAX_NAMES:
            break
    return selected


def research_stance(result, card, event, row=None):
    """Compatibility wrapper: absent directional evidence means wait."""
    from options_decision import evaluate, label
    return label(evaluate(result, row or {}, card, event))


def build_section(ranked, macro_result=None, analyzer=None, held=None, today=None):
    import daily_watch
    cards, event = daily_watch.annotate(
        ranked, held=held, today=today, tape=(macro_result or {}).get('macro_tape'))
    selected = select_research_cards(cards)
    lines = ['## Options Research — 期权链信号\n',
             '优先扫 CORE_WATCH / 写 call 观察名单，再补方向最极端的名字，最多 8 只；前月 25–45 DTE、远月 55–90 DTE。研究快照，不是下单指令。\n',
             '| 股票 / 到期 | ATM IV / RV21 / RV63 | IV÷RV21 / RV63 | HV21分位 | 预期 ±波动 | 5%偏斜 | 远−近 IV | P/C量比 | ATM价差 | 结构信号 | 研究方向 |',
             '|---|---|---|---|---|---|---|---|---|---|---|']
    from options_decision import evaluate, label, render_tickets
    row_map = {r['ticker'].upper(): r for r in ranked}
    results = []
    def fmt(value, scale=1, suffix=''):
        return '—' if value is None else f'{value*scale:.2f}{suffix}'
    for card in selected:
        symbol = card['ticker']
        try:
            r = (analyzer or analyze_ticker)(symbol, today=today)
            r['decision'] = evaluate(r, row_map.get(symbol, {}), card, event,
                                     (macro_result or {}).get('macro_tape'))
            r['stance'] = label(r['decision'])
            r['signals'] = r.get('signals') or snapshot_signals(r)
            results.append(r)
            tags = '；'.join(s['label'] for s in r['signals']) or '—'
            lines.append(f"| {symbol} / {r['expiry']} | {r['atm_iv']*100:.1f}% / {r['rv21']*100:.1f}% / {fmt(r.get('rv63'),100,'%')} | "
                         f"{fmt(r['iv_rv_ratio'])} / {fmt(r['atm_iv']/r['rv63'] if r.get('rv63') else None)} | "
                         f"{fmt(r.get('hv21_percentile'), suffix='%')} | {r['expected_move_pct']:.1f}% | "
                         f"{fmt(r['skew_5pct'],100,'pt')} | {fmt(r['term_slope'],100,'pt')} | "
                         f"{fmt(r['put_call_volume'])} | {r['atm_spread_pct']:.1f}% | {tags} | {r['stance']} |")
        except Exception as exc:
            results.append({'ticker': symbol, 'status': 'unavailable', 'error_type':type(exc).__name__})
            lines.append(f'| {symbol} | — | — | — | — | — | — | — | — | — | 数据不足 / 获取失败 |')
    lines.append('\n### 操作评分与候选合约\n')
    lines.append('方向分来自未叠加奖励的技术/趋势分并由价格确认；适配分不是胜率。缺失数据不填中性分。')
    for r in results:
        d = r.get('decision')
        if d:
            lines.append(f"{r['ticker']}：方向分 {fmt(d['direction']['score'])} / {d['direction']['bias']}；{r['stance']}")
    lines.append(render_tickets({r['ticker']:r['decision'] for r in results if 'decision' in r},
                               {'bull_call_debit','bear_put_debit','bull_put_credit','bear_call_credit'}))
    lines += ['', 'IV÷RV 是历史波动比较，不等于已验证的收益优势；1.2/0.9 与倒挂 3pt 为待验证筛选阈值。',
              'HV21 分位是近半年实现波动的位置，不是期权 IV Rank。预期波动 = IV×√(DTE/365)，不保证覆盖实际涨跌。偏斜为同到期 95% put IV−105% call IV。',
              'P/C 仅覆盖通过筛选的合约，不代表买卖方向或全市场资金流。OI≥50、双边报价、价差≤20%；报价时间无法确认，交易前核验实时价格、财报、宏观日历和逐腿流动性。',
              '结构信号仍是当前快照标签，不参与股票评分和自动下单。所有动作仅指新开仓候选；没有现有期权持仓成本数据，不能据此给出平仓/滚动指令。\n', '---\n']
    return '\n'.join(lines), results
