"""Backtesting simulator — runs the scoring pipeline on historical data."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from schemas.prediction import Prediction
from stock_alpha.regime import classify_regime

log = logging.getLogger("backtesting.simulator")

def run_simulation(
    prices: dict[str, pd.DataFrame],
    features: dict[str, pd.DataFrame],
    horizons: list[int] = [1, 4, 24, 72, 168],
) -> list[dict]:
    """Simulate prediction generation for each ticker and each trading day.

    Returns a list of dicts, each with:
    {
        "ticker": str,
        "date": str,
        "features": dict,  # feature vector
        "prediction": Prediction,
        "regime": str,
        "actual_returns": {1: float, 4: float, 24: float, 72: float, 168: float},
    }
    """
    from stock_alpha.technicals import compute_technicals
    from stock_alpha.scorer import RuleBasedScorer

    scorer = RuleBasedScorer()
    results = []

    for ticker, price_df in prices.items():
        feature_df = features.get(ticker)
        if feature_df is None or len(feature_df) < 60:
            log.warning("Skipping %s: insufficient data (%d rows)", ticker, len(feature_df) if feature_df is not None else 0)
            continue

        # Compute technicals
        tech_df = compute_technicals(price_df.copy())
        if tech_df is None or tech_df.empty:
            continue

        log.info("Simulating %s: %d trading days", ticker, len(tech_df) - 60)

        # Start from day 60 (need lookback for indicators)
        for i in range(60, len(tech_df)):
            row = tech_df.iloc[i]
            date = tech_df.index[i]
            date_str = date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date)

            # Build feature vector
            close_price = float(row.get('close', 0))
            sma20 = row.get('sma_20')
            rsi = row.get('rsi')
            macd_hist = row.get('macd_hist')
            bb_pctb = row.get('bb_pctb')

            # Get sentiment features from synthetic data
            if date in feature_df.index:
                feat_row = feature_df.loc[date]
                sentiment = float(feat_row.get('sentiment_score', 0))
                svc = float(feat_row.get('svc_value', 0))
                corr_conf = float(feat_row.get('correlation_confidence', 0.5))
                source_count = int(feat_row.get('source_count', 1))
            else:
                sentiment = 0.0
                svc = 0.0
                corr_conf = 0.5
                source_count = 1

            # Score using rule-based scorer
            alpha = scorer.score_rule_based(
                ticker=ticker,
                sentiment_score=sentiment,
                svc=None,  # We pass svc directly in features
                technicals=tech_df.iloc[:i+1],
                correlation_confidence=corr_conf,
                contributing_signals=source_count,
            )

            # Classify regime
            regime = classify_regime(df=tech_df.iloc[max(0,i-60):i+1])

            # Compute actual forward returns for evaluation
            actual_returns = {}
            for h in horizons:
                # Convert hours to trading days (approximate: 6.5 trading hours/day)
                fwd_days = max(1, int(h / 6.5))
                if i + fwd_days < len(tech_df):
                    future_close = float(tech_df.iloc[i + fwd_days].get('close', close_price))
                    actual_returns[h] = round((future_close - close_price) / close_price * 100, 4) if close_price > 0 else 0.0
                else:
                    actual_returns[h] = None

            # Build feature dict for training
            feature_vector = {
                "sentiment_score": sentiment,
                "svc_value": svc,
                "rsi": float(rsi) if rsi is not None and not pd.isna(rsi) else None,
                "macd_hist": float(macd_hist) if macd_hist is not None and not pd.isna(macd_hist) else None,
                "bb_pctb": float(bb_pctb) if bb_pctb is not None and not pd.isna(bb_pctb) else None,
                "sma20_distance": round((close_price - float(sma20)) / float(sma20) * 100, 4) if sma20 is not None and not pd.isna(sma20) and float(sma20) > 0 else None,
                "correlation_confidence": corr_conf,
                "source_count": source_count,
                "close_price": close_price,
                "volume_zscore": float(feat_row.get('volume_zscore', 0)) if date in feature_df.index else 0,
            }

            # Create prediction for each horizon
            for h in horizons:
                if actual_returns.get(h) is None:
                    continue

                pred = Prediction(
                    ticker=ticker,
                    direction=alpha.signal_direction,
                    magnitude=abs(alpha.signal_score) * np.sqrt(h / 24) * 2,
                    confidence=max(0, min(1, alpha.confidence)),
                    raw_score=alpha.signal_score,
                    time_horizon_hours=h,
                    decay_rate=24.0,
                    regime=regime.regime,
                    model_version="backtest_v1",
                    created_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=h),
                )

                # Evaluate outcome
                actual = actual_returns[h]
                if alpha.signal_direction == "bullish":
                    pred.outcome = "correct" if actual > 0.1 else "incorrect"
                elif alpha.signal_direction == "bearish":
                    pred.outcome = "correct" if actual < -0.1 else "incorrect"
                else:  # neutral
                    pred.outcome = "correct" if abs(actual) < 0.5 else "incorrect"
                pred.actual_return = actual
                pred.evaluated_at = datetime.now(timezone.utc)

                results.append({
                    "ticker": ticker,
                    "date": date_str,
                    "features": feature_vector,
                    "prediction": pred,
                    "regime": regime.regime,
                    "actual_return": actual,
                    "horizon": h,
                })

    log.info("Simulation complete: %d predictions generated", len(results))
    return results
