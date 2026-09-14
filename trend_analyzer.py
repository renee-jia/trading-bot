"""
Macro trend analysis for buy-and-hold strategy.

Analyzes:
- Relative strength vs SPY benchmark
- Market regime detection (bull/bear/range)
- 52-week price momentum
- Price breadth / health
- VIX fear/greed indicator

Macro context is blended in via macro_score (from macro_analyzer) rather than a
per-stock Claude call — see analyze(macro_score=...).
"""
import os

import pandas as pd
import market_bars
import numpy as np


# Load .env for API key
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_SCRIPT_DIR, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())


# Cache VIX data at module level to avoid re-fetching per stock.
# "ok" tracks a *successful* fetch separately from "attempted", so a transient
# network error doesn't permanently disable VIX for the whole run — we retry
# until we actually get data.
_vix_cache = {"data": None, "ok": False}


def _fetch_vix():
    """Fetch VIX data once and cache it. Retries on prior failure."""
    if _vix_cache["ok"]:
        return _vix_cache["data"]

    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX").history(period="1y", interval="1d")
        vix = market_bars.drop_unfinished_bars(vix)
        if vix is not None and not vix.empty:
            if isinstance(vix.columns, pd.MultiIndex):
                vix.columns = vix.columns.droplevel(1)
            _vix_cache["data"] = vix
            _vix_cache["ok"] = True
    except Exception as e:
        # Leave ok=False so a later call retries instead of caching the failure.
        print(f"  VIX fetch failed (will retry): {e}")

    return _vix_cache["data"]


def analyze(data, benchmark_data, params=None, macro_score=None, vix_data=None):
    """
    Analyze macro trend for a stock.

    Args:
        data: Stock's daily OHLCV DataFrame.
        benchmark_data: SPY daily OHLCV DataFrame.
        params: Optional trend parameters dict.
        macro_score: Optional macro score (0-100) from macro_analyzer to blend in.

    Returns:
        dict with:
            score: 0.0-1.0 trend score
            confidence: 0.0-1.0
            components: sub-analysis details
    """
    if params is None:
        params = _default_params()

    if data is None or len(data) < 60:
        return _empty_result()

    rs = _relative_strength(data, benchmark_data, params)
    regime = _market_regime(data, params)
    momentum = _long_term_momentum(data)
    breadth = _price_breadth(data)
    vix = _vix_analysis(as_of=data.index[-1], data=vix_data)

    # Weighted combination
    weights = {
        "relative_strength": 0.25,
        "regime": 0.25,
        "momentum": 0.20,
        "breadth": 0.10,
        "vix": 0.20,
    }

    # If VIX data unavailable, redistribute weight
    if vix["score"] == 0.5 and not vix.get("signals", {}).get("vix_available", False):
        weights["vix"] = 0
        remaining = sum(v for k, v in weights.items() if k != "vix")
        for k in weights:
            if k != "vix":
                weights[k] = weights[k] / remaining

    score = (
        rs["score"] * weights["relative_strength"]
        + regime["score"] * weights["regime"]
        + momentum["score"] * weights["momentum"]
        + breadth["score"] * weights["breadth"]
        + vix["score"] * weights["vix"]
    )

    # Confidence from component agreement
    sub_scores = [rs["score"], regime["score"], momentum["score"], breadth["score"]]
    if weights["vix"] > 0:
        sub_scores.append(vix["score"])
    variance = np.var(sub_scores)
    confidence = max(0.1, 1.0 - variance * 6)

    components = {
        "relative_strength": rs,
        "regime": regime,
        "momentum": momentum,
        "breadth": breadth,
        "vix": vix,
    }

    result = {
        "score": round(float(np.clip(score, 0, 1)), 4),
        "confidence": round(float(np.clip(confidence, 0, 1)), 4),
        "components": components,
    }

    # Apply macro score adjustment if provided (replaces per-stock Claude calls)
    # The macro_analyzer already runs once with Claude — no need to call Claude
    # 147 more times for each stock. Instead, nudge trend score toward macro sentiment.
    if macro_score is not None:
        macro_normalized = macro_score / 100.0  # Convert 0-100 to 0-1
        quant_score = result["score"]
        # Blend: 85% stock-specific quant + 15% macro environment
        result["score"] = round(float(np.clip(
            quant_score * 0.85 + macro_normalized * 0.15, 0, 1
        )), 4)

    return result


