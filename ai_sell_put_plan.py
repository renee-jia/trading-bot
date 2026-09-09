"""
AI 持有标的 Sell Put 方案 — daily cash-secured-put entry plan for a fixed
list of quality AI names.

Unlike the sell-put radar (which only wakes up on panic drops) this desk
answers the same four questions for every name every day:

1. 建仓   — is this a name worth building a position in *via* short puts?
2. 今日   — is today a day to add (stock or put), or wait for a pullback?
3. 买点   — three concrete buy levels (回踩 / 分批 / 接股).
4. 期权链 — the chosen expiry, three strikes (0.30 / 0.25 / 0.20 delta),
            mid limit price, bid/ask, yield and annualised yield.

The watch list is AI_PUT_WATCH (env, comma-separated) or the default below.
Everything here is a plan, not an order: quotes are Yahoo snapshots without a
timestamp, and every ticket must be repriced live before submitting.
"""
import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from covered_call_advisor import VRP_THIN_PT, _risk_free_rate
from sell_put_advisor import analyze_put_ladder, held_tickers

try:
    from configs import UNIVERSE
except ImportError:  # public harness without core/
    UNIVERSE = {}

# User-required names first, then the desk's own additions.
DEFAULT_WATCH = (
    "AVGO", "MSFT", "MU", "QCOM", "MRVL", "ASML", "LRCX", "CDNS",
    "VRT", "ANET", "PLTR",
    "NVDA", "TSM", "AMD", "AMAT", "KLAC", "ORCL", "SNPS",
    "GOOGL", "META",  # AI Portfolio names (ai_portfolio.py) that were missing
    "AMZN", "STX", "SNDK", "CRWD", "CRDO", "COHR",  # AI Portfolio 观察名单 additions
)

# Entry gates
ENTRY_SCORE = 60.0       # 评分 ≥ 60 → 适合用 put 建仓
SMALL_SCORE = 50.0       # 50–60 → 只小量、只卖最低档
TREND_BREAK = 0.97       # price < 97% of SMA200 = long-term trend broken
ADD_DIP_1D = -1.5        # today's pullback that makes "今日加仓" live
ADD_RSI = 60.0
HOT_RSI = 68.0
CHASE_1D = 3.0
EARLY_STRIKE_PCT = 0.97  # 第一批回踩位 fallback when SMA20 is above spot

ENTRY_LABELS = {
    "yes": "适合 sell put 建仓",
    "small": "可小量（评分一般）",
    "thin": "可建仓，但权利金薄",
    "held": "已持有：put 只作加仓",
    "wait_event": "事件窗口，先不开",
    "wait_earnings": "跨财报 / 无合适到期",
    "no": "不建议用 put 接",
    "no_data": "数据不足",
    "no_chain": "期权链不可用",
}

TODAY_LABELS = {
    "add_now": "今日可加（回撤中）",
    "add_scale": "今日可分批加",
    "wait_pullback": "今日不加，等回踩",
    "no_chase": "今日不追",
    "no_add": "今日不加",
    "no_data": "无评分，先不加",
}


def watch_list():
    raw = os.environ.get("AI_PUT_WATCH", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_WATCH)
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _money(v):
    return "—" if v is None else f"${v:,.0f}" if v >= 100 else f"${v:,.2f}"


def _pct(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}%"


def _levels(hist):
    """SMA20 / SMA50 / SMA200 / 21-day low / tape fallbacks from 1y history."""
    closes = hist["Close"].dropna()
    out = {"sma_20": None, "sma_50": None, "sma_200": None, "low_21d": None,
           "change_1d": None, "from_high": None, "rsi": None}
    if len(closes) >= 2:
        out["change_1d"] = float((closes.iloc[-1] / closes.iloc[-2] - 1) * 100)
        out["from_high"] = float((closes.iloc[-1] / closes.max() - 1) * 100)
    if len(closes) >= 15:
        diff = closes.diff().dropna().tail(14)
        gain, loss = diff.clip(lower=0).mean(), (-diff.clip(upper=0)).mean()
        out["rsi"] = 100.0 if loss == 0 else float(100 - 100 / (1 + gain / loss))
    if len(closes) >= 20:
        out["sma_20"] = float(closes.tail(20).mean())
    if len(closes) >= 50:
        out["sma_50"] = float(closes.tail(50).mean())
    if len(closes) >= 200:
        out["sma_200"] = float(closes.tail(200).mean())
    if len(closes) >= 5:
        out["low_21d"] = float(closes.tail(21).min())
    return out


