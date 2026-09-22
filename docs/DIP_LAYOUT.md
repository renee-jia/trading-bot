# 抄底布局 — Diversified Portfolio

`dip_layout.py` adds a report section (and an email block) that watches a fixed
non-tech book for 抄底 / 加仓, separately from the AI Portfolio and SaaS Watch:

| 模块 | 股票 | 定位 |
|---|---|---|
| Diversified Core | BRK.B | 多元化核心，保险/铁路/能源/工业/现金流 |
| Healthcare – Managed Care | UNH | 医保龙头，和科技股相关性低 |
| Healthcare – Pharma | ABBV | 高现金流、分红、药品管线 |
| Healthcare – MedTech | ISRG | 手术机器人，长期成长 |
| Financials – Bank | JPM | 大型银行里最全面，IB/交易/存贷款/资管 |
| Financials – Payments | V | 高 ROIC 全球支付网络 |
| Financials – Payments | MA | 和 V 类似，更偏全球支付增长 |
| Industrials – Electrification | ETN | 电气化、电网、数据中心供电 |
| Industrials – Grid Infrastructure | PWR | 电网、输电、基础设施建设 |
| Industrials – Machinery | CAT | 工程机械、资源周期、基建 |
| Consumer Defensive | COST | 高质量消费 compounder |
| Consumer Defensive | WMT | 稳定消费、零售、防御性 |
| Consumer Discretionary | BKNG | 旅游平台，高 FCF |
| Materials – Copper | FCX | 铜价、电气化、全球工业周期 |
| Energy | XOM | 能源、现金流、通胀对冲 |
| Utilities / Power | NEE | 电力、公用事业、可再生能源 |
| Nuclear / Power | CEG | 核电、电力需求、数据中心电力主题 |
| Defense / Aerospace | GE | 航空发动机、长期 aftermarket |
| Defense / Aerospace | RTX | 航空航天 + 国防 |
| Insurance | CB | 高质量财险，和 tech 因子差异很大 |

Module and 定位 are the user's fixed view (the `LAYOUT` table) and do not move
with the tape. Override the list with `DIP_LAYOUT` (comma-separated).
`BRK.B` / `BRKB` / `BRK-B` all score as Yahoo ticker `BRK-B` and display as
`BRK.B`. A name without a thesis entry still gets a row.

The section sits after SaaS Watch and before 今日建议买入. The email block
(`=== 抄底布局 ===`) follows SaaS Watch in the summary.

## What it shows

A roll-up, not a new data pull. It reuses the row builder and cell formatters
from `ai_portfolio.py`:

| Block | Source |
|---|---|
| 模块与定位 table: 模块, 股票, 定位, price, 1D/5D, 距高点, SMA50/SMA200, RSI, score, 评级, 买入档位 | fixed thesis + the day's scored rows |
| 今日可动手 table: 抄底 vs 加仓, discount, size, note | `daily_watch.annotate` cards |
| 名单概况 / 今日档位 lines | in-run count, average score and 1-day move, names above SMA200, names ≥8% below the 52-week high, buy buckets |
| Per-name bullet | module header, buy note, stock note |

A name missing from today's scored run still gets a row that says 不在评分池.
This column does not replace 今日建议买入; it only answers whether these 20
names are actionable today. The General cash-pool first rung (VIX × SPX) is
independent of single-name dips.

## Wiring

- `report_generator.generate_report` builds the roll-up after SaaS Watch,
  renders the section, and copies the data into the optional `dip_layout`
  dict so `daily_report.run_daily` can print the email block without
  recomputing.
- `report_format.format_markdown` orders the section between SaaS Watch and
  今日建议买入.
- Layout names that were missing from `UNIVERSE` are added there so they
  enter the daily scored run.

## Validation

`scripts/test_dip_layout.py` covers the list override, Berkshire ticker
aliases, the fixed thesis, the roll-up of cards, missing names, table widths
(13 and 8 columns) and the email lines. `scripts/validate_report.py` asserts
the section renders between SaaS Watch and 今日建议买入.
