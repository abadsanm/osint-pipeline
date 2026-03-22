"""Historical data loading for backtesting."""

import logging
import numpy as np
import pandas as pd
from datetime import datetime

log = logging.getLogger("backtesting.data_loader")

def load_price_data(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV via yfinance for each ticker."""
    import yfinance as yf
    result = {}
    for ticker in tickers:
        log.info("Fetching %s %s to %s", ticker, start, end)
        df = yf.download(ticker, start=start, end=end, progress=False)
        if df is not None and not df.empty:
            # Flatten MultiIndex columns
            if hasattr(df.columns, 'levels') and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            # Normalize column names to lowercase
            df.columns = [c.lower() for c in df.columns]
            result[ticker] = df
            log.info("  %s: %d bars loaded", ticker, len(df))
        else:
            log.warning("  %s: no data", ticker)
    return result

def simulate_sentiment_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Generate synthetic sentiment features from price/volume patterns.

    This bootstraps training before real OSINT history accumulates.
    Uses price momentum and volume patterns as proxies for sentiment.
    """
    df = prices.copy()

    # Synthetic sentiment: normalized momentum (5-day return)
    returns_5d = df['close'].pct_change(5)
    df['sentiment_score'] = np.tanh(returns_5d * 10)  # Scale and bound to -1..+1

    # Volume z-score as proxy for mention volume
    vol_mean = df['volume'].rolling(20).mean()
    vol_std = df['volume'].rolling(20).std()
    df['volume_zscore'] = ((df['volume'] - vol_mean) / vol_std.replace(0, 1)).clip(-3, 3)

    # Synthetic SVC: sentiment change * volume change
    sent_shift = df['sentiment_score'].diff(5)
    vol_change = df['volume'].pct_change(5).clip(-2, 2)
    df['svc_value'] = (sent_shift * vol_change).fillna(0)

    # Source count proxy: higher volume = more sources (1-5 range)
    vol_pctile = df['volume'].rank(pct=True)
    df['source_count'] = (vol_pctile * 4 + 1).round().astype(int)

    # Correlation confidence proxy
    df['correlation_confidence'] = (0.3 + vol_pctile * 0.5).clip(0, 1)

    return df.dropna()
