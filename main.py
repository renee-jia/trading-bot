#!/usr/bin/env python3
"""
Stock Recommendation Engine - Macro Buy-and-Hold Strategy

Comprehensive multi-factor analysis combining:
- Technical analysis (trend, momentum, volume, volatility)
- News sentiment analysis
- Macro trend analysis (relative strength, market regime)
- Quantitative alpha factors

Usage:
    python main.py                                    # Analyze all stocks
    python main.py --tickers AAPL,MSFT,NVDA          # Specific tickers
    python main.py --top 10                           # Show top 10 only
    python main.py --quick                            # Skip news (faster)
"""
import os
import sys
import argparse
from datetime import datetime

# Make the private algorithm package (core/) importable
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "core"))

import data_fetcher
import technical_analyzer
import sentiment_analyzer
import trend_analyzer
import scorer
import report_generator
import macro_analyzer
from configs import UNIVERSE, ANALYZER_WEIGHTS


def analyze_stock(ticker, price_data, benchmark_data, hourly_data=None,
                  news_data=None, alpha_score=None, sector=None,
                  macro_score=None):
    """
    Run full multi-factor analysis on a single stock.

    Args:
        price_data: 2y daily OHLCV DataFrame.
        benchmark_data: SPY 2y daily OHLCV DataFrame.
        hourly_data: 1y hourly OHLCV DataFrame (optional, enhances technical signals).
        news_data: list of news items (optional).
        alpha_score: float 0-1 from alpha factor analysis (optional).
        sector: stock sector string (optional, enhances sentiment analysis).
        macro_score: float 0-100 from macro_analyzer (optional, blended into trend).

    Returns a score_result dict from scorer.score_stock().
    """
    # Technical analysis (daily + hourly)
    tech_result = technical_analyzer.analyze(price_data, hourly_data=hourly_data)

    # Build price context for sentiment analysis
    indicators = tech_result.get("indicators", {})
    price_context = {k: indicators[k] for k in ("price", "change_1d", "change_5d", "change_1m")
                     if k in indicators}

    # Sentiment analysis (Claude LLM with keyword fallback)
    if news_data:
        sent_result = sentiment_analyzer.analyze_sentiment(
            news_data, ticker=ticker, sector=sector, price_context=price_context)
    else:
        sent_result = {"score": 0.5, "confidence": 0.0, "num_articles": 0,
                       "num_bullish": 0, "num_bearish": 0, "headlines": []}

    # Macro trend analysis (daily) — macro_score from macro_analyzer replaces per-stock Claude calls
    trend_result = trend_analyzer.analyze(price_data, benchmark_data, macro_score=macro_score)

    import stock_signals
    research_result = stock_signals.analyze(price_data, benchmark_data)

    # Combine into final score
    score_result = scorer.score_stock(
        tech_result, sent_result, trend_result,
        alpha_score=alpha_score,
        weights=ANALYZER_WEIGHTS,
        research_result=research_result,
    )

    # Attach sentiment and trend details for report generation
    score_result["sentiment_detail"] = {
        "reasoning": sent_result.get("reasoning", ""),
        "key_themes": sent_result.get("key_themes", []),
        "risk_flags": sent_result.get("risk_flags", []),
        "method": sent_result.get("method", "keyword"),
    }
    score_result["trend_detail"] = {
        "claude_synthesis": trend_result.get("claude_synthesis", {}),
    }

    return score_result, tech_result


def compute_alpha_score(price_data):
    """Shared alpha normalization with the historical and allocation harness."""
    import strategy
    return strategy.compute_alpha_score(price_data)


def _fetch_stock_data(ticker, skip_news=False):
    """Fetch all data for a single stock (for parallel execution)."""
    try:
        price_data = data_fetcher.fetch_price_data(ticker, period="2y")
        if price_data is None:
            return ticker, None

        hourly_data = data_fetcher.fetch_hourly_data(ticker, period="1y")

        news_data = None
        if not skip_news:
            raw_news = data_fetcher.fetch_news(ticker)
            if raw_news:
                news_data = raw_news

        info = data_fetcher.fetch_stock_info(ticker)

        return ticker, {
            "price_data": price_data,
            "hourly_data": hourly_data,
            "news_data": news_data,
            "info": info,
        }
    except Exception:
        return ticker, None


