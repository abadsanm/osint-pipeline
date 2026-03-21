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
from pathlib import Path

# Load .env file if it exists
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip()
                if value and key not in os.environ:
                    os.environ[key] = value

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
# Helpers
# ---------------------------------------------------------------------------

def _parse_since(since: Optional[str]) -> Optional[str]:
    """Convert a timeframe preset or ISO string to an ISO cutoff timestamp."""
    if not since:
        return None

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    presets = {
        "1D": timedelta(days=1),
        "5D": timedelta(days=5),
        "1M": timedelta(days=30),
        "YTD": now - datetime(now.year, 1, 1, tzinfo=timezone.utc),
        "1Y": timedelta(days=365),
        "5Y": timedelta(days=365 * 5),
    }

    if since.upper() in presets:
        delta = presets[since.upper()]
        if isinstance(delta, timedelta):
            cutoff = now - delta
        else:
            cutoff = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        return cutoff.isoformat()

    # Assume ISO string
    return since


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
def get_signals(
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = Query(None, description="ISO timestamp or preset: 1D,5D,1M,YTD,1Y,5Y"),
):
    """Recent correlated signals for the signal feed."""
    cutoff = _parse_since(since)
    with _lock:
        if cutoff:
            items = [s for s in signals if s.get("timestamp", "") >= cutoff][-limit:]
        else:
            items = list(signals)[-limit:]
    items.reverse()
    return items


