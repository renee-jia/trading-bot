"""Explainable option-entry suitability, not calibrated win probability.

No execution or position-exit instructions. Every candidate requires live quote,
account, dividend and assignment review before a possible order.
"""
import math

VERSION = 'options-entry-v2'
LABELS = {
    'bull_call_debit': '买入 call debit spread',
    'bear_put_debit': '买入 put debit spread',
    'bull_put_credit': '卖出 put credit spread',
    'bear_call_credit': '卖出 call credit spread',
    'covered_call': '卖出 covered call',
    'cash_secured_put': '卖出现金担保 put',
}


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def direction(row):
    """Use unboosted technical/trend scores once; require price confirmation."""
    sr, ind = row.get('score_result') or {}, row.get('indicators') or {}
    components = sr.get('components') or {}
    tech, trend = components.get('technical') or {}, components.get('trend') or {}
    ts, rs = number(tech.get('score')), number(trend.get('score'))
    tc, rc = number(tech.get('confidence')), number(trend.get('confidence'))
    price, sma, m1 = [number(ind.get(k)) for k in ('price', 'sma_50', 'change_1m')]
    if None in (ts, rs, tc, rc, price, sma, m1) or price <= 0 or sma <= 0:
        return {'score':None, 'confidence':0, 'bias':'unknown', 'reason':'方向数据缺失'}
    if not (0 <= ts <= 100 and 0 <= rs <= 100 and 0 <= tc <= 1 and 0 <= rc <= 1):
        return {'score':None, 'confidence':0, 'bias':'unknown', 'reason':'方向数据越界'}
    confidence = min(tc, rc)
    score = 50 + (((ts+rs)/2)-50)*confidence
    bias = 'neutral'
    if confidence < .5:
        bias = 'unknown'
    elif abs(ts-rs) >= 30:
        bias = 'conflict'
    elif score >= 60 and price > sma and m1 > 0:
        bias = 'bullish'
    elif score <= 40 and price < sma and m1 < 0:
        bias = 'bearish'
    return {'score':round(score,1), 'confidence':confidence, 'bias':bias,
            'reason': '技术/趋势均分按较低置信度收缩；价格与 SMA50、1月收益确认',
            'price_above_sma50':price > sma, 'return_1m':m1}


def _near(quotes, target, tolerance=.025):
    quotes = [q for q in quotes if q.get('standard_contract') is True]
    if not quotes:
        return None
    q = min(quotes, key=lambda x: abs(x['strike']-target))
    return q if abs(q['strike']/target-1) <= tolerance else None


def ticket(snapshot, strategy):
    """One regular 100-share contract per leg; natural bid/ask, not mid fills."""
    spot = snapshot['spot']
    calls, puts = snapshot.get('calls', []), snapshot.get('puts', [])
    move = snapshot['expected_move_pct']/100
    otm = min(.10, max(.03, move))
    long = short = None
    if strategy == 'bull_call_debit':
        long = _near(calls, spot, .015)
        short = _near([q for q in calls if long and q['strike'] > long['strike']], spot*(1+otm))
        kind, credit = 'call', False
    elif strategy == 'bear_put_debit':
        long = _near(puts, spot, .015)
        short = _near([q for q in puts if long and q['strike'] < long['strike']], spot*(1-otm))
        kind, credit = 'put', False
    elif strategy in ('bull_put_credit', 'cash_secured_put'):
        short = _near([q for q in puts if q['strike'] < spot], spot*(1-otm))
        kind, credit = 'put', True
        if strategy == 'bull_put_credit':
            long = _near([q for q in puts if short and q['strike'] < short['strike']],
                         short['strike']-spot*.05) if short else None
    else:
        short = _near([q for q in calls if q['strike'] > spot], spot*(1+otm))
        kind, credit = 'call', True
        if strategy == 'bear_call_credit':
            long = _near([q for q in calls if short and q['strike'] > short['strike']],
                         short['strike']+spot*.05) if short else None
    single = strategy in ('covered_call', 'cash_secured_put')
    if short is None or (not single and long is None):
        return None
    legs = ([{**long, 'side':'buy', 'type':kind}] if long else []) + [
        {**short, 'side':'sell', 'type':kind}]
    # Revalidate serialized quotes; callers may supply cached snapshots.
    for q in legs:
        bid, ask, oi = [number(q.get(k)) for k in ('bid','ask','openInterest')]
        if None in (bid, ask, oi) or not (0 < bid <= ask and oi >= 50):
            return None
        if (ask-bid)/((ask+bid)/2) > .2:
            return None
    premium = short['bid'] - (long['ask'] if long else 0) if credit else long['ask']-short['bid']
    if premium <= 0:
        return None
    if single:
        base = spot if strategy == 'covered_call' else short['strike']
        max_loss = (base-premium)*100
        max_gain = (short['strike']-spot+premium)*100 if strategy == 'covered_call' else premium*100
        breakeven = base-premium
        collateral = base*100
    else:
        width = abs(long['strike']-short['strike'])
        if premium >= width:
            return None
        max_loss = (width-premium)*100 if credit else premium*100
        max_gain = premium*100 if credit else (width-premium)*100
        breakeven = (short['strike'] + (premium if kind=='call' else -premium)) if credit else (
            long['strike'] + (premium if kind=='call' else -premium))
        collateral = max_loss
    if max_loss <= 0 or max_gain <= 0:
        return None
    friction = sum(q['ask']-q['bid'] for q in legs)*100
    return {'strategy':strategy, 'expiry':snapshot['expiry'], 'legs':legs,
            'premium':round(premium,4), 'premium_type':'credit' if credit else 'debit',
            'max_loss':round(max_loss,2), 'max_gain':round(max_gain,2),
            'breakeven':round(breakeven,4), 'capital_required':round(collateral,2),
            'reward_risk':max_gain/max_loss, 'spread_cost_fraction':friction/max_loss,
            'payoff_basis':'到期、每组100股标准合约；费用未计；covered call 从现价计算股票+期权损益'}


