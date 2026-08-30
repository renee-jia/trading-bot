"""Offline unit tests for daily_watch (options desk / config / movers).

Run: python scripts/test_daily_watch.py
No network calls.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import daily_watch as dw


def _row(ticker, d1, d5=0.0, d1m=0.0, rsi=50, score=70, rec="Buy", name=None):
    return {
        "ticker": ticker,
        "name": name or ticker,
        "indicators": {
            "change_1d": d1,
            "change_5d": d5,
            "change_1m": d1m,
            "rsi": rsi,
        },
        "score_result": {"score": score, "recommendation": rec},
    }


def test_extended_winner_is_trim_not_chase():
    card = dw.classify_stock(
        _row("TEAM", d1=5.2, d5=18.0, d1m=94.0, rsi=79, score=68),
        held=True,
    )
    assert card["stock_action"] == "trim_extended"
    card = dw.classify_options(card, {"status": "open", "reason": ""})
    assert card["options_action"] == "sell_call"


def test_rip_without_extension_is_no_chase():
    card = dw.classify_stock(_row("NVDA", d1=4.8, d5=3.0, d1m=8.0, rsi=62, score=74))
    assert card["stock_action"] == "no_chase"
    card = dw.classify_options(card, {"status": "open"})
    assert card["options_action"] == "no_long_call"


def test_quality_dip_allows_put_if_not_held():
    card = dw.classify_stock(
        _row("KLAC", d1=-5.6, d5=-3.0, d1m=-8.0, rsi=38, score=71),
        held=False,
    )
    assert card["stock_action"] == "quality_dip"
    card = dw.classify_options(card, {"status": "open"})
    assert card["options_action"] == "sell_put"

    held = dw.classify_stock(
        _row("KLAC", d1=-5.6, d5=-3.0, rsi=38, score=71),
        held=True,
    )
    held = dw.classify_options(held, {"status": "open"})
    assert held["options_action"] == "no_put"


def test_weak_dip_is_knife():
    card = dw.classify_stock(
        _row("INTC", d1=-6.2, d5=-8.0, rsi=32, score=41, rec="Reduce"),
        held=False,
    )
    assert card["stock_action"] == "avoid_knife"
    card = dw.classify_options(card, {"status": "open"})
    assert card["options_action"] == "none"


def test_soxl_never_gets_puts_or_chase():
    up = dw.classify_stock(_row("SOXL", d1=9.0, d5=12.0, rsi=80, score=55))
    up = dw.classify_options(up, {"status": "open"})
    assert up["stock_action"] == "no_chase"
    assert up["options_action"] == "skip_levered"

    down = dw.classify_stock(_row("SOXL", d1=-9.0, d5=-12.0, rsi=30, score=72))
    down = dw.classify_options(down, {"status": "open"})
    assert down["stock_action"] == "avoid_knife"
    assert down["options_action"] == "skip_levered"


def test_fomc_blackout_blocks_new_shorts():
    ev = dw.options_event_status(today=date(2026, 9, 15))
    assert ev["status"] == "blackout"
    card = dw.classify_options(
        dw.classify_stock(_row("META", d1=5.0, d1m=40.0, rsi=78), held=True),
        ev,
    )
    assert card["options_action"] == "wait_event"


def test_nfp_eve_is_blackout_week_before_is_caution():
    assert dw.options_event_status(today=date(2026, 9, 3))["status"] == "blackout"
    assert dw.options_event_status(today=date(2026, 9, 1))["status"] == "caution"
    # 10/5 is 9 days before 10/14 CPI — outside the 5-day caution window
    assert dw.options_event_status(today=date(2026, 10, 5))["status"] == "open"


def test_config_watch_keeps_core_and_filters_quiet_names():
    ranked = [
        _row("NVDA", d1=0.2, d5=1.0, rsi=52, score=70, name="Nvidia"),
        _row("TEAM", d1=6.0, d5=18.0, d1m=90.0, rsi=80, score=66, name="Atlassian"),
        _row("WMT", d1=0.1, d5=0.4, rsi=50, score=55, name="Walmart"),
    ]
    cards, _ = dw.annotate(ranked, held=set(), today=date(2026, 8, 24))
    core, extras = dw.config_watch_cards(cards, extra_core=())
    core_tickers = {c["ticker"] for c in core}
    extra_tickers = {c["ticker"] for c in extras}
    assert "NVDA" in core_tickers
    assert "TEAM" in extra_tickers
    assert "WMT" not in extra_tickers


def test_top_movers_order_and_labels():
    ranked = [
        _row("AAA", d1=8.0, score=60),
        _row("BBB", d1=3.0, score=60),
        _row("CCC", d1=-9.0, score=72),
        _row("DDD", d1=-1.0, score=50),
    ]
    cards, _ = dw.annotate(ranked, held=set(), today=date(2026, 8, 24))
    up, down = dw.top_movers(cards, n=2)
    assert [c["ticker"] for c in up] == ["AAA", "BBB"]
    assert [c["ticker"] for c in down] == ["CCC", "DDD"]
    assert all(c["change_1d"] > 0 for c in up)
    assert all(c["change_1d"] < 0 for c in down)
    assert up[0]["stock_action"] == "no_chase"
    assert down[0]["stock_action"] == "quality_dip"


def test_report_sections_render():
    ranked = [
        _row("NVDA", d1=-5.2, d5=-2.0, rsi=40, score=73, name="Nvidia"),
        _row("TEAM", d1=5.5, d5=20.0, d1m=94.0, rsi=79, score=68, name="Atlassian"),
        _row("SOXL", d1=11.0, d5=15.0, rsi=82, score=50, name="SOXL"),
        _row("META", d1=1.2, d5=5.9, rsi=50, score=64, name="Meta"),
    ]
    desk = dw.build_options_desk_section(
        ranked, held={"TEAM", "META"}, today=date(2026, 8, 24),
        macro_result={"trend": {"vix": 14.4}},
    )
    watch = dw.build_config_watch_section(
        ranked, held={"TEAM", "META"}, today=date(2026, 8, 24), extra_core=("META",)
    )
    movers = dw.build_top_movers_section(
        ranked, held={"TEAM", "META"}, today=date(2026, 8, 24),
    )
    assert "Options Desk" in desk
    assert "可写权利金" in desk or "谨慎" in desk or "冻结" in desk
    assert "TEAM" in desk
    assert "Config 关注名单" in watch
    assert "核心池" in watch
    assert "Top Movers" in movers
    assert "不跟风" in movers or "超买" in movers
    email = dw.email_watch_lines(
        ranked, held={"TEAM", "META"}, today=date(2026, 8, 24),
    )
    assert any("OPTIONS DESK" in ln for ln in email)
    assert any("TOP MOVERS" in ln for ln in email)

    macro = dw.build_macro_desk_section(
        {
            "score": 57,
            "recommendation": "Neutral",
            "market_data": {
                "SPY": {"change_5d": 0.5},
                "IWM": {"change_5d": -1.4},
                "SMH": {"change_5d": -1.3},
                "^VIX": {"price": 14.4, "change_5d": -4.6},
                "^TNX": {"price": 4.72, "change_1d": 1.0, "change_5d": -0.4},
                "^TYX": {"price": 5.21},
                "^IRX": {"price": 3.73},
                "UUP": {"change_1d": 0.6, "change_5d": 1.0},
                "GLD": {"change_5d": -3.4},
                "USO": {"change_5d": -0.5},
                "HYG": {"change_5d": 0.2},
            },
        },
        today=date(2026, 8, 30),
    )
    assert "Macro Desk" in macro
    assert "利率" in macro and "美元" in macro and "波动率" in macro
    assert "NFP" in macro or "CPI" in macro or "FOMC" in macro
    assert any("MACRO DESK" in ln for ln in dw.email_macro_lines(
        {"market_data": {"^VIX": {"price": 14.4}, "^TNX": {"price": 4.72}}},
        today=date(2026, 8, 30),
    ))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"\n{len(tests)} tests passed")
