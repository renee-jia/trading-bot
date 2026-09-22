"""Offline tests for the 半导体加仓 section. No network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import chip_layout as cl
from test_ai_portfolio import _row, _plan

TODAY = date(2026, 9, 10)


def test_default_lists_and_env_override():
    assert cl.layout_list() == ["NVDA", "AVGO", "TSM", "ASML", "MU", "KLAC", "LRCX", "AMAT"]
    assert [t for _, t, _ in cl.priority_ranks()] == [
        "AVGO", "TSM", "AMAT", "SNPS", "COHR", "NVDA", "LRCX", "ALAB", "MRVL", "CRDO", "AMD", "MU",
    ]
    assert cl.thesis("AVGO")["pt"] == 532
    assert cl.zone_status(340, 330, 350) == "已进入首批区间"
    assert cl.zone_status(400, 330, 350).startswith("高于区间")
    os.environ["CHIP_LAYOUT"] = " nvda, AMAT ,nvda"
    os.environ["CHIP_PRIORITY"] = "avgo, SNPS ,avgo"
    try:
        assert cl.layout_list() == ["NVDA", "AMAT"]
        assert cl.priority_ranks() == [(1, "AVGO", "custom ASIC + networking"),
                                      (2, "SNPS", "EDA 收费站、与 SMH 重叠较少")]
    finally:
        del os.environ["CHIP_LAYOUT"]
        del os.environ["CHIP_PRIORITY"]


def test_build_rolls_up_priority_zone_and_missing_names():
    os.environ["CHIP_LAYOUT"] = "NVDA,AMAT"
    os.environ["CHIP_PRIORITY"] = "AVGO,SNPS"
    try:
        rows = [_row("NVDA", from_high=-1.0, price=227.0),
                _row("AVGO", from_high=-27.0, price=340.0)]
        data = cl.build(rows, sell_put_plan=_plan("NVDA", "AVGO"), held=set(), today=TODAY)
    finally:
        del os.environ["CHIP_LAYOUT"]
        del os.environ["CHIP_PRIORITY"]
    by = {x["ticker"]: x for x in data["names"]}
    assert [x["ticker"] for x in data["names"]] == ["NVDA", "AMAT"]
    assert by["NVDA"]["drawdown_label"] == "接近高位"
    assert by["NVDA"]["zone_status"].startswith("高于区间")
    assert not by["AMAT"]["in_run"]
    pr = {x["ticker"]: x for x in data["priority"]}
    assert [x["ticker"] for x in data["priority"]] == ["AVGO", "SNPS"]
    assert pr["AVGO"]["priority"] == 1 and pr["AVGO"]["reason"] == "custom ASIC + networking"
    assert pr["AVGO"]["zone_status"] == "已进入首批区间"
    assert pr["AVGO"]["add_verdict"].endswith("可以抄底")
    assert not pr["SNPS"]["in_run"] and "EDA" in pr["SNPS"]["reason"]
    assert data["summary"]["in_zone"] == ["AVGO"]


def test_section_and_email_render_priority_first():
    os.environ["CHIP_LAYOUT"] = "NVDA"
    os.environ["CHIP_PRIORITY"] = "AVGO,NVDA"
    try:
        data = cl.build([_row("AVGO", from_high=-27.0, price=340.0)],
                        sell_put_plan=_plan("AVGO"), held=set(), today=TODAY)
    finally:
        del os.environ["CHIP_LAYOUT"]
        del os.environ["CHIP_PRIORITY"]
    md = cl.build_section(data)
    assert md.startswith("## 半导体加仓 — Chip Portfolio")
    assert "### 加仓优先级" in md and "### 估值、回撤与首批区间" in md
    assert "| 1 | **AVGO** | custom ASIC + networking |" in md
    assert "| **NVDA** | AI Compute |" in md
    assert "持仓" not in md and "已持有" not in md
    lines = cl.email_lines(data)
    assert lines[0] == "=== 半导体加仓 ==="
    assert "优先级:" in lines
    assert any("AVGO" in l and "可以抄底" in l for l in lines)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
