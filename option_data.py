"""
Option-chain data source for the report desks.

Yahoo's option endpoint is unreliable from the Cloud Run job (it answers the
first requests, then silently returns empty chains once the 157-name equity
fetch has used up its goodwill), which left every options desk saying
"期权数据不可用" while the same code worked on a laptop. Alpaca's option
contracts (open interest) plus indicative snapshots (bid/ask, IV, greeks, quote
timestamps) are available on the paper account and do not depend on Yahoo.

`ticker(symbol)` returns an object with the yfinance ``Ticker`` surface the
advisors already use — ``options``, ``option_chain(expiry)``, ``history()``,
``calendar`` and ``get_info()`` — backed by Alpaca for the chain and yfinance
for history, earnings and quote type. Alpaca daily bars stand in when the
yfinance history call itself fails. Set ``OPTION_DATA_SOURCE=yfinance`` to
force the old path; without Alpaca keys the fallback is automatic.
"""
import math
import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yfinance as yf

import market_bars

CONTRACT_HORIZON_DAYS = 400   # far enough for LEAP-free expiry pickers
SNAPSHOT_BATCH = 100          # symbols per snapshot request
CHAIN_COLUMNS = ["contractSymbol", "strike", "bid", "ask", "lastPrice",
                 "impliedVolatility", "openInterest", "volume", "delta",
                 "contractSize", "quoteTime"]

_CLIENTS = {}
_CONTRACTS = {}
_LOGGED = set()


def alpaca_keys():
    key = os.environ.get("ALPACA_API_KEY", "").strip()
    secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
    return (key, secret) if key and secret else None


def source():
    """'alpaca' when keys are present (and not overridden), else 'yfinance'."""
    forced = os.environ.get("OPTION_DATA_SOURCE", "").strip().lower()
    if forced in ("yfinance", "yahoo"):
        return "yfinance"
    return "alpaca" if alpaca_keys() else "yfinance"


def _log(msg):
    if msg not in _LOGGED:
        _LOGGED.add(msg)
        print(f"  [option_data] {msg}")


def _clients():
    if "trading" not in _CLIENTS:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.trading.client import TradingClient
        key, secret = alpaca_keys()
        base = os.environ.get("ALPACA_API_BASE_URL", "")
        paper = "paper" in base or not base
        _CLIENTS["trading"] = TradingClient(key, secret, paper=paper)
        _CLIENTS["options"] = OptionHistoricalDataClient(key, secret)
    return _CLIENTS["trading"], _CLIENTS["options"]


def _num(value):
    try:
        f = float(value)
        return f if math.isfinite(f) else np.nan
    except (TypeError, ValueError):
        return np.nan


def fetch_contracts(symbol, today=None, trading=None):
    """All active contracts on `symbol` expiring within the horizon."""
    from alpaca.trading.enums import AssetStatus
    from alpaca.trading.requests import GetOptionContractsRequest

    today = today or date.today()
    trading = trading or _clients()[0]
    out, token = [], None
    while True:
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol], status=AssetStatus.ACTIVE,
            expiration_date_gte=today,
            expiration_date_lte=today + timedelta(days=CONTRACT_HORIZON_DAYS),
            limit=10000, page_token=token)
        page = trading.get_option_contracts(req)
        out.extend(page.option_contracts or [])
        token = getattr(page, "next_page_token", None)
        if not token:
            break
    return out


def fetch_snapshots(symbols, options=None):
    """{contract symbol: OptionsSnapshot} for the given contracts."""
    from alpaca.data.enums import OptionsFeed
    from alpaca.data.requests import OptionSnapshotRequest

    options = options or _clients()[1]
    result = {}
    symbols = list(symbols)
    for i in range(0, len(symbols), SNAPSHOT_BATCH):
        batch = symbols[i:i + SNAPSHOT_BATCH]
        result.update(options.get_option_snapshot(
            OptionSnapshotRequest(symbol_or_symbols=batch, feed=OptionsFeed.INDICATIVE)) or {})
    return result


def _frame(contracts, snapshots, underlying):
    rows = []
    for c in contracts:
        s = snapshots.get(c.symbol)
        quote = getattr(s, "latest_quote", None) if s else None
        trade = getattr(s, "latest_trade", None) if s else None
        greeks = getattr(s, "greeks", None) if s else None
        standard = (str(getattr(c, "size", "")) == "100"
                    and (getattr(c, "root_symbol", underlying) or underlying) == underlying)
        rows.append({
            "contractSymbol": c.symbol,
            "strike": _num(c.strike_price),
            "bid": _num(getattr(quote, "bid_price", None)) if quote else np.nan,
            "ask": _num(getattr(quote, "ask_price", None)) if quote else np.nan,
            "lastPrice": _num(getattr(trade, "price", None)) if trade else np.nan,
            "impliedVolatility": _num(getattr(s, "implied_volatility", None)) if s else np.nan,
            "openInterest": _num(getattr(c, "open_interest", None)),
            "volume": np.nan,  # not part of the snapshot; OI carries liquidity
            "delta": _num(getattr(greeks, "delta", None)) if greeks else np.nan,
            "contractSize": "REGULAR" if standard else "NONSTANDARD",
            "quoteTime": getattr(quote, "timestamp", None) if quote else None,
        })
    frame = pd.DataFrame(rows, columns=CHAIN_COLUMNS)
    if frame.empty:
        return frame
    frame = frame.sort_values("strike").reset_index(drop=True)
    frame["openInterest"] = frame["openInterest"].fillna(0)
    # Quotes with no bid are not tradable; make them look the way Yahoo does.
    frame["bid"] = frame["bid"].fillna(0.0)
    frame["ask"] = frame["ask"].fillna(0.0)
    # Deep-OTM strikes come back without IV; borrow the nearest quoted IV so
    # the delta ladders stay continuous. Consumers still clamp to a sane band.
    iv = frame["impliedVolatility"].where(frame["impliedVolatility"] > 0)
    if iv.notna().any():
        frame["impliedVolatility"] = iv.interpolate(limit_direction="both")
    return frame


