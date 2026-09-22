# Report layout and validation

`generate_report()` saves matching `recommendations_TIMESTAMP.md` and `.html`
files, while preserving its existing `(markdown_path, markdown_content)` return.
The HTML is self-contained, with section navigation, horizontally scrolling
tables on narrow screens, and expandable per-stock details. Email uses the same
Markdown renderer with inline table styles and all stock details expanded.
Raw HTML from report text is escaped, and unsafe link schemes are rejected by
the Markdown parser. No scripts, remote fonts, or other assets are needed.

Reading order: overview, General and cash plan, macro desk, the AI Portfolio
roll-up (`AI_PORTFOLIO.md`), the 半导体加仓 book (`CHIP_LAYOUT.md`), the
光学互联 sleeve (`OPTICS_LAYOUT.md`), the SaaS Watch list (`SAAS_WATCH.md`), the
抄底布局 diversified book (`DIP_LAYOUT.md`), stock and options actions (covered-call ladders for
the `CC_WATCH` list, the sell-put radar, their unified-score second opinions, and the
AI 标的 Sell Put 方案 desk, see `AI_SELL_PUT_PLAN.md`), rankings and
allocation, research/methodology, individual details.

Single newlines inside a paragraph render as line breaks (`breaks=True`), so the
line-per-reading layout of the cash plan and desks is preserved instead of
being merged into one paragraph.

## Email

Gmail clips message bodies above ~102KB and hides the rest behind "View entire
message", which is where the 2026-09-09 report was cut off (the body reached
~770KB, so the clip landed inside Top Picks). `report_format.email_digest`
therefore builds the mailed body from the report sections in order until
`EMAIL_BUDGET` (92KB) is reached. A section that renders above
`EMAIL_SECTION_CAP` (22KB) is reduced to its lead paragraphs and first summary
table; Detailed Analysis and Disclaimer never go in the body. A closing
「邮件正文说明」 block lists what was compacted or left out. The full `.html`
and `.md` reports are attached by `email_sender.send_report_email`, and the
plain-text part carries the console summary. In email tables, cells up to 28
characters are `white-space:nowrap` so prices, labels and dates do not wrap
into one-word-per-line towers in narrow mail panes.
Low-ranked stocks are explicitly a relative ranking, not a blanket sell list.
Experimental scores retain their disabled status. Per-stock budget percentages
are separate from the General cash pool and its conditional deployment cap.

## Validation

Run `.venv_trading/bin/python -m pytest -q` for regression tests.

Run `.venv_trading/bin/python scripts/validate_report.py` for the integration
sample. It reads 16 saved equity histories from `reports/score_validation_cache`,
fetches 17 public macro series and up to 8 option snapshots, and writes the full
report, raw input JSON, and validation results under `reports/report_validation`.
The validator checks Markdown table widths, section ordering, unique navigation
targets, web/email table parity, individual detail coverage, disabled candidate
score promotion, consistent per-name option labels in the email summary, the
AI Portfolio placement, and that the email digest stays under the Gmail budget
while keeping the decision desks (it also writes `*_email.html`).
Unavailable option snapshots must remain unavailable in the replay. Covered-call
ladders (`CC_WATCH`) are blanked and the sell-put radar's chain fetch is
disabled during validation.

Replay a saved sample without network access:

```sh
.venv_trading/bin/python scripts/validate_report.py --replay reports/report_validation/TIMESTAMP/inputs.json
```

Run replays on the same analysis date: event windows and data freshness are
deliberately evaluated against the current date. This is not a historical
backtest. Inputs identify their scope and saved equity dates; missing news
sentiment has zero confidence. AI, broker-account sections, stock discovery,
email delivery and trading are excluded. HTML validation checks structure, not
pixel-level rendering across browsers or individual email clients.

The 2026-09-08 sample covers 20 sections, 41 tables, 16 stock details and 8
option requests (4 usable; CVX, UNH, JNJ and CAT unavailable). Tests: 181 passed.
The dependency and Docker copy list include the shared renderer and the related
cash, score and options modules; deployment itself is outside this validation.

## Privacy

Nothing in the report or email may let a reader infer the author's assets.
The cash entry plan is expressed only as percentages of an unnamed pool, the
covered-call desk works from a ticker watch list with no share or contract
counts, and no desk reads broker positions: `sell_put_advisor.held_tickers()`
is always empty, so no name is ever marked, excluded or sized as "held".
