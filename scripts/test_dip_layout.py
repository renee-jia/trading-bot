"""Offline tests for the 抄底布局 section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import dip_layout as dl
from test_ai_portfolio import _row, _plan

TODAY = date(2026, 9, 10)


def test_default_list_thesis_and_env_override():
    assert dl.layout_list() == [
        "BRK-B", "UNH", "ABBV", "ISRG", "JPM", "V", "MA", "ETN", "PWR", "CAT",
        "COST", "WMT", "BKNG", "FCX", "XOM", "NEE", "CEG", "GE", "RTX", "CB",
    ]
    assert dl.thesis("BRK.B") == {
        "display": "BRK.B",
        "module": "Diversified Core",
        "view": "多元化核心，保险/铁路/能源/工业/现金流",
    }
    assert dl.thesis("BRKB")["display"] == "BRK.B"
    assert dl.thesis("UNH")["module"] == "Healthcare – Managed Care"
    assert dl.thesis("ZZZ") == {"display": "ZZZ", "module": "", "view": ""}
    os.environ["DIP_LAYOUT"] = " brk.b, UNH ,brk.b, zzz"
    try:
        assert dl.layout_list() == ["BRK-B", "UNH", "ZZZ"]
    finally:
        del os.environ["DIP_LAYOUT"]


def test_build_rolls_up_cards_plan_and_missing_names():
    os.environ["DIP_LAYOUT"] = "BRK.B,UNH,ABBV"
    try:
        rows = [
            _row("BRK-B", from_high=-22.0),
            _row("UNH", score=55, d1=4.7, rsi=68, from_high=-1.0, rec="Hold", grade="C"),
        ]
        data = dl.build(rows, macro_result={"macro_tape": None},
                        sell_put_plan=_plan("BRK-B", "ABBV"),
                        held={"UNH"}, today=TODAY)
    finally:
        del os.environ["DIP_LAYOUT"]
    by = {x["ticker"]: x for x in data["names"]}
    assert [x["ticker"] for x in data["names"]] == ["BRK-B", "UNH", "ABBV"]
    assert by["BRK-B"]["display"] == "BRK.B"
    assert by["BRK-B"]["module"] == "Diversified Core"
    assert by["BRK-B"]["buy_action"] == "buy" and by["BRK-B"]["pick"]["strike"] == 92.0
    assert by["UNH"]["held"] and by["UNH"]["stock_label"] == "不跟风追涨"
    assert not by["ABBV"]["in_run"] and by["ABBV"]["score"] is None
    assert by["ABBV"]["view"] == "高现金流、分红、药品管线"
    s = data["summary"]
    assert s["in_run"] == 2 and s["total"] == 3
    assert s["buy_now"] == ["BRK.B"] and s["discounted"] == ["BRK.B"]
    assert "BRK.B" in s["above_200"]


def test_section_tables_are_well_formed_and_email_lines_render():
    os.environ["DIP_LAYOUT"] = "BRK.B,ABBV"
    try:
        data = dl.build([_row("BRK-B", from_high=-22.0)],
                        sell_put_plan=_plan("BRK-B"), held=set(), today=TODAY)
    finally:
        del os.environ["DIP_LAYOUT"]
    md = dl.build_section(data)
    assert md.startswith("## 抄底布局 — Diversified Portfolio")
    assert "### 模块与定位" in md and "### 今日可动手" in md
    assert "| Diversified Core | **BRK.B** | 多元化核心，保险/铁路/能源/工业/现金流 |" in md
    assert "| 抄底 | **BRK.B** | Diversified Core |" in md
    assert "- **ABBV**（Healthcare – Pharma）：今日不在评分池" in md
    widths = set()
    for line in md.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            widths.add(line.count("|"))
    assert widths == {15, 10}  # 14-column thesis table, 9-column action table
    assert "持仓" not in md and "已持有" not in md
    lines = dl.email_lines(data)
    assert lines[0] == "=== 抄底布局 ==="
    assert any("BRK.B" in l and "Diversified Core" in l and "可以抄底" in l for l in lines)
    assert any(l.strip().startswith("ABBV") for l in lines)


def test_empty_run_still_renders_every_name():
    data = dl.build([], sell_put_plan=None, held=set(), today=TODAY)
    md = dl.build_section(data)
    assert md.count("今日不在评分池") == len(dl.layout_list())
    assert data["summary"]["avg_score"] is None
    assert "BRK.B" in md and "CEG" in md and "CB" in md


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
