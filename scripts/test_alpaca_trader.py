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
