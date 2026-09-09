"""
Technical analysis tuned for macro buy-and-hold (weeks+ holding period).

Uses 2y daily data + 1y hourly data:
- Daily: Trend (50/200 SMA, Golden Cross, ADX), Support/Resistance (52-week)
- Hourly (blended): Momentum (RSI, MACD, ROC), Volume (OBV), Volatility (ATR, BB)
"""
import pandas as pd
import numpy as np
import pandas_ta as ta


def analyze(data, hourly_data=None, params=None):
    """
    Run full macro technical analysis using 2y daily + 1y hourly data.

    Args:
        data: 2y daily DataFrame with Open, High, Low, Close, Volume.
        hourly_data: 1y hourly DataFrame (optional, refines momentum/volume/volatility).
        params: dict of technical parameters (optional, uses defaults).

    Returns:
        dict with:
            score: 0.0-1.0 overall technical score
            confidence: 0.0-1.0 confidence level
            components: dict of sub-scores for each category
            indicators: dict of raw indicator values (latest)
    """
    if params is None:
        params = _default_params()

    if data is None or len(data) < params["sma_long"]:
        return _empty_result()

    # Daily-based analysis
    trend = _analyze_trend(data, params)
    momentum = _analyze_momentum(data, params)
    volume = _analyze_volume(data, params)
    volatility = _analyze_volatility(data, params)
    support_resistance = _analyze_support_resistance(data)

    # Blend hourly signals into momentum, volume, volatility
    if hourly_data is not None and len(hourly_data) >= 200:
        hourly = _analyze_hourly(hourly_data)
        if hourly is not None:
            momentum["score"] = round(float(np.clip(
                momentum["score"] * 0.6 + hourly["momentum"] * 0.4, 0, 1)), 4)
            volume["score"] = round(float(np.clip(
                volume["score"] * 0.6 + hourly["volume"] * 0.4, 0, 1)), 4)
            volatility["score"] = round(float(np.clip(
                volatility["score"] * 0.7 + hourly["volatility"] * 0.3, 0, 1)), 4)
            momentum.setdefault("signals", {})["hourly_blended"] = True

    # Weighted combination - trend is king for macro buy-and-hold
    weights = {
        "trend": 0.35,
        "momentum": 0.25,
        "volume": 0.15,
        "volatility": 0.10,
        "support_resistance": 0.15,
    }

    score = (
        trend["score"] * weights["trend"]
        + momentum["score"] * weights["momentum"]
        + volume["score"] * weights["volume"]
        + volatility["score"] * weights["volatility"]
        + support_resistance["score"] * weights["support_resistance"]
    )

    # Confidence: agreement between components
    sub_scores = [trend["score"], momentum["score"], volume["score"],
                  volatility["score"], support_resistance["score"]]
    variance = np.var(sub_scores)
    agreement = max(0.0, 1.0 - variance * 6)

    # Higher confidence when trend and momentum agree
    trend_momentum_agreement = 1.0 - abs(trend["score"] - momentum["score"])
    confidence = agreement * 0.6 + trend_momentum_agreement * 0.4

    # Hourly data availability boosts confidence slightly
    if hourly_data is not None and len(hourly_data) >= 200:
        confidence = min(1.0, confidence + 0.05)

    return {
        "score": round(float(np.clip(score, 0, 1)), 4),
        "confidence": round(float(np.clip(confidence, 0, 1)), 4),
        "components": {
            "trend": trend,
            "momentum": momentum,
            "volume": volume,
            "volatility": volatility,
            "support_resistance": support_resistance,
        },
        "indicators": _get_latest_indicators(data, params),
    }


def _default_params():
    return {
        "sma_short": 50,
        "sma_long": 200,
        "rsi_length": 14,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "bb_length": 20,
        "bb_std": 2.0,
        "adx_length": 14,
        "atr_length": 14,
        "roc_period": 20,
        "obv_signal_period": 20,
    }


def _empty_result():
    return {
        "score": 0.5,
        "confidence": 0.0,
        "components": {},
        "indicators": {},
    }


# ---------------------------------------------------------------------------
# Trend Analysis (35% weight)
# ---------------------------------------------------------------------------

