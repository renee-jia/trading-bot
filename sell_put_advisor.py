"""
Sell-put opportunity scanner: watches the analyzed stock universe for sudden
large drawdowns and proposes cash-secured put ladders on the dip.

Companion to covered_call_advisor (same 45-DTE / pre-earnings / mid-price
rules): covered calls harvest rich premium on strength, short puts harvest
the IV spike that follows panic drops — entering quality names at a discount
if assigned.

Trigger (any one):
- 5-day return <= -7%
- 1-day return <= -5% (ignored when the 5-day return is still > +5% —
  that is a one-day fade after a melt-up, not a panic dip)
- 1-month return <= -12% with RSI < 35 (oversold grind-down)

The report carries no holdings information: `held_tickers()` is always empty,
so no name is ever marked, excluded or sized as "already held". Expiries that
land inside three calendar days after FOMC / CPI / NFP / PCE are skipped
so the short is not pinned into the event.
"""
import os
from datetime import date, datetime, timedelta
from math import sqrt

import numpy as np
import pandas as pd
import yfinance as yf

from covered_call_advisor import (
    VRP_THIN_PT,
    _bs_call_delta,
    _delta_or_bs,
    _next_earnings,
    _pick_expiry,
    _risk_free_rate,
)

DROP_5D = -7.0
DROP_1D = -5.0
DROP_1M = -12.0
RSI_OVERSOLD = 35.0
# A one-day crash after a strong 5-day rally is a fade, not a dip.
RALLY_5D = 5.0
TARGET_DELTAS = (0.30, 0.25, 0.20)  # absolute put deltas
MAX_CANDIDATES = 5  # bound option-chain fetches on broad-selloff days

# High-impact US prints. Update when the calendar rolls.
# FOMC uses a 3-day pin buffer (Wed decision → Friday monthly is the trap).
# CPI / NFP / PCE only block an expiry that lands on the print itself.
HIGH_IMPACT_MACRO = (
    (date(2026, 8, 26), "PCE"),
    (date(2026, 9, 4), "NFP"),
    (date(2026, 9, 11), "CPI"),
    (date(2026, 9, 16), "FOMC"),
    (date(2026, 9, 30), "PCE"),
    (date(2026, 10, 2), "NFP"),
    (date(2026, 10, 14), "CPI"),
    (date(2026, 10, 28), "FOMC"),
    (date(2026, 11, 6), "NFP"),
    (date(2026, 11, 10), "CPI"),
    (date(2026, 12, 4), "NFP"),
    (date(2026, 12, 10), "CPI"),
    (date(2026, 12, 9), "FOMC"),
)
# Fed/BLS/BEA checked 2026-09-08. PCE coverage verified only through September.
MACRO_COVERAGE_START = date(2026, 9, 1)
MACRO_COVERAGE_END = date(2026, 9, 30)
FOMC_BUFFER_DAYS = 3


def expiry_hits_macro(exp_date, events=None, buffer_days=None):
    """True if a high-impact print pins this expiry.

    `events` is a list of dates (tests) or (date, label) pairs. FOMC keeps a
    3-day buffer; other prints only block same-day expiry.
    """
    if events is None:
        events = HIGH_IMPACT_MACRO
    for item in events:
        if isinstance(item, tuple):
            ev, label = item[0], item[1]
        else:
            ev, label = item, "FOMC"
        buf = FOMC_BUFFER_DAYS if label == "FOMC" else 0
        if buffer_days is not None:
            buf = buffer_days
        delta = (exp_date - ev).days
        if 0 <= delta <= buf:
            return True
    return False


def filter_macro_safe_expiries(expirations, events=None, buffer_days=None):
    """Drop expiries that would pin into FOMC / CPI / NFP / PCE."""
    safe = []
    for exp in expirations:
        exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
        if not expiry_hits_macro(exp_date, events=events, buffer_days=buffer_days):
            safe.append(exp)
    return safe


def held_tickers():
    """Always empty by design.

    The report must not reveal or even hint at what the reader owns, so no
    module reads CC_POSITIONS or the broker book for "held" marks. Every name
    is treated as not held; the reader applies their own position filter.
    """
    return set()


