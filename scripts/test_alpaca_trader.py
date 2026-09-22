"""Offline unit tests for alpaca_trader cash/buy guards.

Run: python scripts/test_alpaca_trader.py
Does not talk to Alpaca.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import alpaca_trader as at


def test_negative_cash_blocks_all_buys():
    # 2026-07-10 paper book: cash -$1,165, buying_power still large via margin
    budget = at.buy_budget(cash=-1165, buying_power=200000, equity=139829, cash_pct=0.10)
    assert budget == 0


def test_reserve_is_not_spendable():
    # $20k cash, 10% reserve on $100k equity → only $10k of cash is buyable
    budget = at.buy_budget(cash=20000, buying_power=200000, equity=100000, cash_pct=0.10)
    assert budget == 10000


def test_margin_buying_power_is_ignored():
    budget = at.buy_budget(cash=8000, buying_power=160000, equity=100000, cash_pct=0.0)
    assert budget == 8000


def test_clip_drops_buys_when_budget_is_zero():
    trades = [
        {"side": "sell", "ticker": "MU", "qty": 10, "dollar_amount": 5000},
        {"side": "buy", "ticker": "KLAC", "qty": 20, "dollar_amount": 4000},
    ]
    out, clipped = at.clip_buys_to_budget(trades, 0)
    assert clipped is True
    assert [t["ticker"] for t in out] == ["MU"]


def test_clip_scales_buys_to_fit():
    trades = [
        {"side": "buy", "ticker": "A", "qty": 10, "dollar_amount": 1000},
        {"side": "buy", "ticker": "B", "qty": 10, "dollar_amount": 1000},
    ]
    out, clipped = at.clip_buys_to_budget(trades, 1000)
    assert clipped is True
    assert all(t["qty"] < 10 for t in out)
    assert sum(t["dollar_amount"] for t in out) <= 1000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(tests)} tests passed.")


# --- Turnover controls (added 2026-09-20; see docs/STRATEGY.md) ---

def _book(equity=100000.0):
    """A book sitting exactly on a 10-name target, priced at $100/share."""
    target = {f"T{i}": 10.0 for i in range(10)}
    pos = {t: {"qty": 100.0, "market_value": equity * 0.10, "current_price": 100.0}
           for t in target}
    return target, pos, equity


def test_band_ignores_drift_inside_tolerance():
    """A 1%-of-equity drift is inside the 2% band — no order."""
    target, pos, eq = _book()
    pos["T0"]["market_value"] = eq * 0.11  # 1% of equity above target
    trades = at.calculate_trades(target, pos, eq, 0.0, blend_speed=0.20, band_pct=0.02)
    assert [t for t in trades if t["ticker"] == "T0"] == []


def test_band_lets_a_real_deviation_through():
    """A 3%-of-equity drift is outside the band — order is generated."""
    target, pos, eq = _book()
    pos["T0"]["market_value"] = eq * 0.13
    pos["T0"]["qty"] = 130.0
    trades = at.calculate_trades(target, pos, eq, 0.0, blend_speed=0.20, band_pct=0.02)
    t0 = [t for t in trades if t["ticker"] == "T0"]
    assert len(t0) == 1 and t0[0]["side"] == "sell"


def test_full_exit_bypasses_the_band():
    """A dropped name is sold in full even though the band would mute it."""
    target, pos, eq = _book()
    del target["T9"]                      # dropped from the portfolio
    pos["T9"]["market_value"] = eq * 0.005  # well inside the 2% band
    pos["T9"]["qty"] = 5.0
    trades = at.calculate_trades(target, pos, eq, 0.0, blend_speed=0.20, band_pct=0.02)
    t9 = [t for t in trades if t["ticker"] == "T9"]
    assert len(t9) == 1 and t9[0]["side"] == "sell" and t9[0]["qty"] == 5


def test_lower_blend_speed_trades_less_notional():
    """blend 0.20 must move strictly less notional than the old 0.80."""
    target, pos, eq = _book()
    pos["T0"]["market_value"] = eq * 0.20   # 10% of equity overweight
    pos["T0"]["qty"] = 200.0
    slow = at.calculate_trades(target, pos, eq, 0.0, blend_speed=0.20, band_pct=0.02)
    fast = at.calculate_trades(target, pos, eq, 0.0, blend_speed=0.80, band_pct=0.02)
    n_slow = sum(t["dollar_amount"] for t in slow)
    n_fast = sum(t["dollar_amount"] for t in fast)
    assert 0 < n_slow < n_fast


def test_aligned_book_generates_no_trades():
    """On-target book: the band must suppress everything."""
    target, pos, eq = _book()
    assert at.calculate_trades(target, pos, eq, 0.0,
                               blend_speed=0.20, band_pct=0.02) == []
