"""Offline tests for the Alpaca-backed option chain adapter. No network."""
import os
import sys
from datetime import date, datetime, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import covered_call_advisor as cca
import option_data as od
import sell_put_advisor as spa

TODAY = date(2026, 9, 9)
EXP = date(2026, 10, 16)


def _contract(symbol, strike, kind, exp=EXP, oi=500, size="100", root="ABC"):
    return SimpleNamespace(symbol=symbol, strike_price=strike, type=SimpleNamespace(value=kind),
                           expiration_date=exp, size=size, root_symbol=root, open_interest=str(oi))


def _snap(bid, ask, iv, delta, ts=datetime(2026, 9, 9, 19, 59, tzinfo=timezone.utc)):
    return SimpleNamespace(latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask, timestamp=ts),
                           latest_trade=SimpleNamespace(price=(bid + ask) / 2),
                           implied_volatility=iv, greeks=SimpleNamespace(delta=delta))


def _clients(contracts, snaps, fail=False):
    class Trading:
        def get_option_contracts(self, req):
            if fail:
                raise RuntimeError("alpaca down")
            return SimpleNamespace(option_contracts=contracts, next_page_token=None)
    class Options:
        calls = []
        def get_option_snapshot(self, req):
            self.calls.append(list(req.symbol_or_symbols))
            return {s: snaps[s] for s in req.symbol_or_symbols if s in snaps}
    return Trading(), Options()


def _fake_yf(spot=100.0):
    idx = pd.bdate_range(end=pd.Timestamp(TODAY), periods=260)
    closes = np.linspace(80, spot, 260)
    return SimpleNamespace(
        history=lambda **kw: pd.DataFrame({"Close": closes}, index=idx),
        options=("2026-10-16",),
        option_chain=lambda exp: SimpleNamespace(calls=pd.DataFrame(), puts=pd.DataFrame(), source="yfinance"),
        calendar={"Earnings Date": [date(2026, 10, 28)]},
        get_info=lambda: {"quoteType": "EQUITY"},
    )


def _chain_fixture():
    contracts, snaps = [], {}
    for k in range(80, 121):
        for kind in ("put", "call"):
            sym = f"ABC261016{'P' if kind == 'put' else 'C'}{k:05d}000"
            contracts.append(_contract(sym, float(k), kind))
            if k in (80, 120):
                snaps[sym] = _snap(0.0, 0.05, None, None)      # deep OTM: no IV, no bid
            else:
                call_delta = 0.5 - (k - 100) / 40                # 1.0 at 80 → 0.0 at 120
                snaps[sym] = _snap(1.0 + abs(100 - k) / 10, 1.2 + abs(100 - k) / 10, 0.30 + (100 - k) / 1000,
                                   call_delta if kind == "call" else call_delta - 1)
    contracts.append(_contract("ABC1261016P00100000", 100.0, "put", size="100", root="ABC1"))  # adjusted
    snaps["ABC1261016P00100000"] = _snap(1.0, 1.1, 0.3, -0.5)
    contracts.append(_contract("ABC261120P00100000", 100.0, "put", exp=date(2026, 11, 20)))
    return contracts, snaps