def _default_params():
    return {
        "rs_period": 60,
        "regime_sma_period": 200,
        "regime_adx_threshold": 25,
    }


def _empty_result():
    return {
        "score": 0.5,
        "confidence": 0.0,
        "components": {},
    }


# ---------------------------------------------------------------------------
# VIX Fear/Greed Analysis
# ---------------------------------------------------------------------------

def _vix_analysis(as_of=None, data=None):
    """
    Analyze VIX (CBOE Volatility Index) for market fear/greed.

    VIX < 15: Low fear, complacency (slightly bearish contrarian signal)
    VIX 15-20: Normal/healthy market
    VIX 20-30: Elevated fear (cautious)
    VIX > 30: High fear / panic (contrarian bullish for buy-and-hold)

    Also looks at VIX trend (rising = increasing fear, falling = calming).
    """
    vix_data = _fetch_vix() if data is None else data
    if vix_data is not None and as_of is not None:
        dates = pd.to_datetime(vix_data.index).tz_localize(None).normalize()
        cutoff = pd.Timestamp(as_of).tz_localize(None).normalize()
        vix_data = vix_data.loc[dates <= cutoff]

    if vix_data is None or len(vix_data) < 20:
        return {"score": 0.5, "signals": {"vix_available": False}}

    vix_close = vix_data["Close"]
    current_vix = float(vix_close.iloc[-1])

    signals = {
        "vix_available": True,
        "vix_current": round(current_vix, 2),
    }

    scores = []

    # 1. Absolute VIX level scoring (contrarian for buy-and-hold)
    if current_vix < 13:
        # Extreme complacency — slightly negative (market may be topping)
        level_score = 0.45
        signals["vix_regime"] = "extreme_complacency"
    elif current_vix < 18:
        # Normal/low vol — healthy, mildly bullish
        level_score = 0.60
        signals["vix_regime"] = "low_normal"
    elif current_vix < 25:
        # Elevated — cautious
        level_score = 0.40
        signals["vix_regime"] = "elevated"
    elif current_vix < 35:
        # High fear — contrarian bullish for long-term
        level_score = 0.55
        signals["vix_regime"] = "high_fear"
    else:
        # Panic — strong contrarian buy signal for buy-and-hold
        level_score = 0.65
        signals["vix_regime"] = "panic"
    scores.append(level_score)

    # 2. VIX trend (5-day change)
    if len(vix_close) >= 6:
        vix_5d_change = float((vix_close.iloc[-1] / vix_close.iloc[-6] - 1) * 100)
        signals["vix_change_5d"] = round(vix_5d_change, 2)

        if vix_5d_change < -15:
            # VIX dropping fast — fear subsiding, bullish
            scores.append(0.70)
        elif vix_5d_change < -5:
            scores.append(0.60)
        elif vix_5d_change > 30:
            # VIX spiking — panic onset, near-term bearish
            scores.append(0.30)
        elif vix_5d_change > 10:
            scores.append(0.35)
        else:
            scores.append(0.50)

    # 3. VIX vs its own 50-day moving average
    if len(vix_close) >= 50:
        vix_sma50 = float(vix_close.rolling(50).mean().iloc[-1])
        vix_vs_sma = (current_vix - vix_sma50) / vix_sma50
        signals["vix_sma50"] = round(vix_sma50, 2)
        signals["vix_vs_sma50_pct"] = round(vix_vs_sma * 100, 2)

        if vix_vs_sma < -0.15:
            # VIX well below average — calm market
            scores.append(0.60)
        elif vix_vs_sma > 0.30:
            # VIX well above average — elevated fear
            scores.append(0.35)
        else:
            scores.append(0.50)

    # 4. VIX term structure proxy: current vs 20-day avg
    if len(vix_close) >= 20:
        vix_sma20 = float(vix_close.rolling(20).mean().iloc[-1])
        signals["vix_sma20"] = round(vix_sma20, 2)
        # VIX in contango (current < avg) is normal/bullish
        # VIX in backwardation (current > avg) is bearish/fearful
        if current_vix < vix_sma20 * 0.95:
            scores.append(0.60)
        elif current_vix > vix_sma20 * 1.10:
            scores.append(0.35)
        else:
            scores.append(0.50)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Relative Strength vs Benchmark
