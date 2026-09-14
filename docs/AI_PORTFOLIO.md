# AI Portfolio — 核心 AI 名单

`ai_portfolio.py` adds a report section (and an email block) that watches a
fixed list of AI names separately from the rest of the universe:

```
NVDA, AVGO, GOOGL, META, TSM, MU, AMAT, ORCL, ANET, SNPS
```

Override with the `AI_PORTFOLIO` env var (comma-separated). A second tier, the
观察名单, defaults to

```
ASML, KLAC (设备) · MSFT, AMZN (云) · VRT (电力散热) · STX, SNDK (存储) ·
PLTR, CRWD (软件) · CRDO, COHR (光模块)
```

and is overridden with `AI_PORTFOLIO_WATCH`; names already in the core list are
dropped from it. It renders as its own 15-column table (with a 层 column) under
the core tables, plus a one-line "今日过线" roll-up, and is kept out of the core
组合概况. The section sits
right after Macro Desk and before 今日建议买入 in the report, and its email
block (`=== AI PORTFOLIO ===`) follows the macro lines in the summary.

## What it shows

It is a roll-up, not a new data pull:

| Block | Source |
|---|---|
| 行情与评分 table: price, 1D/5D/1M/3M, 距高点, RSI, SMA50/SMA200 with the gap to price, score (grade), 评级, Regime | the day's scored rows (`indicators`, `score_result`) |
| 今日动作 table: 涨跌结构, 买入档位, 用多少钱, 买点(回踩/分批/接股), Sell Put 首选, 下次财报 | `daily_watch.annotate` cards (stock / buy / options labels), `ai_sell_put_plan` names (levels, chosen strike, earnings) |
| 组合概况 / 今日档位 lines | counts across the list: in-run, average score and 1-day move, names above SMA200, held names, buy / scale-in / watch / no-buy buckets, names with a writable put |
| Per-name bullet | buy note, trend note, put entry note, first two scoring reasons |

Every AI Portfolio name (core and watch tier) is also in the default `AI_PUT_WATCH` list
(`ai_sell_put_plan.py`), so buy levels, earnings and a put ticket exist for
each of them. A name missing from today's scored run (data gap, skipped ticker)
still gets a row that says 不在今日评分池 and keeps the plan data it has.

## Wiring

- `report_generator.generate_report` builds the roll-up after option decisions
  and the sell-put plan exist, renders the section, and copies the data into
  the optional `ai_portfolio` dict argument so `daily_report.run_daily` can
  print the email block without recomputing.
- `report_format.format_markdown` orders the section between Macro Desk and
  今日建议买入.

## Validation

`scripts/test_ai_portfolio.py` covers the list overrides (core and watch), the
roll-up of cards and plan data, missing names, table widths (14, 8 and 15
columns), and the email lines. `scripts/validate_report.py` asserts the section renders ahead of
今日建议买入 and stays inside the mailed digest.
