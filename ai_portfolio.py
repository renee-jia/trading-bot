"""
AI Portfolio — a separately watched core list of AI names, answered every day.

Default list (override with the AI_PORTFOLIO env var, comma-separated):
NVDA, AVGO, GOOGL, META, TSM, MU, AMAT, ORCL, ANET, SNPS.

A second tier, the 观察名单 (AI_PORTFOLIO_WATCH), holds candidates that fill
the layers the core list is thin on: equipment, cloud, power/cooling, storage,
security/application software and optics. It renders as its own table in the
same section and is never mixed into the core summary.

The section is a roll-up, not a new data pull: it reads the day's scored rows,
the shared stock / buy / options labels from `daily_watch`, and the buy levels,
earnings date and put ticket already computed by `ai_sell_put_plan` (whose
default watch list covers every portfolio name). Names that fell out of the
scored run still get a row that says so instead of disappearing.
"""
import os

try:
    from configs import UNIVERSE
except ImportError:  # public harness without core/
    UNIVERSE = {}

DEFAULT_PORTFOLIO = ("NVDA", "AVGO", "GOOGL", "META", "TSM", "MU", "AMAT",
                     "ORCL", "ANET", "SNPS")
DEFAULT_WATCHLIST = ("ASML", "KLAC", "MSFT", "AMZN", "VRT", "STX", "SNDK",
                     "PLTR", "CRWD", "CRDO", "COHR")
LAYER = {
    "ASML": "设备", "KLAC": "设备", "AMAT": "设备", "LRCX": "设备",
    "MSFT": "云", "AMZN": "云", "GOOGL": "云", "ORCL": "云",
    "VRT": "电力散热", "ETN": "电力散热", "GEV": "电力散热",
    "STX": "存储", "SNDK": "存储", "WDC": "存储", "MU": "存储",
    "PLTR": "软件", "CRWD": "软件", "NOW": "软件", "DDOG": "软件", "SNPS": "EDA", "CDNS": "EDA",
    "CRDO": "光模块网络", "COHR": "光模块网络", "ANET": "光模块网络", "CIEN": "光模块网络",
    "NVDA": "算力", "AVGO": "算力", "AMD": "算力", "TSM": "制造", "META": "应用",
}

REC_CN = {"Strong Buy": "强烈买入", "Buy": "买入", "Hold": "持有",
          "Reduce": "减仓", "Avoid": "回避"}


def portfolio_list():
    raw = os.environ.get("AI_PORTFOLIO", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_PORTFOLIO)
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def watchlist_names(core=None):
    """Second-tier candidates, minus anything already in the core list."""
    raw = os.environ.get("AI_PORTFOLIO_WATCH", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_WATCHLIST)
    core = set(core if core is not None else portfolio_list())
    out = []
    for n in names:
        if n not in out and n not in core:
            out.append(n)
    return out


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _pct(v, digits=1):
    return "—" if v is None else f"{v:+.{digits}f}%"


def _money(v):
    return "—" if v is None else (f"${v:,.0f}" if v >= 100 else f"${v:,.2f}")


def _ma_cell(price, ma):
    """Level plus where price sits relative to it."""
    if ma is None:
        return "—"
    if price is None:
        return _money(ma)
    gap = (price / ma - 1) * 100
    return f"{_money(ma)} ({'↑' if gap >= 0 else '↓'}{abs(gap):.0f}%)"


def _item(n, row, card, plan):
    ind = (row or {}).get("indicators") or {}
    sr = (row or {}).get("score_result") or {}
    price = _num(ind.get("price")) if row else _num((plan or {}).get("spot"))
    levels = (plan or {}).get("levels") or {}
    pick = (plan or {}).get("pick")
    earnings = (plan or {}).get("earnings")
    if hasattr(earnings, "strftime"):
        earnings = earnings.strftime("%Y-%m-%d")
    return {
        "ticker": n,
        "layer": LAYER.get(n, ""),
        "name": (row or {}).get("name") or (plan or {}).get("name")
                or (UNIVERSE.get(n) or {}).get("name") or n,
        "in_run": row is not None,
        "price": price,
        "change_1d": _num(ind.get("change_1d")) if row else _num((plan or {}).get("change_1d")),
        "change_5d": _num(ind.get("change_5d")),
        "change_1m": _num(ind.get("change_1m")),
        "change_3m": _num(ind.get("change_3m")),
        "from_high": _num(ind.get("pct_from_52w_high")) if row else _num((plan or {}).get("from_high")),
        "rsi": _num(ind.get("rsi")) if row else _num((plan or {}).get("rsi")),
        "sma_50": _num(ind.get("sma_50")) or _num(levels.get("sma_50")),
        "sma_200": _num(ind.get("sma_200")) or _num(levels.get("sma_200")),
        "score": _num(sr.get("score")),
        "grade": sr.get("grade") or "—",
        "recommendation": sr.get("recommendation") or "",
        "regime": sr.get("regime") or "—",
        "held": bool((card or {}).get("held")) or bool((plan or {}).get("held")),
        "stock_label": (card or {}).get("stock_label") or "—",
        "stock_note": (card or {}).get("stock_note") or "",
        "buy_action": (card or {}).get("buy_action"),
        "buy_label": (card or {}).get("buy_label") or ("不在今日评分池" if row is None else "—"),
        "buy_note": (card or {}).get("buy_note") or "",
        "buy_size": (card or {}).get("buy_size") or "—",
        "options_label": (card or {}).get("options_label") or "—",
        "entry_label": (plan or {}).get("entry_label"),
        "entry_note": (plan or {}).get("entry_note"),
        "today_label": (plan or {}).get("today_label"),
        "trend_note": (plan or {}).get("trend_note"),
        "buy_levels": (plan or {}).get("buy_levels"),
        "expiry": (plan or {}).get("expiry"),
        "dte": (plan or {}).get("dte"),
        "pick": pick,
        "earnings": earnings,
        "reasons": list(sr.get("reasoning") or [])[:2],
    }


