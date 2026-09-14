"""Keep only completed US-session bars.

Yahoo and Alpaca both return the current session's in-progress bar as soon
as the market opens. A report run at 09:30 ET would otherwise treat the
opening print as the day's close, so every "今日涨跌" and every indicator
would be built on a one-minute stub. This module drops that bar until the
session has closed; after 16:00 ET the day's bar is kept.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

NY = ZoneInfo("America/New_York")
SESSION_CLOSE = time(16, 0)


def session_dates(index):
    """Session date (US/Eastern) for each bar; tz-naive input is taken as-is."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert(NY)
    return idx.tz_localize(None).normalize()


def last_session_date(frame):
    """ISO date of the newest bar, or None."""
    if frame is None or len(frame) == 0 or not isinstance(frame.index, pd.DatetimeIndex):
        return None
    return session_dates(frame.index)[-1].date().isoformat()


def drop_unfinished_bars(frame, now=None):
    """Remove bars from a session that has not closed yet (daily or intraday).

    Before 16:00 ET every bar dated today is dropped; after the close today's
    bar is complete and kept. Bars dated after today are always dropped.
    """
    if frame is None or len(frame) == 0 or not isinstance(frame.index, pd.DatetimeIndex):
        return frame
    now = now or datetime.now(NY)
    if now.tzinfo is None:
        now = now.replace(tzinfo=NY)
    now = now.astimezone(NY)
    dates = session_dates(frame.index).date
    keep = dates <= now.date() if now.time() >= SESSION_CLOSE else dates < now.date()
    return frame if keep.all() else frame.loc[keep]
