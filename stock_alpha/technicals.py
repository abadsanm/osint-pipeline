"""
Technical indicator computation and price data fetching.

Uses yfinance for OHLCV data and pandas-ta for technical indicators.
Provides a unified feature DataFrame for each ticker.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from stock_alpha.config import StockAlphaConfig

log = logging.getLogger("stock_alpha.technicals")


class PriceDataProvider:
    """Fetches and caches OHLCV price data from Yahoo Finance."""

    def __init__(self):
        # Cache: {ticker: (last_fetch_time, DataFrame)}
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}

    def get_prices(self, ticker: str) -> Optional[pd.DataFrame]:
        """Get OHLCV data for a ticker, using cache if fresh."""
        import time

        cached = self._cache.get(ticker)
        if cached:
            last_fetch, df = cached
            age_minutes = (time.time() - last_fetch) / 60
            if age_minutes < StockAlphaConfig.PRICE_REFRESH_MINUTES:
                return df

        df = self._fetch_prices(ticker)
        if df is not None and not df.empty:
            self._cache[ticker] = (time.time(), df)
        return df

    @staticmethod
    def _fetch_prices(ticker: str) -> Optional[pd.DataFrame]:
        """Fetch OHLCV data from Yahoo Finance."""
        try:
            import yfinance as yf

            t = yf.Ticker(ticker)
            df = t.history(period=StockAlphaConfig.PRICE_LOOKBACK)

            if df is None or df.empty:
                log.warning("No price data for %s", ticker)
                return None

            # Standardize column names
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]
            return df

        except Exception as e:
            log.warning("Failed to fetch prices for %s: %s", ticker, e)
            return None


def compute_technicals(df: pd.DataFrame) -> pd.DataFrame:
    """Add technical indicator columns to an OHLCV DataFrame.

    Expects columns: open, high, low, close, volume.
    Returns the DataFrame with additional indicator columns.
    """
    import pandas_ta as ta

    if df is None or df.empty or "close" not in df.columns:
        return df

    df = df.copy()

    # RSI
    df["rsi"] = ta.rsi(df["close"], length=StockAlphaConfig.RSI_PERIOD)

    # MACD
    macd = ta.macd(
        df["close"],
        fast=StockAlphaConfig.MACD_FAST,
        slow=StockAlphaConfig.MACD_SLOW,
        signal=StockAlphaConfig.MACD_SIGNAL,
    )
    if macd is not None:
        macd_col = f"MACD_{StockAlphaConfig.MACD_FAST}_{StockAlphaConfig.MACD_SLOW}_{StockAlphaConfig.MACD_SIGNAL}"
        macd_signal_col = f"MACDs_{StockAlphaConfig.MACD_FAST}_{StockAlphaConfig.MACD_SLOW}_{StockAlphaConfig.MACD_SIGNAL}"
        macd_hist_col = f"MACDh_{StockAlphaConfig.MACD_FAST}_{StockAlphaConfig.MACD_SLOW}_{StockAlphaConfig.MACD_SIGNAL}"
        df["macd"] = macd.get(macd_col)
        df["macd_signal"] = macd.get(macd_signal_col)
        df["macd_hist"] = macd.get(macd_hist_col)

    # Bollinger Bands
    bb = ta.bbands(
        df["close"],
        length=StockAlphaConfig.BB_PERIOD,
        std=StockAlphaConfig.BB_STD,
    )
    if bb is not None:
        # pandas-ta bbands column names vary by version — match by prefix
        bb_cols = {c[:3]: c for c in bb.columns}  # e.g., {"BBL": "BBL_20_2.0_2.0", ...}
        df["bb_lower"] = bb.get(bb_cols.get("BBL"))
        df["bb_mid"] = bb.get(bb_cols.get("BBM"))
        df["bb_upper"] = bb.get(bb_cols.get("BBU"))
        # BB %B: where price is within the bands (0=lower, 1=upper)
        if "bb_upper" in df.columns and "bb_lower" in df.columns:
            bb_width = df["bb_upper"] - df["bb_lower"]
            df["bb_pctb"] = (df["close"] - df["bb_lower"]) / bb_width.replace(0, float("nan"))

    # Moving averages
    for period in StockAlphaConfig.SMA_PERIODS:
        df[f"sma_{period}"] = ta.sma(df["close"], length=period)
    for period in StockAlphaConfig.EMA_PERIODS:
        df[f"ema_{period}"] = ta.ema(df["close"], length=period)

    # ATR (Average True Range) — volatility
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # OBV (On-Balance Volume)
    df["obv"] = ta.obv(df["close"], df["volume"])

    # Volume moving average
    df["volume_sma"] = ta.sma(df["volume"], length=20)

    # Daily returns (log returns for stationarity)
    import numpy as np
    df["log_return"] = np.log(df["close"] / df["close"].shift(1))

    return df
