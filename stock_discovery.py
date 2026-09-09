"""
Claude-powered stock discovery for dynamic universe management.

Runs periodically (e.g., weekly) to:
1. Identify emerging high-momentum stocks not in the current universe
2. Flag underperforming stocks to consider removing
3. Spot trending sectors/themes (AI, energy, biotech, etc.)

Uses Claude to synthesize market trends, then validates suggestions
with price data before recommending additions.
"""
import os
import json
import re
from datetime import datetime

import yfinance as yf
import pandas as pd
import numpy as np

# Load .env
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_SCRIPT_DIR, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def discover_stocks(current_universe, scored_results=None, macro_result=None):
    """
    Run stock discovery to find additions and removals.

    Args:
        current_universe: dict of {ticker: {name, sector}} from configs.py
        scored_results: latest scored results (optional, for removal candidates)
        macro_result: latest macro analysis (optional, for context)

    Returns:
        dict with:
            additions: list of {ticker, name, sector, reason, momentum}
            removals: list of {ticker, reason}
            themes: list of trending themes/sectors
            report: markdown summary
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("  No ANTHROPIC_API_KEY — skipping stock discovery")
        return None

    print("  Running Claude stock discovery...")

    current_tickers = sorted(current_universe.keys())

    # Step 1: Ask Claude for stock suggestions
    suggestions = _claude_discover(current_tickers, macro_result, api_key)
    if not suggestions:
        return None

    # Step 2: Validate suggested additions with real price data
    validated_additions = _validate_additions(
        suggestions.get("additions", []),
        current_tickers,
    )

    # Step 3: Identify removal candidates from scored results
    removal_candidates = _identify_removals(
        scored_results, current_universe, suggestions.get("removals", [])
    )

    # Step 4: Build report section
    report = _build_discovery_report(
        validated_additions, removal_candidates,
        suggestions.get("themes", []),
        suggestions.get("reasoning", ""),
    )

    return {
        "additions": validated_additions,
        "removals": removal_candidates,
        "themes": suggestions.get("themes", []),
        "report": report,
    }


def _claude_discover(current_tickers, macro_result, api_key):
    """Ask Claude to suggest stocks to add/remove."""
    import anthropic
    _ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    client = anthropic.Anthropic(
        api_key=api_key,
        default_headers={"anthropic-workspace-id": _ws} if _ws else None,
    )

    now = datetime.now()
    universe_str = ", ".join(current_tickers)

    macro_context = ""
    if macro_result:
        macro_context = f"""
Current macro environment:
- Macro score: {macro_result.get('score', 'N/A')}/100
- Recommendation: {macro_result.get('recommendation', 'N/A')}
- Analysis: {macro_result.get('analysis', {}).get('summary', 'N/A')}
"""

    prompt = f"""You are a senior equity research analyst. Your job is to review a stock universe and suggest additions and removals for a momentum-based trading strategy.

Today's date: {now.strftime('%Y-%m-%d')}

## Current Universe ({len(current_tickers)} stocks)
{universe_str}

{macro_context}

## Strategy Context
- Concentrated momentum strategy: picks top 10 stocks by 3-month + 1-month momentum
- Rebalances biweekly, holds for weeks-to-months
- Needs liquid stocks (>$500M market cap, >500K avg daily volume)
- Focuses on growth, technology, AI, and high-momentum sectors
- Also includes select value/defensive names for bear market rotation

## Your Task
1. **Suggest 5-10 stocks to ADD** that are NOT in the current universe but could be strong momentum candidates. Focus on:
   - Stocks with strong recent price momentum (last 1-3 months)
   - Emerging AI/tech companies gaining institutional attention
   - Stocks in booming sectors (energy infrastructure, defense, biotech breakthroughs)
   - Recent IPOs or spinoffs that have shown strong performance
   - International ADRs with strong momentum

2. **Suggest 3-5 stocks to REMOVE** from the current universe that:
   - Have persistently poor momentum with no catalysts
   - Are too illiquid or low market cap for the strategy
   - Have been delisted, acquired, or fundamentally broken
   - Are redundant (multiple ETFs/stocks covering same exposure)

3. **Identify 3-5 trending themes/sectors** that the portfolio should have exposure to.

Respond with ONLY a valid JSON object (no markdown, no code blocks):

{{
    "additions": [
        {{"ticker": "SYMBOL", "name": "Company Name", "sector": "Sector", "reason": "1-2 sentence reason why this stock should be added"}}
    ],
    "removals": [
        {{"ticker": "SYMBOL", "reason": "1-2 sentence reason why this stock should be removed"}}
    ],
    "themes": [
        "Theme 1: brief description",
        "Theme 2: brief description"
    ],
    "reasoning": "2-3 sentence summary of your overall market thesis and why these changes improve the universe"
}}

