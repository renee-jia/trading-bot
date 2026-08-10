"""
Alpaca paper/live trading module.

Executes the concentrated top-10 momentum strategy:
- Scores all stocks, selects top 10 by score
- Weights: 95% momentum rank + 5% score^2
- Rank buffer: held names keep their slot until they fall out of the top 15
  by momentum; new names only enter at top 10 (cuts boundary churn)
- Macro-based cash reserve (0-20% — ~80%+ always invested)
- Gradual rebalancing (80% blend toward target); zero-target positions are
  exited in full so whole-share rounding can't strand remnants
- Min trade size 0.3% of equity (floor $50)
- Examines the account and rebalances every run

Usage:
    # As part of daily report:
    python daily_report.py --trade

    # Standalone:
    python alpaca_trader.py                    # Paper trading (default)
    python alpaca_trader.py --live             # Live trading (requires confirmation)
    python alpaca_trader.py --dry-run          # Show trades without executing

Live-trading safety (real money):
    LIVE trades are gated inside run_trading(), so EVERY caller — including
    daily_report.py --live — is protected. To trade live you must:
      1. Set env ALPACA_ALLOW_LIVE=1 (covers automated/non-interactive runs).
      2. Type 'YES' when prompted, if running interactively (a TTY).
    Optional hard guardrail:
      ALPACA_MAX_NOTIONAL=<dollars>  Abort a live run if total $ traded exceeds
                                     this. Defaults to 0 (disabled). Paper ignores it.
"""
import os
import sys
import json
import numpy as np
from datetime import datetime, timedelta

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "core"))  # private algorithm package


def _authorize_live_trading():
    """Gate real-money trading. Raises PermissionError unless explicitly authorized.

    Lives here (not in __main__) so that every entry point — daily_report.py,
    cron jobs, direct imports — is forced through the same check. Paper trading
    never calls this.
    """
    if os.environ.get("ALPACA_ALLOW_LIVE") != "1":
        raise PermissionError(
            "LIVE trading blocked: set ALPACA_ALLOW_LIVE=1 to authorize real-money "
            "trades. (Paper trading requires no flag.)"
        )
    # Interactive runs additionally require a typed confirmation.
    if sys.stdin is not None and sys.stdin.isatty():
        confirm = input(
            "WARNING: about to trade with REAL MONEY. Type 'YES' to confirm: "
        )
        if confirm != "YES":
            raise PermissionError("Live trading aborted by user.")


def _fetch_latest_prices(tickers):
    """Best-effort latest trade prices from Alpaca's data API.

    Used to size buys for target stocks not already held, instead of a possibly
    stale yfinance daily close. Returns {ticker: price}; missing tickers are
    simply absent so callers can fall back.
    """
    out = {}
    if not tickers:
        return out
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        api_key = os.environ.get("ALPACA_API_KEY", "")
        api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        dc = StockHistoricalDataClient(api_key, api_secret)
        syms = [_to_alpaca_symbol(t) for t in tickers]
        req = StockLatestTradeRequest(symbol_or_symbols=syms)
        latest = dc.get_stock_latest_trade(req)
        for t in tickers:
            tr = latest.get(_to_alpaca_symbol(t))
            if tr is not None and float(tr.price) > 0:
                out[t] = float(tr.price)
    except Exception as e:
        print(f"  (Alpaca latest-price fetch failed, will fall back to yfinance: {e})")
    return out


def _to_alpaca_symbol(ticker):
    """Convert yfinance ticker to Alpaca symbol (e.g. BRK-B → BRK.B)."""
    return ticker.replace("-", ".")


def _from_alpaca_symbol(symbol):
    """Convert Alpaca symbol to yfinance ticker (e.g. BRK.B → BRK-B)."""
    return symbol.replace(".", "-")


