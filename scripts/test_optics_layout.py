"""Offline tests for the 光学互联 section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import optics_layout as ol
from test_ai_portfolio import _row, _plan

TODAY = date(2026, 9, 10)


def test_default_list_thesis_and_env_override():
    assert ol.layout_list() == ["COHR", "LITE", "AAOI", "CRDO", "MRVL", "GLW", "LYTE"]
    assert ol.thesis("COHR") == {
        "module": "光器件 / 激光 / transceiver",
        "view": "我最喜欢的 core optics 标的之一",
    }
    assert ol.thesis("LYTE")["module"] == "ETF"
    os.environ["OPTICS_LAYOUT"] = " cohr, LITE ,cohr, zzz"
    try:
        assert ol.layout_list() == ["COHR", "LITE", "ZZZ"]
    finally:
        del os.environ["OPTICS_LAYOUT"]


def test_build_and_section_include_drawdown():
    os.environ["OPTICS_LAYOUT"] = "COHR,LITE"
    try:
        data = ol.build([_row("COHR", from_high=-22.0)], sell_put_plan=_plan("COHR"),
                        held=set(), today=TODAY)
    finally:
        del os.environ["OPTICS_LAYOUT"]
    by = {x["ticker"]: x for x in data["names"]}
    assert by["COHR"]["add_verdict"].endswith("可以抄底")
    assert by["COHR"]["drawdown_label"] == "深回撤"
    assert not by["LITE"]["in_run"]
    md = ol.build_section(data)
    assert md.startswith("## 光学互联 — Optics Portfolio")
    assert "| 光器件 / 激光 / transceiver | **COHR** | 我最喜欢的 core optics 标的之一 |" in md
    assert "### 今日可动手" in md
    assert "持仓" not in md
    lines = ol.email_lines(data)
    assert lines[0] == "=== 光学互联 ==="
    assert any("COHR" in l and "可以抄底" in l for l in lines)


def test_empty_run_still_renders_every_name():
    data = ol.build([], sell_put_plan=None, held=set(), today=TODAY)
    md = ol.build_section(data)
    assert md.count("今日不在评分池") == len(ol.layout_list())
    assert "LYTE" in md and "AAOI" in md and "GLW" in md


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
