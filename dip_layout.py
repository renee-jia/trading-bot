"""
抄底布局 — a separately watched diversified book for dip / add decisions.

Default list (override with DIP_LAYOUT, comma-separated). Each name has a
fixed module + thesis. Daily numbers are a roll-up of the scored run and
`daily_watch` buy labels, not a new option-chain pull.
"""
import os

from ai_portfolio import REC_CN, _item, _ma_cell, _money, _pct, _table

try:
    from configs import UNIVERSE
except ImportError:
    UNIVERSE = {}

# ticker used in UNIVERSE / Yahoo (BRK-B), display, module, 定位
LAYOUT = (
    ("BRK-B", "BRK.B", "Diversified Core", "多元化核心，保险/铁路/能源/工业/现金流"),
    ("UNH", "UNH", "Healthcare – Managed Care", "医保龙头，和科技股相关性低"),
    ("ABBV", "ABBV", "Healthcare – Pharma", "高现金流、分红、药品管线"),
    ("ISRG", "ISRG", "Healthcare – MedTech", "手术机器人，长期成长"),
    ("JPM", "JPM", "Financials – Bank", "大型银行里最全面，IB/交易/存贷款/资管"),
    ("V", "V", "Financials – Payments", "高 ROIC 全球支付网络"),
    ("MA", "MA", "Financials – Payments", "和 V 类似，更偏全球支付增长"),
    ("ETN", "ETN", "Industrials – Electrification", "电气化、电网、数据中心供电"),
    ("PWR", "PWR", "Industrials – Grid Infrastructure", "电网、输电、基础设施建设"),
    ("CAT", "CAT", "Industrials – Machinery", "工程机械、资源周期、基建"),
    ("COST", "COST", "Consumer Defensive", "高质量消费 compounder"),
    ("WMT", "WMT", "Consumer Defensive", "稳定消费、零售、防御性"),
    ("BKNG", "BKNG", "Consumer Discretionary", "旅游平台，高 FCF"),
    ("FCX", "FCX", "Materials – Copper", "铜价、电气化、全球工业周期"),
    ("XOM", "XOM", "Energy", "能源、现金流、通胀对冲"),
    ("NEE", "NEE", "Utilities / Power", "电力、公用事业、可再生能源"),
    ("CEG", "CEG", "Nuclear / Power", "核电、电力需求、数据中心电力主题"),
    ("GE", "GE", "Defense / Aerospace", "航空发动机、长期 aftermarket"),
    ("RTX", "RTX", "Defense / Aerospace", "航空航天 + 国防"),
    ("CB", "CB", "Insurance", "高质量财险，和 tech 因子差异很大"),
)
THESIS = {ticker: {"display": display, "module": module, "view": view}
          for ticker, display, module, view in LAYOUT}
DEFAULT_LAYOUT = tuple(ticker for ticker, *_ in LAYOUT)


def _canonical(ticker):
    token = (ticker or "").strip().upper()
    if token in ("BRK.B", "BRKB"):
        return "BRK-B"
    return token.replace(".", "-")


def layout_list():
    raw = os.environ.get("DIP_LAYOUT", "").strip()
    names = [_canonical(x) for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_LAYOUT)
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def thesis(ticker):
    item = THESIS.get(_canonical(ticker), {})
    return {
        "display": item.get("display") or ticker,
        "module": item.get("module") or "",
        "view": item.get("view") or "",
    }


def build(ranked, macro_result=None, sell_put_plan=None, held=None, today=None):
    import daily_watch

    names = layout_list()
    rows = {(r.get("ticker") or "").upper(): r for r in (ranked or [])}
    subset = [rows[n] for n in names if n in rows]
    cards, event = daily_watch.annotate(
        subset, held=held, today=today, tape=(macro_result or {}).get("macro_tape"))
    card_map = {c["ticker"]: c for c in cards}
    plan_map = {p["ticker"]: p for p in ((sell_put_plan or {}).get("names") or [])}

    out = []
    for n in names:
        item = _item(n, rows.get(n), card_map.get(n), plan_map.get(n))
        item.update(thesis(n))
        if not item.get("name") or item["name"] == n:
            item["name"] = (UNIVERSE.get(n) or {}).get("name") or item["display"]
        if not item.get("drawdown_label"):
            item["drawdown_label"] = daily_watch.drawdown_label(item.get("from_high"))
        if not item.get("add_verdict"):
            item["add_verdict"] = "不在今日评分池" if not item["in_run"] else (item.get("buy_label") or "—")
        out.append(item)

    scored = [x for x in out if x["score"] is not None]
    d1 = [x["change_1d"] for x in out if x["change_1d"] is not None]
    summary = {
        "total": len(out),
        "in_run": sum(1 for x in out if x["in_run"]),
        "avg_score": sum(x["score"] for x in scored) / len(scored) if scored else None,
        "avg_1d": sum(d1) / len(d1) if d1 else None,
        "buy_now": [x["display"] for x in out if x["buy_action"] == "buy"],
        "scale_in": [x["display"] for x in out if x["buy_action"] in ("scale_in", "add_held")],
        "watch": [x["display"] for x in out if x["buy_action"] == "watch_buy"],
        "no_buy": [x["display"] for x in out if x["buy_action"] == "no_buy"],
        "above_200": [x["display"] for x in out
                      if x["price"] is not None and x["sma_200"] is not None and x["price"] >= x["sma_200"]],
        "discounted": [x["display"] for x in out
                       if x["from_high"] is not None and x["from_high"] <= -8],
    }
    return {"event": event, "names": out, "summary": summary}


