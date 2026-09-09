# Core review — 2026-09-08

## Fixed: enforce the configured position cap

The previous allocator redistributed excess five times and then returned without
checking the final cap. With macro score 50 and equal stock scores of 70,
two candidates produced a 65% maximum weight, three produced 40%, and four
produced 28.47%, despite a configured 25% cap.

The shared allocator now fixes capped names and redistributes only to remaining
names until all fit. Unallocatable capital remains in cash. Two candidates now
receive 25% each with 50% cash. Empty or single-name universes still return no
allocation (the executor skips them); their returned cash fraction is now 100%.
The report displays this actual cash and identifies its fresh-portfolio assumption.
It also describes excluded names as outside the momentum ranking, not score ranking.

Tests cover 2–20 candidates across three macro regimes, cash conservation, the
25% cap, holding-buffer selection, and unchanged normal ten-name allocations.
`core/strategy.py` is private and git-ignored: this local fix must also be included
in the private package used by any future deployment.

## Remaining priorities

| Priority | Finding | Next change / validation |
|---|---|---|
| 1 | Live uses a held-name rank buffer; backtest/report omit it. Backtest rebalances every 14 bars, blends 80% toward targets and renormalizes to full investment; live checks every run with trade thresholds. | Add an explicit live-matching backtest mode with cash ledger, holding threshold, buffer and execution cadence. Keep the fully-invested selection experiment separately labelled. Recheck caps after every blend/normalization. |
| 1 | Static event list can expire; no future event currently becomes “open”. The list also lacks completeness guarantees. | Model calendar coverage and freshness explicitly. Return unknown when coverage cannot be established; propagate this through research and both legacy advisors. Use authoritative scheduled releases, rather than inferring safety from an empty list. |
| 1 | New research and legacy option ladders use different liquidity and earnings gates. A research “wait” can coexist with a legacy ladder. | Build a shared quote-validation and eligibility result, then have every report section consume it. Include quote age, earnings/dividend checks, collateral and contract-size constraints. |
| 2 | “95% momentum / 5% score” mixes unnormalized components on different scales. | In a ten-name portfolio with every score at 70, the score term contributes only 1.59% of the raw total. Compare separately normalized blends in walk-forward tests; do not silently reinterpret the coefficients. |
| 2 | Technical, trend and alpha reuse price momentum; the scorer also boosts agreement and acceleration. | Ablate correlated factors and bonuses; measure incremental rank IC, net returns, drawdown and turnover. |
| 2 | Option history currently stores derived snapshots in normal reports, while full chains are saved only by validation. | Archive timestamped raw chains daily, record selected contracts and missing-data reasons, and track comparable IV tenors and subsequent realized volatility. Only then add IV percentile, volume anomalies or option-P&L backtests. |
| 3 | No sector cap or portfolio volatility target. | Compare sector caps and volatility-scaled sizing against the same universe with transaction costs and held-out periods. |

The allocator fix does not repair the backtest's later full-investment
renormalization, so the current backtest can still exceed the nominal cap in a
small universe. Existing backtest returns should not be presented as proof of
live-equivalent performance. No profitability claim follows from these fixes.
