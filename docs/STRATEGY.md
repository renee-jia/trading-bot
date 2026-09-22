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

- **Cloud Run Job** on GCP (`reward-seeking` project) triggers at 7:00 AM Pacific (10:00 AM ET), Mon-Fri. Because the US session is already open, `market_bars.drop_unfinished_bars` scores the previous completed close, not the in-progress day. Paper trades run with that same signal.
- Price history is filtered by `market_bars.drop_unfinished_bars`: a session that has not closed (before 16:00 ET) never reaches the analysis, so a run during market hours scores the previous close instead of the opening print. The report header shows `数据截至`, the newest completed session used.
- Runs full analysis, generates report, emails HTML-formatted results
- No idle cost — container only runs during the ~7 min analysis
- Local backup via macOS launchd (runs when laptop wakes up)

## Rebalance Cadence & Turnover (reviewed 2026-09-20)

The live book ran a **daily** cadence with `blend_speed=0.80` and a 0.3%-of-equity
min-trade floor. Measured over the paper account's first 6.2 months
(2026-03-13 → 2026-09-18, 2,392 fills):

| | |
|---|---|
| Turnover | $3.09M notional on a $123k book — **~48x annualized** |
| Days with fills | **111 / 111** trading days |
| Median daily traded notional | **18.3% of equity** |

Realized P&L by holding period (FIFO over all closed legs) showed where the
money actually came from:

| Holding period | Legs | Cost basis | P&L | Return |
|---|---|---|---|---|
| ≤1 day | 221 | $244,983 | −2,246 | −0.92% |
| 2–5 days | 346 | $220,290 | −3,921 | −1.78% |
| 6–10 days | 531 | $370,040 | −224 | −0.06% |
| 11–21 days | 613 | $423,793 | −1,901 | −0.45% |
| **22–60 days** | **272** | **$219,672** | **+27,518** | **+12.53%** |
| >60 days | 19 | $7,125 | −207 | −2.91% |

Every leg held ≤21 days lost money in aggregate (−$8,292 on $1.26M of cost).
All of the profit came from the positions that were left alone.

### Why the old settings churned

Three parameters were mutually inconsistent:

1. `blend_speed=0.80` closes 80% of the gap to target **per run**, i.e. 96% in
   two runs. On a daily cadence that is full re-weighting every two days, not
   "gradual rebalancing".
2. The rank ladder `mom_w = 3.0 − 2.8 * rank_pct` spreads the top 10 from 18.65%
   down to 1.35%. One **adjacent** rank swap moves a weight by 1.92% of equity
   (~$2,366 on $123k) — 6.4x the $369 min-trade floor, so the floor never bound.
3. Momentum is recomputed every run from 1m/3m returns, so ranks reshuffle daily
   by construction.

### Backtest evidence

`backtest_strategy.py` now takes `cost_per_side`, `rebalance_interval`,
`blend_speed` and `use_scores`, so cadence and cost can be swept. Note the
shipped backtest had `rebalance_interval = 14` hard-coded while live traded
daily — **it was never validating the live configuration.**

Strategy return over the live window and trailing 12m, by cost assumption
(140-name universe, scores disabled — see below):

| config | turn/yr | live 5bp | live 15bp | live 25bp | 12m 5bp | 12m 15bp | 12m 25bp |
|---|---|---|---|---|---|---|---|
| **live: daily, blend 0.80** | 62.6x | +40.4% | +35.8% | +31.3% | +141.9% | +128.0% | +114.9% |
| daily, blend 0.20 | 27.5x | +55.1% | +52.8% | +50.5% | +172.9% | +166.0% | +159.2% |
| weekly, blend 0.80 | 26.2x | +59.0% | +56.7% | +54.4% | +158.9% | +152.3% | +145.9% |
| 14d, blend 0.80 (old BT) | 15.0x | +39.9% | +38.7% | +37.5% | +192.8% | +189.0% | +185.2% |
| **weekly, blend 0.20 (shipped)** | ~9.5x | +58.4% | +57.4% | +56.5% | +180.9% | +178.1% | +175.4% |

The daily/0.80 pair is the worst config in **both** windows at **every** cost
assumption, and it is also the most cost-sensitive (−9.1 pts live, −27.0 pts 12m
going 5bp → 25bp, vs ~−2 pts for the low-turnover configs).

### What shipped

- `alpaca_trader.run_trading` gained a **cadence gate** (`REBALANCE_INTERVAL_DAYS`,
  default 5 trading days, 0 disables). It is stateless — it reads the broker's
  last fill, because Cloud Run jobs have no persistent disk.
- `calculate_trades` default `blend_speed` 0.80 → **0.20**.
- `calculate_trades` gained `band_pct` (default **2% of equity**): a name only
  trades when it is that far from **target**. Measuring against target rather
  than against the blended trade size keeps the band meaningful independent of
  `blend_speed`. Full exits still bypass it.

On the actual book as of 2026-09-18, a representative reshuffle produces
8 orders / 18.3% of equity under the old settings (matching the measured live
median exactly) vs **4 orders / 8.5%** under the new ones.

### What was tested and rejected

- **Flattening the rank ladder.** Once the cadence is fixed, the ladder does not
  drive turnover at all (9.6x / 9.4x / 9.5x for slopes 3.0-2.8 / 1.6-1.2 / flat).
  Full flattening costs ~28 pts of 12m return for ~5 pts of vol. Not shipped.
- **Skip-the-recent-month momentum** (3-1 instead of 0.3·m1 + 0.7·m3). Wins the
  live window (+60.7% vs +58.4% at 5bp) but loses badly on trailing 12m (+143.0%
  vs +180.9%). Not robust on this evidence. Not shipped.

### Known limits of these numbers

- The backtest says +40.4% for the live config over the live window; the account
  actually did **+23.1%**. The gap comes from survivorship bias (the universe is
  today's ticker list), no macro cash drag, execution at the close rather than
  the open auction, whole-share rounding and buy-budget clipping. **Use the
  backtest to rank configurations, never to predict live returns.**
- Scores are disabled (`use_scores=False`) in the sweep. With `MOM_PCT = 0.95`
  the score moves a weight by ±0.1% of equity across its entire 0–100 range and
  never affects selection, so this changes nothing material while making a
  daily-cadence sweep tractable.
- Risk is **not** addressed by any of this. Every config tested lands at 53–64%
  annualized vol and 29–39% max drawdown. Sector caps and vol targeting remain
  open (the book is currently 10/10 SaaS out of a 161-name universe).
