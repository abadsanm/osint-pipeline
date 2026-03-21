"""
Stock Alpha Engine orchestrator.

Consumes CorrelatedSignals from osint.correlated.stock_alpha,
runs FinBERT sentiment on contributing documents, computes SVC,
fetches technical indicators, computes microstructure and order flow
indicators from cached market data, and emits scored alpha signals.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Optional

from stock_alpha.config import StockAlphaConfig
from stock_alpha.scorer import AlphaSignal, RuleBasedScorer
from stock_alpha.sentiment import FinBERTAnalyzer, SentimentResult, aggregate_sentiment
from stock_alpha.svc import SVCComputer, SentimentDataPoint
from stock_alpha.technicals import PriceDataProvider, compute_technicals
from stock_alpha.microstructure import (
    compute_anchored_vwap,
    compute_volume_profile,
    detect_fair_value_gaps,
)
from stock_alpha.order_flow import compute_order_flow_delta, detect_liquidity_sweeps
from schemas.signal import CorrelatedSignal
from schemas.market_data import MarketBar, AggregatedTrade, OrderBookSnapshot

log = logging.getLogger("stock_alpha.engine")

# Maximum cached items per symbol
_MAX_BARS = 500
_MAX_TRADES = 2000
_MAX_DEPTH = 100


class StockAlphaEngine:
    """Processes correlated signals into scored alpha signals."""

    def __init__(self):
        self._analyzer = FinBERTAnalyzer()
        self._svc = SVCComputer()
        self._prices = PriceDataProvider()
        self._scorer = RuleBasedScorer()

        # Market data caches (symbol -> deque of recent data)
        self._bars: dict[str, deque[MarketBar]] = defaultdict(lambda: deque(maxlen=_MAX_BARS))
        self._trades: dict[str, deque[AggregatedTrade]] = defaultdict(lambda: deque(maxlen=_MAX_TRADES))
        self._depth: dict[str, deque[OrderBookSnapshot]] = defaultdict(lambda: deque(maxlen=_MAX_DEPTH))

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

        # 4. Compute microstructure & order flow from cached market data
        micro_signals = self._compute_microstructure(ticker)
        flow_signals = self._compute_order_flow(ticker)

        # 5. Score the signal
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
            microstructure_signals=micro_signals,
            order_flow_signals=flow_signals,
        )

        return alpha

    # ------------------------------------------------------------------
    # Market data ingestion (called from __main__ when consuming market topics)
    # ------------------------------------------------------------------

    def ingest_bar(self, data: dict):
        """Cache a market bar from Kafka."""
        try:
            bar = MarketBar.model_validate(data)
            self._bars[bar.symbol].append(bar)
        except Exception as e:
            log.debug("Failed to ingest bar: %s", e)

    def ingest_trade(self, data: dict):
        """Cache an aggregated trade from Kafka."""
        try:
            trade = AggregatedTrade.model_validate(data)
            self._trades[trade.symbol].append(trade)
        except Exception as e:
            log.debug("Failed to ingest trade: %s", e)

    def ingest_depth(self, data: dict):
        """Cache an order book snapshot from Kafka."""
        try:
            snap = OrderBookSnapshot.model_validate(data)
            self._depth[snap.symbol].append(snap)
        except Exception as e:
            log.debug("Failed to ingest depth: %s", e)

    # ------------------------------------------------------------------
    # Microstructure & order flow computation
    # ------------------------------------------------------------------

    def _compute_microstructure(self, ticker: str) -> Optional[dict]:
        """Compute microstructure signals from cached bars.

        Returns None if no market data is available for the ticker.
        """
        bars = list(self._bars.get(ticker, []))
        if len(bars) < 3:
            return None

        signals: dict = {}

        # Anchored VWAP — anchor at start of cached data
        vwap_values = compute_anchored_vwap(bars, bars[0].timestamp)
        if vwap_values:
            latest_vwap = vwap_values[-1]
            latest_price = bars[-1].close
            if latest_vwap > 0:
                # Positive = price above VWAP (bullish)
                signals["anchored_vwap_position"] = (latest_price - latest_vwap) / latest_vwap

        # Volume profile
        vp = compute_volume_profile(bars)
        if vp and vp.get("poc_price"):
            poc = vp["poc_price"]
            latest_price = bars[-1].close
            if poc > 0:
                signals["volume_profile_position"] = (latest_price - poc) / poc

        # Fair value gaps — net bias
        fvgs = detect_fair_value_gaps(bars)
        if fvgs:
            bullish_count = sum(1 for f in fvgs if f["type"] == "bullish")
            bearish_count = sum(1 for f in fvgs if f["type"] == "bearish")
            total = bullish_count + bearish_count
            signals["fvg_bias"] = (bullish_count - bearish_count) / total if total > 0 else 0.0

        return signals if signals else None

    def _compute_order_flow(self, ticker: str) -> Optional[dict]:
        """Compute order flow signals from cached trades and depth snapshots.

        Returns None if no market data is available for the ticker.
        """
        trades = list(self._trades.get(ticker, []))
        if len(trades) < 5:
            return None

        signals: dict = {}

        # Order flow delta
        delta = compute_order_flow_delta(trades)
        signals["cumulative_delta"] = delta["cumulative_delta"]
        signals["imbalance_ratio"] = delta["imbalance_ratio"]
        total_vol = delta["buy_volume"] + delta["sell_volume"]
        if total_vol > 0:
            signals["cumulative_delta_normalized"] = delta["cumulative_delta"] / total_vol
        else:
            signals["cumulative_delta_normalized"] = 0.0

        # Liquidity sweeps
        depth = list(self._depth.get(ticker, []))
        if depth:
            sweeps = detect_liquidity_sweeps(depth, trades)
            if sweeps:
                ask_sweeps = sum(1 for s in sweeps if s["sweep_type"] == "ask")
                bid_sweeps = sum(1 for s in sweeps if s["sweep_type"] == "bid")
                total_sweeps = ask_sweeps + bid_sweeps
                # Ask sweeps = buying pressure (bullish), bid sweeps = selling pressure (bearish)
                signals["sweep_bias"] = (ask_sweeps - bid_sweeps) / total_sweeps if total_sweeps > 0 else 0.0

        return signals if signals else None

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
