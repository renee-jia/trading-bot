# Backtest Results Log

> A dated log of strategy backtest runs. Append a new section per run — do not
> overwrite history. Each entry records the code version, config, results, and
> interpretation so results stay traceable as the strategy evolves.

---

## 2026-06-02 — Strategy V6 (T+1 execution + transaction costs)

**Code version:** working tree on top of `cb93ae0` (uncommitted)
**Run date:** 2026-06-02
**Engine:** `backtest_strategy.py` + `backtest_years.py`; weight math from `strategy.py` (shared with live `alpaca_trader.py`)

### What changed vs the previous version
- **Look-ahead fixed:** rebalances are now **decided on day *t*'s close and executed at day *t+1*'s close** (the old code decided and executed on the same close, inflating returns).
- **Transaction costs added:** **5 bps/side** (spread + slippage; Alpaca commissions are $0) charged on traded notional at each rebalance; a one-time entry cost is applied to both the strategy and the buy-and-hold basket for a fair comparison.
- **SPY market benchmark added** so the edge can be split into *basket-vs-market* and *strategy-vs-basket*.
- Weight logic consolidated into `strategy.py` (single source of truth) — the backtest now exercises the **same** code the bot trades live.

### Config
| Item | Value |
|------|-------|
| Universe | AAPL, MSFT, NVDA, GOOGL, AMZN, META, AVGO, TSLA, AMD, CRM, ORCL, ADBE (12 large-cap tech/AI) |
| Strategy | V6 — concentrated **top-10 by momentum**, weight 95% momentum rank + 5% score², max position 25% |
| Rebalance | every 14 trading days (biweekly), **executed T+1** |
| Cost | 5 bps/side |
| "Buy & Hold" | **equal-weight basket of the same tickers** (NOT the market) |
| SPY | true market benchmark |

> ⚠️ **Survivorship bias:** the universe is *today's* survivors held back through time. Delisted/crashed names that would have been bought are absent, so both the basket and the strategy returns are flattering. Treat all alpha here as an **upper bound**.

### Multi-year sweep (calendar years)

| Year | Days | Strategy | Basket (B&H) | SPY | Nasdaq-100 (QQQ) | Strategy − SPY | **Strategy − Basket** |
|------|------|----------|--------------|-----|------------------|----------------|------------------------|
| 2022 | 251 | −45.4% | −41.2% | −18.6% | −33.2% | −26.7% | **−4.1%** |
| 2023 | 250 | +92.4% | +103.5% | +26.7% | +55.9% | +65.7% | **−11.1%** |
| 2024 | 252 | +60.1% | +51.5% | +25.6% | +27.7% | +34.5% | **+8.6%** |
| 2025 | 250 | +24.2% | +22.4% | +18.0% | +21.0% | +6.2% | **+1.8%** |
| 2026 (to 06-03) | 105 | +26.6% | +16.7% | +10.7% | +21.5% | +15.9% | **+9.9%** |

- **Strategy − SPY** = strategy's total excess over the market.
- **Strategy − Basket** = the strategy's *own* edge over equal-weight holding the same names. The rest of "Strategy − SPY" is just the hand-picked basket beating SPY (survivorship).

### Detailed single window: 2025-06-02 → 2026-06-02 (252 trading days)

| Metric | Strategy | Buy & Hold (basket) | Diff |
|--------|----------|---------------------|------|
| Final value ($100k start) | $163,606 | $158,934 | +$4,672 |
| Total return | +63.61% | +58.93% | +4.67% |
| Sharpe | 2.05 | 2.13 | −0.08 |
| Max drawdown | 25.06% | 23.34% | +1.72% |
| Volatility (ann.) | 25.72% | 23.08% | +2.64% |

- Market (SPY) over the same window: **+29.62%**
- Basket vs SPY: **+29.31%** (the basket beating the market = stock selection / survivorship)
- Strategy vs SPY: **+33.99%** total, of which only **+4.67%** is the strategy's edge over the basket.

### Key findings
1. **No reliable strategy alpha over its own basket.** Strategy − Basket is positive in 3 years (+8.6, +1.8, +9.9) but negative in 2 (−4.1 in 2022, −11.1 in 2023); averages ≈ **+1%/yr with very high variance**.
2. **Amplifies drawdowns in down years.** 2022: strategy −45.4% vs basket −41.2% vs SPY −18.6%. Concentration + 95% momentum + no stops deepened the loss.
3. **Lags broad rebounds.** 2023: strategy +92.4% vs basket +103.5% (momentum whipsawed off the 2022 bottom).
4. **Most of the headline "beat SPY by 30–65%" is the survivorship-biased basket, not the algorithm.** A single favorable window (the +4.67% 2025-06→2026-06 run) is not representative.

**Conclusion:** Strategy V6 behaves as a **beta amplifier** on a hand-picked winner basket — additive in up years, worse in down years — without a stable risk-adjusted edge over simply equal-weighting the same names.

### Reproduce
```bash
source .venv_trading/bin/activate
# Single recent year (verbose, with SPY decomposition):
python backtest_strategy.py --tickers AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,TSLA,AMD,CRM,ORCL,ADBE
# Multi-year sweep:
python backtest_years.py
```
