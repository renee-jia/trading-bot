"""
Daily watch + options desk for the report.

Three features, all mechanical (no extra option-chain fetches):

1. Options Desk — today's premium stance (open / caution / blackout)
   and a compact ticket list: write calls, cash-secured puts, or wait.
2. Config Watch — always-on CORE_WATCH names plus any UNIVERSE name
   that made a notable move. Tells you whether to add, hold, or fade.
3. Top Movers — biggest 1-day winners and losers, with an explicit
   跟风 / 不跟风 / 抄底 / 不接刀 label.

Covered-call ladders and sell-put chains stay in their own advisors;
this module decides *whether* those structures are allowed today.
"""
from datetime import date, datetime

from sell_put_advisor import (HIGH_IMPACT_MACRO, held_tickers,
                              MACRO_COVERAGE_START, MACRO_COVERAGE_END)

try:
    from configs import CORE_WATCH, UNIVERSE
except ImportError:
    CORE_WATCH = (
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "AMD", "AVGO",
        "TSM", "SMH",
    )
    UNIVERSE = {}

# 3x / inverse products: never chase, never sell puts against them.
LEVERED = {"SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO", "SPXU", "TECL", "TECS"}

CHASE_1D = 4.0
CHASE_5D = 8.0
CHASE_RSI = 70.0
DIP_1D = -4.0
DIP_5D = -7.0
EXTEND_1M = 30.0
EXTEND_RSI = 75.0
QUALITY_SCORE = 60.0
WEAK_SCORE = 50.0
BUY_SCORE = 65.0
SCALE_SCORE = 60.0
WATCH_SCORE = 58.0
NOTABLE_1D = 3.0
NOTABLE_5D = 6.0
TOP_N = 8
BUY_LIST_N = 8

STOCK_LABELS = {
    "trim_extended": "超买减仓，不跟风",
    "no_chase": "不跟风追涨",
    "quality_dip": "质量回撤，可分批",
    "watch_dip": "跌了但评分一般，先看",
    "avoid_knife": "不接刀",
    "hold_watch": "持有观望",
}

BUY_LABELS = {
    "buy": "建议买入",
    "scale_in": "建议分批买入",
    "add_held": "已持有，可小加",
    "watch_buy": "候补，等回踩",
    "no_buy": "今日不买",
}

OPTIONS_LABELS = {
    "sell_call": "covered call 形态候选，待期权链确认",
    "sell_put": "put 形态候选，待期权链确认",
    "no_long_call": "不买 call 追涨",
    "no_put": "已持有，不再叠 put",
    "wait_event": "事件窗口，不开新空头",
    "skip_levered": "杠杆ETF，不做期权",
    "none": "今日无高性价比结构",
}


def _num(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value, digits=1, suffix="%"):
    n = _num(value)
    if n is None:
        return "—"
    return f"{n:+.{digits}f}{suffix}"


