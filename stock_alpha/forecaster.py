"""
Time-series forecaster with Prophet baseline and LSTM.

Provides two forecast approaches:
- Prophet: Always available, uses price history + optional sentiment/volume regressors
- LSTM: Used when 90+ days of feature history exist, learns from multi-feature sequences

Falls back gracefully: LSTM -> Prophet -> empty list.
"""

from __future__ import annotations

import logging
import time
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

from stock_alpha.config import StockAlphaConfig
from stock_alpha.technicals import PriceDataProvider

log = logging.getLogger("stock_alpha.forecaster")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ForecastResult(BaseModel):
    horizon_days: int
    predicted_return: float  # expected % return
    confidence_interval: tuple[float, float]  # (lower, upper) 95% CI
    confidence: float  # derived from CI width: narrower = higher
    model_used: Literal["prophet", "lstm"]


# ---------------------------------------------------------------------------
# Module-level caches
# ---------------------------------------------------------------------------

# Prophet model cache: {ticker: (fit_time, model, last_price)}
_prophet_cache: dict[str, tuple[float, object, float]] = {}
_PROPHET_CACHE_TTL = 30 * 60  # 30 minutes in seconds

# Shared price data provider (same pattern as technicals.py)
_price_provider = PriceDataProvider()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LSTM_MIN_HISTORY = 90  # Minimum days of feature history for LSTM
LSTM_LOOKBACK = 30  # Input sequence length for LSTM
LSTM_EPOCHS = 50
LSTM_HIDDEN = 64
LSTM_LR = 1e-3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def forecast(
    ticker: str,
    features_history: Optional[list[dict]] = None,
    horizons: list[int] = [1, 3, 7],
) -> list[ForecastResult]:
    """Generate forecasts at multiple horizons.

    Uses LSTM if features_history has 90+ days, otherwise Prophet.
    Falls back gracefully: LSTM -> Prophet -> empty list.
    """
    results: list[ForecastResult] = []

    # Try LSTM first if enough feature history is available
    if features_history and len(features_history) >= LSTM_MIN_HISTORY:
        try:
            results = _forecast_lstm(ticker, features_history, horizons)
            if results:
                log.info(
                    "LSTM forecast for %s: %d horizons", ticker, len(results)
                )
                return results
        except Exception as e:
            log.warning("LSTM forecast failed for %s, falling back to Prophet: %s", ticker, e)

    # Fall back to Prophet
    try:
        results = _forecast_prophet(ticker, features_history, horizons)
        if results:
            log.info(
                "Prophet forecast for %s: %d horizons", ticker, len(results)
            )
            return results
    except Exception as e:
        log.warning("Prophet forecast failed for %s: %s", ticker, e)

    log.warning("All forecast methods failed for %s, returning empty list", ticker)
    return []


# ---------------------------------------------------------------------------
# Prophet implementation
# ---------------------------------------------------------------------------


def _forecast_prophet(
    ticker: str,
    features_history: Optional[list[dict]],
    horizons: list[int],
) -> list[ForecastResult]:
    """Prophet-based price forecast with optional sentiment/volume regressors."""
    try:
        from prophet import Prophet
    except ImportError:
        log.warning("prophet package not installed, skipping Prophet forecast")
        return []

    # Check cache
    cached = _prophet_cache.get(ticker)
    if cached:
        fit_time, model, last_price = cached
        if time.time() - fit_time < _PROPHET_CACHE_TTL:
            return _prophet_predict(model, last_price, horizons)

    # Fetch price data
    prices_df = _price_provider.get_prices(ticker)
    if prices_df is None or prices_df.empty:
        log.warning("No price data for %s, cannot build Prophet model", ticker)
        return []

    # Build Prophet DataFrame
    prophet_df = pd.DataFrame()
    prophet_df["ds"] = prices_df.index.tz_localize(None) if prices_df.index.tz else prices_df.index
    prophet_df["y"] = prices_df["close"].values
    prophet_df = prophet_df.reset_index(drop=True)

    # Determine which regressors we can add
    has_sentiment = False
    has_volume = False

    if features_history and len(features_history) >= 5:
        feat_df = pd.DataFrame(features_history)

        # Sentiment SMA 5-day regressor
        if "sentiment_score" in feat_df.columns:
            sentiment_sma = feat_df["sentiment_score"].rolling(5, min_periods=1).mean()
            if len(sentiment_sma) == len(prophet_df):
                prophet_df["sentiment_sma_5d"] = sentiment_sma.values
                has_sentiment = True
            elif len(sentiment_sma) >= len(prophet_df):
                # Align from the tail (most recent entries match price data)
                prophet_df["sentiment_sma_5d"] = sentiment_sma.iloc[-len(prophet_df):].values
                has_sentiment = True

        # Volume z-score regressor
        if "volume_ratio" in feat_df.columns:
            vol = feat_df["volume_ratio"]
            vol_mean = vol.mean()
            vol_std = vol.std()
            if vol_std and vol_std > 0:
                volume_zscore = ((vol - vol_mean) / vol_std).fillna(0)
                if len(volume_zscore) == len(prophet_df):
                    prophet_df["volume_zscore"] = volume_zscore.values
                    has_volume = True
                elif len(volume_zscore) >= len(prophet_df):
                    prophet_df["volume_zscore"] = volume_zscore.iloc[-len(prophet_df):].values
                    has_volume = True

    # Fit Prophet
    model = Prophet(
        daily_seasonality=False,
        yearly_seasonality=True,
        weekly_seasonality=True,
        changepoint_prior_scale=0.05,
    )

    if has_sentiment:
        model.add_regressor("sentiment_sma_5d")
    if has_volume:
        model.add_regressor("volume_zscore")

    # Suppress Prophet's verbose stdout logging
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        model.fit(prophet_df)
    finally:
        sys.stdout = old_stdout

    current_price = float(prophet_df["y"].iloc[-1])

    # Cache the fitted model
    _prophet_cache[ticker] = (time.time(), model, current_price)

    return _prophet_predict(model, current_price, horizons)