Guidelines:
- Only suggest US-listed stocks (including ADRs)
- Ticker symbols must be valid and currently trading
- Be specific about WHY each stock fits the momentum strategy
- Consider the current macro environment when suggesting defensive vs growth names
- Don't suggest stocks already in the universe
- For removals, only suggest stocks that truly don't belong — don't remove stocks just because they're temporarily down"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        result = json.loads(text)
        return result

    except Exception as e:
        print(f"  Claude discovery failed: {e}")
        return None


def _validate_additions(suggestions, current_tickers):
    """Validate suggested additions with real price data."""
    validated = []

    for s in suggestions:
        ticker = s.get("ticker", "").upper().strip()
        if not ticker or ticker in current_tickers:
            continue

        try:
            df = yf.Ticker(ticker).history(period="3mo", interval="1d")
            if df is None or df.empty or len(df) < 20:
                continue

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)

            close = df["Close"]
            volume = df["Volume"]
            current_price = float(close.iloc[-1])

            # Check liquidity: avg daily volume × price > $5M
            avg_vol = float(volume.tail(20).mean())
            avg_dollar_vol = avg_vol * current_price
            if avg_dollar_vol < 5_000_000:
                continue

            # Compute momentum
            mom_1m = 0
            mom_3m = 0
            if len(close) >= 21:
                mom_1m = (current_price / float(close.iloc[-21]) - 1) * 100
            if len(close) >= 63:
                mom_3m = (current_price / float(close.iloc[-63]) - 1) * 100
            elif len(close) >= 40:
                mom_3m = (current_price / float(close.iloc[0]) - 1) * 100

            validated.append({
                "ticker": ticker,
                "name": s.get("name", ticker),
                "sector": s.get("sector", "Unknown"),
                "reason": s.get("reason", ""),
                "price": round(current_price, 2),
                "momentum_1m": round(mom_1m, 2),
                "momentum_3m": round(mom_3m, 2),
                "avg_dollar_volume": round(avg_dollar_vol / 1_000_000, 1),
            })

        except Exception:
            continue

    # Sort by 3-month momentum
    validated.sort(key=lambda x: x["momentum_3m"], reverse=True)
    return validated


def _identify_removals(scored_results, current_universe, claude_removals):
    """Identify stocks to consider removing."""
    removals = []
    seen = set()

    # Claude's suggestions
    for r in claude_removals:
        ticker = r.get("ticker", "").upper()
        if ticker and ticker in current_universe and ticker not in seen:
            removals.append({
                "ticker": ticker,
                "reason": r.get("reason", "Claude recommendation"),
                "source": "claude",
            })
            seen.add(ticker)

    # Data-driven: stocks with consistently low scores
    if scored_results:
        for r in scored_results:
            ticker = r["ticker"]
            score = r["score_result"]["score"]
            # Flag stocks scoring below 30 (Avoid territory)
            if score < 30 and ticker not in seen:
                ind = r.get("indicators", {})
                mom_3m = ind.get("change_3m", 0)
                if mom_3m is not None and mom_3m < -20:
                    removals.append({
                        "ticker": ticker,
                        "reason": f"Score {score:.0f}/100, 3M momentum {mom_3m:+.1f}%",
                        "source": "data",
                    })
                    seen.add(ticker)

    return removals


def _build_discovery_report(additions, removals, themes, reasoning):
    """Build markdown report section."""
    section = "\n---\n\n## Stock Universe Discovery\n\n"

    if reasoning:
        section += f"*{reasoning}*\n\n"

    # Trending themes
    if themes:
        section += "### Trending Themes\n\n"
        for theme in themes:
            section += f"- {theme}\n"
        section += "\n"

    # Suggested additions
    if additions:
        section += f"### Suggested Additions ({len(additions)} stocks)\n\n"
        section += "| Ticker | Name | Sector | Price | 1M | 3M | Avg Vol($M) | Reason |\n"
        section += "|--------|------|--------|-------|-----|-----|-------------|--------|\n"
        for a in additions:
            section += (
                f"| **{a['ticker']}** "
                f"| {a['name'][:20]} "
                f"| {a['sector'][:15]} "
                f"| ${a['price']:.2f} "
                f"| {a['momentum_1m']:+.1f}% "
                f"| {a['momentum_3m']:+.1f}% "
                f"| {a['avg_dollar_volume']:.0f}M "
                f"| {a['reason'][:60]} |\n"
            )
        section += "\n"

    # Removal candidates
    if removals:
        section += f"### Removal Candidates ({len(removals)} stocks)\n\n"
        section += "| Ticker | Reason | Source |\n"
        section += "|--------|--------|--------|\n"
        for r in removals:
            section += f"| **{r['ticker']}** | {r['reason'][:80]} | {r['source']} |\n"
        section += "\n"

    if not additions and not removals:
        section += "*No changes suggested — current universe looks well-positioned.*\n\n"

    section += "> These are suggestions only. Review before adding/removing stocks from `configs.py`.\n"
    section += "\n---\n"
    return section
