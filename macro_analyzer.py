"""
Macro market analysis for daily buy/sell/hold recommendation.

Analyzes overall market conditions by combining:
- Major index trends (SPY, QQQ, DIA)
- VIX fear/greed indicator
- Market-wide news sentiment (via Claude)
- Breadth and momentum signals

Produces a single macro score (0-100) with a daily action recommendation
plus a horizoned stock-trend outlook (1 week / 1 month / 6 months) used
by the daily report's General section.
"""
import os
import time
import json
import re
from datetime import datetime

import pandas as pd
import market_bars
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



def _llm_log(payload):
    """One-line JSON record of every LLM call / blend / clamp (Cloud Run stdout)."""
    try:
        print("LLMLOG " + json.dumps(payload, ensure_ascii=False, default=str))
    except Exception:
        pass


def analyze_macro():
    """
    Run comprehensive macro analysis.

    Returns dict with:
        score: 0-100 macro score
        recommendation: "Strong Buy Day" / "Buy Day" / "Neutral" / "Cautious" / "Risk Off"
        market_data: dict of index/VIX metrics
        news: list of news items with titles and publishers
        analysis: Claude's comprehensive assessment
        trend: mechanical index vs internals classification
        trend_outlook: 1w/1m/6m view and add-vs-sell stance
    """
    print("  Fetching macro market data...")

    # 1. Fetch major index data (1y so 200-SMA and drawdown-from-high are real)
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
            _llm_log({"kind": "macro", "status": "fallback", "error": str(e)[:200]})

    # Blend scores
    if claude_result:
        final_score = quant_score * 0.5 + claude_result["score"] * 0.5
    else:
        final_score = quant_score
    _llm_log({"kind": "macro_blend", "quant_score": round(quant_score, 1),
              "claude_score": round(claude_result["score"], 1) if claude_result else None,
              "final_score": round(max(0, min(100, final_score)), 1),
              "source": "claude" if claude_result else "fallback"})

    final_score = round(max(0, min(100, final_score)), 1)
    recommendation = _get_macro_recommendation(final_score)

    trend = classify_market_trend(market_data)
    outlook = merge_trend_outlook(trend, final_score, market_data, claude_result)
    tape = classify_macro_tape(market_data)
    import cash_entry_plan
    cash_plan = cash_entry_plan.from_market_data(market_data)

    return {
        "score": final_score,
        "recommendation": recommendation,
        "quant_score": round(quant_score, 1),
        "claude_score": round(claude_result["score"], 1) if claude_result else None,
        "market_data": market_data,
        "news": news_items,
        "quant_signals": quant_signals,
        "analysis": claude_result or {},
        "trend": trend,
        "trend_outlook": outlook,
        "macro_tape": tape,
        "cash_entry_plan": cash_plan,
    }