def get_alpaca_client(paper=True):
    """Initialize Alpaca trading client."""
    from alpaca.trading.client import TradingClient

    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not api_secret:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set. "
            "Get them from https://app.alpaca.markets/paper/dashboard/overview"
        )

    return TradingClient(api_key, api_secret, paper=paper)


def compute_target_weights(scored_results, macro_result=None, held=None):
    """
    Compute target portfolio weights using the concentrated top-10 strategy.

    Delegates to strategy.compute_target_weights — the single source of truth
    shared with report_generator and backtest_strategy, so the live, reported
    and backtested portfolios cannot drift apart.

    held: set of currently-held tickers (real positions, not remnants) —
    enables the rank buffer so names oscillating around the top-10 boundary
    don't get churned.

    Returns:
        (weights, cash_pct): weights is {ticker: weight_pct}, cash_pct 0.0-0.20.
    """
    import strategy

    if not scored_results or len(scored_results) < 2:
        return {}, 0.0

    macro_score = macro_result.get("score", 50) if macro_result else 50

    stocks = []
    for r in scored_results:
        ind = r.get("indicators", {})
        m1 = ind.get("change_1m") / 100 if ind.get("change_1m") is not None else 0.0
        m3 = ind.get("change_3m") / 100 if ind.get("change_3m") is not None else 0.0
        stocks.append({
            "ticker": r["ticker"],
            "score": r["score_result"]["score"],
            "mom": strategy.momentum_composite(m1, m3),
        })

    return strategy.compute_target_weights(stocks, macro_score=macro_score,
                                           held=held)


def get_current_positions(client):
    """Get current portfolio positions as {ticker: market_value}."""
    positions = client.get_all_positions()
    pos_map = {}
    for p in positions:
        # Convert Alpaca symbols back to yfinance tickers (BRK.B → BRK-B)
        ticker = _from_alpaca_symbol(p.symbol)
        pos_map[ticker] = {
            "qty": float(p.qty),
            "market_value": float(p.market_value),
            "current_price": float(p.current_price),
        }
    return pos_map


def get_account_value(client):
    """Get total account equity."""
    account = client.get_account()
    return float(account.equity)