def build_section(data):
    names = data.get("names") or []
    s = data.get("summary") or {}
    lines = [
        "## 抄底布局 — Diversified Portfolio\n",
        "固定名单（`DIP_LAYOUT`）是科技之外的抄底/加仓底仓：消费、医疗、金融、工业、能源、公用事业、国防。"
        "模块和定位不随行情改；旁边的折扣、均线、评分和买入档位是当天数据。"
        "这一栏不替代「今日建议买入」的全市场扫描，只盯这 20 只该不该动手。\n",
    ]
    if names:
        avg_score = f"{s['avg_score']:.0f}" if s.get("avg_score") is not None else "—"
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**名单概况：** {s.get('in_run', 0)}/{s.get('total', 0)} 只在今日评分池，"
            f"平均评分 **{avg_score}**，平均 1 日 {_pct(s.get('avg_1d'))}；"
            f"站上 200 日线：{lst('above_200')}；距高点 ≤ −8%：{lst('discounted')}。\n"
        )
        lines.append(
            f"**今日档位：** 抄底 {lst('buy_now')} · 加仓/分批 {lst('scale_in')} · "
            f"候补 {lst('watch')} · 今日不买 {lst('no_buy')}。\n"
        )

    lines.append("### 模块与定位\n")
    rows = []
    for x in names:
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        rows.append([
            x["module"] or "—",
            f"**{x['display']}**",
            x["view"] or "—",
            _money(x["price"]), _pct(x["change_1d"]), _pct(x["change_5d"]),
            _pct(x["from_high"]), x.get("drawdown_label") or "—",
            _ma_cell(x["price"], x["sma_50"]),
            _ma_cell(x["price"], x["sma_200"]),
            f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
            f"{score}（{x['grade']}）", rec, x.get("add_verdict") or x.get("buy_label") or "—",
        ])
    lines.append(_table(
        ["模块", "股票", "定位", "现价", "1日", "5日", "距高点", "回撤", "50日线", "200日线",
         "RSI", "评分", "评级", "加仓"], rows))

    dips = [x for x in names if x.get("buy_action") == "buy"]
    adds = [x for x in names if x.get("buy_action") in ("scale_in", "add_held")]
    if dips or adds:
        lines.append("\n### 今日可动手\n")
        action_rows = []
        for x in dips + adds:
            action_rows.append([
                "抄底" if x.get("buy_action") == "buy" else "加仓",
                f"**{x['display']}**",
                x["module"] or "—",
                _pct(x["from_high"]), x.get("drawdown_label") or "—",
                _pct(x.get("change_5d")),
                x.get("add_verdict") or x.get("buy_label") or "—",
                x.get("buy_size") or "—",
                x.get("buy_note") or x.get("stock_note") or "—",
            ])
        lines.append(_table(
            ["动作", "股票", "模块", "距高点", "回撤", "5日", "加仓判断", "仓位", "说明"],
            action_rows))
        lines.append("")

    for x in names:
        head = f"- **{x['display']}**（{x['module']}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池，只看固定定位。")
            continue
        bits = [x["buy_note"]] if x.get("buy_note") else []
        if x.get("stock_note"):
            bits.append(x["stock_note"])
        lines.append(head + " ".join(bits))
    lines.append("")
    lines.append(
        "定位是固定配置观点，买入档位用当天综合分和折扣。"
        "指数现金池仍看 General 的 VIX×SPX 阶梯；个股抄底不要求指数先到第一档。"
        "价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== 抄底布局 ==="]
    s = data.get("summary") or {}
    def lst(key):
        return "、".join(s.get(key) or []) or "无"
    lines.append(f"抄底 {lst('buy_now')} · 加仓 {lst('scale_in')} · 折扣 {lst('discounted')}")
    for x in data.get("names") or []:
        score = f"评分 {x['score']:.0f}" if x["score"] is not None else "评分 —"
        lines.append(
            f"  {x['display']:<6} {x['module']:<28} {_money(x['price']):>8} "
            f"{_pct(x['change_1d']):>7} 距高点 {_pct(x['from_high'])}"
            f"（{x.get('drawdown_label') or '—'}）  "
            f"{score} | {x.get('add_verdict') or x.get('buy_label') or '—'}"
        )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
