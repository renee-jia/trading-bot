#!/usr/bin/env python3
"""
Daily stock analysis runner.

Fetches 2y daily + 1y hourly data, runs the full improved model on all stocks,
generates a Markdown report, and emails it.

Model: Technical (35%) + Trend (30%) + Alpha (20%) + Sentiment (15%)
Data:  2y daily close + 1y hourly bars
Signals: Momentum acceleration, multi-TF alignment, conviction bonus

Usage:
    python daily_report.py                    # Run + email report
    python daily_report.py --no-email         # Run without emailing
    python daily_report.py --tickers AAPL,NVDA  # Specific tickers
"""
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# Ensure we import from the script's own directory (for launchd)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "core"))  # private algorithm package

# Load .env file if it exists (for launchd which doesn't inherit shell env)
_env_file = os.path.join(_SCRIPT_DIR, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

import main as analysis_main
import report_generator
import email_sender
import stock_discovery
from configs import UNIVERSE


def run_daily(tickers=None, skip_news=False, send_mail=True, trade=False,
              paper=True, dry_run=False):
    """Run daily analysis, generate report, optionally email and trade."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'='*70}")
    print(f"DAILY STOCK ANALYSIS — {date_str}")
    print(f"{'='*70}")

    focus_mode = False
    if tickers is None:
        # Default pool: semiconductor + big-tech leaders (configs.FOCUS_UNIVERSE).
        # UNIVERSE_MODE=full analyses everything again.
        try:
            from configs import active_tickers
            tickers = active_tickers()
            focus_mode = len(tickers) < len(UNIVERSE)
        except ImportError:
            tickers = sorted(set(UNIVERSE.keys()))

    # Run the full analysis
    results, macro_result = analysis_main.run_analysis(tickers, skip_news=skip_news, skip_alpha=False)

    if not results:
        print("No results generated.")
        return

    # Sort by score (highest first)
    results.sort(key=lambda x: x["score_result"]["score"], reverse=True)

    # Execute trades BEFORE report generation so the report reflects post-trade portfolio
    if trade:
        try:
            import alpaca_trader
            trade_result = alpaca_trader.run_trading(
                scored_results=results,
                macro_result=macro_result,
                paper=paper,
                dry_run=dry_run,
            )
            print(f"\nTrading result: {trade_result.get('status', 'unknown')}")
        except Exception as e:
            print(f"\nTrading error: {e}")

    # Run stock discovery (weekly — only on Mondays)
    discovery_result = None
    # Discovery proposes new (mostly smaller) names, so it is off in focus mode.
    if datetime.now().weekday() == 0 and not focus_mode:  # Monday
        try:
            discovery_result = stock_discovery.discover_stocks(
                UNIVERSE, scored_results=results, macro_result=macro_result
            )
        except Exception as e:
            print(f"Stock discovery failed: {e}")

    # AI 持有标的 sell-put plan: built once, shared by the report and the email
    sell_put_plan = None
    try:
        import ai_sell_put_plan
        sell_put_plan = ai_sell_put_plan.build_plan(results, macro_result=macro_result)
    except Exception as e:
        print(f"AI sell-put plan failed: {e}")

    # Generate report (after trading, so portfolio section shows post-trade state)
    options_decisions = {}
    ai_portfolio_data = {}
    saas_watch_data = {}
    dip_layout_data = {}
    chip_layout_data = {}
    optics_layout_data = {}
    power_layout_data = {}
    report_path, _ = report_generator.generate_report(
        results, output_dir="reports", macro_result=macro_result,
        discovery_result=discovery_result,
        options_decisions=options_decisions,
        sell_put_plan=sell_put_plan,
        ai_portfolio=ai_portfolio_data,
        saas_watch=saas_watch_data,
        dip_layout=dip_layout_data,
        chip_layout=chip_layout_data,
        optics_layout=optics_layout_data,
        power_layout=power_layout_data,
    )
    print(f"\nReport saved: {report_path}")

    # Build email summary
    top_picks = [r for r in results if r["score_result"]["recommendation"] in ("Strong Buy", "Buy")]
    from report_sections import plain_summary
    summary = plain_summary(Path(report_path).read_text(encoding="utf-8"))

    print(f"\n{summary}")

    # Send email: decision-first HTML digest in the body (Gmail clips bodies
    # over ~100KB), full HTML + Markdown report attached.
    if send_mail:
        with open(report_path, "r") as f:
            report_content = f.read()
        subject = (
            f"Stock Analysis Report — {date_str} | "
            f"{(macro_result or {}).get('trend_outlook', {}).get('stance_label') or (str(len(top_picks)) + ' Buys')}"
        )
        html_path = os.path.splitext(report_path)[0] + ".html"
        attachments = [p for p in (html_path, report_path) if os.path.exists(p)]
        email_sender.send_report_email(report_content, subject=subject,
                                       attachments=attachments, text_summary=summary)

    return report_path


def is_trading_day():
    """Check if today is a weekday (Mon-Fri)."""
    return datetime.now().weekday() < 5


def main():
    parser = argparse.ArgumentParser(description="Daily stock analysis + email report")
    parser.add_argument("--tickers", type=str, default=None,
                        help="Comma-separated tickers (default: full universe)")
    parser.add_argument("--quick", action="store_true",
                        help="Skip news sentiment (faster)")
    parser.add_argument("--no-email", action="store_true",
                        help="Skip sending email")
    parser.add_argument("--force", action="store_true",
                        help="Run even on weekends")
    parser.add_argument("--trade", action="store_true",
                        help="Execute trades via Alpaca after analysis")
    parser.add_argument("--live", action="store_true",
                        help="Use live trading (default: paper)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show trades without executing")
    args = parser.parse_args()

    if not args.force and not is_trading_day():
        print(f"Skipping: {datetime.now().strftime('%A')} is not a trading day.")
        return

    tickers = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None

    run_daily(
        tickers=tickers,
        skip_news=args.quick,
        send_mail=not args.no_email,
        trade=args.trade,
        paper=not args.live,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