def _short(text, limit):
    """Visible ellipsis instead of a silent mid-sentence cut."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def next_macro_event(today=None, events=None):
    """Return (event_date, label, days_ahead) or None."""
    today = today or date.today()
    events = HIGH_IMPACT_MACRO if events is None else events
    upcoming = []
    for item in events:
        ev, label = (item[0], item[1]) if isinstance(item, tuple) else (item, "EVENT")
        delta = (ev - today).days
        if delta >= 0:
            upcoming.append((ev, label, delta))
    if not upcoming:
        return None
    upcoming.sort(key=lambda x: x[2])
    return upcoming[0]


def options_event_status(today=None, events=None):
    """Classify whether new short-premium trades are allowed.

    blackout: print is today/tomorrow, or FOMC within 2 days
    caution:  any high-impact print within 5 days
    open:     otherwise
    """
    today = today or date.today()
    if events is None and not MACRO_COVERAGE_START <= today <= MACRO_COVERAGE_END:
        return {'status':'unknown', 'label':'日历覆盖未确认', 'event':None, 'days':None,
                'reason':'宏观日历超出已核验范围，暂停新期权候选，先更新日历。'}
    nxt = next_macro_event(today, events)
    if nxt is None:
        return {
            "status": "unknown",
            "label": "日历覆盖未确认",
            "event": None,
            "days": None,
            "reason": "没有未来事件条目，不能据此判断可以新开仓。",
        }
    ev, label, days = nxt
    if label == "FOMC" and days <= 2:
        status, slabel = "blackout", "事件冻结"
        reason = f"{ev.isoformat()} {label} 在 {days} 天内，新开空头权利仓会把事件波动吃进。等会议后再写。"
    elif days <= 1:
        status, slabel = "blackout", "事件冻结"
        reason = f"{ev.isoformat()} {label} 就在眼前，今天不开新的 call / put 空头。"
    elif days <= 5:
        status, slabel = "caution", "谨慎，少开"
        reason = f"距离 {ev.isoformat()} {label} 还有 {days} 天。可以管理已有仓位，新开仓只做远月、少张数。"
    else:
        status, slabel = "open", "可写权利金"
        reason = f"下一件大事是 {ev.isoformat()} {label}（还有 {days} 天）。按纪律写权利金可以。"
    return {
        "status": status,
        "label": slabel,
        "event": ev,
        "event_label": label,
        "days": days,
        "reason": reason,
    }


def classify_stock(row, held=False):
    """Return stock + options action dict for one scored name."""
    ticker = (row.get("ticker") or "").upper()
    name = row.get("name") or ticker
    meta = UNIVERSE.get(ticker) or {}
    sector = row.get("sector") or meta.get("sector") or ""
    ind = row.get("indicators") or {}
    sr = row.get("score_result") or {}
    d1 = _num(ind.get("change_1d"))
    d5 = _num(ind.get("change_5d"))
    d1m = _num(ind.get("change_1m"))
    rsi = _num(ind.get("rsi"))
    from_high = _num(ind.get("pct_from_52w_high", ind.get("from_high")))
    score = _num(sr.get("score"))
    rec = sr.get("recommendation") or ""
    levered = ticker in LEVERED

    ripped = (
        (d1 is not None and d1 >= CHASE_1D)
        or (d5 is not None and d5 >= CHASE_5D and rsi is not None and rsi >= CHASE_RSI)
    )
    extended = (
        ripped
        and d1m is not None and d1m >= EXTEND_1M
        and rsi is not None and rsi >= EXTEND_RSI
    )
    dumped = (
        (d1 is not None and d1 <= DIP_1D)
        or (d5 is not None and d5 <= DIP_5D)
    )

    if levered and ripped:
        stock = "no_chase"
        note = "杠杆ETF的跟风是在加波动，不是在买趋势。"
    elif extended:
        stock = "trim_extended"
        note = f"近一月 {d1m:+.0f}% 且 RSI {rsi:.0f}，这是减仓窗口，不是加仓窗口。"
    elif ripped:
        stock = "no_chase"
        note = "单日/数日涨太急，跟风买的是别人的获利盘。"
    elif dumped and levered:
        stock = "avoid_knife"
        note = "杠杆ETF的大跌会继续放大，不按普通股票去抄。"
    elif dumped and score is not None and score >= QUALITY_SCORE:
        stock = "quality_dip"
        note = f"评分 {score:.0f}（{rec or '—'}），只说明模型仍偏强，需核查下跌原因，不能据此认定错杀。"
    elif dumped and (score is None or score < WEAK_SCORE):
        stock = "avoid_knife"
        note = f"评分 {score:.0f}，下跌可能是基本面在变差，不接刀。" if score is not None else "评分缺失，按不接刀处理。"
    elif dumped:
        stock = "watch_dip"
        note = "跌了但评分不上不下，先看下一根日K和事件，不要当天市价扫货。"
    else:
        stock = "hold_watch"
        note = "没有极端涨跌，保持关注即可。"

    return {
        "ticker": ticker,
        "options_decision": row.get("options_decision"),
        "name": name,
        "sector": sector,
        "change_1d": d1,
        "change_5d": d5,
        "change_1m": d1m,
        "from_high": from_high,
        "rsi": rsi,
        "score": score,
        "recommendation": rec,
        "held": bool(held),
        "levered": levered,
        "stock_action": stock,
        "stock_label": STOCK_LABELS[stock],
        "stock_note": note,
        "notable": _is_notable(d1, d5, stock),
    }


def _is_notable(d1, d5, stock_action):
    if stock_action != "hold_watch":
        return True
    if d1 is not None and abs(d1) >= NOTABLE_1D:
        return True
    if d5 is not None and abs(d5) >= NOTABLE_5D:
        return True
    return False


def classify_options(card, event_status):
    """Attach options_action / options_label / options_note to a stock card."""
    status = (event_status or {}).get("status") or "open"
    stock = card["stock_action"]
    held = card["held"]
    levered = card["levered"]

    if levered:
        opt, note = "skip_levered", "杠杆产品不做短权利金。"
    elif status in ("blackout", "unknown"):
        opt, note = "wait_event", (event_status or {}).get("reason") or "事件窗口内不开新空头。"
    elif stock == "trim_extended" and held:
        opt, note = "sell_call", "股票形态提示评估 covered call；仍须期权链、仓位及卖股意愿确认。"
    elif stock in ("trim_extended", "no_chase") and not held:
        opt, note = "no_long_call", "不要买 call 去追已经拉过的日K。"
    elif stock == "no_chase" and held:
        opt, note = "sell_call", "已持有的强势股可以写虚值 call 收租，不要再加股票。"
    elif stock == "quality_dip" and held:
        opt, note = "no_put", "已经有底仓，再卖 put 是加倍，不是抄底。"
    elif stock == "quality_dip" and not held:
        opt, note = "sell_put", "愿意按行权价接股再卖 put；只想赌反弹就用股票分批，不要用短 put 赌。"
    elif stock == "avoid_knife":
        opt, note = "none", "弱评分或杠杆产品，不卖 put 去接。"
    elif stock == "watch_dip":
        opt, note = "none", "方向不清，权利和股票都先空手。"
    else:
        opt, note = "none", "没有必须做的期权结构。"

    if status == "caution" and opt in ("sell_call", "sell_put"):
        note = note + " 目前是谨慎窗口：只开远月、少张数，或等到数据后再下单。"

    card = dict(card)
    card["options_action"] = opt
    card["options_label"] = OPTIONS_LABELS[opt]
    card["options_note"] = note
    if card.get('options_decision') is not None:
        from options_decision import label
        decision = card['options_decision']
        card['options_action'] = decision.get('action', 'wait')
        card['options_label'] = label(decision)
        card['options_note'] = '；'.join(decision.get('reasons') or ['候选须核验实时报价、资金和现有仓位'])
    return card


def _size_for(tier, event_status, held):
    status = (event_status or {}).get("status") or "open"
    if held:
        return "已有仓：再用计划资金的约 10%"
    if tier == "buy":
        if status == "blackout":
            return "计划资金 10–15%，收盘附近或数据后再补，不要开盘扫货"
        if status == "caution":
            return "计划资金 15–20%"
        return "计划资金 20–30%，仍分两笔"
    if tier == "scale_in":
        if status in ("blackout", "caution"):
            return "计划资金 10–15%，事件日前不要一次买完"
        return "计划资金 15–25%，分两到三笔"
    return "先不加，记下回踩价"


def classify_buy(card, event_status=None, tape=None):
    """Independent buy ladder. Event freeze hits options, not this list.

    buy        评分够 + 已经有折扣 + 没追高
    scale_in   评分过线、位置不贵，日常分批
    watch_buy  名字好但今天偏贵，等回踩
    add_held   已持有且仍符合加仓条件，只小加
    no_buy     超买、弱评分、杠杆、接刀
    """
    card = dict(card)
    score = card.get("score")
    rsi = card.get("rsi")
    d1 = card.get("change_1d")
    d5 = card.get("change_5d")
    from_high = card.get("from_high")
    rec = card.get("recommendation") or ""
    structure = card.get("stock_action")
    held = card.get("held")
    levered = card.get("levered")
    tape_action = (tape or {}).get("stock_action")

    ripped = structure in ("trim_extended", "no_chase")
    knife = structure == "avoid_knife"
    discounted = (
        (from_high is not None and from_high <= -8)
        or (d5 is not None and d5 <= -2.5)
        or (d1 is not None and d1 <= -1.5 and (rsi is None or rsi <= 55))
    )
    mildly_cheap = (
        (from_high is not None and from_high <= -5)
        or (d5 is not None and d5 <= -1.0)
        or (d1 is not None and d1 <= -0.8)
        or (score is not None and score >= 70 and (rsi is None or rsi < 58))
    )
    hot_entry = (
        (rsi is not None and rsi >= 65)
        or (d1 is not None and d1 >= 1.5)
        or (from_high is not None and from_high > -3)
    )
    strong = (
        score is not None and score >= BUY_SCORE
        and rec in ("Buy", "Strong Buy", "")
    ) or (score is not None and score >= 68)
    decent = score is not None and score >= SCALE_SCORE
    watchable = score is not None and score >= WATCH_SCORE

    if levered or knife or ripped or (rsi is not None and rsi >= 75):
        tier, note = "no_buy", "超买、弱评分或杠杆产品，今天不买。"
    elif score is None or score < WATCH_SCORE:
        tier, note = "no_buy", "评分不够，先不列入买入名单。"
    elif strong and discounted and (rsi is None or rsi < 62) and (d1 is None or d1 < 2):
        tier, note = "buy", "评分够、已经有折扣、也没有在追高。仍分批，不要一次打满。"
    elif decent and mildly_cheap and (rsi is None or rsi < 68) and (d1 is None or d1 < 2.5):
        tier, note = "scale_in", "过线的质量股，位置不贵。用分批代替观望到永远。"
    elif watchable and not ripped:
        if hot_entry:
            tier, note = "watch_buy", "名字可以，今天偏贵。等 RSI 回到 60 下或再回踩 2–3%。"
        elif decent:
            tier, note = "scale_in", "评分过线且没有明显超买，可以开始第一批。"
        else:
            tier, note = "watch_buy", "刚过关注线，先放进候补，等更好的价。"
    else:
        tier, note = "no_buy", "今天没有足够的买入理由。"

    # Macro tape can haircut, not delete, a quality dip.
    if tape_action == "trim" and tier == "buy" and structure != "quality_dip":
        tier, note = "scale_in", note + " 宏观偏紧，从「买入」降到分批。"
    if tape_action == "trim" and tier == "scale_in" and structure != "quality_dip":
        note = note + " 宏观偏紧，第一批再小一档。"

    if held and tier in ("buy", "scale_in"):
        tier = "add_held"
        note = "已经有仓位。只小加，不按新开仓去打。"

    card["buy_action"] = tier
    card["buy_label"] = BUY_LABELS[tier]
    card["buy_note"] = note
    card["buy_size"] = _size_for(
        "buy" if tier == "buy" else "scale_in" if tier in ("scale_in", "add_held") else "watch",
        event_status,
        held and tier == "add_held",
    )
    return card


def annotate(ranked, held=None, today=None, events=None, tape=None):
    """Classify every scored name. Returns (cards, event_status)."""
    held = {t.upper() for t in (held if held is not None else held_tickers())}
    event_status = options_event_status(today=today, events=events)
    cards = []
    for row in ranked or []:
        card = classify_stock(row, held=row.get("ticker", "").upper() in held)
        card = classify_options(card, event_status)
        cards.append(classify_buy(card, event_status, tape))
    return cards, event_status


def core_tickers(extra=None):
    names = [t.upper() for t in CORE_WATCH]
    for t in extra or ():
        t = (t or "").upper()
        if t and t not in names:
            names.append(t)
    return names


def config_watch_cards(cards, extra_core=None):
    """Always-on core names, then other UNIVERSE names that actually moved."""
    core = set(core_tickers(extra_core))
    by_ticker = {c["ticker"]: c for c in cards}
    watched = []
    for t in core_tickers(extra_core):
        if t in by_ticker:
            watched.append(by_ticker[t])
    extras = [
        c for c in cards
        if c["ticker"] not in core and c.get("notable")
    ]
    extras.sort(key=lambda c: abs(c["change_1d"] or 0), reverse=True)
    return watched, extras[:12]


def top_movers(cards, n=TOP_N):
    scored = [c for c in cards if c["change_1d"] is not None]
    up = [c for c in sorted(scored, key=lambda c: c["change_1d"], reverse=True) if c["change_1d"] > 0][:n]
    down = [c for c in sorted(scored, key=lambda c: c["change_1d"]) if c["change_1d"] < 0][:n]
    return up, down


def buy_picks(cards, include_watch=False, n=BUY_LIST_N):
    """Ranked buy / scale-in / add-held names for the daily list."""
    rank = {"buy": 0, "scale_in": 1, "add_held": 2, "watch_buy": 3}
    picks = [c for c in cards if c.get("buy_action") in rank]
    if not include_watch:
        picks = [c for c in picks if c["buy_action"] != "watch_buy"]
    picks.sort(key=lambda c: (
        rank.get(c["buy_action"], 9),
        -(c["score"] or 0),
        c["from_high"] if c.get("from_high") is not None else 0,
    ))
    return picks[:n]


def action_tickets(cards):
    """Names the desk should actually mention today."""
    interesting = []
    for c in cards:
        if (
            c["stock_action"] == "hold_watch"
            and c["options_action"] == "none"
            and c.get("buy_action") in (None, "no_buy", "watch_buy")
        ):
            continue
        interesting.append(c)
    interesting.sort(key=lambda c: (
        0 if c.get("buy_action") == "buy" else
        1 if c.get("buy_action") in ("scale_in", "add_held") else
        2 if c["stock_action"] == "trim_extended" else
        3 if c["stock_action"] == "quality_dip" else
        4 if c["stock_action"] == "avoid_knife" else
        5 if c["stock_action"] == "no_chase" else 6,
        -(c["score"] or 0),
    ))
    return interesting


def _table(headers, rows):
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _row_for_watch(c):
    rsi = f"{c['rsi']:.0f}" if c["rsi"] is not None else "—"
    score = f"{c['score']:.1f}" if c["score"] is not None else "—"
    return [
        f"**{c['ticker']}**",
        c["name"][:16],
        _fmt(c["change_1d"]),
        _fmt(c["change_5d"]),
        rsi,
        score,
        c["stock_label"],
        c.get("buy_label") or "—",
        c["options_label"],
    ]


def _macro_level(value, digits=2, suffix=""):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def build_macro_desk_section(macro_result=None, today=None):
    """Markdown: rates / dollar / vol / risk tape + event calendar."""
    import macro_analyzer as ma

    market_data = (macro_result or {}).get("market_data") or {}
    tape = (macro_result or {}).get("macro_tape") or ma.classify_macro_tape(market_data)
    event = options_event_status(today=today)
    events = []
    try:
        events = ma._upcoming_macro_events(5, today=today or date.today())
    except Exception:
        events = []

    score = (macro_result or {}).get("score")
    rec = (macro_result or {}).get("recommendation") or ""
    score_txt = f"{float(score):.0f}/100" if isinstance(score, (int, float)) else "—"

    curve = tape.get("curve_10_3")
    curve_txt = "—"
    if curve is not None:
        state = {"inverted": "倒挂", "flat": "平坦", "normal": "正常"}.get(
            tape.get("curve_state"), tape.get("curve_state") or ""
        )
        curve_txt = f"{curve:+.2f}pt（10Y−3M，{state}）"

    lines = [
        "## Macro Desk — 今日宏观\n",
        f"**风险偏好：{tape.get('risk_label', '—')}**  |  "
        f"利率：{tape.get('rates_label', '—')}  |  "
        f"波动：{tape.get('vol_label', '—')}  |  "
        f"宏观分 {score_txt}"
        + (f"（{rec}）" if rec else "")
        + "\n",
        f"{tape.get('implication', '')}\n",
    ]

    rows = [
        [
            "利率",
            f"10Y {_macro_level(tape.get('tnx'))}% · 30Y {_macro_level(tape.get('tyx'))}% · "
            f"3M {_macro_level(tape.get('irx'))}%",
            f"10Y 1日 {_fmt(tape.get('tnx_1d'))} / 5日 {_fmt(tape.get('tnx_5d'))}。"
            f"{tape.get('rates_label')}。曲线 {curve_txt}",
        ],
        [
            "美元",
            f"UUP 1日 {_fmt(tape.get('uup_1d'))} / 5日 {_fmt(tape.get('uup_5d'))}",
            tape.get("dollar_label") or "—",
        ],
        [
            "波动率",
            f"VIX {_macro_level(tape.get('vix'), 1)}",
            tape.get("vol_label") or "—",
        ],
        [
            "风险内部",
            f"SPY 5日 {_fmt(tape.get('spy_5d'))} · IWM {_fmt(tape.get('iwm_5d'))} · "
            f"SMH {_fmt(tape.get('smh_5d'))}",
            "小盘或芯片明显落后时，指数稳不等于全面风险偏好打开。",
        ],
        [
            "商品 / 信用",
            f"金 5日 {_fmt(tape.get('gld_5d'))} · 油 {_fmt(tape.get('oil_5d'))} · "
            f"HYG {_fmt(tape.get('hyg_5d'))}",
            "油涨抬通胀预期，金涨更像避险或实际利率回落；HYG 跌是信用在收。",
        ],
    ]
    lines.append(_table(["维度", "读数", "怎么用"], rows))
    lines.append("")

    if event.get("event"):
        lines.append(
            f"**政策窗口：** {event['label']} — {event['reason']}\n"
        )
    if events:
        ev_rows = []
        today = today or date.today()
        for ev, label in events:
            ev_rows.append([ev.isoformat(), label, f"{(ev - today).days} 天"])
        lines.append("### 眼前的宏观事件\n")
        lines.append(_table(["日期", "事件", "还有"], ev_rows))
        lines.append("")

    stock = tape.get("stock_action")
    opt = tape.get("options_action")
    stock_map = {
        "scale_in": "股票：分批，不打满",
        "hold": "指数：持有不追。个股仍看「今日建议买入」",
        "trim": "股票：减高 beta，停新开仓",
    }
    opt_map = {
        "wait": "期权：先别开新空头，等波动率定价完",
        "caution": "期权：少张数、远月，或等到数据后再写",
        "no_new_short": "期权：今天不开新的短 put / 短 call",
        "sell_call_ok": "期权：已持有的强势股可以写 call，不买短 call",
        "selective": "期权：按统一方向分、波动估值、流动性及事件窗口筛选，具体结构见 Options Research",
    }
    lines.append(f"- {stock_map.get(stock, '股票：按趋势栏执行')}")
    lines.append(f"- {opt_map.get(opt, '期权：按 Options Desk 执行')}")
    lines.append("")
    lines.append("---\n")
    return "\n".join(lines)


def email_macro_lines(macro_result=None, today=None):
    """Short plaintext block for the daily email."""
    import macro_analyzer as ma

    tape = (macro_result or {}).get("macro_tape")
    if not tape:
        tape = ma.classify_macro_tape((macro_result or {}).get("market_data") or {})
    event = options_event_status(today=today)
    lines = [
        "=== MACRO DESK ===",
        f"风险偏好: {tape.get('risk_label', '—')} | 利率: {tape.get('rates_label', '—')} | "
        f"波动: {tape.get('vol_label', '—')}",
    ]
    if tape.get("implication"):
        lines.append(tape["implication"])
    if event.get("event"):
        lines.append(
            f"下一事件: {event['event'].isoformat()} {event.get('event_label', '')} "
            f"({event.get('days')} 天) · {event['label']}"
        )
    import cash_entry_plan
    plan = (macro_result or {}).get('cash_entry_plan') or cash_entry_plan.from_market_data(
        (macro_result or {}).get('market_data'),today=today)
    lines.extend(cash_entry_plan.email_lines(plan))
    return lines


def _load_cards(ranked, held=None, today=None, macro_result=None):
    return annotate(
        ranked,
        held=held,
        today=today,
        tape=(macro_result or {}).get("macro_tape"),
    )


def build_buy_section(ranked, macro_result=None, held=None, today=None):
    """Markdown: explicit 建议买入 / 分批 / 候补 list."""
    cards, event = _load_cards(ranked, held=held, today=today, macro_result=macro_result)
    active = buy_picks(cards, include_watch=False, n=BUY_LIST_N)
    waiting = [c for c in buy_picks(cards, include_watch=True, n=16)
               if c["buy_action"] == "watch_buy"][:5]

    lines = [
        "## 今日建议买入\n",
        "这一栏和「宏观持有 / 期权冻结」是分开的。指数可以继续拿着，"
        "个股仍按评分、距高点、RSI 给出买入档位。事件窗口只缩小仓位，不把名单清空。\n",
        "**规则：** 建议买入 = 评分 ≥ 65 且已有折扣（距高点 ≤ -8% 或 5 日 ≤ -2.5%）；"
        "建议分批 = 评分 ≥ 60 且位置不贵；候补 = 名字好但今天偏贵。"
        "超买、弱评分、杠杆 ETF 明确「今日不买」。\n",
        "**资金口径：** 下表百分比针对各股票预先分配的买入预算，不是每只占总资金的比例；"
        "若使用 General 的现金入场计划资金池，须先满足该计划的入场条件和本批总额上限。\n",
    ]
    if event.get("status") in ("caution", "blackout"):
        nxt = ""
        if event.get("event"):
            nxt = f"{event['event'].isoformat()} {event.get('event_label', '')}"
        lines.append(
            f"事件窗口缩小新开仓规模：临近 {nxt or '宏观数据'}。"
            "买入名单仍需分批，金额按下表事件档位参考，不要开盘扫货。期权动作以统一评分候选为准。\n"
        )

    if active:
        rows = []
        for c in active:
            rows.append([
                f"**{c['ticker']}**",
                c["name"][:16],
                f"{c['score']:.1f}" if c["score"] is not None else "—",
                _fmt(c["change_1d"]),
                _fmt(c.get("from_high")),
                f"{c['rsi']:.0f}" if c["rsi"] is not None else "—",
                c["buy_label"],
                c["buy_size"],
            ])
        lines.append(_table(
            ["代码", "名称", "评分", "1日", "距高点", "RSI", "建议", "用多少钱"],
            rows,
        ))
        for c in active[:6]:
            lines.append(f"- **{c['ticker']}**：{c['buy_note']}")
        lines.append("")
    else:
        lines.append("今天没有过线的买入候选。不是空仓信号，只是没有同时满足评分和价格折扣。\n")

    if waiting:
        lines.append("### 候补（等回踩再买）\n")
        wait_rows = []
        for c in waiting:
            wait_rows.append([
                f"**{c['ticker']}**",
                f"{c['score']:.1f}" if c["score"] is not None else "—",
                _fmt(c["change_1d"]),
                _fmt(c.get("from_high")),
                _short(c["buy_note"], 60),
            ])
        lines.append(_table(
            ["代码", "评分", "1日", "距高点", "等什么"],
            wait_rows,
        ))
    lines.append("---\n")
    return "\n".join(lines)


def build_options_desk_section(ranked, macro_result=None, held=None, today=None):
    """Markdown: today's options stance + compact tickets."""
    cards, event = _load_cards(ranked, held=held, today=today, macro_result=macro_result)
    vix = ((macro_result or {}).get("trend") or {}).get("vix")
    vix_s = f"{float(vix):.1f}" if isinstance(vix, (int, float)) else "—"
    nxt = ""
    if event.get("event"):
        nxt = f"{event['event'].isoformat()} {event.get('event_label', '')}（{event['days']} 天后）"

    tickets = action_tickets(cards)
    sell_calls = [c for c in tickets if c["options_action"] == "sell_call"]
    sell_puts = [c for c in tickets if c["options_action"] == "sell_put"]
    no_chase = [c for c in tickets if c["stock_action"] in ("no_chase", "trim_extended")]
    dips = [c for c in tickets if c["stock_action"] == "quality_dip"]

    lines = [
        "## Options Desk — 今日期权操作\n",
        f"**权利金立场：{event['label']}**  |  VIX {vix_s}"
        + (f"  |  下一事件：{nxt}" if nxt else "")
        + "\n",
        f"{event['reason']}\n",
        "今日规则：",
        "- **不买** 短期 call 去追已经大涨的名字。",
        "- **不卖** 短到期 put 去赌非农 / CPI / FOMC 当天的反弹。",
        "- 卖 call 限已有足额股票且愿意卖股；强趋势不自动覆盖，详见统一评分。",
        "- 卖 put 需要方向、两档 IV/RV 与合约筛选共同通过，大跌本身不是理由。",
        "- 3 倍 ETF（如 SOXL）：股票和期权都不跟。\n",
    ]

    buys = [c for c in tickets if c.get("buy_action") in ("buy", "scale_in", "add_held")]
    if tickets:
        rows = []
        for c in tickets[:12]:
            note = c["options_note"] or c["stock_note"]
            if note and note in c["options_label"]:
                note = c.get("buy_note") or c["stock_note"]  # label already says it
            rows.append([
                f"**{c['ticker']}**",
                _fmt(c["change_1d"]),
                c["stock_label"],
                c["options_label"],
                _short(note, 90),
            ])
        lines.append("### 今日票据\n")
        lines.append(_table(
            ["股票", "1日", "股票动作", "期权动作", "说明"],
            rows,
        ))
    else:
        lines.append("今日没有必须下单的期权结构。等下一档回撤或事件过完。\n")

    lines.append("---\n")
    return "\n".join(lines)