def calculate_trades(target_weights, current_positions, account_value,
                     cash_pct, blend_speed=0.80):
    """
    Calculate trades needed to move toward target weights.

    Uses gradual rebalancing (blend_speed) to reduce turnover.

    Returns:
        list of dicts: [{ticker, side, qty, dollar_amount, reason}]
    """
    from alpaca.trading.enums import OrderSide

    investable = account_value * (1 - cash_pct)

    # Current weights
    current_weights = {}
    for ticker, pos in current_positions.items():
        current_weights[ticker] = (pos["market_value"] / account_value * 100) if account_value > 0 else 0

    # All tickers (union of current positions and targets)
    all_tickers = set(list(target_weights.keys()) + list(current_positions.keys()))

    # Blend toward target (gradual rebalancing). Names with a ZERO target are
    # exited outright (blended weight 0, full-qty sell below) — blending an
    # exit leaves a tail that whole-share rounding can never sell off, which is
    # how the book once accumulated dozens of stuck 1-share remnants.
    blended_weights = {}
    for ticker in all_tickers:
        current_w = current_weights.get(ticker, 0)
        target_w = target_weights.get(ticker, 0)
        if target_w <= 0:
            blended_weights[ticker] = 0.0
        else:
            blended_weights[ticker] = current_w + blend_speed * (target_w - current_w)

    # Normalize blended weights
    total_blended = sum(max(0, w) for w in blended_weights.values())
    if total_blended > 0:
        scale = (1 - cash_pct) * 100 / total_blended
        blended_weights = {t: max(0, w * scale) for t, w in blended_weights.items()}

    # Minimum trade size: 0.3% of equity (floor $50). The old flat $50 threshold
    # let daily rank shuffles generate hundreds of tiny rebalance orders.
    min_trade = max(50.0, account_value * 0.003)

    trades = []
    for ticker in all_tickers:
        target_value = account_value * blended_weights.get(ticker, 0) / 100
        current_value = current_positions.get(ticker, {}).get("market_value", 0)
        held_qty = int(float(current_positions.get(ticker, {}).get("qty", 0)))
        diff = target_value - current_value

        # Full exit: dropped from the portfolio → sell every share we hold,
        # bypassing the min-trade and rounding paths so no remnant survives.
        if target_value <= 0 and held_qty > 0:
            trades.append({
                "ticker": ticker,
                "side": "sell",
                "qty": held_qty,
                "dollar_amount": current_value,
                "current_weight": current_weights.get(ticker, 0),
                "target_weight": 0.0,
                "blended_weight": 0.0,
                "reason": "Exit (dropped from portfolio)",
            })
            continue

        # Skip tiny trades (< min_trade or < 1% of position)
        if abs(diff) < min_trade:
            continue
        if current_value > 0 and abs(diff) / current_value < 0.01:
            continue

        current_price = current_positions.get(ticker, {}).get("current_price", 0)
        if current_price <= 0:
            continue

        qty = int(abs(diff) / current_price)
        if qty == 0:
            # Whole-share rounding can't establish/adjust this position. Surface
            # it instead of silently skipping (matters for high-priced names).
            if diff > 0:
                print(f"  Note: {ticker} buy ~${abs(diff):,.0f} rounds to 0 shares "
                      f"at ${current_price:,.2f} — under-allocated this run.")
            continue

        if diff > 0:
            side = "buy"
            reason = f"Increase to {blended_weights.get(ticker, 0):.1f}% (target: {target_weights.get(ticker, 0):.1f}%)"
        else:
            side = "sell"
            # Never sell more than we actually hold (avoids rejected/short orders).
            qty = min(qty, held_qty)
            if qty == 0:
                continue
            reason = f"Reduce to {blended_weights.get(ticker, 0):.1f}% (target: {target_weights.get(ticker, 0):.1f}%)"

        trades.append({
            "ticker": ticker,
            "side": side,
            "qty": qty,
            "dollar_amount": abs(diff),
            "current_weight": current_weights.get(ticker, 0),
            "target_weight": target_weights.get(ticker, 0),
            "blended_weight": blended_weights.get(ticker, 0),
            "reason": reason,
        })

    # Sort: sells first (free up cash), then buys
    trades.sort(key=lambda x: (0 if x["side"] == "sell" else 1, -x["dollar_amount"]))

    return trades


def execute_trades(client, trades, dry_run=False):
    """
    Execute trades via Alpaca.

    Args:
        client: Alpaca TradingClient
        trades: list from calculate_trades()
        dry_run: if True, only print trades without executing

    Returns:
        list of executed order results
    """
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    results = []
    print(f"\n{'='*60}")
    print(f"{'DRY RUN — ' if dry_run else ''}EXECUTING {len(trades)} TRADES")
    print(f"{'='*60}")

    for trade in trades:
        ticker = trade["ticker"]
        side = OrderSide.BUY if trade["side"] == "buy" else OrderSide.SELL
        qty = trade["qty"]
        amount = trade["dollar_amount"]

        print(f"\n  {trade['side'].upper():>4} {qty:>5} x {ticker:<8} "
              f"(~${amount:,.0f}) — {trade['reason']}")

        if dry_run:
            results.append({"ticker": ticker, "status": "dry_run", "qty": qty})
            continue

        try:
            order = MarketOrderRequest(
                symbol=_to_alpaca_symbol(ticker),
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )
            result = client.submit_order(order)
            results.append({
                "ticker": ticker,
                "status": "submitted",
                "order_id": str(result.id),
                "qty": qty,
                "side": trade["side"],
            })
            print(f"         Order submitted: {result.id}")
        except Exception as e:
            results.append({
                "ticker": ticker,
                "status": "error",
                "error": str(e),
            })
            print(f"         ERROR: {e}")

    print(f"\n{'='*60}")
    submitted = sum(1 for r in results if r["status"] == "submitted")
    errors = sum(1 for r in results if r["status"] == "error")
    print(f"Results: {submitted} submitted, {errors} errors")
    print(f"{'='*60}")

    return results