def _analyze_trend(data, params):
    """
    Trend analysis for macro timeframe.
    Signals:
      - Price vs 50-day and 200-day SMA
      - Golden Cross (50 > 200) vs Death Cross
      - ADX trend strength
      - SMA slope direction
    """
    close = data["Close"]
    # Require a full window so an under-fed SMA is NaN (and skipped) rather than
    # a fake average of however many bars happen to exist. A "200-day SMA" built
    # from 30 bars is not a 200-day SMA.
    sma_short = close.rolling(params["sma_short"], min_periods=params["sma_short"]).mean()
    sma_long = close.rolling(params["sma_long"], min_periods=params["sma_long"]).mean()

    latest_price = close.iloc[-1]
    latest_sma_short = sma_short.iloc[-1]
    latest_sma_long = sma_long.iloc[-1]

    signals = {}
    scores = []

    # 1. Price vs 50-day SMA (above = bullish)
    above_sma50 = None
    if not pd.isna(latest_sma_short):
        above_sma50 = latest_price > latest_sma_short
        pct_from_sma50 = (latest_price - latest_sma_short) / latest_sma_short
        sma50_score = _sigmoid(pct_from_sma50, scale=20)  # 5% above = ~0.73
        scores.append(sma50_score)
        signals["above_sma50"] = above_sma50
        signals["pct_from_sma50"] = round(float(pct_from_sma50 * 100), 2)

    # 2. Price vs 200-day SMA (above = bullish)
    above_sma200 = None
    if not pd.isna(latest_sma_long):
        above_sma200 = latest_price > latest_sma_long
        pct_from_sma200 = (latest_price - latest_sma_long) / latest_sma_long
        sma200_score = _sigmoid(pct_from_sma200, scale=15)
        scores.append(sma200_score)
        signals["above_sma200"] = above_sma200
        signals["pct_from_sma200"] = round(float(pct_from_sma200 * 100), 2)

    # 3. Golden Cross (50 > 200 = bullish) — only meaningful when both SMAs exist
    if not pd.isna(latest_sma_short) and not pd.isna(latest_sma_long):
        golden_cross = latest_sma_short > latest_sma_long
        gc_score = 0.7 if golden_cross else 0.3
        scores.append(gc_score)
        signals["golden_cross"] = golden_cross

    # 4. SMA-200 slope (rising = bullish market) — needs a non-NaN value 20 bars back
    if len(sma_long) >= 20 and not pd.isna(sma_long.iloc[-1]) and not pd.isna(sma_long.iloc[-20]):
        sma_slope = (sma_long.iloc[-1] - sma_long.iloc[-20]) / sma_long.iloc[-20]
        slope_score = _sigmoid(sma_slope, scale=30)
        scores.append(slope_score)
        signals["sma200_slope"] = round(float(sma_slope * 100), 2)

    # 5. ADX trend strength
    try:
        adx_result = ta.adx(data["High"], data["Low"], close, length=params["adx_length"])
        if adx_result is not None:
            adx_col = f"ADX_{params['adx_length']}"
            if adx_col in adx_result.columns:
                adx_val = adx_result[adx_col].iloc[-1]
                if not pd.isna(adx_val):
                    # ADX > 25 = strong trend; combine with direction
                    is_trending = adx_val > 25
                    trend_dir = 1 if above_sma50 and above_sma200 else -1 if not above_sma50 and not above_sma200 else 0
                    if is_trending and trend_dir > 0:
                        adx_score = 0.8  # Strong uptrend
                    elif is_trending and trend_dir < 0:
                        adx_score = 0.2  # Strong downtrend
                    elif is_trending:
                        adx_score = 0.5  # Trending but mixed direction
                    else:
                        adx_score = 0.5  # Not trending
                    scores.append(adx_score)
                    signals["adx"] = round(float(adx_val), 2)
                    signals["is_trending"] = bool(is_trending)
    except Exception:
        pass

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Momentum Analysis (25% weight)
# ---------------------------------------------------------------------------