def _fetch_market_data():
    """Fetch data for major indices and VIX."""
    data = {}
    cash_histories = {}
    tickers = {
        "SPY": "S&P 500 ETF",
        "^GSPC": "S&P 500 Price Index",
        "QQQ": "Nasdaq 100",
        "DIA": "Dow Jones",
        "IWM": "Russell 2000",
        "SMH": "Semiconductors",
        "XLE": "Energy",
        "^VIX": "VIX",
        "TLT": "20Y Treasury Bond",
        "^IRX": "3M Yield",
        "^FVX": "5Y Yield",
        "^TNX": "10Y Yield",
        "^TYX": "30Y Yield",
        "GLD": "Gold",
        "UUP": "US Dollar",
        "USO": "WTI Oil ETF",
        "HYG": "High Yield Credit",
    }

    for ticker, name in tickers.items():
        try:
            df = yf.Ticker(ticker).history(period="1y", interval="1d")
            df = market_bars.drop_unfinished_bars(df)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                if ticker in ("^GSPC", "^VIX"):
                    cash_histories[ticker] = df
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

                hi = float(close.max())
                if hi > 0:
                    info["from_high"] = round((current / hi - 1) * 100, 2)

                # SMA
                if len(close) >= 50:
                    info["sma_50"] = round(float(close.rolling(50).mean().iloc[-1]), 2)
                    info["above_sma50"] = current > info["sma_50"]
                if len(close) >= 200:
                    sma200 = float(close.rolling(200).mean().iloc[-1])
                    info["sma_200"] = round(sma200, 2)
                    info["above_sma200"] = current > sma200
                elif len(close) >= 100:
                    sma100 = float(close.rolling(100).mean().iloc[-1])
                    info["above_sma100"] = current > sma100

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

    if '^GSPC' in data:
        import cash_entry_plan
        data['^GSPC']['cash_plan_snapshot'] = cash_entry_plan.snapshot_from_history(
            cash_entry_plan.completed_daily_history(cash_histories.get('^GSPC')),
            cash_entry_plan.completed_daily_history(cash_histories.get('^VIX')))
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
    _ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    client = anthropic.Anthropic(
        api_key=api_key,
        default_headers={"anthropic-workspace-id": _ws} if _ws else None,
    )

    now = datetime.now()

    # Build market data summary
    market_lines = []
    for ticker, info in market_data.items():
        name = info.get("name", ticker)
        price = info.get("price", 0)
        chg_1d = info.get("change_1d", 0)
        chg_5d = info.get("change_5d")
        chg_1m = info.get("change_1m", 0)
        rsi = info.get("rsi", "N/A")
        above50 = "above" if info.get("above_sma50") else "below"
        from_hi = info.get("from_high")
        extra = ""
        if chg_5d is not None:
            extra += f", 5D: {chg_5d:+.2f}%"
        if from_hi is not None:
            extra += f", vs 1y high: {from_hi:+.2f}%"
        market_lines.append(
            f"- {name} ({ticker}): ${price:.2f}, 1D: {chg_1d:+.2f}%{extra}, 1M: {chg_1m:+.2f}%, RSI: {rsi}, {above50} 50-SMA"
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

    classified = classify_market_trend(market_data)
    events = _upcoming_macro_events()
    events_block = "\n".join(f"- {d.isoformat()} {label}" for d, label in events) or "- none listed"

    prompt = f"""You are a senior macro strategist at a hedge fund. Analyze the current market conditions and provide a daily macro assessment for a buy-and-hold investor.

Today's date: {now.strftime('%Y-%m-%d')}

## Market Data
{market_block}

## Quantitative Signals
{signal_block}

## Mechanical trend classification (use this; do not contradict the drawdown math)
- Phase: {classified.get('phase')}
- Label: {classified.get('label')}
- Structure: {classified.get('structure')}
- SPY vs 1y high: {classified.get('spy_from_high')}%
- QQQ vs 1y high: {classified.get('qqq_from_high')}%
- SMH vs 1y high: {classified.get('smh_from_high')}%
- SPY above 200-SMA: {classified.get('above_sma200')}
- VIX: {classified.get('vix')}

## Upcoming high-impact US prints (next few)
{events_block}

## Recent Market News
{news_block}

Also factor in your knowledge of:
- **Federal Reserve policy**: Current rate stance, QT/QE status, upcoming FOMC meetings, Jackson Hole
- **Geopolitical risks**: Wars, trade tensions, sanctions, oil supply disruptions
- **Economic data**: Recent GDP, jobs, inflation, PMI readings
- **Liquidity conditions**: Fed balance sheet, reverse repo, credit spreads
- **Market sentiment**: AAII survey, put/call ratio, fund flows, Fear & Greed index
- **Seasonal patterns**: Time of year, earnings season, September seasonality

Respond with ONLY a valid JSON object (no markdown, no code blocks):

{{
  "score": <float 0-100, overall macro favorability. 75+ = strong buy day, 60-74 = buy day, 45-59 = neutral, 30-44 = cautious, <30 = risk off>,
  "confidence": <float 0.0-1.0>,
  "summary": <string, 2-3 sentence executive summary of today's macro environment, English>,
  "bull_case": <string, 1-2 sentences on what's working for bulls, English>,
  "bear_case": <string, 1-2 sentences on what's worrying bears, English>,
  "key_risks": <list of strings, top 3-5 macro risks right now, English>,
  "key_catalysts": <list of strings, top 3-5 positive catalysts, English>,
  "action_guidance": <string, 1-2 sentences of specific guidance: should investors be adding, holding, or trimming today?, English>,
  "cited_news": <list of strings, 3-5 most impactful news headlines from the list above that drive your assessment>,
  "current_trend": <string, Chinese, 3-5 sentences: is this a crash, a pullback, a rotation, or an uptrend? cite index vs internals (semis/energy/yields)>,
  "outlook_1w": {{"direction": "up"|"down"|"range", "view": "<Chinese, 2-3 sentences, name the events that matter this week>"}},
  "outlook_1m": {{"direction": "up"|"down"|"range", "view": "<Chinese, 2-3 sentences through the next FOMC/CPI window>"}},
  "outlook_6m": {{"direction": "up"|"down"|"range", "view": "<Chinese, 2-3 sentences, base case not a price target>"}},
  "stance": "aggressive_buy"|"scale_in"|"hold"|"trim"|"aggressive_sell",
  "sizing_guidance": <string, Chinese, 3-5 sentences: what fraction of planned capital to deploy NOW; whether to sell hard or buy the dip; what NOT to do>
}}

Guidelines:
- Be specific and actionable, not vague.
- Score of 50 = truly neutral. Don't default to neutral — take a stance.
- If VIX is spiking AND indices are falling, the near-term outlook is negative even if it's a contrarian buy signal.
- If the Fed is cutting rates, that's a major tailwind. If hiking, major headwind.
- Cite the actual news headlines that matter most.
- **stance rules:**
  - aggressive_sell = index in a confirmed downtrend / crash AND you want net selling. Rare. Not for a 1-3% pullback from highs.
  - trim = reduce risk, stop new buys. Use when yields/oil are the driver and growth is crowded.
  - hold = do not chase, do not panic-sell.
  - scale_in = buy weakness in slices (typically 20-30% of planned capital now). Default when it is a dip inside an uptrend or an internal rotation, NOT a crash.
  - aggressive_buy = only if VIX is panicked (roughly 28+) AND the index is already in a double-digit drawdown. Never for a one-day chip selloff while SPY is near highs.
- Distinguish index trend from internals. Equal-weight/energy up + semis down is rotation, not a reason to dump the whole book or to all-in semiconductors.
- September is historically the weakest month; do not treat that as a crash signal by itself."""

    _t0 = time.time()
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}],
    )
    _latency = round(time.time() - _t0, 3)
    _usage = getattr(response, "usage", None)
    _meta = {
        "kind": "macro", "model": "claude-haiku-4-5", "latency_s": _latency,
        "in_tokens": getattr(_usage, "input_tokens", None),
        "out_tokens": getattr(_usage, "output_tokens", None),
        "stop_reason": getattr(response, "stop_reason", None),
        "quant_signals": quant_signals, "mechanical_phase": classified.get("phase"),
        "mechanical_structure": classified.get("structure"),
    }

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        result = json.loads(text)
        result["score"] = max(0, min(100, float(result["score"])))
        result["confidence"] = max(0, min(1, float(result["confidence"])))
    except Exception as e:
        _llm_log({**_meta, "status": "parse_error", "error": str(e)[:200],
                  "raw_head": text[:120]})
        raise
    # Full model output is kept verbatim for the reasoning analysis.
    _llm_log({**_meta, "status": "ok", "result": result})
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


