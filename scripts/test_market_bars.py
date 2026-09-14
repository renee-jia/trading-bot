"""The session in progress must never reach the analysis or the report."""
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pandas as pd

sys.path[:0] = [str(Path(__file__).resolve().parents[1])]
import market_bars as mb

NY = ZoneInfo("America/New_York")


def frame(index):
    return pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1}, index=index)


def test_drops_todays_daily_bar_before_close_keeps_it_after():
    idx = pd.DatetimeIndex(["2026-09-10", "2026-09-11", "2026-09-14"], tz=NY)
    at_open = datetime(2026, 9, 14, 9, 30, tzinfo=NY)
    after_close = datetime(2026, 9, 14, 16, 0, tzinfo=NY)
    assert mb.last_session_date(mb.drop_unfinished_bars(frame(idx), at_open)) == "2026-09-11"
    assert mb.last_session_date(mb.drop_unfinished_bars(frame(idx), after_close)) == "2026-09-14"


def test_handles_chicago_and_naive_indices():
    chicago = pd.DatetimeIndex(["2026-09-11", "2026-09-14"], tz=ZoneInfo("America/Chicago"))
    naive = pd.DatetimeIndex(["2026-09-11", "2026-09-14"])
    morning = datetime(2026, 9, 14, 6, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
    for idx in (chicago, naive):
        assert list(mb.session_dates(mb.drop_unfinished_bars(frame(idx), morning).index).date) == [pd.Timestamp("2026-09-11").date()]


def test_intraday_bars_from_open_session_are_dropped():
    idx = pd.DatetimeIndex(["2026-09-11 15:30", "2026-09-14 09:30", "2026-09-14 10:30"], tz=NY)
    mid = datetime(2026, 9, 14, 10, 45, tzinfo=NY)
    assert mb.last_session_date(mb.drop_unfinished_bars(frame(idx), mid)) == "2026-09-11"


def test_untouched_when_nothing_is_unfinished_and_none_passthrough():
    idx = pd.DatetimeIndex(["2026-09-10", "2026-09-11"], tz=NY)
    f = frame(idx)
    assert mb.drop_unfinished_bars(f, datetime(2026, 9, 14, 9, 30, tzinfo=NY)) is f
    assert mb.drop_unfinished_bars(None) is None
    assert mb.last_session_date(pd.DataFrame()) is None
