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
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

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
        "group.id": "sentinel-api-raw",
        "auto.offset.reset": "latest",
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
                entity_id = doc.get("entity_id")
                if entity_id:
                    if entity_id not in entity_mentions:
                        entity_mentions[entity_id] = {
                            "id": entity_id,
                            "label": entity_id,
                            "volume": 0,
                            "sentiment": 0.5,
                            "sources": defaultdict(int),
                            "keywords": [],
                            "last_seen": None,
                        }
                    em = entity_mentions[entity_id]
                    em["volume"] += 1
                    em["sources"][source] += 1
                    em["last_seen"] = doc.get("ingested_at")

                    # Extract keywords from title
                    title = doc.get("title", "")
                    if title and len(em["keywords"]) < 5:
                        words = [w for w in title.split()[:3] if len(w) > 3]
                        em["keywords"] = list(set(em["keywords"] + words))[:5]

        except Exception as e:
            log.warning("Failed to process raw message: %s", e)


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
        "group.id": "sentinel-api-signals",
        "auto.offset.reset": "latest",
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
        total_sources = sum(e["sources"].values()) if isinstance(e["sources"], dict) else 0
        result.append({
            "id": e["id"],
            "label": e["label"],
            "sector": "Multi-source",
            "sentiment": e.get("sentiment", 0.5),
            "volume": e["volume"],
            "priceChange24h": 0,
            "keywords": e["keywords"][:3],
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
