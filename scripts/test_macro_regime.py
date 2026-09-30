"""Regime Desk (macro_regime): offline invariants — no network, no LLM."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macro_regime as mr
import macro_events


def _series(n=400, start=100.0, drift=0.0005, seed=1):
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, 0.01, n)
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.Series(start * np.cumprod(1 + r), index=idx)


def test_tape_stats_fields_and_signs():
    s = _series()
    t = mr.tape_stats(s)
    assert set(t) >= {"last", "ret_5d", "ret_1m", "ret_3m", "from_high", "vs_sma50", "vs_sma200"}
    assert t["from_high"] <= 0.0 + 1e-9
    assert mr.tape_stats(s.iloc[:30]) is None


def test_breadth_from_frame_counts_and_bounds():
    px = pd.DataFrame({f"T{i}": _series(seed=i) for i in range(30)})
    px.iloc[-1, 0] = np.nan          # one name without today's print is excluded
    b = mr.breadth_from_frame(px)
    assert b["n"] == 29
    for k in ("above_50", "above_200", "pct_20_below_high"):
        assert 0 <= b[k] <= 100
    assert b["median_from_high"] <= 0


def test_universe_breadth_from_indicators():
    rows = [{"indicators": {"price": 100 + i, "sma_50": 100, "sma_200": 90 + i, "pct_from_52w_high": -i, "change_1m": i - 5}}
            for i in range(12)]
    b = mr.universe_breadth(rows)
    assert b["n"] == 12
    assert b["above_50"] == pytest.approx(11 / 12 * 100)
    assert mr.universe_breadth(rows[:3]) is None


def test_analog_buckets_and_stats():
    assert mr._dd_bucket(-1) == "距高点 <3%" and mr._dd_bucket(-20) == "回撤 >15%"
    assert mr._vix_bucket(30) == "VIX ≥25" and mr._tnx_bucket(0.5).startswith("10y 三月内升")
    n = 3000
    idx = pd.bdate_range("2010-01-01", periods=n)
    rng = np.random.default_rng(0)
    spy = pd.Series(100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n)), index=idx)
    vix = pd.Series(np.clip(rng.normal(18, 5, n), 9, 80), index=idx)
    tnx = pd.Series(np.clip(3 + np.cumsum(rng.normal(0, 0.03, n)), 0.5, 8), index=idx)
    a = mr.analog_stats(spy, vix, tnx)
    assert a is not None and "state" in a
    if a.get("fwd_6m_median") is not None:
        assert 0 <= a["fwd_6m_hit"] <= 100
        assert a["worst_6m_dd_median"] <= 0


def test_entry_ladder_levels_and_hits():
    rows = mr.entry_ladder(spx_last=7200, spx_peak=8000, vix=26)
    assert [r["tier"] for r in rows][:2] == ["普通回调", "第一买点"]
    first = rows[0]
    assert first["level"] == pytest.approx(8000 * 0.97)
    # -10% drawdown with VIX 26: tiers up to "好买点" (-8%, VIX 23) are hit; "恐慌" needs VIX 27
    hit = [r["tier"] for r in rows if r["hit"]]
    assert "好买点" in " ".join(hit) and not any("恐慌" in t for t in hit)


def _fake_data(tnx=5.26, vix=16.0, vix3m=18.1, above50=25.0, qqq_vs200=10.9, jpy10=1.0, brent=96.0, hy=3.02):
    return {
        "as_of": "2026-09-29",
        "tape": {"QQQ": {"last": 737.9, "vs_sma200": qqq_vs200, "vs_sma50": 3.4, "from_high": -1.3, "ret_1m": 3.1, "ret_5d": -1.3, "ret_3m": 0.3},
                 "SPY": {"last": 764.2, "vs_sma200": 6.8, "vs_sma50": 0.4, "from_high": -1.5, "ret_1m": -0.4, "ret_5d": -1.2, "ret_3m": 2.6}},
        "vol": {"^VIX": vix, "^VIX3M": vix3m, "vix_5d_ago": 14.2, "vix_pct_rank_1y": 27.0},
        "rates": {"^TNX": tnx, "^TNX_5d": 0.06, "^TNX_3m": 0.9},
        "other": {"JPY=X": {"last": 157.0, "ret_10d": jpy10, "ret_5d": 0.2, "ret_1m": 1.0},
                  "BZ=F": {"last": brent, "ret_5d": -1.0, "ret_1m": 15.0}},
        "fred": {"hy_oas": {"value": hy, "date": "2026-09-28", "change_5d": 0.34}},
        "breadth_sp500": {"n": 500, "as_of": "2026-09-29", "above_50": above50, "above_200": 43.0,
                          "median_from_high": -15.4, "pct_20_below_high": 35.0, "median_ret_1m": -5.5},
        "breadth_universe": None,
        "analog": None,
        "spx": {"last": 7671.0, "peak": 7799.0, "peak_date": "2026-08-13", "drawdown": -1.6},
        "events": [("2026-10-02", "非农", 3, ""), ("2026-10-28", "FOMC", 3, "")],
        "events_coverage_days": 100,
    }


def test_reduce_triggers_statuses():
    d = _fake_data()
    d["ladder"] = mr.entry_ladder(7671.0, 7799.0, 16.0)
    rows = mr.reduce_triggers(d)
    st = {lab: status for lab, _, _, status in rows}
    assert st["10 年期 >5.40%"].startswith("🟡")          # 5.26 is near, not hit
    assert st["Brent >120"].startswith("🟢")
    assert st["VIX 期限结构倒挂"].startswith("🟢")
    d2 = _fake_data(tnx=5.5, vix=30, vix3m=26, above50=15.0, qqq_vs200=-2.0, jpy10=-6.0, brent=125.0, hy=4.8)
    d2["ladder"] = mr.entry_ladder(7000.0, 7799.0, 30.0)
    assert all(status.startswith("🔴") for _, _, _, status in mr.reduce_triggers(d2))


def test_extract_json_handles_fences_and_prose():
    txt = 'Here you go:\n```json\n{"regime_label": "x", "scenarios": {"base": {"prob": 50}}}\n```\nthanks'
    assert mr._extract_json(txt)["regime_label"] == "x"
    assert mr._extract_json('prefix {"a": 1} suffix') == {"a": 1}
    assert mr._extract_json("no json here") is None


def test_section_renders_with_and_without_synthesis():
    d = _fake_data()
    d["ladder"] = mr.entry_ladder(7671.0, 7799.0, 16.0)
    d["reduce_triggers"] = mr.reduce_triggers(d)
    md = mr.build_section({"data": d, "synthesis": None, "source": "fallback"})
    assert md.startswith("## 宏观回撤与加仓时机 — Regime Desk")
    assert "AI 综合不可用" in md and "加仓阶梯" in md and "减仓/对冲触发" in md
    assert "| 美债 10y | 5.26% |" in md
    syn = {"regime_label": "紧缩驱动的避险", "regime_summary": "摘要。", "what_changed": "无重大新增",
           "drawdown_windows": [{"window": "10/27-30", "trigger": "FOMC+财报", "likelihood": "高"}],
           "scenarios": {"bull": {"prob": 25, "line": "b"}, "base": {"prob": 45, "line": "m"}, "bear": {"prob": 30, "line": "w"}},
           "entry_view": "等 -8%。", "reduce_view": "持有。", "top_risks": ["r1"], "news": [{"headline": "h", "why": "w"}],
           "sources": ["https://example.com/a"]}
    md2 = mr.build_section({"data": d, "synthesis": syn, "source": "claude-opus-5"})
    assert "紧缩驱动的避险" in md2 and "| 基准 | 45% |" in md2 and "10/27-30" in md2
    assert "example.com" in md2
    assert len(md2.encode()) < 22_000   # stays under the email section cap


def test_data_block_is_stable_text():
    d = _fake_data()
    d["ladder"] = mr.entry_ladder(7671.0, 7799.0, 16.0)
    d["reduce_triggers"] = mr.reduce_triggers(d)
    txt = mr._data_block(d)
    assert "as_of: 2026-09-29" in txt and "10y 5.26%" in txt and "现金入场阶梯" in txt


def test_events_calendar_helpers():
    up = macro_events.upcoming(3, today=date(2026, 10, 1))
    assert len(up) == 3 and up[0][0] >= date(2026, 10, 1)
    assert macro_events.coverage_days(today=date(2026, 10, 1)) > macro_events.MIN_FORWARD_DAYS
    assert macro_events.coverage_days(today=date(2030, 1, 1)) == 0
    for d, label, stars, _ in macro_events.EVENTS:
        date.fromisoformat(d)
        assert 1 <= stars <= 3 and label
