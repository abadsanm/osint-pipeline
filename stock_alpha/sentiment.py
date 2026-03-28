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
    """Runs FinBERT inference for financial sentiment analysis.

    Tries ONNX Runtime via optimum for 3-5x faster CPU inference.
    Falls back to standard PyTorch if optimum is not installed.
    First ONNX run converts the model (~2 min), then uses the cached version.
    """

    def __init__(self, model_name: str = ""):
        self._model_name = model_name or StockAlphaConfig.FINBERT_MODEL
        self._pipeline = None
        self._using_onnx = False

    def setup(self):
        """Load the FinBERT model. Prefers ONNX Runtime, falls back to PyTorch."""
        from transformers import AutoTokenizer, pipeline

        log.info("Loading FinBERT model '%s'...", self._model_name)

        # Try ONNX Runtime first (3-5x faster on CPU)
        try:
            from optimum.onnxruntime import ORTModelForSequenceClassification

            model = ORTModelForSequenceClassification.from_pretrained(
                self._model_name, export=True,
            )
            tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._pipeline = pipeline(
                "sentiment-analysis",
                model=model,
                tokenizer=tokenizer,
                truncation=True,
                max_length=256,
            )
            self._using_onnx = True
            log.info("FinBERT loaded with ONNX Runtime (accelerated)")
        except (ImportError, Exception) as e:
            if isinstance(e, ImportError):
                log.info("optimum not installed, using PyTorch: pip install optimum[onnxruntime]")
            else:
                log.warning("ONNX export failed, falling back to PyTorch: %s", e)

            self._pipeline = pipeline(
                "sentiment-analysis",
                model=self._model_name,
                truncation=True,
                max_length=256,
            )
            log.info("FinBERT loaded with PyTorch")

    def analyze(self, text: str) -> SentimentResult:
        """Analyze a single text and return sentiment."""
        if not self._pipeline or not text:
            return SentimentResult(label="neutral", confidence=0.0, score=0.0)

        results = self._pipeline(text[:1024])
        result = results[0]
        return _to_sentiment_result(result)

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        """Analyze multiple texts in a batch."""
        if not self._pipeline or not texts:
            return [SentimentResult(label="neutral", confidence=0.0, score=0.0)] * len(texts)

        # Truncate texts (use 256 * 4 chars as rough token estimate)
        truncated = [t[:1024] for t in texts]

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
