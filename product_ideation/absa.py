"""
Aspect-Based Sentiment Analysis (ABSA).

Extracts product aspects from review text and assigns per-aspect sentiment.
Uses PyABSA for aspect extraction and sentiment classification.
Falls back to spaCy noun-chunk extraction if PyABSA is unavailable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from product_ideation.config import ProductIdeationConfig

log = logging.getLogger("product_ideation.absa")


@dataclass
class AspectResult:
    """A single aspect extraction with sentiment."""
    aspect: str
    sentiment: str      # "Positive", "Negative", "Neutral"
    confidence: float


class ABSAExtractor:
    """Extracts aspects and sentiment from review text."""

    def __init__(self):
        self._extractor = None
        self._fallback_nlp = None

    def setup(self):
        """Load ABSA model. Tries PyABSA first, falls back to spaCy."""
        try:
            from pyabsa import AspectTermExtraction as ATEPC

            self._extractor = ATEPC.AspectExtractor(
                checkpoint=ProductIdeationConfig.ABSA_MODEL,
            )
            log.info("PyABSA model loaded (checkpoint=%s)", ProductIdeationConfig.ABSA_MODEL)
        except Exception as e:
            log.warning("PyABSA not available (%s), using spaCy fallback", e)
            import spacy
            self._fallback_nlp = spacy.load("en_core_web_lg", disable=["parser"])
            log.info("spaCy fallback loaded for ABSA")

    def extract(self, text: str) -> list[AspectResult]:
        """Extract aspects and sentiment from text."""
        if not text:
            return []

        if self._extractor:
            return self._extract_pyabsa(text)
        elif self._fallback_nlp:
            return self._extract_spacy_fallback(text)
        return []

    def extract_batch(self, texts: list[str]) -> list[list[AspectResult]]:
        """Extract aspects from multiple texts."""
        return [self.extract(t) for t in texts]

    def _extract_pyabsa(self, text: str) -> list[AspectResult]:
        """Use PyABSA for aspect extraction."""
        try:
            result = self._extractor.predict(text, print_result=False)
        except Exception as e:
            log.warning("PyABSA extraction error: %s", e)
            return []

        aspects = result.get("aspect", [])
        sentiments = result.get("sentiment", [])
        confidences = result.get("confidence", [])

        results = []
        for i in range(len(aspects)):
            aspect = aspects[i] if i < len(aspects) else ""
            sentiment = sentiments[i] if i < len(sentiments) else "Neutral"
            confidence = float(confidences[i]) if i < len(confidences) else 0.0

            if aspect and confidence >= ProductIdeationConfig.MIN_ASPECT_CONFIDENCE:
                results.append(AspectResult(
                    aspect=aspect.lower().strip(),
                    sentiment=sentiment,
                    confidence=confidence,
                ))

        return results

    def _extract_spacy_fallback(self, text: str) -> list[AspectResult]:
        """Fallback: extract noun chunks as aspects, use basic sentiment heuristic."""
        doc = self._fallback_nlp(text[:10000])
        results = []

        positive_words = {"great", "excellent", "amazing", "love", "perfect", "best", "good", "fantastic", "awesome", "wonderful"}
        negative_words = {"bad", "terrible", "horrible", "worst", "poor", "awful", "hate", "broken", "useless", "disappointing"}

        for chunk in doc.noun_chunks:
            aspect = chunk.text.lower().strip()
            if len(aspect) < 3 or len(aspect) > 50:
                continue

            # Simple context-window sentiment
            start = max(0, chunk.start - 5)
            end = min(len(doc), chunk.end + 5)
            context_tokens = {t.text.lower() for t in doc[start:end]}

            pos_count = len(context_tokens & positive_words)
            neg_count = len(context_tokens & negative_words)

            if pos_count > neg_count:
                sentiment = "Positive"
                confidence = 0.6
            elif neg_count > pos_count:
                sentiment = "Negative"
                confidence = 0.6
            else:
                sentiment = "Neutral"
                confidence = 0.4

            results.append(AspectResult(
                aspect=aspect, sentiment=sentiment, confidence=confidence,
            ))

        return results

    def teardown(self):
        self._extractor = None
        self._fallback_nlp = None
