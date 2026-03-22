"""
Prediction Schema — Output of the calibrated prediction engine.

Each Prediction represents a directional call on a ticker with calibrated
confidence, time horizon, and decay characteristics. PredictionBatch groups
predictions across multiple time horizons for a single scoring event.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Prediction(BaseModel):
    prediction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str
    direction: Literal["bullish", "bearish", "neutral"]
    magnitude: float = Field(0.0, description="Expected % move")
    confidence: float = Field(ge=0.0, le=1.0, description="Calibrated probability")
    raw_score: float = Field(description="Original -1 to +1 alpha score pre-calibration")
    time_horizon_hours: int = Field(description="When to evaluate: 1, 4, 24, 72, 168")
    decay_rate: float = Field(description="Confidence half-life in hours")
    regime: str = Field(default="unknown", description="Market regime at prediction time")
    contributing_signals: list[dict] = Field(default_factory=list)
    model_version: str = Field(default="v1.0")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Filled after expiry by tracker
    outcome: Optional[Literal["correct", "incorrect", "expired"]] = None
    actual_return: Optional[float] = None
    evaluated_at: Optional[datetime] = None

    @field_validator("created_at", "expires_at", "evaluated_at", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        if v is None:
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        if isinstance(v, str):
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v

    def to_kafka_key(self) -> str:
        return self.ticker

    def to_kafka_value(self) -> str:
        return self.model_dump_json()

    def current_confidence(self) -> float:
        """Apply exponential decay: confidence * exp(-elapsed_hours * ln2 / decay_rate)"""
        elapsed = (datetime.now(timezone.utc) - self.created_at).total_seconds() / 3600
        if self.decay_rate <= 0:
            return self.confidence
        return self.confidence * math.exp(-elapsed * math.log(2) / self.decay_rate)

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at


class PredictionBatch(BaseModel):
    ticker: str
    predictions: list[Prediction] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("generated_at", mode="before")
    @classmethod
    def ensure_utc_batch(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def to_kafka_key(self) -> str:
        return self.ticker

    def to_kafka_value(self) -> str:
        return self.model_dump_json()