def _analyze_momentum(data, params):
    """
    Momentum analysis:
      - RSI-14 (oversold = buy opportunity for macro)
      - MACD 12/26/9 signal line crossover
      - Rate of Change (ROC) 20-day
    """
    close = data["Close"]
    signals = {}
    scores = []

    # 1. RSI
    rsi = ta.rsi(close, length=params["rsi_length"])
    if rsi is not None and len(rsi) > 0:
        rsi_val = rsi.iloc[-1]
        if not pd.isna(rsi_val):
            # For buy-and-hold: oversold (< 30) = great buying opportunity
            # Overbought (> 70) doesn't mean sell, just less upside
            if rsi_val < 30:
                rsi_score = 0.85  # Oversold - strong buy signal for macro
            elif rsi_val < 40:
                rsi_score = 0.70
            elif rsi_val < 50:
                rsi_score = 0.55
            elif rsi_val < 60:
                rsi_score = 0.50
            elif rsi_val < 70:
                rsi_score = 0.45  # Moderate, fine for holding
            else:
                rsi_score = 0.30  # Overbought, less upside expected
            scores.append(rsi_score)
            signals["rsi"] = round(float(rsi_val), 2)

    # 2. MACD
    macd_result = ta.macd(close, fast=params["macd_fast"],
                          slow=params["macd_slow"], signal=params["macd_signal"])
    if macd_result is not None:
        macd_col = f"MACD_{params['macd_fast']}_{params['macd_slow']}_{params['macd_signal']}"
        signal_col = f"MACDs_{params['macd_fast']}_{params['macd_slow']}_{params['macd_signal']}"
        hist_col = f"MACDh_{params['macd_fast']}_{params['macd_slow']}_{params['macd_signal']}"

        if macd_col in macd_result.columns and signal_col in macd_result.columns:
            macd_val = macd_result[macd_col].iloc[-1]
            signal_val = macd_result[signal_col].iloc[-1]
            hist_val = macd_result[hist_col].iloc[-1] if hist_col in macd_result.columns else 0

            if not pd.isna(macd_val) and not pd.isna(signal_val):
                # MACD above signal = bullish momentum
                macd_bullish = macd_val > signal_val
                # MACD above zero = overall bullish
                macd_positive = macd_val > 0
                # Histogram expanding = strengthening momentum
                hist_expanding = False
                if hist_col in macd_result.columns and len(macd_result) > 1:
                    prev_hist = macd_result[hist_col].iloc[-2]
                    if not pd.isna(prev_hist) and not pd.isna(hist_val):
                        hist_expanding = abs(hist_val) > abs(prev_hist) and hist_val > 0

                if macd_bullish and macd_positive:
                    macd_score = 0.80
                elif macd_bullish and not macd_positive:
                    macd_score = 0.60  # Recovering
                elif not macd_bullish and macd_positive:
                    macd_score = 0.45  # Losing momentum but still positive
                else:
                    macd_score = 0.25  # Bearish

                if hist_expanding:
                    macd_score = min(0.95, macd_score + 0.10)

                scores.append(macd_score)
                signals["macd_bullish"] = bool(macd_bullish)
                signals["macd_positive"] = bool(macd_positive)
                signals["macd_hist_expanding"] = bool(hist_expanding)

    # 3. Rate of Change (ROC)
    if len(close) > params["roc_period"]:
        roc = (close.iloc[-1] - close.iloc[-params["roc_period"]]) / close.iloc[-params["roc_period"]]
        roc_score = _sigmoid(float(roc), scale=8)  # 10% gain in 20 days = ~0.69
        scores.append(roc_score)
        signals["roc_20d"] = round(float(roc * 100), 2)

    # 4. RSI slope (momentum acceleration)
    if rsi is not None and len(rsi) >= 5:
        rsi_now = rsi.iloc[-1]
        rsi_5ago = rsi.iloc[-5]
        if not pd.isna(rsi_now) and not pd.isna(rsi_5ago):
            rsi_delta = float(rsi_now - rsi_5ago)
            # Rising RSI = accelerating momentum (bullish)
            # Falling RSI = decelerating (bearish)
            accel_score = _sigmoid(rsi_delta / 10, scale=3)  # 10-point rise -> ~0.73
            scores.append(accel_score)
            signals["rsi_slope_5d"] = round(rsi_delta, 2)

    # 5. Multi-timeframe momentum agreement
    mom_signals = []
    for period in [5, 10, 20]:
        if len(close) > period:
            ret = float((close.iloc[-1] / close.iloc[-period]) - 1)
            mom_signals.append(ret > 0)
    if len(mom_signals) >= 3:
        agreement = sum(mom_signals) / len(mom_signals)
        if agreement == 1.0:
            scores.append(0.75)  # All timeframes positive
            signals["momentum_aligned"] = "bullish"
        elif agreement == 0.0:
            scores.append(0.25)  # All timeframes negative
            signals["momentum_aligned"] = "bearish"
        else:
            scores.append(0.50)
            signals["momentum_aligned"] = "mixed"

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Volume Analysis (15% weight)
# ---------------------------------------------------------------------------

