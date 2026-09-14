"""Offline tests for the SaaS Watch section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import saas_watch as sw
from test_ai_portfolio import _row, _plan

TODAY = date(2026, 9, 10)


def test_default_list_thesis_and_env_override():
    assert sw.saas_list() == ["CRM", "NOW", "ADBE", "DDOG", "SNOW", "CRWD", "NET", "WDAY", "INTU"]
    assert sw.thesis("CRM") == {"stars": "⭐⭐⭐⭐⭐", "view": "首选 value SaaS", "risk": "增长不够快"}
    assert sw.thesis("ADBE")["stars"] == "⭐⭐⭐⭐½" and sw.thesis("ZZZ") == {"stars": "—", "view": "", "risk": ""}
    os.environ["SAAS_WATCH"] = " crm, NOW ,crm, zzz"
    try:
        assert sw.saas_list() == ["CRM", "NOW", "ZZZ"]
    finally:
        del os.environ["SAAS_WATCH"]


def test_build_rolls_up_cards_plan_and_missing_names():
    os.environ["SAAS_WATCH"] = "CRM,NOW,ADBE"
    try:
        rows = [_row("CRM", from_high=-22.0), _row("NOW", score=55, d1=4.7, rsi=68, from_high=-1.0, rec="Hold", grade="C")]
        data = sw.build(rows, macro_result={"macro_tape": None}, sell_put_plan=_plan("CRM", "ADBE"),
                        held={"NOW"}, today=TODAY)
    finally:
        del os.environ["SAAS_WATCH"]
    by = {x["ticker"]: x for x in data["names"]}
    assert [x["ticker"] for x in data["names"]] == ["CRM", "NOW", "ADBE"]
    assert by["CRM"]["stars"] == "⭐⭐⭐⭐⭐" and by["CRM"]["view"] == "首选 value SaaS"
    assert by["CRM"]["buy_action"] == "buy" and by["CRM"]["pick"]["strike"] == 92.0
    assert by["NOW"]["held"] and by["NOW"]["stock_label"] == "不跟风追涨"
    assert not by["ADBE"]["in_run"] and by["ADBE"]["score"] is None and by["ADBE"]["buy_levels"]["l1"] == 97.0
    s = data["summary"]
    assert s["in_run"] == 2 and s["total"] == 3 and s["buy_now"] == ["CRM"] and s["held"] == ["NOW"]
    assert s["discounted"] == ["CRM"] and set(s["puts"]) == {"CRM", "ADBE"}


def test_section_tables_are_well_formed_and_email_lines_render():
    os.environ["SAAS_WATCH"] = "CRM,ADBE"
    try:
        data = sw.build([_row("CRM")], sell_put_plan=_plan("CRM"), held=set(), today=TODAY)
    finally:
        del os.environ["SAAS_WATCH"]
    md = sw.build_section(data)
    assert md.startswith("## SaaS Watch — 软件 SaaS 名单")
    assert "### 观点与评分" in md and "### 今日动作" in md
    assert "| **CRM** | ⭐⭐⭐⭐⭐ | 首选 value SaaS | 增长不够快 |" in md
    assert "2026-10-16 $92P @ $1.84" in md and "$97.00 / $95.00 / $90.00" in md
    assert "- **ADBE**（⭐⭐⭐⭐½，最深 value / contrarian；风险：AI disruption 最大）：今日不在评分池" in md
    widths = set()
    for line in md.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            widths.add(line.count("|"))
    assert widths == {16, 8}  # 15-column thesis table, 7-column action table
    assert "持仓" not in md and "已持有" not in md
    lines = sw.email_lines(data)
    assert lines[0] == "=== SAAS WATCH ==="
    assert any(l.strip().startswith("CRM") and "⭐⭐⭐⭐⭐" in l and "$92P" in l and "建议买入" in l for l in lines)
    assert any(l.strip().startswith("ADBE") and "不在今日评分池" in l for l in lines)


def test_empty_run_still_renders_every_name():
    data = sw.build([], sell_put_plan=None, held=set(), today=TODAY)
    md = sw.build_section(data)
    assert md.count("今日不在评分池") == len(sw.saas_list())
    assert data["summary"]["avg_score"] is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
