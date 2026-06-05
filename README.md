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
> Account opened **2026-03-13**. Snapshot **2026-06-04**. _Past performance ≠ future results._

| | Return (since inception) |
|---|---:|
| **🤖 This strategy (paper)** | **+53.6%** |
| S&P 500 (SPY) | +13.5% |
| Nasdaq-100 (QQQ) | +24.8% |

Starting capital **$100,000** → peak equity **$159,560**, max drawdown **−8.4%**, 32 open positions concentrated in semis/AI (MRVL +33%, ARM +18%, SNDK +12%).

📊 **Full breakdown + equity curve → [docs/PAPER_TRADING.md](docs/PAPER_TRADING.md)** (refresh anytime with `python scripts/fetch_paper_performance.py`)

---

## 🧪 Backtest — Strategy vs S&P 500 & Nasdaq-100

Per-calendar-year, point-in-time backtest with **T+1 execution** and **5 bps/side transaction costs** (the *same* weighting code the live bot trades):

| Year | 🤖 Strategy | S&P 500 | Nasdaq-100 |
|------|------------:|--------:|-----------:|
| 2022 | −45.4% | −18.6% | −33.2% |
| 2023 | +92.4% | +26.7% | +55.9% |
| 2024 | +60.1% | +25.6% | +27.7% |
| 2025 | +24.2% | +18.0% | +21.0% |
| 2026 (YTD) | +26.6% | +10.7% | +21.5% |

> ⚠️ **Honesty note:** the universe is today's survivors held back through time (survivorship bias), so treat the raw alpha as an **upper bound**. The strategy is a high-beta amplifier — it shines in up years and overshoots drawdowns in down years (see 2022). The full, self-critical analysis is in **[docs/BACKTEST_RESULTS.md](docs/BACKTEST_RESULTS.md)** — including the strategy's edge *over its own basket*, which is small and noisy. We publish the warts on purpose.

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

# Analyze the full universe (157 US equities)
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
macro_analyzer.py               Market regime & risk-on/off
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

- **Local daily agent (macOS):** `./setup_daily.sh install` renders a git-ignored launchd plist from `*.plist.template` and schedules `daily_report.py` for 8 AM on trading days.
- **Container:** `docker build -t trading-bot .` — secrets are injected at runtime (`--env`), never baked into the image.

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