def _analyze_volume(data, params):
    """
    Volume analysis:
      - OBV trend (rising OBV = accumulation)
      - Volume vs 20-day average (above average on up days = bullish)
    """
    close = data["Close"]
    volume = data["Volume"]
    signals = {}
    scores = []

    # 1. OBV trend
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    obv_sma = obv.rolling(params["obv_signal_period"], min_periods=1).mean()

    if len(obv) > params["obv_signal_period"]:
        obv_above_sma = obv.iloc[-1] > obv_sma.iloc[-1]
        # OBV slope over last 20 days
        obv_slope = (obv.iloc[-1] - obv.iloc[-20]) if len(obv) > 20 else 0
        obv_score = 0.65 if obv_above_sma else 0.35
        if obv_slope > 0 and obv_above_sma:
            obv_score = 0.75
        elif obv_slope < 0 and not obv_above_sma:
            obv_score = 0.25
        scores.append(obv_score)
        signals["obv_rising"] = bool(obv_above_sma)

    # 2. Volume on up-days vs down-days (last 20 days)
    if len(data) >= 20:
        recent = data.tail(20)
        up_days = recent[recent["Close"] > recent["Close"].shift(1)]
        down_days = recent[recent["Close"] < recent["Close"].shift(1)]

        avg_up_vol = up_days["Volume"].mean() if len(up_days) > 0 else 0
        avg_down_vol = down_days["Volume"].mean() if len(down_days) > 0 else 0

        if avg_up_vol + avg_down_vol > 0:
            vol_ratio = avg_up_vol / (avg_up_vol + avg_down_vol)
            vol_score = 0.3 + vol_ratio * 0.4  # Maps 0.5 ratio to 0.5 score
            scores.append(vol_score)
            signals["up_day_volume_ratio"] = round(float(vol_ratio), 3)

    # 3. Current volume vs average
    vol_ma = volume.rolling(20, min_periods=1).mean()
    if vol_ma.iloc[-1] > 0:
        vol_vs_avg = volume.iloc[-1] / vol_ma.iloc[-1]
        signals["volume_vs_avg"] = round(float(vol_vs_avg), 2)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Volatility Analysis (10% weight)
# ---------------------------------------------------------------------------

def _analyze_volatility(data, params):
    """
    Volatility analysis:
      - ATR relative to price (high ATR = more risk)
      - Bollinger Band position (near lower = potential buy)
      - Historical volatility trend
    """
    close = data["Close"]
    signals = {}
    scores = []

    # 1. ATR as percentage of price
    atr = ta.atr(data["High"], data["Low"], close, length=params["atr_length"])
    if atr is not None and len(atr) > 0:
        atr_val = atr.iloc[-1]
        if not pd.isna(atr_val) and close.iloc[-1] > 0:
            atr_pct = atr_val / close.iloc[-1]
            # Lower ATR% = lower risk = better for buy-and-hold entry
            if atr_pct < 0.015:
                atr_score = 0.75  # Low volatility
            elif atr_pct < 0.025:
                atr_score = 0.60
            elif atr_pct < 0.04:
                atr_score = 0.45
            else:
                atr_score = 0.30  # High volatility
            scores.append(atr_score)
            signals["atr_pct"] = round(float(atr_pct * 100), 2)

    # 2. Bollinger Band position
    bb = ta.bbands(close, length=params["bb_length"], std=params["bb_std"])
    if bb is not None:
        bb_upper_col = f"BBU_{params['bb_length']}_{params['bb_std']}"
        bb_lower_col = f"BBL_{params['bb_length']}_{params['bb_std']}"

        if bb_upper_col in bb.columns and bb_lower_col in bb.columns:
            bb_upper = bb[bb_upper_col].iloc[-1]
            bb_lower = bb[bb_lower_col].iloc[-1]

            if not pd.isna(bb_upper) and not pd.isna(bb_lower) and bb_upper > bb_lower:
                bb_range = bb_upper - bb_lower
                bb_position = (close.iloc[-1] - bb_lower) / bb_range
                # For macro buy-and-hold:
                # Near lower band = potential buying opportunity (mean reversion)
                # Near upper band = less immediate upside but not a sell signal
                if bb_position < 0.2:
                    bb_score = 0.80  # Near lower band - value buy
                elif bb_position < 0.4:
                    bb_score = 0.60
                elif bb_position < 0.6:
                    bb_score = 0.50  # Middle
                elif bb_position < 0.8:
                    bb_score = 0.45
                else:
                    bb_score = 0.35  # Near upper band
                scores.append(bb_score)
                signals["bb_position"] = round(float(bb_position), 3)

    # 3. 20-day historical volatility
    if len(close) > 20:
        returns = close.pct_change().dropna()
        hist_vol = returns.tail(20).std() * np.sqrt(252)  # Annualized
        # Lower vol = safer entry
        vol_score = _sigmoid(-float(hist_vol) + 0.3, scale=5)  # 30% vol = neutral
        scores.append(vol_score)
        signals["annual_volatility"] = round(float(hist_vol * 100), 2)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Support / Resistance Analysis (15% weight)
