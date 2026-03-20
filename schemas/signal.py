"""
Correlated Signal Schema

Output of the cross-correlation engine. Represents a multi-source signal
about an entity detected within a time window, with confidence scoring
and anomaly flags.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SignalType(str, Enum):
    MULTI_SOURCE_CONVERGENCE = "multi_source_convergence"
    VOLUME_SPIKE = "volume_spike"
    INSIDER_ACTIVITY = "insider_activity"
    COORDINATED_INAUTHENTICITY = "coordinated_inauthenticity"


class SourceContribution(BaseModel):
    """Per-source breakdown within a correlated signal."""
    source: str
    mention_count: int = 0
    avg_quality_score: float = 0.0
    avg_account_age_days: Optional[float] = None
    doc_ids: list[str] = Field(default_factory=list)


class CorrelatedSignal(BaseModel):
    """
    A cross-correlated signal emitted when an entity is mentioned
    across multiple sources within a time window.
    """

    # Identity
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    entity_text: str = Field(..., description="Canonical entity name")
    entity_type: str = Field(..., description="TICKER, COMPANY, PERSON, PRODUCT, etc.")
    entity_aliases: list[str] = Field(default_factory=list)

    # Time window
    window_start: datetime
    window_end: datetime

    # Source breakdown
    source_breakdown: dict[str, SourceContribution] = Field(default_factory=dict)
    total_mentions: int = 0
    unique_sources: int = 0

    # Scoring
    confidence_score: float = Field(
        0.0, ge=0.0, le=1.0,
        description="Weighted multi-source confidence (0.0-1.0)",
    )
    velocity: float = Field(
        0.0, description="Mentions per hour within the window",
    )
    velocity_zscore: float = Field(
        0.0, description="Std deviations above baseline velocity",
    )

    # Anomaly detection
    anomaly_flags: list[str] = Field(default_factory=list)
    signal_type: SignalType = SignalType.MULTI_SOURCE_CONVERGENCE

    # Provenance
    contributing_doc_ids: list[str] = Field(default_factory=list)
    routed_to: list[str] = Field(default_factory=list)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def to_kafka_key(self) -> str:
        return self.entity_text

    def to_kafka_value(self) -> str:
        return self.model_dump_json()
