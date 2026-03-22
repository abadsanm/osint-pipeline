"""
Sentiment Velocity — rate of change of sentiment over time.

Instead of a static sentiment score, this module captures whether sentiment
is *accelerating* or *decelerating*, which is far more actionable for
detecting inflection points. A stock with moderately positive sentiment
that is accelerating fast is a stronger signal than one sitting at high
positive sentiment but flattening out.

Velocity = first derivative (sentiment change per hour).
Acceleration = second derivative (velocity change over the window).
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("stock_alpha.sentiment_velocity")

# Try numpy for fast linear regression; fall back to manual least-squares.
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

_MAX_WINDOW_HOURS = 48
_MAX_POINTS_PER_TICKER = 5000


@dataclass
class TimestampedSentiment:
    """A single sentiment observation with its timestamp and weight."""

    timestamp: datetime
    sentiment: float  # -1 to +1
    mention_count: int = 1


@dataclass
class SentimentVelocity:
    """Result of velocity computation for a ticker."""

    ticker: str
    velocity: float  # sentiment change per hour (positive = improving)
    acceleration: float  # rate of change of velocity (positive = accelerating bullish)
    momentum_score: float  # -1 to +1 composite score
    regime: str  # e.g. "accelerating_bullish"
    window_hours: float  # how many hours of data used
    data_points: int


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _linear_slope(xs: list[float], ys: list[float]) -> float:
    """Compute the slope of a least-squares linear fit.

    Parameters
    ----------
    xs : list[float]
        Independent variable values (e.g. hours since window start).
    ys : list[float]
        Dependent variable values (e.g. weighted sentiment).

    Returns
    -------
    float
        The slope of the best-fit line.  Returns 0.0 when the input
        is degenerate (all-equal x, single point, etc.).
    """
    n = len(xs)
    if n < 2:
        return 0.0

    if _HAS_NUMPY:
        xa = np.array(xs, dtype=np.float64)
        ya = np.array(ys, dtype=np.float64)
        # polyfit degree-1 → [slope, intercept]
        coeffs = np.polyfit(xa, ya, 1)
        return float(coeffs[0])

    # Manual least-squares: slope = (n*Σxy - Σx*Σy) / (n*Σx² - (Σx)²)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


def _bucket_observations(
    points: list[TimestampedSentiment],
    origin: datetime,
) -> tuple[list[float], list[float]]:
    """Group observations into hourly buckets weighted by mention_count.

    Returns parallel lists of (hour_offset, weighted_avg_sentiment).
    """
    buckets: dict[int, tuple[float, int]] = {}  # hour_index → (weighted_sum, total_weight)

    for pt in points:
        delta_h = (pt.timestamp - origin).total_seconds() / 3600.0
        bucket_idx = int(delta_h)
        w_sum, w_total = buckets.get(bucket_idx, (0.0, 0))
        buckets[bucket_idx] = (w_sum + pt.sentiment * pt.mention_count, w_total + pt.mention_count)

    xs: list[float] = []
    ys: list[float] = []
    for idx in sorted(buckets):
        w_sum, w_total = buckets[idx]
        if w_total > 0:
            xs.append(float(idx))
            ys.append(w_sum / w_total)

    return xs, ys


def _classify_regime(velocity: float, acceleration: float) -> str:
    """Classify the sentiment regime based on velocity and acceleration.

    Parameters
    ----------
    velocity : float
        First derivative of sentiment (per hour).
    acceleration : float
        Second derivative of sentiment.

    Returns
    -------
    str
        One of the five regime labels.
    """
    if velocity > 0.01:
        if acceleration > 0:
            return "accelerating_bullish"
        return "decelerating_bullish"
    if velocity < -0.01:
        if acceleration < 0:
            return "accelerating_bearish"
        return "decelerating_bearish"
    return "stable"


# ---------------------------------------------------------------------------
# Stateful tracker
# ---------------------------------------------------------------------------


class SentimentVelocityTracker:
    """Maintains per-ticker sentiment history and computes velocity metrics.

    Usage::

        tracker = SentimentVelocityTracker()
        tracker.add_observation("AAPL", datetime.now(timezone.utc), 0.35, mention_count=12)
        # ... more observations ...
        result = tracker.compute("AAPL")
        if result:
            print(result.regime, result.momentum_score)
    """

    def __init__(self) -> None:
        self._history: dict[str, deque[TimestampedSentiment]] = defaultdict(
            lambda: deque(maxlen=_MAX_POINTS_PER_TICKER)
        )

    # -- mutators -----------------------------------------------------------

    def add_observation(
        self,
        ticker: str,
        timestamp: datetime,
        sentiment: float,
        mention_count: int = 1,
    ) -> None:
        """Record a sentiment observation for *ticker*.

        Parameters
        ----------
        ticker : str
            Stock ticker symbol (e.g. ``"AAPL"``).
        timestamp : datetime
            When the observation was made.  Should be timezone-aware (UTC).
        sentiment : float
            Sentiment score in the range ``[-1, +1]``.
        mention_count : int, optional
            Number of underlying mentions this observation represents.
            Used as a weight when bucketing.  Defaults to 1.
        """
        self._history[ticker].append(
            TimestampedSentiment(
                timestamp=timestamp,
                sentiment=max(-1.0, min(1.0, sentiment)),
                mention_count=max(1, mention_count),
            )
        )

    def compute(self, ticker: str) -> Optional[SentimentVelocity]:
        """Compute velocity, acceleration, and momentum for *ticker*.

        Returns ``None`` if there are fewer than 3 data points (the minimum
        needed for a meaningful velocity estimate).  Acceleration requires
        at least 5 data points; if fewer are available it is set to 0.

        The observation window is capped at 48 hours — older points are
        trimmed before computation.
        """
        raw = self._history.get(ticker)
        if not raw or len(raw) < 3:
            return None

        # Trim to 48-hour window
        now = max(pt.timestamp for pt in raw)
        cutoff = now - timedelta(hours=_MAX_WINDOW_HOURS)
        points = [pt for pt in raw if pt.timestamp >= cutoff]

        if len(points) < 3:
            return None

        origin = min(pt.timestamp for pt in points)
        window_hours = (now - origin).total_seconds() / 3600.0

        # Bucket into hourly weighted averages
        xs, ys = _bucket_observations(points, origin)
        if len(xs) < 2:
            return None

        # Velocity — slope of sentiment over time (per hour)
        velocity = _linear_slope(xs, ys)

        # Acceleration — compare velocity of recent half vs prior half
        acceleration = 0.0
        if len(points) >= 5 and len(xs) >= 4:
            mid = len(xs) // 2
            prior_xs, prior_ys = xs[:mid], ys[:mid]
            recent_xs, recent_ys = xs[mid:], ys[mid:]
            v_prior = _linear_slope(prior_xs, prior_ys)
            v_recent = _linear_slope(recent_xs, recent_ys)
            acceleration = v_recent - v_prior

        # Momentum score — bounded in [-1, +1] via tanh
        momentum_score = math.tanh(velocity * 5.0 + acceleration * 2.0)

        regime = _classify_regime(velocity, acceleration)

        log.debug(
            "ticker=%s vel=%.4f acc=%.4f momentum=%.3f regime=%s pts=%d",
            ticker,
            velocity,
            acceleration,
            momentum_score,
            regime,
            len(points),
        )

        return SentimentVelocity(
            ticker=ticker,
            velocity=round(velocity, 6),
            acceleration=round(acceleration, 6),
            momentum_score=round(momentum_score, 4),
            regime=regime,
            window_hours=round(window_hours, 2),
            data_points=len(points),
        )

    # -- maintenance --------------------------------------------------------

    def cleanup(self, max_age_hours: int = 72) -> None:
        """Remove observations older than *max_age_hours* and drop empty tickers."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        empty: list[str] = []
        for ticker, history in self._history.items():
            while history and history[0].timestamp < cutoff:
                history.popleft()
            if not history:
                empty.append(ticker)
        for t in empty:
            del self._history[t]
        if empty:
            log.info("cleanup: dropped %d stale tickers", len(empty))

    def get_tracked_tickers(self) -> list[str]:
        """Return a list of tickers currently being tracked."""
        return list(self._history.keys())
