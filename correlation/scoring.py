"""
Confidence scoring for correlated signals.

Combines three factors:
  1. Source diversity (40%) — weighted by source reliability
  2. Volume anomaly (30%) — z-score of current velocity vs baseline
  3. Quality aggregation (30%) — average quality signals from contributing docs
"""

from __future__ import annotations

import math

from correlation.config import CorrelationConfig
from correlation.window_store import WindowSlice


def compute_source_diversity(window: WindowSlice) -> float:
    """Score based on number and reliability of distinct sources.

    Returns 0.0-1.0. Higher when mentions come from multiple
    high-reliability sources.
    """
    sources = window.unique_sources
    if not sources:
        return 0.0

    total_possible = sum(CorrelationConfig.SOURCE_WEIGHTS.values())
    achieved = sum(
        CorrelationConfig.SOURCE_WEIGHTS.get(s, 0.3)
        for s in sources
    )

    # Normalize to 0-1, but cap so that 3+ reliable sources saturates
    raw = achieved / max(total_possible * 0.4, 1.0)
    return min(1.0, raw)


def compute_volume_anomaly(velocity: float, mean_velocity: float, stddev: float) -> float:
    """Map velocity z-score to 0.0-1.0 via sigmoid.

    z > 2.0 is notable, z > 3.0 is highly anomalous.
    """
    if stddev <= 0:
        stddev = 1.0

    zscore = (velocity - mean_velocity) / stddev

    # Sigmoid: 1 / (1 + exp(-0.5 * (z - 2.0)))
    try:
        score = 1.0 / (1.0 + math.exp(-0.5 * (zscore - 2.0)))
    except OverflowError:
        score = 0.0 if zscore < 0 else 1.0

    return score, zscore


def compute_quality_aggregate(window: WindowSlice) -> float:
    """Average quality signals from contributing documents.

    Factors:
    - Entity confidence from NER
    - Quality score (normalized)
    - Verification status boost
    """
    if not window.mentions:
        return 0.0

    scores = []
    for m in window.mentions:
        base = m.entity_confidence

        # Boost for quality score (normalize roughly)
        if m.quality_score and m.quality_score > 0:
            # Log-scale normalization — HN points, GitHub stars, dollar values
            # all on vastly different scales
            quality_boost = min(0.3, math.log1p(m.quality_score) / 30.0)
            base += quality_boost

        # Penalty for very new accounts (bot signal)
        if m.account_age_days is not None and m.account_age_days < 30:
            base *= 0.7

        scores.append(min(1.0, base))

    return sum(scores) / len(scores)


def compute_confidence(
    window: WindowSlice,
    mean_velocity: float,
    stddev_velocity: float,
) -> tuple[float, float, float]:
    """Compute the final confidence score for a correlated signal.

    Returns (confidence_score, velocity_zscore, volume_anomaly_component).
    """
    source_div = compute_source_diversity(window)
    vol_anomaly, zscore = compute_volume_anomaly(
        window.velocity, mean_velocity, stddev_velocity,
    )
    quality = compute_quality_aggregate(window)

    confidence = (
        CorrelationConfig.WEIGHT_SOURCE_DIVERSITY * source_div
        + CorrelationConfig.WEIGHT_VOLUME_ANOMALY * vol_anomaly
        + CorrelationConfig.WEIGHT_QUALITY * quality
    )

    return min(1.0, max(0.0, confidence)), zscore, vol_anomaly
