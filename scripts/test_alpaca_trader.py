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
    pos["T0"]["market_value"] = eq * 0.20   # 10% of equity overweight,
    pos["T0"]["qty"] = 200.0                # funded by the other nine
    for i in range(1, 10):
        pos[f"T{i}"]["market_value"] = eq * 0.80 / 9
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


# --- Rotation regressions (2026-10-05 first V7 rebalance) ---

def _rotation_book():
    """The 2026-10-05 inputs, straight from the prod log."""
    eq = 125257.49
    target = {"DELL": 17.6, "TEAM": 15.8, "MU": 14.0, "ALAB": 12.2, "SNDK": 10.4,
              "HPE": 8.7, "AMD": 6.8, "CRWD": 5.0, "OKTA": 3.2, "SOXL": 1.3}
    held = {"TEAM": (112, 21780.64), "OKTA": (92, 19867.86), "WIX": (247, 18142.15),
            "WDAY": (65, 12134.20), "TEM": (141, 11139.0), "CRM": (44, 10202.5),
            "NET": (12, 4273.02), "MSTR": (21, 3475.08), "PLTR": (15, 2830.2),
            "SMCI": (63, 2733.57)}
    pos = {t: {"qty": q, "market_value": v, "current_price": v / q}
           for t, (q, v) in held.items()}
    for t, px in {"DELL": 575, "MU": 1220, "ALAB": 354, "SNDK": 150, "HPE": 68.5,
                  "AMD": 710, "CRWD": 289, "SOXL": 40}.items():
        pos[t] = {"qty": 0, "market_value": 0, "current_price": px}
    return target, pos, eq


def test_rotation_never_buys_an_overweight_name():
    """OKTA held at 15.9% vs a 3.2% target was BOUGHT on 2026-10-05."""
    target, pos, eq = _rotation_book()
    trades = at.calculate_trades(target, pos, eq, 0.05)
    okta = [t for t in trades if t["ticker"] == "OKTA"]
    assert len(okta) == 1 and okta[0]["side"] == "sell"


def test_rotation_redeploys_exit_proceeds():
    """Cash freed by the 8 exits funds the new names, not idle cash."""
    target, pos, eq = _rotation_book()
    cw = {t: p["market_value"] / eq * 100 for t, p in pos.items()}
    plan = at.plan_weights(cw, target, 95.0)
    assert abs(sum(plan.values()) - 95.0) < 1e-6
    for t, tw in target.items():
        assert plan[t] <= max(tw, cw[t]) + 1e-9, t   # never past target
    # OKTA is still only trimmed 20%, so the new names share the rest pro rata
    # — well past the old 20%-of-gap fill (DELL got 7.3% of a 17.6% target).
    for t in ("DELL", "MU", "ALAB", "SNDK", "HPE", "AMD", "CRWD"):
        assert plan[t] >= 0.8 * target[t], (t, plan[t])


def test_idle_cash_flows_only_to_underweight_names():
    """The post-10/05 book: 59% cash, OKTA overweight — trim OKTA, buy the rest."""
    eq = 100000.0
    target = {"A": 30.0, "B": 30.0, "C": 5.0, "D": 30.0}
    pos = {"A": {"qty": 10, "market_value": 2000, "current_price": 200},
           "B": {"qty": 10, "market_value": 2000, "current_price": 200},
           "C": {"qty": 100, "market_value": 20000, "current_price": 200},
           "D": {"qty": 0, "market_value": 0, "current_price": 200}}
    trades = {t["ticker"]: t for t in at.calculate_trades(target, pos, eq, 0.05)}
    assert trades["C"]["side"] == "sell"
    assert all(trades[t]["side"] == "buy" for t in "ABD")
    bought = sum(trades[t]["dollar_amount"] for t in "ABD")
    assert bought > 0.8 * (eq * 0.95 - 24000)


def test_plan_scales_down_when_over_invested():
    """Raising the cash reserve trims the movable names; sum = investable."""
    cw = {"A": 50.0, "B": 50.0}
    plan = at.plan_weights(cw, {"A": 50.0, "B": 50.0}, 80.0, band_pct=0.0)
    assert abs(sum(plan.values()) - 80.0) < 1e-6


class _FakeOrder:
    def __init__(self, oid, status="filled"):
        import types
        self.id = oid
        self.status = types.SimpleNamespace(value=status)


class _FakeClient:
    """Fills sells instantly; cash only rises once the sells are submitted."""
    def __init__(self, cash, positions):
        import types
        self.cash = cash
        self.positions = positions
        self.submitted = []
        self._types = types

    def get_account(self):
        return self._types.SimpleNamespace(equity="100000", cash=str(self.cash),
                                           buying_power=str(4 * 100000))

    def get_orders(self, filter=None):
        return []

    def get_clock(self):
        return self._types.SimpleNamespace(is_open=True)

    def get_all_positions(self):
        return [self._types.SimpleNamespace(symbol=t, qty=str(q), market_value=str(v),
                                            current_price=str(v / q))
                for t, (q, v) in self.positions.items()]

    def submit_order(self, req):
        side = str(getattr(req.side, "value", req.side)).lower()
        px = 100.0
        if side == "sell":
            self.cash += req.qty * px
        else:
            self.cash -= req.qty * px
        self.submitted.append((side, req.symbol, req.qty))
        return _FakeOrder(f"o{len(self.submitted)}")

    def get_order_by_id(self, oid):
        return _FakeOrder(oid)


def test_buys_are_funded_by_same_run_sells():
    """2026-10-05: $12k pre-sell budget vs $54k demand. Post-sell cash must count."""
    client = _FakeClient(cash=5000, positions={"OLD": (950, 95000.0)})
    saved = (at.get_alpaca_client, at.compute_target_weights,
             at._calendar_days_since_last_fill, at._fetch_latest_prices,
             at._verify_trades)
    at.get_alpaca_client = lambda paper=True: client
    at.compute_target_weights = lambda *a, **k: ({"NEW": 95.0}, 0.05)
    at._calendar_days_since_last_fill = lambda c: None
    at._fetch_latest_prices = lambda tickers: {t: 100.0 for t in tickers}
    at._verify_trades = lambda *a, **k: None
    try:
        at.run_trading([{}, {}], paper=True)
    finally:
        (at.get_alpaca_client, at.compute_target_weights,
         at._calendar_days_since_last_fill, at._fetch_latest_prices,
         at._verify_trades) = saved
    sides = [s for s, _, _ in client.submitted]
    assert sides[0] == "sell" and sides[-1] == "buy"
    new_qty = sum(q for s, sym, q in client.submitted if sym == "NEW")
    assert new_qty >= 900, new_qty      # ~95% of equity, not 0% of pre-sell cash


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(tests)} tests passed.")