def _prophet_predict(
    model: object,
    current_price: float,
    horizons: list[int],
) -> list[ForecastResult]:
    """Generate ForecastResult list from a fitted Prophet model."""
    from prophet import Prophet  # Already imported if we got here

    max_horizon = max(horizons)

    # Build future dataframe
    future = model.make_future_dataframe(periods=max_horizon, freq="B")  # Business days

    # Fill regressor columns in the future period with last known values
    for col in future.columns:
        if col not in ("ds", "y"):
            # Forward-fill any NaN in regressor columns
            future[col] = future[col].ffill()
            future[col] = future[col].fillna(0)

    forecast_df = model.predict(future)

    results: list[ForecastResult] = []
    for h in horizons:
        # Get the forecast row at horizon h business days from now
        idx = len(forecast_df) - max_horizon + h - 1
        if idx < 0 or idx >= len(forecast_df):
            continue

        row = forecast_df.iloc[idx]
        yhat = float(row["yhat"])
        yhat_lower = float(row["yhat_lower"])
        yhat_upper = float(row["yhat_upper"])

        # Predicted return as percentage
        predicted_return = (yhat - current_price) / current_price * 100

        # CI as percentage returns
        ci_lower = (yhat_lower - current_price) / current_price * 100
        ci_upper = (yhat_upper - current_price) / current_price * 100

        # Confidence: narrower CI relative to price = higher confidence
        ci_width = yhat_upper - yhat_lower
        confidence = max(0.0, min(1.0, 1.0 - (ci_width / current_price * 10)))

        results.append(
            ForecastResult(
                horizon_days=h,
                predicted_return=round(predicted_return, 4),
                confidence_interval=(round(ci_lower, 4), round(ci_upper, 4)),
                confidence=round(confidence, 4),
                model_used="prophet",
            )
        )

    return results


# ---------------------------------------------------------------------------
# LSTM implementation
# ---------------------------------------------------------------------------

# Feature column ordering for LSTM input
_LSTM_FEATURES = [
    "daily_return",
    "sentiment_score",
    "svc_value",
    "volume_ratio",
    "rsi_norm",
    # regime one-hot columns added dynamically
]

_REGIME_CATEGORIES = ["bull", "bear", "neutral", "volatile"]


