# AI 标的 Sell Put 方案

`ai_sell_put_plan.py` adds a report section (and an email block) that answers
four questions every trading day for a **fixed** list of quality AI names,
whether or not the name moved today. The panic-drop radar in
`sell_put_advisor.py` and the unified `Cash-secured Put — 统一评分候选` section
stay as they are; this desk sits next to them and always gives an answer per name.

## Watch list

`AI_PUT_WATCH` (env, comma-separated) overrides the default:

```
AVGO, MSFT, MU, QCOM, MRVL, ASML, LRCX, CDNS, VRT, ANET, PLTR   ← required
NVDA, TSM, AMD, AMAT, KLAC, ORCL, SNPS                          ← desk additions
GOOGL, META, AMZN, STX, SNDK, CRWD, CRDO, COHR                  ← AI Portfolio core + watch names
CRM, NOW, ADBE                                                  ← SaaS Watch top-rated names
```

Each name costs one history pull plus one option-chain pull (contracts and
snapshots from Alpaca via `option_data.py`, see `OPTIONS_RESEARCH.md`), so 29
names add roughly two minutes to the daily job.

A name whose data source returns no expirations at all is labelled
`期权链不可用` (state `no_chain`) rather than `跨财报 / 无合适到期`, so a data
outage is not mistaken for an earnings wait; the stock-add columns still work.

## The four answers

| Column | Rule |
|---|---|
| **建仓** (enter via short put) | `适合` = score ≥ 60, price ≥ 97% of SMA200, no macro blackout, an expiry that clears earnings and FOMC/CPI/NFP/PCE, IV not more than 1pt below trimmed realized vol. `可小量` = score 50–60 (lowest strike, one contract). `权利金薄` = IV below realized vol. `不建议` = below SMA200 or score < 50. |
| **今日加仓** | `今日可加` = 1-day ≤ −1.5% or ≥ 8% below the 52-week high, RSI < 60, and the house buy ladder (`daily_watch.classify_buy`) is not `no_buy`; or price within 1% of SMA20. `分批加` = buy ladder says buy/scale-in and RSI < 68. `不追` = 1-day ≥ +3% or the chase flag. `等回踩` = RSI ≥ 68 or watch-only. Names outside today's scored run show `无评分`. |
| **买点** | 回踩 = SMA20 (or spot × 0.97 when SMA20 is above spot) · 分批 = SMA50 · 接股 = 21-day low, or the lowest ladder strike if lower. Always descending. |
| **Sell Put 首选** | Expiry from the shared 25–60 DTE / pre-earnings / macro-safe picker. Strikes at 0.30 / 0.25 / 0.20 put delta; the summary quotes the ~0.25Δ rung for a full entry and the lowest rung for `可小量` / `权利金薄`. Limit price = mid, bid/ask shown, cash-secured yield on the strike, annualized. |

A macro `caution` window appends "只开远月、少张数" to the entry note;
`blackout`/unknown calendar coverage sets `事件窗口，先不开` while the stock-add
column still works (the freeze is for new short premium, not for shares).

Every detail block also prints the unified options-decision label for the name
when the research overlay evaluated it, so the two sections can be compared
instead of contradicting each other silently.

## Wiring

- `daily_report.run_daily` builds the plan once and passes it to
  `report_generator.generate_report(sell_put_plan=...)` and the email summary.
- `generate_report` builds its own plan when none is supplied and saves
  `reports/ai_sell_put_plan_TIMESTAMP.json` next to the options snapshot.
- `scripts/validate_report.py` stores the plan in `inputs.json`; `--replay`
  feeds it back without network.
- `report_format.format_markdown` orders the section right after
  `Cash-secured Put`.

## Validation

`scripts/test_ai_sell_put_plan.py` covers entry/today/levels/strike selection,
held names, earnings waits, thin premium, event blackout, fetch errors, section
and email rendering, the env override, and a fake-`yfinance` ladder build.
Run `.venv_trading/bin/python -m pytest -q`.

Quotes are Alpaca indicative snapshots (the footer prints the latest quote
time) or Yahoo snapshots without a timestamp when Alpaca is unavailable.
Nothing here is an order: reprice live, reserve strike × 100 cash, and only
sell a put on a name you are willing to own at that strike.
