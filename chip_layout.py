"""
半导体加仓 — compute / WFE book plus the user's add-priority ranking.

Two fixed views (override with CHIP_LAYOUT / CHIP_PRIORITY, comma-separated):
the 8-name first-batch zone book, and the 12-name add-priority list. Live
price, 52-week drawdown, implied upside vs PT, zone status and the add
verdict update every report.
"""
import os

from ai_portfolio import REC_CN, _item, _ma_cell, _money, _pct, _table

try:
    from configs import UNIVERSE
except ImportError:
    UNIVERSE = {}

# ticker, module, forward PE, PT, snapshot upside, snapshot drawdown note, zone lo, zone hi
LAYOUT = (
    ("NVDA", "AI Compute", "~24.5x", 328, "~44%", "接近高位", 205, 215),
    ("AVGO", "ASIC + Networking", "~18.5x", 532, "~47%", "-27%", 330, 350),
    ("TSM", "Foundry", "~20.7x", 552, "~27%", "相对高位", 400, 420),
    ("ASML", "EUV", "~高 40s–50s", 2137, "~27%", "-16%", 1550, 1650),
    ("MU", "HBM / Memory", "~6–7x", 1515, "~46%", "高位附近", 900, 980),
    ("KLAC", "Inspection", "~31x", 234, "~27%", "-40%", 170, 180),
    ("LRCX", "Etch / Deposition", "~29–31x", 373, "~27%", "-33%", 270, 285),
    ("AMAT", "Broad WFE", "~23–25x", 641, "~39%", "明显低于前高", 420, 440),
)
THESIS = {
    ticker: {
        "module": module, "fwd_pe": pe, "pt": pt, "upside_note": upside,
        "drawdown_note": dd, "zone_lo": lo, "zone_hi": hi,
    }
    for ticker, module, pe, pt, upside, dd, lo, hi in LAYOUT
}
DEFAULT_LAYOUT = tuple(ticker for ticker, *_ in LAYOUT)

# rank, ticker, reason — add order, not a daily score
PRIORITY = (
    (1, "AVGO", "custom ASIC + networking"),
    (2, "TSM", "整个 AI silicon 收费站"),
    (3, "AMAT", "WFE supercycle"),
    (4, "SNPS", "EDA 收费站、与 SMH 重叠较少"),
    (5, "COHR", "optical bottleneck"),
    (6, "NVDA", "最强 AI compute，但间接 exposure 已经偏多"),
    (7, "LRCX", "memory + advanced-node capex"),
    (8, "ALAB", "connectivity 高增长"),
    (9, "MRVL", "custom silicon 高 beta"),
    (10, "CRDO", "connectivity 超高 growth"),
    (11, "AMD", "upside 大，但当前 price 已非常 aggressive"),
    (12, "MU", "HBM 非常强，但周期风险最大"),
)
PRIORITY_REASON = {ticker: reason for _, ticker, reason in PRIORITY}
DEFAULT_PRIORITY = tuple(ticker for _, ticker, _ in PRIORITY)


def layout_list():
    raw = os.environ.get("CHIP_LAYOUT", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_LAYOUT)
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def priority_ranks():
    raw = os.environ.get("CHIP_PRIORITY", "").strip()
    if raw:
        names = []
        for x in raw.split(","):
            n = x.strip().upper()
            if n and n not in names:
                names.append(n)
        return [(i, n, PRIORITY_REASON.get(n, "")) for i, n in enumerate(names, 1)]
    return [(rank, ticker, reason) for rank, ticker, reason in PRIORITY]


def thesis(ticker):
    item = THESIS.get((ticker or "").strip().upper(), {})
    return {
        "module": item.get("module") or "",
        "fwd_pe": item.get("fwd_pe") or "—",
        "pt": item.get("pt"),
        "upside_note": item.get("upside_note") or "",
        "drawdown_note": item.get("drawdown_note") or "",
        "zone_lo": item.get("zone_lo"),
        "zone_hi": item.get("zone_hi"),
    }


def zone_status(price, lo, hi):
    if price is None or lo is None or hi is None:
        return "—"
    if lo <= price <= hi:
        return "已进入首批区间"
    if price > hi:
        return f"高于区间 {(price / hi - 1) * 100:.0f}%"
    return f"低于区间 {(1 - price / lo) * 100:.0f}%"


def live_upside(price, pt, fallback=""):
    if price and pt:
        return _pct((pt / price - 1) * 100)
    return fallback or "—"


def _zone_cell(lo, hi):
    if lo is None or hi is None:
        return "—"
    return f"{_money(lo)}–{_money(hi)}"


