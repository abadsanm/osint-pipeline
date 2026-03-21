"""
Sentinel API — Real-time data server for the dashboard.

Reads from Kafka topics and serves live pipeline data via REST endpoints.
Maintains in-memory caches that are updated by background Kafka consumers.

Usage:
    python -m api.server
    # or: uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

# Unique instance ID — ensures each API restart re-reads Kafka from earliest
_INSTANCE_ID = uuid.uuid4().hex[:8]

from confluent_kafka import Consumer, KafkaError
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sentinel-api")

app = FastAPI(title="Sentinel API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# In-memory data stores (populated by background Kafka consumers)
# ---------------------------------------------------------------------------

MAX_ITEMS = 500

# Recent documents per source
recent_docs: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_ITEMS))

# Signals (from correlated topics)
signals: deque = deque(maxlen=MAX_ITEMS)

# Entity mention counts (for heat sphere)
entity_mentions: dict[str, dict] = {}

# Topic message counts
topic_counts: dict[str, int] = defaultdict(int)

# Pipeline stats
pipeline_stats = {
    "total_ingested": 0,
    "total_normalized": 0,
    "total_correlated": 0,
    "sources": defaultdict(int),
    "started_at": datetime.now(timezone.utc).isoformat(),
}

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Kafka background consumers
# ---------------------------------------------------------------------------

def _consume_raw_topics():
    """Background thread: consume from all raw topics."""
    raw_topics = [
        "tech.hn.stories", "tech.hn.comments",
        "tech.github.events",
        "consumer.reviews.trustpilot",
        "finance.sec.insider", "finance.congress.trades",
        "finance.reddit.posts", "finance.reddit.comments",
        "trends.google.interest",
    ]

    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": f"sentinel-api-raw-{_INSTANCE_ID}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe(raw_topics)
    log.info("Raw topic consumer started: %s", raw_topics)

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Raw consumer error: %s", msg.error())
            continue

        try:
            doc = json.loads(msg.value())
            topic = msg.topic()

            with _lock:
                recent_docs[topic].append(doc)
                topic_counts[topic] += 1
                pipeline_stats["total_ingested"] += 1

                source = doc.get("source", "unknown")
                pipeline_stats["sources"][source] += 1

                # Track entity mentions for heat sphere
                _track_entity_from_doc(doc)

        except Exception as e:
            log.warning("Failed to process raw message: %s", e)


def _track_entity_from_doc(doc: dict):
    """Track entity mentions from a document (called under _lock)."""
    source = doc.get("source", "unknown")
    title = doc.get("title", "")
    quality_score = (doc.get("quality_signals") or {}).get("score") or 0
    engagement = (doc.get("quality_signals") or {}).get("engagement_count") or 0

    entities_to_track = []
    for ent in doc.get("entities", []):
        ent_text = ent.get("text", "")
        # Skip short, numeric-only, or common noise entities
        if ent_text and len(ent_text) > 1 and not ent_text.isdigit():
            entities_to_track.append(ent_text)

    entity_id = doc.get("entity_id")
    # Skip numeric entity IDs (HN parent story IDs) — prefer named entities
    if entity_id and not entity_id.isdigit() and entity_id not in entities_to_track:
        entities_to_track.append(entity_id)

    for eid in entities_to_track:
        if eid not in entity_mentions:
            entity_mentions[eid] = {
                "id": eid,
                "label": eid,
                "volume": 0,
                "sentiment_sum": 0.0,
                "sentiment_count": 0,
                "sources": defaultdict(int),
                "keywords": [],
                "sample_docs": [],
                "last_seen": None,
            }
        em = entity_mentions[eid]
        em["volume"] += 1
        em["sources"][source] += 1
        em["last_seen"] = doc.get("ingested_at")

        # Compute sentiment from engagement signals
        # Higher engagement = more positive signal (0.3-0.9 range)
        if quality_score and quality_score > 0:
            import math
            # Log-scale: 1 point=0.3, 10=0.5, 100=0.7, 1000=0.9
            norm = 0.3 + 0.6 * min(1.0, math.log1p(quality_score) / math.log1p(1000))
            em["sentiment_sum"] += norm
            em["sentiment_count"] += 1
        elif engagement and engagement > 0:
            import math
            norm = 0.3 + 0.6 * min(1.0, math.log1p(engagement) / math.log1p(500))
            em["sentiment_sum"] += norm
            em["sentiment_count"] += 1

        if title and (em["label"] == eid or em["label"].isdigit()):
            em["label"] = title[:60]

        if title and len(em["keywords"]) < 8:
            words = [w for w in title.split() if len(w) > 3][:3]
            em["keywords"] = list(set(em["keywords"] + words))[:8]

        if len(em["sample_docs"]) < 5:
            em["sample_docs"].append({
                "title": title[:120] if title else doc.get("content_text", "")[:120],
                "source": source,
                "url": doc.get("url"),
                "created_at": doc.get("created_at"),
                "score": quality_score,
            })


def _consume_signals():
    """Background thread: consume from correlated + alpha + ideation topics."""
    signal_topics = [
        "osint.normalized",
        "osint.correlated",
        "osint.correlated.stock_alpha",
        "osint.correlated.product_ideation",
        "osint.correlated.anomalies",
        "stock.alpha.signals",
        "product.ideation.gaps",
    ]

    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": f"sentinel-api-signals-{_INSTANCE_ID}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe(signal_topics)
    log.info("Signal topic consumer started: %s", signal_topics)

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Signal consumer error: %s", msg.error())
            continue

        try:
            data = json.loads(msg.value())
            topic = msg.topic()

            with _lock:
                topic_counts[topic] += 1

                if topic == "osint.normalized":
                    pipeline_stats["total_normalized"] += 1
                    # Track entities from normalized docs (they have NER entities)
                    _track_entity_from_doc(data)

                elif topic.startswith("osint.correlated"):
                    pipeline_stats["total_correlated"] += 1

                    # Add to signal feed
                    signal_entry = {
                        "id": data.get("signal_id", ""),
                        "type": _classify_signal_type(data),
                        "ticker": data.get("entity_text", ""),
                        "headline": _build_headline(data),
                        "source": ", ".join(data.get("source_breakdown", {}).keys()),
                        "timestamp": data.get("created_at", datetime.now(timezone.utc).isoformat()),
                        "confidence": data.get("confidence_score", 0),
                    }
                    # Skip signals with numeric-only entity names
                    if signal_entry["ticker"].isdigit():
                        continue

                    # Deduplicate by entity+headline
                    dedup_key = f"{signal_entry['ticker']}:{signal_entry['headline']}"
                    existing_keys = {f"{s['ticker']}:{s['headline']}" for s in signals}
                    if dedup_key not in existing_keys:
                        signals.append(signal_entry)

        except Exception as e:
            log.warning("Failed to process signal message: %s", e)


def _classify_signal_type(data: dict) -> str:
    anomalies = data.get("anomaly_flags", [])
    if anomalies:
        return "bearish"
    score = data.get("confidence_score", 0.5)
    if score > 0.7:
        return "bullish"
    if score < 0.3:
        return "bearish"
    return "volume"


def _build_headline(data: dict) -> str:
    entity = data.get("entity_text", "")
    mentions = data.get("total_mentions", 0)
    sources = data.get("unique_sources", 0)
    signal_type = data.get("signal_type", "")
    anomalies = data.get("anomaly_flags", [])

    if anomalies:
        return f"Anomaly detected: {', '.join(anomalies)}"
    if signal_type == "insider_activity":
        return f"Insider activity detected across {sources} sources."
    if signal_type == "volume_spike":
        return f"Volume spike: {mentions} mentions across {sources} platforms."
    return f"Multi-source signal: {mentions} mentions across {sources} sources."


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/api/stats")
def stats():
    with _lock:
        return {
            **pipeline_stats,
            "sources": dict(pipeline_stats["sources"]),
            "topic_counts": dict(topic_counts),
            "entities_tracked": len(entity_mentions),
        }


@app.get("/api/signals")
def get_signals(limit: int = Query(20, ge=1, le=100)):
    """Recent correlated signals for the signal feed."""
    with _lock:
        items = list(signals)[-limit:]
    items.reverse()
    return items


@app.get("/api/sectors")
def get_sectors(limit: int = Query(30, ge=1, le=100)):
    """Entity mentions aggregated as sectors for the heat sphere."""
    with _lock:
        entities = sorted(
            entity_mentions.values(),
            key=lambda e: e["volume"],
            reverse=True,
        )[:limit]

    result = []
    for e in entities:
        # Compute actual sentiment from accumulated scores
        if e.get("sentiment_count", 0) > 0:
            sentiment = e["sentiment_sum"] / e["sentiment_count"]
        else:
            sentiment = 0.5

        # Build source list
        sources_dict = dict(e["sources"]) if isinstance(e["sources"], dict) else {}
        source_list = sorted(sources_dict.keys())

        result.append({
            "id": e["id"],
            "label": e["label"],
            "sector": ", ".join(source_list) if source_list else "Unknown",
            "sentiment": round(sentiment, 3),
            "volume": e["volume"],
            "priceChange24h": round((sentiment - 0.5) * 10, 1),  # Derive from sentiment
            "keywords": e["keywords"][:5],
            "sources": sources_dict,
            "sampleDocs": e.get("sample_docs", []),
            "uniqueSources": len(source_list),
        })

    return result


@app.get("/api/recent/{topic}")
def get_recent(topic: str, limit: int = Query(20, ge=1, le=100)):
    """Recent documents from a specific topic."""
    # Map friendly names to topic names
    topic_map = {
        "hn": "tech.hn.stories",
        "github": "tech.github.events",
        "reddit": "finance.reddit.posts",
        "sec": "finance.sec.insider",
        "trustpilot": "consumer.reviews.trustpilot",
        "trends": "trends.google.interest",
    }
    actual_topic = topic_map.get(topic, topic)

    with _lock:
        items = list(recent_docs.get(actual_topic, []))[-limit:]
    items.reverse()
    return items


@app.get("/api/topics")
def get_topics():
    """Message counts per topic."""
    with _lock:
        return dict(topic_counts)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    # Start background Kafka consumers
    t1 = threading.Thread(target=_consume_raw_topics, daemon=True)
    t1.start()
    t2 = threading.Thread(target=_consume_signals, daemon=True)
    t2.start()
    log.info("Sentinel API started — Kafka consumers running")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=False)
