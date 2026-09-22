"""
光学互联 — photonics / interconnect sleeve for dip / add decisions.

Default list (override with OPTICS_LAYOUT, comma-separated). Module and
「我怎么看」are the user's fixed view. Live price, 52-week drawdown and the
add verdict update every report.
"""
import os

from ai_portfolio import REC_CN, _item, _ma_cell, _money, _pct, _table

try:
    from configs import UNIVERSE
except ImportError:
    UNIVERSE = {}

# ticker, module, 我怎么看
LAYOUT = (
    ("COHR", "光器件 / 激光 / transceiver", "我最喜欢的 core optics 标的之一"),
    ("LITE", "光器件 / 激光 / transceiver", "更纯的 AI optics / laser exposure，成长很强"),
    ("AAOI", "光器件 / 激光 / transceiver", "小市值高 beta，800G/1.6T ramp，风险最高"),
    ("CRDO", "高速互连芯片", "AEC + optics + SerDes，高增长"),
    ("MRVL", "高速互连芯片", "optical DSP + custom silicon，更成熟"),
    ("GLW", "光纤 / 连接基础设施", "Corning，AI 数据中心 fiber density / CPO beneficiary"),
    ("LYTE", "ETF", "目前找到最「纯」的 photonics & optics ETF"),
)
THESIS = {ticker: {"module": module, "view": view} for ticker, module, view in LAYOUT}
DEFAULT_LAYOUT = tuple(ticker for ticker, *_ in LAYOUT)


def layout_list():
    raw = os.environ.get("OPTICS_LAYOUT", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_LAYOUT)
    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def thesis(ticker):
    item = THESIS.get((ticker or "").strip().upper(), {})
    return {
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
        item["display"] = n
        if not item.get("name") or item["name"] == n:
            item["name"] = (UNIVERSE.get(n) or {}).get("name") or n
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
        "discounted": [x["display"] for x in out
                       if x["from_high"] is not None and x["from_high"] <= -8],
        "near_high": [x["display"] for x in out
                      if x["from_high"] is not None and x["from_high"] > -3],
    }
    return {"event": event, "names": out, "summary": summary}


def build_section(data):
    names = data.get("names") or []
    s = data.get("summary") or {}
    lines = [
        "## 光学互联 — Optics Portfolio\n",
        "固定名单（`OPTICS_LAYOUT`）是光器件、高速互连、光纤和光子 ETF 的加仓本。"
        "模块和「我怎么看」不随行情改；现价、距高点回撤、均线和加仓判断是当天数据。"
        "COHR / CRDO / MRVL 也会出现在半导体加仓栏，这里按光学链条单独看。\n",
    ]
    if names:
        avg_score = f"{s['avg_score']:.0f}" if s.get("avg_score") is not None else "—"
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**名单概况：** {s.get('in_run', 0)}/{s.get('total', 0)} 只在今日评分池，"
            f"平均评分 **{avg_score}**，平均 1 日 {_pct(s.get('avg_1d'))}；"
            f"距高点 ≤ −8%：{lst('discounted')}；接近高位：{lst('near_high')}。\n"
        )
        lines.append(
            f"**今日档位：** 抄底 {lst('buy_now')} · 加仓/分批 {lst('scale_in')} · "
            f"候补 {lst('watch')} · 今日不买 {lst('no_buy')}。\n"
        )

    lines.append("### 模块与看法\n")
    rows = []
    for x in names:
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        rows.append([
            x.get("module") or "—",
            f"**{x['display']}**",
            x.get("view") or "—",
            _money(x["price"]), _pct(x["change_1d"]), _pct(x["change_5d"]),
            _pct(x["from_high"]), x.get("drawdown_label") or "—",
            _ma_cell(x["price"], x["sma_50"]), _ma_cell(x["price"], x["sma_200"]),
            f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
            f"{score}（{x['grade']}）", rec, x.get("add_verdict") or "—",
        ])
    lines.append(_table(
        ["模块", "股票", "我怎么看", "现价", "1日", "5日", "距高点", "回撤",
         "50日线", "200日线", "RSI", "评分", "评级", "加仓判断"], rows))

    dips = [x for x in names if x.get("buy_action") == "buy"]
    adds = [x for x in names if x.get("buy_action") in ("scale_in", "add_held")]
    if dips or adds:
        lines.append("\n### 今日可动手\n")
        action_rows = []
        for x in dips + adds:
            action_rows.append([
                "抄底" if x.get("buy_action") == "buy" else "加仓",
                f"**{x['display']}**",
                x.get("module") or "—",
                _pct(x["from_high"]), x.get("drawdown_label") or "—",
                x.get("add_verdict") or "—",
                x.get("buy_note") or x.get("stock_note") or "—",
            ])
        lines.append(_table(
            ["动作", "股票", "模块", "距高点", "回撤", "加仓判断", "说明"],
            action_rows))
        lines.append("")

    for x in names:
        head = f"- **{x['display']}**（{x.get('module')}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池，只看固定看法。")
            continue
        lines.append(
            head + f"距高点 {_pct(x['from_high'])}（{x.get('drawdown_label') or '—'}） "
            f"{x.get('add_verdict') or ''} "
            f"{x.get('buy_note') or ''}".rstrip()
        )
    lines.append("")
    lines.append(
        "看法是固定配置观点，买入档位用当天综合分和折扣。"
        "AAOI / LYTE 波动大，仓位按小市值或 ETF 处理，不要按 NVDA 的仓位去打。"
        "价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== 光学互联 ==="]
    s = data.get("summary") or {}
    def lst(key):
        return "、".join(s.get(key) or []) or "无"
    lines.append(f"抄底 {lst('buy_now')} · 加仓 {lst('scale_in')} · 折扣 {lst('discounted')}")
    for x in data.get("names") or []:
        score = f"评分 {x['score']:.0f}" if x["score"] is not None else "评分 —"
        lines.append(
            f"  {x['display']:<5} {x.get('module') or '':<22} {_money(x['price']):>8} "
            f"距高点 {_pct(x['from_high'])}（{x.get('drawdown_label') or '—'}）  "
            f"{score} | {x.get('add_verdict') or '—'}"
        )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