def _fill(item, n, daily_watch):
    item["display"] = n
    if not item.get("name") or item["name"] == n:
        item["name"] = (UNIVERSE.get(n) or {}).get("name") or n
    item.update(thesis(n))
    if not item.get("drawdown_label"):
        item["drawdown_label"] = daily_watch.drawdown_label(item.get("from_high"))
    if not item.get("add_verdict"):
        item["add_verdict"] = "不在今日评分池" if not item["in_run"] else (item.get("buy_label") or "—")
    item["zone"] = _zone_cell(item.get("zone_lo"), item.get("zone_hi"))
    item["zone_status"] = zone_status(item.get("price"), item.get("zone_lo"), item.get("zone_hi"))
    item["upside"] = live_upside(item.get("price"), item.get("pt"), item.get("upside_note"))
    return item


def _summary(items):
    scored = [x for x in items if x["score"] is not None]
    d1 = [x["change_1d"] for x in items if x["change_1d"] is not None]
    return {
        "total": len(items),
        "in_run": sum(1 for x in items if x["in_run"]),
        "avg_score": sum(x["score"] for x in scored) / len(scored) if scored else None,
        "avg_1d": sum(d1) / len(d1) if d1 else None,
        "buy_now": [x["display"] for x in items if x["buy_action"] == "buy"],
        "scale_in": [x["display"] for x in items if x["buy_action"] in ("scale_in", "add_held")],
        "watch": [x["display"] for x in items if x["buy_action"] == "watch_buy"],
        "no_buy": [x["display"] for x in items if x["buy_action"] == "no_buy"],
        "in_zone": [x["display"] for x in items if x.get("zone_status") == "已进入首批区间"],
        "discounted": [x["display"] for x in items
                       if x["from_high"] is not None and x["from_high"] <= -8],
        "near_high": [x["display"] for x in items
                      if x["from_high"] is not None and x["from_high"] > -3],
    }


def build(ranked, macro_result=None, sell_put_plan=None, held=None, today=None):
    import daily_watch

    zone_names = layout_list()
    ranks = priority_ranks()
    watch = []
    for n in zone_names + [t for _, t, _ in ranks]:
        if n not in watch:
            watch.append(n)
    rows = {(r.get("ticker") or "").upper(): r for r in (ranked or [])}
    subset = [rows[n] for n in watch if n in rows]
    cards, event = daily_watch.annotate(
        subset, held=held, today=today, tape=(macro_result or {}).get("macro_tape"))
    card_map = {c["ticker"]: c for c in cards}
    plan_map = {p["ticker"]: p for p in ((sell_put_plan or {}).get("names") or [])}

    book = {}
    for n in watch:
        book[n] = _fill(_item(n, rows.get(n), card_map.get(n), plan_map.get(n)), n, daily_watch)

    names = [book[n] for n in zone_names]
    priority = []
    for rank, n, reason in ranks:
        item = dict(book[n])
        item["priority"] = rank
        item["reason"] = reason
        priority.append(item)

    return {
        "event": event,
        "names": names,
        "priority": priority,
        "summary": _summary(list(book.values())),
    }


