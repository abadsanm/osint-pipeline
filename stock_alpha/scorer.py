"""
Stock Alpha signal scorer.

Combines FinBERT sentiment, SVC metric, technical indicators, and
cross-correlation confidence into a unified alpha signal.

Two modes:
  1. RULE-BASED (default): Weighted combination of indicators with
     configurable thresholds. No training data needed.
  2. ML-BASED (optional): LightGBM model trained on historical features.
     Requires labeled training data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from stock_alpha.config import StockAlphaConfig
from stock_alpha.svc import SVCResult

log = logging.getLogger("stock_alpha.scorer")


@dataclass
class AlphaSignal:
    """A scored stock alpha signal ready for output."""
    ticker: str
    signal_score: float        # -1 (strong sell) to +1 (strong buy)
    signal_direction: str      # "bullish", "bearish", "neutral"
    confidence: float          # 0-1

    # Component scores
    sentiment_score: float
    svc_value: float
    technical_score: float
    correlation_confidence: float

    # Context
    contributing_signals: int
    top_sources: list[str]
    timestamp: datetime

    # Technical snapshot
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_pctb: Optional[float] = None
    price: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "signal_score": round(self.signal_score, 4),
            "signal_direction": self.signal_direction,
            "confidence": round(self.confidence, 4),
            "sentiment_score": round(self.sentiment_score, 4),
            "svc_value": round(self.svc_value, 6),
            "technical_score": round(self.technical_score, 4),
            "correlation_confidence": round(self.correlation_confidence, 4),
            "contributing_signals": self.contributing_signals,
            "top_sources": self.top_sources,
            "timestamp": self.timestamp.isoformat(),
            "rsi": round(self.rsi, 2) if self.rsi is not None else None,
            "macd_hist": round(self.macd_hist, 4) if self.macd_hist is not None else None,
            "bb_pctb": round(self.bb_pctb, 4) if self.bb_pctb is not None else None,
            "price": round(self.price, 2) if self.price is not None else None,
        }


class RuleBasedScorer:
    """Combines sentiment, SVC, and technicals into a weighted alpha signal.

    Weights:
      - Sentiment (FinBERT): 30%
      - SVC metric: 20%
      - Technical indicators: 30%
      - Cross-correlation confidence: 20%
    """

    WEIGHT_SENTIMENT = 0.30
    WEIGHT_SVC = 0.20
    WEIGHT_TECHNICAL = 0.30
    WEIGHT_CORRELATION = 0.20

    def score(
        self,
        ticker: str,
        sentiment_score: float,
        svc: Optional[SVCResult],
        technicals: Optional[pd.DataFrame],
        correlation_confidence: float,
        contributing_signals: int = 0,
        top_sources: Optional[list[str]] = None,
    ) -> AlphaSignal:
        """Compute a unified alpha signal from all components."""
        # Normalize SVC to -1..+1 range (tanh-like clamping)
        svc_value = svc.svc_value if svc else 0.0
        svc_normalized = max(-1.0, min(1.0, svc_value * 10))  # Scale up small values

        # Compute technical score
        tech_score, rsi, macd_hist, bb_pctb, price = self._score_technicals(technicals)

        # Weighted combination
        raw_score = (
            self.WEIGHT_SENTIMENT * sentiment_score
            + self.WEIGHT_SVC * svc_normalized
            + self.WEIGHT_TECHNICAL * tech_score
            + self.WEIGHT_CORRELATION * (correlation_confidence * 2 - 1)  # Map 0-1 to -1..+1
        )

        # Clamp to -1..+1
        signal_score = max(-1.0, min(1.0, raw_score))

        # Determine direction
        if signal_score > 0.15:
            direction = "bullish"
        elif signal_score < -0.15:
            direction = "bearish"
        else:
            direction = "neutral"

        # Confidence = average of component confidences
        components_present = sum([
            abs(sentiment_score) > 0.01,
            svc is not None,
            technicals is not None and not technicals.empty,
            correlation_confidence > 0,
        ])
        confidence = min(1.0, (correlation_confidence + abs(signal_score)) / 2)
        if components_present < 2:
            confidence *= 0.5  # Low confidence with few components

        return AlphaSignal(
            ticker=ticker,
            signal_score=signal_score,
            signal_direction=direction,
            confidence=confidence,
            sentiment_score=sentiment_score,
            svc_value=svc_value,
            technical_score=tech_score,
            correlation_confidence=correlation_confidence,
            contributing_signals=contributing_signals,
            top_sources=top_sources or [],
            timestamp=datetime.now(timezone.utc),
            rsi=rsi,
            macd_hist=macd_hist,
            bb_pctb=bb_pctb,
            price=price,
        )

    @staticmethod
    def _score_technicals(
        df: Optional[pd.DataFrame],
    ) -> tuple[float, Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Derive a -1..+1 technical score from indicators.

        Returns (score, rsi, macd_hist, bb_pctb, price).
        """
        if df is None or df.empty:
            return 0.0, None, None, None, None

        latest = df.iloc[-1]
        signals = []

        price = latest.get("close")
        rsi = latest.get("rsi")
        macd_hist = latest.get("macd_hist")
        bb_pctb = latest.get("bb_pctb")

        # RSI signal
        if rsi is not None and not pd.isna(rsi):
            if rsi < 30:
                signals.append(0.5)    # Oversold → bullish
            elif rsi > 70:
                signals.append(-0.5)   # Overbought → bearish
            else:
                signals.append(0.0)

        # MACD histogram signal
        if macd_hist is not None and not pd.isna(macd_hist):
            # Positive histogram = bullish momentum
            signals.append(max(-1.0, min(1.0, macd_hist * 5)))

        # Bollinger Band %B signal
        if bb_pctb is not None and not pd.isna(bb_pctb):
            if bb_pctb < 0.2:
                signals.append(0.3)    # Near lower band → bounce potential
            elif bb_pctb > 0.8:
                signals.append(-0.3)   # Near upper band → pullback risk
            else:
                signals.append(0.0)

        # SMA trend (price vs SMA20)
        sma_20 = latest.get("sma_20")
        if sma_20 is not None and price is not None and not pd.isna(sma_20):
            if price > sma_20:
                signals.append(0.2)
            else:
                signals.append(-0.2)

        if not signals:
            return 0.0, rsi, macd_hist, bb_pctb, price

        tech_score = sum(signals) / len(signals)
        return max(-1.0, min(1.0, tech_score)), rsi, macd_hist, bb_pctb, price
