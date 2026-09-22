# 半导体加仓 — Chip Portfolio

`chip_layout.py` watches two fixed views in one section:

1. **加仓优先级** (`CHIP_PRIORITY`): AVGO → TSM → AMAT → SNPS → COHR → NVDA → LRCX → ALAB → MRVL → CRDO → AMD → MU
2. **估值与首批区间** (`CHIP_LAYOUT`): NVDA / AVGO / TSM / ASML / MU / KLAC / LRCX / AMAT, with reference Forward PE, Wall St. PT and the first-batch zone

Priority and zones do not move with the tape. Live price, 52-week drawdown, implied upside vs PT, zone status and the add verdict update every report.

The section sits after AI Portfolio and before 光学互联. Email block: `=== 半导体加仓 ===`.

`scripts/test_chip_layout.py` covers the list override, zone status, priority roll-up and the email lines.