# ---------------------------------------------------------------------------

def _analyze_support_resistance(data):
    """
    Support/resistance analysis:
      - 52-week high/low proximity
      - Distance from recent consolidation levels
    """
    close = data["Close"]
    signals = {}
    scores = []

    # 1. 52-week high/low position (252 trading days)
    lookback = min(252, len(close))
    high_52w = close.tail(lookback).max()
    low_52w = close.tail(lookback).min()
    current = close.iloc[-1]

    if high_52w > low_52w:
        position_52w = (current - low_52w) / (high_52w - low_52w)
        # For macro buy-and-hold:
        # Near 52w high = strong stock, momentum (moderate bullish)
        # Near 52w low = potential value buy or falling knife
        # Middle = neutral
        if position_52w > 0.9:
            pos_score = 0.60  # Near highs - momentum but less upside
        elif position_52w > 0.7:
            pos_score = 0.65  # Strong position
        elif position_52w > 0.5:
            pos_score = 0.55
        elif position_52w > 0.3:
            pos_score = 0.45  # Below midpoint
        elif position_52w > 0.1:
            pos_score = 0.35  # Near lows
        else:
            pos_score = 0.25  # At 52w low - high risk

        scores.append(pos_score)
        signals["position_52w"] = round(float(position_52w * 100), 2)
        signals["pct_from_52w_high"] = round(float((current / high_52w - 1) * 100), 2)

    # 2. Recent price stability (20-day range as % of price)
    if len(close) >= 20:
        recent_high = close.tail(20).max()
        recent_low = close.tail(20).min()
        recent_range_pct = (recent_high - recent_low) / current
        # Tighter range = consolidation (potential breakout)
        if recent_range_pct < 0.05:
            range_score = 0.60  # Tight consolidation
        elif recent_range_pct < 0.10:
            range_score = 0.50
        else:
            range_score = 0.40  # Wide range, choppy
        scores.append(range_score)
        signals["recent_range_pct"] = round(float(recent_range_pct * 100), 2)

    score = np.mean(scores) if scores else 0.5
    return {"score": round(float(np.clip(score, 0, 1)), 4), "signals": signals}


# ---------------------------------------------------------------------------
# Hourly Data Analysis
# ---------------------------------------------------------------------------

# Scaling: ~7 hourly bars per trading day
_HOURS_PER_DAY = 7