def build(ranked, macro_result=None, sell_put_plan=None, held=None, today=None):
    """Return {'names': [...core], 'watch': [...], 'summary': {...}, 'event': {...}}."""
    import daily_watch

    names = portfolio_list()
    watch = watchlist_names(names)
    rows = {(r.get("ticker") or "").upper(): r for r in (ranked or [])}
    subset = [rows[n] for n in names + watch if n in rows]
    cards, event = daily_watch.annotate(
        subset, held=held, today=today, tape=(macro_result or {}).get("macro_tape"))
    card_map = {c["ticker"]: c for c in cards}
    plan_map = {p["ticker"]: p for p in ((sell_put_plan or {}).get("names") or [])}

    out = [_item(n, rows.get(n), card_map.get(n), plan_map.get(n)) for n in names]
    watch_items = [_item(n, rows.get(n), card_map.get(n), plan_map.get(n)) for n in watch]

    scored = [x for x in out if x["score"] is not None]
    d1 = [x["change_1d"] for x in out if x["change_1d"] is not None]
    summary = {
        "total": len(out),
        "in_run": sum(1 for x in out if x["in_run"]),
        "avg_score": sum(x["score"] for x in scored) / len(scored) if scored else None,
        "avg_1d": sum(d1) / len(d1) if d1 else None,
        "buy_now": [x["ticker"] for x in out if x["buy_action"] == "buy"],
        "scale_in": [x["ticker"] for x in out if x["buy_action"] in ("scale_in", "add_held")],
        "watch": [x["ticker"] for x in out if x["buy_action"] == "watch_buy"],
        "no_buy": [x["ticker"] for x in out if x["buy_action"] == "no_buy"],
        "above_200": [x["ticker"] for x in out
                      if x["price"] is not None and x["sma_200"] is not None and x["price"] >= x["sma_200"]],
        "puts": [x["ticker"] for x in out if x["pick"] and x["expiry"]],
        "held": [x["ticker"] for x in out if x["held"]],
        "watch_buyable": [x["ticker"] for x in watch_items
                          if x["buy_action"] in ("buy", "scale_in", "add_held")],
    }
    return {"event": event, "names": out, "watch": watch_items, "summary": summary}


# ----------------------------------------------------------------- rendering

def _table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _put_cell(x):
    k = x.get("pick")
    if k and x.get("expiry"):
        return f"{x['expiry']} ${k['strike']:.0f}P @ ${k['mid']:.2f}（Δ{k['delta']:.2f}，年化 {k['ann_pct']:.0f}%）"
    if x.get("entry_label"):
        return x["entry_label"]
    return "—"


def _levels_cell(x):
    lv = x.get("buy_levels")
    if not lv:
        return "—"
    return f"{_money(lv['l1'])} / {_money(lv['l2'])} / {_money(lv['l3'])}"