def trend_label(spot, lv):
    s50, s200 = lv.get("sma_50"), lv.get("sma_200")
    if spot is None or s200 is None:
        return "unknown", "均线数据不足"
    if spot < s200 * TREND_BREAK:
        return "broken", f"跌破 200 日线（${s200:,.0f}）"
    if spot >= s200 and s50 is not None and s50 >= s200:
        return "up", "长期多头（价 > 50日 > 200日）"
    if spot >= s200:
        return "up_weak", "200 日线之上，中期走弱"
    return "test", "正在回测 200 日线"


def buy_levels(spot, lv, ladder=None):
    """Three descending buy levels: 回踩 / 分批 / 接股."""
    if spot is None:
        return None
    s20, s50, low = lv.get("sma_20"), lv.get("sma_50"), lv.get("low_21d")
    l1 = s20 if s20 is not None and s20 < spot else spot * EARLY_STRIKE_PCT
    l2 = s50 if s50 is not None and s50 < l1 else l1 * 0.96
    l3 = low if low is not None and low < l2 else l2 * 0.96
    put_strike = min((x["strike"] for x in (ladder or [])), default=None)
    if put_strike is not None and put_strike < l3:
        l3 = put_strike
    return {"l1": l1, "l2": l2, "l3": l3}


def decide_entry(card, r, event, trend, held, vrp):
    """建仓 stance: whether this name should be entered via short puts."""
    score = card.get("score")
    if r is None or r.get("spot") is None:
        return "no_data", "行情或期权链不可用"
    if r.get("status") == "no_chain":
        return "no_chain", "数据源没有返回任何到期日（不是跨财报），本期只用股票按买点分批"
    if card.get("levered"):
        return "no", "杠杆产品不卖 put"
    if trend == "broken" or (score is not None and score < SMALL_SCORE):
        why = "跌破 200 日线" if trend == "broken" else f"评分 {score:.0f} 偏弱"
        return "no", f"{why}，下跌可能是基本面在变，不用 put 去接"
    if held:
        return "held", "已有股票仓位，put 只作为加仓工具，张数按加仓预算控制"
    status = (event or {}).get("status")
    if status in ("blackout", "unknown"):
        return "wait_event", (event or {}).get("reason") or "事件窗口内不开新空头"
    if r.get("status") == "wait" or not r.get("ladder"):
        return "wait_earnings", "所有 25+ DTE 到期日跨财报或贴事件，等财报后 IV 落地再写"
    if vrp is not None and vrp < VRP_THIN_PT:
        return "thin", f"IV 低于真实波动率 {abs(vrp):.0f}pt，权利金不划算：只挂最低档或直接买股"
    where = {"up": "趋势完好", "up_weak": "200 日线之上但中期走弱", "test": "正在回测 200 日线",
             "unknown": "均线数据不足"}.get(trend, "")
    if score is None:
        return "yes", f"本次无评分，仅按价格：{where}，事件与财报让开，卖 0.25Δ 左右并控制张数"
    if score >= ENTRY_SCORE:
        return "yes", f"评分 {score:.0f} 过线、{where}、事件与财报都让开，按纪律卖 0.25Δ 左右"
    return "small", f"评分 {score:.0f} 在 50–60 之间，{where}，只卖最低档、只开 1 张"