def _prepare_lstm_data(
    features_history: list[dict],
) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Prepare LSTM input sequences from feature history.

    Returns (X, y_returns, feature_means_stds) or None if data is insufficient.
    X shape: (n_samples, LSTM_LOOKBACK, n_features)
    y shape: (n_samples,)
    """
    df = pd.DataFrame(features_history)

    # Ensure required columns exist, fill missing with defaults
    if "daily_return" not in df.columns:
        if "close" in df.columns:
            df["daily_return"] = df["close"].pct_change().fillna(0)
        else:
            return None

    for col in ["sentiment_score", "svc_value", "volume_ratio"]:
        if col not in df.columns:
            df[col] = 0.0

    # Normalize RSI to 0-1
    if "rsi" in df.columns:
        df["rsi_norm"] = df["rsi"].fillna(50) / 100.0
    else:
        df["rsi_norm"] = 0.5

    # One-hot encode regime
    if "regime" in df.columns:
        for cat in _REGIME_CATEGORIES:
            df[f"regime_{cat}"] = (df["regime"] == cat).astype(float)
    else:
        for cat in _REGIME_CATEGORIES:
            df[f"regime_{cat}"] = 0.0

    # Assemble feature matrix
    feature_cols = [
        "daily_return",
        "sentiment_score",
        "svc_value",
        "volume_ratio",
        "rsi_norm",
    ] + [f"regime_{cat}" for cat in _REGIME_CATEGORIES]

    # Fill NaN
    for col in feature_cols:
        df[col] = df[col].fillna(0)

    values = df[feature_cols].values.astype(np.float32)

    # Normalize features (z-score), save stats for inference
    means = values.mean(axis=0)
    stds = values.std(axis=0)
    stds[stds == 0] = 1.0  # Prevent division by zero
    values_norm = (values - means) / stds

    # Build lookback sequences
    X_list = []
    y_list = []

    for i in range(LSTM_LOOKBACK, len(values_norm)):
        X_list.append(values_norm[i - LSTM_LOOKBACK : i])
        # Target: next-day return (from daily_return column, index 0)
        y_list.append(values[i, 0])  # Use un-normalized return as target

    if len(X_list) < 10:  # Need at least 10 training samples
        return None

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)
    stats = np.stack([means, stds])  # (2, n_features)

    return X, y, stats


def _forecast_lstm(
    ticker: str,
    features_history: list[dict],
    horizons: list[int],
) -> list[ForecastResult]:
    """LSTM-based return forecast with uncertainty estimation."""
    try:
        import torch
        import torch.nn as nn
        from torch.utils.data import DataLoader, TensorDataset
    except ImportError:
        log.warning("PyTorch not available, skipping LSTM forecast")
        return []

    prepared = _prepare_lstm_data(features_history)
    if prepared is None:
        log.warning("Insufficient data for LSTM forecast on %s", ticker)
        return []

    X, y, stats = prepared
    n_features = X.shape[2]

    # -----------------------------------------------------------------------
    # Model definition
    # -----------------------------------------------------------------------

    class ReturnPredictor(nn.Module):
        def __init__(self, input_size: int, hidden_size: int = 64):
            super().__init__()
            self.lstm1 = nn.LSTM(input_size, hidden_size, batch_first=True)
            self.lstm2 = nn.LSTM(hidden_size, 32, batch_first=True)
            self.dropout = nn.Dropout(0.2)
            self.fc_mean = nn.Linear(32, 1)
            self.fc_std = nn.Linear(32, 1)

        def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
            x, _ = self.lstm1(x)
            x, _ = self.lstm2(x)
            x = self.dropout(x[:, -1, :])  # Last timestep
            mean = self.fc_mean(x)
            std = torch.nn.functional.softplus(self.fc_std(x))  # Ensure positive
            return mean, std

    # -----------------------------------------------------------------------
    # Training
    # -----------------------------------------------------------------------

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model = ReturnPredictor(input_size=n_features, hidden_size=LSTM_HIDDEN).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=LSTM_LR)
    mse_loss = nn.MSELoss()

    X_tensor = torch.from_numpy(X).to(device)
    y_tensor = torch.from_numpy(y).unsqueeze(-1).to(device)

    dataset = TensorDataset(X_tensor, y_tensor)
    loader = DataLoader(dataset, batch_size=32, shuffle=True)

    model.train()
    for epoch in range(LSTM_EPOCHS):
        epoch_loss = 0.0
        for batch_x, batch_y in loader:
            optimizer.zero_grad()
            pred_mean, pred_std = model(batch_x)
            loss = mse_loss(pred_mean, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()

    log.debug(
        "LSTM training for %s complete: final loss=%.6f",
        ticker,
        epoch_loss / max(len(loader), 1),
    )

    # -----------------------------------------------------------------------
    # Inference for each horizon
    # -----------------------------------------------------------------------

    model.eval()
    results: list[ForecastResult] = []

    # Use the most recent lookback window as input
    last_sequence = torch.from_numpy(X[-1:]).to(device)  # (1, LOOKBACK, n_features)

    with torch.no_grad():
        for h in horizons:
            # For multi-day horizons, we approximate by scaling single-step prediction
            # A proper approach would be iterative, but this is lightweight
            pred_mean, pred_std = model(last_sequence)

            mean_return = float(pred_mean.item())
            std_return = float(pred_std.item())

            # Scale by sqrt(horizon) for multi-day (random walk assumption)
            h_factor = np.sqrt(h)
            scaled_mean = mean_return * h
            scaled_std = std_return * h_factor

            # Convert to percentage
            predicted_return = scaled_mean * 100

            # 95% CI
            ci_lower = (scaled_mean - 1.96 * scaled_std) * 100
            ci_upper = (scaled_mean + 1.96 * scaled_std) * 100

            # Confidence: lower std = higher confidence
            confidence = max(0.0, min(1.0, 1.0 - scaled_std * 10))

            results.append(
                ForecastResult(
                    horizon_days=h,
                    predicted_return=round(predicted_return, 4),
                    confidence_interval=(round(ci_lower, 4), round(ci_upper, 4)),
                    confidence=round(confidence, 4),
                    model_used="lstm",
                )
            )

    return results


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def clear_cache(ticker: Optional[str] = None) -> None:
    """Clear Prophet model cache, optionally for a specific ticker."""
    if ticker:
        _prophet_cache.pop(ticker, None)
    else:
        _prophet_cache.clear()
