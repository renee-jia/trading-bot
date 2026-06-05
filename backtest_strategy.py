#!/usr/bin/env python3
"""
Backtest: Strategy (score-based rebalancing) vs Buy-and-Hold.

Simulates a 1-year period where:
- Buy & Hold: Equal-weight portfolio bought on day 1, held for 1 year.
- Strategy: Monthly rebalancing based on the scoring engine. Overweight high-score
  stocks, underweight low-score stocks, exit "Avoid" stocks.

Usage:
    python backtest_strategy.py --tickers NVDA,AAPL,MSFT
"""
import argparse
import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

# Ensure imports work
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "core"))  # private algorithm package

import data_fetcher
import technical_analyzer
import trend_analyzer
import scorer
import strategy
from configs import ANALYZER_WEIGHTS

# Alpha score is shared with live trading / report via strategy.py
compute_alpha_score = strategy.compute_alpha_score


def score_stock_at_date(ticker, all_daily, benchmark_daily, end_idx):
    """
    Score a stock using data up to end_idx (simulating point-in-time).
    Uses at least 252 days of history.
    """
    min_history = 252
    start_idx = max(0, end_idx - 500)  # Use up to 2y of history
    if end_idx - start_idx < min_history:
        return None

    price_data = all_daily.iloc[start_idx:end_idx].copy()
    bench_data = benchmark_daily.iloc[start_idx:end_idx].copy() if benchmark_daily is not None else None

    if len(price_data) < min_history:
        return None

    tech_result = technical_analyzer.analyze(price_data)
    trend_result = trend_analyzer.analyze(price_data, bench_data)
    alpha_score = compute_alpha_score(price_data)

    # No sentiment in backtest (we don't have historical news)
    sent_result = {"score": 0.5, "confidence": 0.0, "num_articles": 0,
                   "num_bullish": 0, "num_bearish": 0, "headlines": []}

    score_result = scorer.score_stock(
        tech_result, sent_result, trend_result,
        alpha_score=alpha_score,
        weights=ANALYZER_WEIGHTS,
    )
    return score_result