def build_config_watch_section(ranked, held=None, today=None, extra_core=None,
                               macro_result=None):
    """Markdown: CORE_WATCH always, plus notable rest-of-universe names."""
    cards, _event = _load_cards(ranked, held=held, today=today, macro_result=macro_result)
    extra_core = extra_core or held_tickers()
    core, extras = config_watch_cards(cards, extra_core=extra_core)

    lines = [
        "## Config 关注名单\n",
        "每天固定看 `CORE_WATCH`（config 里的核心池：Mag7、芯片龙头）。"
        "其余 universe 名字只在单日 |涨跌| ≥ 3% 或 5 日 |涨跌| ≥ 6% 时出现。"
        "「股票」看涨跌结构，「买入」是能不能加仓。不是自动下单。\n",
    ]
    headers = ["代码", "名称", "1日", "5日", "RSI", "评分", "涨跌", "买入", "期权"]
    if core:
        lines.append("### 核心池\n")
        lines.append(_table(headers, [_row_for_watch(c) for c in core]))
        notes = [
            c for c in core
            if c["stock_action"] != "hold_watch" or c.get("buy_action") not in (None, "no_buy")
        ]
        for c in notes[:6]:
            lines.append(f"- **{c['ticker']}**：{c.get('buy_note') or c['stock_note']}")
        if notes:
            lines.append("")
    if extras:
        lines.append("### 今日异动（核心池以外）\n")
        lines.append(_table(headers, [_row_for_watch(c) for c in extras]))
    elif not core:
        lines.append("今日没有可展示的 config 关注项。\n")
    lines.append("---\n")
    return "\n".join(lines)


