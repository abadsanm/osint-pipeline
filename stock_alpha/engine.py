"""
Stock Alpha Engine orchestrator.

Consumes CorrelatedSignals from osint.correlated.stock_alpha,
runs FinBERT sentiment on contributing documents, computes SVC,
fetches technical indicators, and emits scored alpha signals.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from stock_alpha.config import StockAlphaConfig
from stock_alpha.scorer import AlphaSignal, RuleBasedScorer
from stock_alpha.sentiment import FinBERTAnalyzer, SentimentResult, aggregate_sentiment
from stock_alpha.svc import SVCComputer, SentimentDataPoint
from stock_alpha.technicals import PriceDataProvider, compute_technicals
from schemas.signal import CorrelatedSignal

log = logging.getLogger("stock_alpha.engine")


class StockAlphaEngine:
    """Processes correlated signals into scored alpha signals."""

    def __init__(self):
        self._analyzer = FinBERTAnalyzer()
        self._svc = SVCComputer()
        self._prices = PriceDataProvider()
        self._scorer = RuleBasedScorer()

    def setup(self):
        """Load models."""
        self._analyzer.setup()
        log.info("Stock Alpha Engine initialized")

    def process_signal(self, signal: CorrelatedSignal) -> Optional[AlphaSignal]:
        """Process a single correlated signal into an alpha signal."""
        # Only process TICKER and COMPANY entity types
        if signal.entity_type not in ("TICKER", "COMPANY"):
            return None

        # Check minimum confidence
        if signal.confidence_score < StockAlphaConfig.MIN_CONFIDENCE_TO_EMIT:
            return None

        ticker = signal.entity_text.upper()

        # 1. Run FinBERT sentiment on the signal content
        sentiment_score = self._analyze_sentiment(signal)

        # 2. Update SVC with new sentiment data
        self._svc.add_data_point(ticker, SentimentDataPoint(
            timestamp=datetime.now(timezone.utc),
            sentiment_score=sentiment_score,
            mention_count=signal.total_mentions,
        ))
        svc_result = self._svc.compute(ticker)

        # 3. Fetch price data and compute technicals
        technicals = self._get_technicals(ticker)

        # 4. Score the signal
        top_sources = sorted(
            signal.source_breakdown.keys(),
            key=lambda s: signal.source_breakdown[s].mention_count,
            reverse=True,
        )[:3]

        alpha = self._scorer.score(
            ticker=ticker,
            sentiment_score=sentiment_score,
            svc=svc_result,
            technicals=technicals,
            correlation_confidence=signal.confidence_score,
            contributing_signals=signal.total_mentions,
            top_sources=top_sources,
        )

        return alpha

    def _analyze_sentiment(self, signal: CorrelatedSignal) -> float:
        """Run FinBERT on the signal's entity text and title."""
        # Build texts from the signal context
        texts = []

        # Use the signal title as the primary text
        title = f"{signal.entity_text}: {signal.signal_type.value}"
        texts.append(title)

        # Add a summary of source contributions
        for source, contrib in signal.source_breakdown.items():
            texts.append(
                f"{signal.entity_text} mentioned {contrib.mention_count} times on {source}"
            )

        if not texts:
            return 0.0

        results = self._analyzer.analyze_batch(texts)
        agg = aggregate_sentiment(results)
        return agg["avg_score"]

    def _get_technicals(self, ticker: str):
        """Fetch price data and compute technical indicators."""
        df = self._prices.get_prices(ticker)
        if df is None or df.empty:
            return None
        return compute_technicals(df)

    def teardown(self):
        self._analyzer.teardown()
        self._svc.cleanup()

    @property
    def stats(self) -> dict:
        return {
            "tickers_tracked": len(self._svc.get_tracked_tickers()),
        }