def run_trading(scored_results, macro_result=None, paper=True, dry_run=False,
                max_notional=None):
    """
    Main entry point: examine the account, compute weights, execute trades.
    Runs unconditionally — min-trade thresholds in calculate_trades suppress
    churn when the portfolio is already aligned.

    Live (real-money) trading is gated here via _authorize_live_trading() so all
    callers are protected. max_notional (or env ALPACA_MAX_NOTIONAL) caps the
    total dollars traded in a single live run; exceeding it aborts the run.
    """
    print(f"\n{'='*60}")
    print(f"ALPACA {'PAPER' if paper else 'LIVE'} TRADING")
    print(f"{'='*60}")

    # --- LIVE TRADING SAFETY GATE (applies to every caller) ---
    # A dry-run never reaches a broker, so it's exempt.
    if not paper and not dry_run:
        _authorize_live_trading()

    # Initialize client
    client = get_alpaca_client(paper=paper)
    account = client.get_account()
    account_value = float(account.equity)
    buying_power = float(getattr(account, "buying_power", account_value) or account_value)
    print(f"Account Equity: ${account_value:,.2f}  |  Buying Power: ${buying_power:,.2f}")

    # Pending-order guard: never stack new orders on top of unfilled ones, or a
    # double run / unfilled market-on-open batch would double our exposure.
    try:
        open_orders = client.get_orders()
    except Exception as e:
        open_orders = []
        print(f"  Warning: could not check open orders ({e})")
    if open_orders and not dry_run:
        print(f"\n{len(open_orders)} open order(s) already pending — skipping this run "
              f"to avoid double-trading:")
        for o in open_orders:
            print(f"  {o.symbol:<8} {o.side.name:<4} {o.qty} [{o.status.name}]")
        return {"status": "skipped", "reason": "open_orders_pending",
                "open_orders": len(open_orders)}

    # Examine account every run — no interval gate. The min-trade thresholds
    # in calculate_trades prevent churn once we're aligned.

    # Get current positions first: the rank buffer needs to know what we hold.
    # Remnants (< 1% of equity) don't count as held — a stuck leftover must not
    # reserve a portfolio slot.
    current_positions = get_current_positions(client)
    held = {t for t, pos in current_positions.items()
            if pos["market_value"] >= account_value * 0.01}

    # Compute target weights
    target_weights, cash_pct = compute_target_weights(scored_results, macro_result,
                                                      held=held)
    if not target_weights:
        print("No target weights computed. Skipping.")
        return {"status": "error", "reason": "no target weights"}

    print(f"\nCash Reserve: {cash_pct*100:.0f}%")
    print(f"Investable: ${account_value * (1 - cash_pct):,.2f}")
    print(f"\nTarget Weights (Top 10):")
    for ticker in sorted(target_weights, key=lambda x: target_weights[x], reverse=True):
        if target_weights[ticker] > 0.1:
            print(f"  {ticker:<8} {target_weights[ticker]:>5.1f}%")
    if current_positions:
        print(f"\nCurrent Positions ({len(current_positions)}):")
        for ticker in sorted(current_positions, key=lambda x: current_positions[x]["market_value"], reverse=True):
            pos = current_positions[ticker]
            pct = pos["market_value"] / account_value * 100
            print(f"  {ticker:<8} {pos['qty']:>6.0f} shares  ${pos['market_value']:>10,.2f}  ({pct:.1f}%)")
    else:
        print("\nNo current positions (starting fresh)")

    # Fetch current prices for any target stocks NOT already in positions
    # (needed for buy orders — without a price we can't calculate qty).
    # Prefer Alpaca's latest trade price; fall back to yfinance daily close.
    missing = [t for t in target_weights if t not in current_positions]
    if missing:
        print(f"Fetching prices for {len(missing)} new target stocks...")
        fresh = _fetch_latest_prices(missing)
        import yfinance as _yf
        for ticker in missing:
            price = fresh.get(ticker)
            if price is None:
                try:
                    hist = _yf.Ticker(ticker).history(period="5d")
                    if hist is not None and not hist.empty:
                        price = float(hist["Close"].iloc[-1])
                except Exception:
                    price = None
            if price and price > 0:
                current_positions[ticker] = {
                    "qty": 0, "market_value": 0, "current_price": price,
                }
            else:
                print(f"  Warning: could not fetch price for {ticker} — skipping")

    # Calculate trades
    trades = calculate_trades(target_weights, current_positions, account_value, cash_pct)

    if not trades:
        print("\nNo trades needed — portfolio is already aligned.")
        return {"status": "no_trades_needed"}

    # Buying-power guard: sells settle asynchronously, so their proceeds are NOT
    # available to fund same-batch buys. Scale buys down to fit available buying
    # power and drop any that round to zero — prevents rejected/over-leveraged orders.
    buys = [t for t in trades if t["side"] == "buy"]
    total_buy = sum(t["dollar_amount"] for t in buys)
    if buys and total_buy > buying_power:
        scale = (buying_power / total_buy) * 0.99 if total_buy > 0 else 0
        print(f"\nBuying power ${buying_power:,.0f} < buy demand ${total_buy:,.0f} — "
              f"scaling buys to {scale*100:.0f}%.")
        for t in buys:
            price = t["dollar_amount"] / t["qty"] if t["qty"] else 0
            t["qty"] = int(t["qty"] * scale)
            t["dollar_amount"] = t["qty"] * price
        trades = [t for t in trades if t["qty"] > 0]

    # Hard notional cap for live runs: abort entirely (don't partially execute an
    # unexpectedly large rebalance) if total $ traded exceeds the configured cap.
    if max_notional is None:
        max_notional = float(os.environ.get("ALPACA_MAX_NOTIONAL", "0") or 0)
    if max_notional and not paper:
        total_notional = sum(t["dollar_amount"] for t in trades)
        if total_notional > max_notional:
            print(f"\nABORT: total trade notional ${total_notional:,.0f} exceeds cap "
                  f"${max_notional:,.0f} (ALPACA_MAX_NOTIONAL). No orders submitted.")
            return {"status": "aborted", "reason": "notional_cap_exceeded",
                    "total_notional": total_notional, "max_notional": max_notional}

    # Execute
    results = execute_trades(client, trades, dry_run=dry_run)

    # --- Post-trade verification ---
    _verify_trades(client, trades, results, target_weights, account_value, cash_pct, dry_run)

    return {
        "status": "completed",
        "trades": len(trades),
        "results": results,
        "target_weights": target_weights,
        "cash_pct": cash_pct,
    }


