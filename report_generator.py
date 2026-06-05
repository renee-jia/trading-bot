"""
Report generator for stock recommendations.

Generates clean Markdown reports with:
- Executive summary
- Top picks ranked by score
- Detailed per-stock analysis with reasoning
"""
from datetime import datetime
from pathlib import Path


def generate_report(scored_results, output_dir="reports", macro_result=None,
                    discovery_result=None):
    """
    Generate a comprehensive Markdown recommendation report.

    Args:
        scored_results: list of dicts, each with keys:
            ticker, name, sector, score_result, indicators
        output_dir: directory to write report to
        macro_result: dict from macro_analyzer.analyze_macro() (optional)
        discovery_result: dict from stock_discovery.discover_stocks() (optional)

    Returns:
        (report_path, report_content) tuple
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = f"{output_dir}/recommendations_{timestamp}.md"

    # Sort by score
    ranked = sorted(scored_results, key=lambda x: x["score_result"]["score"], reverse=True)

    report = _build_report(ranked, macro_result=macro_result,
                          discovery_result=discovery_result)

    with open(report_path, "w") as f:
        f.write(report)

    return report_path, report


def _build_report(ranked, macro_result=None, discovery_result=None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(ranked)

    # Categorize
    strong_buys = [r for r in ranked if r["score_result"]["recommendation"] == "Strong Buy"]
    buys = [r for r in ranked if r["score_result"]["recommendation"] == "Buy"]
    holds = [r for r in ranked if r["score_result"]["recommendation"] == "Hold"]
    reduces = [r for r in ranked if r["score_result"]["recommendation"] == "Reduce"]
    avoids = [r for r in ranked if r["score_result"]["recommendation"] == "Avoid"]

    # Aggregate market overview data
    market_overview = _build_market_overview(ranked)

    # AI / Semiconductor / Chip / Storage thematic sector analysis
    ai_semi_section = _build_ai_semi_section(ranked)

    # Build macro section
    macro_section = _build_macro_section(macro_result) if macro_result else ""

    # Build Alpaca portfolio performance section
    alpaca_section = _build_alpaca_performance()

    report = f"""# Stock Recommendations Report

**Generated:** {now}
**Strategy:** Macro Buy-and-Hold (weeks+ holding period)
**Stocks Analyzed:** {total}

---

{macro_section}

{alpaca_section}

{market_overview}

{ai_semi_section}

---

## Executive Summary

| Category | Count | Tickers |
|----------|-------|---------|
| Strong Buy | {len(strong_buys)} | {_ticker_list(strong_buys)} |
| Buy | {len(buys)} | {_ticker_list(buys)} |
| Hold | {len(holds)} | {_ticker_list(holds)} |
| Reduce | {len(reduces)} | {_ticker_list(reduces)} |
| Avoid | {len(avoids)} | {_ticker_list(avoids)} |

---

## Scoring Methodology

Each stock is evaluated across four dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| Technical | 30% | Trend (MA crossovers, ADX), momentum (RSI, MACD), volume, volatility |
| Trend | 25% | Relative strength vs SPY, market regime, long-term momentum |
| Alpha | 30% | 30 quantitative alpha factors (momentum, mean-reversion, volume) |
| Sentiment | 15% | News headline sentiment, recency-weighted |

**Score Scale:** 0-100 (75+ = Strong Buy, 60-74 = Buy, 45-59 = Hold, 30-44 = Reduce, <30 = Avoid)

---

## Portfolio Strategy

**Strategy V6: Concentrated Top-10 by Momentum.** In `backtest_strategy.py` (1-year window, current large-cap universe, T+1 execution, 5 bps/side costs) the strategy modestly out-returned equal-weight buy-and-hold (low-single-digit % alpha) but at higher volatility and drawdown — so the edge is return, not risk-adjusted, and it is survivorship-biased toward today's winners. Treat backtested alpha as an upper bound.

**How it works:**
1. **Stock Selection:** Select top 10 stocks by **momentum rank** (70% 3-month + 30% 1-month composite return). Momentum is a better filter than score for catching breakouts early.
2. **Weight Allocation:** Within the top 10, blend 95% momentum ranking + 5% score-squared weighting. The highest-momentum stock gets ~3x the weight of the lowest.
3. **Rebalancing:** The bot re-examines the book on every scheduled run (the backtest models a biweekly, 14-trading-day cadence), moving 80% of the way toward target weights each time.
4. **Risk Management:** Max single position capped at 25%. Macro-based cash reserve (0-20%) adjusts exposure to market conditions.
5. **Philosophy:** Ride winners aggressively, rotate quickly into momentum leaders, let the market tell you what's working.