def build_section(data):
    names = data.get("names") or []
    priority = data.get("priority") or []
    s = data.get("summary") or {}
    lines = [
        "## 半导体加仓 — Chip Portfolio\n",
        "两张固定表：加仓优先级（`CHIP_PRIORITY`）回答先加谁；"
        "估值与首批区间（`CHIP_LAYOUT`）回答这 8 只的参考 PE、华尔街目标价和第一笔价格带。"
        "优先级和区间不随当天行情改；现价、距高点回撤、隐含上涨、是否进入区间、加仓判断是当天数据。\n",
    ]
    if names or priority:
        avg_score = f"{s['avg_score']:.0f}" if s.get("avg_score") is not None else "—"
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**名单概况：** {s.get('in_run', 0)}/{s.get('total', 0)} 只在今日评分池，"
            f"平均评分 **{avg_score}**，平均 1 日 {_pct(s.get('avg_1d'))}；"
            f"已进首批区间：{lst('in_zone')}；距高点 ≤ −8%：{lst('discounted')}；"
            f"接近高位：{lst('near_high')}。\n"
        )
        lines.append(
            f"**今日档位：** 抄底 {lst('buy_now')} · 加仓/分批 {lst('scale_in')} · "
            f"候补 {lst('watch')} · 今日不买 {lst('no_buy')}。\n"
        )

    if priority:
        lines.append("### 加仓优先级\n")
        rows = []
        for x in priority:
            rows.append([
                str(x.get("priority") or "—"),
                f"**{x['display']}**",
                x.get("reason") or "—",
                _money(x["price"]),
                _pct(x["change_1d"]),
                _pct(x["from_high"]),
                x.get("drawdown_label") or "—",
                f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
                x.get("add_verdict") or "—",
            ])
        lines.append(_table(
            ["优先级", "股票", "原因", "现价", "1日", "距高点", "回撤", "RSI", "加仓判断"],
            rows))
        lines.append("")

    lines.append("### 估值、回撤与首批区间\n")
    rows = []
    for x in names:
        rows.append([
            f"**{x['display']}**",
            x.get("module") or "—",
            _money(x["price"]),
            x.get("fwd_pe") or "—",
            _money(x.get("pt")),
            x.get("upside") or "—",
            _pct(x["from_high"]),
            x.get("drawdown_label") or "—",
            x.get("zone") or "—",
            x.get("zone_status") or "—",
        ])
    lines.append(_table(
        ["股票", "模块", "现价", "Forward PE", "Wall St. PT", "隐含 Upside",
         "距高点", "回撤", "首批区间", "区间"], rows))

    lines.append("\n### 加仓判断\n")
    judge_rows = []
    seen = set()
    for x in list(priority) + names:
        if x["display"] in seen:
            continue
        seen.add(x["display"])
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        judge_rows.append([
            f"**{x['display']}**",
            f"{score}（{x['grade']}）", rec,
            f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
            _ma_cell(x["price"], x["sma_50"]),
            _ma_cell(x["price"], x["sma_200"]),
            _pct(x["from_high"]),
            x.get("drawdown_label") or "—",
            x.get("add_verdict") or "—",
            x.get("buy_note") or x.get("stock_note") or "—",
        ])
    lines.append(_table(
        ["股票", "评分", "评级", "RSI", "50日线", "200日线", "距高点", "回撤", "加仓判断", "说明"],
        judge_rows))

    pool = []
    seen = set()
    for x in list(priority) + names:
        if x["display"] in seen:
            continue
        seen.add(x["display"])
        pool.append(x)
    dips = [x for x in pool if x.get("buy_action") == "buy"]
    adds = [x for x in pool if x.get("buy_action") in ("scale_in", "add_held")]
    in_zone = [x for x in pool if x.get("zone_status") == "已进入首批区间"]
    if dips or adds or in_zone:
        lines.append("\n### 今日可动手\n")
        action_rows = []
        seen = set()
        for x in dips + adds + in_zone:
            if x["display"] in seen:
                continue
            seen.add(x["display"])
            if x.get("buy_action") == "buy":
                action = "抄底"
            elif x.get("buy_action") in ("scale_in", "add_held"):
                action = "加仓"
            else:
                action = "进入区间"
            action_rows.append([
                action,
                f"**{x['display']}**",
                x.get("reason") or x.get("module") or "—",
                _pct(x["from_high"]),
                x.get("drawdown_label") or "—",
                x.get("zone") or "—",
                x.get("zone_status") or "—",
                x.get("add_verdict") or "—",
            ])
        lines.append(_table(
            ["动作", "股票", "原因/模块", "距高点", "回撤", "首批区间", "区间", "加仓判断"],
            action_rows))
        lines.append("")

    for x in priority:
        head = f"- **{x['display']}**（优先级 {x.get('priority')}，{x.get('reason')}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池，只看固定优先级。")
            continue
        lines.append(
            head + f"距高点 {_pct(x['from_high'])}（{x.get('drawdown_label') or '—'}） "
            f"{x.get('add_verdict') or ''}"
        )
    for x in names:
        if any(p["display"] == x["display"] for p in priority):
            continue
        head = f"- **{x['display']}**（{x.get('module') or '—'}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池，只看固定估值和首批区间。")
            continue
        lines.append(
            head + f"距高点 {_pct(x['from_high'])}（{x.get('drawdown_label') or '—'}） "
            f"区间 {x.get('zone_status') or '—'} {x.get('add_verdict') or ''}"
        )
    lines.append("")
    lines.append(
        "优先级是加仓顺序，不是今天必须买。"
        "Forward PE 和目标价是参考锚，不是当天一致预期的实时抓取。"
        "隐含 Upside 用当天现价对华尔街目标价重算。"
        "首批区间到了只表示可以开始第一笔，不表示一次打满。"
        "价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== 半导体加仓 ==="]
    s = data.get("summary") or {}
    def lst(key):
        return "、".join(s.get(key) or []) or "无"
    lines.append(
        f"抄底 {lst('buy_now')} · 加仓 {lst('scale_in')} · "
        f"进区间 {lst('in_zone')} · 折扣 {lst('discounted')}"
    )
    if data.get("priority"):
        lines.append("优先级:")
        for x in data["priority"]:
            lines.append(
                f"  {x.get('priority') or '—':>2} {x['display']:<5} "
                f"{x.get('reason') or ''}  "
                f"{_money(x['price'])} 距高点 {_pct(x['from_high'])}"
                f"（{x.get('drawdown_label') or '—'}） | {x.get('add_verdict') or '—'}"
            )
    lines.append("首批区间:")
    for x in data.get("names") or []:
        lines.append(
            f"  {x['display']:<5} {_money(x['price']):>8} "
            f"{x.get('zone') or '—'} {x.get('zone_status') or '—'} "
            f"距高点 {_pct(x['from_high'])} | {x.get('add_verdict') or '—'}"
        )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