STANCE_LABELS = {
    "aggressive_buy": "大量买入抄底",
    "scale_in": "分批逢低买，不要一次打满",
    "hold": "持有观望，不追不砸",
    "trim": "减仓、停止新开仓",
    "aggressive_sell": "尽量卖出、降低风险预算",
}

HORIZON_DIR_LABELS = {
    "up": "偏多",
    "down": "偏空",
    "range": "震荡",
}

_PHASE_LABELS = {
    "bear_market": "熊市（指数距高点超过 20%）",
    "correction": "调整（距高点 10–20%）",
    "pullback": "回撤（距高点 3–10%）",
    "uptrend": "上升趋势",
    "range": "震荡",
}

_STRUCTURE_LABELS = {
    "internal_rotation": "指数还好，成长/芯片内部杀估值",
    "growth_underperforms": "成长落后于大盘",
    "broad": "涨跌比较同步",
}


def _upcoming_macro_events(n=6, today=None):
    """Next high-impact US prints, if the sell-put calendar is importable."""
    try:
        from sell_put_advisor import HIGH_IMPACT_MACRO
        today = today or datetime.now().date()
        upcoming = [(d, label) for d, label in HIGH_IMPACT_MACRO if d >= today]
        return upcoming[:n]
    except Exception:
        return []