def decide_today(card, entry, spot, lv):
    """今日 stance: is today an add day for this name?"""
    if card.get("score") is None:
        return "no_data", "本次没有评分，先不动"
    d1, rsi = card.get("change_1d"), card.get("rsi")
    from_high = card.get("from_high")
    buy = card.get("buy_action")
    if entry == "no":
        return "no_add", "趋势或评分不过关，今天不加"
    if card.get("stock_action") in ("no_chase", "trim_extended") or (d1 is not None and d1 >= CHASE_1D):
        return "no_chase", "今天涨得急，加仓是在买别人的获利盘"
    dipping = (d1 is not None and d1 <= ADD_DIP_1D) or (from_high is not None and from_high <= -8)
    calm = rsi is None or rsi < ADD_RSI
    s20 = lv.get("sma_20")
    near_support = spot is not None and s20 is not None and spot <= s20 * 1.01
    if dipping and calm and buy in ("buy", "scale_in", "add_held", "watch_buy"):
        return "add_now", "今天有回撤且 RSI 不高，可加第一批（股票或卖 put 二选一，不叠加）"
    if buy in ("buy", "scale_in", "add_held") and (rsi is None or rsi < HOT_RSI):
        return "add_scale", "位置不贵，可分批加，不要一次打满"
    if near_support and calm and buy != "no_buy":
        return "add_now", "回到 20 日线附近且不超买，可加第一批"
    if buy == "watch_buy" or (rsi is not None and rsi >= HOT_RSI):
        return "wait_pullback", "名字可以，今天偏贵，等回踩到买点再加"
    return "no_add", "今天没有足够的加仓理由，按买点挂单等"


def pick_strike(entry, ladder):
    """Which rung of the ladder to quote in the summary."""
    if not ladder or entry in ("no", "no_data"):
        return None
    rungs = sorted(ladder, key=lambda x: x["strike"], reverse=True)  # 0.30 → 0.20Δ
    if entry in ("yes", "held"):
        return rungs[min(1, len(rungs) - 1)]   # ~0.25Δ
    return rungs[-1]                            # lowest strike


def analyze_name(ticker, rate, today=None, ticker_factory=None):
    """One yfinance pass per name: history, levels, put ladder."""
    import option_data
    t = (ticker_factory or option_data.ticker)(ticker)
    hist = t.history(period="1y")
    if hist is None or hist.empty or "Close" not in hist:
        raise ValueError("无历史行情")
    r = analyze_put_ladder(ticker, rate, t=t, hist=hist, today=today)
    r["levels"] = _levels(hist)
    return r


