"""
OSINT Pipeline — Unified Document Schema

Every connector normalizes its source data into this schema before
publishing to Kafka. Downstream consumers (NLP pipeline, cross-correlation
engine, storage layer) only need to understand this one format.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SourcePlatform(str, Enum):
    HACKER_NEWS = "hacker_news"
    GITHUB = "github"
    TRUSTPILOT = "trustpilot"
    AMAZON = "amazon"
    REDDIT = "reddit"
    TWITTER_X = "twitter_x"
    BLUESKY = "bluesky"
    SEC_EDGAR = "sec_edgar"
    OPENINSIDER = "openinsider"
    NEWS = "news"
    FRED = "fred"
    PRODUCTHUNT = "producthunt"
    BINANCE = "binance"
    ALPACA = "alpaca"
    STOCKTWITS = "stocktwits"


class ContentType(str, Enum):
    STORY = "story"           # HN story, Reddit post, tweet
    COMMENT = "comment"       # HN comment, Reddit comment, review reply
    REVIEW = "review"         # TrustPilot/Amazon review
    ISSUE = "issue"           # GitHub issue
    PULL_REQUEST = "pull_request"
    REPOSITORY = "repository" # GitHub repo metadata
    FILING = "filing"         # SEC filing
    ARTICLE = "article"       # News article
    METRIC = "metric"         # Economic/financial metric data point
    POST = "post"             # ProductHunt launch, generic post


class QualitySignals(BaseModel):
    """Community-validated quality metrics from the source platform."""

    score: Optional[float] = Field(
        None,
        description="Platform score: HN points, GitHub stars, upvote ratio"
    )
    engagement_count: Optional[int] = Field(
        None,
        description="Total engagement: HN descendants, comment count, review helpful votes"
    )
    is_verified: Optional[bool] = Field(
        None,
        description="Verified purchase (Amazon), verified reviewer (TrustPilot)"
    )
    account_age_days: Optional[int] = Field(
        None,
        description="Age of the authoring account in days — bot detection signal"
    )


class EntityMention(BaseModel):
    """A named entity extracted during light NER at ingestion time."""

    text: str
    entity_type: str = Field(
        ...,
        description="TICKER, COMPANY, PERSON, PRODUCT, TECHNOLOGY"
    )
    confidence: float = Field(ge=0.0, le=1.0)


class OsintDocument(BaseModel):
    """
    The universal document that flows through the entire pipeline.

    Design principles:
    - Every field that varies by source goes in `quality_signals` or `metadata`
    - `content_text` is always the primary text for NLP processing
    - `dedup_hash` enables near-duplicate detection across sources
    - No PII fields — author handles are pseudonymized at ingestion
    """

    # === Identity ===
    doc_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique document ID"
    )
    source: SourcePlatform
    source_id: str = Field(
        ...,
        description="Original ID from the source platform (e.g., HN item ID, GitHub issue number)"
    )
    content_type: ContentType

    # === Content ===
    title: Optional[str] = Field(None, max_length=1000)
    content_text: str = Field(
        ...,
        description="Primary text content — review body, comment text, story text, etc."
    )
    url: Optional[str] = Field(
        None,
        description="Source URL for the original content"
    )
    language: Optional[str] = Field(
        None,
        description="ISO 639-1 language code, detected by fastText lid.176"
    )

    # === Temporal ===
    created_at: datetime = Field(
        ...,
        description="When the content was created on the source platform (UTC)"
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When our pipeline ingested this document (UTC)"
    )

    # === Quality & Engagement ===
    quality_signals: QualitySignals = Field(default_factory=QualitySignals)

    # === Rating (reviews only) ===
    rating: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Normalized 0.0–1.0 rating (star ratings divided by max stars)"
    )

    # === Relationships ===
    parent_id: Optional[str] = Field(
        None,
        description="Source ID of the parent document (comment → story, reply → review)"
    )
    entity_id: Optional[str] = Field(
        None,
        description="Product ASIN, TrustPilot business ID, GitHub repo full_name, ticker symbol"
    )

    # === NER (populated during stream processing, empty at ingestion) ===
    entities: list[EntityMention] = Field(default_factory=list)

    # === Deduplication ===
    dedup_hash: Optional[str] = Field(
        None,
        description="SimHash or MinHash fingerprint for near-duplicate detection"
    )

    # === Extensible metadata ===
    metadata: dict = Field(
        default_factory=dict,
        description="Source-specific fields that don't fit the universal schema"
    )

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def compute_dedup_hash(self) -> str:
        """Simple content hash for exact-duplicate detection.
        Near-duplicate detection (SimHash/MinHash) runs in Flink.
        """
        text = (self.content_text or "") + (self.title or "")
        self.dedup_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        return self.dedup_hash

    def to_kafka_key(self) -> str:
        """Kafka partition key — ensures all docs for the same entity
        land on the same partition for ordered processing."""
        return self.entity_id or self.source_id

    def to_kafka_value(self) -> str:
        """Serialize to JSON for Kafka producer."""
        return self.model_dump_json()