VOL_LABELS = {
    "complacent": "过冷，市场太安静",
    "calm": "正常偏低",
    "elevated": "抬升",
    "stress": "紧张",
    "panic": "恐慌",
    "unknown": "未知",
}

RATES_LABELS = {
    "tighter": "利率在收紧",
    "easier": "利率在放松",
    "stable_tight": "利率高位稳定",
    "unknown": "未知",
}

DOLLAR_LABELS = {
    "strong": "美元走强",
    "weak": "美元走弱",
    "flat": "美元平稳",
    "unknown": "未知",
}

RISK_LABELS = {
    "risk_on": "风险偏好打开",
    "risk_off": "避险",
    "mixed": "风险偏好分化",
    "unknown": "未知",
}


def _md_get(market_data, ticker, key, default=None):
    info = (market_data or {}).get(ticker) or {}
    val = info.get(key, default)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def classify_macro_tape(market_data):
    """Mechanical read of rates, dollar, vol, credit, and risk appetite.

    Safe with a partial market_data dict. Does not call the network.
    Yield % changes from yfinance are percent-of-level (4.72 → 4.80 is
    about +1.7%), roughly 4–5 bp per 1% at current 10Y levels.
    """
    vix = _md_get(market_data, "^VIX", "price")
    vix_5d = _md_get(market_data, "^VIX", "change_5d")
    tnx = _md_get(market_data, "^TNX", "price")
    tyx = _md_get(market_data, "^TYX", "price")
    irx = _md_get(market_data, "^IRX", "price")
    fvx = _md_get(market_data, "^FVX", "price")
    tnx_1d = _md_get(market_data, "^TNX", "change_1d")
    tnx_5d = _md_get(market_data, "^TNX", "change_5d")
    uup_5d = _md_get(market_data, "UUP", "change_5d")
    uup_1d = _md_get(market_data, "UUP", "change_1d")
    gld_5d = _md_get(market_data, "GLD", "change_5d")
    uso_5d = _md_get(market_data, "USO", "change_5d")
    xle_5d = _md_get(market_data, "XLE", "change_5d")
    hyg_5d = _md_get(market_data, "HYG", "change_5d")
    spy_5d = _md_get(market_data, "SPY", "change_5d")
    iwm_5d = _md_get(market_data, "IWM", "change_5d")
    smh_5d = _md_get(market_data, "SMH", "change_5d")
    tlt_5d = _md_get(market_data, "TLT", "change_5d")

    if vix is None:
        vol = "unknown"
    elif vix < 13:
        vol = "complacent"
    elif vix < 18:
        vol = "calm"
    elif vix < 25:
        vol = "elevated"
    elif vix < 28:
        vol = "stress"
    else:
        vol = "panic"

    if tnx_5d is not None and tnx_5d >= 2.0:
        rates = "tighter"
    elif tnx_5d is not None and tnx_5d <= -2.0:
        rates = "easier"
    elif tnx is not None and tnx >= 4.5:
        rates = "stable_tight"
    elif tnx is not None:
        rates = "easier" if (tnx_5d or 0) < 0 else "stable_tight"
    else:
        rates = "unknown"

    if uup_5d is not None and uup_5d >= 1.0:
        dollar = "strong"
    elif uup_5d is not None and uup_5d <= -1.0:
        dollar = "weak"
    elif uup_5d is not None:
        dollar = "flat"
    else:
        dollar = "unknown"

    oil_heat = None
    if uso_5d is not None:
        oil_heat = uso_5d
    elif xle_5d is not None:
        oil_heat = xle_5d

    curve_10_3 = None
    curve_state = "unknown"
    if tnx is not None and irx is not None:
        curve_10_3 = round(tnx - irx, 2)
        if curve_10_3 < 0:
            curve_state = "inverted"
        elif curve_10_3 < 0.30:
            curve_state = "flat"
        else:
            curve_state = "normal"

    off_votes = 0
    on_votes = 0
    if vol in ("elevated", "stress", "panic"):
        off_votes += 1
    if vol in ("calm",) and (vix_5d is None or vix_5d <= 0):
        on_votes += 1
    if vol == "complacent":
        on_votes += 1
    if rates == "tighter":
        off_votes += 1
    if rates == "easier":
        on_votes += 1
    if dollar == "strong":
        off_votes += 1
    if dollar == "weak":
        on_votes += 1
    if spy_5d is not None and spy_5d <= -2:
        off_votes += 1
    if spy_5d is not None and spy_5d >= 1:
        on_votes += 1
    if iwm_5d is not None and spy_5d is not None and iwm_5d < spy_5d - 1.5:
        off_votes += 1
    if hyg_5d is not None and hyg_5d <= -1:
        off_votes += 1
    if tlt_5d is not None and tlt_5d >= 1.5 and (spy_5d is not None and spy_5d <= 0):
        off_votes += 1

    if off_votes >= on_votes + 2 and off_votes >= 2:
        risk = "risk_off"
    elif on_votes >= off_votes + 2 and on_votes >= 2:
        risk = "risk_on"
    else:
        risk = "mixed"

    if vol == "panic" and rates != "tighter":
        implication = (
            "波动率已经恐慌，但利率没有再收紧——这是分批的宏观条件，不是清仓条件。"
        )
        stock_action = "scale_in"
        options_action = "wait"
    elif vol == "complacent" and rates in ("tighter", "stable_tight"):
        implication = (
            "波动率过冷、利率仍紧：指数容易在数据日突然补波动。股票不追高，"
            "期权不要卖太短的权利金去赌平静会持续。"
        )
        stock_action = "hold"
        options_action = "caution"
    elif rates == "tighter" and dollar == "strong":
        implication = (
            "美元和长端一起紧，成长股和芯片会先挨打。停止加高 beta，"
            "不要用短 put 去接下跌中的半导体。"
        )
        stock_action = "trim"
        options_action = "no_new_short"
    elif risk == "risk_off":
        implication = (
            "宏观带已经偏向避险。先降新开仓，已有仓位用权利金或减高波动，而不是无差别砍底仓。"
        )
        stock_action = "trim"
        options_action = "caution"
    elif risk == "risk_on" and vol in ("calm", "complacent"):
        implication = (
            "风险偏好还在，但位置不便宜。股票持有，加仓等回撤；期权以 covered call 为主，不买短 call。"
        )
        stock_action = "hold"
        options_action = "sell_call_ok"
    else:
        implication = (
            "宏观信号打架（常见于指数稳、内部轮动）。按股票质量分批，不按大盘情绪一把做多或做空。"
        )
        stock_action = "hold"
        options_action = "selective"

    return {
        "vol": vol,
        "vol_label": VOL_LABELS[vol],
        "vix": vix,
        "rates": rates,
        "rates_label": RATES_LABELS[rates],
        "tnx": tnx,
        "tyx": tyx,
        "irx": irx,
        "fvx": fvx,
        "tnx_1d": tnx_1d,
        "tnx_5d": tnx_5d,
        "dollar": dollar,
        "dollar_label": DOLLAR_LABELS[dollar],
        "uup_5d": uup_5d,
        "uup_1d": uup_1d,
        "gld_5d": gld_5d,
        "oil_5d": oil_heat,
        "hyg_5d": hyg_5d,
        "spy_5d": spy_5d,
        "iwm_5d": iwm_5d,
        "smh_5d": smh_5d,
        "tlt_5d": tlt_5d,
        "curve_10_3": curve_10_3,
        "curve_state": curve_state,
        "risk": risk,
        "risk_label": RISK_LABELS[risk],
        "implication": implication,
        "stock_action": stock_action,
        "options_action": options_action,
        "off_votes": off_votes,
        "on_votes": on_votes,
    }