# ---------------------------------------------------------------------------

def _relative_strength(data, benchmark_data, params):
    """
    Compare stock performance to SPY over multiple timeframes.
    Outperforming SPY = higher score.
    """
    close = data["Close"]
    signals = {}
    scores = []

    if benchmark_data is None or len(benchmark_data) < 60:
        return {"score": 0.5, "signals": {"benchmark_available": False}}

    spy_close = benchmark_data["Close"]

    # Align dates
    common_idx = close.index.intersection(spy_close.index)
    if len(common_idx) < 20:
        return {"score": 0.5, "signals": {"benchmark_available": False}}

    stock = close.reindex(common_idx)
    spy = spy_close.reindex(common_idx)

    # Relative strength over multiple periods
    for period, label in [(20, "1m"), (60, "3m"), (120, "6m")]:
        if len(stock) >= period:
            stock_ret = (stock.iloc[-1] / stock.iloc[-period]) - 1
            spy_ret = (spy.iloc[-1] / spy.iloc[-period]) - 1
            alpha = stock_ret - spy_ret  # Outperformance

            # Score: positive alpha = bullish
            rs_score = _sigmoid(float(alpha), scale=8)
            scores.append(rs_score)
            signals[f"alpha_{label}"] = round(float(alpha * 100), 2)
            signals[f"stock_return_{label}"] = round(float(stock_ret * 100), 2)
            signals[f"spy_return_{label}"] = round(float(spy_ret * 100), 2)

    # Relative strength trend (is RS improving?)
    if len(stock) >= 60 and len(spy) >= 60:
        rs_line = stock / spy
        rs_sma = rs_line.rolling(20, min_periods=1).mean()
        rs_rising = rs_line.iloc[-1] > rs_sma.iloc[-1]
        rs_trend_score = 0.65 if rs_rising else 0.35
        scores.append(rs_trend_score)
        signals["rs_trend_rising"] = bool(rs_rising)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Market Regime Detection
# ---------------------------------------------------------------------------

