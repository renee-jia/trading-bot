"""Offline tests for the AI Portfolio roll-up section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import ai_portfolio as ap

TODAY = date(2026, 9, 9)


def _row(ticker, score=70, d1=-2.0, d5=-3.0, d1m=4.0, d3m=12.0, rsi=48, from_high=-9.0,
         price=100.0, sma50=95.0, sma200=85.0, rec="Buy", grade="B+", regime="bull"):
    return {
        "ticker": ticker, "name": f"{ticker} Inc.",
        "indicators": {"price": price, "change_1d": d1, "change_5d": d5, "change_1m": d1m,
                       "change_3m": d3m, "rsi": rsi, "pct_from_52w_high": from_high,
                       "sma_50": sma50, "sma_200": sma200},
        "score_result": {"score": score, "recommendation": rec, "grade": grade, "regime": regime,
                         "reasoning": ["Golden Cross", "RSI neutral", "third reason"]},
    }


def _plan(*tickers):
    names = []
    for t in tickers:
        names.append({"ticker": t, "name": t, "held": False, "spot": 100.0,
                      "levels": {"sma_50": 95.0, "sma_200": 85.0},
                      "buy_levels": {"l1": 97.0, "l2": 95.0, "l3": 90.0},
                      "expiry": "2026-10-16", "dte": 37, "earnings": date(2026, 11, 18),
                      "pick": {"strike": 92.0, "mid": 1.84, "delta": 0.25, "ann_pct": 19.0},
                      "entry_label": "适合 sell put 建仓", "entry_note": "评分过线", "today_label": "今日可加（回撤中）",
                      "trend_note": "长期多头（价 > 50日 > 200日）"})
    return {"names": names}


def test_default_list_and_env_override():
    for t in ("NVDA", "AVGO", "GOOGL", "META", "TSM", "MU", "AMAT", "ORCL", "ANET", "SNPS"):
        assert t in ap.portfolio_list()
    os.environ["AI_PORTFOLIO"] = " nvda, META ,nvda"
    os.environ["AI_PORTFOLIO_WATCH"] = "msft, NVDA, vrt"
    try:
        assert ap.portfolio_list() == ["NVDA", "META"]
        assert ap.watchlist_names() == ["MSFT", "VRT"]      # core names never repeat in the watch tier
    finally:
        del os.environ["AI_PORTFOLIO"]
        del os.environ["AI_PORTFOLIO_WATCH"]
    for t in ("ASML", "KLAC", "MSFT", "AMZN", "VRT", "STX", "SNDK", "PLTR", "CRWD", "CRDO", "COHR"):
        assert t in ap.watchlist_names()


def test_build_rolls_up_cards_plan_and_missing_names():
    os.environ["AI_PORTFOLIO"] = "NVDA,META,SNPS"
    os.environ["AI_PORTFOLIO_WATCH"] = "VRT,STX"
    try:
        rows = [_row("NVDA"), _row("META", score=55, d1=4.7, rsi=68, from_high=-1.0, rec="Hold", grade="C"),
                _row("VRT", score=72)]
        data = ap.build(rows, macro_result={"macro_tape": None}, sell_put_plan=_plan("NVDA", "SNPS", "VRT"),
                        held={"META"}, today=TODAY)
    finally:
        del os.environ["AI_PORTFOLIO"]
        del os.environ["AI_PORTFOLIO_WATCH"]
    watch = {x["ticker"]: x for x in data["watch"]}
    assert list(watch) == ["VRT", "STX"] and watch["VRT"]["layer"] == "电力散热"
    assert watch["VRT"]["buy_action"] == "buy" and data["summary"]["watch_buyable"] == ["VRT"]
    assert not watch["STX"]["in_run"]
    by = {x["ticker"]: x for x in data["names"]}
    assert [x["ticker"] for x in data["names"]] == ["NVDA", "META", "SNPS"]
    assert by["NVDA"]["buy_action"] == "buy" and by["NVDA"]["pick"]["strike"] == 92.0
    assert by["NVDA"]["earnings"] == "2026-11-18" and by["NVDA"]["reasons"] == ["Golden Cross", "RSI neutral"]
    assert by["META"]["held"] and by["META"]["stock_label"] == "不跟风追涨"
    assert not by["SNPS"]["in_run"] and by["SNPS"]["score"] is None
    assert by["SNPS"]["price"] == 100.0 and by["SNPS"]["buy_levels"]["l1"] == 97.0  # from the put plan
    s = data["summary"]
    assert s["in_run"] == 2 and s["total"] == 3            # watch tier stays out of the core summary
    assert s["buy_now"] == ["NVDA"] and s["held"] == ["META"] and set(s["puts"]) == {"NVDA", "SNPS"}
    assert "NVDA" in s["above_200"] and "META" in s["above_200"]


def test_section_tables_are_well_formed_and_email_lines_render():
    os.environ["AI_PORTFOLIO"] = "NVDA,SNPS"
    os.environ["AI_PORTFOLIO_WATCH"] = "VRT"
    try:
        data = ap.build([_row("NVDA"), _row("VRT", score=72)], sell_put_plan=_plan("NVDA", "VRT"),
                        held=set(), today=TODAY)
    finally:
        del os.environ["AI_PORTFOLIO"]
        del os.environ["AI_PORTFOLIO_WATCH"]
    md = ap.build_section(data)
    assert md.startswith("## AI Portfolio — 核心 AI 名单")
    assert "### 行情与评分" in md and "### 今日动作" in md and "### 观察名单" in md
    assert "| **VRT** | 电力散热 |" in md and "观察名单今日过线（买入/分批）：VRT" in md
    assert "2026-10-16 $92P @ $1.84" in md and "$97.00 / $95.00 / $90.00" in md
    assert "- **SNPS**：今日不在评分池" in md
    widths = set()
    for line in md.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            widths.add(line.count("|"))
    assert widths == {16, 8, 17}  # 15-column tape table, 7-column action table, 16-column watch table
    assert "持仓" not in md and "已持有" not in md
    lines = ap.email_lines(data)
    assert lines[0] == "=== AI PORTFOLIO ==="
    assert any(l.strip().startswith("NVDA") and "$92P" in l and "可以抄底" in l for l in lines)
    assert any(l.strip().startswith("SNPS") and "不在今日评分池" in l for l in lines)
    assert "观察名单:" in lines and any(l.strip().startswith("VRT") and "可以抄底" in l for l in lines)


def test_empty_run_still_renders_every_name():
    data = ap.build([], sell_put_plan=None, held=set(), today=TODAY)
    md = ap.build_section(data)
    assert md.count("今日不在评分池") == len(ap.portfolio_list())
    assert data["summary"]["avg_score"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
