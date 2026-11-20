# 波段抄底 — TQQQ / SOXL swing desk

`leveraged_swing.py` adds a daily rule-based dip-buying plan for the two 3x
ETFs. It is a report section, not an order generator: `alpaca_trader.py` does
not read it.

## Signals

Indicators are computed on the unlevered underlying (QQQ for TQQQ, SOXX for
SOXL): drawdown from the 252-day closing high, RSI(14), Bollinger %B (20, 2),
5-day return, and distance to the 20/50/200-day averages. Fear is VIX level,
its 1-year percentile, VIX/VIX3M, and the distance from VIX's 10-day closing
high. News sentiment (average sentiment component of the scored pool),
breadth, high-yield spread, macro reduce triggers and the event calendar come
from the rest of the report; they have no long history, so they can only
shrink or delay an entry.

## Entry ladder (cumulative % of a separate swing sleeve)

| Tier | TQQQ (QQQ) | SOXL (SOXX) | Fear | Sleeve |
|---|---|---|---|---|
| 1 试探仓 | drawdown ≤ −6% and oversold | ≤ −10% and oversold | VIX ≥ 20, rolled ≥5% off 10-day high | 25% |
| 2 加仓 | ≤ −10% and RSI<40 or %B<0.15 | ≤ −18% | VIX ≥ 25 or 1y rank ≥ 90%, rolled over | 60% |
| 3 恐慌满仓 | ≤ −16% | ≤ −28% | VIX ≥ 30, no confirmation needed | 100% |

Oversold = RSI<35, %B<0.05, or a 5-day drop of 4% (QQQ) / 7% (SOXX). When the
price tier is met but VIX is still within 5% of its 10-day high, the call is
"等待确认" with the VIX level that confirms it.

Guards: a ⭐⭐⭐ event within one day, HY OAS > 4.50%, or two or more macro
reduce triggers cap the position at the probe size; an underlying below its
200-day average halves it; hot sentiment shrinks a probe further.

## Exits

Sell a third when the underlying closes above its 20-day average, another
third above the 50-day average or RSI > 60, the rest at RSI > 70 or the upper
band. Stop: underlying closes 4% below the 10-day closing low at entry (tier 3
exempt). Time stop: 15 sessions without a 20-day reclaim, tier back to zero →
sell half.

## Replay (2010-02 to 2026-11, closes, no costs)

The section recomputes these every day from the same functions.

| Variant | TQQQ episodes / win / mean / worst | SOXL episodes / win / mean / worst |
|---|---|---|
| No VIX confirmation | 43 / 58% / +5.7% / −20.8% | 50 / 60% / +6.8% / −40.0% |
| **VIX rollover for tiers 1–2 (adopted)** | 28 / 71% / +11.1% / −15.4% | 31 / 74% / +15.9% / −25.2% |
| Up-day confirmation instead | 35 / 60% / +6.1% / −34.5% | 32 / 56% / +5.9% / −44.1% |
| No stop (run on the unconfirmed entries) | 28 / 86% / +11.2% / −15.9%, open drawdown −51% | 31 / 84% / +16.7% / −17.3%, open drawdown −53% |

Also tried: rollover-or-up-day, rollover required for tier 3 as well, a
10-session time stop and a 7% stop (the last two only on the unconfirmed and
rollover-or-up-day entries). None beat the adopted row on both products.
Removing the stop raised the win rate but left open losses near −50%, so the
stop stays; the stop was not re-tested without it on the adopted entries.

Limits: the confirmation rule was chosen among seven variants on this same
history, so the adopted row is in-sample; there is no out-of-sample test. The
sample starts in 2010 and contains no 2008-style bear market. In 2022 the
adopted TQQQ rule lost about 22% of the sleeve across six episodes. Fresh
triggers did not raise the share of positive 10-day outcomes much above the
unconditional rate (about 60%); the gain is in the size of the median move,
with a fatter left tail (10th percentile around −20%). Deepest mark-to-market
loss inside an episode was about −50% (TQQQ) and −37% (SOXL).
