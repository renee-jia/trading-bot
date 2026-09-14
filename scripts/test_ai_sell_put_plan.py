"""Offline tests for the AI 标的 Sell Put 方案 desk. No network."""
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))

import ai_sell_put_plan as ap

TODAY = date(2026, 9, 8)
OPEN = {"status": "open", "label": "可写权利金", "reason": ""}


def _row(ticker, score=70, d1=-2.0, d5=-3.0, d1m=2.0, rsi=48, from_high=-9.0, rec="Buy"):
    return {
        "ticker": ticker, "name": ticker,
        "indicators": {"change_1d": d1, "change_5d": d5, "change_1m": d1m,
                       "rsi": rsi, "pct_from_52w_high": from_high, "price": 100.0,
                       "sma_50": 95.0},
        "score_result": {"score": score, "recommendation": rec},
    }


def _ladder(strikes=(95, 92, 90)):
    out = []
    for k, dlt in zip(strikes, (0.30, 0.25, 0.20)):
        mid = round(k * 0.02, 2)
        out.append({"strike": float(k), "below_support": k < 91, "delta": dlt,
                    "otm_pct": (1 - k / 100) * 100, "bid": mid - 0.05, "ask": mid + 0.05,
                    "mid": mid, "breakeven": k - mid, "be_discount": (1 - (k - mid) / 100) * 100,
                    "cash": k * 100, "yield_pct": mid / k * 100,
                    "ann_pct": mid / k * 100 * 365 / 38, "oi": 500})
    return out


def _snapshot(spot=100.0, status="ok", ladder=None, atm_iv=0.42, rv=0.30,
              sma20=102.0, sma50=95.0, sma200=85.0, low21=91.0):
    return {
        "ticker": "X", "spot": spot, "pct_from_high": -9.0, "low_21d": low21,
        "rv_trimmed": rv, "earnings": TODAY + timedelta(days=70),
        "expiry": "2026-10-16", "dte": 38, "status": status,
        "ladder": _ladder() if ladder is None else ladder,
        "atm_iv": atm_iv, "exp_move": 12.0,
        "levels": {"sma_20": sma20, "sma_50": sma50, "sma_200": sma200, "low_21d": low21},
    }


def _plan(rows, snaps, held=None, today=TODAY):
    def analyzer(ticker, rate, today=None):
        s = snaps[ticker]
        if isinstance(s, Exception):
            raise s
        return s
    return ap.build_plan(rows, held=held or set(), today=today,
                         tickers=[r["ticker"] for r in rows], analyzer=analyzer, rate=0.04)


def test_quality_dip_is_entry_yes_and_add_today():
    plan = _plan([_row("AVGO")], {"AVGO": _snapshot()})
    p = plan["names"][0]
    assert p["entry"] == "yes"
    assert p["today"] == "add_now"
    assert p["pick"]["strike"] == 92.0          # ~0.25Δ rung for a full entry
    assert p["expiry"] == "2026-10-16"
    lv = p["buy_levels"]
    assert lv["l1"] == 97.0                     # SMA20 above spot → spot × 0.97
    assert lv["l2"] == 95.0                     # SMA50
    assert lv["l3"] == 90.0                     # lowest strike below the 21d low


def test_broken_trend_or_weak_score_never_sells_puts():
    plan = _plan([_row("MU", score=72), _row("PLTR", score=45)],
                 {"MU": _snapshot(sma200=110.0), "PLTR": _snapshot()})
    by = {p["ticker"]: p for p in plan["names"]}
    assert by["MU"]["entry"] == "no" and "200" in by["MU"]["entry_note"]
    assert by["PLTR"]["entry"] == "no"
    assert by["MU"]["today"] == "no_add" and by["PLTR"]["today"] == "no_add"
    assert by["MU"]["pick"] is None and by["PLTR"]["pick"] is None
    assert "| — 不卖 |" in ap.build_section(plan)


def test_chase_day_is_not_an_add_day():
    plan = _plan([_row("NVDA", d1=4.5, d5=9.0, rsi=74, from_high=-0.5)], {"NVDA": _snapshot()})
    p = plan["names"][0]
    assert p["today"] == "no_chase"
    assert p["entry"] == "yes"                  # entry via put still fine; timing is the issue


def test_held_name_keeps_chain_but_labels_put_as_add():
    plan = _plan([_row("MSFT")], {"MSFT": _snapshot()}, held={"MSFT"})
    p = plan["names"][0]
    assert p["held"] and p["entry"] == "held"
    assert p["pick"] is not None


