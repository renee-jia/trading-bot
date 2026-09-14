"""
SaaS Watch — a separately watched list of software / SaaS names.

Default list (override with the SAAS_WATCH env var, comma-separated), in the
user's ranking order:
CRM, NOW, ADBE, DDOG, SNOW, CRWD, NET, WDAY, INTU.

Each name carries a fixed thesis (star rating, positioning, main risk) that is
the user's own view and does not change day to day. The daily numbers next to
it are a roll-up, not a new data pull: the day's scored rows, the shared stock
/ buy / options labels from `daily_watch`, and the buy levels, earnings date
and put ticket from `ai_sell_put_plan` for the names on that desk's watch list
(CRM / NOW / ADBE by default). Names that fell out of the scored run still get
a row that says so instead of disappearing.
"""
import os

from ai_portfolio import (REC_CN, _item, _levels_cell, _ma_cell, _money, _pct,
                          _put_cell, _table)

DEFAULT_SAAS = ("CRM", "NOW", "ADBE", "DDOG", "SNOW", "CRWD", "NET", "WDAY", "INTU")

# ticker -> (stars, 定位, 主要风险)
THESIS = {
    "CRM":  ("⭐⭐⭐⭐⭐", "首选 value SaaS", "增长不够快"),
    "NOW":  ("⭐⭐⭐⭐⭐", "首选 quality SaaS", "已经反弹不少"),
    "ADBE": ("⭐⭐⭐⭐½", "最深 value / contrarian", "AI disruption 最大"),
    "DDOG": ("⭐⭐⭐⭐", "AI/cloud observability winner", "估值仍贵"),
    "SNOW": ("⭐⭐⭐⭐", "data + AI infrastructure", "valuation 高"),
    "CRWD": ("⭐⭐⭐⭐", "security 最不容易被 AI 替代", "很贵"),
    "NET":  ("⭐⭐⭐½", "长期 AI/network optionality", "太贵"),
    "WDAY": ("⭐⭐⭐", "便宜但 catalyst 较弱", "增长/AI monetization"),
    "INTU": ("⭐⭐⭐", "moat 强", "增长/AI monetization"),
}


def saas_list():
    raw = os.environ.get("SAAS_WATCH", "").strip()
    names = [x.strip().upper() for x in raw.split(",") if x.strip()] if raw else list(DEFAULT_SAAS)
    out = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def thesis(ticker):
    stars, view, risk = THESIS.get(ticker, ("—", "", ""))
    return {"stars": stars, "view": view, "risk": risk}


def build(ranked, macro_result=None, sell_put_plan=None, held=None, today=None):
    """Return {'names': [...], 'summary': {...}, 'event': {...}}."""
    import daily_watch

    names = saas_list()
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
        out.append(item)

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
        "discounted": [x["ticker"] for x in out
                       if x["from_high"] is not None and x["from_high"] <= -15],
    }
    return {"event": event, "names": out, "summary": summary}


# ----------------------------------------------------------------- rendering

def build_section(data):
    names = data.get("names") or []
    s = data.get("summary") or {}
    lines = [
        "## SaaS Watch — 软件 SaaS 名单\n",
        "固定名单（`SAAS_WATCH`）每天单独看：" + "、".join(x["ticker"] for x in names) + "。"
        "星级、定位和主要风险是固定观点，不随行情变化；旁边的评分、涨跌结构、买入档位和 sell put 首选是当天数据。"
        "这一栏和 AI Portfolio 分开：SaaS 的问题是估值与 AI 替代风险，不是算力周期。\n",
    ]
    if names:
        avg_score = f"{s['avg_score']:.0f}" if s.get("avg_score") is not None else "—"
        avg_1d = _pct(s.get("avg_1d"))
        def lst(key):
            return "、".join(s.get(key) or []) or "无"
        lines.append(
            f"**名单概况：** {s.get('in_run', 0)}/{s.get('total', 0)} 只在今日评分池，"
            f"平均评分 **{avg_score}**，平均 1 日 {avg_1d}；"
            f"站上 200 日线：{lst('above_200')}；距高点 ≥15% 折扣：{lst('discounted')}。\n"
        )
        lines.append(
            f"**今日档位：** 建议买入 {lst('buy_now')} · 分批/小加 {lst('scale_in')} · "
            f"候补 {lst('watch')} · 今日不买 {lst('no_buy')}；"
            f"有可写 put 的：{lst('puts')}。\n"
        )

    lines.append("### 观点与评分\n")
    rows = []
    for x in names:
        score = f"{x['score']:.1f}" if x["score"] is not None else "—"
        rec = REC_CN.get(x["recommendation"], x["recommendation"] or "—")
        rows.append([
            f"**{x['ticker']}**", x["stars"], x["view"] or "—", x["risk"] or "—",
            _money(x["price"]), _pct(x["change_1d"]), _pct(x["change_1m"]), _pct(x["change_3m"]),
            _pct(x["from_high"]),
            f"{x['rsi']:.0f}" if x["rsi"] is not None else "—",
            _ma_cell(x["price"], x["sma_50"]), _ma_cell(x["price"], x["sma_200"]),
            f"{score}（{x['grade']}）", rec, x["regime"],
        ])
    lines.append(_table(
        ["代码", "星级", "定位", "主要风险", "现价", "1日", "1月", "3月", "距高点", "RSI",
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
        head = f"- **{x['ticker']}**（{x['stars']}，{x['view']}；风险：{x['risk']}）："
        if not x["in_run"]:
            lines.append(head + "今日不在评分池（数据缺失或被跳过），只看固定观点与买点。")
            continue
        bits = [x["buy_note"]] if x["buy_note"] else []
        if x.get("trend_note"):
            bits.append(x["trend_note"])
        if x.get("entry_note"):
            bits.append(f"put：{x['entry_note']}")
        if x["reasons"]:
            bits.append("因素：" + "；".join(x["reasons"]))
        lines.append(head + " ".join(bits))
    lines.append("")

    lines.append(
        "星级是固定观点，评分是当天综合分（Technical/Trend/Alpha/Sentiment），两者不一致时以星级定要不要买、以评分定什么时候买。"
        "「买点」和「Sell Put 首选」只对在 `AI_PUT_WATCH` 名单里的名字有值（默认 CRM/NOW/ADBE）；"
        "股票与 put 二选一，不叠加。所有价格下单前重新报价。\n"
    )
    lines.append("---\n")
    return "\n".join(lines)


def email_lines(data):
    lines = ["=== SAAS WATCH ==="]
    for x in data.get("names") or []:
        score = f"评分 {x['score']:.0f}" if x["score"] is not None else "评分 —"
        rec = REC_CN.get(x["recommendation"], "")
        k = x.get("pick")
        put = (f"put {x['expiry']} ${k['strike']:.0f}P @{k['mid']:.2f}"
               if k and x.get("expiry") else (x.get("entry_label") or "put —"))
        lines.append(
            f"  {x['ticker']:<5} {x['stars']:<6} {_money(x['price']):>8} {_pct(x['change_1d']):>7}  "
            f"{score}{'(' + rec + ')' if rec else ''} | {x['buy_label']} | {put}"
        )
    return lines


if __name__ == "__main__":
    print(build_section(build([])))
