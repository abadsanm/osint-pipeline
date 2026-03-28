"""
Cross-correlation engine.

Orchestrates entity ingestion, periodic window evaluation,
confidence scoring, anomaly detection, and signal emission.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from correlation.anomaly import detect_anomalies
from correlation.config import CorrelationConfig
from correlation.scoring import compute_confidence
from correlation.window_store import (
    EntityKey,
    EntityWindowStore,
    MentionEvent,
    WindowSlice,
)
from schemas.document import OsintDocument
from schemas.signal import (
    CorrelatedSignal,
    SignalType,
    SourceContribution,
)

log = logging.getLogger("correlation.engine")


class CrossCorrelationEngine:
    """Ingests normalized documents and emits correlated signals."""

    def __init__(self):
        self._store = EntityWindowStore()

    def ingest_document(self, doc: OsintDocument):
        """Extract entity mentions from a document and add to the window store."""
        # Use entity_id as primary entity if available
        if doc.entity_id:
            key = EntityKey(
                canonical=doc.entity_id.upper(),
                entity_type=_infer_entity_type(doc),
            )
            event = _doc_to_mention(doc, doc.entity_id)
            self._store.ingest(key, event)

        # Also index by extracted entities from NER
        for entity in doc.entities:
            canonical = entity.text.upper()
            key = EntityKey(canonical=canonical, entity_type=entity.entity_type)
            event = _doc_to_mention(doc, entity.text, entity.confidence)
            self._store.ingest(key, event)

    def evaluate_windows(self) -> list[CorrelatedSignal]:
        """Evaluate all entity windows and emit signals that meet thresholds.

        Should be called periodically (every SLIDE_MINUTES).
        """
        self._store.cleanup()
        signals = []
        now = datetime.now(timezone.utc)

        for key in self._store.get_all_entity_keys():
            window = self._store.get_window(key, window_end=now)

            # Bypass mention minimum for high-authority sources (weight >= 0.9)
            high_authority = any(
                CorrelationConfig.SOURCE_WEIGHTS.get(src, 0) >= 0.9
                for src in window.unique_sources
            )
            if window.total_mentions < CorrelationConfig.MIN_MENTIONS_TO_EMIT and not high_authority:
                continue

            signal = self._evaluate_single(key, window, now)
            if signal:
                signals.append(signal)

        if signals:
            log.info(
                "Evaluation: %d entities checked, %d signals emitted",
                self._store.entity_count, len(signals),
            )

        return signals

    def _evaluate_single(
        self,
        key: EntityKey,
        window: WindowSlice,
        now: datetime,
    ) -> CorrelatedSignal | None:
        """Evaluate a single entity window and produce a signal if warranted."""
        # Compute baseline velocity
        mean_v, stddev_v = self._store.get_baseline_velocity(key, now)

        # Compute confidence score
        confidence, zscore, vol_anomaly = compute_confidence(
            window, mean_v, stddev_v,
        )

        # Detect anomalies
        anomaly_flags = detect_anomalies(window)

        # Apply penalty if anomalies detected
        if anomaly_flags:
            confidence *= CorrelationConfig.ANOMALY_PENALTY

        # Determine signal type
        signal_type = _classify_signal(key, window, anomaly_flags, zscore)

        # Build source breakdown
        source_breakdown = _build_source_breakdown(window)

        signal = CorrelatedSignal(
            entity_text=key.canonical,
            entity_type=key.entity_type,
            entity_aliases=sorted(window.aliases),
            window_start=window.window_start,
            window_end=window.window_end,
            source_breakdown=source_breakdown,
            total_mentions=window.total_mentions,
            unique_sources=len(window.unique_sources),
            confidence_score=round(min(1.0, max(0.0, confidence)), 4),
            velocity=round(window.velocity, 2),
            velocity_zscore=round(zscore, 2),
            anomaly_flags=anomaly_flags,
            signal_type=signal_type,
            contributing_doc_ids=[m.doc_id for m in window.mentions],
        )

        return signal

    @property
    def stats(self) -> dict:
        return {
            "entities_tracked": self._store.entity_count,
            "total_events": self._store.total_events,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc_to_mention(
    doc: OsintDocument,
    entity_text: str,
    entity_confidence: float = 1.0,
) -> MentionEvent:
    """Convert an OsintDocument to a MentionEvent."""
    return MentionEvent(
        doc_id=doc.doc_id,
        source=doc.source.value,
        created_at=doc.created_at,
        ingested_at=doc.ingested_at,
        quality_score=doc.quality_signals.score,
        engagement_count=doc.quality_signals.engagement_count,
        account_age_days=doc.quality_signals.account_age_days,
        entity_confidence=entity_confidence,
        entity_text=entity_text,
        dedup_hash=doc.dedup_hash,
    )


def _infer_entity_type(doc: OsintDocument) -> str:
    """Infer entity type from document source and content type."""
    from schemas.document import ContentType

    if doc.content_type == ContentType.FILING:
        return "TICKER"
    if doc.content_type == ContentType.REVIEW:
        return "PRODUCT"
    if doc.content_type == ContentType.REPOSITORY:
        return "TECHNOLOGY"

    # Check entities for a dominant type
    if doc.entities:
        type_counts: dict[str, int] = {}
        for e in doc.entities:
            type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
        if type_counts:
            return max(type_counts, key=type_counts.get)

    return "COMPANY"


def _classify_signal(
    key: EntityKey,
    window: WindowSlice,
    anomaly_flags: list[str],
    zscore: float,
) -> SignalType:
    """Determine the signal type based on characteristics."""
    if anomaly_flags:
        return SignalType.COORDINATED_INAUTHENTICITY

    sources = window.unique_sources
    if "sec_edgar" in sources and key.entity_type in ("TICKER", "COMPANY"):
        return SignalType.INSIDER_ACTIVITY

    if len(sources) >= CorrelationConfig.MIN_SOURCES_FOR_STRONG:
        return SignalType.MULTI_SOURCE_CONVERGENCE

    if zscore > 3.0:
        return SignalType.VOLUME_SPIKE

    return SignalType.MULTI_SOURCE_CONVERGENCE


def _build_source_breakdown(window: WindowSlice) -> dict[str, SourceContribution]:
    """Build per-source contribution breakdown."""
    by_source: dict[str, list[MentionEvent]] = {}
    for m in window.mentions:
        by_source.setdefault(m.source, []).append(m)

    breakdown = {}
    for source, mentions in by_source.items():
        quality_scores = [m.quality_score for m in mentions if m.quality_score is not None]
        ages = [m.account_age_days for m in mentions if m.account_age_days is not None]

        breakdown[source] = SourceContribution(
            source=source,
            mention_count=len(mentions),
            avg_quality_score=round(sum(quality_scores) / len(quality_scores), 2) if quality_scores else 0.0,
            avg_account_age_days=round(sum(ages) / len(ages), 1) if ages else None,
            doc_ids=[m.doc_id for m in mentions],
        )

    return breakdown
