# SaaS Watch — 软件 SaaS 名单

`saas_watch.py` adds a report section (and an email block) that watches a fixed
list of software / SaaS names separately from the AI Portfolio, in the user's
ranking order:

| Ticker | 星级 | 定位 | 主要风险 |
|---|---|---|---|
| CRM | ⭐⭐⭐⭐⭐ | 首选 value SaaS | 增长不够快 |
| NOW | ⭐⭐⭐⭐⭐ | 首选 quality SaaS | 已经反弹不少 |
| ADBE | ⭐⭐⭐⭐½ | 最深 value / contrarian | AI disruption 最大 |
| DDOG | ⭐⭐⭐⭐ | AI/cloud observability winner | 估值仍贵 |
| SNOW | ⭐⭐⭐⭐ | data + AI infrastructure | valuation 高 |
| CRWD | ⭐⭐⭐⭐ | security 最不容易被 AI 替代 | 很贵 |
| NET | ⭐⭐⭐½ | 长期 AI/network optionality | 太贵 |
| WDAY | ⭐⭐⭐ | 便宜但 catalyst 较弱 | 增长/AI monetization |
| INTU | ⭐⭐⭐ | moat 强 | 增长/AI monetization |

The star rating, positioning and risk are the user's fixed view (the `THESIS`
table in the module) and do not move with the tape. Override the list with the
`SAAS_WATCH` env var (comma-separated); a name without a thesis entry renders
with `—`.

The section sits right after AI Portfolio and before 今日建议买入 in the
report, and its email block (`=== SAAS WATCH ===`) follows the AI Portfolio
lines in the summary.

## What it shows

A roll-up, not a new data pull. It reuses the row builder and cell formatters
from `ai_portfolio.py`:

| Block | Source |
|---|---|
| 观点与评分 table: 星级, 定位, 主要风险, price, 1D/1M/3M, 距高点, RSI, SMA50/SMA200 with the gap to price, score (grade), 评级, Regime | fixed thesis + the day's scored rows |
| 今日动作 table: 涨跌结构, 买入档位, 用多少钱, 买点, Sell Put 首选, 下次财报 | `daily_watch.annotate` cards, `ai_sell_put_plan` names |
| 名单概况 / 今日档位 lines | in-run count, average score and 1-day move, names above SMA200, names ≥15% below the 52-week high, held names, buy buckets, names with a writable put |
| Per-name bullet | thesis header, buy note, trend note, put entry note, first two scoring reasons |

Only CRM, NOW and ADBE are on the default `AI_PUT_WATCH` list, so 买点 /
Sell Put 首选 / 下次财报 are filled for those three and show `—` for the rest
unless `AI_PUT_WATCH` is extended. A name missing from today's scored run still
gets a row that says 不在今日评分池.

## Wiring

- `report_generator.generate_report` builds the roll-up right after the AI
  Portfolio one, renders the section, and copies the data into the optional
  `saas_watch` dict argument so `daily_report.run_daily` can print the email
  block without recomputing.
- `report_format.format_markdown` orders the section between AI Portfolio and
  今日建议买入.

## Validation

`scripts/test_saas_watch.py` covers the list override, the fixed thesis, the
roll-up of cards and plan data, missing names, table widths (15 and 8 columns)
and the email lines. `scripts/validate_report.py` asserts the section renders
between AI Portfolio and 今日建议买入.