def classify_market_trend(market_data):
    """Mechanical regime from index drawdown, 200-SMA, and internals.

    Distinguishes a real index decline from a semiconductor rotation while
    SPY is still near highs. Safe to call with a partial market_data dict.
    """
    spy = market_data.get("SPY") or {}
    qqq = market_data.get("QQQ") or {}
    smh = market_data.get("SMH") or {}
    xle = market_data.get("XLE") or {}
    vix = (market_data.get("^VIX") or {}).get("price")

    spy_dd = spy.get("from_high")
    qqq_dd = qqq.get("from_high")
    smh_dd = smh.get("from_high")
    above200 = spy.get("above_sma200")
    spy_1m = spy.get("change_1m")
    spy_5d = spy.get("change_5d")
    qqq_5d = qqq.get("change_5d")
    smh_5d = smh.get("change_5d")
    xle_5d = xle.get("change_5d")

    if spy_dd is not None and spy_dd <= -20:
        phase = "bear_market"
    elif spy_dd is not None and spy_dd <= -10:
        phase = "correction"
    elif spy_dd is not None and spy_dd <= -3:
        phase = "pullback"
    elif spy_1m is not None and spy_1m > 0 and above200:
        phase = "uptrend"
    elif above200 is False and spy_1m is not None and spy_1m < 0:
        phase = "range"
    else:
        phase = "uptrend" if above200 else "range"

    structure = "broad"
    if smh_5d is not None and spy_5d is not None and smh_5d <= -3 and spy_5d >= -1.5:
        structure = "internal_rotation"
    elif xle_5d is not None and qqq_5d is not None and xle_5d >= 1.5 and qqq_5d <= -1:
        structure = "internal_rotation"
    elif spy_1m is not None and qqq.get("change_1m") is not None:
        if qqq["change_1m"] < spy_1m - 2:
            structure = "growth_underperforms"

    return {
        "phase": phase,
        "label": _PHASE_LABELS[phase],
        "structure": structure,
        "structure_label": _STRUCTURE_LABELS[structure],
        "spy_from_high": spy_dd,
        "qqq_from_high": qqq_dd,
        "smh_from_high": smh_dd,
        "vix": vix,
        "above_sma200": above200,
        "spy_rsi": spy.get("rsi"),
        "spy_change_5d": spy_5d,
        "smh_change_5d": smh_5d,
    }


