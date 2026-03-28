"""
Sentiment Volume Convergence (SVC) metric.

SVC = sentiment_shift x volume_change

Captures situations where sentiment is moving AND volume confirms.
High SVC values correlate with subsequent price movements.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from stock_alpha.config import StockAlphaConfig

log = logging.getLogger("stock_alpha.svc")


@dataclass
class SentimentDataPoint:
    """A timestamped sentiment observation for a ticker."""
    timestamp: datetime
    sentiment_score: float  # -1 to +1
    mention_count: int = 1  # Volume of mentions


@dataclass
class SVCResult:
    """Result of SVC computation for a ticker."""
    ticker: str
    svc_value: float
    sentiment_avg: float
    sentiment_shift: float
    volume_avg: float
    volume_change: float
    data_points: int


class SVCComputer:
    """Computes rolling SVC metrics per ticker.

    Maintains a time-series of sentiment data points per ticker
    and computes SVC on demand.
    """

    def __init__(self):
        # {ticker: deque of SentimentDataPoints}
        self._history: dict[str, deque[SentimentDataPoint]] = {}
        self._max_history = 1000  # Max data points per ticker

    def add_data_point(self, ticker: str, dp: SentimentDataPoint):
        """Add a sentiment observation for a ticker."""
        if ticker not in self._history:
            self._history[ticker] = deque(maxlen=self._max_history)
        self._history[ticker].append(dp)

    def compute(self, ticker: str) -> Optional[SVCResult]:
        """Compute the current SVC value for a ticker."""
        history = self._history.get(ticker)
        if not history or len(history) < StockAlphaConfig.SENTIMENT_WINDOW + 1:
            return None

        points = list(history)

        # Split into two windows for shift computation
        window = StockAlphaConfig.SENTIMENT_WINDOW
        if len(points) < window * 2:
            # Not enough for two windows — use what we have
            mid = len(points) // 2
            recent = points[mid:]
            prior = points[:mid]
        else:
            recent = points[-window:]
            prior = points[-window * 2 : -window]

        if not recent or not prior:
            return None

        # Sentiment averages
        recent_sentiment = sum(p.sentiment_score for p in recent) / len(recent)
        prior_sentiment = sum(p.sentiment_score for p in prior) / len(prior)
        sentiment_shift = recent_sentiment - prior_sentiment

        # Volume averages (mention count as proxy for volume)
        recent_volume = sum(p.mention_count for p in recent) / len(recent)
        prior_volume = sum(p.mention_count for p in prior) / len(prior)

        # Volume change (% change, handle zero — require minimum baseline)
        if prior_volume >= 2.0:
            volume_change = (recent_volume - prior_volume) / prior_volume
        elif recent_volume > prior_volume and recent_volume >= 3.0:
            volume_change = 0.5  # Conservative signal for new tickers
        else:
            volume_change = 0.0  # Not enough baseline to measure change

        # SVC = sentiment_shift * volume_change
        svc_value = sentiment_shift * volume_change

        return SVCResult(
            ticker=ticker,
            svc_value=round(svc_value, 6),
            sentiment_avg=round(recent_sentiment, 4),
            sentiment_shift=round(sentiment_shift, 4),
            volume_avg=round(recent_volume, 2),
            volume_change=round(volume_change, 4),
            data_points=len(points),
        )

    def get_tracked_tickers(self) -> list[str]:
        return list(self._history.keys())

    def cleanup(self, max_age_hours: int = 72):
        """Remove old data points."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        empty = []
        for ticker, history in self._history.items():
            while history and history[0].timestamp < cutoff:
                history.popleft()
            if not history:
                empty.append(ticker)
        for t in empty:
            del self._history[t]
