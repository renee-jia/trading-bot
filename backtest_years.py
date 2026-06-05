#!/usr/bin/env python3
"""
Multi-year sweep: run the V6 strategy vs equal-weight basket (Buy & Hold) vs SPY
for each calendar year. Fetches price data once and reuses it across years.

Usage:
    python backtest_years.py
    python backtest_years.py --tickers AAPL,MSFT,NVDA --years 2022,2023,2024
"""
import argparse
import sys
import os

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "core"))  # private algorithm package

import data_fetcher
import backtest_strategy as bt

DEFAULT_TICKERS = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META",
                   "AVGO", "TSLA", "AMD", "CRM", "ORCL", "ADBE"]


def _strip_tz(df):
    if df is not None and getattr(df.index, "tz", None) is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    return df


def main():
    ap = argparse.ArgumentParser(description="Per-year strategy vs basket vs SPY")
    ap.add_argument("--tickers", type=str, default=None)
    ap.add_argument("--years", type=str, default="2022,2023,2024,2025,2026")
    args = ap.parse_args()

    tickers = ([t.strip().upper() for t in args.tickers.split(",")]
               if args.tickers else DEFAULT_TICKERS)
    years = [int(y) for y in args.years.split(",")]

    print(f"Fetching 10y data for {len(tickers)} tickers (once)...")
    all_data = {}
    for t in tickers:
        df = _strip_tz(data_fetcher.fetch_price_data(t, period="10y"))
        if df is not None and len(df) >= 400:
            all_data[t] = df
            print(f"  {t}: {len(df)} bars ({df.index[0].date()} → {df.index[-1].date()})")
        else:
            print(f"  {t}: skipped")
    benchmark = _strip_tz(data_fetcher.fetch_benchmark(period="10y"))

    rows = []
    for y in years:
        res = bt.run_backtest(
            list(all_data.keys()), all_data=all_data, benchmark=benchmark,
            test_start=pd.Timestamp(y, 1, 1), test_end=pd.Timestamp(y, 12, 31),
            verbose=False,
        )
        if res is None:
            print(f"\n{y}: insufficient data — skipped")
            continue
        rows.append((y, res))
        print(f"  ...{y} done ({res['start'].date()} → {res['end'].date()}, {res['days']}d)")

    # Summary table
    print("\n" + "=" * 86)
    print("PER-YEAR: Strategy vs Buy&Hold (equal-wt basket) vs SPY (market)")
    print("=" * 86)
    print(f"{'Year':<6} {'Days':>5} {'Strategy':>10} {'Basket(B&H)':>12} {'SPY':>9} "
          f"{'Strat-SPY':>10} {'Strat-Basket':>13}")
    print("-" * 86)
    for y, r in rows:
        spy = r["spy_return"]
        spy_s = f"{spy:>+8.1f}%" if spy is not None else "    n/a"
        strat_spy = f"{r['strategy_return']-spy:>+9.1f}%" if spy is not None else "      n/a"
        print(f"{y:<6} {r['days']:>5} {r['strategy_return']:>+9.1f}% "
              f"{r['bh_return']:>+11.1f}% {spy_s} {strat_spy} "
              f"{r['strategy_return']-r['bh_return']:>+12.1f}%")
    print("-" * 86)
    print("Strat-SPY = strategy's total excess over the market.")
    print("Strat-Basket = the strategy's edge over an equal-weight hold of the SAME names")
    print("               (the rest of Strat-SPY is just the hand-picked basket beating SPY).")
    print("NOTE: survivorship-biased — universe is today's survivors held back through time.")


if __name__ == "__main__":
    main()