def _market_regime(data, params):
    """
    Detect market regime:
    - Bull: Price above rising 200-day SMA
    - Bear: Price below falling 200-day SMA
    - Range: Price near flat 200-day SMA or oscillating around it
    """
    close = data["Close"]
    signals = {}

    # Use the longest regime SMA the history can actually support (plus ~20 bars
    # of headroom for the slope read). Fall back to a shorter *fixed* window for
    # young names; record the period used instead of mislabeling a short SMA as
    # the 200-day. min_periods == period so the value is a true full-window mean.
    long_period = params["regime_sma_period"]  # 200
    if len(close) >= long_period + 20:
        sma_period = long_period
    elif len(close) >= 120:
        sma_period = 100
    else:
        return {"score": 0.5, "signals": {"regime": "unknown"}}

    sma = close.rolling(sma_period, min_periods=sma_period).mean()
    latest_price = close.iloc[-1]
    latest_sma = sma.iloc[-1]
    if pd.isna(latest_sma):
        return {"score": 0.5, "signals": {"regime": "unknown"}}

    # SMA slope over last 20 days
    if not pd.isna(sma.iloc[-20]):
        sma_slope = (sma.iloc[-1] - sma.iloc[-20]) / sma.iloc[-20]
    else:
        sma_slope = 0

    # Price vs SMA
    pct_from_sma = (latest_price - latest_sma) / latest_sma

    # Determine regime
    if pct_from_sma > 0.03 and sma_slope > 0.005:
        regime = "bull"
        regime_score = 0.75
    elif pct_from_sma > 0 and sma_slope > 0:
        regime = "mild_bull"
        regime_score = 0.65
    elif pct_from_sma < -0.03 and sma_slope < -0.005:
        regime = "bear"
        regime_score = 0.25
    elif pct_from_sma < 0 and sma_slope < 0:
        regime = "mild_bear"
        regime_score = 0.35
    else:
        regime = "range"
        regime_score = 0.50

    signals["regime"] = regime
    signals["regime_sma_period"] = sma_period
    signals["pct_from_sma"] = round(float(pct_from_sma * 100), 2)
    signals["sma_slope_20d"] = round(float(sma_slope * 100), 2)

    # Consecutive days above/below SMA
    if len(close) >= 10:
        above_count = sum(1 for i in range(-10, 0)
                          if not pd.isna(sma.iloc[i]) and close.iloc[i] > sma.iloc[i])
        signals["days_above_sma_last10"] = above_count
        if above_count >= 8 and regime in ("bull", "mild_bull"):
            regime_score = min(0.85, regime_score + 0.10)
        elif above_count <= 2 and regime in ("bear", "mild_bear"):
            regime_score = max(0.15, regime_score - 0.10)

    return {"score": round(float(np.clip(regime_score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Long-term Momentum
# ---------------------------------------------------------------------------

def _long_term_momentum(data):
    """
    Multi-timeframe momentum analysis:
    - 1-month, 3-month, 6-month, 12-month returns
    - Momentum consistency (are all timeframes positive?)
    """
    close = data["Close"]
    signals = {}
    scores = []

    periods = [(21, "1m"), (63, "3m"), (126, "6m"), (252, "12m")]

    for period, label in periods:
        if len(close) > period:
            ret = (close.iloc[-1] / close.iloc[-period]) - 1
            mom_score = _sigmoid(float(ret), scale=5)
            scores.append(mom_score)
            signals[f"return_{label}"] = round(float(ret * 100), 2)

    # Consistency bonus
    if len(scores) >= 3:
        all_positive = all(s > 0.5 for s in scores)
        all_negative = all(s < 0.5 for s in scores)
        signals["momentum_consistent"] = all_positive or all_negative
        if all_positive:
            scores.append(0.75)
        elif all_negative:
            scores.append(0.25)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Price Breadth / Health
# ---------------------------------------------------------------------------

def _price_breadth(data):
    """
    Price health indicators:
    - Percentage of last 20 days that were up
    - New highs count (rolling 20-day)
    """
    close = data["Close"]
    signals = {}
    scores = []

    if len(close) < 20:
        return {"score": 0.5, "signals": {}}

    daily_returns = close.pct_change().tail(20).dropna()
    if len(daily_returns) > 0:
        up_pct = (daily_returns > 0).sum() / len(daily_returns)
        breadth_score = 0.3 + up_pct * 0.4
        scores.append(breadth_score)
        signals["up_day_pct_20d"] = round(float(up_pct * 100), 1)

    if len(close) >= 40:
        rolling_high = close.rolling(20).max()
        recent_new_highs = sum(
            1 for i in range(-20, 0)
            if close.iloc[i] >= rolling_high.iloc[i] * 0.999
        )
        high_score = min(0.8, 0.3 + recent_new_highs * 0.05)
        scores.append(high_score)
        signals["new_20d_highs"] = recent_new_highs

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _sigmoid(x, scale=10):
    """Map value to 0-1 using sigmoid. x=0 -> 0.5."""
    return 1.0 / (1.0 + np.exp(-scale * x))