def build_top_movers_section(ranked, held=None, today=None, macro_result=None):
    """Markdown: top winners / losers with chase-vs-dip labels."""
    cards, _event = _load_cards(ranked, held=held, today=today, macro_result=macro_result)
    up, down = top_movers(cards)

    def mover_rows(group):
        rows = []
        for c in group:
            rows.append([
                f"**{c['ticker']}**",
                c["name"][:16],
                _fmt(c["change_1d"]),
                _fmt(c["change_5d"]),
                f"{c['score']:.1f}" if c["score"] is not None else "—",
                c["stock_label"],
                c.get("buy_label") or "—",
                c["options_label"],
            ])
        return rows

    headers = ["代码", "名称", "1日", "5日", "评分", "跟风/抄底", "买入", "期权"]
    lines = [
        "## Top Movers — 跟风还是抄底\n",
        "按当日涨跌幅取涨幅最大和跌幅最大的各 8 只（来自当天分析过的 config 股票）。"
        "**默认不跟风。** 大涨且 RSI 高 = 不追；质量回撤可以进「建议买入」栏。"
        "跟风买 call 仍是最差的期权用法。\n",
    ]
    if up:
        lines.append("### 今日涨幅榜\n")
        lines.append(_table(headers, mover_rows(up)))
    if down:
        lines.append("### 今日跌幅榜\n")
        lines.append(_table(headers, mover_rows(down)))
    if not up and not down:
        lines.append("没有足够的当日涨跌数据。\n")
    lines.append("---\n")
    return "\n".join(lines)