def run_analysis(tickers, skip_news=False, skip_alpha=False):
    """
    Run full analysis on a list of tickers.

    Args:
        tickers: list of ticker symbols
        skip_news: if True, skip news sentiment (faster)
        skip_alpha: if True, skip alpha factor computation (faster)

    Returns:
        tuple of (results_list, macro_result)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Run macro analysis first
    print(f"\nRunning macro market analysis...")
    try:
        macro_result = macro_analyzer.analyze_macro()
        print(f"  Macro Score: {macro_result['score']:.0f}/100 — {macro_result['recommendation']}")
    except Exception as e:
        print(f"  Macro analysis failed: {e}")
        macro_result = None

    print(f"\nFetching SPY benchmark data...")
    benchmark_data = data_fetcher.fetch_benchmark(period="2y")
    if benchmark_data is None:
        print("  Warning: Could not fetch benchmark data. Relative strength will be unavailable.")

    total = len(tickers)

    # Phase 1: Fetch all data in parallel (I/O bound — threads help a lot)
    print(f"\nFetching data for {total} stocks (parallel)...")
    fetched = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_stock_data, t, skip_news): t for t in tickers}
        for future in as_completed(futures):
            ticker, data = future.result()
            if data is not None:
                fetched[ticker] = data
                bars = len(data["price_data"])
                hourly = len(data["hourly_data"]) if data["hourly_data"] is not None else 0
                news_count = len(data["news_data"]) if data["news_data"] else 0
                print(f"  {ticker}: {bars} daily + {hourly} hourly bars, {news_count} news")
            else:
                print(f"  {ticker}: skipped (no data)")

    print(f"\n{len(fetched)}/{total} stocks fetched. Running analysis...")

    # Phase 2: Analyze sequentially (CPU bound + Claude API calls)
    results = []
    for i, ticker in enumerate(tickers, 1):
        if ticker not in fetched:
            continue

        stock_data = fetched[ticker]
        price_data = stock_data["price_data"]
        hourly_data = stock_data["hourly_data"]
        news_data = stock_data["news_data"]
        info = stock_data["info"]

        print(f"\n[{i}/{total}] Analyzing {ticker}...")

        # Compute alpha score
        alpha_score = None
        if not skip_alpha:
            alpha_score = compute_alpha_score(price_data)

        # Run analysis — pass macro_score so trend_analyzer can blend macro context
        # without making a separate Claude API call per stock
        _macro_score = macro_result.get("score") if macro_result else None
        try:
            score_result, tech_result = analyze_stock(
                ticker, price_data, benchmark_data,
                hourly_data=hourly_data,
                news_data=news_data, alpha_score=alpha_score,
                sector=info.get("sector", "Unknown"),
                macro_score=_macro_score,
            )
        except Exception as e:
            print(f"  Error: {e}")
            continue

        # Get indicators from technical result
        indicators = tech_result.get("indicators", {})

        result = {
            "ticker": ticker,
            "name": info.get("name", ticker),
            "sector": info.get("sector", "Unknown"),
            "score_result": score_result,
            "indicators": indicators,
            "info": info,
            "news_data": news_data or [],
        }

        results.append(result)

        # Print summary
        sr = score_result
        print(f"  Score: {sr['score']:.0f}/100 ({sr['grade']}) - {sr['recommendation']}")
        print(f"  Confidence: {sr['confidence']*100:.0f}% | Regime: {sr.get('regime', '-')}")
        comp = sr["components"]
        print(f"  Tech: {comp['technical']['score']:.0f} | "
              f"Trend: {comp['trend']['score']:.0f} | "
              f"Alpha: {comp['alpha']['score']:.0f} | "
              f"Sent: {comp['sentiment']['score']:.0f}")

    import stock_signals
    return stock_signals.add_peer_ranks(results), macro_result


def main():
    parser = argparse.ArgumentParser(
        description="Stock Recommendation Engine - Macro Buy-and-Hold Strategy"
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="Comma-separated ticker symbols (default: all from config)"
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Only show top N recommendations in console output"
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="Skip news sentiment analysis (faster)"
    )
    parser.add_argument(
        "--no-alpha", action="store_true",
        help="Skip alpha factor computation (faster)"
    )
    parser.add_argument(
        "--output-dir", type=str, default="reports",
        help="Output directory for reports (default: reports/)"
    )
    parser.add_argument(
        "--no-report", action="store_true",
        help="Skip report generation, only print to console"
    )
    args = parser.parse_args()

    # Determine tickers
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
    else:
        tickers = sorted(set(UNIVERSE.keys()))

    print("=" * 70)
    print("STOCK RECOMMENDATION ENGINE")
    print("Strategy: Macro Buy-and-Hold (weeks+ holding)")
    print("=" * 70)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Stocks: {len(tickers)}")
    print(f"News: {'Enabled' if not args.quick else 'Disabled'}")
    print(f"Alpha: {'Enabled' if not args.no_alpha else 'Disabled'}")
    print("=" * 70)

    # Run analysis
    results, macro_result = run_analysis(tickers, skip_news=args.quick, skip_alpha=args.no_alpha)

    if not results:
        print("\nNo results generated. Check your tickers and network connection.")
        sys.exit(1)

    # Sort by score
    results.sort(key=lambda x: x["score_result"]["score"], reverse=True)

    # Print summary
    print("\n" + "=" * 70)
    print("RECOMMENDATIONS SUMMARY")
    print("=" * 70)

    show = results[:args.top] if args.top else results

    print(f"\n{'Ticker':<8} {'Score':>6} {'Grade':>6} {'Rec':<12} {'Conf':>6} {'Regime':<10} {'Price':>10}")
    print("-" * 70)

    for r in show:
        sr = r["score_result"]
        price = r.get("indicators", {}).get("price", 0)
        print(
            f"{r['ticker']:<8} "
            f"{sr['score']:>5.0f} "
            f"{sr['grade']:>6} "
            f"{sr['recommendation']:<12} "
            f"{sr['confidence']*100:>5.0f}% "
            f"{sr.get('regime', '-'):<10} "
            f"${price:>9.2f}"
        )

    # Category counts
    categories = {}
    for r in results:
        rec = r["score_result"]["recommendation"]
        categories[rec] = categories.get(rec, 0) + 1

    print(f"\n{'='*70}")
    for cat in ["Strong Buy", "Buy", "Hold", "Reduce", "Avoid"]:
        count = categories.get(cat, 0)
        if count > 0:
            print(f"  {cat}: {count}")

    # Generate report
    if not args.no_report:
        report_path, _ = report_generator.generate_report(results, output_dir=args.output_dir, macro_result=macro_result)
        print(f"\nReport saved: {report_path}")

    print(f"\nAnalysis complete. {len(results)} stocks analyzed.")


if __name__ == "__main__":
    main()