def evaluate(snapshot, row, card, event, tape=None):
    d = direction(row)
    out = {'version':VERSION, 'direction':d, 'status':'wait', 'action':'wait',
           'score':None, 'candidates':[], 'reasons':[], 'entry_only':True}
    reasons = out['reasons']
    if card.get('levered'):
        reasons.append('杠杆标的不进入期权候选')
    if snapshot.get('earnings_status') not in ('clear', 'not_applicable'):
        reasons.append('跨财报或财报日未确认')
    if event.get('status') in ('blackout', 'unknown') or event.get('status') is None:
        reasons.append('宏观事件冻结或日历覆盖未确认')
    if d['bias'] in ('unknown', 'conflict'):
        reasons.append('方向数据不足、置信度低于50%或技术/趋势严重冲突')
    ratio = number(snapshot.get('iv_rv_ratio'))
    rv63 = number(snapshot.get('rv63'))
    if ratio is None or ratio <= 0 or rv63 is None or rv63 <= 0:
        reasons.append('缺少有效 IV/RV21 或 RV63')
    if snapshot.get('price_age_days') is None or not 0 <= snapshot['price_age_days'] <= 4:
        reasons.append('标的行情时间未确认或过旧')
    if reasons:
        return out
    ratio63 = snapshot['atm_iv']/rv63
    # Both untrimmed horizons must agree. Gaps remain part of realized risk.
    cheap = max(ratio, ratio63) <= .9
    rich = min(ratio, ratio63) >= 1.2
    options = []
    rsi = number((row.get('indicators') or {}).get('rsi'))
    if rsi is None:
        reasons.append('缺少入场位置 RSI')
        return out
    if d['bias'] == 'bullish' and rsi < 70 and card.get('stock_action') not in ('no_chase','trim_extended'):
        if cheap:
            options += ['bull_call_debit']
        if rich and not card.get('held'):
            options += ['bull_put_credit', 'cash_secured_put']
    elif d['bias'] == 'bearish' and rsi > 30:
        if cheap:
            options += ['bear_put_debit']
        if rich:
            options += ['bear_call_credit']
    if rich and card.get('held') and d['bias'] == 'neutral':
        options += ['covered_call']
    if not options:
        reasons.append('方向、入场位置与两档波动定价没有同时满足；不因高评分或大跌单独开仓')
    for strategy in options:
        credit = strategy not in ('bull_call_debit', 'bear_put_debit')
        blocked = []
        if credit:
            from sell_put_advisor import expiry_hits_macro
            from datetime import date
            if expiry_hits_macro(date.fromisoformat(snapshot['expiry'])):
                blocked.append('卖方到期日落在宏观事件窗口')
        if credit and (tape or {}).get('options_action') == 'no_new_short':
            blocked.append('宏观禁止新卖方仓位')
        if credit and (snapshot.get('term_slope') is None or snapshot['term_slope'] < -.03):
            blocked.append('远月数据缺失或近月 IV 倒挂，先核查事件')
        if credit and (tape or {}).get('stock_action') == 'trim' and strategy in ('bull_put_credit','cash_secured_put'):
            blocked.append('宏观减仓时不新增接股风险')
        t = ticket(snapshot, strategy)
        if t is None:
            blocked.append('没有合格的标准合约组合或价格不合理')
        if blocked:
            reasons.extend(f"{LABELS[strategy]}：{r}" for r in blocked)
            continue
        alignment = d['score'] if strategy in ('bull_call_debit','bull_put_credit','cash_secured_put') else (
            100-d['score'] if strategy in ('bear_put_debit','bear_call_credit') else 100-2*abs(d['score']-50))
        valuation = min(100, max(0, (min(ratio,ratio63)-1)*200 if credit else (1-max(ratio,ratio63))*400))
        liquidity = max(0, 100*(1-t['spread_cost_fraction']/.2))
        # Return/risk is not expected return or probability; never lets a gate pass.
        payoff = min(100, t['reward_risk']*100) if not strategy in ('covered_call','cash_secured_put') else 50
        score = round(.4*alignment+.25*valuation+.2*liquidity+.15*payoff,1)
        t.update(score=score, components={'direction':round(alignment,1),'valuation':round(valuation,1),
                                        'liquidity':round(liquidity,1),'payoff':round(payoff,1)})
        if t['spread_cost_fraction'] > .2:
            reasons.append(f'{LABELS[strategy]}：逐腿价差成本过高')
            continue
        if strategy in ('bull_call_debit','bear_put_debit') and t['reward_risk'] < .5:
            reasons.append(f'{LABELS[strategy]}：到期最大收益/风险低于0.5')
            continue
        if score < 60:
            reasons.append(f'{LABELS[strategy]}：适配分 {score}，未达60')
            continue
        t['checks_required'] = ['实时报价并重新计算净价/损益，不能直接按本快照下单', '手续费', '账户可用资金及现有期权仓位', '除息及提前行权风险', '到期前管理两腿，提前指派可能临时增加资金需求']
        if strategy == 'covered_call':
            t['checks_required'] += ['每张至少100股可交割且未被占用', '接受按行权价卖股']
        elif strategy == 'cash_secured_put':
            t['checks_required'] += ['预留行权价×100现金', '接受接股']
        out['candidates'].append(t)
    out['candidates'].sort(key=lambda c: c['score'], reverse=True)
    if out['candidates']:
        best = out['candidates'][0]
        out.update(status='conditional', action=best['strategy'], score=best['score'])
    return out