def _verify_trades(client, trades, results, target_weights, account_value,
                    cash_pct, dry_run):
    """
    Post-trade verification: check that orders were submitted, positions
    are directionally correct, and flag any discrepancies.
    """
    print(f"\n{'='*60}")
    print("POST-TRADE VERIFICATION")
    print(f"{'='*60}")

    if dry_run:
        print("  (dry run — skipping verification)")
        return

    # 1. Check order submission results
    submitted = [r for r in results if r["status"] == "submitted"]
    errors = [r for r in results if r["status"] == "error"]
    total = len(trades)

    print(f"\n  Orders: {len(submitted)}/{total} submitted, {len(errors)} errors")
    if errors:
        print("  ERRORS:")
        for e in errors:
            print(f"    {e['ticker']}: {e.get('error', 'unknown')}")

    # 2. Check pending vs filled orders
    from alpaca.trading.requests import GetOrdersRequest
    from alpaca.trading.enums import QueryOrderStatus

    open_orders = client.get_orders()
    if open_orders:
        print(f"\n  Pending orders: {len(open_orders)} (will fill at next market open)")
        for o in open_orders:
            print(f"    {o.symbol:<8} {o.side.name:<4} {int(float(o.qty)):>5} shares — {o.status.name}")
    else:
        print(f"\n  All orders filled or no pending orders.")

    # 3. Verify positions match intent
    current = get_current_positions(client)
    acct = client.get_account()
    equity = float(acct.equity)
    cash = float(acct.cash)
    invested = sum(p["market_value"] for p in current.values())

    print(f"\n  Account equity:  ${equity:>12,.2f}")
    print(f"  Invested:        ${invested:>12,.2f}  ({invested/equity*100:.1f}%)")
    print(f"  Cash:            ${cash:>12,.2f}  ({cash/equity*100:.1f}%)")
    print(f"  Target cash:     {cash_pct*100:.0f}%")

    # 4. Check position count
    target_tickers = set(target_weights.keys())
    held_tickers = set(current.keys())
    expected_buys = target_tickers - held_tickers
    unexpected_holds = held_tickers - target_tickers

    if expected_buys:
        print(f"\n  WARNING: Target stocks NOT in portfolio: {', '.join(sorted(expected_buys))}")
        print(f"    (These may be in pending orders waiting for market open)")
    if unexpected_holds:
        print(f"\n  NOTE: Held stocks NOT in target (being phased out): {', '.join(sorted(unexpected_holds))}")

    # 5. Check weight alignment
    print(f"\n  Weight alignment:")
    all_tickers = sorted(target_tickers | held_tickers,
                         key=lambda t: target_weights.get(t, 0), reverse=True)
    misaligned = 0
    for ticker in all_tickers:
        target_w = target_weights.get(ticker, 0)
        actual_w = current.get(ticker, {}).get("market_value", 0) / equity * 100 if equity > 0 else 0
        diff = actual_w - target_w
        flag = " <<<" if abs(diff) > 5 else ""
        if target_w > 0 or actual_w > 0.5:
            print(f"    {ticker:<8} target: {target_w:>5.1f}%  actual: {actual_w:>5.1f}%  diff: {diff:>+5.1f}%{flag}")
            if abs(diff) > 5:
                misaligned += 1

    if misaligned:
        print(f"\n  WARNING: {misaligned} positions misaligned by >5% — will converge on next rebalance")
    else:
        print(f"\n  All positions within 5% of target.")

    print(f"{'='*60}")


