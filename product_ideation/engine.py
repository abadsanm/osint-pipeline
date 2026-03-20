"""
Product Ideation Engine orchestrator.

Consumes CorrelatedSignals from osint.correlated.product_ideation,
accumulates review content per entity, and periodically runs ABSA,
feature request extraction, question clustering, and gap analysis.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from product_ideation.absa import ABSAExtractor
from product_ideation.config import ProductIdeationConfig
from product_ideation.feature_requests import extract_feature_requests
from product_ideation.gap_analysis import compute_gaps
from product_ideation.questions import QuestionClusterer, extract_questions
from schemas.product_gap import ProductGap, ProductIdeationReport
from schemas.signal import CorrelatedSignal

log = logging.getLogger("product_ideation.engine")


@dataclass
class ReviewEntry:
    """A review text accumulated for analysis."""
    doc_id: str
    content_text: str
    source: str
    created_at: datetime


class ProductIdeationEngine:
    """Processes correlated product signals into ideation reports."""

    def __init__(self):
        self._absa = ABSAExtractor()
        self._clusterer = QuestionClusterer()
        # Accumulated reviews per entity_id
        self._reviews: dict[str, deque[ReviewEntry]] = defaultdict(
            lambda: deque(maxlen=ProductIdeationConfig.MAX_REVIEWS_IN_MEMORY)
        )

    def setup(self):
        """Load models."""
        self._absa.setup()
        self._clusterer.setup()
        log.info("Product Ideation Engine initialized")

    def ingest_signal(self, signal: CorrelatedSignal):
        """Accumulate review content from a correlated signal."""
        entity_id = signal.entity_text

        # Create a review entry from the signal metadata
        self._reviews[entity_id].append(ReviewEntry(
            doc_id=signal.signal_id,
            content_text=f"{signal.entity_text}: {signal.total_mentions} mentions across {signal.unique_sources} sources",
            source=",".join(signal.source_breakdown.keys()),
            created_at=signal.created_at,
        ))

    def ingest_review_text(self, entity_id: str, doc_id: str, text: str, source: str):
        """Directly ingest review text (from normalized docs if available)."""
        self._reviews[entity_id].append(ReviewEntry(
            doc_id=doc_id,
            content_text=text,
            source=source,
            created_at=datetime.now(timezone.utc),
        ))

    def evaluate(self) -> list[ProductIdeationReport]:
        """Run analysis on all accumulated entities and produce reports."""
        reports = []

        for entity_id, reviews in self._reviews.items():
            if len(reviews) < ProductIdeationConfig.MIN_REVIEWS_FOR_GAP:
                continue

            report = self._analyze_entity(entity_id, list(reviews))
            if report and report.gaps:
                reports.append(report)

        if reports:
            log.info("Evaluation produced %d reports across %d entities",
                     len(reports), len(self._reviews))

        return reports

    def _analyze_entity(
        self, entity_id: str, reviews: list[ReviewEntry],
    ) -> Optional[ProductIdeationReport]:
        """Run full analysis pipeline on reviews for a single entity."""
        texts = [r.content_text for r in reviews]
        doc_ids = [r.doc_id for r in reviews]

        # 1. ABSA: extract aspects and sentiment
        absa_results = self._absa.extract_batch(texts)

        # 2. Feature request extraction
        all_requests = []
        for text in texts:
            all_requests.extend(extract_feature_requests(text))

        # 3. Question extraction and clustering
        all_questions = []
        for text in texts:
            all_questions.extend(extract_questions(text))
        question_clusters = self._clusterer.cluster(all_questions)

        # 4. Gap analysis
        gaps = compute_gaps(
            entity_id=entity_id,
            absa_results=absa_results,
            feature_requests=all_requests,
            question_clusters=question_clusters,
            doc_ids=doc_ids,
        )

        # Build top positive/negative aspect lists
        top_positive = []
        top_negative = []
        from schemas.product_gap import AspectSentiment

        for review_aspects in absa_results:
            for ar in review_aspects:
                entry = AspectSentiment(
                    aspect=ar.aspect,
                    sentiment=ar.sentiment,
                    confidence=ar.confidence,
                )
                if ar.sentiment == "Positive":
                    top_positive.append(entry)
                elif ar.sentiment == "Negative":
                    top_negative.append(entry)

        # Deduplicate and sort by frequency
        top_positive = _deduplicate_aspects(top_positive)[:10]
        top_negative = _deduplicate_aspects(top_negative)[:10]

        return ProductIdeationReport(
            entity_id=entity_id,
            total_reviews_analyzed=len(reviews),
            gaps=gaps[:20],  # Top 20 gaps
            top_positive_aspects=top_positive,
            top_negative_aspects=top_negative,
        )

    def teardown(self):
        self._absa.teardown()
        self._clusterer.teardown()
        self._reviews.clear()

    @property
    def stats(self) -> dict:
        return {
            "entities_tracked": len(self._reviews),
            "total_reviews": sum(len(r) for r in self._reviews.values()),
        }


def _deduplicate_aspects(aspects: list) -> list:
    """Deduplicate aspects by name, keeping highest confidence."""
    seen: dict[str, object] = {}
    for a in aspects:
        key = a.aspect.lower()
        if key not in seen or a.confidence > seen[key].confidence:
            seen[key] = a
    return sorted(seen.values(), key=lambda x: x.confidence, reverse=True)