def build_plan(ranked, macro_result=None, held=None, today=None,
               tickers=None, analyzer=None, rate=None):
    """Return {'event':..., 'names':[...]} for the watch list."""
    import daily_watch

    today = today or date.today()
    names = tickers or watch_list()
    held = {t.upper() for t in (held if held is not None else held_tickers())}
    rows = {(r.get("ticker") or "").upper(): r for r in (ranked or [])}
    subset = [rows[n] for n in names if n in rows]
    cards, event = daily_watch.annotate(
        subset, held=held, today=today, tape=(macro_result or {}).get("macro_tape"))
    card_map = {c["ticker"]: c for c in cards}
    rate = rate if rate is not None else _risk_free_rate()

    out = []
    for n in names:
        card = card_map.get(n) or {
            "ticker": n, "name": (UNIVERSE.get(n) or {}).get("name", n),
            "score": None, "rsi": None, "change_1d": None, "change_5d": None,
            "change_1m": None, "from_high": None, "held": n in held,
            "levered": False, "stock_action": "hold_watch", "buy_action": None,
            "options_decision": None,
        }
        try:
            r = (analyzer or analyze_name)(n, rate, today=today)
        except Exception as exc:
            r = None
            err = f"{type(exc).__name__}: {exc}"[:80]
        else:
            err = None
        spot = r.get("spot") if r else None
        lv = (r or {}).get("levels") or {}
        trend, trend_note = trend_label(spot, lv)
        vrp = ((r["atm_iv"] - r["rv_trimmed"]) * 100
               if r and r.get("atm_iv") and r.get("rv_trimmed") else None)
        # Names outside today's scored run still get tape columns from history.
        card = dict(card)
        for key in ("change_1d", "from_high", "rsi"):
            if card.get(key) is None and lv.get(key) is not None:
                card[key] = lv[key]
        entry, entry_note = decide_entry(card, r, event, trend, n in held, vrp)
        if (event or {}).get("status") == "caution" and entry in ("yes", "small", "thin", "held"):
            entry_note += "。目前是谨慎窗口：只开远月、少张数，或等数据后再下单"
        today_action, today_note = decide_today(card, entry, spot, lv)
        ladder = (r or {}).get("ladder") or []
        levels = buy_levels(spot, lv, ladder)
        decision = card.get("options_decision")
        unified = None
        if decision is not None:
            from options_decision import label
            unified = label(decision)
        out.append({
            "ticker": n,
            "name": card.get("name") or n,
            "held": n in held,
            "score": card.get("score"),
            "recommendation": card.get("recommendation") or "",
            "rsi": card.get("rsi"),
            "change_1d": card.get("change_1d"),
            "change_5d": card.get("change_5d"),
            "from_high": card.get("from_high"),
            "buy_action": card.get("buy_action"),
            "spot": spot,
            "levels": lv,
            "trend": trend,
            "trend_note": trend_note,
            "vrp": vrp,
            "atm_iv": (r or {}).get("atm_iv"),
            "rv_trimmed": (r or {}).get("rv_trimmed"),
            "earnings": (r or {}).get("earnings"),
            "expiry": (r or {}).get("expiry"),
            "dte": (r or {}).get("dte"),
            "chain_status": (r or {}).get("status") if r else "error",
            "quote_note": (r or {}).get("quote_note"),
            "exp_move": (r or {}).get("exp_move"),
            "low_21d": lv.get("low_21d"),
            "ladder": ladder,
            "pick": pick_strike(entry, ladder),
            "entry": entry,
            "entry_label": ENTRY_LABELS[entry],
            "entry_note": entry_note,
            "today": today_action,
            "today_label": TODAY_LABELS[today_action],
            "today_note": today_note,
            "buy_levels": levels,
            "unified": unified,
            "error": err,
        })
    return {"date": today.isoformat(), "event": event, "names": out}


# ----------------------------------------------------------------- rendering

def _levels_txt(p):
    lv = p.get("buy_levels")
    if not lv:
        return "—"
    return f"{_money(lv['l1'])} / {_money(lv['l2'])} / {_money(lv['l3'])}"


def _pick_txt(p):
    k = p.get("pick")
    if p.get("entry") == "no":
        return "— 不卖"
    if p.get("entry") == "no_chain":
        return "— 链不可用"
    if not k or not p.get("expiry"):
        return "—"
    prefix = "事件后再开：" if p.get("entry") == "wait_event" else ""
    return (f"{prefix}{p['expiry']}（{p['dte']}d）卖 ${k['strike']:.0f}P @ **${k['mid']:.2f}** "
            f"({k['bid']:.2f}/{k['ask']:.2f}) Δ{k['delta']:.2f} 年化 {k['ann_pct']:.0f}%")


