"""Historical data loading for backtesting.

Supports individual tickers, comma-separated lists, and universe
presets (sp500, nasdaq100, mega_cap).
"""

import logging
import os
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("backtesting.data_loader")

# Cache directory for downloaded price data
_CACHE_DIR = Path("data/backtest_cache")


def get_sp500_tickers() -> list[str]:
    """Fetch current S&P 500 constituent tickers.

    Tries multiple sources: Wikipedia HTML scrape, then datahub CSV,
    then hardcoded fallback (top 100 by market cap).
    """
    # Method 1: GitHub datasets CSV (most reliable, no auth needed)
    try:
        import urllib.request
        import csv
        import io
        req = urllib.request.Request(
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
            headers={"User-Agent": "Mozilla/5.0 (OSINT Pipeline Backtester)"},
        )
        data = urllib.request.urlopen(req, timeout=15).read().decode()
        reader = csv.DictReader(io.StringIO(data))
        tickers = sorted([row["Symbol"].replace(".", "-") for row in reader])
        if len(tickers) > 400:
            log.info("Fetched %d S&P 500 tickers from GitHub datasets", len(tickers))
            return tickers
    except Exception as e:
        log.debug("GitHub S&P 500 fetch failed: %s", e)

    # Method 2: Wikipedia with proper headers
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            headers={"User-Agent": "Mozilla/5.0 (OSINT Pipeline Backtester)"},
        )
        html = urllib.request.urlopen(req, timeout=15).read().decode()
        table = pd.read_html(html, header=0)[0]
        tickers = sorted(table["Symbol"].str.replace(".", "-", regex=False).tolist())
        if len(tickers) > 400:
            log.info("Fetched %d S&P 500 tickers from Wikipedia", len(tickers))
            return tickers
    except Exception as e:
        log.debug("Wikipedia S&P 500 fetch failed: %s", e)

    log.warning("Using hardcoded S&P 500 fallback (%d tickers)", len(_SP500_FALLBACK))
    return _SP500_FALLBACK


def get_nasdaq100_tickers() -> list[str]:
    """Fetch NASDAQ-100 tickers."""
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://en.wikipedia.org/wiki/Nasdaq-100",
            headers={"User-Agent": "Mozilla/5.0 (OSINT Pipeline Backtester)"},
        )
        html = urllib.request.urlopen(req, timeout=15).read().decode()
        table = pd.read_html(html, header=0)[0]
        col = "Ticker" if "Ticker" in table.columns else table.columns[1]
        tickers = sorted(table[col].str.replace(".", "-", regex=False).tolist())
        log.info("Fetched %d NASDAQ-100 tickers from Wikipedia", len(tickers))
        return tickers
    except Exception as e:
        log.warning("Failed to fetch NASDAQ-100 list: %s", e)
        return _NASDAQ100_FALLBACK


def resolve_universe(universe: str) -> list[str]:
    """Resolve a universe name to a ticker list.

    Supported universes:
        sp500      — S&P 500 (~503 tickers)
        nasdaq100  — NASDAQ-100 (~100 tickers)
        mega_cap   — Top 50 by market cap
        large_cap  — Top 200 S&P 500 constituents
        custom     — pass tickers directly via --tickers
    """
    universe = universe.lower().strip()

    if universe == "sp500":
        return get_sp500_tickers()
    elif universe == "nasdaq100":
        return get_nasdaq100_tickers()
    elif universe == "mega_cap":
        return _MEGA_CAP_50
    elif universe == "large_cap":
        return get_sp500_tickers()[:200]
    else:
        log.warning("Unknown universe '%s', using mega_cap", universe)
        return _MEGA_CAP_50