def test_source_selection_respects_keys_and_override():
    saved = {k: os.environ.pop(k, None) for k in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY", "OPTION_DATA_SOURCE")}
    try:
        assert od.source() == "yfinance"
        os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"] = "k", "s"
        assert od.source() == "alpaca"
        os.environ["OPTION_DATA_SOURCE"] = "yfinance"
        assert od.source() == "yfinance"
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


def test_alpaca_chain_has_yfinance_shape_and_fills_gaps():
    contracts, snaps = _chain_fixture()
    trading, options = _clients(contracts, snaps)
    t = od.AlpacaOptionTicker("abc", today=TODAY, yf_ticker=_fake_yf(), trading=trading, options=options)
    assert t.options == ("2026-10-16", "2026-11-20")
    chain = t.option_chain("2026-10-16")
    assert chain.source == "alpaca" and chain.quote_time is not None
    puts, calls = chain.puts, chain.calls
    assert list(puts.strike) == sorted(puts.strike)
    assert set(od.CHAIN_COLUMNS) <= set(puts.columns)
    assert len(puts) == 42 and len(calls) == 41          # adjusted contract kept but flagged
    assert (puts.loc[puts.contractSymbol.str.startswith("ABC1"), "contractSize"] == "NONSTANDARD").all()
    assert puts.impliedVolatility.notna().all()          # deep-OTM IV interpolated
    assert float(puts.loc[puts.strike == 80, "bid"].iloc[0]) == 0.0
    assert od.atm_iv(puts, 100.6) == float(puts.loc[puts.strike == 101, "impliedVolatility"].iloc[0])
    assert "Alpaca" in od.quote_note(chain)
    assert sum(len(c) for c in options.calls) == len(puts) + len(calls)


def test_alpaca_failure_falls_back_to_yahoo_chain():
    contracts, snaps = _chain_fixture()
    trading, options = _clients(contracts, snaps, fail=True)
    t = od.AlpacaOptionTicker("ABC", today=TODAY, yf_ticker=_fake_yf(), trading=trading, options=options)
    assert t.options == ("2026-10-16",)
    assert t.source == "yfinance" and "alpaca down" in t.last_error
    assert t.option_chain("2026-10-16").source == "yfinance"


def test_history_passthrough_and_atm_fallback():
    t = od.AlpacaOptionTicker("ABC", today=TODAY, yf_ticker=_fake_yf(), trading=object(), options=object())
    assert len(t.history(period="1y")) == 260
    assert od.atm_iv(pd.DataFrame(columns=["strike", "impliedVolatility"]), 100, fallback=0.25) == 0.25
    assert od.atm_iv(pd.DataFrame({"strike": [100.0], "impliedVolatility": [np.nan]}), 100, fallback=0.25) == 0.25


def test_quoted_delta_wins_and_bs_fills_missing():
    frame = pd.DataFrame({"strike": [90.0, 95.0, 100.0], "impliedVolatility": [0.3, 0.3, 0.3],
                          "delta": [-0.2, np.nan, 5.0]})
    out = cca._delta_or_bs(frame, 100.0, 40 / 365, 0.04, 0.3, put=True)
    assert out[0] == 0.2
    assert 0.0 < out[1] < 1.0 and 0.0 < out[2] < 1.0
    plain = pd.DataFrame({"strike": [110.0], "impliedVolatility": [0.3]})
    assert 0.0 < cca._delta_or_bs(plain, 100.0, 40 / 365, 0.04, 0.3)[0] < 0.5


def test_put_ladder_uses_adapter_and_flags_missing_chain():
    contracts, snaps = _chain_fixture()
    trading, options = _clients(contracts, snaps)
    t = od.AlpacaOptionTicker("ABC", today=TODAY, yf_ticker=_fake_yf(), trading=trading, options=options)
    r = spa.analyze_put_ladder("ABC", 0.04, t=t, today=TODAY)
    assert r["status"] == "ok" and r["expiry"] == "2026-10-16"
    assert [l["strike"] for l in r["ladder"]] == [92.0, 90.0, 88.0]   # 0.30 / 0.25 / 0.20Δ from quoted greeks
    assert [round(l["delta"], 2) for l in r["ladder"]] == [0.3, 0.25, 0.2]
    assert "Alpaca" in r["quote_note"]
    empty = od.AlpacaOptionTicker("ABC", today=TODAY, yf_ticker=_fake_yf(),
                                  trading=_clients([], {})[0], options=options)
    empty_yf = _fake_yf(); empty_yf.options = ()
    empty._yf = empty_yf
    r = spa.analyze_put_ladder("ABC", 0.04, t=empty, today=TODAY)
    assert r["status"] == "no_chain" and r["ladder"] == []


def test_covered_call_ladders_section_and_no_chain_text():
    contracts, snaps = _chain_fixture()
    trading, options = _clients(contracts, snaps)
    def analyzer(ticker, shares, rate, today=None):
        t = od.AlpacaOptionTicker(ticker, today=TODAY, yf_ticker=_fake_yf(), trading=trading, options=options)
        return cca.analyze_ticker(ticker, shares, rate, t=t, today=TODAY)
    md = cca.build_covered_call_ladders({"ABC": 300}, rate=0.04, analyzer=analyzer, today=TODAY)
    assert md.startswith("## Covered Call Advisor")
    assert "### ABC\n" in md and "300" not in md and "可卖" not in md and "张）" not in md
    assert "覆盖比例 **约" in md and "持仓" not in md and "持有" not in md
    assert "推荐到期日：2026-10-16" in md and "| 行权价 |" in md and "Alpaca" in md
    assert cca.build_covered_call_ladders({}, rate=0.04) == ""
    r = {"ticker": "ABC", "spot": 100.0, "rv21": .3, "rv_trimmed": .28,
         "earnings": None, "expiry": None, "dte": None, "status": "no_chain", "regime": "中性震荡",
         "cover_frac_lo": .6, "cover_frac_hi": .8, "ladder": [], "atm_iv": None, "exp_move": None, "quote_note": None}
    assert "期权链不可用" in cca._format_ticker_section(r)


def test_sell_put_radar_lists_candidates_with_ladders():
    contracts, snaps = _chain_fixture()
    trading, options = _clients(contracts, snaps)
    def analyzer(ticker, rate, today=None):
        t = od.AlpacaOptionTicker(ticker, today=TODAY, yf_ticker=_fake_yf(), trading=trading, options=options)
        return spa.analyze_put_ladder(ticker, rate, t=t, today=TODAY)
    rows = [{"ticker": "ABC", "name": "Abc", "indicators": {"change_1d": -6.0, "change_5d": -8.0, "rsi": 30},
             "score_result": {"score": 70, "recommendation": "Buy"}},
            {"ticker": "HELD", "name": "Held", "indicators": {"change_1d": -6.0, "change_5d": -8.0, "rsi": 30},
             "score_result": {"score": 70, "recommendation": "Buy"}}]
    md = spa.build_sell_put_radar(rows, exclude={"HELD"}, rate=0.04, analyzer=analyzer, today=TODAY)
    assert md.startswith("## Sell Put 雷达") and "### ABC" in md and "推荐到期日：2026-10-16" in md
    assert "HELD" in md and "### HELD" not in md
    quiet = spa.build_sell_put_radar([], exclude=set(), rate=0.04, analyzer=analyzer)
    assert "无 sell put 候选" in quiet


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
