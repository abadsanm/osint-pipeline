"""
Product Gap Schema

Output of the Product Ideation Engine. Represents a product opportunity
identified from consumer feedback analysis.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GapType(str, Enum):
    NEGATIVE_SENTIMENT = "negative_sentiment"
    MISSING_FEATURE = "missing_feature"
    UNANSWERED_QUESTION = "unanswered_question"


class AspectSentiment(BaseModel):
    """Sentiment analysis for a specific product aspect."""
    aspect: str
    sentiment: str          # "Positive", "Negative", "Neutral"
    confidence: float = 0.0
    source_text: str = ""


class ProductGap(BaseModel):
    """A product opportunity identified from review analysis."""

    gap_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entity_id: str = Field(..., description="Product/business being analyzed")
    aspect: str = Field(..., description="Product aspect or feature request")
    gap_type: GapType
    frequency: int = Field(0, description="Number of reviews mentioning this")
    avg_sentiment: float = Field(0.0, description="-1 to +1")
    opportunity_score: float = Field(0.0, ge=0.0, le=1.0)

    example_quotes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    contributing_doc_ids: list[str] = Field(default_factory=list)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_kafka_key(self) -> str:
        return self.entity_id

    def to_kafka_value(self) -> str:
        return self.model_dump_json()


class ProductIdeationReport(BaseModel):
    """Aggregated ideation report for a product/business."""

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entity_id: str
    entity_name: Optional[str] = None
    total_reviews_analyzed: int = 0

    gaps: list[ProductGap] = Field(default_factory=list)
    top_positive_aspects: list[AspectSentiment] = Field(default_factory=list)
    top_negative_aspects: list[AspectSentiment] = Field(default_factory=list)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_kafka_key(self) -> str:
        return self.entity_id

    def to_kafka_value(self) -> str:
        return self.model_dump_json()
