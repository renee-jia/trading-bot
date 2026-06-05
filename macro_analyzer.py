"""
Macro market analysis for daily buy/sell/hold recommendation.

Analyzes overall market conditions by combining:
- Major index trends (SPY, QQQ, DIA)
- VIX fear/greed indicator
- Market-wide news sentiment (via Claude)
- Breadth and momentum signals

Produces a single macro score (0-100) with a daily action recommendation.
"""
import os
import json
import re
from datetime import datetime

import pandas as pd
import numpy as np
import yfinance as yf

# Load .env for API key
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_SCRIPT_DIR, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


def analyze_macro():
    """
    Run comprehensive macro analysis.

    Returns dict with:
        score: 0-100 macro score
        recommendation: "Strong Buy Day" / "Buy Day" / "Neutral" / "Cautious" / "Risk Off"
        market_data: dict of index/VIX metrics
        news: list of news items with titles and publishers
        analysis: Claude's comprehensive assessment
    """
    print("  Fetching macro market data...")

    # 1. Fetch major index data
    market_data = _fetch_market_data()

    # 2. Fetch market-wide news
    news_items = _fetch_market_news()

    # 3. Quantitative macro score
    quant_score, quant_signals = _quantitative_macro_score(market_data)

    # 4. Claude macro synthesis
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_result = None
    if api_key:
        try:
            claude_result = _claude_macro_analysis(
                market_data, news_items, quant_signals, api_key
            )
        except Exception as e:
            print(f"  Claude macro analysis failed: {e}")

    # Blend scores
    if claude_result:
        final_score = quant_score * 0.5 + claude_result["score"] * 0.5
    else:
        final_score = quant_score

    final_score = round(max(0, min(100, final_score)), 1)
    recommendation = _get_macro_recommendation(final_score)

    return {
        "score": final_score,
        "recommendation": recommendation,
        "quant_score": round(quant_score, 1),
        "claude_score": round(claude_result["score"], 1) if claude_result else None,
        "market_data": market_data,
        "news": news_items,
        "quant_signals": quant_signals,
        "analysis": claude_result or {},
    }


def _fetch_market_data():
    """Fetch data for major indices and VIX."""
    data = {}
    tickers = {
        "SPY": "S&P 500",
        "QQQ": "Nasdaq 100",
        "DIA": "Dow Jones",
        "IWM": "Russell 2000",
        "^VIX": "VIX",
        "TLT": "20Y Treasury Bond",
        "GLD": "Gold",
        "UUP": "US Dollar",
    }

    for ticker, name in tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="6mo", interval="1d")
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                close = df["Close"]
                current = float(close.iloc[-1])

                info = {"name": name, "price": round(current, 2)}

                # Changes
                if len(close) >= 2:
                    info["change_1d"] = round((current / float(close.iloc[-2]) - 1) * 100, 2)
                if len(close) >= 6:
                    info["change_5d"] = round((current / float(close.iloc[-6]) - 1) * 100, 2)
                if len(close) >= 22:
                    info["change_1m"] = round((current / float(close.iloc[-22]) - 1) * 100, 2)
                if len(close) >= 63:
                    info["change_3m"] = round((current / float(close.iloc[-63]) - 1) * 100, 2)

                # SMA
                if len(close) >= 50:
                    info["sma_50"] = round(float(close.rolling(50).mean().iloc[-1]), 2)
                    info["above_sma50"] = current > info["sma_50"]
                if len(close) >= 100:
                    sma200 = float(close.rolling(100).mean().iloc[-1])  # Use 100 since we have 6mo
                    info["above_sma100"] = current > sma200

                # RSI
                if len(close) >= 15:
                    delta = close.diff()
                    gain = delta.where(delta > 0, 0).rolling(14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else 100
                    info["rsi"] = round(100 - (100 / (1 + rs)), 1)

                data[ticker] = info
        except Exception:
            pass

    return data


def _fetch_market_news():
    """Fetch general market news from major indices."""
    all_news = []
    seen_titles = set()
    now = datetime.now()

    for ticker in ["SPY", "QQQ", "^DJI"]:
        try:
            t = yf.Ticker(ticker)
            raw = t.news or []
            for item in raw[:15]:
                content = item.get("content", item)
                title = content.get("title", "") or item.get("title", "")
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)

                provider = content.get("provider", {})
                publisher = provider.get("displayName", "") if isinstance(provider, dict) else item.get("publisher", "")

                pub_dt = now
                pub_date_str = content.get("pubDate", "")
                pub_time = item.get("providerPublishTime", 0)
                if pub_date_str:
                    try:
                        pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                    except (ValueError, AttributeError):
                        pass
                elif pub_time:
                    try:
                        pub_dt = datetime.fromtimestamp(pub_time)
                    except (OSError, ValueError):
                        pass

                # Link
                canonical = content.get("canonicalUrl", {})
                if isinstance(canonical, dict):
                    link = canonical.get("url", "")
                else:
                    link = item.get("link", "")

                all_news.append({
                    "title": title,
                    "publisher": publisher,
                    "publish_time": pub_dt,
                    "link": link,
                })
        except Exception:
            pass

    # Sort by recency
    all_news.sort(key=lambda x: x["publish_time"], reverse=True)
    return all_news[:20]