if __name__ == "__main__":
    import argparse

    # Load .env
    _env_file = os.path.join(_SCRIPT_DIR, ".env")
    if os.path.exists(_env_file):
        with open(_env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())

    parser = argparse.ArgumentParser(description="Alpaca trading for concentrated top-10 strategy")
    parser.add_argument("--live", action="store_true", help="Use live trading (default: paper)")
    parser.add_argument("--dry-run", action="store_true", help="Show trades without executing")
    parser.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers (default: full universe)")
    args = parser.parse_args()

    # Live authorization is enforced inside run_trading() (env ALPACA_ALLOW_LIVE=1
    # plus a typed confirmation when interactive) — no separate prompt needed here.

    # Run analysis first
    import main as analysis_main
    from configs import UNIVERSE

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else sorted(set(UNIVERSE.keys()))

    print("Running analysis...")
    results, macro_result = analysis_main.run_analysis(tickers, skip_news=False, skip_alpha=False)

    if not results:
        print("No analysis results. Exiting.")
        sys.exit(1)

    # Execute trades
    try:
        run_trading(
            scored_results=results,
            macro_result=macro_result,
            paper=not args.live,
            dry_run=args.dry_run,
        )
    except PermissionError as e:
        print(f"Aborted: {e}")
        sys.exit(1)
