"""
FinBERT-based financial sentiment analysis.

Analyzes text from correlated signals using ProsusAI/finbert.
Outputs numeric sentiment scores: positive=+1, neutral=0, negative=-1,
weighted by model confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from stock_alpha.config import StockAlphaConfig

log = logging.getLogger("stock_alpha.sentiment")


@dataclass
class SentimentResult:
    """Sentiment analysis result for a single text."""
    label: str          # "positive", "negative", "neutral"
    confidence: float   # Model confidence (0-1)
    score: float        # Numeric: +confidence, 0, -confidence


class FinBERTAnalyzer:
    """Runs FinBERT inference for financial sentiment analysis."""

    def __init__(self, model_name: str = ""):
        self._model_name = model_name or StockAlphaConfig.FINBERT_MODEL
        self._pipeline = None

    def setup(self):
        """Load the FinBERT model. Takes ~10-30s on first run (downloads ~440MB)."""
        from transformers import pipeline

        log.info("Loading FinBERT model '%s'...", self._model_name)
        self._pipeline = pipeline(
            "sentiment-analysis",
            model=self._model_name,
            truncation=True,
            max_length=StockAlphaConfig.MAX_TEXT_LENGTH,
        )
        log.info("FinBERT model loaded")

    def analyze(self, text: str) -> SentimentResult:
        """Analyze a single text and return sentiment."""
        if not self._pipeline or not text:
            return SentimentResult(label="neutral", confidence=0.0, score=0.0)

        results = self._pipeline(text[:StockAlphaConfig.MAX_TEXT_LENGTH * 4])
        result = results[0]
        return _to_sentiment_result(result)

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Analyze multiple texts in a batch."""
        if not self._pipeline or not texts:
            return [SentimentResult(label="neutral", confidence=0.0, score=0.0)] * len(texts)

        # Truncate texts
        truncated = [t[:StockAlphaConfig.MAX_TEXT_LENGTH * 4] for t in texts]

        results = self._pipeline(
            truncated,
            batch_size=StockAlphaConfig.SENTIMENT_BATCH_SIZE,
        )
        return [_to_sentiment_result(r) for r in results]

    def teardown(self):
        self._pipeline = None


def _to_sentiment_result(raw: dict) -> SentimentResult:
    """Convert transformers pipeline output to SentimentResult."""
    label = raw.get("label", "neutral").lower()
    confidence = raw.get("score", 0.0)

    if label == "positive":
        score = confidence
    elif label == "negative":
        score = -confidence
    else:
        score = 0.0

    return SentimentResult(label=label, confidence=confidence, score=score)


def aggregate_sentiment(results: list[SentimentResult]) -> dict:
    """Aggregate multiple sentiment results into summary stats."""
    if not results:
        return {
            "avg_score": 0.0,
            "avg_confidence": 0.0,
            "positive_pct": 0.0,
            "negative_pct": 0.0,
            "neutral_pct": 0.0,
            "count": 0,
        }

    scores = [r.score for r in results]
    confidences = [r.confidence for r in results]
    n = len(results)

    positive = sum(1 for r in results if r.label == "positive")
    negative = sum(1 for r in results if r.label == "negative")
    neutral = sum(1 for r in results if r.label == "neutral")

    return {
        "avg_score": sum(scores) / n,
        "avg_confidence": sum(confidences) / n,
        "positive_pct": positive / n,
        "negative_pct": negative / n,
        "neutral_pct": neutral / n,
        "count": n,
    }