def _quantitative_macro_score(market_data):
    """Compute quantitative macro score from index data."""
    scores = []
    signals = {}

    spy = market_data.get("SPY", {})
    qqq = market_data.get("QQQ", {})
    vix = market_data.get("^VIX", {})
    iwm = market_data.get("IWM", {})
    tlt = market_data.get("TLT", {})

    # 1. SPY trend (25%)
    if spy:
        spy_score = 50
        if spy.get("above_sma50"):
            spy_score += 10
        change_1m = spy.get("change_1m", 0)
        spy_score += max(-15, min(15, change_1m * 1.5))
        rsi = spy.get("rsi", 50)
        if rsi > 70:
            spy_score -= 5  # Overbought
        elif rsi < 30:
            spy_score += 10  # Oversold = buying opportunity
        scores.append(("spy_trend", max(0, min(100, spy_score)), 0.25))
        signals["spy_trend"] = round(spy_score, 1)
        signals["spy_change_1d"] = spy.get("change_1d", 0)
        signals["spy_change_1m"] = change_1m
        signals["spy_rsi"] = rsi

    # 2. VIX (20%)
    if vix:
        vix_level = vix.get("price", 20)
        signals["vix_level"] = vix_level
        signals["vix_change_1d"] = vix.get("change_1d", 0)

        if vix_level < 13:
            vix_score = 55  # Complacency
        elif vix_level < 18:
            vix_score = 70  # Low vol, healthy
        elif vix_level < 25:
            vix_score = 40  # Elevated caution
        elif vix_level < 35:
            vix_score = 30  # High fear
        else:
            vix_score = 45  # Panic = contrarian buy

        # VIX direction matters
        vix_1d = vix.get("change_1d", 0)
        if vix_1d < -5:
            vix_score += 10  # Fear subsiding
        elif vix_1d > 10:
            vix_score -= 10  # Fear spiking

        scores.append(("vix", max(0, min(100, vix_score)), 0.20))
        signals["vix_score"] = round(vix_score, 1)

    # 3. Market breadth - compare SPY vs IWM (15%)
    if spy and iwm:
        spy_1m = spy.get("change_1m", 0)
        iwm_1m = iwm.get("change_1m", 0)
        breadth_diff = iwm_1m - spy_1m  # Positive = small caps outperforming = healthy
        breadth_score = 50 + breadth_diff * 2
        scores.append(("breadth", max(0, min(100, breadth_score)), 0.15))
        signals["breadth_score"] = round(breadth_score, 1)
        signals["iwm_vs_spy_1m"] = round(breadth_diff, 2)

    # 4. Tech momentum - QQQ (20%)
    if qqq:
        qqq_score = 50
        qqq_1m = qqq.get("change_1m", 0)
        qqq_score += max(-20, min(20, qqq_1m * 1.5))
        if qqq.get("above_sma50"):
            qqq_score += 8
        scores.append(("tech_momentum", max(0, min(100, qqq_score)), 0.20))
        signals["qqq_change_1m"] = qqq_1m
        signals["qqq_score"] = round(qqq_score, 1)

    # 5. Bond/rate signal - TLT (10%)
    if tlt:
        tlt_1m = tlt.get("change_1m", 0)
        # Rising bonds (falling yields) = bullish for stocks
        tlt_score = 50 + tlt_1m * 3
        scores.append(("rates", max(0, min(100, tlt_score)), 0.10))
        signals["tlt_change_1m"] = tlt_1m
        signals["rates_score"] = round(tlt_score, 1)

    # 6. Risk appetite - Gold vs SPY (10%)
    gld = market_data.get("GLD", {})
    if gld and spy:
        gld_1m = gld.get("change_1m", 0)
        spy_1m = spy.get("change_1m", 0)
        # Gold outperforming = risk-off = bearish for stocks
        risk_diff = spy_1m - gld_1m
        risk_score = 50 + risk_diff * 2
        scores.append(("risk_appetite", max(0, min(100, risk_score)), 0.10))
        signals["risk_appetite_score"] = round(risk_score, 1)

    if not scores:
        return 50, signals

    total_weight = sum(w for _, _, w in scores)
    macro_score = sum(s * w for _, s, w in scores) / total_weight
    return macro_score, signals