def test_earnings_wait_and_thin_premium():
    plan = _plan([_row("ANET"), _row("VRT")],
                 {"ANET": _snapshot(status="wait", ladder=[]),
                  "VRT": _snapshot(atm_iv=0.25, rv=0.30)})
    by = {p["ticker"]: p for p in plan["names"]}
    assert by["ANET"]["entry"] == "wait_earnings" and by["ANET"]["pick"] is None
    assert by["VRT"]["entry"] == "thin"
    assert by["VRT"]["pick"]["strike"] == 90.0  # lowest rung only


def test_event_blackout_blocks_new_puts_but_not_stock_add():
    fomc = date(2026, 9, 16)
    plan = _plan([_row("QCOM")], {"QCOM": _snapshot()}, today=fomc - timedelta(days=1))
    p = plan["names"][0]
    assert p["entry"] == "wait_event"
    assert p["today"] == "add_now"


def test_missing_row_and_fetch_error_are_reported_not_fatal():
    plan = _plan([_row("AMD")], {"AMD": RuntimeError("boom"), "LRCX": _snapshot()})
    # LRCX is not in ranked → no score; AMD analyzer raised
    plan2 = ap.build_plan([_row("AMD")], held=set(), today=TODAY, tickers=["AMD", "LRCX"],
                          analyzer=lambda t, r, today=None: _snapshot() if t == "LRCX" else (_ for _ in ()).throw(RuntimeError("boom")),
                          rate=0.04)
    by = {p["ticker"]: p for p in plan2["names"]}
    assert by["AMD"]["entry"] == "no_data" and by["AMD"]["error"].startswith("RuntimeError")
    assert by["LRCX"]["today"] == "no_data" and by["LRCX"]["score"] is None
    md = ap.build_section(plan2)
    assert "数据获取失败" in md and "LRCX" in md
    assert plan["names"][0]["entry"] == "no_data"


def test_section_and_email_render_all_names():
    plan = _plan([_row("AVGO"), _row("MSFT", score=55, d1=0.3, rsi=63, from_high=-2.0)],
                 {"AVGO": _snapshot(), "MSFT": _snapshot()})
    md = ap.build_section(plan)
    assert md.startswith("## AI 标的 Sell Put 方案")
    assert "持仓" not in md and "持有" not in md
    assert "| **AVGO** |" in md and "| **MSFT** |" in md
    assert "2026-10-16（38d）卖 $92P @ **$1.84**" in md
    assert "### AVGO（AVGO）" in md and "首选：**$92 put" in md
    for line in md.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            assert line.count("|") in (11, 13)  # summary (10 cols) / ladder (12 cols)
    lines = ap.email_lines(plan)
    assert lines[0] == "=== AI SELL PUT 方案 ==="
    assert any(l.strip().startswith("AVGO") and "$92P" in l for l in lines)
    assert any(l.strip().startswith("MSFT") and "可小量" in l for l in lines)


def test_watch_list_env_override_and_default_includes_required_names():
    os.environ["AI_PUT_WATCH"] = " mu, avgo ,MU"
    try:
        assert ap.watch_list() == ["MU", "AVGO"]
    finally:
        del os.environ["AI_PUT_WATCH"]
    for t in ("AVGO", "MSFT", "MU", "QCOM", "MRVL", "ASML", "LRCX", "CDNS", "VRT", "ANET", "PLTR"):
        assert t in ap.watch_list()


def test_analyze_name_with_fake_yfinance_builds_ladder_and_levels():
    idx = pd.bdate_range(end=pd.Timestamp(TODAY), periods=260)
    closes = np.linspace(80, 100, 260) + np.sin(np.arange(260) / 5)
    hist = pd.DataFrame({"Close": closes}, index=idx)
    spot = float(closes[-1])
    strikes = np.arange(60, 120, 1.0)
    puts = pd.DataFrame({
        "strike": strikes,
        "bid": np.clip((strikes - spot) * 0.5 + 3, 0.05, None) / 3,
        "ask": np.clip((strikes - spot) * 0.5 + 3, 0.05, None) / 3 + 0.1,
        "impliedVolatility": 0.40, "openInterest": 300, "volume": 50,
    })
    chain = SimpleNamespace(puts=puts, calls=puts.copy())
    fake = SimpleNamespace(
        history=lambda period: hist,
        options=["2026-09-25", "2026-10-16", "2026-11-20"],
        option_chain=lambda exp: chain,
        calendar={"Earnings Date": [TODAY + timedelta(days=80)]},
    )
    r = ap.analyze_name("FAKE", 0.04, today=TODAY, ticker_factory=lambda s: fake)
    assert r["expiry"] == "2026-10-16" and r["status"] == "ok"
    assert len(r["ladder"]) == 3 and all(l["strike"] < spot for l in r["ladder"])
    assert r["levels"]["sma_200"] is not None and r["levels"]["low_21d"] <= spot


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