def find_drop_candidates(ranked, exclude=None):
    """Filter ranked stocks down to genuine panic-dip candidates.

    Returns (candidates, skipped) where skipped is [{ticker, reason}, ...].
    """
    exclude = {t.upper() for t in (exclude or set())}
    candidates = []
    skipped = []
    for r in ranked:
        ticker = (r.get("ticker") or "").upper()
        if ticker in exclude:
            skipped.append({"ticker": ticker, "reason": "已持有，不再叠空头 put"})
            continue

        ind = r.get("indicators", {}) or {}
        d1 = ind.get("change_1d")
        d5 = ind.get("change_5d")
        d1m = ind.get("change_1m")
        rsi = ind.get("rsi")

        triggers = []
        if d5 is not None and d5 <= DROP_5D:
            triggers.append(f"5日 {d5:+.1f}%")
        one_day_fade = (
            d1 is not None and d1 <= DROP_1D
            and d5 is not None and d5 > RALLY_5D
        )
        if d1 is not None and d1 <= DROP_1D and not one_day_fade:
            triggers.append(f"单日 {d1:+.1f}%")
        if (d1m is not None and d1m <= DROP_1M
                and rsi is not None and rsi < RSI_OVERSOLD):
            triggers.append(f"1月 {d1m:+.1f}% 且 RSI {rsi:.0f}")
        if not triggers:
            if one_day_fade:
                skipped.append({
                    "ticker": ticker,
                    "reason": f"单日 {d1:+.1f}% 但 5 日 {d5:+.1f}%，视为回吐而非恐慌",
                })
            continue

        candidates.append({
            "ticker": ticker,
            "name": r.get("name", ticker),
            "score": r.get("score_result", {}).get("score"),
            "recommendation": r.get("score_result", {}).get("recommendation", ""),
            "rsi": rsi,
            "change_1d": d1,
            "change_5d": d5,
            "change_1m": d1m,
            "triggers": triggers,
        })

    # Worst 5-day performers first; cap fetch fan-out
    candidates.sort(key=lambda c: c["change_5d"] if c["change_5d"] is not None else 0)
    return candidates[:MAX_CANDIDATES], skipped


def analyze_put_ladder(ticker, rate, t=None, hist=None, today=None):
    """Cash-secured put recommendation for one ticker.

    `t` / `hist` let callers reuse an already-fetched Ticker and 1y history
    (ai_sell_put_plan does this so each name is fetched once).
    """
    import option_data
    t = t or option_data.ticker(ticker, today=today)
    hist = t.history(period="1y") if hist is None else hist
    closes = hist["Close"]
    spot = float(closes.iloc[-1])
    pct_from_high = (spot / float(closes.max()) - 1) * 100
    low_21d = float(closes.tail(21).min())

    # Trimmed realized vol (same as CC advisor): drop the 3 largest daily
    # moves so one panic gap doesn't dominate the estimate.
    rets = closes.pct_change().dropna()
    last21 = rets.tail(21)
    rv_trimmed = float(last21[last21.abs().rank(ascending=False) > 3].std() * np.sqrt(252))

    earnings = _next_earnings(t)
    expirations = list(t.options or ())
    safe_exps = filter_macro_safe_expiries(expirations)
    expiry, dte, status = _pick_expiry(safe_exps, earnings, today=today)
    if not expirations:
        status = "no_chain"  # data source returned nothing: not an earnings wait

    result = {
        "ticker": ticker,
        "spot": spot,
        "pct_from_high": pct_from_high,
        "low_21d": low_21d,
        "rv_trimmed": rv_trimmed,
        "earnings": earnings,
        "expiry": expiry,
        "dte": dte,
        "status": status,
        "ladder": [],
        "atm_iv": None,
        "exp_move": None,
        "quote_note": None,
    }
    if status in ("wait", "no_chain") or expiry is None:
        return result

    chain = t.option_chain(expiry)
    result["quote_note"] = option_data.quote_note(chain)
    puts = chain.puts
    puts = puts[
        (puts.bid > 0) & (puts.ask >= puts.bid) & (puts.strike < spot)
        & ((puts.openInterest.fillna(0) >= 10) | (puts.volume.fillna(0) > 0))
    ].copy()
    if puts.empty:
        result["status"] = "wait"
        return result

    result["atm_iv"] = option_data.atm_iv(chain.puts, spot, fallback=rv_trimmed)

    t_years = dte / 365
    result["exp_move"] = spot * result["atm_iv"] * sqrt(t_years)
    # Put delta = call delta - 1; ladder targets use absolute value. Quoted
    # greeks win when the source provides them.
    puts["abs_delta"] = _delta_or_bs(puts, spot, t_years, rate, result["atm_iv"], put=True)

    used = set()
    for target in TARGET_DELTAS:
        row = puts.iloc[(puts.abs_delta - target).abs().argsort()].iloc[0]
        if row.strike in used:
            continue
        used.add(row.strike)
        mid = round((row.bid + row.ask) / 2, 2) if row.ask > 0 else float(row.bid)
        # Cash-secured yield is on the collateral (strike), not the spot.
        yield_pct = mid / row.strike * 100
        result["ladder"].append({
            "strike": float(row.strike),
            "below_support": float(row.strike) < low_21d,
            "delta": float(row.abs_delta),
            "otm_pct": (1 - row.strike / spot) * 100,
            "bid": float(row.bid),
            "ask": float(row.ask),
            "mid": mid,
            "breakeven": float(row.strike) - mid,
            "be_discount": (1 - (row.strike - mid) / spot) * 100,
            "cash": float(row.strike) * 100,
            "yield_pct": yield_pct,
            "ann_pct": yield_pct * 365 / dte,
            "oi": int(row.openInterest) if pd.notna(row.openInterest) else 0,
        })
    result["ladder"].sort(key=lambda x: x["strike"], reverse=True)
    return result