def load_price_data(
    tickers: list[str],
    start: str,
    end: str,
    batch_size: int = 20,
    use_cache: bool = True,
) -> dict[str, pd.DataFrame]:
    """Fetch historical OHLCV via yfinance for each ticker.

    Downloads in batches to avoid rate limiting and uses a local
    parquet cache to avoid re-downloading on subsequent runs.
    """
    import yfinance as yf

    result = {}
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = f"{start}_{end}"

    to_download = []
    for ticker in tickers:
        cache_path = _CACHE_DIR / f"{ticker}_{cache_key}.parquet"
        if use_cache and cache_path.exists():
            try:
                df = pd.read_parquet(cache_path)
                if not df.empty:
                    result[ticker] = df
                    continue
            except Exception:
                pass
        to_download.append(ticker)

    if result:
        log.info("Loaded %d tickers from cache", len(result))

    if not to_download:
        log.info("All %d tickers loaded from cache", len(tickers))
        return result

    log.info("Downloading %d tickers in batches of %d...", len(to_download), batch_size)

    for i in range(0, len(to_download), batch_size):
        batch = to_download[i : i + batch_size]
        batch_str = " ".join(batch)
        log.info(
            "  Batch %d/%d: %s... (%d tickers)",
            i // batch_size + 1,
            (len(to_download) + batch_size - 1) // batch_size,
            ", ".join(batch[:5]),
            len(batch),
        )

        try:
            df = yf.download(
                batch_str,
                start=start,
                end=end,
                progress=False,
                group_by="ticker",
                threads=True,
            )

            if df is None or df.empty:
                log.warning("  Batch returned no data")
                continue

            # Parse batch results
            if len(batch) == 1:
                # Single ticker — no MultiIndex on columns
                ticker = batch[0]
                single_df = df.copy()
                if hasattr(single_df.columns, "levels") and single_df.columns.nlevels > 1:
                    single_df.columns = single_df.columns.get_level_values(0)
                single_df.columns = [c.lower() for c in single_df.columns]
                if not single_df.empty and "close" in single_df.columns:
                    single_df = single_df.dropna(subset=["close"])
                    result[ticker] = single_df
                    # Cache
                    cache_path = _CACHE_DIR / f"{ticker}_{cache_key}.parquet"
                    single_df.to_parquet(cache_path)
            else:
                # Multiple tickers — MultiIndex columns (ticker, field)
                for ticker in batch:
                    try:
                        if ticker in df.columns.get_level_values(0):
                            ticker_df = df[ticker].copy()
                        elif ticker.upper() in df.columns.get_level_values(0):
                            ticker_df = df[ticker.upper()].copy()
                        else:
                            continue

                        ticker_df.columns = [c.lower() for c in ticker_df.columns]
                        ticker_df = ticker_df.dropna(subset=["close"])
                        if not ticker_df.empty:
                            result[ticker] = ticker_df
                            cache_path = _CACHE_DIR / f"{ticker}_{cache_key}.parquet"
                            ticker_df.to_parquet(cache_path)
                    except Exception as e:
                        log.debug("  Failed to parse %s: %s", ticker, e)

        except Exception as e:
            log.warning("  Batch download failed: %s", e)

        # Rate limit between batches
        if i + batch_size < len(to_download):
            time.sleep(1)

    log.info(
        "Data loading complete: %d/%d tickers loaded (%d from cache, %d downloaded)",
        len(result),
        len(tickers),
        len(tickers) - len(to_download),
        len(result) - (len(tickers) - len(to_download)),
    )
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


# ── Fallback ticker lists ──────────────────────────────────────────────────

_MEGA_CAP_50 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH",
    "LLY", "JPM", "V", "XOM", "AVGO", "MA", "PG", "JNJ", "COST", "HD", "MRK",
    "ABBV", "CRM", "AMD", "BAC", "NFLX", "CVX", "KO", "PEP", "TMO", "WMT",
    "LIN", "ACN", "MCD", "CSCO", "ADBE", "ABT", "DHR", "ORCL", "INTC", "PM",
    "TXN", "QCOM", "NEE", "UPS", "AMGN", "LOW", "RTX", "SPGI", "SYK", "INTU",
]

_NASDAQ100_FALLBACK = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA", "AVGO",
    "PEP", "COST", "ADBE", "CSCO", "NFLX", "CMCSA", "AMD", "INTC", "TXN",
    "QCOM", "AMGN", "INTU", "HON", "AMAT", "ISRG", "BKNG", "SBUX", "MDLZ",
    "ADI", "REGN", "VRTX", "LRCX", "GILD", "ADP", "PANW", "MU", "KLAC",
    "SNPS", "CDNS", "CSX", "MELI", "MAR", "CHTR", "PYPL", "ORLY", "MNST",
    "ABNB", "FTNT", "PCAR", "NXPI", "KDP", "CTAS", "AEP", "MCHP", "KHC",
    "DXCM", "EXC", "PAYX", "IDXX", "EA", "ROST", "ODFL", "LULU", "CTSH",
    "MRNA", "XEL", "ON", "GFS", "CPRT", "GEHC", "FAST", "FANG", "VRSK",
    "CEG", "DASH", "BKR", "CDW", "ZS", "TTD", "ANSS", "WBD", "TEAM",
    "DDOG", "ALGN", "WBA", "ILMN", "SIRI", "ENPH", "LCID", "RIVN", "JD",
]

_SP500_FALLBACK = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B", "UNH",
    "LLY", "JPM", "V", "XOM", "AVGO", "MA", "PG", "JNJ", "COST", "HD", "MRK",
    "ABBV", "CRM", "AMD", "BAC", "NFLX", "CVX", "KO", "PEP", "TMO", "WMT",
    "LIN", "ACN", "MCD", "CSCO", "ADBE", "ABT", "DHR", "ORCL", "INTC", "PM",
    "TXN", "QCOM", "NEE", "UPS", "AMGN", "LOW", "RTX", "SPGI", "SYK", "INTU",
    "GS", "ELV", "BLK", "ISRG", "T", "AXP", "BKNG", "MDLZ", "VRTX", "GILD",
    "ADI", "LRCX", "REGN", "DE", "MMC", "SBUX", "SLB", "C", "CI", "PLD",
    "MO", "CB", "TJX", "ZTS", "BSX", "AMAT", "CVS", "SCHW", "BDX", "TMUS",
    "SO", "DUK", "FI", "PGR", "CL", "ICE", "CME", "HCA", "EQIX", "EOG",
    "SHW", "MPC", "MCK", "PSA", "NOC", "EMR", "SNPS", "CDNS", "MSI", "APD",
]