def _ladder_table(p):
    lines = ["| 行权价 | OTM | Delta | Bid/Ask | **挂单价(mid)** | 权利金/张 | 占用现金 | 保本价 | 保本折价 | 收益率 | 年化 | OI |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for l in sorted(p["ladder"], key=lambda x: x["strike"], reverse=True):
        strike_txt = f"${l['strike']:.0f} ✓" if l.get("below_support") else f"${l['strike']:.0f}"
        lines.append(
            f"| {strike_txt} | -{l['otm_pct']:.1f}% | {l['delta']:.2f} "
            f"| {l['bid']:.2f}/{l['ask']:.2f} | **${l['mid']:.2f}** | ${l['mid']*100:,.0f} "
            f"| ${l['cash']:,.0f} | ${l['breakeven']:.2f} | -{l['be_discount']:.1f}% "
            f"| {l['yield_pct']:.2f}% | {l['ann_pct']:.1f}% | {l['oi']:,} |")
    return "\n".join(lines) + "\n"


def _detail(p):
    lines = [f"### {p['ticker']}（{p['name']}）— {p['entry_label']} · {p['today_label']}\n"]
    if p.get("error") and p.get("spot") is None:
        lines.append(f"> 数据获取失败：{p['error']}\n")
        return "\n".join(lines)
    earn = p.get("earnings") or "未知"
    if hasattr(earn, "strftime"):
        earn = earn.strftime("%Y-%m-%d")
    iv = f"{p['atm_iv']*100:.0f}%" if p.get("atm_iv") else "—"
    rv = f"{p['rv_trimmed']*100:.0f}%" if p.get("rv_trimmed") else "—"
    score = f"{p['score']:.0f}（{p['recommendation'] or '—'}）" if p.get("score") is not None else "—"
    lv = p.get("levels") or {}
    rsi = f"{p['rsi']:.0f}" if p.get("rsi") is not None else "—"
    lines.append(
        f"现价 **{_money(p['spot'])}**（1日 {_pct(p['change_1d'])}，距高点 {_pct(p['from_high'])}，"
        f"RSI {rsi}） | 评分 {score} | "
        f"{p['trend_note']}：SMA20 {_money(lv.get('sma_20'))} · SMA50 {_money(lv.get('sma_50'))} · "
        f"SMA200 {_money(lv.get('sma_200'))} · 21日低 {_money(lv.get('low_21d'))} | "
        f"ATM Put IV {iv} / 真实波动 {rv}"
        + (f"（溢价 {p['vrp']:+.0f}pt）" if p.get("vrp") is not None else "")
        + f" | 下次财报 **{earn}**\n"
    )
    lines.append(f"- **建仓：** {p['entry_note']}")
    lines.append(f"- **今日：** {p['today_note']}")
    bl = p.get("buy_levels")
    if bl:
        lines.append(
            f"- **买入点位：** 第一批回踩 {_money(bl['l1'])}（20日线/−3%）· "
            f"第二批 {_money(bl['l2'])}（50日线）· 第三批/接股 {_money(bl['l3'])}（21日低点或最低行权价）"
        )
    if p.get("unified"):
        lines.append(f"- **统一期权评分：** {p['unified']}")
    lines.append("")
    if p.get("ladder") and p.get("expiry"):
        em = ""
        if p.get("exp_move") and p.get("spot"):
            em = f" | 到期 ±1σ **${p['spot']-p['exp_move']:.0f} – ${p['spot']+p['exp_move']:.0f}**"
        short = "" if p.get("chain_status") == "ok" else "（财报临近，缩短周期）"
        lines.append(f"**Sell Put 期权链：到期 {p['expiry']}（{p['dte']} DTE）**{short}{em}\n")
        lines.append(_ladder_table(p))
        k = p.get("pick")
        if k:
            lines.append(f"首选：**${k['strike']:.0f} put，挂 ${k['mid']:.2f} 限价**（每张收 ${k['mid']*100:,.0f}，"
                         f"占用 ${k['cash']:,.0f}，被行权成本 ${k['breakeven']:.2f}）。✓ = 行权价在 21 日低点之下。\n")
        else:
            lines.append("上表仅供参考：本栏判定不卖，等趋势或评分修复后再看。✓ = 行权价在 21 日低点之下。\n")
        if p.get("dte") is not None and p["dte"] <= 24:
            lines.append("> 管理：周期短——成交后挂 GTC 买回单（成交价 × 50%），达标即离场，不设滚动点；"
                         "跌穿行权价且仍想要这只股就等行权，不想要就向下+向外滚。\n")
        else:
            exp_date = datetime.strptime(p["expiry"], "%Y-%m-%d").date()
            roll = exp_date - timedelta(days=21)
            lines.append(f"> 管理：成交后挂 GTC 买回单（成交价 × 50%）；未触发则 **{roll}** 平仓滚动；"
                         f"跌穿行权价且仍想要这只股就等行权，不想要就向下+向外滚。\n")
    elif p.get("chain_status") == "no_chain":
        lines.append("> 期权链不可用：数据源没有返回任何到期日（不是跨财报）。只用股票按买点分批，下次运行再看。\n")
    elif p.get("spot") is not None:
        lines.append("> 本期没有可写的到期日（跨财报 / 贴事件），只用股票按买点分批。\n")
    if p.get("quote_note") and p.get("ladder"):
        lines.append(f"*{p['quote_note']}。*\n")
    return "\n".join(lines)


def build_section(plan):
    event = plan.get("event") or {}
    names = plan.get("names") or []
    nxt = ""
    if event.get("event"):
        ev = event["event"]
        ev = ev.isoformat() if hasattr(ev, "isoformat") else str(ev)
        nxt = f"下一事件 {ev} {event.get('event_label','')}（{event.get('days')} 天）"
    lines = [
        "## AI 持有标的 Sell Put 方案\n",
        "固定名单（`AI_PUT_WATCH`）每天更新：能不能用 sell put 建仓、今天要不要加、加在哪个价、"
        "以及具体到期日 / 行权价 / 挂单价。这一栏是名单式方案，和「Cash-secured Put — 统一评分候选」"
        "并列：那一栏只列通过统一评分的合约，这一栏对每只都给答案。\n",
        f"**权利金立场：{event.get('label', '—')}**" + (f"  |  {nxt}" if nxt else "") + "\n",
        "**规则：** 建仓 = 评分 ≥ 60 且站稳 200 日线且不跨财报 / 不贴 FOMC-CPI-非农；"
        "评分 50–60 只小量；跌破 200 日线或评分 < 50 不用 put 接。"
        "今日加仓看当天回撤 + RSI < 60；涨 ≥ 3% 不追。"
        "买入点位 = 20 日线（或 −3%）/ 50 日线 / 21 日低点。"
        "行权价按 0.30 / 0.25 / 0.20 Δ，挂单价 = mid；股票和 put 二选一，不叠加。\n",
    ]
    rows = []
    for p in names:
        rsi = f"{p['rsi']:.0f}" if p.get("rsi") is not None else "—"
        score = f"{p['score']:.0f}" if p.get("score") is not None else "—"
        held = "持有" if p.get("held") else ""
        rows.append([
            f"**{p['ticker']}**", _money(p.get("spot")), _pct(p.get("change_1d")),
            _pct(p.get("from_high")), rsi, score, held,
            p["entry_label"], p["today_label"], _levels_txt(p), _pick_txt(p),
        ])
    lines.append(_table(
        ["代码", "现价", "1日", "距高点", "RSI", "评分", "持仓", "建仓", "今日加仓",
         "买点(回踩/分批/接股)", "Sell Put 首选"], rows))
    lines.append("")
    for p in names:
        lines.append(_detail(p))
    notes = {p.get("quote_note") for p in names if p.get("quote_note")}
    provenance = "；".join(sorted(notes)) if notes else "报价为 Yahoo 快照，无时间戳"
    lines.append(f"{provenance}；下单前重新报价。现金担保 put 需预留行权价×100，"
                 "并接受按行权价接股。本栏不含已有期权仓位，不给平仓指令。\n")
    lines.append("---\n")
    return "\n".join(lines)


def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def email_lines(plan):
    event = plan.get("event") or {}
    lines = ["=== AI SELL PUT 方案 ===", f"立场: {event.get('label', '—')}"]
    for p in plan.get("names") or []:
        k = p.get("pick")
        if k and p.get("expiry"):
            chain = f"{p['expiry']} ${k['strike']:.0f}P @{k['mid']:.2f} 年化{k['ann_pct']:.0f}%"
        elif p.get("entry") == "no":
            chain = "不卖"
        elif p.get("entry") == "no_chain":
            chain = "期权链不可用"
        else:
            chain = "无合适到期"
        bl = p.get("buy_levels")
        levels = (f"买点 {_money(bl['l1'])}/{_money(bl['l2'])}/{_money(bl['l3'])}" if bl else "买点 —")
        lines.append(
            f"  {p['ticker']:<5} {_money(p.get('spot')):>8} {_pct(p.get('change_1d')):>7}  "
            f"{p['entry_label']} | {p['today_label']} | {levels} | {chain}"
        )
    return lines


if __name__ == "__main__":
    plan = build_plan([], tickers=["AVGO", "MSFT"])
    print(build_section(plan))
