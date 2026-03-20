"""
Gap analysis aggregator.

Combines ABSA results, feature requests, and question clusters into
ranked product opportunity gaps.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict

from product_ideation.absa import AspectResult
from product_ideation.config import ProductIdeationConfig
from product_ideation.feature_requests import FeatureRequest
from product_ideation.questions import QuestionCluster
from schemas.product_gap import GapType, ProductGap

log = logging.getLogger("product_ideation.gap_analysis")


def compute_gaps(
    entity_id: str,
    absa_results: list[list[AspectResult]],
    feature_requests: list[FeatureRequest],
    question_clusters: list[QuestionCluster],
    doc_ids: list[str] | None = None,
) -> list[ProductGap]:
    """Compute product gaps from all three analysis components.

    Returns gaps sorted by opportunity_score (highest first).
    """
    gaps = []

    # 1. Aspects with consistently negative sentiment
    gaps.extend(_negative_aspect_gaps(entity_id, absa_results))

    # 2. Missing feature requests
    gaps.extend(_feature_request_gaps(entity_id, feature_requests))

    # 3. Recurring unanswered questions
    gaps.extend(_question_gaps(entity_id, question_clusters))

    # Attach doc IDs
    if doc_ids:
        for gap in gaps:
            gap.contributing_doc_ids = doc_ids[:20]  # Cap for size

    return sorted(gaps, key=lambda g: g.opportunity_score, reverse=True)


def _negative_aspect_gaps(
    entity_id: str,
    absa_results: list[list[AspectResult]],
) -> list[ProductGap]:
    """Find aspects with consistently negative sentiment."""
    # Aggregate sentiment per aspect
    aspect_data: dict[str, list[AspectResult]] = defaultdict(list)
    for review_aspects in absa_results:
        for ar in review_aspects:
            aspect_data[ar.aspect.lower()].append(ar)

    gaps = []
    for aspect, entries in aspect_data.items():
        total = len(entries)
        if total < ProductIdeationConfig.MIN_REVIEWS_FOR_GAP:
            continue

        neg_count = sum(1 for e in entries if e.sentiment == "Negative")
        neg_fraction = neg_count / total

        if neg_fraction >= ProductIdeationConfig.NEGATIVE_THRESHOLD:
            avg_conf = sum(e.confidence for e in entries if e.sentiment == "Negative") / max(neg_count, 1)

            # Opportunity score: higher when more reviews mention it negatively
            score = neg_fraction * avg_conf * min(total / 10, 1.0)

            gaps.append(ProductGap(
                entity_id=entity_id,
                aspect=aspect,
                gap_type=GapType.NEGATIVE_SENTIMENT,
                frequency=total,
                avg_sentiment=-(neg_fraction),
                opportunity_score=round(min(1.0, score), 4),
                example_quotes=[e.source_text for e in entries[:3] if hasattr(e, "source_text")],
            ))

    return gaps


def _feature_request_gaps(
    entity_id: str,
    feature_requests: list[FeatureRequest],
) -> list[ProductGap]:
    """Surface frequently requested missing features."""
    # Count similar requests (lowercase, first 60 chars)
    need_counts: Counter = Counter()
    need_examples: dict[str, list[str]] = defaultdict(list)

    for fr in feature_requests:
        key = fr.extracted_need.lower()[:60]
        need_counts[key] += 1
        if len(need_examples[key]) < 3:
            need_examples[key].append(fr.matched_text)

    gaps = []
    for need, count in need_counts.most_common(30):
        if count < ProductIdeationConfig.MIN_FEATURE_REQUESTS:
            continue

        score = min(count / 10, 1.0)

        gaps.append(ProductGap(
            entity_id=entity_id,
            aspect=need,
            gap_type=GapType.MISSING_FEATURE,
            frequency=count,
            avg_sentiment=-1.0,
            opportunity_score=round(score, 4),
            example_quotes=need_examples[need],
        ))

    return gaps


def _question_gaps(
    entity_id: str,
    question_clusters: list[QuestionCluster],
) -> list[ProductGap]:
    """Surface recurring unanswered questions as product gaps."""
    gaps = []

    for cluster in question_clusters:
        if cluster.frequency < ProductIdeationConfig.MIN_FEATURE_REQUESTS:
            continue

        score = min(cluster.frequency / 10, 1.0)

        gaps.append(ProductGap(
            entity_id=entity_id,
            aspect=cluster.representative,
            gap_type=GapType.UNANSWERED_QUESTION,
            frequency=cluster.frequency,
            avg_sentiment=0.0,
            opportunity_score=round(score, 4),
            example_quotes=cluster.questions[:3],
        ))

    return gaps