def label(decision):
    if decision.get('status') == 'conditional':
        return f"候选：{LABELS[decision['action']]}（{decision['score']:.0f}/100）"
    return '等待：' + '；'.join(decision.get('reasons') or ['期权链未评估'])


def render_tickets(decisions, strategies=None):
    lines = []
    for symbol, d in decisions.items():
        for t in d.get('candidates', []):
            if strategies and t['strategy'] not in strategies:
                continue
            legs = '；'.join(f"{'买' if q['side']=='buy' else '卖'} {q['strike']:g} {q['type']}" for q in t['legs'])
            lines += [f"### {symbol} — {LABELS[t['strategy']]} | 适配分 {t['score']:.0f}/100",
                      f"到期 {t['expiry']}；{legs}。每腿 1 张。",
                      f"{'收入' if t['premium_type']=='credit' else '支出'}参考 ${t['premium']*100:.2f}/组（买按 ask、卖按 bid，非保证成交价）。",
                      f"最大收益 ${t['max_gain']:.2f} / 最大亏损 ${t['max_loss']:.2f} / 保本 ${t['breakeven']:.2f} / 资金参考 ${t['capital_required']:.2f}。",
                      '评分分项：' + ' / '.join(f'{k} {v:.0f}' for k,v in t['components'].items()) + '。',
                      '执行前待核验：'+'、'.join(t['checks_required'])+'。',
                      t['payoff_basis']+'。\n']
    return '\n\n'.join(lines)
