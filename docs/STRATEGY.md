# Trading Bot Strategy Overview

## Philosophy

Macro buy-and-hold strategy designed for **weeks to months** holding periods. The engine scores each stock 0-100 across four dimensions, producing a recommendation from Strong Buy to Avoid.

## Score → Recommendation

| Score | Grade | Recommendation |
|-------|-------|----------------|
| 75+   | A     | Strong Buy     |
| 60-74 | B/B+  | Buy            |
| 45-59 | C/C+  | Hold           |
| 30-44 | C/D   | Reduce         |
| <30   | F     | Avoid          |

## Top-Level Scoring

| Dimension | Weight | Source | Description |
|-----------|--------|--------|-------------|
| Technical | 35% | `technical_analyzer.py` | Price-based indicators: trend, momentum, volume, volatility |
| Trend | 30% | `trend_analyzer.py` | Macro positioning: regime, relative strength vs SPY, VIX, Claude synthesis |
| Alpha | 20% | `custom_alphas.py` | 30 quantitative alpha factors |
| Sentiment | 15% | `sentiment_analyzer.py` | Claude LLM news analysis with keyword fallback |

## Component Details

### Technical (35%)

Uses 2-year daily + 1-year hourly data.

| Sub-component | Weight | Key Signals |
|---------------|--------|-------------|
| Trend | 35% | 50/200 SMA crossover (Golden/Death Cross), ADX trend strength |
| Momentum | 25% | RSI-14, MACD 12/26/9, Rate of Change. Blended 60% daily + 40% hourly |
| Volume | 15% | OBV trend, volume vs 20-day average. Blended 60% daily + 40% hourly |
| Support/Resistance | 15% | Position relative to 52-week high/low |
| Volatility | 10% | ATR-14, Bollinger Band position. Blended 70% daily + 30% hourly |

### Trend (30%)

Macro-level analysis with VIX, Claude AI synthesis, and comprehensive macro indicators.

| Sub-component | Weight | Key Signals |
|---------------|--------|-------------|
| Relative Strength | 25% | Alpha vs SPY at 1m/3m/6m, RS trend direction |
| Market Regime | 25% | Bull/bear/range via price vs 200-SMA + SMA slope |
| VIX | 20% | Current level, 5-day trend, vs 50-day average, term structure |
| Momentum | 20% | Returns at 1m/3m/6m/12m, multi-timeframe consistency bonus |
| Breadth | 10% | Up-day percentage, rolling 20-day new highs |

**Claude synthesis** blends 30% with the quantitative score above (final = 70% quant + 30% Claude macro assessment).

Claude's macro assessment incorporates:
- **Geopolitical & Tail Risks**: Wars, oil spikes, banking crises, credit events, trade wars
- **Liquidity Conditions**: Fed balance sheet (QE/QT), reverse repo, bank reserves, financial conditions
- **Economic Growth**: GDP, ISM PMI, unemployment, consumer spending
- **Interest Rates**: Fed Funds Rate, 10Y Treasury, real rates (TIPS), rate direction impact

### Alpha (20%)

30 quantitative factors computed from price/volume data:
- Momentum factors (short-term, medium-term, acceleration)
- Mean-reversion factors (distance from moving averages, RSI extremes)
- Volume factors (volume-price correlation, OBV momentum)

Each factor normalized 0-1, final alpha score = mean of all valid factors.

### Sentiment (15%)

**Primary (Claude LLM):** Sends up to 20 news headlines with ticker, sector, and price context to Claude Haiku. Also factors in market-wide sentiment indicators:
- **VIX** (fear/greed gauge)
- **AAII Sentiment Survey** (retail investor positioning, contrarian signal at extremes)
- **Put/Call Ratio** (options market hedging vs speculation)
- **CNN Fear & Greed Index** (composite market sentiment)

Returns:
- Sentiment score (0-1)
- Confidence level
- Key themes and risk flags (including market-wide sentiment context)
- Written reasoning with broader market sentiment context

**Fallback (keyword-based):** Recency-weighted lexicon matching against curated bullish/bearish financial phrases.

## Score Modifiers

Applied in `scorer.py` after the weighted combination:

| Modifier | Effect |
|----------|--------|
| Conviction bonus | +12% when all major components agree bullish; -12% when all bearish |
| Regime adjustment | Bear market pulls scores toward neutral (x0.85); bull market boosts strong signals (x1.08) |
| Confidence weighting | Low-confidence signals pulled toward 0.5 (neutral) using quadratic pull |
| Weight redistribution | If sentiment or alpha unavailable, their weight redistributes proportionally |

## Data Sources

| Data | Source | Period |
|------|--------|--------|
| Price (daily) | yfinance | 2 years |
| Price (hourly) | yfinance | 1 year |
| News headlines | yfinance | Latest ~10-30 articles |
| Benchmark (SPY) | yfinance | 2 years |
| VIX | yfinance (^VIX) | 1 year |
| Sentiment AI | Anthropic Claude Haiku | Per-stock analysis |
| Trend AI | Anthropic Claude Haiku | Per-stock synthesis |

## Running

```bash
source .venv_trading/bin/activate
python main.py                          # All stocks
python main.py --tickers AAPL,NVDA      # Specific tickers
python main.py --quick                  # Skip news (faster)
python main.py --quick --no-alpha       # Fastest mode
```

## Automated Daily Reports

- **Cloud Run Job** on GCP (`reward-seeking` project) triggers at 2:00 PM Pacific (5:00 PM ET, after the close), Mon-Fri
- Price history is filtered by `market_bars.drop_unfinished_bars`: a session that has not closed (before 16:00 ET) never reaches the analysis, so a run during market hours scores the previous close instead of the opening print. The report header shows `数据截至`, the newest completed session used.
- Runs full analysis, generates report, emails HTML-formatted results
- No idle cost — container only runs during the ~7 min analysis
- Local backup via macOS launchd (runs when laptop wakes up)