class AlpacaOptionTicker:
    """yfinance-Ticker-shaped view: Alpaca chains, yfinance everything else."""

    def __init__(self, symbol, today=None, yf_ticker=None, trading=None, options=None):
        self.symbol = symbol.upper()
        self.today = today or date.today()
        self._yf = yf_ticker
        self._trading, self._options = trading, options
        self._contracts = None
        self.last_error = None
        self.source = "alpaca"

    # ---- yfinance passthrough -------------------------------------------
    @property
    def yf(self):
        if self._yf is None:
            self._yf = yf.Ticker(self.symbol)
        return self._yf

    def history(self, **kwargs):
        try:
            frame = self.yf.history(**kwargs)
        except Exception as exc:  # throttled / 5xx: try the broker's bars
            _log(f"{self.symbol}: yfinance history failed ({type(exc).__name__}); using Alpaca bars")
            frame = None
        if frame is None or frame.empty or "Close" not in frame:
            return alpaca_history(self.symbol, period=kwargs.get("period", "1y"),
                                  end=self.today)
        return market_bars.drop_unfinished_bars(frame)

    @property
    def calendar(self):
        return self.yf.calendar

    def get_info(self):
        return self.yf.get_info()

    @property
    def info(self):
        return self.yf.info

    # ---- Alpaca chain ------------------------------------------------------
    def _load(self):
        if self._contracts is None:
            trading = self._trading or _clients()[0]
            self._contracts = fetch_contracts(self.symbol, today=self.today, trading=trading)
        return self._contracts

    @property
    def options(self):
        try:
            exps = sorted({c.expiration_date for c in self._load()})
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:120]
            _log(f"{self.symbol}: Alpaca contracts failed ({self.last_error}); falling back to Yahoo chain")
            self.source = "yfinance"
            return self.yf.options
        return tuple(e.isoformat() for e in exps)

    def option_chain(self, expiry):
        if self.source == "yfinance":
            return self.yf.option_chain(expiry)
        exp = date.fromisoformat(expiry)
        contracts = [c for c in self._load() if c.expiration_date == exp]
        if not contracts:
            raise ValueError(f"{self.symbol}: no Alpaca contracts for {expiry}")
        options = self._options or _clients()[1]
        snaps = fetch_snapshots([c.symbol for c in contracts], options=options)
        calls = _frame([c for c in contracts if str(getattr(c.type, "value", c.type)) == "call"],
                       snaps, self.symbol)
        puts = _frame([c for c in contracts if str(getattr(c.type, "value", c.type)) == "put"],
                      snaps, self.symbol)
        times = [t for t in list(calls.get("quoteTime", [])) + list(puts.get("quoteTime", [])) if t]
        return SimpleNamespace(calls=calls, puts=puts, source="alpaca",
                               quote_time=max(times) if times else None)


def alpaca_history(symbol, period="1y", end=None):
    """Daily OHLCV from Alpaca shaped like yfinance history (fallback only)."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    key, secret = alpaca_keys()
    days = {"5d": 10, "1mo": 45, "3mo": 100, "6mo": 200, "1y": 380, "2y": 750}.get(period, 380)
    end = end or date.today()
    client = StockHistoricalDataClient(key, secret)
    bars = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbol, timeframe=TimeFrame.Day,
        start=datetime.combine(end - timedelta(days=days), datetime.min.time()),
        adjustment="all"))
    frame = bars.df
    if frame is None or frame.empty:
        raise ValueError(f"{symbol}: no Alpaca bars")
    if isinstance(frame.index, pd.MultiIndex):
        frame = frame.xs(symbol, level=0)
    frame = frame.rename(columns={"open": "Open", "high": "High", "low": "Low",
                                  "close": "Close", "volume": "Volume"})
    frame.index = pd.DatetimeIndex(frame.index).tz_convert("America/New_York").normalize()
    frame.index.name = "Date"
    return market_bars.drop_unfinished_bars(frame[["Open", "High", "Low", "Close", "Volume"]])


class CompletedBarsTicker:
    """yf.Ticker whose history() never includes the session in progress."""

    def __init__(self, symbol):
        self.symbol = symbol.upper()
        self._yf = yf.Ticker(symbol)
        self.source = "yfinance"

    def history(self, **kwargs):
        return market_bars.drop_unfinished_bars(self._yf.history(**kwargs))

    def __getattr__(self, name):
        return getattr(self._yf, name)


def ticker(symbol, today=None):
    """Chain-capable ticker for the desks; yfinance when Alpaca is unavailable."""
    if source() == "alpaca":
        return AlpacaOptionTicker(symbol, today=today)
    return CompletedBarsTicker(symbol)


def atm_iv(frame, spot, fallback=None):
    """Implied vol at the strike nearest spot, or `fallback` when unquoted."""
    if frame is None or len(frame) == 0:
        return fallback
    idx = (frame["strike"] - spot).abs().idxmin()
    iv = _num(frame.loc[idx, "impliedVolatility"])
    return float(iv) if iv == iv and iv > 0 else fallback


def quote_note(chain):
    """One-line provenance for report footers."""
    if getattr(chain, "source", None) == "alpaca":
        t = getattr(chain, "quote_time", None)
        if isinstance(t, datetime):
            t = t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"报价来源 Alpaca indicative（最近报价 {t or '未知'}）"
    return "报价来源 Yahoo 快照（无时间戳）"
