"""
Market Regime Detection

Classifies the current market regime for a ticker using:
1. SMA slope (trend direction)
2. ATR volatility percentile (breakout detection)
3. Hurst exponent via R/S method (mean-reversion vs trending)

Regime labels: trending_up, trending_down, range_bound, high_vol_breakout
"""

from __future__ import annotations

import logging
import math
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

log = logging.getLogger("stock_alpha.regime")


class MarketRegime(BaseModel):
    regime: Literal["trending_up", "trending_down", "range_bound", "high_vol_breakout"]
    confidence: float = Field(ge=0.0, le=1.0)
    indicators: dict = Field(default_factory=dict)


def _compute_sma_slope_pct(close: pd.Series, lookback_days: int) -> float:
    """Compute normalised SMA slope as a percentage of current price."""
    sma = close.rolling(window=lookback_days).mean()
    sma_tail = sma.dropna().iloc[-lookback_days:]

    if len(sma_tail) < lookback_days:
        sma_tail = sma.dropna()

    if len(sma_tail) < 2:
        return 0.0

    x = np.arange(len(sma_tail), dtype=float)
    y = sma_tail.values.astype(float)
    slope = np.polyfit(x, y, 1)[0]
    current_price = float(close.iloc[-1])

    if current_price == 0:
        return 0.0

    return slope / current_price * 100


def _compute_atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ATR series from OHLCV DataFrame."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(window=period).mean()


def _atr_percentile(atr_series: pd.Series, lookback_year: int = 252) -> tuple[float, float]:
    """Return (current_atr, percentile_rank) for the latest ATR value."""
    atr_clean = atr_series.dropna()
    if len(atr_clean) < 2:
        return 0.0, 50.0

    current_atr = float(atr_clean.iloc[-1])
    history = atr_clean.iloc[-lookback_year:]

    try:
        from scipy.stats import percentileofscore

        pct = percentileofscore(history.values, current_atr, kind="rank")
    except ImportError:
        arr = history.values
        pct = float(np.sum(arr <= current_atr)) / len(arr) * 100

    return current_atr, float(pct)


def _hurst_exponent(close: pd.Series) -> Optional[float]:
    """Estimate Hurst exponent via Rescaled Range (R/S) analysis.

    Returns None when insufficient data or computation fails.
    """
    log_returns = np.log(close / close.shift(1)).dropna().values.astype(float)

    if len(log_returns) < 130:
        # Not enough data even for the smallest window sizes
        return None

    window_sizes = [n for n in [8, 16, 32, 64, 128] if n < len(log_returns)]
    if len(window_sizes) < 2:
        return None

    rs_means: list[float] = []
    valid_sizes: list[int] = []

    for n in window_sizes:
        num_chunks = len(log_returns) // n
        if num_chunks == 0:
            continue

        rs_values: list[float] = []
        for i in range(num_chunks):
            chunk = log_returns[i * n : (i + 1) * n]
            std = float(np.std(chunk, ddof=1))

            if std == 0 or math.isnan(std):
                continue

            mean_adj = chunk - np.mean(chunk)
            cumsum = np.cumsum(mean_adj)
            r = float(np.max(cumsum) - np.min(cumsum))
            rs_values.append(r / std)

        if rs_values:
            rs_means.append(float(np.mean(rs_values)))
            valid_sizes.append(n)

    if len(valid_sizes) < 2:
        return None

    try:
        log_n = np.log(np.array(valid_sizes, dtype=float))
        log_rs = np.log(np.array(rs_means, dtype=float))

        if np.any(np.isnan(log_rs)) or np.any(np.isinf(log_rs)):
            return None

        hurst = float(np.polyfit(log_n, log_rs, 1)[0])

        # Clamp to reasonable range
        return max(0.0, min(1.0, hurst))
    except Exception:
        return None


def classify_regime(
    df: Optional[pd.DataFrame] = None,
    ticker: Optional[str] = None,
    lookback_days: int = 20,
) -> MarketRegime:
    """Classify market regime from OHLCV data.

    Provide either df (DataFrame with open/high/low/close/volume columns)
    or ticker (fetches via yfinance).
    """
    if df is None and ticker:
        from stock_alpha.technicals import PriceDataProvider

        provider = PriceDataProvider()
        df = provider.get_prices(ticker)

    if df is None or len(df) < lookback_days + 10:
        return MarketRegime(
            regime="range_bound",
            confidence=0.3,
            indicators={"error": "insufficient_data"},
        )

    try:
        slope_pct = _compute_sma_slope_pct(df["close"], lookback_days)
    except Exception:
        log.warning("SMA slope computation failed", exc_info=True)
        slope_pct = 0.0

    try:
        atr_series = _compute_atr_series(df)
        current_atr, atr_percentile = _atr_percentile(atr_series)
    except Exception:
        log.warning("ATR computation failed", exc_info=True)
        current_atr, atr_percentile = 0.0, 50.0

    try:
        hurst = _hurst_exponent(df["close"])
    except Exception:
        log.warning("Hurst exponent computation failed", exc_info=True)
        hurst = None

    # --- Classification ---
    # Use 0.5 (random walk) as fallback when Hurst is unavailable
    hurst_val = hurst if hurst is not None else 0.5

    if atr_percentile > 80:
        regime: Literal["trending_up", "trending_down", "range_bound", "high_vol_breakout"] = (
            "high_vol_breakout"
        )
        confidence = min(1.0, atr_percentile / 100)
    elif slope_pct > 0.15 and hurst_val > 0.5:
        regime = "trending_up"
        confidence = min(1.0, 0.5 + abs(slope_pct) * 2 + (hurst_val - 0.5))
    elif slope_pct < -0.15 and hurst_val > 0.5:
        regime = "trending_down"
        confidence = min(1.0, 0.5 + abs(slope_pct) * 2 + (hurst_val - 0.5))
    else:
        regime = "range_bound"
        confidence = min(1.0, 0.5 + (0.5 - hurst_val) if hurst_val < 0.5 else 0.4)

    indicators = {
        "sma_slope_pct": round(slope_pct, 4),
        "atr_percentile": round(atr_percentile, 1),
        "hurst_exponent": round(hurst, 3) if hurst is not None else None,
        "current_atr": round(current_atr, 4),
        "current_price": round(float(df["close"].iloc[-1]), 2),
    }

    log.info(
        "Regime for %s: %s (confidence=%.2f, slope=%.4f, atr_pct=%.1f, hurst=%s)",
        ticker or "provided_df",
        regime,
        confidence,
        slope_pct,
        atr_percentile,
        f"{hurst:.3f}" if hurst is not None else "N/A",
    )

    return MarketRegime(regime=regime, confidence=confidence, indicators=indicators)
