# Option chain data source (2026-09-09)

Every options desk (`options_research`, `covered_call_advisor`,
`sell_put_advisor`, `ai_sell_put_plan`) gets its chain through
`option_data.ticker(symbol)`. With Alpaca keys present the object serves
expirations and chains from Alpaca — option contracts (open interest, contract
size, root symbol) plus indicative snapshots (bid/ask with timestamp, implied
volatility, greeks) — and delegates history, earnings calendar and quote type to
yfinance, with Alpaca daily bars as the history fallback. Quoted deltas are used
for the ladders when present; Black-Scholes fills gaps. Set
`OPTION_DATA_SOURCE=yfinance` to force the Yahoo chain; without keys that is the
automatic fallback, and any Alpaca failure falls back per symbol.

Why: from the Cloud Run job Yahoo answered the first requests and then returned
empty option chains once the 157-name equity fetch had run, so every desk
reported 期权数据不可用 from at least 2026-08-31 through 2026-09-09 while the same
code worked locally. Snapshot footers now print the quote source and time. The research overlay's
front month prefers the standard monthly inside the 25–45 DTE window (weeklies
at 30 DTE often fail the OI≥50 ATM gate even on AMD-sized names).

# Options entry decisions — v2

The option report now separates **stock direction**, **strategy suitability** and
**contract feasibility**. A 72/100 suitability score is a transparent rule score,
not 72% probability of profit, an expected return or a calibrated recommendation.
Stock portfolio scores and execution weights are unchanged.

## Direction, valuation and entry gates

Direction = `50 + (((technical + trend) / 2) - 50) * min(confidences)`.
These are the original component scores, before composite agreement, acceleration
and regime bonuses. Missing/nonfinite data blocks evaluation. Confidence below
0.5 or a technical/trend disagreement of 30 points blocks new candidates.

- Bullish: direction ≥60, price above SMA50, one-month return positive.
- Bearish: direction ≤40, price below SMA50, one-month return negative.
- Otherwise neutral; this does not establish that the stock will remain flat.
- Bullish debit/put-credit entries also require RSI below 70 and no chase/extension
  flag. Bearish entries require RSI above 30 to avoid shorting an oversold move.

ATM IV must be ≤0.9 of **both** RV21 and RV63 for debit candidates, or ≥1.2 of
both for credit candidates. Both RV measures include gaps and use annualized
adjusted-close log returns. Spot uses unadjusted Close. IV/RV is a historical
richness proxy, not an estimated future variance premium. P/C and moneyness skew
are descriptive; neither is converted into buying/selling direction.

| Direction and context | Structures considered |
|---|---|
| Bullish + cheap IV | Bull call debit spread |
| Bearish + cheap IV | Bear put debit spread |
| Bullish + rich IV + not already held | Bull put credit spread, cash-secured put |
| Bearish + rich IV | Bear call credit spread |
| Neutral + rich IV + held | Covered call, conditional on available shares and willingness to sell |

A strong uptrend no longer automatically triggers covered calls, and a low stock
score is not a naked-call sell signal. A high composite stock score or a large
drop alone cannot authorize a put sale. Existing holdings do not get additional
short-put candidates.

Unknown/crossing earnings (including a two-day buffer), unknown macro-calendar
coverage, event blackout, missing RV or missing/stale underlying bar dates block
candidates. A missing far chain or inversion below −3 IV points blocks credit
structures. No-new-short macro stance blocks credit structures but does not
itself prohibit debits. Macro trim blocks new bullish put-credit exposure.
Event-pinned credit expirations are excluded through the shared expiry filter.

## Actual contracts, cost and scoring

Only verified REGULAR contracts, positive two-sided noncrossed quotes, OI≥50 and
spread/mid≤20% qualify. One standard 100-share contract per leg is assumed.
The near expiration targets 30 days within 25–45 DTE; far IV targets 60 within
55–90 DTE. Debit spreads buy near ATM and sell OTM, targeting the IV-derived move
clamped to 3–10% of spot. Credit spreads sell OTM at that target and buy a wing
approximately 5% of spot farther out. These are bounded template candidates,
not a search for the globally optimal expiry/strike pair.

