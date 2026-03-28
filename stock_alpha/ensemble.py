"""
Ensemble Model Selection

Combines predictions from multiple models into a final calibrated prediction:
1. Rule-based scorer (always available)
2. XGBoost learned scorer (when trained)
3. Prophet forecaster (always available)
4. LSTM forecaster (when trained)

Weighting: rolling 30-day accuracy per model per regime.
Agreement factor: 4/4 agree = 1.0, 3/4 = 0.85, 2/4 = 0.6
Strong disagreement → output "neutral" with low confidence.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from schemas.prediction import Prediction
from stock_alpha.regime import MarketRegime

log = logging.getLogger("stock_alpha.ensemble")

# Default weight when a model has no track record
_DEFAULT_WEIGHT = 0.25

# Agreement multipliers
_AGREEMENT_FACTORS = {
    4: 1.0,   # unanimous
    3: 0.85,  # strong majority
    2: 0.6,   # split
    1: 0.3,   # lone dissenter — very low confidence
    0: 0.1,   # shouldn't happen but handle it
}


class ModelVote:
    """A single model's prediction."""
    def __init__(self, model_name: str, direction: str, score: float, confidence: float):
        self.model_name = model_name
        self.direction = direction  # "bullish", "bearish", "neutral"
        self.score = score  # -1 to +1
        self.confidence = confidence  # 0 to 1


def ensemble_predict(
    ticker: str,
    rule_score: float,
    rule_confidence: float,
    xgb_score: Optional[float],
    xgb_confidence: Optional[float],
    prophet_forecast: Optional[dict],  # {predicted_return, confidence}
    lstm_forecast: Optional[dict],    # {predicted_return, confidence}
    regime: MarketRegime,
    model_accuracies: Optional[dict] = None,  # {model_name: {regime: accuracy}}
    time_horizon_hours: int = 24,
    decay_rate: float = 24.0,
) -> Prediction:
    """Combine multiple model outputs into a single ensemble prediction."""

    if model_accuracies is None:
        model_accuracies = {}

    # Build votes from available models
    votes: list[ModelVote] = []

    # 1. Rule-based (always available)
    votes.append(ModelVote(
        "rule_based",
        _score_to_direction(rule_score),
        rule_score,
        rule_confidence,
    ))

    # 2. XGBoost (if available)
    if xgb_score is not None:
        votes.append(ModelVote(
            "xgboost",
            _score_to_direction(xgb_score),
            xgb_score,
            xgb_confidence or 0.5,
        ))

    # 3. Prophet (if available)
    if prophet_forecast is not None:
        ret = prophet_forecast.get("predicted_return", 0)
        conf = prophet_forecast.get("confidence", 0.5)
        direction = "bullish" if ret > 0.1 else "bearish" if ret < -0.1 else "neutral"
        # Smooth saturation: tanh maps small returns proportionally, large returns saturate
        score = math.tanh(ret / 5)
        votes.append(ModelVote("prophet", direction, score, conf))

    # 4. LSTM (if available)
    if lstm_forecast is not None:
        ret = lstm_forecast.get("predicted_return", 0)
        conf = lstm_forecast.get("confidence", 0.5)
        direction = "bullish" if ret > 0.1 else "bearish" if ret < -0.1 else "neutral"
        score = math.tanh(ret / 5)
        votes.append(ModelVote("lstm", direction, score, conf))

    # Get per-model weights based on accuracy for this regime
    weights = _get_model_weights(votes, regime.regime, model_accuracies)

    # Weighted vote
    total_weight = sum(weights.values())
    if total_weight == 0:
        total_weight = 1.0

    weighted_score = sum(
        v.score * weights.get(v.model_name, _DEFAULT_WEIGHT)
        for v in votes
    ) / total_weight

    weighted_confidence = sum(
        v.confidence * weights.get(v.model_name, _DEFAULT_WEIGHT)
        for v in votes
    ) / total_weight

    # Confidence-weighted agreement (not just majority vote)
    bullish_weight = sum(v.confidence * weights.get(v.model_name, _DEFAULT_WEIGHT) for v in votes if v.direction == "bullish")
    bearish_weight = sum(v.confidence * weights.get(v.model_name, _DEFAULT_WEIGHT) for v in votes if v.direction == "bearish")
    neutral_weight = sum(v.confidence * weights.get(v.model_name, _DEFAULT_WEIGHT) for v in votes if v.direction == "neutral")

    directions = [v.direction for v in votes]
    bullish_count = directions.count("bullish")
    bearish_count = directions.count("bearish")

    # Use weighted direction, not pure count
    if bullish_weight > bearish_weight and bullish_weight > neutral_weight:
        final_direction = "bullish"
        agreement_count = bullish_count
    elif bearish_weight > bullish_weight and bearish_weight > neutral_weight:
        final_direction = "bearish"
        agreement_count = bearish_count
    else:
        final_direction = "neutral"
        agreement_count = max(bullish_count, bearish_count, directions.count("neutral"))

    # If strong disagreement (models split evenly), force neutral with reduced confidence
    n_models = len(votes)
    if n_models >= 3 and agreement_count <= n_models / 2:
        final_direction = "neutral"
        weighted_confidence = max(0.15, weighted_confidence - 0.2)  # Additive penalty, not multiplicative

    # Apply agreement factor
    agreement_factor = _AGREEMENT_FACTORS.get(agreement_count, 0.5)
    final_confidence = min(1.0, weighted_confidence * agreement_factor)

    # Clamp score
    final_score = max(-1.0, min(1.0, weighted_score))

    # Compute magnitude from score
    magnitude = abs(final_score) * math.sqrt(time_horizon_hours / 24) * 2

    # Build contributing signals list
    contributing = [
        {
            "source": v.model_name,
            "signal_type": v.direction,
            "value": round(v.score, 4),
            "weight": round(weights.get(v.model_name, _DEFAULT_WEIGHT), 3),
        }
        for v in votes
    ]

    log.info(
        "Ensemble %s: %s (score=%.3f, conf=%.3f) — %d/%d agree, regime=%s",
        ticker, final_direction, final_score, final_confidence,
        agreement_count, n_models, regime.regime,
    )

    return Prediction(
        ticker=ticker,
        direction=final_direction,
        magnitude=round(magnitude, 4),
        confidence=round(final_confidence, 4),
        raw_score=round(final_score, 4),
        time_horizon_hours=time_horizon_hours,
        decay_rate=decay_rate,
        regime=regime.regime,
        contributing_signals=contributing,
        model_version="ensemble_v1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=time_horizon_hours),
    )


def _score_to_direction(score: float) -> str:
    if score > 0.15:
        return "bullish"
    elif score < -0.15:
        return "bearish"
    return "neutral"


def _get_model_weights(
    votes: list[ModelVote],
    regime: str,
    model_accuracies: dict,
) -> dict[str, float]:
    """Get per-model weights based on rolling accuracy for this regime."""
    weights = {}
    for v in votes:
        acc_data = model_accuracies.get(v.model_name, {})
        regime_acc = acc_data.get(regime)
        if regime_acc is not None and regime_acc > 0:
            # Weight proportional to accuracy, but minimum 0.1
            weights[v.model_name] = max(0.1, regime_acc)
        else:
            weights[v.model_name] = _DEFAULT_WEIGHT
    return weights