def fallback_stance(trend, macro_score, vix=None):
    """Conservative add-vs-sell stance when Claude is missing or incomplete."""
    phase = (trend or {}).get("phase") or "range"
    structure = (trend or {}).get("structure")
    score = 50 if macro_score is None else float(macro_score)
    vix = vix if vix is not None else (trend or {}).get("vix")

    if phase == "bear_market":
        if vix is not None and vix >= 28:
            return "scale_in"
        return "trim"
    if phase == "correction":
        return "scale_in" if score >= 50 else "hold"
    if phase == "pullback":
        if structure == "internal_rotation":
            return "scale_in"
        if score < 40:
            return "trim"
        if score >= 55:
            return "scale_in"
        return "hold"
    if phase == "uptrend":
        if structure == "internal_rotation":
            return "scale_in"
        rsi = (trend or {}).get("spy_rsi")
        if rsi is not None and rsi >= 70:
            return "hold"
        if score >= 70:
            return "scale_in"
        return "hold"
    if score < 40:
        return "trim"
    return "hold"


def _horizon_dict(direction, view):
    d = direction if direction in HORIZON_DIR_LABELS else "range"
    return {"direction": d, "view": view or ""}


def _coerce_horizon(raw, fallback):
    if isinstance(raw, str) and raw.strip():
        return _horizon_dict(fallback["direction"], raw.strip())
    if not isinstance(raw, dict):
        return dict(fallback)
    direction = str(raw.get("direction") or fallback["direction"]).lower()
    view = raw.get("view") or fallback["view"]
    return _horizon_dict(direction, view)


