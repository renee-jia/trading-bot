# $400k cash entry plan

User-supplied cash pool at 2026-09-08: $400,000. This report-only plan is separate
from the bot's portfolio cash percentage and broker account. It does not submit
orders, change the automatic allocation engine or presume purchases were executed.

| Stage | Minimum VIX | Minimum SPX drawdown | Increment if previous stage funded | Cumulative target | Cash target |
|---|---:|---:|---:|---:|---:|
| Ordinary pullback | 18 | 3% | $50k | $50k | $350k |
| First entry | 20 | 5% | $75k | $125k | $275k |
| Good entry | 23 | 8% | $100k | $225k | $175k |
| Panic | 27 | 10% | $100k | $325k | $75k |
| Deep selloff | 30 | 15% | $75k | $400k | $0 |

Both lower bounds must hold. Upper ranges in the user's table are descriptive,
not exclusion limits: a VIX of 33 with a 12% drawdown remains the panic tier.
Gaps such as 7–8% use the highest already-satisfied lower tier. If only fear or
only drawdown reaches a higher tier, the engine does not upgrade automatically.

The SPX reference is the highest **closing price** in 63 sessions ending at the
plan inception (September 8). Later new closing highs raise the reference; the
reference does not fall as the earlier peak rolls out of a window. Rolling 63-
and 252-session drawdowns are displayed as context. The index is Yahoo `^GSPC`,
not adjusted SPY prices. SPX and VIX must share the latest session, dates must be
fresh, and partial daily bars before 16:00 New York time are excluded. Early-close
days conservatively wait until 16:00 as well.

In a multi-tier gap down, the target can increase by more than one tranche, but
the suggested next review batch is capped at $100k. This is a pacing convention,
not a validated optimal trade size. On recovery, the plan does not recommend
selling stock to restore an earlier cash balance.

## October time rule

“Mid-October” is implemented as October 15, 2026. If VIX is below 18, drawdown is
less than 3%, and no 3% drawdown occurred since plan inception, the target changes
to $50k per elapsed week, capped at the $400k pool. A prior dip followed by recovery
does not count as “never dipped”; that situation requires review of what was
actually bought. Missing historical path data does not satisfy this time rule.

## Cumulative amounts, not repeat orders

Defaults reflect the user's September 8 declaration: $0 deployed, $400k remaining.
After actual buys, update both `CASH_PLAN_DEPLOYED` and
`CASH_PLAN_CAPITAL_AS_OF` in runtime configuration. No purchase is inferred from
an earlier recommendation. Repeated reports show the same outstanding target gap;
they are not instructions to buy that gap again. Old capital declarations are
explicitly marked as needing confirmation. Invalid capital configuration or
missing/stale market data blocks suggested purchases.

The General report and email macro summary share this plan. Existing portfolio
management remains in the general trend assessment; this table describes deployment
of the specified new cash pool. It does not authorize options transactions.

## September 8 validation

Fetched daily closes: SPX 7,673.52; VIX 15.72. Reference high: 7,798.99 on August 13;
drawdown −1.61%. VIX rose 5.36% over five sessions, but remains below the first
18 threshold and the index has not reached a 3% pullback. Current target: no new
cash deployed; retain the declared $400k. Based on this peak, a 3% pullback is
approximately SPX 7,565 and still requires VIX≥18.

VIX × sqrt(30/365) is about 4.5% at the sampled level. This is a volatility-scale
conversion, not a forecast drop, a guaranteed interval or proof of a bottom.
[Cboe describes VIX as 30-day expected volatility derived from SPX options](https://www.cboe.com/tradable-products/volatility-trading).
The sampled SPX close also matches the
[September 8 AP market summary](https://apnews.com/article/cadd309d4fd4933397cd38fe436edb71).

This is a user-defined planning ladder, not a historically optimized strategy.
Tests cover thresholds, asymmetric VIX/drawdown, cumulative accounting, gap moves,
time fallback, stale/missing data, preserved high-water mark, partial bars and
report/email integration. Raw closes and a rendered General sample are saved under
`reports/cash_plan_validation/`. Repeat the public-data check with
`.venv_trading/bin/python scripts/validate_cash_plan.py`.
