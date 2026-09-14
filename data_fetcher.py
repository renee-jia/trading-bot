"""
Centralized data fetching for price data, news, and benchmarks.

Handles all yfinance quirks (MultiIndex columns, empty data, etc.) so that
downstream modules receive clean DataFrames.
"""
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import market_bars


def fetch_price_data(ticker, period="2y", interval="1d"):
    """
    Fetch OHLCV price data for a single ticker.

    Returns a clean DataFrame with columns: Open, High, Low, Close, Volume.
    Returns None if data is unavailable.
    """
    try:
        # Use Ticker.history() instead of yf.download() for thread safety.
        # yf.download() shares session state across threads, causing data
        # corruption (merged tickers, duplicate columns) in concurrent fetches.
        t = yf.Ticker(ticker)
        data = t.history(period=period, interval=interval)
    except Exception:
        return None

    if data is None or data.empty:
        return None

    if isinstance(data.columns, pd.MultiIndex):
        data.columns = data.columns.droplevel(1)

    # Deduplicate columns (safety net for any remaining MultiIndex issues)
    if data.columns.duplicated().any():
        data = data.loc[:, ~data.columns.duplicated()]

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(c in data.columns for c in required):
        return None

    data = data[required].copy()
    data.dropna(subset=["Close"], inplace=True)
    # Never score the session in progress: drop today's bar until the close.
    data = market_bars.drop_unfinished_bars(data)

    if len(data) < 50:
        return None

    return data


def fetch_multiple(tickers, period="2y", interval="1d"):
    """
    Fetch price data for multiple tickers. Returns dict of {ticker: DataFrame}.
    Skips tickers that fail.
    """
    results = {}
    for ticker in tickers:
        df = fetch_price_data(ticker, period=period, interval=interval)
        if df is not None:
            results[ticker] = df
    return results


def fetch_news(ticker, max_items=30):
    """
    Fetch recent news headlines for a ticker via yfinance.

    Returns a list of dicts with keys: title, publisher, publish_time, link.
    Returns empty list on failure.
    """
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news
        if not raw_news:
            return []

        articles = []
        for item in raw_news[:max_items]:
            # Handle both old and new yfinance news format
            content = item.get("content", item)  # New format nests under 'content'

            title = content.get("title", "") or item.get("title", "")
            if not title:
                continue

            # Publisher
            provider = content.get("provider", {})
            if isinstance(provider, dict):
                publisher = provider.get("displayName", "")
            else:
                publisher = item.get("publisher", "")

            # Publish time - try multiple formats
            pub_dt = datetime.now()
            pub_date_str = content.get("pubDate", "")
            pub_time = item.get("providerPublishTime", 0)

            if pub_date_str:
                try:
                    # ISO format: "2026-03-05T23:00:00Z"
                    pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    pass
            elif pub_time:
                try:
                    pub_dt = datetime.fromtimestamp(pub_time)
                except (OSError, ValueError):
                    pass

            # Link
            canonical = content.get("canonicalUrl", {})
            if isinstance(canonical, dict):
                link = canonical.get("url", "")
            else:
                link = item.get("link", "")

            articles.append({
                "title": title,
                "publisher": publisher,
                "publish_time": pub_dt,
                "link": link,
            })

        return articles
    except Exception:
        return []


def fetch_hourly_data(ticker, period="1y"):
    """
    Fetch hourly OHLCV data for a single ticker (up to ~730 days).

    Returns a clean DataFrame or None.
    """
    return fetch_price_data(ticker, period=period, interval="1h")


def fetch_benchmark(period="2y"):
    """Fetch SPY as market benchmark."""
    return fetch_price_data("SPY", period=period)


def fetch_stock_info(ticker):
    """
    Fetch company info (sector, market cap, etc.).
    Returns dict or empty dict on failure.
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        return {
            "sector": info.get("sector", "Unknown"),
            "industry": info.get("industry", "Unknown"),
            "market_cap": info.get("marketCap", 0),
            "name": info.get("shortName", ticker),
            "pe_ratio": info.get("trailingPE", None),
            "forward_pe": info.get("forwardPE", None),
            "dividend_yield": info.get("dividendYield", None),
            "beta": info.get("beta", None),
        }
    except Exception:
        return {"sector": "Unknown", "name": ticker}
