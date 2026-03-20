"""
In-memory sliding window store for entity mention tracking.

Maintains a time-sorted deque of MentionEvents per entity, supports
window slicing and retention cleanup.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from correlation.config import CorrelationConfig


@dataclass
class MentionEvent:
    """A single entity mention from a normalized document."""
    doc_id: str
    source: str
    created_at: datetime
    ingested_at: datetime
    quality_score: Optional[float] = None
    engagement_count: Optional[int] = None
    account_age_days: Optional[int] = None
    entity_confidence: float = 1.0
    entity_text: str = ""
    dedup_hash: Optional[str] = None


@dataclass
class EntityKey:
    """Hashable key for an entity in the store."""
    canonical: str
    entity_type: str

    def __hash__(self):
        return hash((self.canonical, self.entity_type))

    def __eq__(self, other):
        return (self.canonical, self.entity_type) == (other.canonical, other.entity_type)


@dataclass
class WindowSlice:
    """A slice of mentions within a specific time window."""
    entity_key: EntityKey
    mentions: list[MentionEvent]
    window_start: datetime
    window_end: datetime
    aliases: set[str] = field(default_factory=set)

    @property
    def total_mentions(self) -> int:
        return len(self.mentions)

    @property
    def unique_sources(self) -> set[str]:
        return {m.source for m in self.mentions}

    @property
    def velocity(self) -> float:
        """Mentions per hour."""
        duration_hours = (self.window_end - self.window_start).total_seconds() / 3600
        if duration_hours <= 0:
            return float(self.total_mentions)
        return self.total_mentions / duration_hours


class EntityWindowStore:
    """Manages sliding windows of entity mentions.

    Each entity gets a deque of MentionEvents sorted by time.
    Supports efficient window slicing and retention cleanup.
    """

    def __init__(self):
        self._store: dict[EntityKey, deque[MentionEvent]] = {}
        self._aliases: dict[EntityKey, set[str]] = {}

    def ingest(self, key: EntityKey, event: MentionEvent):
        """Add a mention event for an entity."""
        if key not in self._store:
            self._store[key] = deque()
            self._aliases[key] = set()

        self._store[key].append(event)
        if event.entity_text:
            self._aliases[key].add(event.entity_text)

    def get_window(
        self,
        key: EntityKey,
        window_end: Optional[datetime] = None,
        window_minutes: Optional[int] = None,
    ) -> WindowSlice:
        """Get mentions within a time window for an entity."""
        if window_end is None:
            window_end = datetime.now(timezone.utc)
        if window_minutes is None:
            window_minutes = CorrelationConfig.WINDOW_SIZE_MINUTES

        window_start = window_end - timedelta(minutes=window_minutes)

        mentions = []
        if key in self._store:
            for event in self._store[key]:
                if window_start <= event.created_at <= window_end:
                    mentions.append(event)

        return WindowSlice(
            entity_key=key,
            mentions=mentions,
            window_start=window_start,
            window_end=window_end,
            aliases=self._aliases.get(key, set()).copy(),
        )

    def get_all_entity_keys(self) -> list[EntityKey]:
        """Return all entity keys currently tracked."""
        return list(self._store.keys())

    def get_baseline_velocity(
        self,
        key: EntityKey,
        current_window_end: Optional[datetime] = None,
        baseline_hours: Optional[int] = None,
        window_minutes: Optional[int] = None,
    ) -> tuple[float, float]:
        """Compute mean and stddev of hourly velocity over the baseline period.

        Returns (mean_velocity, stddev_velocity).
        Used for z-score computation.
        """
        if current_window_end is None:
            current_window_end = datetime.now(timezone.utc)
        if baseline_hours is None:
            baseline_hours = CorrelationConfig.WINDOW_RETENTION_HOURS
        if window_minutes is None:
            window_minutes = CorrelationConfig.WINDOW_SIZE_MINUTES

        baseline_start = current_window_end - timedelta(hours=baseline_hours)

        # Divide baseline into non-overlapping windows
        velocities = []
        t = baseline_start
        while t + timedelta(minutes=window_minutes) <= current_window_end:
            w = self.get_window(key, window_end=t + timedelta(minutes=window_minutes), window_minutes=window_minutes)
            velocities.append(w.velocity)
            t += timedelta(minutes=window_minutes)

        if len(velocities) < 2:
            return 0.0, 1.0  # Not enough data for meaningful baseline

        mean_v = sum(velocities) / len(velocities)
        variance = sum((v - mean_v) ** 2 for v in velocities) / len(velocities)
        stddev_v = math.sqrt(variance) if variance > 0 else 1.0

        return mean_v, stddev_v

    def cleanup(self, retention_hours: Optional[int] = None):
        """Remove mentions older than the retention period."""
        if retention_hours is None:
            retention_hours = CorrelationConfig.WINDOW_RETENTION_HOURS

        cutoff = datetime.now(timezone.utc) - timedelta(hours=retention_hours)
        empty_keys = []

        for key, events in self._store.items():
            while events and events[0].created_at < cutoff:
                events.popleft()
            if not events:
                empty_keys.append(key)

        for key in empty_keys:
            del self._store[key]
            self._aliases.pop(key, None)

    @property
    def entity_count(self) -> int:
        return len(self._store)

    @property
    def total_events(self) -> int:
        return sum(len(d) for d in self._store.values())
