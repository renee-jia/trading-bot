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

---

## 2026-09-28 — Strategy V7: 6-1 momentum + portfolio vol targeting

**Code version:** working tree on top of `fc12b6a`
**Run date:** 2026-09-28
**Engine:** a vectorized research backtester (scratch, not committed) that replicates the live executor exactly — decide at close *t* every 5 trading days, execute at close *t+1*, held-name rank buffer, ladder weights, 25% cap, `blend_speed` 0.20, 2%-of-equity band, full exits, 5 bps/side. `backtest_strategy.py` was updated to the same V7 rules afterwards.

### Why this run

The paper account is +22.6% since 2026-03-13 vs QQQ +25.1%, with a −28% max drawdown and 60%+ realized vol. Two literature surveys (academic and practitioner, ~90 sources) point to the same diagnoses: the 1m/3m formation window sits in the short-term-reversal zone, and a 10-name rank-weighted book has ~2× the risk of any momentum product with a live track record. The two fixes with the best replication record are a longer skip-month formation window and volatility management of momentum specifically (Barroso & Santa-Clara 2015; Cederburg et al. 2020 find vol management fails for most factors *except* momentum).

### Two universes

1. **Live universe** — today's 170-name `configs.UNIVERSE`. Heavily survivorship- and hindsight-biased (names were added *after* they ran: the equal-weight basket alone compounds at 34%/yr). Use only for *relative* comparisons.
2. **Point-in-time S&P 500** — membership on each date from a public historical-constituents list (610 names with price data; delisted names without data are still missing, so a residual bias remains, but there is no "picked after the run-up" bias). This is the honest test.

All rows below are the **mean over the 5 possible rebalance-day offsets** (Monday/Tuesday/… cadence). Rebalance-timing luck is large — the V6 config's CAGR ranges 54–76% across offsets on the live universe — so single-offset numbers are not comparable.

### Signal (weekly, top-10 ladder, hold-until-rank-20, 2017-01 → 2026-09)

| Signal | LIVE cagr | Sharpe | MaxDD | turn/yr | 2022 | SP500 cagr | Sharpe | MaxDD | turn/yr | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **V6: 0.3·1m + 0.7·3m** (buf 15) | 65% | 1.14 | −72% | 16.1x | −40% | 17% | 0.64 | −49% | 22.6x | −25% |
| 3-1 | 62% | 1.09 | −76% | 21.4x | −46% | 16% | 0.61 | −47% | 27.1x | −19% |
| 6-0 | 86% | 1.38 | −57% | 7.5x | −27% | 24% | 0.78 | −44% | 10.7x | −9% |
| **6-1 (V7)** | **89%** | **1.41** | −57% | 9.1x | −24% | 25% | 0.79 | −41% | 12.5x | −13% |
| 9-1 | 71% | 1.26 | −53% | 6.3x | −23% | **30%** | **0.90** | −39% | 8.5x | −7% |
| 12-1 | 69% | 1.27 | −60% | 5.1x | −21% | 24% | 0.77 | −40% | 6.5x | +6% |
| rank-avg(6-1, 9-1, 12-1) | 72% | 1.28 | −52% | 6.7x | −16% | 27% | 0.84 | −39% | 8.9x | −2% |
| risk-adjusted 6m (ret/vol) | 59% | 1.24 | −55% | 10.1x | −13% | 17% | 0.70 | −47% | 15.1x | −10% |
| residual momentum vs QQQ 6-1 | 53% | 1.27 | −48% | 12.1x | −10% | 22% | 0.85 | −40% | 17.4x | +3% |
| SPY | | | | | | 15.3% | 0.88 | −34% | | −18% |
| QQQ | | | | | | 21.5% | 0.97 | −35% | | −33% |
| SPMO (live ETF) | | | | | | 20.9% | 1.00 | −31% | | −10% |