def _format_candidate(c, r):
    lines = [f"### {c['ticker']}（{c['name']}）— 触发：{'；'.join(c['triggers'])}\n"]
    earn = r["earnings"].strftime("%Y-%m-%d") if r["earnings"] else "未知"
    iv_txt = f"{r['atm_iv']*100:.0f}%" if r["atm_iv"] else "-"
    vrp = (r["atm_iv"] - r["rv_trimmed"]) * 100 if r["atm_iv"] else None
    score_txt = f"{c['score']:.0f}（{c['recommendation']}）" if c["score"] is not None else "-"
    lines.append(
        f"现价 **${r['spot']:.2f}**（距52周高点 {r['pct_from_high']:+.1f}%） | "
        f"ATM Put IV {iv_txt} | 真实波动率(剔跳空) {r['rv_trimmed']*100:.0f}%"
        + (f" | 波动率溢价 {vrp:+.0f}pt" if vrp is not None else "")
        + f" | Bot评分 {score_txt} | 下次财报 **{earn}**\n"
    )
    if c["score"] is not None and c["score"] < 50:
        lines.append("> ⚠️ **接刀警告**：Bot评分偏弱，这次下跌可能是基本面恶化而非情绪错杀。"
                     "只在你愿意按行权价接股的前提下卖put，或直接跳过。\n")

    if r["status"] == "no_chain":
        lines.append("> 期权链不可用：数据源没有返回任何到期日，本期不给合约；只用股票按买点分批。\n")
        return "\n".join(lines)
    if r["status"] == "wait" or not r["ladder"]:
        lines.append("> 所有 25+ DTE 到期日均跨财报、贴着 FOMC/CPI/非农，或期权数据不可用——"
                     "大跌+事件风险是双重风险，**建议观望**。\n")
        return "\n".join(lines)

    if vrp is not None and vrp < VRP_THIN_PT:
        lines.append(f"> ⚠️ **权利金偏薄**：IV 低于真实波动率 {abs(vrp):.0f}pt——大跌但恐慌溢价没起来，"
                     "卖put性价比不高，建议跳过或只挂更低行权价。\n")

    dte_note = "" if r["status"] == "ok" else "（财报临近，被迫缩短周期）"
    exp_date = datetime.strptime(r["expiry"], "%Y-%m-%d").date()
    roll_date = exp_date - timedelta(days=21)
    em_note = ""
    if r.get("exp_move"):
        em = r["exp_move"]
        em_note = (f" | 到期日 ±1σ 区间 **${r['spot']-em:.0f} – ${r['spot']+em:.0f}** | "
                   f"近21日低点（支撑）**${r['low_21d']:.0f}**")
    lines.append(f"**推荐到期日：{r['expiry']}（{r['dte']} DTE）**{dte_note}{em_note}\n")
    lines.append("| 行权价 | OTM | Delta | Bid/Ask | **挂单价(mid)** | 权利金/张 | 占用现金 | 保本价 | 保本折价 | 收益率 | 年化 | OI |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for l in r["ladder"]:
        strike_txt = f"${l['strike']:.0f} ✓" if l.get("below_support") else f"${l['strike']:.0f}"
        lines.append(
            f"| {strike_txt} | -{l['otm_pct']:.1f}% | {l['delta']:.2f} "
            f"| {l['bid']:.2f}/{l['ask']:.2f} | **${l['mid']:.2f}** | ${l['mid']*100:,.0f} "
            f"| ${l['cash']:,.0f} | ${l['breakeven']:.2f} | -{l['be_discount']:.1f}% "
            f"| {l['yield_pct']:.2f}% | {l['ann_pct']:.1f}% | {l['oi']:,} |"
        )
    lines.append("")
    lines.append("*✓ = 行权价在近21日低点之下（跌破支撑才会被行权，更稳）*\n")
    if r["dte"] <= 24:
        lines.append("> 管理：本期周期短——成交后挂 GTC 买回单（限价 = 成交价 × 50%），达标即离场；"
                     "跌穿行权价时，愿意接股就等行权（成本=保本价），不愿意就向下+向外滚动。\n")
    else:
        lines.append(f"> 管理：成交后挂 GTC 买回单（限价 = 成交价 × 50%）；未触发则 **{roll_date}**（剩21天）平仓滚动；"
                     f"跌穿行权价时，愿意接股就等行权（成本=保本价），不愿意就向下+向外滚动。\n")
    if r.get("quote_note"):
        lines.append(f"*{r['quote_note']}；下单前重新报价。*\n")
    return "\n".join(lines)


def build_sell_put_radar(ranked, exclude=None, rate=None, analyzer=None, today=None):
    """Panic-drop scan of the analyzed universe with cash-secured put ladders."""
    if exclude is None:
        exclude = held_tickers()
    candidates, skipped = find_drop_candidates(ranked, exclude=exclude)

    header = [
        "## Sell Put 雷达（大跌收租机会）\n",
        f"*扫描规则：5日 ≤ {DROP_5D:.0f}% / 单日 ≤ {DROP_1D:.0f}%（5日仍 > +{RALLY_5D:.0f}% 则忽略）/ "
        f"1月 ≤ {DROP_1M:.0f}% 且 RSI < {RSI_OVERSOLD:.0f}。"
        "FOMC 后 3 个自然日内到期的合约跳过"
        "（CPI/非农/PCE 当天到期也跳过）。收租为主，被行权则以折价接股。*\n",
    ]
    if skipped:
        skip_txt = "；".join(f"{s['ticker']}（{s['reason']}）" for s in skipped[:8])
        header.append(f"*已排除：{skip_txt}*\n")
    if not candidates:
        header.append("今日 list 中无股票触发大跌条件，无 sell put 候选。\n")
        header.append("---\n")
        return "\n".join(header)

    rate = _risk_free_rate() if rate is None else rate
    analyzer = analyzer or analyze_put_ladder
    parts = header
    for c in candidates:
        try:
            parts.append(_format_candidate(c, analyzer(c["ticker"], rate, today=today)))
        except Exception as e:
            parts.append(f"### {c['ticker']}\n\n*期权数据获取失败：{e}*\n")
    parts.append("---\n")
    return "\n".join(parts)


def build_sell_put_section(ranked, exclude=None, decisions=None):
    """Unified-score cash-secured put candidates (no extra fetches)."""
    if decisions is not None:
        from options_decision import render_tickets
        text = render_tickets(decisions, {'cash_secured_put'})
        return "## Cash-secured Put — 统一评分候选\n\n" + (text or "没有通过统一评分的现金担保 put 候选。") + "\n\n---\n"
    return ("## Cash-secured Put — 等待完整评分\n\n"
            "大跌不足以证明值得卖 put；缺少统一评分，本栏不单独输出开仓或滚动指令。\n\n---\n")


if __name__ == "__main__":
    # Self-test with synthetic drop triggers on real tickers
    fake_ranked = [
        {"ticker": "INTC", "name": "Intel", "indicators":
            {"change_1d": -2.0, "change_5d": -8.5, "change_1m": -10.0, "rsi": 38},
         "score_result": {"score": 42, "recommendation": "Reduce"}},
        {"ticker": "NVDA", "name": "Nvidia", "indicators":
            {"change_1d": -5.5, "change_5d": -6.0, "change_1m": -4.0, "rsi": 45},
         "score_result": {"score": 71, "recommendation": "Buy"}},
        {"ticker": "AAPL", "name": "Apple", "indicators":
            {"change_1d": 0.5, "change_5d": 1.0, "change_1m": 3.0, "rsi": 60},
         "score_result": {"score": 65, "recommendation": "Buy"}},
    ]
    print(build_sell_put_section(fake_ranked))