---

## Top Picks

"""

    # Top picks table
    top = ranked[:20]
    report += "| Rank | Ticker | Name | Score | Grade | Recommendation | Confidence | Regime |\n"
    report += "|------|--------|------|-------|-------|----------------|------------|--------|\n"

    for i, r in enumerate(top, 1):
        sr = r["score_result"]
        name = r.get("name", r["ticker"])[:20]
        report += (
            f"| {i} | **{r['ticker']}** | {name} "
            f"| {sr['score']:.0f} | {sr['grade']} "
            f"| {sr['recommendation']} "
            f"| {sr['confidence']*100:.0f}% "
            f"| {sr.get('regime', '-')} |\n"
        )

    # Component breakdown for top picks
    report += "\n### Score Breakdown (Top Picks)\n\n"
    report += "| Ticker | Technical | Trend | Alpha | Sentiment | Final |\n"
    report += "|--------|-----------|-------|-------|-----------|-------|\n"

    for r in top:
        sr = r["score_result"]
        c = sr["components"]
        report += (
            f"| **{r['ticker']}** "
            f"| {c['technical']['score']:.0f} "
            f"| {c['trend']['score']:.0f} "
            f"| {c['alpha']['score']:.0f} "
            f"| {c['sentiment']['score']:.0f} "
            f"| **{sr['score']:.0f}** |\n"
        )

    # Price performance for top picks
    report += "\n### Price Performance (Top Picks)\n\n"
    report += "| Ticker | Price | 1D | 5D | 1M | 3M | RSI | SMA50 | SMA200 |\n"
    report += "|--------|-------|-----|-----|-----|-----|-----|-------|--------|\n"

    for r in top:
        ind = r.get("indicators", {})
        report += (
            f"| **{r['ticker']}** "
            f"| ${ind.get('price', 0):.2f} "
            f"| {ind.get('change_1d', 0):+.1f}% "
            f"| {ind.get('change_5d', 0):+.1f}% "
            f"| {ind.get('change_1m', 0):+.1f}% "
            f"| {ind.get('change_3m', 0):+.1f}% "
            f"| {ind.get('rsi', 0):.0f} "
            f"| ${ind.get('sma_50', 0):.2f} "
            f"| ${ind.get('sma_200', 0):.2f} |\n"
        )

    # Portfolio weight suggestions (score + momentum + macro)
    report += _build_portfolio_weights(ranked, macro_result)

    # Bottom 20 - Stocks to Sell/Avoid (placed before detailed analysis for visibility)
    report += _build_bottom_20(ranked)

    # Stock discovery section (weekly, on Mondays)
    if discovery_result and discovery_result.get("report"):
        report += discovery_result["report"]

    # Detailed analysis
    report += "\n---\n\n## Detailed Analysis\n\n"

    for r in ranked:
        sr = r["score_result"]
        ind = r.get("indicators", {})
        name = r.get("name", r["ticker"])
        sector = r.get("sector", "Unknown")

        report += f"### {r['ticker']} - {name}\n\n"
        report += f"**Sector:** {sector} | "
        report += f"**Score:** {sr['score']:.0f}/100 ({sr['grade']}) | "
        report += f"**Recommendation:** {sr['recommendation']} | "
        report += f"**Confidence:** {sr['confidence']*100:.0f}%\n\n"

        # Component scores
        c = sr["components"]
        report += "| Component | Score | Confidence | Weight |\n"
        report += "|-----------|-------|------------|--------|\n"
        for comp_name, comp_data in c.items():
            report += (
                f"| {comp_name.title()} "
                f"| {comp_data['score']:.0f}/100 "
                f"| {comp_data['confidence']*100:.0f}% "
                f"| {comp_data['weight']*100:.0f}% |\n"
            )

        # Key indicators
        if ind:
            report += f"\n**Price:** ${ind.get('price', 0):.2f}"
            if ind.get("change_1d") is not None:
                report += f" | **1D:** {ind.get('change_1d', 0):+.1f}%"
            if ind.get("change_1m") is not None:
                report += f" | **1M:** {ind.get('change_1m', 0):+.1f}%"
            if ind.get("change_3m") is not None:
                report += f" | **3M:** {ind.get('change_3m', 0):+.1f}%"
            if ind.get("rsi") is not None:
                report += f" | **RSI:** {ind.get('rsi', 0):.0f}"
            report += "\n"

        # Sentiment news summary
        sent_detail = sr.get("sentiment_detail", {})
        if sent_detail.get("reasoning"):
            report += f"\n**Sentiment Analysis** ({sent_detail.get('method', 'keyword').title()}):\n"
            report += f"{sent_detail['reasoning']}\n"
        if sent_detail.get("key_themes"):
            report += "\n**Key Themes:**\n"
            for theme in sent_detail["key_themes"]:
                report += f"- {theme}\n"
        if sent_detail.get("risk_flags"):
            report += "\n**Risk Flags:**\n"
            for flag in sent_detail["risk_flags"]:
                report += f"- {flag}\n"

        # Original news sources
        news_data = r.get("news_data", [])
        if news_data:
            report += "\n**Recent News:**\n"
            for article in news_data[:10]:
                title = article.get("title", "")
                publisher = article.get("publisher", "")
                link = article.get("link", "")
                pub_time = article.get("publish_time")
                age_str = ""
                if pub_time:
                    age_days = max(0, (datetime.now() - pub_time).total_seconds() / 86400)
                    if age_days < 1:
                        age_str = "today"
                    elif age_days < 2:
                        age_str = "1d ago"
                    else:
                        age_str = f"{age_days:.0f}d ago"
                pub_str = f" — {publisher}" if publisher else ""
                age_tag = f" [{age_str}]" if age_str else ""
                if link:
                    report += f"- [{title}]({link}){pub_str}{age_tag}\n"
                else:
                    report += f"- {title}{pub_str}{age_tag}\n"

        # Trend outlook from Claude synthesis
        trend_detail = sr.get("trend_detail", {})
        claude_synth = trend_detail.get("claude_synthesis", {})
        if claude_synth:
            report += "\n**Macro Trend Outlook:**\n"
            if claude_synth.get("regime_assessment"):
                report += f"- **Regime:** {claude_synth['regime_assessment']}\n"
            if claude_synth.get("momentum_assessment"):
                report += f"- **Momentum:** {claude_synth['momentum_assessment']}\n"
            if claude_synth.get("risk_assessment"):
                report += f"- **Risk:** {claude_synth['risk_assessment']}\n"
            if claude_synth.get("outlook"):
                report += f"- **Outlook:** {claude_synth['outlook']}\n"

        # Reasoning
        reasoning = sr.get("reasoning", [])
        if reasoning:
            report += "\n**Key Factors:**\n"
            for reason in reasoning:
                report += f"- {reason}\n"

        report += "\n---\n\n"

    # Footer
    report += """## Disclaimer