def _analyze_hourly(hourly_data):
    """
    Extract momentum, volume, and volatility signals from 1y hourly data.

    Hourly indicator periods are scaled from daily equivalents:
      daily RSI-14 ≈ hourly RSI-98, daily MACD 12/26/9 ≈ hourly 84/182/63, etc.

    Returns dict with momentum, volume, volatility scores (0-1) or None.
    """
    try:
        close = hourly_data["Close"]
        volume = hourly_data["Volume"]
        high = hourly_data["High"]
        low = hourly_data["Low"]
    except (KeyError, TypeError):
        return None

    result = {}

    # --- Momentum (hourly) ---
    mom_scores = []

    # Short-term RSI (14 hourly bars ≈ 2 trading days)
    rsi_st = ta.rsi(close, length=14)
    if rsi_st is not None and len(rsi_st) > 0 and not pd.isna(rsi_st.iloc[-1]):
        rsi_val = float(rsi_st.iloc[-1])
        if rsi_val < 30:
            mom_scores.append(0.85)
        elif rsi_val < 40:
            mom_scores.append(0.70)
        elif rsi_val < 50:
            mom_scores.append(0.55)
        elif rsi_val < 60:
            mom_scores.append(0.50)
        elif rsi_val < 70:
            mom_scores.append(0.45)
        else:
            mom_scores.append(0.30)

    # Medium-term RSI (98 hourly bars ≈ daily RSI-14)
    rsi_mt = ta.rsi(close, length=14 * _HOURS_PER_DAY)
    if rsi_mt is not None and len(rsi_mt) > 0 and not pd.isna(rsi_mt.iloc[-1]):
        rsi_val = float(rsi_mt.iloc[-1])
        if rsi_val < 30:
            mom_scores.append(0.85)
        elif rsi_val < 40:
            mom_scores.append(0.70)
        elif rsi_val < 50:
            mom_scores.append(0.55)
        elif rsi_val < 60:
            mom_scores.append(0.50)
        elif rsi_val < 70:
            mom_scores.append(0.45)
        else:
            mom_scores.append(0.30)

    # Hourly MACD (scaled from daily 12/26/9)
    h_fast = 12 * _HOURS_PER_DAY
    h_slow = 26 * _HOURS_PER_DAY
    h_signal = 9 * _HOURS_PER_DAY
    macd_r = ta.macd(close, fast=h_fast, slow=h_slow, signal=h_signal)
    if macd_r is not None:
        macd_col = f"MACD_{h_fast}_{h_slow}_{h_signal}"
        signal_col = f"MACDs_{h_fast}_{h_slow}_{h_signal}"
        if macd_col in macd_r.columns and signal_col in macd_r.columns:
            macd_val = macd_r[macd_col].iloc[-1]
            signal_val = macd_r[signal_col].iloc[-1]
            if not pd.isna(macd_val) and not pd.isna(signal_val):
                bullish = macd_val > signal_val
                positive = macd_val > 0
                if bullish and positive:
                    mom_scores.append(0.80)
                elif bullish:
                    mom_scores.append(0.60)
                elif positive:
                    mom_scores.append(0.45)
                else:
                    mom_scores.append(0.25)

    # 5-day ROC on hourly (5 * 7 = 35 bars)
    roc_bars = 5 * _HOURS_PER_DAY
    if len(close) > roc_bars:
        roc = float((close.iloc[-1] - close.iloc[-roc_bars]) / close.iloc[-roc_bars])
        mom_scores.append(_sigmoid(roc, scale=8))

    result["momentum"] = float(np.mean(mom_scores)) if mom_scores else 0.5

    # --- Volume (hourly) ---
    vol_scores = []

    # OBV trend (130 hourly bars ≈ 20 trading days)
    obv_period = 20 * _HOURS_PER_DAY
    obv = (np.sign(close.diff()) * volume).fillna(0).cumsum()
    if len(obv) > obv_period:
        obv_sma = obv.rolling(obv_period, min_periods=1).mean()
        obv_above = obv.iloc[-1] > obv_sma.iloc[-1]
        obv_slope = obv.iloc[-1] - obv.iloc[-obv_period]
        if obv_above and obv_slope > 0:
            vol_scores.append(0.75)
        elif obv_above:
            vol_scores.append(0.65)
        elif obv_slope < 0:
            vol_scores.append(0.25)
        else:
            vol_scores.append(0.35)

    # Up-hour vs down-hour volume (last 20 trading days)
    if len(hourly_data) >= obv_period:
        recent = hourly_data.tail(obv_period)
        up_bars = recent[recent["Close"] > recent["Close"].shift(1)]
        down_bars = recent[recent["Close"] < recent["Close"].shift(1)]
        avg_up = up_bars["Volume"].mean() if len(up_bars) > 0 else 0
        avg_down = down_bars["Volume"].mean() if len(down_bars) > 0 else 0
        if avg_up + avg_down > 0:
            ratio = avg_up / (avg_up + avg_down)
            vol_scores.append(0.3 + ratio * 0.4)

    result["volume"] = float(np.mean(vol_scores)) if vol_scores else 0.5

    # --- Volatility (hourly) ---
    vola_scores = []

    # ATR on hourly (98 bars ≈ 14 trading days)
    atr_period = 14 * _HOURS_PER_DAY
    atr = ta.atr(high, low, close, length=atr_period)
    if atr is not None and len(atr) > 0 and not pd.isna(atr.iloc[-1]) and close.iloc[-1] > 0:
        atr_pct = float(atr.iloc[-1] / close.iloc[-1])
        # Hourly ATR% is ~1/sqrt(7) of daily ATR%
        if atr_pct < 0.003:
            vola_scores.append(0.75)
        elif atr_pct < 0.005:
            vola_scores.append(0.60)
        elif atr_pct < 0.008:
            vola_scores.append(0.45)
        else:
            vola_scores.append(0.30)

    # Bollinger Band position (140 bars ≈ 20 trading days)
    bb_period = 20 * _HOURS_PER_DAY
    bb = ta.bbands(close, length=bb_period, std=2.0)
    if bb is not None:
        bb_upper_col = f"BBU_{bb_period}_2.0"
        bb_lower_col = f"BBL_{bb_period}_2.0"
        if bb_upper_col in bb.columns and bb_lower_col in bb.columns:
            bb_upper = bb[bb_upper_col].iloc[-1]
            bb_lower = bb[bb_lower_col].iloc[-1]
            if not pd.isna(bb_upper) and not pd.isna(bb_lower) and bb_upper > bb_lower:
                bb_pos = float((close.iloc[-1] - bb_lower) / (bb_upper - bb_lower))
                if bb_pos < 0.2:
                    vola_scores.append(0.80)
                elif bb_pos < 0.4:
                    vola_scores.append(0.60)
                elif bb_pos < 0.6:
                    vola_scores.append(0.50)
                elif bb_pos < 0.8:
                    vola_scores.append(0.45)
                else:
                    vola_scores.append(0.35)

    # Intraday volatility (annualized from hourly returns, last 20 days)
    if len(close) > obv_period:
        hourly_returns = close.pct_change().dropna().tail(obv_period)
        # Annualize: hourly vol × sqrt(hours_per_year)
        hist_vol = float(hourly_returns.std() * np.sqrt(252 * _HOURS_PER_DAY))
        vol_score = _sigmoid(-hist_vol + 0.3, scale=5)
        vola_scores.append(vol_score)

    result["volatility"] = float(np.mean(vola_scores)) if vola_scores else 0.5

    return result