def fallback_trend_outlook(trend, macro_score, market_data=None):
    """Template 1w/1m/6m outlook from the mechanical classifier."""
    trend = trend or {}
    phase = trend.get("phase") or "range"
    structure = trend.get("structure") or "broad"
    stance = fallback_stance(trend, macro_score, trend.get("vix"))
    spy_dd = trend.get("spy_from_high")
    dd_txt = f"{spy_dd:.1f}%" if spy_dd is not None else "未知"

    if structure == "internal_rotation":
        current = (
            f"指数层面不是崩盘：标普距近一年高点 {dd_txt}。"
            f"下跌主要发生在成长股/半导体内部，属于轮动而不是系统性抛售。"
        )
        w = _horizon_dict("range", "近一周仍可能跟着利率、油价和财报波动，指数宽幅震荡，芯片弹性更大。")
        m = _horizon_dict("range", "未来一个月要过非农、CPI 和 FOMC，方向取决于期限溢价会不会再冲高。")
        h = _horizon_dict("up", "半年维度只要指数仍在长期均线上方，基准仍是偏多，但不要按半导体单边去押。")
    elif phase == "bear_market":
        current = f"指数距高点 {dd_txt}，已进入熊市回撤区间。这不是普通回调。"
        w = _horizon_dict("down", "近一周优先降低风险，反弹当减仓窗口，而不是抄底确认。")
        m = _horizon_dict("down", "未来一个月趋势跟随的胜率偏低，等跌势放缓或波动率见顶再加仓。")
        h = _horizon_dict("range", "半年维度才讨论是否形成大底；现在不宜把子弹一次打完。")
    elif phase == "correction":
        current = f"指数距高点 {dd_txt}，属于调整而不是崩盘。"
        w = _horizon_dict("range", "近一周波动会放大，适合分批而不是一把买满。")
        m = _horizon_dict("range", "未来一个月看调整是否在长期均线处停下。")
        h = _horizon_dict("up", "若这是牛市中的调整，半年维度仍偏向修复，但仓位要留余地。")
    elif phase == "pullback":
        current = f"指数距高点 {dd_txt}，是上升趋势里的回撤。"
        w = _horizon_dict("range", "近一周不要把回撤当成崩盘，也不要因为一日反弹就追高。")
        m = _horizon_dict("range", "未来一个月仍是边走边看，尤其是 9 月季节性偏弱。")
        h = _horizon_dict("up", "半年基准仍是偏多，前提是不要在拥挤板块上加倍。")
    elif phase == "uptrend":
        current = f"指数仍在上升趋势（距高点 {dd_txt}，价格在长期均线上方）。"
        w = _horizon_dict("up", "近一周偏多，但超买时优先持有而不是加杠杆追涨。")
        m = _horizon_dict("range", "未来一个月留意季节性和政策会议，涨多了就降低追涨意愿。")
        h = _horizon_dict("up", "半年维度趋势未坏，用分批替代一次打满。")
    else:
        current = f"指数处于震荡（距高点 {dd_txt}）。"
        w = _horizon_dict("range", "近一周没有单边趋势，降低交易频率。")
        m = _horizon_dict("range", "未来一个月等突破方向，而不是提前押注。")
        h = _horizon_dict("range", "半年维度等待趋势重新明确。")

    events = _upcoming_macro_events(4)
    if events:
        ev = "、".join(f"{d.isoformat()} {lab}" for d, lab in events)
        w["view"] += f" 眼前事件：{ev}。"

    sizing = {
        "aggressive_buy": "可以把计划加仓资金的大部分用于逢低买入，但仍应分两到三笔，不要在开盘第一分钟打满。",
        "scale_in": "不要尽量卖出，也不要大量一次抄底。用计划加仓资金的大约 20–30% 买质量回撤，余下等收益率或政策事件明朗。",
        "hold": "今天既不是清仓日，也不是重仓抄底日。已有仓位持有，停止追涨，不因一日波动砍仓。",
        "trim": "停止新开仓，把拥挤成长仓降到纪律上限以内。这是减风险，不是因为崩盘而清仓。",
        "aggressive_sell": "优先把风险预算降下来，卖出或大幅降低高波动成长仓，现金先于抄底。",
    }

    return {
        "current_trend": current,
        "outlook_1w": w,
        "outlook_1m": m,
        "outlook_6m": h,
        "stance": stance,
        "stance_label": STANCE_LABELS[stance],
        "sizing_guidance": sizing[stance],
        "source": "fallback",
    }