def build_section(data):
    names = data.get("names") or []
    s = data.get("summary") or {}
    lines = [
        "## AI Portfolio — 核心 AI 名单\n",
        "固定名单（`AI_PORTFOLIO`）每天单独看：" + "、".join(x["ticker"] for x in names) + "。"
        "这一栏只汇总当天的评分、涨跌结构、买入档位、买点和 sell put 首选；"
        "买点与期权链细节在「AI 标的 Sell Put 方案」，逐股因素在 Detailed Analysis。\n",
    ]
    if names:
        avg_score = f"{s['avg_score']:.0f}" if s.get("avg_score") is not None else "—"
        avg_1d = _pct(s.get("avg_1d"))
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**组合概况：** {s.get('in_run', 0)}/{s.get('total', 0)} 只在今日评分池，"
            f"平均评分 **{avg_score}**，平均 1 日 {avg_1d}；"
            f"站上 200 日线：{lst('above_200')}。\n"
        )
        lines.append(
            f"**今日档位：** 建议买入 {lst('buy_now')} · 分批/小加 {lst('scale_in')} · "
            f"候补 {lst('watch')} · 今日不买 {lst('no_buy')}；"
            f"有可写 put 的：{lst('puts')}。\n"
        )

    lines.append("### 行情与评分\n")
    rows = []
    for x in names:
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        rows.append([
            f"**{x['ticker']}**", x["name"][:16], _money(x["price"]),
            _pct(x["change_1d"]), _pct(x["change_5d"]), _pct(x["change_1m"]), _pct(x["change_3m"]),
            _pct(x["from_high"]),
            f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
            _ma_cell(x["price"], x["sma_50"]), _ma_cell(x["price"], x["sma_200"]),
            f"{score}（{x['grade']}）", rec, x["regime"],
        ])
    lines.append(_table(
        ["代码", "名称", "现价", "1日", "5日", "1月", "3月", "距高点", "RSI",
         "50日线", "200日线", "评分", "评级", "Regime"], rows))

    lines.append("\n### 今日动作\n")
    rows = []
    for x in names:
        rows.append([
            f"**{x['ticker']}**",
            x["stock_label"], x["buy_label"], x["buy_size"],
            _levels_cell(x), _put_cell(x), x["earnings"] or "—",
        ])
    lines.append(_table(
        ["代码", "涨跌结构", "买入", "用多少钱", "买点(回踩/分批/接股)",
         "Sell Put 首选", "下次财报"], rows))
    lines.append("")

    for x in names:
        if not x["in_run"]:
            lines.append(f"- **{x['ticker']}**：今日不在评分池（数据缺失或被跳过），只看买点与期权链。")
            continue
        bits = [x["buy_note"]] if x["buy_note"] else []
        if x.get("trend_note"):
            bits.append(x["trend_note"])
        if x.get("entry_note"):
            bits.append(f"put：{x['entry_note']}")
        if x["reasons"]:
            bits.append("因素：" + "；".join(x["reasons"]))
        lines.append(f"- **{x['ticker']}**：" + " ".join(bits))
    lines.append("")

    watch = data.get("watch") or []
    if watch:
        lines.append("### 观察名单（候选加入核心）\n")
        lines.append(
            "固定名单（`AI_PORTFOLIO_WATCH`）补核心名单缺的层：设备（ASML/KLAC）、云（MSFT/AMZN）、"
            "电力散热（VRT）、存储（STX/SNDK）、软件（PLTR/CRWD）、光模块（CRDO/COHR）。"
            "只观察，不进组合概况；连续过线再考虑升入核心。\n")
        rows = []
        for x in watch:
            score = f"{x['score']:.1f}" if x["score"] is not None else "—"
            rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
            rows.append([
                f"**{x['ticker']}**", x["layer"] or "—", x["name"][:14], _money(x["price"]),
                _pct(x["change_1d"]), _pct(x["change_1m"]), _pct(x["change_3m"]), _pct(x["from_high"]),
                f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
                _ma_cell(x["price"], x["sma_200"]), f"{score}（{x['grade']}）", rec,
                x["buy_label"], _put_cell(x), x["earnings"] or "—",
            ])
        lines.append(_table(
            ["代码", "层", "名称", "现价", "1日", "1月", "3月", "距高点", "RSI", "200日线",
             "评分", "评级", "买入", "Sell Put 首选", "下次财报"], rows))
        buyable = s.get("watch_buyable") or []
        lines.append("")
        lines.append("观察名单今日过线（买入/分批）：" + ("、".join(buyable) if buyable else "无") + "。\n")

    lines.append(
        "评分是综合分（Technical/Trend/Alpha/Sentiment），评级只是分数区间；"
        "「买入」是当天加仓档位，「Sell Put 首选」按 0.25Δ 左右取整；股票与 put 二选一，不叠加。"
        "所有价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== AI PORTFOLIO ==="]
    for x in data.get("names") or []:
        score = f"评分 {x['score']:.0f}" if x["score"] is not None else "评分 —"
        rec = REC_CN.get(x["recommendation"], "")
        k = x.get("pick")
        put = (f"put {x['expiry']} ${k['strike']:.0f}P @{k['mid']:.2f}"
               if k and x.get("expiry") else (x.get("entry_label") or "put —"))
        lines.append(
            f"  {x['ticker']:<5} {_money(x['price']):>8} {_pct(x['change_1d']):>7}  "
            f"{score}{'(' + rec + ')' if rec else ''} | {x['buy_label']} | "
            f"买点 {_levels_cell(x)} | {put}"
        )
    watch = data.get("watch") or []
    if watch:
        lines.append("观察名单:")
        for x in watch:
            score = f"评分 {x['score']:.0f}" if x["score"] is not None else "评分 —"
            k = x.get("pick")
            put = (f"put {x['expiry']} ${k['strike']:.0f}P @{k['mid']:.2f}"
                   if k and x.get("expiry") else (x.get("entry_label") or "put —"))
            lines.append(
                f"  {x['ticker']:<5} {_money(x['price']):>8} {_pct(x['change_1d']):>7}  "
                f"{score} | {x['buy_label']} | {put}"
            )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