@app.get("/api/sectors")
def get_sectors(
    limit: int = Query(30, ge=1, le=100),
    since: Optional[str] = Query(None, description="ISO timestamp or preset: 1D,5D,1M,YTD,1Y,5Y"),
):
    """Entity mentions aggregated as sectors for the heat sphere."""
    # Note: entity_mentions is cumulative; since filtering applies to last_seen
    cutoff = _parse_since(since)
    with _lock:
        if cutoff:
            filtered = {k: v for k, v in entity_mentions.items()
                        if v.get("last_seen") and v["last_seen"] >= cutoff}
        else:
            filtered = entity_mentions
        entities = sorted(
            filtered.values(),
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


@app.get("/api/search")
def search_entities(
    q: str = Query("", min_length=1, max_length=200, description="Search query"),
):
    """Search entity_mentions by id, label, or keywords. Returns top 10 matches."""
    query = q.lower().strip()
    if not query:
        return []

    results = []
    with _lock:
        for eid, em in entity_mentions.items():
            # Score based on match quality
            score = 0
            eid_lower = eid.lower()
            label_lower = (em.get("label") or "").lower()
            keywords_lower = [k.lower() for k in em.get("keywords", [])]

            if query == eid_lower or query == label_lower:
                score = 100  # Exact match
            elif query in eid_lower:
                score = 80
            elif query in label_lower:
                score = 60
            elif any(query in kw for kw in keywords_lower):
                score = 40
            else:
                # Check if any query word matches
                query_words = query.split()
                for word in query_words:
                    if word in eid_lower or word in label_lower:
                        score = max(score, 30)
                    elif any(word in kw for kw in keywords_lower):
                        score = max(score, 20)

            if score > 0:
                sentiment = 0.5
                if em.get("sentiment_count", 0) > 0:
                    sentiment = em["sentiment_sum"] / em["sentiment_count"]

                results.append({
                    "id": em["id"],
                    "label": em.get("label", eid),
                    "volume": em.get("volume", 0),
                    "sentiment": round(sentiment, 3),
                    "_score": score,
                    "_volume": em.get("volume", 0),
                })

    # Sort by match quality, then by volume
    results.sort(key=lambda r: (-r["_score"], -r["_volume"]))
    # Remove internal scoring fields and limit to 10
    for r in results:
        del r["_score"]
        del r["_volume"]

    return results[:10]


# ---------------------------------------------------------------------------
# AI Analysis Endpoint
# ---------------------------------------------------------------------------

@app.post("/api/analyze")
async def analyze_entity(request: dict):
    """Generate AI-powered analysis for an entity using Claude."""
    entity_id = request.get("entity_id", "")
    entity_label = request.get("label", entity_id)
    sentiment = request.get("sentiment", 0.5)
    volume = request.get("volume", 0)
    sources = request.get("sources", {})
    keywords = request.get("keywords", [])
    sample_docs = request.get("sampleDocs", [])
    signal_type = request.get("signal_type", "")
    confidence = request.get("confidence", 0)
    context_type = request.get("context_type", "entity")  # "entity", "signal", "sector"

    # Build context for Claude
    source_list = ", ".join(f"{k} ({v} mentions)" for k, v in sources.items()) if sources else "unknown"
    sample_texts = "\n".join(
        f"- [{d.get('source', '?')}] {d.get('title', d.get('headline', ''))}"
        for d in (sample_docs or [])[:5]
    )

    sentiment_label = "bullish" if sentiment > 0.6 else "bearish" if sentiment < 0.4 else "neutral"

    if context_type == "signal":
        prompt = f"""You are a senior OSINT financial analyst. Analyze this correlated signal:

Entity: {entity_label}
Signal Type: {signal_type}
Confidence: {confidence:.0%}
Sentiment: {sentiment_label} ({sentiment:.0%})
Volume: {volume} mentions
Sources: {source_list}

Sample content:
{sample_texts}

Provide a concise analysis (3-4 paragraphs) covering:
1. **Why this signal matters** — what is driving this entity's appearance in the pipeline and why it's being flagged
2. **Sentiment drivers** — what specific factors are pushing the sentiment in this direction based on the source content
3. **Cross-source validation** — how strong is this signal given the sources it comes from (SEC filings are strongest, Reddit weakest)
4. **Actionable recommendations** — specific next steps for both stock trading and product strategy perspectives

Be direct, data-driven, and specific. Use the sample content to ground your analysis."""
    else:
        prompt = f"""You are a senior OSINT financial analyst. Analyze this entity trending in our pipeline:

Entity: {entity_label} (ID: {entity_id})
Sentiment: {sentiment_label} ({sentiment:.0%})
Mention Volume: {volume}
Sources: {source_list}
Keywords: {', '.join(keywords) if keywords else 'none extracted'}

Sample content from pipeline:
{sample_texts}

Provide a concise analysis (3-4 paragraphs) covering:
1. **Why this entity is trending** — what is driving the mention volume and why the pipeline is capturing it
2. **Sentiment analysis** — what's pushing the sentiment {sentiment_label} and whether this is likely to continue
3. **Relevance assessment** — is this a meaningful market signal, noise, or potential coordinated activity
4. **Actionable recommendations**:
   - For **Stock Alpha**: any trading implications, risk signals, or opportunities
   - For **Product Ideation**: any consumer insights, product gaps, or innovation opportunities
   - Suggested next steps for deeper investigation

Be direct and specific. Reference the actual source content where possible."""

    try:
        import anthropic
        client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        analysis_text = message.content[0].text

        return {
            "entity_id": entity_id,
            "label": entity_label,
            "analysis": analysis_text,
            "model": "claude-sonnet-4-20250514",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except ImportError:
        return {
            "entity_id": entity_id,
            "label": entity_label,
            "analysis": _generate_fallback_analysis(
                entity_label, sentiment, sentiment_label, volume, source_list, keywords, sample_texts, context_type
            ),
            "model": "rule-based-fallback",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.warning("AI analysis failed: %s", e)
        return {
            "entity_id": entity_id,
            "label": entity_label,
            "analysis": _generate_fallback_analysis(
                entity_label, sentiment, sentiment_label, volume, source_list, keywords, sample_texts, context_type
            ),
            "model": "rule-based-fallback",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def _generate_fallback_analysis(
    label, sentiment, sentiment_label, volume, source_list, keywords, sample_texts, context_type
):
    """Generate a rule-based analysis when Claude is unavailable."""
    strength = "strong" if volume > 50 else "moderate" if volume > 10 else "weak"
    kw_text = ", ".join(keywords[:3]) if keywords else "general discussion"

    return f"""## Analysis: {label}

**Signal Strength:** {strength.title()} ({volume} mentions)
**Sentiment:** {sentiment_label.title()} ({sentiment:.0%})
**Sources:** {source_list}

### Why This Is Trending
{label} has accumulated {volume} mentions across {source_list}. The primary discussion themes include {kw_text}. This represents a {strength} signal in our pipeline.

### Sentiment Assessment
Current sentiment is {sentiment_label} at {sentiment:.0%}. {"This suggests positive market perception and potential upward momentum." if sentiment > 0.6 else "This suggests negative market perception — monitor for potential downside risk." if sentiment < 0.4 else "Sentiment is neutral — the market is undecided on direction."}

### Recommendations
- **Stock Alpha:** {"Consider monitoring for entry points if supported by technical indicators." if sentiment > 0.6 else "Exercise caution — negative sentiment may precede price decline." if sentiment < 0.4 else "Wait for sentiment to establish a clear direction before acting."}
- **Product Ideation:** Review the source content for consumer pain points and feature requests related to {label}.
- **Next Steps:** Cross-reference with SEC filings and earnings data for stronger signal validation.

*Note: AI-powered analysis unavailable. Set ANTHROPIC_API_KEY for Claude-generated insights.*"""


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
