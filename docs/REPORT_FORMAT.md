# Report layout and validation

`generate_report()` saves matching `recommendations_TIMESTAMP.md` and `.html`
files, while preserving its existing `(markdown_path, markdown_content)` return.
The HTML is self-contained, with section navigation, horizontally scrolling
tables on narrow screens, and expandable per-stock details. Email uses the same
Markdown renderer with inline table styles and all stock details expanded.
Raw HTML from report text is escaped, and unsafe link schemes are rejected by
the Markdown parser. No scripts, remote fonts, or other assets are needed.

Reading order: overview, General and cash plan, macro desk, stock and options
actions (including the AI 持有标的 Sell Put 方案 desk, see `AI_SELL_PUT_PLAN.md`),
rankings and allocation, research/methodology, individual details.
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
score promotion, and consistent per-name option labels in the email summary.
Unavailable option snapshots must remain unavailable in the replay.

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