- This analysis is for informational purposes only, not financial advice.
- Past performance does not guarantee future results.
- Scores are based on quantitative analysis of technical, trend, sentiment, and alpha factors.
- Always do your own research before making investment decisions.
- Strategy is designed for macro buy-and-hold (weeks+ holding period).

---

*Generated by Trading Bot Recommendation Engine*
"""
    return report


def _build_macro_section(macro_result):
    """Build the daily macro assessment section."""
    if not macro_result:
        return ""

    score = macro_result.get("score", 50)
    rec = macro_result.get("recommendation", "Neutral")
    quant = macro_result.get("quant_score")
    claude = macro_result.get("claude_score")
    analysis = macro_result.get("analysis", {})
    market_data = macro_result.get("market_data", {})
    news = macro_result.get("news", [])

    # Score emoji based on recommendation
    if score >= 75:
        indicator = "STRONG BUY DAY"
    elif score >= 60:
        indicator = "BUY DAY"
    elif score >= 45:
        indicator = "NEUTRAL"
    elif score >= 30:
        indicator = "CAUTIOUS"
    else:
        indicator = "RISK OFF"

    section = f"## Daily Macro Assessment — {indicator}\n\n"
    section += f"**Macro Score: {score:.0f}/100** | **Action: {rec}**"
    if quant is not None:
        section += f" | Quant: {quant:.0f}"
    if claude is not None:
        section += f" | AI: {claude:.0f}"
    section += "\n\n"

    # Claude's summary
    if analysis.get("summary"):
        section += f"{analysis['summary']}\n\n"

    # Action guidance
    if analysis.get("action_guidance"):
        section += f"**Today's Guidance:** {analysis['action_guidance']}\n\n"

    # Market data table
    section += "### Market Snapshot\n\n"
    section += "| Index | Price | 1D | 1M | 3M | RSI |\n"
    section += "|-------|-------|-----|-----|-----|-----|\n"
    for ticker in ["SPY", "QQQ", "DIA", "IWM", "^VIX", "TLT", "GLD"]:
        info = market_data.get(ticker, {})
        if not info:
            continue
        name = info.get("name", ticker)
        price = info.get("price", 0)
        chg_1d = info.get("change_1d", 0)
        chg_1m = info.get("change_1m", 0)
        chg_3m = info.get("change_3m", 0)
        rsi = info.get("rsi", "—")
        rsi_str = f"{rsi:.0f}" if isinstance(rsi, (int, float)) else rsi
        section += f"| {name} | ${price:.2f} | {chg_1d:+.2f}% | {chg_1m:+.2f}% | {chg_3m:+.2f}% | {rsi_str} |\n"

    # Bull/Bear case
    if analysis.get("bull_case") or analysis.get("bear_case"):
        section += "\n"
        if analysis.get("bull_case"):
            section += f"**Bull Case:** {analysis['bull_case']}\n\n"
        if analysis.get("bear_case"):
            section += f"**Bear Case:** {analysis['bear_case']}\n\n"

    # Key catalysts and risks
    catalysts = analysis.get("key_catalysts", [])
    risks = analysis.get("key_risks", [])
    if catalysts:
        section += "**Positive Catalysts:**\n"
        for c in catalysts:
            section += f"- {c}\n"
        section += "\n"
    if risks:
        section += "**Key Risks:**\n"
        for r in risks:
            section += f"- {r}\n"
        section += "\n"

    # Cited news
    cited = analysis.get("cited_news", [])
    if cited:
        section += "**Key Market News Driving Today's Assessment:**\n"
        for headline in cited:
            section += f"- {headline}\n"
        section += "\n"

    # All recent market news
    if news:
        section += "### Recent Market News\n\n"
        for article in news[:12]:
            title = article.get("title", "")
            publisher = article.get("publisher", "")
            link = article.get("link", "")
            pub_time = article.get("publish_time")
            age_str = ""
            if pub_time:
                age_days = max(0, (datetime.now() - pub_time).total_seconds() / 86400)
                if age_days < 1:
                    age_str = "today"
                elif age_days < 2:
                    age_str = "1d ago"
                else:
                    age_str = f"{age_days:.0f}d ago"
            pub_str = f" — {publisher}" if publisher else ""
            age_tag = f" [{age_str}]" if age_str else ""
            if link:
                section += f"- [{title}]({link}){pub_str}{age_tag}\n"
            else:
                section += f"- {title}{pub_str}{age_tag}\n"

    section += "\n---\n"
    return section


def _build_bottom_20(ranked):
    """Build Bottom 20 Sell/Avoid section."""
    bottom = ranked[-20:] if len(ranked) >= 20 else ranked
    bottom = list(reversed(bottom))  # Worst first

    section = "\n---\n\n## Bottom 20 — Sell/Avoid\n\n"
    section += "| Rank | Ticker | Name | Score | Grade | Recommendation | Key Issue |\n"
    section += "|------|--------|------|-------|-------|----------------|-----------|\n"

    for i, r in enumerate(bottom, 1):
        sr = r["score_result"]
        name = r.get("name", r["ticker"])[:20]
        reasons = sr.get("reasoning", [])
        issue = reasons[0][:60] if reasons else "Weak multi-factor profile"
        section += (
            f"| {i} | **{r['ticker']}** | {name} "
            f"| {sr['score']:.0f} | {sr['grade']} "
            f"| {sr['recommendation']} "
            f"| {issue} |\n"
        )

    # Score breakdown for bottom 20
    section += "\n### Score Breakdown (Bottom 20)\n\n"
    section += "| Ticker | Technical | Trend | Alpha | Sentiment | Final |\n"
    section += "|--------|-----------|-------|-------|-----------|-------|\n"

    for r in bottom:
        sr = r["score_result"]
        c = sr["components"]
        section += (
            f"| **{r['ticker']}** "
            f"| {c['technical']['score']:.0f} "
            f"| {c['trend']['score']:.0f} "
            f"| {c['alpha']['score']:.0f} "
            f"| {c['sentiment']['score']:.0f} "
            f"| **{sr['score']:.0f}** |\n"
        )

    return section


def _build_portfolio_weights(ranked, macro_result=None):
    """
    Build suggested portfolio weights using concentrated top-N strategy.

    Strategy V6 (weight math lives in strategy.py, shared with live trading):
    - Select top 10 stocks by momentum (3m+1m composite)
    - Weight blend: 95% momentum ranking + 5% score^2
    - Macro overlay: cash reserve 0-20% based on macro conditions
    - Stocks outside top 10 get 0% allocation
    - Max position cap: 25%
    """
    import strategy

    if not ranked or len(ranked) < 2:
        return ""

    top_n = strategy.TOP_N
    max_pos = strategy.MAX_POS

    # --- Macro cash allocation (canonical, shared with live trading) ---
    macro_score = macro_result.get("score", 50) if macro_result else 50
    macro_rec = macro_result.get("recommendation", "Neutral") if macro_result else "Neutral"
    cash_pct = strategy.macro_cash_pct(macro_score)
    cash_label = strategy.macro_cash_label(cash_pct)

    # --- Momentum composite per stock (for display + selection) ---
    mom_data = {}
    for r in ranked:
        ind = r.get("indicators", {})
        m1 = ind.get("change_1m") / 100 if ind.get("change_1m") is not None else 0.0
        m3 = ind.get("change_3m") / 100 if ind.get("change_3m") is not None else 0.0
        mom_data[r["ticker"]] = strategy.momentum_composite(m1, m3)

    # --- Target weights via the single source of truth ---
    stocks = [{"ticker": r["ticker"], "score": r["score_result"]["score"],
               "mom": mom_data[r["ticker"]]} for r in ranked]
    weights_pct, cash_pct = strategy.compute_target_weights(stocks, macro_score=macro_score)
    # Expand to all tickers (0 for excluded) for the display tables below.
    final_weights = {r["ticker"]: weights_pct.get(r["ticker"], 0.0) for r in ranked}

    # Top N by momentum, for the "Mom Rank" display column
    top_by_mom = sorted([r["ticker"] for r in ranked],
                        key=lambda x: mom_data[x], reverse=True)[:top_n]

    # Sort by weight descending
    sorted_stocks = sorted(final_weights.keys(), key=lambda x: final_weights[x], reverse=True)

    # Build section
    section = "\n---\n\n## Suggested Portfolio Allocation\n\n"
    section += f"**Macro Score: {macro_score:.0f}/100 ({macro_rec})** | "
    section += f"**Cash Reserve: {cash_label}**\n\n"
    section += f"**Strategy V6:** Concentrated Top-{top_n} by Momentum | "
    section += "95% momentum + 5% score | "
    section += f"Max position: {max_pos:.0f}%\n\n"
    section += "Top 10 stocks selected by **momentum rank** (3-month + 1-month composite). "
    section += "Capital weighted toward the strongest momentum names with score as a minor quality tilt. "
    section += "Stocks outside top 10 are excluded (0% weight).\n\n"

    # Top holdings table (the ones that get allocation)
    allocated = [t for t in sorted_stocks if final_weights[t] > 0.1]
    not_allocated = [t for t in sorted_stocks if final_weights[t] <= 0.1]

    section += f"### Portfolio Holdings ({len(allocated)} stocks)\n\n"
    section += "| Rank | Ticker | Name | Weight | Score | 1M | 3M | Mom Rank |\n"
    section += "|------|--------|------|--------|-------|-----|-----|----------|\n"

    for i, t in enumerate(allocated, 1):
        r = next(x for x in ranked if x["ticker"] == t)
        sr = r["score_result"]
        ind = r.get("indicators", {})
        name = r.get("name", t)[:18]
        w = final_weights[t]
        m1 = ind.get("change_1m", 0)
        m3 = ind.get("change_3m", 0)
        mom_rank = top_by_mom.index(t) + 1 if t in top_by_mom else "-"

        section += (
            f"| {i} | **{t}** | {name} "
            f"| {w:.1f}% "
            f"| {sr['score']:.0f} "
            f"| {m1:+.1f}% "
            f"| {m3:+.1f}% "
            f"| #{mom_rank} |\n"
        )

    # Show total allocated
    total_allocated = sum(final_weights[t] for t in allocated)
    section += f"\n**Total Invested:** {total_allocated:.1f}% | "
    section += f"**Cash:** {cash_pct*100:.0f}%\n"

    # Not allocated (excluded from portfolio)
    if not_allocated:
        section += f"\n### Excluded from Portfolio ({len(not_allocated)} stocks — 0% weight)\n\n"
        section += "| Ticker | Name | Score | Recommendation | Reason |\n"
        section += "|--------|------|-------|----------------|--------|\n"
        for t in not_allocated[:30]:
            r = next(x for x in ranked if x["ticker"] == t)
            sr = r["score_result"]
            name = r.get("name", t)[:18]
            section += (
                f"| {t} | {name} "
                f"| {sr['score']:.0f} "
                f"| {sr['recommendation']} "
                f"| Outside top {top_n} by score |\n"
            )
        if len(not_allocated) > 30:
            section += f"\n*...and {len(not_allocated) - 30} more excluded stocks*\n"

    section += "\n"
    return section


def _build_alpaca_performance():
    """Build Alpaca paper/live portfolio performance section."""
    import os

    api_key = os.environ.get("ALPACA_API_KEY", "")
    api_secret = os.environ.get("ALPACA_SECRET_KEY", "")

    if not api_key or not api_secret:
        return ""

    try:
        from alpaca.trading.client import TradingClient

        client = TradingClient(api_key, api_secret, paper=True)
        account = client.get_account()
        positions = client.get_all_positions()

        equity = float(account.equity)
        cash = float(account.cash)
        buying_power = float(account.buying_power)

        # Calculate total P&L (assuming $100k starting capital)
        starting_capital = 100000.0
        total_pnl = equity - starting_capital
        total_return_pct = (equity / starting_capital - 1) * 100

        section = "## Portfolio Performance (Alpaca Paper)\n\n"
        section += f"**Equity:** ${equity:,.2f} | "
        section += f"**Cash:** ${cash:,.2f} | "
        section += f"**Invested:** ${equity - cash:,.2f}\n\n"
        section += f"**Total P&L:** ${total_pnl:+,.2f} ({total_return_pct:+.2f}%) | "
        section += f"**Starting Capital:** ${starting_capital:,.0f}\n\n"

        if not positions:
            section += "*No open positions*\n\n---\n"
            return section

        # Sort positions by market value descending
        positions.sort(key=lambda p: float(p.market_value), reverse=True)

        section += f"### Open Positions ({len(positions)})\n\n"
        section += "| Ticker | Qty | Avg Cost | Price | Mkt Value | P&L | P&L % | Weight |\n"
        section += "|--------|-----|----------|-------|-----------|-----|-------|--------|\n"

        total_market_value = sum(float(p.market_value) for p in positions)

        for p in positions:
            ticker = p.symbol
            qty = float(p.qty)
            avg_cost = float(p.avg_entry_price)
            current_price = float(p.current_price)
            market_value = float(p.market_value)
            unrealized_pl = float(p.unrealized_pl)
            unrealized_plpc = float(p.unrealized_plpc) * 100
            weight = (market_value / equity * 100) if equity > 0 else 0

            section += (
                f"| **{ticker}** "
                f"| {qty:.0f} "
                f"| ${avg_cost:.2f} "
                f"| ${current_price:.2f} "
                f"| ${market_value:,.0f} "
                f"| ${unrealized_pl:+,.0f} "
                f"| {unrealized_plpc:+.1f}% "
                f"| {weight:.1f}% |\n"
            )

        # Summary
        total_unrealized = sum(float(p.unrealized_pl) for p in positions)
        section += f"\n**Total Unrealized P&L:** ${total_unrealized:+,.2f} | "
        section += f"**Positions:** {len(positions)} | "
        section += f"**Cash Weight:** {cash / equity * 100:.1f}%\n"

        section += "\n---\n"
        return section

    except Exception as e:
        return f"## Portfolio Performance (Alpaca Paper)\n\n*Unable to fetch: {e}*\n\n---\n"


def _build_ai_semi_section(ranked):
    """Build the AI / Semiconductor / Chip / Storage thematic analysis (in Chinese).

    Groups stocks by their *config* sector (configs.UNIVERSE), not the coarse
    yfinance sector, so the AI-hardware taxonomy (Semiconductors, AI
    Infrastructure, Memory & Storage, etc.) is reliable.
    """
    if not ranked:
        return ""

    try:
        from configs import UNIVERSE
    except Exception:
        UNIVERSE = {}

    # Theme groups → set of config sectors, ordered hardware-first.
    GROUPS = [
        ("半导体 / 芯片", {"Semiconductors"}),
        ("存储 / 内存", {"AI Data Storage"}),
        ("AI 基础设施 / 数据中心", {"AI Infrastructure", "AI Cloud"}),
        ("AI 网络 / 光通信", {"Networking", "Photonics"}),
        ("AI 软件 / 数据", {"AI Software", "Data & Analytics", "AI Healthcare", "AI Cybersecurity"}),
    ]

    def cfg_sector(ticker):
        meta = UNIVERSE.get(ticker, {})
        return meta.get("sector", "")

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    # Bucket the in-scope stocks by group.
    in_scope_sectors = set().union(*[s for _, s in GROUPS])
    scoped = [r for r in ranked if cfg_sector(r["ticker"]) in in_scope_sectors]
    if not scoped:
        return ""

    section = "## AI / 半导体 / 芯片 / 存储 板块分析\n\n"

    # --- Complex-wide summary ---
    all_scores = [r["score_result"]["score"] for r in scoped]
    all_1m = [r.get("indicators", {}).get("change_1m") for r in scoped]
    all_3m = [r.get("indicators", {}).get("change_3m") for r in scoped]
    n_buy = sum(1 for r in scoped
                if r["score_result"]["recommendation"] in ("Strong Buy", "Buy"))
    avg_score = avg(all_scores) or 0
    avg_1m = avg(all_1m)
    avg_3m = avg(all_3m)

    # 板块整体定调
    if avg_score >= 65:
        mood = "强势（资金明显偏好该主题）"
    elif avg_score >= 55:
        mood = "偏强（多数标的处于上升趋势）"
    elif avg_score >= 45:
        mood = "中性（分化，需精选个股）"
    elif avg_score >= 35:
        mood = "偏弱（趋势走弱，注意减仓）"
    else:
        mood = "弱势（主题性回避）"

    section += (
        f"**主题整体：{mood}** —— 覆盖 {len(scoped)} 只标的，"
        f"平均评分 **{avg_score:.0f}/100**，"
        f"其中 Buy/Strong Buy 共 {n_buy} 只（占 {n_buy/len(scoped)*100:.0f}%）。"
    )
    if avg_1m is not None and avg_3m is not None:
        section += f" 平均涨幅：近 1 月 {avg_1m:+.1f}%、近 3 月 {avg_3m:+.1f}%。"
    section += "\n\n"

    # --- Per-group breakdown table ---
    section += "### 子板块概览\n\n"
    section += "| 子板块 | 标的数 | 平均分 | 平均 1M | 平均 3M | Buy 数 | 领涨标的 |\n"
    section += "|--------|-------|-------|---------|---------|-------|----------|\n"

    group_buckets = []
    for label, sectors in GROUPS:
        members = [r for r in scoped if cfg_sector(r["ticker"]) in sectors]
        if not members:
            continue
        group_buckets.append((label, members))
        g_score = avg([r["score_result"]["score"] for r in members]) or 0
        g_1m = avg([r.get("indicators", {}).get("change_1m") for r in members])
        g_3m = avg([r.get("indicators", {}).get("change_3m") for r in members])
        g_buy = sum(1 for r in members
                    if r["score_result"]["recommendation"] in ("Strong Buy", "Buy"))
        # 组内按 3 月动量排序取领涨
        leader = max(
            members,
            key=lambda r: (r.get("indicators", {}).get("change_3m") or -1e9),
        )
        leader_3m = leader.get("indicators", {}).get("change_3m")
        leader_str = f"{leader['ticker']}"
        if leader_3m is not None:
            leader_str += f" ({leader_3m:+.0f}%)"
        g_1m_str = f"{g_1m:+.1f}%" if g_1m is not None else "—"
        g_3m_str = f"{g_3m:+.1f}%" if g_3m is not None else "—"
        section += (
            f"| {label} | {len(members)} | {g_score:.0f} "
            f"| {g_1m_str} | {g_3m_str} | {g_buy} | {leader_str} |\n"
        )

    # --- Leaders / laggards across the whole complex (by 3M momentum) ---
    def mom3(r):
        return r.get("indicators", {}).get("change_3m")

    with_mom = [r for r in scoped if mom3(r) is not None]
    if with_mom:
        by_mom = sorted(with_mom, key=mom3, reverse=True)
        leaders = by_mom[:5]
        laggards = list(reversed(by_mom[-5:]))

        section += "\n### 主题内动量排行（近 3 月）\n\n"
        section += "**领涨 Top 5：**\n\n"
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|------|\n"
        for r in leaders:
            section += _ai_semi_row(r, cfg_sector(r["ticker"]))
        section += "\n**落后 Bottom 5：**\n\n"
        section += "| 标的 | 名称 | 子板块 | 评分 | 1M | 3M | 建议 |\n"
        section += "|------|------|--------|------|-----|-----|------|\n"
        for r in laggards:
            section += _ai_semi_row(r, cfg_sector(r["ticker"]))

    # --- Short Chinese takeaway ---
    section += "\n### 板块解读\n\n"
    strongest = max(group_buckets, key=lambda g: avg([x["score_result"]["score"] for x in g[1]]) or 0) if group_buckets else None
    weakest = min(group_buckets, key=lambda g: avg([x["score_result"]["score"] for x in g[1]]) or 0) if group_buckets else None
    if strongest and weakest and strongest[0] != weakest[0]:
        s_avg = avg([x["score_result"]["score"] for x in strongest[1]]) or 0
        w_avg = avg([x["score_result"]["score"] for x in weakest[1]]) or 0
        section += (
            f"- **最强子板块：{strongest[0]}**（平均分 {s_avg:.0f}），"
            f"**最弱子板块：{weakest[0]}**（平均分 {w_avg:.0f}）。\n"
        )
    if avg_3m is not None:
        if avg_3m > 10:
            section += "- 该主题近 3 月显著跑赢，动量策略会重仓此处，但需警惕高位回撤与估值透支。\n"
        elif avg_3m < -5:
            section += "- 该主题近 3 月走弱，趋势/动量信号转差，仓位上应保持谨慎、等待企稳确认。\n"
        else:
            section += "- 该主题近 3 月震荡分化，建议聚焦评分与动量同时居前的龙头，回避落后标的。\n"
    section += (
        "- 子板块判定依据 `configs.py` 的细分行业字段；"
        "评分综合技术、趋势、Alpha 与情绪四维度。仅供参考，非投资建议。\n"
    )

    section += "\n---\n"
    return section


def _ai_semi_row(r, sub_sector):
    """Render one Chinese-section table row for the AI/semi momentum tables."""
    sr = r["score_result"]
    ind = r.get("indicators", {})
    name = r.get("name", r["ticker"])[:16]
    m1 = ind.get("change_1m")
    m3 = ind.get("change_3m")
    m1_str = f"{m1:+.1f}%" if m1 is not None else "—"
    m3_str = f"{m3:+.1f}%" if m3 is not None else "—"
    rec_map = {
        "Strong Buy": "强烈买入", "Buy": "买入", "Hold": "持有",
        "Reduce": "减仓", "Avoid": "回避",
    }
    rec = rec_map.get(sr["recommendation"], sr["recommendation"])
    return (
        f"| **{r['ticker']}** | {name} | {sub_sector} "
        f"| {sr['score']:.0f} | {m1_str} | {m3_str} | {rec} |\n"
    )


def _build_market_overview(ranked):
    """Build a general market trend overview section from aggregated stock data."""
    if not ranked:
        return ""

    # Count regimes across all stocks
    regimes = {}
    trend_scores = []
    sentiment_scores = []
    claude_insights = []

    for r in ranked:
        sr = r["score_result"]
        regime = sr.get("regime", "Unknown")
        regimes[regime] = regimes.get(regime, 0) + 1

        comp = sr.get("components", {})
        if "trend" in comp:
            trend_scores.append(comp["trend"]["score"])
        if "sentiment" in comp:
            sentiment_scores.append(comp["sentiment"]["score"])

        # Collect unique Claude synthesis outlooks
        synth = sr.get("trend_detail", {}).get("claude_synthesis", {})
        if synth.get("outlook"):
            outlook = synth["outlook"]
            if outlook not in claude_insights:
                claude_insights.append(outlook)

    total = len(ranked)
    avg_trend = sum(trend_scores) / len(trend_scores) if trend_scores else 50
    avg_sent = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 50

    # Determine overall market sentiment label
    if avg_trend >= 65:
        market_mood = "Bullish"
    elif avg_trend >= 55:
        market_mood = "Moderately Bullish"
    elif avg_trend >= 45:
        market_mood = "Neutral"
    elif avg_trend >= 35:
        market_mood = "Moderately Bearish"
    else:
        market_mood = "Bearish"

    overview = "## Market Overview\n\n"
    overview += f"**Overall Market Mood:** {market_mood} (avg trend score: {avg_trend:.0f}/100)\n\n"

    # Regime distribution
    overview += "**Market Regime Distribution:**\n\n"
    overview += "| Regime | Stocks | % |\n"
    overview += "|--------|--------|---|\n"
    for regime, count in sorted(regimes.items(), key=lambda x: -x[1]):
        pct = count / total * 100
        overview += f"| {regime} | {count} | {pct:.0f}% |\n"

    overview += f"\n**Average Trend Score:** {avg_trend:.0f}/100 | "
    overview += f"**Average Sentiment Score:** {avg_sent:.0f}/100\n"

    # Key macro insights from Claude synthesis (show up to 5 unique)
    if claude_insights:
        overview += "\n**Key Macro Insights:**\n"
        for insight in claude_insights[:5]:
            # Truncate long insights
            text = insight[:200] + "..." if len(insight) > 200 else insight
            overview += f"- {text}\n"

    return overview


def _ticker_list(items, max_show=10):
    """Format ticker list for summary table."""
    tickers = [r["ticker"] for r in items[:max_show]]
    result = ", ".join(tickers)
    if len(items) > max_show:
        result += f", +{len(items) - max_show} more"
    return result if result else "-"
