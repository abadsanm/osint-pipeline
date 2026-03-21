"""
OSINT Pipeline — Market Data Schemas

Pydantic v2 models for real-time market data flowing through Kafka:
order book snapshots, aggregated trades, and OHLCV bars.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class OrderBookSnapshot(BaseModel):
    """Top-of-book snapshot with up to 20 bid/ask price levels."""

    symbol: str
    timestamp: datetime = Field(
        ...,
        description="Snapshot capture time (UTC)",
    )
    bids: list[list[float]] = Field(
        ...,
        description="Top 20 bid levels, each [price, qty]",
    )
    asks: list[list[float]] = Field(
        ...,
        description="Top 20 ask levels, each [price, qty]",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def to_kafka_key(self) -> str:
        """Partition key — all snapshots for the same symbol on one partition."""
        return self.symbol

    def to_kafka_value(self) -> str:
        """Serialize to JSON for Kafka producer."""
        return self.model_dump_json()


class AggregatedTrade(BaseModel):
    """A single aggregated trade event."""

    symbol: str
    timestamp: datetime = Field(
        ...,
        description="Trade execution time (UTC)",
    )
    price: float
    quantity: float
    is_buyer_maker: bool = Field(
        ...,
        description="True = seller-initiated (buyer is maker), False = buyer-initiated",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def to_kafka_key(self) -> str:
        """Partition key — all trades for the same symbol on one partition."""
        return self.symbol

    def to_kafka_value(self) -> str:
        """Serialize to JSON for Kafka producer."""
        return self.model_dump_json()


class MarketBar(BaseModel):
    """OHLCV bar (candlestick) for a given time interval."""

    symbol: str
    timestamp: datetime = Field(
        ...,
        description="Bar open time (UTC)",
    )
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = Field(
        None,
        description="Volume-weighted average price for the bar",
    )
    trade_count: Optional[int] = Field(
        None,
        description="Number of trades in the bar interval",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def ensure_utc(cls, v):
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def to_kafka_key(self) -> str:
        """Partition key — all bars for the same symbol on one partition."""
        return self.symbol

    def to_kafka_value(self) -> str:
        """Serialize to JSON for Kafka producer."""
        return self.model_dump_json()
