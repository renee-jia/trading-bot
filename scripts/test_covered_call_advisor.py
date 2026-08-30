"""Offline unit tests for covered_call_advisor strategy logic.

Run: python scripts/test_covered_call_advisor.py
All tests are synthetic/deterministic — no network calls.
"""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import covered_call_advisor as cca

EXPS = [
    "2026-08-07", "2026-08-14", "2026-08-21", "2026-08-28",
    "2026-09-04", "2026-09-11", "2026-09-18", "2026-09-25",
    "2026-10-02", "2026-10-09", "2026-10-16", "2026-11-20",
    "2026-12-18", "2027-01-15", "2027-03-19", "2027-06-17", "2028-01-21",
]
EARN = date(2026, 10, 28)
NEXT_EARN = date(2027, 1, 27)


def test_expiry_calendar_walk():
    # (today, earnings, expected_expiry, expected_status)
    cases = [
        (date(2026, 8, 3), EARN, "2026-09-18", "ok"),      # standard 45d entry
        (date(2026, 8, 28), EARN, "2026-10-16", "ok"),     # roll lands on last pre-earnings monthly
        (date(2026, 9, 25), EARN, "2026-10-16", "short"),  # forced short cycle before earnings
        (date(2026, 10, 5), EARN, None, "wait"),           # <15d to any pre-earnings expiry
        (date(2026, 10, 27), EARN, None, "wait"),          # earnings eve
        (date(2026, 10, 29), NEXT_EARN, "2026-12-18", "ok"),  # resumes day after earnings
        (date(2026, 8, 3), None, "2026-09-18", "ok"),      # unknown earnings: never a LEAP
        (date(2027, 1, 20), NEXT_EARN, None, "wait"),      # next earnings window
    ]
    for today, earn, want_exp, want_status in cases:
        exp, _, status = cca._pick_expiry(EXPS, earn, today=today)
        assert (exp, status) == (want_exp, want_status), \
            f"{today}: got ({exp}, {status}), want ({want_exp}, {want_status})"


def test_monthly_detection():
    assert cca._is_monthly(date(2026, 9, 18))
    assert cca._is_monthly(date(2026, 10, 16))
    assert not cca._is_monthly(date(2026, 9, 11))


def test_bs_delta_matches_scipy_reference():
    # Reference values computed with scipy.stats.norm on 2026-08-03
    for k, ref in [(390, 0.402), (400, 0.326), (410, 0.258), (420, 0.199)]:
        d = cca._bs_call_delta(373.51, k, 46 / 365, 0.037, 0.35)
        assert abs(d - ref) < 0.005, f"K={k}: {d:.3f} vs ref {ref}"


def test_momentum_regime():
    # V-recovery near the high (negative 3m return) must still read as trending
    googl_like = pd.Series(
        [300.0] * 100 + list(np.linspace(300, 408, 80))
        + list(np.linspace(408, 317, 5)) + list(np.linspace(317, 373.5, 67)))
    regime, lo, hi, _ = cca._momentum_regime(googl_like)
    assert "趋势强" in regime and (lo, hi) == (0.4, 0.6)

    meta_like = pd.Series(
        [600.0] * 100 + list(np.linspace(600, 787, 80)) + list(np.linspace(787, 590, 72)))
    regime, lo, hi, _ = cca._momentum_regime(meta_like)
    assert "磨底" in regime and (lo, hi) == (0.8, 1.0)

    neutral = pd.Series([95.0] * 150 + [100.0] * 30 + [88.0] * 72)
    regime, lo, hi, _ = cca._momentum_regime(neutral)
    assert "中性" in regime and (lo, hi) == (0.6, 0.8)


def test_position_parsing():
    cases = [
        ("META:210,GOOGL:1000", {"META": 210, "GOOGL": 1000}),
        ("", {}),
        ("META:abc,GOOGL:1000", {"GOOGL": 1000}),
        (" meta:210 , googl:1000 ", {"META": 210, "GOOGL": 1000}),
    ]
    for raw, want in cases:
        os.environ["CC_POSITIONS"] = raw
        assert cca.get_positions() == want, raw


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(tests)} tests passed.")