Entry reference uses **buy at ask / sell at bid**, not assumed mid fills. Each
candidate shows strikes, expiry, net premium, expiration breakeven, maximum gain,
maximum loss and capital reference. Reject nonpositive or width-exceeding net
prices. Debit maximum gain/risk must be at least 0.5. Sum of leg bid/ask widths
relative to maximum loss must be ≤20%.

Suitability = 40% direction alignment + 25% valuation + 20% liquidity + 15% payoff:

- Alignment: bullish direction score, its bearish complement, or
  `100 - 2*abs(direction-50)` for neutral covered calls.
- Valuation: credit `clip((min(IV/RV21, IV/RV63)-1)*200, 0, 100)`;
  debit `clip((1-max(IV/RV21, IV/RV63))*400, 0, 100)`.
- Liquidity: `max(0, 100*(1 - spread_cost_fraction/0.2))`.
- Payoff: `min(100, maximum_gain/maximum_loss*100)` for spreads; a fixed 50 for
  stock-backed structures, whose downside is not comparable to debit risk.
- All gates must pass and suitability must reach 60. Scores across these
  structures are screening heuristics, not comparable expected returns.

All numerical cutoffs and weights are uncalibrated hypotheses. They require
walk-forward validation before performance claims or automatic trading use.

## One decision across report sections

The research table, Options Desk, watch/mover tables, covered-call/put sections
and email summary consume the same per-ticker decision. Unscanned names show
not evaluated rather than inheriting a stock-only option signal. Legacy standalone
section builders without these decisions wait instead of generating independent
ladders. Their low-level quote helpers remain available for diagnostics.

Daily snapshots include selected-expiry filtered contracts and derived metrics,
RV63, underlying date, retrieval UTC timestamp, component scores, reasons and
candidate legs. They are not a complete historical option-chain archive.

Yahoo bid/ask freshness is unverified: the retrieval time is not a quote timestamp.
Every candidate remains **conditional**, requiring current quotes/repricing,
fees, available funds, existing option exposure, dividends and assignment checks.
Covered calls need 100 unencumbered shares per contract and willingness to sell;
CSPs need strike×100 cash and willingness to take delivery. Spread maximum loss
is an expiration payoff assuming both legs are managed together; early assignment
can create temporary stock/cash obligations. Covered-call loss is marked from
today's stock price, not the unknown original cost basis.

These are **entry** signals. No existing option positions/costs were provided, so
no stop-loss, take-profit, close or roll instructions are inferred.

## Calendar and validation

Fed/BLS dates were checked on 2026-09-08; incorrect November/December CPI and
December FOMC entries were corrected and missing October/December employment
releases added. Complete coverage for the selected NFP/CPI/FOMC/PCE set is
currently verified only for September 2026. Default dates outside that interval,
or an empty upcoming calendar, return unknown. It is not an automatically refreshed
calendar and excludes unscheduled events and other releases.

- [Federal Reserve FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- [BLS CPI calendar](https://www.bls.gov/schedule/news_release/cpi.htm)
- [BLS employment calendar](https://www.bls.gov/schedule/news_release/empsit.htm)
- [BEA release schedule](https://www.bea.gov/news/schedule)

140 offline tests passed after the decision changes, including bullish/bearish
and debit/credit scenarios, exact spread/single-leg payoffs, poor liquidity,
conflicting or missing inputs, calendar coverage, and report decision consistency.
The public-live validation script checks AAPL/NVDA/SPY data and independently
recomputes RV21, renders the report and checks email/section agreement, without
sending messages or submitting trades:

`.venv_trading/bin/python scripts/validate_options_research.py`

Raw validation chains, prices, report, snapshot and validation summary are stored
under `reports/validation/`. This verifies implementation behavior, not profitability.
Historical options, option fill costs and account-aware exits remain unvalidated.

Financial definitions:
- [OIC: volatility and Greeks](https://www.optionseducation.org/referencelibrary/faq/technical-information)
- [OIC: bear put spread](https://www.optionseducation.org/strategies/all-strategies/bear-put-spread)
- [OIC: bull put spread](https://prd-web.optionseducation.org/strategies/all-strategies/bull-put-spread-credit-put-spread)

Stock news remains compact: short summary, up to two risk flags and two sources;
the underlying news-analysis inputs are retained.
