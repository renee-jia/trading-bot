# Stock score differentiation — v2

## What changes

The original absolute stock score and recommendation remain active. A bounded,
stock-specific experimental overlay computes a separate candidate score at most
±12 points away. Historical validation did not justify promoting it. Reports show
the active score, candidate score, hypothetical change, three signal scores and
a separate peer percentile.
Scores retain one decimal instead of being rounded to integers in the report.

Peer percentiles require at least ten distinct valid tickers. Average ranks handle
ties, so ten identical stocks all receive percentile 50. A bearish universe's best
stock can have percentile 100 while keeping its Avoid/Reduce absolute rating.
Percentiles depend on the chosen pool and are not comparable across different pools.
They never feed back into the absolute score or trading action.

## Added signals

| Signal | Weight in overlay | Definition |
|---|---:|---|
| Market-adjusted strength | 50% | Fit stock/SPY beta on 126 sessions preceding the latest 63. Sum latest 63 daily log returns minus beta×SPY returns, divided by square root of residual squared-return sum. Map with `50+50*tanh(value/2)`. This is a one-market-factor proxy, not a full Fama–French residual-momentum model. |
| Downside resilience | 25% | In the latest 63 aligned sessions, select SPY log-return days below −0.15%; require ≥10. Stock mean / SPY mean on those days is downside capture. Map with `50+50*tanh((1-capture)/0.75)`. |
| Volume-weighted close location | 25% | Latest 21 sessions: volume-weighted `(2*close-high-low)/(high-low)`, with flat-range days neutral. Map with `50+50*tanh(value/0.25)`. This measures closing pressure, not observed institutional flows. |

Adjustment = `12/50 * sum(weight * (signal_score - 50))` over available signals.
Missing signals contribute zero and do not increase remaining weights. Invalid
candles or zero volume omit the volume signal. Insufficient benchmark history or
an invalid beta omits market-adjusted strength. Raw values and signal coverage are
saved in `score_result.research_signals`.

The candidate scales, weights and ±12 bound were specified before the historical paired
comparison; they are research choices, not fitted probabilities. The old model's
confidence is retained without claiming recalibration. Composite agreement and
momentum/regime bonuses are not added again for these new signals.

## Integration and correctness fixes

- `main.analyze_stock` and `backtest_strategy.score_stock_at_date` pass the same
  research signals to the shared private scorer. Its default is diagnostic-only;
  active score, recommendation, grade and confidence remain the baseline.
  The validation harness explicitly uses `promote=True` to evaluate the experiment.
- Live and historical alpha normalization now call the same implementation.
- Scorer fallback weights match config: technical 35%, trend 30%, alpha 20%,
  sentiment 15%. Existing explicitly configured paths keep that baseline blend.
- VIX calculations are capped at the stock's as-of date. Validation supplies
  historical VIX explicitly. The normal one-year VIX fetch can leave older
  backtests without VIX; those observations use the existing missing-data fallback,
  rather than today's VIX.
- Historical benchmark slices use dates rather than stock-array positions, which
  could accidentally include future benchmark rows when start dates differed.
- Option entry direction still requires its own original component and price
  confirmations; a higher adjusted stock score is not an option buy instruction.

`core/scorer.py` is private and git-ignored. Any future deployment must include the
updated private package as well as `stock_signals.py`. No deployment is part of
this change. Momentum-first portfolio construction has not been redesigned.

## Validation design

`python scripts/validate_stock_scores.py` downloads/caches public Yahoo adjusted
OHLCV, SPY and VIX from 2020. It uses monthly cross-sections from 2022 onward,
16 fixed present-day stocks across several industries, 500-bar history windows,
the full existing technical/trend/alpha baseline and no historical sentiment or
hourly data. Both versions receive identical point-in-time inputs.

A score at month-end is compared with the subsequent 20-session return, entering
at the following session's close. Metrics include cross-sectional score standard
deviation/IQR, distinct rounded scores, Spearman rank IC and gross forward
highest-minus-lowest quartile return. The latter is a ranking diagnostic, not
portfolio P&L: it excludes transaction costs, borrow, capital allocation and
portfolio path effects. Selection at a tied quartile boundary includes all ties.

Results are reported for 2022–2024 and a separate 2025+ holdout. Paired circular
three-observation block resampling estimates uncertainty in average IC change.
No iterative threshold fitting is performed on the holdout. The fixed survivor
universe, limited number of names and autocorrelated observations constrain any
performance inference. More distinct scores alone do not establish better signals.

Generated observations, monthly metrics, current scores and report are under
`reports/score_validation/`. Raw cached market data are under
`reports/score_validation_cache/`; delete/refresh that cache explicitly for a later
market snapshot rather than interpreting an old cache as live data.

Conceptual background, not evidence that these particular settings work:
[MSCI momentum methodology overview](https://www.msci.com/indexes/group/momentum-indexes),
[AQR discussion of momentum and changing market betas](https://www.aqr.com/insights/research/working-paper/understanding-momentum-and-reversals).


## Observed validation and adoption decision

The run at `reports/score_validation/20260908_191026/` covered 55 monthly
cross-sections (January 2022–July 2026), 880 stock observations, and all three
signals were available for every observation.

| Metric | Baseline | Candidate |
|---|---:|---:|
| Mean cross-sectional score standard deviation | 13.17 | 15.95 |
| Mean rank IC, all dates | 0.062 | 0.038 |
| Mean rank IC, 2025+ holdout | −0.019 | −0.009 |
| Gross top-minus-bottom 20-session return, all dates | 0.43% | 0.26% |

Dispersion improved approximately 21%, but full-sample ranking quality weakened.
Holdout improvement was small and uncertain. We did **not** optimize another
weight set on these holdout observations or promote the candidate into trading.
Standalone signal IC was approximately 0.001 for residual strength, −0.080 for
downside resilience and 0.034 for close-location volume. These numbers do not
justify treating the risk/resilience signal as an automatic bullish bonus.

Enabled now: decimal precision, relative peer comparison, new diagnostic signals,
consistent default weights, shared alpha normalization and date-safe VIX/benchmark
handling. Experimental score adjustments remain visible but inactive. The old
model's weak recent holdout also means its absolute buy/sell labels should not
be interpreted as validated forward-return probabilities.