def _claude_macro_analysis(market_data, news_items, quant_signals, api_key):
    """Use Claude to produce a comprehensive macro market assessment."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    now = datetime.now()

    # Build market data summary
    market_lines = []
    for ticker, info in market_data.items():
        name = info.get("name", ticker)
        price = info.get("price", 0)
        chg_1d = info.get("change_1d", 0)
        chg_1m = info.get("change_1m", 0)
        rsi = info.get("rsi", "N/A")
        above50 = "above" if info.get("above_sma50") else "below"
        market_lines.append(
            f"- {name} ({ticker}): ${price:.2f}, 1D: {chg_1d:+.2f}%, 1M: {chg_1m:+.2f}%, RSI: {rsi}, {above50} 50-SMA"
        )
    market_block = "\n".join(market_lines)

    # Build news summary
    news_lines = []
    for item in news_items[:15]:
        title = item["title"]
        publisher = item.get("publisher", "")
        age = (now - item["publish_time"]).total_seconds() / 86400
        age_str = "today" if age < 1 else f"{age:.0f}d ago"
        pub = f" — {publisher}" if publisher else ""
        news_lines.append(f"- [{age_str}]{pub} {title}")
    news_block = "\n".join(news_lines)

    # Quant signals
    signal_lines = [f"- {k}: {v}" for k, v in quant_signals.items()]
    signal_block = "\n".join(signal_lines)

    prompt = f"""You are a senior macro strategist at a hedge fund. Analyze the current market conditions and provide a daily macro assessment for a buy-and-hold investor.

Today's date: {now.strftime('%Y-%m-%d')}

## Market Data
{market_block}

## Quantitative Signals
{signal_block}

## Recent Market News
{news_block}

Also factor in your knowledge of:
- **Federal Reserve policy**: Current rate stance, QT/QE status, upcoming FOMC meetings
- **Geopolitical risks**: Wars, trade tensions, sanctions, oil supply disruptions
- **Economic data**: Recent GDP, jobs, inflation, PMI readings
- **Liquidity conditions**: Fed balance sheet, reverse repo, credit spreads
- **Market sentiment**: AAII survey, put/call ratio, fund flows, Fear & Greed index
- **Seasonal patterns**: Time of year, earnings season, tax season effects

Respond with ONLY a valid JSON object (no markdown, no code blocks):

{{
  "score": <float 0-100, overall macro favorability. 75+ = strong buy day, 60-74 = buy day, 45-59 = neutral, 30-44 = cautious, <30 = risk off>,
  "confidence": <float 0.0-1.0>,
  "summary": <string, 2-3 sentence executive summary of today's macro environment>,
  "bull_case": <string, 1-2 sentences on what's working for bulls>,
  "bear_case": <string, 1-2 sentences on what's worrying bears>,
  "key_risks": <list of strings, top 3-5 macro risks right now>,
  "key_catalysts": <list of strings, top 3-5 positive catalysts>,
  "action_guidance": <string, 1-2 sentences of specific guidance: should investors be adding, holding, or trimming today?>,
  "cited_news": <list of strings, 3-5 most impactful news headlines from the list above that drive your assessment>
}}

Guidelines:
- Be specific and actionable, not vague.
- Score of 50 = truly neutral. Don't default to neutral — take a stance.
- If VIX is spiking AND indices are falling, the near-term outlook is negative even if it's a contrarian buy signal.
- If the Fed is cutting rates, that's a major tailwind. If hiking, major headwind.
- Cite the actual news headlines that matter most.
- Consider both the near-term (today/this week) and medium-term (1-3 months) outlook.
- A day with bad news but oversold conditions could still be a "buy day" for long-term investors."""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    result = json.loads(text)
    result["score"] = max(0, min(100, float(result["score"])))
    result["confidence"] = max(0, min(1, float(result["confidence"])))
    return result


def _get_macro_recommendation(score):
    """Map macro score to daily action recommendation."""
    if score >= 75:
        return "Strong Buy Day"
    elif score >= 60:
        return "Buy Day"
    elif score >= 45:
        return "Neutral"
    elif score >= 30:
        return "Cautious"
    else:
        return "Risk Off"