def merge_trend_outlook(trend, macro_score, market_data, claude_result):
    """Start from the mechanical fallback, overlay valid Claude fields."""
    base = fallback_trend_outlook(trend, macro_score, market_data)
    if not claude_result:
        return base

    current = claude_result.get("current_trend")
    if isinstance(current, str) and current.strip():
        base["current_trend"] = current.strip()

    base["outlook_1w"] = _coerce_horizon(claude_result.get("outlook_1w"), base["outlook_1w"])
    base["outlook_1m"] = _coerce_horizon(claude_result.get("outlook_1m"), base["outlook_1m"])
    base["outlook_6m"] = _coerce_horizon(claude_result.get("outlook_6m"), base["outlook_6m"])

    stance = claude_result.get("stance")
    if stance in STANCE_LABELS:
        # Guardrail: do not let the model scream "all-in" on a near-high pullback.
        spy_dd = (trend or {}).get("spy_from_high")
        vix = (trend or {}).get("vix")
        _raw_stance = stance
        if stance == "aggressive_buy" and not (
            vix is not None and vix >= 28 and spy_dd is not None and spy_dd <= -10
        ):
            stance = "scale_in"
        if stance == "aggressive_sell" and spy_dd is not None and spy_dd > -10:
            stance = "trim"
        _llm_log({"kind": "clamp", "llm_stance": _raw_stance, "final_stance": stance,
                  "clamped": _raw_stance != stance,
                  "mechanical_stance": base.get("stance"), "spy_from_high": spy_dd,
                  "vix": vix})
        base["stance"] = stance
        base["stance_label"] = STANCE_LABELS[stance]
    else:
        _llm_log({"kind": "clamp", "llm_stance": stance, "final_stance": base.get("stance"),
                  "clamped": None, "invalid_stance": True})

    sizing = claude_result.get("sizing_guidance")
    if isinstance(sizing, str) and sizing.strip():
        base["sizing_guidance"] = sizing.strip()

    base["source"] = "claude"
    return base

