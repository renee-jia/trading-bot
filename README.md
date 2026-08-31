<div align="center">

# 🤖 Multi-Agent Quantitative Trading System

**A macro buy-and-hold engine that orchestrates a team of specialized analysis agents — technical, trend, macro, sentiment, and quantitative alpha — into a single 0–100 conviction score, then sizes and executes a live portfolio through Alpaca.**

![Python](https://img.shields.io/badge/python-3.12-blue)
![Broker](https://img.shields.io/badge/broker-Alpaca-yellow)
![Status](https://img.shields.io/badge/paper%20trading-live-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## 📈 Live Paper-Trading Performance

> Real money-weighted Alpaca paper account, auto-generated from the broker API.
> Account opened **2026-03-13**. _Past performance ≠ future results._

| Snapshot | 🤖 Strategy (paper) | S&P 500 (SPY) | Nasdaq-100 (QQQ) | Equity |
|---|---:|---:|---:|---:|
| **2026-08-31** (latest) | **+34.5%** | +16.1% | +20.2% | $134,457 |
| 2026-07-09 | +39.8% | +13.5% | +21.4% | $139,829 |
| 2026-06-04 | +53.6% | +13.5% | +24.8% | $159,560 |

Starting capital **$100,000** → peak equity **$165,578** (2026-06-23), current equity **$134,457**, max drawdown **−26.8%**. The book is now **10** names and about **24% cash** (top holdings: TEAM, WIX, PATH) after rotating out of the July semiconductor pile. Still ahead of SPY and QQQ over the same window; the gap vs the June peak is the cost of that high-beta ride.

📊 **Full breakdown + equity curve → [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md)** (refresh anytime with `python scripts/fetch_paper_performance.py`)

---

## 🧪 Backtest — Strategy vs S&P 500 & Nasdaq-100

Per-calendar-year, point-in-time backtest with **T+1 execution** and **5 bps/side transaction costs** (the *same* weighting code the live bot trades):

| Year | 🤖 Strategy | S&P 500 | Nasdaq-100 |
|------|------------:|--------:|-----------:|
| 2023 | +92.4% | +26.7% | +55.9% |
| 2024 | +60.1% | +25.6% | +27.7% |
| 2025 | +24.2% | +18.0% | +21.0% |
| 2026 (YTD) | +26.6% | +10.7% | +21.5% |

> ⚠️ **Honesty note:** the universe is today's survivors held back through time (survivorship bias), so treat the raw alpha as an **upper bound**. The strategy is a high-beta amplifier — it shines in up years and overshoots drawdowns in down years. The full, self-critical analysis is in **[docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)** — including the strategy's edge *over its own basket*, which is small and noisy. We publish the warts on purpose.

---

## 🧠 Multi-Agent Architecture

The score for every stock is produced by a panel of independent analysis agents, each looking at the market through a different lens, then fused by a weighted scorer:

```
                         ┌─────────────────────────────┐
   Market data ─────────▶│        DATA LAYER           │  yfinance: 2y daily + 1y hourly
   (prices, news, SPY)   │  data_fetcher / discovery   │
                         └──────────────┬──────────────┘
                                        ▼
        ┌───────────────────────────────────────────────────────────────┐
        │                     ANALYSIS AGENTS                            │
        ├───────────────┬───────────────┬──────────────┬────────────────┤
        │ 📐 Technical  │ 📊 Trend      │ 🌍 Macro     │ 📰 Sentiment   │
        │ SMA/RSI/MACD  │ rel-strength  │ regime &     │ news headline  │
        │ ADX/Boll/OBV  │ vs SPY        │ risk on/off  │ lexicon+decay  │
        │   (35%)       │   (30%)       │              │   (15%)        │
        ├───────────────┴───────────────┴──────────────┴────────────────┤
        │           🔒 Quantitative Alpha — 30 factors (20%)             │  ← core/ (private)
        └───────────────────────────────┬───────────────────────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │   🔒 SCORER  →  0–100 score │  confidence- & regime-adjusted
                         │   🔒 STRATEGY → target wts  │  top-10 momentum, max 25%/name
                         └──────────────┬──────────────┘
                                        ▼
                         ┌─────────────────────────────┐
                         │  Alpaca executor + report   │  T+1 rebalance, email digest
                         │  (trend / macro / options   │  desks + config & movers)
                         └─────────────────────────────┘
```

**Plus a layer of Claude research agents** (in [`.claude/skills/`](.claude/skills/)) that enrich the macro view — `market-news-analyst`, `scenario-analyzer`, `market-environment-analysis`, `earnings-calendar`, and `economic-calendar-fetcher`.

> 🔒 The proprietary signal engine — the **30 alpha factors, the scorer, and the position-sizing strategy** — lives in a private `core/` package that is **not** included in this repository. Everything else (the harness, analyzers, backtester, executor) is open.

---

## 🎯 Scoring Model

| Agent | Weight | Signals |
|-------|:------:|---------|
| 📐 Technical | 35% | SMA 50/200, RSI, MACD, ADX, Bollinger, OBV |
| 📊 Trend | 30% | Relative strength vs SPY, market regime |
| 🔒 Alpha | 20% | 30 quantitative factors (WorldQuant-style) |
| 📰 Sentiment | 15% | News headline analysis with recency decay |

Scores are confidence-adjusted and regime-aware (conservative in bear markets, a slight boost in bull markets).

| Score | Recommendation | Grade |
|:-----:|----------------|:-----:|
| 75+ | Strong Buy | A |
| 60–74 | Buy | B / B+ |
| 45–59 | Hold | C / C+ |
| 30–44 | Reduce | D |
| <30 | Avoid | F |

---

## 🚀 Quick Start

> The private `core/` package is required to run end-to-end. Without it the harness imports the scorer/strategy/alpha modules from `core/` and will fail — by design, the alpha is not published.

```bash
python -m venv .venv_trading && source .venv_trading/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill in your Alpaca keys

# Analyze the full universe (161 US equities / ETFs)
python main.py

# Specific tickers, top 10, skip news for speed
python main.py --tickers AAPL,NVDA,MSFT --top 10 --quick

# Daily report + paper trade
python daily_report.py --trade

# Backtest vs SPY (a single recent year, verbose)
python backtest_strategy.py --tickers AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,TSLA,AMD,CRM,ORCL,ADBE
# Multi-year sweep vs SPY & Nasdaq
python backtest_years.py
```

### CLI options (`main.py`)

```
--tickers AAPL,MSFT    Analyze specific stocks (default: full universe)
--top N                Show top N results only
--quick                Skip news sentiment (faster)
--no-alpha             Skip alpha factor computation (faster)
--output-dir DIR       Report output directory (default: reports/)
--no-report            Console output only, skip report file
```

---

## 📁 Repository Layout

```
main.py  daily_report.py        Entry points (CLI + daily runner)
data_fetcher.py                 Prices, news, benchmarks (yfinance)
technical_analyzer.py           Trend / momentum / volume / volatility
trend_analyzer.py               Relative strength vs SPY, market regime
macro_analyzer.py               Market regime, 1w/1m/6m stance, rates tape
daily_watch.py                  Options desk, config watch, top movers
covered_call_advisor.py         Covered-call ladders for held shares
sell_put_advisor.py             Cash-secured puts on panic dips
sentiment_analyzer.py           News headline sentiment
stock_discovery.py              Weekly universe expansion
report_generator.py             Markdown report output
email_sender.py                 Email digest
alpaca_trader.py                Paper/live execution via Alpaca
backtest_strategy.py            Strategy vs buy-and-hold (single window)
backtest_years.py               Per-year sweep vs SPY & Nasdaq
core/             🔒 PRIVATE    custom_alphas · scorer · strategy · configs
docs/                           STRATEGY · BACKTEST_RESULTS · PAPER_TRADING
scripts/                        fetch_paper_performance.py
.claude/skills/                 Claude research agents
```

---

## ⚙️ Deployment

- **Production:** GCP Cloud Run job `daily-report` runs `daily_report.py --trade` on trading mornings (Alpaca paper). Secrets are runtime env vars, not baked into the image.
- **Local daily agent (macOS):** `./setup_daily.sh install` renders a git-ignored launchd plist from `*.plist.template` and schedules a report-only run (no `--trade`) so it does not double-fill the paper account.
- **Container:** `docker build -t trading-bot .` — secrets are injected at runtime (`--env`).

---

## 🔐 Security & Privacy

- Secrets live only in `.env` (git-ignored). No API keys, passwords, or personal emails are tracked — see `.env.example` for the required variables.
- The launchd plist is rendered locally and git-ignored; only the placeholder `*.plist.template` is tracked.
- The proprietary `core/` algorithm is git-ignored and absent from this public repo.

---

## ⚠️ Disclaimer

This project is for **research and educational purposes only**. It is not investment advice. Markets carry risk; past and backtested performance does not guarantee future results. Trade live money at your own risk.

## 📄 License

[MIT](LICENSE) for the published harness. The private `core/` signal engine is not licensed for use or distribution.