- **On the unbiased universe the V6 signal is worse than buying SPY on a risk-adjusted basis** (Sharpe 0.64 vs 0.88) and turns the book over 23×/yr. That is the strategy without the hand-picked universe.
- Every formation window of 6–12 months beats V6 on *both* universes, at every offset, with half the turnover. 3-month and shorter windows do not. 6-1 is the best on the traded universe and solid on the S&P; 9-1 is the best on the S&P. **Shipped: 6-1** (single window, simplest to explain; `MOM_MODE=legacy` restores V6).
- Risk-adjusted, residual and frog-in-the-pan variants lower vol but not Sharpe relative to raw 6-1; regime filters (SPY 200-SMA, 12-month sign, Garg 4-state) cost more return than they save on both universes; sector caps and inverse-vol weights were neutral-to-negative. Not shipped.

### Overlays on 6-1 (same construction)

| Overlay | LIVE cagr | Sharpe | MaxDD | 2022 | SP500 cagr | Sharpe | MaxDD | 2022 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| none | 89% | 1.41 | −57% | −24% | 25% | 0.79 | −41% | −13% |
| vol target 35% (20d basket vol) | 67% | 1.61 | −42% | −9% | 20% | 0.75 | −36% | −10% |
| **vol target 40% (shipped)** | 72% | 1.58 | −45% | −10% | 21% | 0.75 | −38% | −10% |
| vol target 45% | 75% | 1.55 | −48% | −12% | 22% | 0.77 | −39% | −11% |
| 20% trailing stop | 84% | 1.47 | −51% | −21% | 25% | 0.85 | −38% | −10% |
| vol target 40% + 20% trailing stop | 67% | 1.63 | −40% | −11% | 22% | 0.84 | −35% | −8% |
| top-15 / hold-to-20 | 80% | 1.40 | −54% | −25% | 23% | 0.78 | −40% | −10% |

- Vol targeting is a **drawdown tool**: it cuts 2022 from −24% to −10% and MaxDD by 12+ points on the traded universe, lifts Sharpe there (1.41 → 1.58), and is roughly Sharpe-neutral on the S&P (where the basket rarely exceeds 40% vol, so it seldom binds). 40% was chosen over 35% because it only kicks in when the book is genuinely wild (2020, 2026 semis mania) and costs ~nothing in normal years. Realized vol is measured on the *target basket* (20 trading days, ladder weights) so it does not depend on the account's own history — Cloud Run jobs have no disk.
- The 20% trailing stop is the best remaining candidate (improves Sharpe and MaxDD on both universes) but needs per-position entry-high state that the stateless job does not have. **Open item.**

### Live window sanity check (2026-03-13 → 2026-09-25, single offset)

| | Return | Vol | MaxDD |
|---|---:|---:|---:|
| Paper account (actual) | +22.6% | ~60% | −28% |
| V6 simulated | +50% | 83% | −42% |
| V7 simulated (6-1, vt 35–40%) | +41–43% | 48–52% | −23 to −26% |
| QQQ | +25.8% | 22% | −11% |

The V6 simulation overstates the account by ~27 points because ~15 universe names were added during the window after they had already run. Treat every live-universe number as an upper bound; the *ranking* of configurations is what transfers.

### What shipped (`core/strategy.py`, `technical_analyzer.py`, `alpaca_trader.py`, `report_generator.py`, `backtest_strategy.py`)

| Change | V6 | V7 |
|---|---|---|
| Momentum signal | 0.3·1m + 0.7·3m return | 6-month return ending one month ago (`change_6m_skip1`); falls back to V6 blend when <127 bars |
| Hold-until rank buffer | 15 | 20 |
| Exposure | 1 − macro cash | (1 − macro cash) × min(1, 0.40 / 20-day realized vol of the target basket); never levers |
| Env knobs | — | `MOM_MODE` (`6_1`/`legacy`), `VOL_TARGET` (0 disables), `VOL_LOOKBACK` |

Selection, ladder weights, 25% cap, weekly cadence, blend 0.20, 2% band and the macro cash reserve are unchanged. Unit tests: `scripts/test_strategy_weights.py` (V7 section).

### Known limits
- No point-in-time data for the traded universe; the S&P 500 test is the only unbiased evidence and it is large-cap only.
- Vol targeting leaves cash idle (Alpaca paper pays nothing). Parking it in a T-bill ETF would add ~4%/yr on the un-invested fraction.
- Rebalance-timing luck (±10 points of CAGR/yr between offsets) is larger than most of the overlay effects; tranching the book across the week is the mechanical fix and is not implemented.