# ---------------------------------------------------------------------------
# Latest indicator values for reporting
# ---------------------------------------------------------------------------

def _get_latest_indicators(data, params):
    """Extract latest indicator values for the report."""
    close = data["Close"]
    indicators = {"price": round(float(close.iloc[-1]), 2)}
    # Full-window SMAs; omit (rather than fabricate) when history is too short.
    sma50 = close.rolling(params["sma_short"], min_periods=params["sma_short"]).mean().iloc[-1]
    sma200 = close.rolling(params["sma_long"], min_periods=params["sma_long"]).mean().iloc[-1]
    if not pd.isna(sma50):
        indicators["sma_50"] = round(float(sma50), 2)
    if not pd.isna(sma200):
        indicators["sma_200"] = round(float(sma200), 2)

    # Price changes
    if len(close) >= 2:
        indicators["change_1d"] = round(float((close.iloc[-1] / close.iloc[-2] - 1) * 100), 2)
    if len(close) >= 6:
        indicators["change_5d"] = round(float((close.iloc[-1] / close.iloc[-6] - 1) * 100), 2)
    if len(close) >= 21:
        indicators["change_1m"] = round(float((close.iloc[-1] / close.iloc[-21] - 1) * 100), 2)
    if len(close) >= 63:
        indicators["change_3m"] = round(float((close.iloc[-1] / close.iloc[-63] - 1) * 100), 2)

    lookback = min(len(close), 252)
    if lookback >= 20:
        high_52w = float(close.tail(lookback).max())
        if high_52w > 0:
            indicators["pct_from_52w_high"] = round(
                float((close.iloc[-1] / high_52w - 1) * 100), 2
            )

    # RSI
    rsi = ta.rsi(close, length=params["rsi_length"])
    if rsi is not None and len(rsi) > 0 and not pd.isna(rsi.iloc[-1]):
        indicators["rsi"] = round(float(rsi.iloc[-1]), 2)

    return indicators


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _sigmoid(x, scale=10):
    """Map value to 0-1 range using sigmoid. x=0 -> 0.5."""
    return 1.0 / (1.0 + np.exp(-scale * x))