def email_watch_lines(ranked, held=None, today=None, macro_result=None):
    """Short plaintext blocks for the daily email summary."""
    cards, event = _load_cards(ranked, held=held, today=today, macro_result=macro_result)
    lines = [
        "=== 建议买入 ===",
    ]
    active = buy_picks(cards, include_watch=False, n=6)
    if active:
        for c in active:
            lines.append(
                f"  {c['ticker']:<6} {c['buy_label']}  {_fmt(c['change_1d']):>7}  "
                f"评分 {c['score']:.1f}  {c['buy_size']}"
            )
    else:
        lines.append("  今日无过线买入候选")
    waiting = [c for c in buy_picks(cards, include_watch=True, n=12)
               if c["buy_action"] == "watch_buy"][:4]
    if waiting:
        lines.append("候补:")
        for c in waiting:
            lines.append(f"  {c['ticker']:<6} {c['buy_label']}  评分 {c['score']:.1f}")

    lines.extend([
        "",
        "=== OPTIONS DESK ===",
        f"立场: {event['label']}",
        event["reason"],
    ])
    tickets = action_tickets(cards)[:8]
    if tickets:
        lines.append("票据:")
        for c in tickets:
            lines.append(
                f"  {c['ticker']:<6} {_fmt(c['change_1d']):>7}  "
                f"{c['stock_label']} | {c['options_label']}"
            )
    else:
        lines.append("今日无必须下单的期权结构")

    extra_core = held if held is not None else held_tickers()
    core, extras = config_watch_cards(cards, extra_core=extra_core)
    highlights = [
        c for c in core + extras
        if c["stock_action"] != "hold_watch" or c.get("buy_action") not in (None, "no_buy")
    ][:8]
    lines.append("")
    lines.append("=== CONFIG 关注（需动作） ===")
    if highlights:
        for c in highlights:
            lines.append(
                f"  {c['ticker']:<6} {_fmt(c['change_1d']):>7}  "
                f"{c.get('buy_label') or c['stock_label']}"
            )
    else:
        lines.append("  核心池今日无需动作")

    up, down = top_movers(cards, n=5)
    lines.append("")
    lines.append("=== TOP MOVERS ===")
    for label, group in (("涨", up[:5]), ("跌", down[:5])):
        for c in group:
            lines.append(
                f"  [{label}] {c['ticker']:<6} {_fmt(c['change_1d']):>7}  {c['stock_label']}"
            )
    return lines


if __name__ == "__main__":
    today = datetime.now().date()
    print(options_event_status(today=today))
