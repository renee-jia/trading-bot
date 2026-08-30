"""Offline tests for the daily-report stock-trend / add-vs-sell stance.

Run: python scripts/test_market_trend.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import macro_analyzer as ma
import report_generator as rg


def _md(**spy_extra):
    spy = {
        "from_high": -1.4,
        "change_1m": 3.4,
        "change_5d": -0.4,
        "above_sma200": True,
        "rsi": 77,
    }
    spy.update(spy_extra)
    return {
        "SPY": spy,
        "QQQ": {"from_high": -3.8, "change_1m": 3.0, "change_5d": -0.2},
        "SMH": {"from_high": -14.9, "change_5d": -4.2},
        "XLE": {"change_5d": 4.5},
        "^VIX": {"price": 15.2},
    }


def test_near_high_chip_selloff_is_rotation_not_crash():
    t = ma.classify_market_trend(_md())
    assert t["phase"] == "uptrend"
    assert t["structure"] == "internal_rotation"
    stance = ma.fallback_stance(t, macro_score=57, vix=15.2)
    assert stance == "scale_in"


def test_ten_percent_drawdown_is_correction():
    t = ma.classify_market_trend(_md(from_high=-12.0, change_1m=-8.0, above_sma200=False))
    assert t["phase"] == "correction"


def test_bear_without_panic_vix_trims():
    t = ma.classify_market_trend(_md(from_high=-22.0, change_1m=-15.0, above_sma200=False))
    assert t["phase"] == "bear_market"
    assert ma.fallback_stance(t, 40, vix=18) == "trim"


def test_bear_with_panic_vix_scales_in():
    t = {"phase": "bear_market", "structure": "broad", "vix": 32}
    assert ma.fallback_stance(t, 35, vix=32) == "scale_in"


def test_claude_all_in_near_highs_is_downgraded():
    trend = ma.classify_market_trend(_md())
    merged = ma.merge_trend_outlook(
        trend, 60, _md(),
        {"stance": "aggressive_buy", "sizing_guidance": "all in",
         "current_trend": "自定义趋势",
         "outlook_1w": {"direction": "down", "view": "本周看空"}},
    )
    assert merged["stance"] == "scale_in"
    assert merged["current_trend"] == "自定义趋势"
    assert merged["outlook_1w"]["direction"] == "down"


def test_claude_panic_sell_near_highs_is_trim():
    trend = ma.classify_market_trend(_md())
    merged = ma.merge_trend_outlook(
        trend, 40, _md(), {"stance": "aggressive_sell"}
    )
    assert merged["stance"] == "trim"


def test_report_section_has_three_horizons_and_stance():
    ranked = [{
        "ticker": "NVDA",
        "score_result": {
            "recommendation": "Buy",
            "regime": "bull",
            "components": {"trend": {"score": 63}},
        },
    }]
    trend = ma.classify_market_trend(_md())
    outlook = ma.fallback_trend_outlook(trend, 57, _md())
    text = rg._build_stock_trend_section(ranked, {
        "score": 57,
        "market_data": _md(),
        "trend": trend,
        "trend_outlook": outlook,
    })
    assert "## General — 美股趋势判断" in text
    assert "未来一周" in text and "未来一个月" in text and "未来半年" in text
    assert "仓位建议" in text
    assert outlook["stance_label"] in text


def test_tight_dollar_and_rising_yields_is_risk_off_trim():
    md = _md()
    md["^TNX"] = {"price": 4.85, "change_1d": 2.0, "change_5d": 4.5}
    md["^IRX"] = {"price": 3.73}
    md["UUP"] = {"change_1d": 0.6, "change_5d": 1.4}
    md["SPY"]["change_5d"] = -2.4
    md["IWM"] = {"change_5d": -4.0}
    md["^VIX"] = {"price": 21.0, "change_5d": 12.0}
    tape = ma.classify_macro_tape(md)
    assert tape["rates"] == "tighter"
    assert tape["dollar"] == "strong"
    assert tape["vol"] == "elevated"
    assert tape["risk"] == "risk_off"
    assert tape["stock_action"] == "trim"


def test_calm_vol_near_highs_is_hold_not_panic():
    md = _md()
    md["^TNX"] = {"price": 4.72, "change_1d": 0.2, "change_5d": -0.4}
    md["^IRX"] = {"price": 3.73}
    md["UUP"] = {"change_5d": 0.2}
    md["SPY"]["change_5d"] = 0.5
    md["^VIX"] = {"price": 14.4, "change_5d": -5.0}
    tape = ma.classify_macro_tape(md)
    assert tape["vol"] == "calm"
    assert tape["rates"] == "stable_tight"
    assert tape["risk"] in ("risk_on", "mixed")
    assert tape["stock_action"] in ("hold", "scale_in")


def test_inverted_curve_is_flagged():
    tape = ma.classify_macro_tape({
        "^TNX": {"price": 3.40, "change_5d": 0.1},
        "^IRX": {"price": 4.10},
        "^VIX": {"price": 16.0},
    })
    assert tape["curve_state"] == "inverted"
    assert tape["curve_10_3"] < 0


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok", fn.__name__)
    print(f"{len(tests)} tests passed")