def run_backtest(tickers, lookback_years=2, all_data=None, benchmark=None,
                 test_start=None, test_end=None, verbose=True):
    """
    Run backtest comparing strategy vs equal-weight buy-and-hold (and SPY).

    Args:
        tickers: ticker list.
        lookback_years: data period to fetch when all_data is not supplied.
        all_data: optional pre-fetched {ticker: DataFrame} (skips fetching, so a
                  multi-year sweep can fetch once and reuse).
        benchmark: optional pre-fetched SPY DataFrame.
        test_start, test_end: optional pd.Timestamp window. When given, the test
                  period is the common trading days within [test_start, test_end]
                  (history before it is still used for warmup/scoring). When
                  omitted, the test period is the last 252 common days.
        verbose: print full tables + rebalance log when True.

    Returns:
        summary dict (or None if insufficient data).
    """
    if all_data is None:
        print(f"\nFetching data for {len(tickers)} stocks...")
        all_data = {}
        for ticker in tickers:
            df = data_fetcher.fetch_price_data(ticker, period=f"{lookback_years}y")
            if df is not None and len(df) >= 400:
                all_data[ticker] = df
                print(f"  {ticker}: {len(df)} daily bars")
            else:
                print(f"  {ticker}: skipped (insufficient data)")
        benchmark = data_fetcher.fetch_benchmark(period=f"{lookback_years}y")

    if len(all_data) < 2:
        print("Not enough stocks with sufficient data.")
        return None

    valid_tickers = list(all_data.keys())
    if verbose:
        print(f"\n{len(valid_tickers)} stocks with sufficient data: {', '.join(valid_tickers)}")

    # Find common trading days across all stocks
    common_dates = all_data[valid_tickers[0]].index
    for t in valid_tickers[1:]:
        common_dates = common_dates.intersection(all_data[t].index)

    # Select the test window
    if test_start is not None or test_end is not None:
        mask = common_dates
        if test_start is not None:
            mask = mask[mask >= test_start]
        if test_end is not None:
            mask = mask[mask <= test_end]
        test_dates = mask
        if len(test_dates) < 20:
            print(f"Only {len(test_dates)} common trading days in window — skipping.")
            return None
    else:
        if len(common_dates) < 252:
            print(f"Only {len(common_dates)} common trading days, need at least 252.")
            return None
        test_dates = common_dates[-252:]

    start_date = test_dates[0]
    end_date = test_dates[-1]
    if verbose:
        print(f"\nBacktest period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        print(f"Trading days: {len(test_dates)}")

    initial_capital = 100000
    COST = strategy.COST_PER_SIDE  # per-side transaction cost (spread + slippage)

    # ---- BUY & HOLD ----
    # Equal weight on day 1, hold for 1 year. Pay a one-time entry cost so the
    # comparison with the (also cost-charged) strategy is fair.
    bh_weight = 1.0 / len(valid_tickers)
    bh_shares = {}
    bh_capital = initial_capital * (1 - COST)  # one-way buy from cash
    for t in valid_tickers:
        price_at_start = float(all_data[t].loc[start_date, "Close"])
        bh_shares[t] = (bh_capital * bh_weight) / price_at_start

    bh_values = []
    for date in test_dates:
        total = sum(bh_shares[t] * float(all_data[t].loc[date, "Close"]) for t in valid_tickers)
        bh_values.append(total)

    # ---- STRATEGY V6 (concentrated top-N by momentum) ----
    # Uses the SAME weight math as live trading (strategy.compute_target_weights),
    # so this backtest actually validates what the bot trades.
    #
    # Two correctness fixes vs the old version:
    #  - Execution lag: a rebalance is DECIDED on day i's close but EXECUTED at
    #    day i+1's close. The old code decided and executed on the same close
    #    (look-ahead), inflating returns.
    #  - Transaction costs: each rebalance pays cost on the traded notional.
    rebalance_interval = 14  # Biweekly
    strategy_shares = {}
    strategy_values = []
    rebalance_log = []
    pending_target = None  # weights (fractions) decided yesterday, executed today

    equal_weight = 1.0 / len(valid_tickers)
    current_weights = {t: equal_weight for t in valid_tickers}
    strat_capital = initial_capital * (1 - COST)  # one-way entry cost
    for t in valid_tickers:
        price = float(all_data[t].loc[start_date, "Close"])
        strategy_shares[t] = (strat_capital * equal_weight) / price

    for i, date in enumerate(test_dates):
        price_today = {t: float(all_data[t].loc[date, "Close"]) for t in valid_tickers}

        # 1) Execute any rebalance decided on the PREVIOUS bar, at today's close.
        if pending_target is not None:
            cur_val = sum(strategy_shares[t] * price_today[t] for t in valid_tickers)
            old_w = {t: (strategy_shares[t] * price_today[t] / cur_val) if cur_val > 0 else 0
                     for t in valid_tickers}
            cost = strategy.rebalance_cost(old_w, pending_target, cur_val, COST)
            cur_val -= cost
            for t in valid_tickers:
                alloc = cur_val * pending_target.get(t, 0)
                strategy_shares[t] = alloc / price_today[t] if price_today[t] > 0 else 0
            current_weights = pending_target
            pending_target = None

        # 2) Track value at today's close (after any execution).
        strategy_values.append(sum(strategy_shares[t] * price_today[t] for t in valid_tickers))

        # 3) On rebalance days, DECIDE using data through today; queue for tomorrow.
        if i > 0 and i % rebalance_interval == 0:
            current_value = strategy_values[-1]

            # Score + momentum AS OF today's close (end_idx = date_pos + 1 includes today)
            scores = {}
            mom_composite = {}
            for t in valid_tickers:
                date_pos = all_data[t].index.get_loc(date)
                result = score_stock_at_date(t, all_data[t], benchmark, date_pos + 1)
                scores[t] = result["score"] if result else 50
                m1 = m3 = 0.0
                if date_pos >= 21:
                    m1 = price_today[t] / float(all_data[t].iloc[date_pos - 21]["Close"]) - 1
                if date_pos >= 63:
                    m3 = price_today[t] / float(all_data[t].iloc[date_pos - 63]["Close"]) - 1
                mom_composite[t] = strategy.momentum_composite(m1, m3)

            # Canonical top-N weights (no macro cash in backtest → renormalize to
            # fully invested below; this isolates the selection/weighting alpha).
            stocks = [{"ticker": t, "score": scores[t], "mom": mom_composite[t]}
                      for t in valid_tickers]
            target_pct, _ = strategy.compute_target_weights(stocks, macro_score=None)
            target_weights = {t: target_pct.get(t, 0) / 100 for t in valid_tickers}

            # Gradual rebalancing toward target, then renormalize to 1.0
            actual_weights = {t: (strategy_shares[t] * price_today[t] / current_value)
                              if current_value > 0 else equal_weight for t in valid_tickers}
            blend_speed = 0.80
            new_weights = {t: max(0, actual_weights[t] + blend_speed * (target_weights[t] - actual_weights[t]))
                           for t in valid_tickers}
            total_new = sum(new_weights.values())
            if total_new > 0:
                new_weights = {t: v / total_new for t, v in new_weights.items()}

            pending_target = new_weights  # executed at next bar's close (T+1)

            rebalance_log.append({
                "date": date.strftime("%Y-%m-%d"),
                "portfolio_value": current_value,
                "scores": {t: scores[t] for t in valid_tickers},
                "weights": {t: round(new_weights[t] * 100, 1) for t in valid_tickers},
            })

    # ---- RESULTS ----
    bh_final = bh_values[-1]
    strat_final = strategy_values[-1]
    bh_return = (bh_final / initial_capital - 1) * 100
    strat_return = (strat_final / initial_capital - 1) * 100

    # Calculate metrics
    bh_daily_returns = np.diff(bh_values) / bh_values[:-1]
    strat_daily_returns = np.diff(strategy_values) / strategy_values[:-1]

    bh_sharpe = np.mean(bh_daily_returns) / np.std(bh_daily_returns) * np.sqrt(252) if np.std(bh_daily_returns) > 0 else 0
    strat_sharpe = np.mean(strat_daily_returns) / np.std(strat_daily_returns) * np.sqrt(252) if np.std(strat_daily_returns) > 0 else 0

    bh_max_dd = _max_drawdown(bh_values)
    strat_max_dd = _max_drawdown(strategy_values)

    bh_volatility = np.std(bh_daily_returns) * np.sqrt(252) * 100
    strat_volatility = np.std(strat_daily_returns) * np.sqrt(252) * 100

    # Per-stock buy & hold returns
    stock_returns = {}
    for t in valid_tickers:
        start_price = float(all_data[t].loc[start_date, "Close"])
        end_price = float(all_data[t].loc[end_date, "Close"])
        stock_returns[t] = (end_price / start_price - 1) * 100

    # SPY market return over the same window — the true market baseline. The
    # "Buy & Hold" column is an equal-weight basket of THESE tickers (a hand-
    # picked set), not the market, so SPY shows how much the basket itself is
    # just riding a strong tape vs the strategy's own edge over the basket.
    spy_return = None
    if benchmark is not None and "Close" in benchmark.columns:
        try:
            spy = benchmark["Close"].reindex(test_dates, method="ffill").dropna()
            if len(spy) >= 2:
                spy_return = (float(spy.iloc[-1]) / float(spy.iloc[0]) - 1) * 100
        except Exception:
            spy_return = None

    if verbose:
        print(f"\n{'='*70}")
        print(f"BACKTEST RESULTS")
        print(f"{'='*70}")
        print(f"Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')} ({len(test_dates)} trading days)")
        print(f"Stocks: {', '.join(valid_tickers)}")
        print(f"'Buy & Hold' = equal-weight basket of the stocks above (NOT the market)")
        print(f"Initial Capital: ${initial_capital:,.0f}")
        print(f"Rebalance: Every {rebalance_interval} trading days (biweekly), executed T+1")
        print(f"Transaction cost: {COST*1e4:.0f} bps/side (spread + slippage)")
        print(f"{'='*70}")
        print("NOTE: Universe is the CURRENT ticker list — results are subject to")
        print("survivorship bias (delisted/crashed names that would have been held")
        print("are absent). Treat the alpha as an upper bound, not a guarantee.")
        print(f"{'='*70}")

        print(f"\n{'Metric':<25} {'Strategy':>15} {'Buy & Hold':>15} {'Diff':>12}")
        print(f"{'-'*67}")
        print(f"{'Final Value':<25} ${strat_final:>14,.0f} ${bh_final:>14,.0f} ${strat_final-bh_final:>+11,.0f}")
        print(f"{'Total Return':<25} {strat_return:>14.2f}% {bh_return:>14.2f}% {strat_return-bh_return:>+11.2f}%")
        print(f"{'Sharpe Ratio':<25} {strat_sharpe:>15.2f} {bh_sharpe:>15.2f} {strat_sharpe-bh_sharpe:>+12.2f}")
        print(f"{'Max Drawdown':<25} {strat_max_dd:>14.2f}% {bh_max_dd:>14.2f}% {strat_max_dd-bh_max_dd:>+11.2f}%")
        print(f"{'Volatility (ann.)':<25} {strat_volatility:>14.2f}% {bh_volatility:>14.2f}% {strat_volatility-bh_volatility:>+11.2f}%")

        # Market benchmark — decompose the edge into basket-vs-market and strategy-vs-basket.
        if spy_return is not None:
            print(f"\n{'Market benchmark (SPY)':<25} {spy_return:>+14.2f}%")
            print(f"  Basket vs SPY:   {bh_return - spy_return:>+7.2f}%  "
                  f"(how much the hand-picked basket beat the market)")
            print(f"  Strategy vs SPY: {strat_return - spy_return:>+7.2f}%  "
                  f"(total) — of which {strat_return - bh_return:+.2f}% is the strategy's edge over the basket")

        print(f"\n{'='*70}")
        print(f"PER-STOCK BUY & HOLD RETURNS")
        print(f"{'='*70}")
        print(f"{'Ticker':<10} {'Return':>10} {'Start Price':>15} {'End Price':>15}")
        print(f"{'-'*50}")
        for t in sorted(stock_returns.keys(), key=lambda x: stock_returns[x], reverse=True):
            start_p = float(all_data[t].loc[start_date, "Close"])
            end_p = float(all_data[t].loc[end_date, "Close"])
            print(f"{t:<10} {stock_returns[t]:>+9.2f}% ${start_p:>14.2f} ${end_p:>14.2f}")

        if rebalance_log:
            print(f"\n{'='*70}")
            print(f"REBALANCE LOG ({len(rebalance_log)} rebalances)")
            print(f"{'='*70}")
            for entry in rebalance_log:
                print(f"\n  Date: {entry['date']} | Portfolio: ${entry['portfolio_value']:,.0f}")
                for t in valid_tickers:
                    score = entry['scores'].get(t, 50)
                    weight = entry['weights'].get(t, 0)
                    rec = "Strong Buy" if score >= 75 else "Buy" if score >= 60 else "Hold" if score >= 45 else "Reduce" if score >= 30 else "Avoid"
                    print(f"    {t:<8} Score: {score:>5.0f} ({rec:<12}) Weight: {weight:>5.1f}%")

        winner = "STRATEGY" if strat_return > bh_return else "BUY & HOLD"
        margin = abs(strat_return - bh_return)
        print(f"\n{'='*70}")
        print(f"WINNER: {winner} (by {margin:.2f}%)")
        print(f"{'='*70}")

    return {
        "start": start_date, "end": end_date, "days": len(test_dates),
        "strategy_return": strat_return, "bh_return": bh_return, "spy_return": spy_return,
        "strategy_sharpe": strat_sharpe, "bh_sharpe": bh_sharpe,
        "strategy_max_dd": strat_max_dd, "bh_max_dd": bh_max_dd,
        "strategy_vol": strat_volatility, "bh_vol": bh_volatility,
    }


def _max_drawdown(values):
    """Calculate maximum drawdown percentage."""
    peak = values[0]
    max_dd = 0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return max_dd


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest strategy vs buy-and-hold")
    parser.add_argument("--tickers", type=str, required=True,
                        help="Comma-separated ticker symbols")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",")]
    run_backtest(tickers)
