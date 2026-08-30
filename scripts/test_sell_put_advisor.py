"""Offline unit tests for sell_put_advisor filters.

Run: python scripts/test_sell_put_advisor.py
No network calls.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import covered_call_advisor as cca
import sell_put_advisor as spa

EXPS = [
    "2026-08-21", "2026-08-28", "2026-09-04", "2026-09-11",
    "2026-09-18", "2026-09-25", "2026-10-02", "2026-10-16",
    "2026-11-20", "2026-12-18",
]


def _row(ticker, d1, d5, d1m=0.0, rsi=50, score=70, rec="Buy"):
    return {
        "ticker": ticker,
        "name": ticker,
        "indicators": {"change_1d": d1, "change_5d": d5, "change_1m": d1m, "rsi": rsi},
        "score_result": {"score": score, "recommendation": rec},
    }


def test_fade_after_rally_is_not_a_dip():
    ranked = [
        _row("STX", d1=-8.2, d5=11.3),   # one-day crash after melt-up
        _row("AVGO", d1=-2.9, d5=-8.4),  # genuine 5-day dump
    ]
    cands, skipped = spa.find_drop_candidates(ranked, exclude=set())
    tickers = {c["ticker"] for c in cands}
    assert "AVGO" in tickers
    assert "STX" not in tickers
    assert any(s["ticker"] == "STX" for s in skipped)


def test_one_day_crash_still_counts_without_a_rally():
    ranked = [_row("KLAC", d1=-5.6, d5=-3.0)]
    cands, skipped = spa.find_drop_candidates(ranked, exclude=set())
    assert [c["ticker"] for c in cands] == ["KLAC"]
    assert skipped == []


def test_held_names_are_excluded():
    ranked = [
        _row("META", d1=-3.2, d5=-8.1),
        _row("KLAC", d1=-5.6, d5=-3.0),
    ]
    cands, skipped = spa.find_drop_candidates(ranked, exclude={"META"})
    assert [c["ticker"] for c in cands] == ["KLAC"]
    assert skipped[0]["ticker"] == "META"


def test_sep18_blocked_by_fomc_oct16_allowed():
    assert spa.expiry_hits_macro(date(2026, 9, 18))   # Fri after Wed FOMC
    assert spa.expiry_hits_macro(date(2026, 9, 16))   # FOMC day
    assert spa.expiry_hits_macro(date(2026, 9, 4))    # NFP on expiry
    assert not spa.expiry_hits_macro(date(2026, 10, 16))  # CPI two days earlier is ok
    assert not spa.expiry_hits_macro(date(2026, 9, 25))


def test_pick_expiry_skips_fomc_week_monthly():
    safe = spa.filter_macro_safe_expiries(EXPS)
    assert "2026-09-18" not in safe
    assert "2026-10-16" in safe
    exp, dte, status = cca._pick_expiry(safe, date(2026, 10, 28), today=date(2026, 8, 18))
    assert status == "ok" and exp == "2026-10-16"
    assert dte == 59


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(tests)} tests passed.")
