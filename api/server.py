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
import re
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
from fastapi import FastAPI, HTTPException, Query
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

# Alerts (evaluated from incoming signals)
alerts: deque = deque(maxlen=200)

# Alert dedup tracking: {(alert_type, ticker): timestamp} — prevent same alert within 30 min
_alert_dedup: dict[tuple[str, str], float] = {}
_ALERT_DEDUP_WINDOW = 30 * 60  # 30 minutes in seconds

# Predictions tracking
predictions: deque = deque(maxlen=1000)
prediction_outcomes: deque = deque(maxlen=1000)
prediction_stats = {
    "total_predictions": 0,
    "total_evaluated": 0,
    "correct": 0,
    "by_regime": defaultdict(lambda: {"total": 0, "correct": 0}),
    "by_horizon": defaultdict(lambda: {"total": 0, "correct": 0}),
    "by_direction": defaultdict(lambda: {"total": 0, "correct": 0}),
    "calibration_bins": defaultdict(lambda: {"predicted_sum": 0.0, "actual_correct": 0, "count": 0}),
    "recent_accuracy_window": deque(maxlen=200),
    "model_agreement": {"4_agree": 0, "3_agree": 0, "2_agree": 0, "total": 0},
}

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

# Signal backtester (accuracy tracking)
from stock_alpha.backtester import SignalBacktester
_backtester = SignalBacktester()

# ---------------------------------------------------------------------------
# SQLite persistence layer
# ---------------------------------------------------------------------------

from api.persistence import SentinelDB

db = SentinelDB()  # data/sentinel.db — auto-creates data/ dir


def _restore_state_from_db():
    """Populate in-memory caches from the persisted DB on startup."""
    global pipeline_stats

    # --- Restore pipeline_stats and topic_counts ---
    saved_stats, saved_tc = db.load_stats()
    if saved_stats:
        for k, v in saved_stats.items():
            if k == "sources":
                # Merge into the defaultdict
                if isinstance(v, dict):
                    for sk, sv in v.items():
                        pipeline_stats["sources"][sk] = sv
            elif k in pipeline_stats:
                pipeline_stats[k] = v
        # Keep the current started_at (this is a fresh run)
        pipeline_stats["started_at"] = datetime.now(timezone.utc).isoformat()

    if saved_tc:
        for k, v in saved_tc.items():
            topic_counts[k] = v

    # --- Restore entity_mentions ---
    db_entities = db.get_entities(limit=5000)
    for ent in db_entities:
        eid = ent["id"]
        # Convert sources dict back to defaultdict(int)
        src = defaultdict(int)
        for sk, sv in ent.get("sources", {}).items():
            src[sk] = sv
        ent["sources"] = src
        entity_mentions[eid] = ent

    # --- Restore signals ---
    db_signals = db.get_signals(limit=MAX_ITEMS)
    db_signals.reverse()  # oldest first so deque ordering is correct
    for sig in db_signals:
        signals.append(sig)

    # --- Restore alerts ---
    db_alerts = db.get_alerts(limit=200)
    db_alerts.reverse()  # oldest first
    for alert in db_alerts:
        alerts.append(alert)

    # --- Restore backtest records ---
    db_records = db.get_backtest_records(limit=1000)
    for rec in db_records:
        sid = rec.get("signal_id", "")
        if sid and sid not in _backtester._records:
            from stock_alpha.backtester import SignalRecord
            ts = rec.get("timestamp", "")
            if isinstance(ts, str) and ts:
                try:
                    timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    timestamp = datetime.now(timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)
            _backtester._records[sid] = SignalRecord(
                signal_id=sid,
                ticker=rec.get("ticker", ""),
                direction=rec.get("direction", "neutral"),
                signal_score=rec.get("signal_score", 0.0),
                confidence=rec.get("confidence", 0.0),
                price_at_signal=rec.get("price_at_signal", 0.0),
                timestamp=timestamp,
                price_1d=rec.get("price_1d"),
                price_5d=rec.get("price_5d"),
                price_10d=rec.get("price_10d"),
                correct_1d=rec.get("correct_1d"),
                correct_5d=rec.get("correct_5d"),
                correct_10d=rec.get("correct_10d"),
            )

    log.info(
        "State restored from DB: %d entities, %d signals, %d alerts, %d backtest records",
        len(entity_mentions), len(signals), len(alerts), len(_backtester._records),
    )


# Periodic stats saver timestamp
_last_stats_save: float = 0.0
_STATS_SAVE_INTERVAL = 60.0  # seconds


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
        "finance.news.articles", "finance.options.flow",
        "tech.techcrunch.articles", "finance.seeking_alpha.articles",
        "finance.finviz.news", "tech.techmeme.articles", "tech.newsletter.articles",
        "gov.sam.opportunities",
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

                # Periodic stats save
                _maybe_save_stats()

        except Exception as e:
            log.warning("Failed to process raw message: %s", e)


def _track_entity_from_doc(doc: dict):
    """Track entity mentions from a document (called under _lock)."""
    source = doc.get("source", "unknown")
    title = doc.get("title", "")
    content = doc.get("content_text", "")
    quality_score = (doc.get("quality_signals") or {}).get("score") or 0
    engagement = (doc.get("quality_signals") or {}).get("engagement_count") or 0

    entities_to_track = []
    entity_types = {}  # Track entity_type for clean labelling
    for ent in doc.get("entities", []):
        ent_text = ent.get("text", "")
        ent_type = ent.get("entity_type", "")
        # Skip short, numeric-only, or common noise entities
        if ent_text and len(ent_text) > 1 and not ent_text.isdigit():
            entities_to_track.append(ent_text)
            entity_types[ent_text] = ent_type

    entity_id = doc.get("entity_id")
    # Skip numeric entity IDs (HN parent story IDs) — prefer named entities
    if entity_id and not entity_id.isdigit() and entity_id not in entities_to_track:
        entities_to_track.append(entity_id)

    # Noise filter — reject entities that aren't useful for financial/product analysis
    ENTITY_BLOCKLIST = {
        # Religious/cultural terms
        "allahu akbar", "inshallah", "mashallah", "alhamdulillah",
        # Common noise words that slip through NER
        "show hn", "ask hn", "tell hn", "launch hn",
        "http", "https", "www", "com", "org",
        "the", "this", "that", "what", "which",
        # OS/distro names that match financial keywords
        "fedora", "fedora linux", "fedora asahiremix", "fedora and",
        # Legislative noise
        "bill c-22", "the lawful", "thelawfulaccessact",
        # Generic terms
        "update", "version", "release", "download", "install",
        "comment", "post", "thread", "reply", "deleted",
    }

    for eid in entities_to_track:
        # Skip entities in the blocklist
        if eid.lower().strip() in ENTITY_BLOCKLIST:
            continue
        # Skip entities shorter than 2 chars or longer than 50 chars
        if len(eid.strip()) < 2 or len(eid.strip()) > 50:
            continue
        if eid not in entity_mentions:
            entity_mentions[eid] = {
                "id": eid,
                "label": eid,  # Label stays as entity name, never overwritten by title
                "volume": 0,
                "sentiment_sum": 0.0,
                "sentiment_count": 0,
                "sources": defaultdict(int),
                "keywords": [],
                "sample_docs": [],
                "last_seen": None,
                "entity_type": entity_types.get(eid, ""),
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

        # Extract keywords from titles (never use title as entity label)
        if title and len(em["keywords"]) < 8:
            words = [w for w in title.split() if len(w) > 3][:3]
            em["keywords"] = list(set(em["keywords"] + words))[:8]

        # Collect sample documents (up to 10 for richer context)
        if len(em["sample_docs"]) < 10:
            em["sample_docs"].append({
                "title": title[:120] if title else "",
                "content": content[:300] if content else "",
                "source": source,
                "url": doc.get("url"),
                "created_at": doc.get("created_at"),
                "score": quality_score,
                "headline": (title or content)[:150],
            })

        # Persist to SQLite
        try:
            db.upsert_entity(eid, em)
        except Exception as e:
            log.debug("DB upsert_entity failed for %s: %s", eid, e)


def _maybe_save_stats():
    """Save pipeline_stats and topic_counts to DB every _STATS_SAVE_INTERVAL seconds."""
    global _last_stats_save
    now = time.time()
    if now - _last_stats_save < _STATS_SAVE_INTERVAL:
        return
    _last_stats_save = now
    try:
        db.save_stats(pipeline_stats, topic_counts)
    except Exception as e:
        log.debug("DB save_stats failed: %s", e)


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
                    source_breakdown = data.get("source_breakdown", {})
                    entity_text = data.get("entity_text", "")

                    signal_entry = {
                        "id": data.get("signal_id", ""),
                        "type": _classify_signal_type(data),
                        "ticker": entity_text,
                        "headline": _build_headline(data),
                        "source": ", ".join(source_breakdown.keys()),
                        "timestamp": data.get("created_at", datetime.now(timezone.utc).isoformat()),
                        "confidence": data.get("confidence_score", 0),
                        # Pass through for AI analysis context
                        "sources": {
                            src: info.get("mention_count", info) if isinstance(info, dict) else info
                            for src, info in source_breakdown.items()
                        },
                        "volume": data.get("total_mentions", 0),
                        "signal_type": data.get("signal_type", ""),
                        "keywords": data.get("entity_aliases", [])[:5],
                    }
                    # Skip signals with numeric-only entity names
                    if signal_entry["ticker"].isdigit():
                        continue

                    # Enrich with sampleDocs from entity_mentions if available
                    em_data = entity_mentions.get(entity_text) or entity_mentions.get(entity_text.upper())
                    if em_data:
                        signal_entry["sampleDocs"] = em_data.get("sample_docs", [])[:5]
                    else:
                        signal_entry["sampleDocs"] = []

                    # Deduplicate by entity+headline
                    dedup_key = f"{signal_entry['ticker']}:{signal_entry['headline']}"
                    existing_keys = {f"{s['ticker']}:{s['headline']}" for s in signals}
                    if dedup_key not in existing_keys:
                        signals.append(signal_entry)
                        _evaluate_alerts(signal_entry)

                        # Persist signal to SQLite
                        try:
                            db.add_signal(signal_entry)
                        except Exception as e:
                            log.debug("DB add_signal failed: %s", e)

                elif topic == "stock.alpha.signals":
                    # Record alpha signals for backtesting
                    _record_alpha_for_backtest(data)

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


def _evaluate_alerts(signal_entry: dict):
    """Evaluate a signal and fire alerts if conditions are met. Called under _lock."""
    now = time.time()
    ticker = signal_entry.get("ticker", "")
    confidence = signal_entry.get("confidence", 0)
    volume = signal_entry.get("volume", 0)
    sources = signal_entry.get("sources", {})
    headline = signal_entry.get("headline", "")
    sig_type = signal_entry.get("type", "")
    timestamp = signal_entry.get("timestamp", datetime.now(timezone.utc).isoformat())

    # Lower thresholds for watched entities (research queue items)
    try:
        watched = db.get_watched_entities() if db else set()
    except Exception:
        watched = set()
    is_watched = ticker.upper() in watched

    if is_watched:
        # Always alert for watched entities — any new signal is noteworthy
        dedup_key = ("research_update", ticker)
        last_fired = _alert_dedup.get(dedup_key, 0)
        if now - last_fired >= _ALERT_DEDUP_WINDOW:
            _alert_dedup[dedup_key] = now
            alert_entry = {
                "id": uuid.uuid4().hex[:12],
                "type": "research_update",
                "priority": "high",
                "ticker": ticker,
                "headline": headline,
                "message": f"New signal for watched entity: {headline}",
                "timestamp": timestamp,
                "read": False,
            }
            alerts.append(alert_entry)
            try:
                db.add_alert(alert_entry)
            except Exception as e:
                log.debug("DB add_alert (research_update) failed: %s", e)

    # Update watched items' current_mention_count when new signals come in
    if is_watched and db:
        try:
            for item in db.get_research_items(status="active"):
                if item["entity_id"].upper() == ticker.upper():
                    db.update_research_item(item["id"], {
                        "current_mention_count": item.get("current_mention_count", 0) + 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
        except Exception as e:
            log.debug("DB update research item mention count failed: %s", e)

    # Collect all alerts that should fire for this signal
    pending: list[tuple[str, str, str]] = []  # (alert_type, priority, message)

    # 1. High Confidence Signal
    if confidence > 0.75:
        pending.append((
            "high_confidence",
            "high",
            f"High-confidence signal ({confidence:.0%}) detected for {ticker}.",
        ))

    # 2. Volume Spike
    if volume > 20:
        pending.append((
            "volume_spike",
            "medium",
            f"Volume spike: {volume} mentions for {ticker}.",
        ))

    # 3. Multi-Source Convergence
    num_sources = len(sources) if isinstance(sources, dict) else 0
    if num_sources >= 3:
        source_names = ", ".join(sources.keys()) if isinstance(sources, dict) else ""
        pending.append((
            "convergence",
            "high",
            f"{ticker} detected across {num_sources} sources: {source_names}.",
        ))

    # 4. Anomaly Detection (bearish + moderate confidence)
    if sig_type == "bearish" and confidence > 0.6:
        pending.append((
            "anomaly",
            "medium",
            f"Bearish anomaly for {ticker} (confidence {confidence:.0%}).",
        ))

    # 5. Insider Activity
    if "insider" in headline.lower():
        pending.append((
            "insider",
            "high",
            f"Insider activity detected for {ticker}.",
        ))

    # Deduplicate and append
    for alert_type, priority, message in pending:
        dedup_key = (alert_type, ticker)
        last_fired = _alert_dedup.get(dedup_key, 0)
        if now - last_fired < _ALERT_DEDUP_WINDOW:
            continue  # Skip — same alert type + ticker fired recently

        _alert_dedup[dedup_key] = now
        alert_entry = {
            "id": uuid.uuid4().hex[:12],
            "type": alert_type,
            "priority": priority,
            "ticker": ticker,
            "headline": headline,
            "message": message,
            "timestamp": timestamp,
            "read": False,
        }
        alerts.append(alert_entry)

        # Persist alert to SQLite
        try:
            db.add_alert(alert_entry)
        except Exception as e:
            log.debug("DB add_alert failed: %s", e)

    # Prune old dedup entries (older than 2x window) to prevent unbounded growth
    cutoff = now - _ALERT_DEDUP_WINDOW * 2
    stale_keys = [k for k, v in _alert_dedup.items() if v < cutoff]
    for k in stale_keys:
        del _alert_dedup[k]


def _record_alpha_for_backtest(data: dict):
    """Record an alpha signal for backtesting. Called from _consume_signals."""
    try:
        ticker = data.get("ticker", "")
        direction = data.get("signal_direction", "neutral")
        signal_score = data.get("signal_score", 0.0)
        confidence = data.get("confidence", 0.0)
        price = data.get("price")
        ts = data.get("timestamp", "")
        signal_id = data.get("signal_id") or f"{ticker}_{ts}"

        if not ticker or price is None:
            # Try to fetch the current price if not in the message
            if ticker and price is None:
                try:
                    df = _backtester._price_provider.get_prices(ticker)
                    if df is not None and not df.empty:
                        close_col = "close" if "close" in df.columns else "Close"
                        if close_col in df.columns:
                            price = float(df[close_col].iloc[-1])
                except Exception:
                    pass
            if not ticker or price is None:
                return

        # Parse timestamp
        if isinstance(ts, str) and ts:
            try:
                timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = datetime.now(timezone.utc)

        _backtester.record_signal(
            signal_id=signal_id,
            ticker=ticker,
            direction=direction,
            signal_score=signal_score,
            confidence=confidence,
            price_at_signal=price,
            timestamp=timestamp,
        )

        # Persist backtest record to SQLite
        try:
            db.add_backtest_record({
                "signal_id": signal_id,
                "ticker": ticker,
                "direction": direction,
                "signal_score": signal_score,
                "confidence": confidence,
                "price_at_signal": price,
                "timestamp": timestamp,
            })
        except Exception as e2:
            log.debug("DB add_backtest_record failed: %s", e2)
    except Exception as e:
        log.warning("Failed to record alpha signal for backtest: %s", e)


def _consume_predictions():
    """Background thread: consume predictions and outcomes for tracking."""
    pred_topics = [
        "stock.alpha.predictions",
        "stock.alpha.outcomes",
    ]

    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": f"sentinel-api-predictions-{_INSTANCE_ID}",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe(pred_topics)
    log.info("Prediction topic consumer started: %s", pred_topics)

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Prediction consumer error: %s", msg.error())
            continue

        try:
            data = json.loads(msg.value())
            topic = msg.topic()

            with _lock:
                topic_counts[topic] += 1

                if topic == "stock.alpha.predictions":
                    predictions.append(data)
                    prediction_stats["total_predictions"] += 1

                    # Track model agreement from contributing_signals
                    contrib = data.get("contributing_signals", [])
                    if contrib:
                        directions = [s.get("signal_type") for s in contrib if s.get("signal_type")]
                        # Count how many models agree on the majority direction
                        if directions:
                            from collections import Counter as _Counter
                            most_common_dir, most_common_count = _Counter(directions).most_common(1)[0]
                            prediction_stats["model_agreement"]["total"] += 1
                            if most_common_count >= 4:
                                prediction_stats["model_agreement"]["4_agree"] += 1
                            elif most_common_count >= 3:
                                prediction_stats["model_agreement"]["3_agree"] += 1
                            elif most_common_count >= 2:
                                prediction_stats["model_agreement"]["2_agree"] += 1

                elif topic == "stock.alpha.outcomes":
                    prediction_outcomes.append(data)
                    prediction_stats["total_evaluated"] += 1

                    is_correct = data.get("outcome") == "correct"
                    if is_correct:
                        prediction_stats["correct"] += 1

                    # Update by_regime
                    regime = data.get("regime", "unknown")
                    prediction_stats["by_regime"][regime]["total"] += 1
                    if is_correct:
                        prediction_stats["by_regime"][regime]["correct"] += 1

                    # Update by_horizon
                    horizon = str(data.get("time_horizon_hours", "unknown"))
                    prediction_stats["by_horizon"][horizon]["total"] += 1
                    if is_correct:
                        prediction_stats["by_horizon"][horizon]["correct"] += 1

                    # Update by_direction
                    direction = data.get("direction", "unknown")
                    prediction_stats["by_direction"][direction]["total"] += 1
                    if is_correct:
                        prediction_stats["by_direction"][direction]["correct"] += 1

                    # Update calibration bins (10 bins: 0-10%, 10-20%, ... 90-100%)
                    conf = data.get("confidence", 0.5)
                    bin_idx = min(int(conf * 10), 9)  # 0-9
                    bin_key = f"{bin_idx * 10}-{(bin_idx + 1) * 10}%"
                    prediction_stats["calibration_bins"][bin_key]["predicted_sum"] += conf
                    prediction_stats["calibration_bins"][bin_key]["count"] += 1
                    if is_correct:
                        prediction_stats["calibration_bins"][bin_key]["actual_correct"] += 1

                    # Update recent accuracy window
                    prediction_stats["recent_accuracy_window"].append(1 if is_correct else 0)

        except Exception as e:
            log.warning("Failed to process prediction message: %s", e)


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


def _compute_timeframe_volume(em: dict, cutoff: Optional[str]) -> tuple[int, float, int]:
    """Compute volume, sentiment_sum, sentiment_count for an entity within a timeframe.

    When *cutoff* is provided, only sample_docs whose created_at >= cutoff are counted.
    Returns (volume, sentiment_sum, sentiment_count).
    Falls back to the cumulative values when there is no cutoff or no sample_docs.
    """
    if not cutoff:
        return em.get("volume", 0), em.get("sentiment_sum", 0.0), em.get("sentiment_count", 0)

    docs = em.get("sample_docs", [])
    if not docs:
        # No sample_docs to filter — fall back to cumulative values only if last_seen is recent
        if em.get("last_seen") and em["last_seen"] >= cutoff:
            return em.get("volume", 0), em.get("sentiment_sum", 0.0), em.get("sentiment_count", 0)
        return 0, 0.0, 0

    import math

    volume = 0
    sentiment_sum = 0.0
    sentiment_count = 0
    for doc in docs:
        doc_ts = doc.get("created_at") or ""
        if doc_ts >= cutoff:
            volume += 1
            score = doc.get("score", 0)
            if score and score > 0:
                norm = 0.3 + 0.6 * min(1.0, math.log1p(score) / math.log1p(1000))
                sentiment_sum += norm
                sentiment_count += 1

    # Scale volume: sample_docs is capped at 10, so estimate real volume proportionally
    total_docs = len(docs)
    total_volume = em.get("volume", 0)
    if total_docs > 0 and volume > 0:
        # Proportional estimate: (matching docs / total docs) * total cumulative volume
        volume = max(volume, round(total_volume * volume / total_docs))

    return volume, sentiment_sum, sentiment_count


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


@app.get("/api/alerts")
def get_alerts(
    limit: int = Query(50, ge=1, le=200),
    unread_only: bool = Query(False, description="Return only unread alerts"),
):
    """Recent alerts, newest first."""
    with _lock:
        if unread_only:
            items = [a for a in alerts if not a.get("read", False)]
        else:
            items = list(alerts)
    items = items[-limit:]
    items.reverse()
    return items


@app.post("/api/alerts/{alert_id}/read")
def mark_alert_read(alert_id: str):
    """Mark a single alert as read."""
    with _lock:
        for a in alerts:
            if a["id"] == alert_id:
                a["read"] = True
                db.mark_alert_read(alert_id)
                return {"ok": True, "id": alert_id}
    return {"ok": False, "error": "alert not found"}


@app.post("/api/alerts/read-all")
def mark_all_alerts_read():
    """Mark all alerts as read."""
    with _lock:
        count = 0
        for a in alerts:
            if not a.get("read", False):
                a["read"] = True
                count += 1
    db.mark_all_alerts_read()
    return {"ok": True, "marked": count}


# ---------------------------------------------------------------------------
# Signal Backtesting / Accuracy Tracking
# ---------------------------------------------------------------------------

@app.get("/api/backtest")
def get_backtest_stats(
    cleanup_days: Optional[int] = Query(None, ge=1, le=365, description="Remove signals older than N days"),
):
    """Signal accuracy stats. Evaluates pending signals lazily on each request."""
    # Optionally clean up old signals
    if cleanup_days is not None:
        _backtester.cleanup(max_age_days=cleanup_days)

    # Evaluate any signals that have matured past their horizon
    newly_evaluated = _backtester.evaluate_pending()

    stats = _backtester.get_accuracy_stats()
    stats["newly_evaluated"] = newly_evaluated
    return stats


# ---------------------------------------------------------------------------
# ML Prediction Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/ml/status")
def ml_status():
    """Status of the ML prediction model."""
    try:
        from stock_alpha.ml_scorer import MLScorer
        from stock_alpha.feature_store import FeatureStore

        scorer = MLScorer()
        store = FeatureStore()
        store_stats = store.get_stats()

        return {
            "model_trained": scorer.is_trained,
            "training_stats": scorer.training_stats if scorer.is_trained else None,
            "feature_importance": scorer.get_feature_importance()[:10] if scorer.is_trained else [],
            "feature_store": store_stats,
        }
    except Exception as e:
        return {"model_trained": False, "error": str(e), "feature_store": {}}


@app.post("/api/ml/train")
def ml_train():
    """Train the ML model on available feature store data."""
    try:
        from stock_alpha.ml_scorer import MLScorer
        from stock_alpha.feature_store import FeatureStore

        store = FeatureStore()

        # First label any unlabeled snapshots
        labeled = store.label_with_returns()
        log.info("Labeled %d snapshots with forward returns", labeled)

        stats = store.get_stats()
        if stats.get("labeled", 0) < 50:
            return {
                "status": "insufficient_data",
                "message": f"Need at least 50 labeled samples, have {stats.get('labeled', 0)}. Keep the pipeline running to accumulate data.",
                "feature_store": stats,
            }

        scorer = MLScorer()
        training_result = scorer.train(store)

        return {
            "status": "trained",
            "stats": training_result,
            "feature_store": stats,
        }
    except Exception as e:
        log.error("ML training failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


@app.get("/api/ml/predict/{ticker}")
def ml_predict(ticker: str):
    """Get ML prediction for a ticker using current features."""
    ticker = ticker.upper().strip()
    try:
        from stock_alpha.ml_scorer import MLScorer
        from stock_alpha.feature_store import FeatureStore

        scorer = MLScorer()
        if not scorer.is_trained:
            return {"ticker": ticker, "prediction": None, "reason": "Model not trained yet"}

        store = FeatureStore()
        # Get latest snapshot for this ticker
        data = store.get_training_data(ticker=ticker)
        if not data:
            return {"ticker": ticker, "prediction": None, "reason": "No feature data for this ticker"}

        latest = data[-1]
        prediction = scorer.predict(latest)

        return {
            "ticker": ticker,
            "prediction": prediction,
            "snapshot_time": latest.get("timestamp"),
            "features_used": len(scorer._feature_names),
        }
    except Exception as e:
        return {"ticker": ticker, "prediction": None, "reason": str(e)}


@app.post("/api/ml/retrain-check")
def ml_retrain_check():
    """Check if model needs retraining (staleness + drift)."""
    try:
        from stock_alpha.ml_scorer import MLScorer
        from stock_alpha.feature_store import FeatureStore

        scorer = MLScorer()
        store = FeatureStore()

        stale = False
        retrained = False
        drift = {}

        # Check staleness and retrain if needed
        retrain_result = scorer.retrain_if_stale(store)
        if retrain_result is not None:
            stale = True
            retrained = True

        # Check drift
        if scorer.is_trained:
            drift = scorer.detect_drift(store)

        return {
            "stale": stale,
            "drift": drift,
            "retrained": retrained,
            "model_trained": scorer.is_trained,
        }
    except Exception as e:
        log.error("ML retrain-check failed: %s", e, exc_info=True)
        return {"stale": False, "drift": {}, "retrained": False, "error": str(e)}


@app.get("/api/ml/feature-importance")
def ml_feature_importance():
    """Get feature importance from the trained model."""
    try:
        from stock_alpha.ml_scorer import MLScorer

        scorer = MLScorer()
        if not scorer.is_trained:
            return {"features": [], "reason": "Model not trained yet"}

        importance = scorer.get_feature_importance()[:20]
        return {"features": importance}
    except Exception as e:
        return {"features": [], "reason": str(e)}


# ---------------------------------------------------------------------------
# Prediction & Backtesting Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/predictions")
def get_predictions(
    ticker: Optional[str] = Query(None, description="Filter by ticker symbol"),
    direction: Optional[str] = Query(None, description="Filter by direction: bullish, bearish, neutral"),
    limit: int = Query(50, ge=1, le=200),
):
    """Recent predictions, newest first. Optional ticker and direction filters."""
    with _lock:
        items = list(predictions)
    # Apply filters
    if ticker:
        ticker_upper = ticker.upper().strip()
        items = [p for p in items if (p.get("ticker") or "").upper() == ticker_upper]
    if direction:
        direction_lower = direction.lower().strip()
        items = [p for p in items if (p.get("direction") or "").lower() == direction_lower]
    items = items[-limit:]
    items.reverse()
    return items


@app.get("/api/predictions/stats")
def get_prediction_stats():
    """Full prediction statistics: accuracy, rolling accuracy, regime/horizon/direction breakdowns,
    calibration curve, and model agreement percentages."""
    with _lock:
        total_eval = prediction_stats["total_evaluated"]
        correct = prediction_stats["correct"]
        overall_accuracy = (correct / total_eval) if total_eval > 0 else 0.0

        # Rolling accuracy from recent window
        window = list(prediction_stats["recent_accuracy_window"])
        rolling_accuracy = (sum(window) / len(window)) if window else 0.0

        # by_regime with accuracy computed
        by_regime = {}
        for regime, data in prediction_stats["by_regime"].items():
            t = data["total"]
            c = data["correct"]
            by_regime[regime] = {"total": t, "correct": c, "accuracy": round(c / t, 4) if t > 0 else 0.0}

        # by_horizon with accuracy computed
        by_horizon = {}
        for horizon, data in prediction_stats["by_horizon"].items():
            t = data["total"]
            c = data["correct"]
            by_horizon[horizon] = {"total": t, "correct": c, "accuracy": round(c / t, 4) if t > 0 else 0.0}

        # by_direction with accuracy computed
        by_direction = {}
        for direction, data in prediction_stats["by_direction"].items():
            t = data["total"]
            c = data["correct"]
            by_direction[direction] = {"total": t, "correct": c, "accuracy": round(c / t, 4) if t > 0 else 0.0}

        # Calibration curve (10 bins)
        calibration_curve = []
        for i in range(10):
            bin_key = f"{i * 10}-{(i + 1) * 10}%"
            bin_data = prediction_stats["calibration_bins"].get(bin_key)
            if bin_data and bin_data["count"] > 0:
                calibration_curve.append({
                    "bin": bin_key,
                    "bin_center": round((i * 10 + (i + 1) * 10) / 200, 2),
                    "avg_predicted_confidence": round(bin_data["predicted_sum"] / bin_data["count"], 3),
                    "actual_accuracy": round(bin_data["actual_correct"] / bin_data["count"], 3),
                    "count": bin_data["count"],
                })
            else:
                calibration_curve.append({
                    "bin": bin_key,
                    "bin_center": round((i * 10 + (i + 1) * 10) / 200, 2),
                    "avg_predicted_confidence": 0.0,
                    "actual_accuracy": 0.0,
                    "count": 0,
                })

        # Model agreement percentages
        ma = prediction_stats["model_agreement"]
        ma_total = ma["total"] if ma["total"] > 0 else 1
        model_agreement = {
            "4_agree_pct": round(ma["4_agree"] / ma_total * 100, 1),
            "3_agree_pct": round(ma["3_agree"] / ma_total * 100, 1),
            "2_agree_pct": round(ma["2_agree"] / ma_total * 100, 1),
            "total": ma["total"],
        }

    return {
        "total_predictions": prediction_stats["total_predictions"],
        "total_evaluated": total_eval,
        "correct": correct,
        "overall_accuracy": round(overall_accuracy, 4),
        "rolling_accuracy_200": round(rolling_accuracy, 4),
        "by_regime": by_regime,
        "by_horizon": by_horizon,
        "by_direction": by_direction,
        "calibration_curve": calibration_curve,
        "model_agreement": model_agreement,
    }


@app.get("/api/predictions/outcomes")
def get_prediction_outcomes(
    limit: int = Query(50, ge=1, le=200),
):
    """Recent evaluated prediction outcomes, newest first."""
    with _lock:
        items = list(prediction_outcomes)[-limit:]
    items.reverse()
    return items


@app.get("/api/backtesting/results")
def get_backtesting_results():
    """Load the most recent backtesting results JSON from backtesting/results/ directory."""
    results_dir = Path(__file__).resolve().parent.parent / "backtesting" / "results"
    if not results_dir.exists():
        return {"status": "no_results", "message": "No backtesting results directory found."}

    json_files = sorted(results_dir.glob("*.json"))
    if not json_files:
        return {"status": "no_results", "message": "No backtesting result files found."}

    latest = json_files[-1]
    try:
        with open(latest) as f:
            data = json.load(f)
        return {"status": "ok", "file": latest.name, "results": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Settings Endpoints
# ---------------------------------------------------------------------------

_DEFAULT_SETTINGS = {
    "watchlist": ["AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "META", "AMZN", "SPY", "QQQ"],
    "alert_thresholds": {
        "high_confidence": 0.75,
        "volume_spike": 20,
        "convergence_sources": 3,
    },
    "refresh_intervals": {
        "signals": 15,
        "alpha": 30,
        "innovation": 15,
    },
    "connectors": {
        "hacker_news": True,
        "github": True,
        "reddit": True,
        "sec_edgar": True,
        "google_trends": True,
        "financial_news": True,
        "fred": True,
        "producthunt": True,
        "openinsider": True,
        "binance": True,
        "alpaca": False,
    },
    "display": {
        "theme": "dark",
        "chart_style": "line",
        "show_source_ticker": True,
        "default_timeframe": "1D",
    },
}

_SETTINGS_FILE = Path(__file__).resolve().parent.parent / "data" / "settings.json"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _load_settings() -> dict:
    """Load settings from disk, merged over defaults."""
    settings = json.loads(json.dumps(_DEFAULT_SETTINGS))  # deep copy
    if _SETTINGS_FILE.exists():
        try:
            with open(_SETTINGS_FILE) as f:
                saved = json.load(f)
            settings = _deep_merge(settings, saved)
        except Exception as exc:
            log.warning("Failed to load settings from %s: %s", _SETTINGS_FILE, exc)
    return settings


def _save_settings(settings: dict) -> None:
    """Persist settings to disk."""
    _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


_settings = _load_settings()


@app.get("/api/settings")
def get_settings():
    """Return all current settings."""
    return _settings


@app.put("/api/settings")
def update_settings(body: dict):
    """Deep-merge incoming settings with current settings and persist."""
    global _settings
    _settings = _deep_merge(_settings, body)
    _save_settings(_settings)
    return _settings


@app.get("/api/settings/{key}")
def get_setting(key: str):
    """Return a single top-level setting value."""
    if key not in _settings:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return {key: _settings[key]}


# ---------------------------------------------------------------------------
# Research Queue endpoints
# ---------------------------------------------------------------------------


@app.get("/api/research")
def get_research_items(status: Optional[str] = Query(None), limit: int = Query(50)):
    """Get research items, optionally filtered by status."""
    items = db.get_research_items(status=status, limit=limit)
    return {"items": items, "count": len(items)}


@app.post("/api/research")
def create_research_item(request: dict):
    """Create a new research item from AI analysis.

    Body: {entity_id, entity_label, action, source_context, priority, sentiment, mention_count}
    Auto-generates id and timestamps.
    """
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "id": uuid.uuid4().hex[:12],
        "entity_id": request.get("entity_id", ""),
        "entity_label": request.get("entity_label", ""),
        "action": request.get("action", ""),
        "source_context": request.get("source_context", ""),
        "priority": request.get("priority", "medium"),
        "status": "active",
        "notes": request.get("notes", ""),
        "created_at": now,
        "updated_at": now,
        "last_analysis": None,
        "alert_count": 0,
        "sentiment_at_creation": request.get("sentiment"),
        "current_sentiment": request.get("sentiment"),
        "mention_count_at_creation": request.get("mention_count", 0),
        "current_mention_count": request.get("mention_count", 0),
    }
    db.add_research_item(item)
    return item


@app.put("/api/research/{item_id}")
def update_research_item(item_id: str, request: dict):
    """Update a research item (notes, status, priority)."""
    existing = db.get_research_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Research item '{item_id}' not found")

    updates = {k: v for k, v in request.items() if k in {
        "notes", "status", "priority", "action", "entity_label", "source_context",
    }}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.update_research_item(item_id, updates)
    return db.get_research_item(item_id)


@app.delete("/api/research/{item_id}")
def delete_research_item(item_id: str):
    """Delete a research item."""
    existing = db.get_research_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Research item '{item_id}' not found")
    db.delete_research_item(item_id)
    return {"deleted": item_id}


@app.post("/api/research/{item_id}/reanalyze")
async def reanalyze_research_item(item_id: str):
    """Trigger re-analysis for a research item using current pipeline data.

    Fetches current entity data, calls Claude for fresh analysis,
    updates the item's last_analysis and current_sentiment/mention_count.
    """
    existing = db.get_research_item(item_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Research item '{item_id}' not found")

    entity_id = existing["entity_id"]

    # Look up the entity in entity_mentions (under _lock)
    with _lock:
        entity_data = entity_mentions.get(entity_id, {}).copy()

    # Also try the DB if not in memory
    if not entity_data:
        entity_data = db.get_entity(entity_id) or {}

    current_volume = entity_data.get("volume", 0)
    sentiment_sum = entity_data.get("sentiment_sum", 0.0)
    sentiment_count = entity_data.get("sentiment_count", 0)
    current_sentiment = (sentiment_sum / sentiment_count) if sentiment_count > 0 else 0.5
    sources = entity_data.get("sources", {})
    keywords = entity_data.get("keywords", [])
    sample_docs = entity_data.get("sample_docs", [])

    # Build context dict matching /api/analyze format
    analysis_request = {
        "entity_id": entity_id,
        "label": existing["entity_label"],
        "sentiment": current_sentiment,
        "volume": current_volume,
        "sources": sources,
        "keywords": keywords,
        "sampleDocs": sample_docs,
        "context_type": "entity",
    }

    # Call the same analysis logic as /api/analyze
    analysis_result = await analyze_entity(analysis_request)

    # Update the research item with fresh data
    now = datetime.now(timezone.utc).isoformat()
    db.update_research_item(item_id, {
        "last_analysis": analysis_result.get("analysis", ""),
        "current_sentiment": current_sentiment,
        "current_mention_count": current_volume,
        "updated_at": now,
    })

    updated = db.get_research_item(item_id)
    return {
        "item": updated,
        "analysis": analysis_result,
    }


@app.get("/api/sectors")
def get_sectors(
    limit: int = Query(30, ge=1, le=100),
    since: Optional[str] = Query(None, description="ISO timestamp or preset: 1D,5D,1M,YTD,1Y,5Y"),
):
    """Entity mentions aggregated as sectors for the heat sphere."""
    cutoff = _parse_since(since)
    with _lock:
        if cutoff:
            filtered = {k: v for k, v in entity_mentions.items()
                        if v.get("last_seen") and v["last_seen"] >= cutoff}
        else:
            filtered = entity_mentions
        # Take a snapshot for processing outside the lock
        entities_snapshot = list(filtered.values())

    # Compute timeframe-specific volume and sort by it
    scored = []
    for e in entities_snapshot:
        tf_volume, tf_sent_sum, tf_sent_count = _compute_timeframe_volume(e, cutoff)
        if tf_volume <= 0:
            continue
        scored.append((e, tf_volume, tf_sent_sum, tf_sent_count))
    scored.sort(key=lambda t: t[1], reverse=True)
    scored = scored[:limit]

    result = []
    for e, tf_volume, tf_sent_sum, tf_sent_count in scored:
        # Compute sentiment from timeframe-filtered data
        if tf_sent_count > 0:
            sentiment = tf_sent_sum / tf_sent_count
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
            "volume": tf_volume,
            "priceChange24h": round((sentiment - 0.5) * 10, 1),  # Derive from sentiment
            "keywords": e["keywords"][:5],
            "sources": sources_dict,
            "sampleDocs": e.get("sample_docs", [])[:5],
            "uniqueSources": len(source_list),
            "entity_type": e.get("entity_type", ""),
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


# Cache for macro chart yfinance data (30-minute TTL)
_macro_chart_cache: dict = {"data": None, "fetched_at": 0}


def _fetch_macro_timeline() -> list[dict]:
    """Fetch S&P 500, VIX, 10Y Treasury via yfinance for the macro chart."""
    now = time.time()
    if _macro_chart_cache["data"] and now - _macro_chart_cache["fetched_at"] < 1800:
        return _macro_chart_cache["data"]

    try:
        import yfinance as yf

        # Get 6 months of weekly data — flatten MultiIndex columns from yfinance
        spy = yf.download("SPY", period="6mo", interval="1wk", progress=False)
        vix = yf.download("^VIX", period="6mo", interval="1wk", progress=False)
        tny = yf.download("^TNX", period="6mo", interval="1wk", progress=False)
        # Flatten MultiIndex columns if present
        for df in [spy, vix, tny]:
            if hasattr(df.columns, "levels") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)

        timeline: list[dict] = []
        for date in spy.index:
            date_str = date.strftime("%Y-%m-%d")
            try:
                sp_close = float(spy.loc[date, "Close"])
            except Exception:
                continue
            entry: dict = {
                "date": date_str,
                "sp500": round(sp_close, 1),
                "sentiment": 50,
            }
            try:
                if date in vix.index:
                    entry["vix"] = round(float(vix.loc[date, "Close"]), 1)
            except Exception:
                pass
            try:
                if date in tny.index:
                    entry["treasury10y"] = round(float(tny.loc[date, "Close"]), 2)
            except Exception:
                pass
            timeline.append(entry)

        _macro_chart_cache["data"] = timeline
        _macro_chart_cache["fetched_at"] = now
        return timeline
    except Exception as e:
        log.warning("Failed to fetch macro timeline: %s", e)
        return []


@app.get("/api/pulse/charts")
def get_pulse_charts(
    since: Optional[str] = Query(None, description="ISO timestamp or preset: 1D,5D,1M,YTD,1Y,5Y"),
):
    """Chart card datasets for the Global Pulse page, built from live entity_mentions."""
    ECONOMIC_KEYWORDS = {
        "interest rate": "Interest Rates",
        "federal reserve": "Interest Rates",
        "fed funds": "Interest Rates",
        "rate hike": "Interest Rates",
        "rate cut": "Interest Rates",
        "employment": "Employment",
        "unemployment": "Employment",
        "jobs report": "Employment",
        "labor market": "Employment",
        "hiring": "Employment",
        "layoff": "Employment",
        "inflation": "Inflation",
        "cpi": "Inflation",
        "price index": "Inflation",
        "deflation": "Inflation",
        "gdp": "GDP Growth",
        "gross domestic": "GDP Growth",
        "economic growth": "GDP Growth",
        "housing": "Housing",
        "mortgage": "Housing",
        "real estate": "Housing",
        "home sales": "Housing",
        "treasury": "Treasury",
        "yield curve": "Treasury",
        "bond": "Treasury",
        "10 year": "Treasury",
        "recession": "Recession Risk",
        "downturn": "Recession Risk",
        "bear market": "Recession Risk",
        "consumer spending": "Consumer",
        "retail sales": "Consumer",
        "consumer confidence": "Consumer",
        "tariff": "Trade Policy",
        "trade war": "Trade Policy",
        "sanctions": "Trade Policy",
        "oil price": "Energy",
        "opec": "Energy",
        "natural gas": "Energy",
        "crypto": "Crypto",
        "bitcoin": "Crypto",
        "ethereum": "Crypto",
        "regulation": "Regulation",
        "antitrust": "Regulation",
        "sec ": "Regulation",
    }
    # Pre-compile word-boundary patterns for strict matching
    _ECONOMIC_PATTERNS = {
        keyword: (re.compile(r'\b' + re.escape(keyword) + r'\b', re.IGNORECASE), category)
        for keyword, category in ECONOMIC_KEYWORDS.items()
    }

    cutoff = _parse_since(since)

    with _lock:
        # Snapshot entity_mentions for processing
        raw_entities = list(entity_mentions.values())

    # Pre-compute timeframe-filtered volume/sentiment for each entity
    entities: list[dict] = []
    for e in raw_entities:
        tf_vol, tf_sent_sum, tf_sent_count = _compute_timeframe_volume(e, cutoff)
        if tf_vol <= 0:
            continue
        # Create a lightweight copy with timeframe-specific values
        entities.append({
            **e,
            "_tf_volume": tf_vol,
            "_tf_sent_sum": tf_sent_sum,
            "_tf_sent_count": tf_sent_count,
        })

    # --- topSectors: highest sentiment entities with enough data ---
    # Prefer entities from multiple sources or with short names (likely tickers/companies)
    top_candidates = [
        e for e in entities
        if e["_tf_volume"] >= 5 and e["_tf_sent_count"] > 0
    ]
    # Score: sentiment * (1 + source_diversity_bonus)
    def _sector_rank(e):
        avg_sent = e["_tf_sent_sum"] / e["_tf_sent_count"]
        num_sources = len(e.get("sources", {})) if isinstance(e.get("sources"), dict) else 1
        # Prefer short labels (tickers) and multi-source entities
        name_bonus = 1.2 if len(e.get("id", "")) <= 6 else 1.0
        source_bonus = 1.0 + (num_sources - 1) * 0.3
        return avg_sent * source_bonus * name_bonus

    top_candidates.sort(key=_sector_rank, reverse=True)
    top_sectors = []
    seen_names = set()
    for e in top_candidates:
        name = e["id"] if len(e["id"]) <= 15 else e["label"][:20]
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        avg = e["_tf_sent_sum"] / e["_tf_sent_count"]
        top_sectors.append({
            "name": name,
            "score": round(avg * 100),
            "volume": e["_tf_volume"],
            "sources": dict(e["sources"]) if isinstance(e["sources"], dict) else {},
        })
        if len(top_sectors) >= 5:
            break

    # --- emergingRisks: negative sentiment, high volume ---
    risk_candidates = [
        e for e in entities
        if e["_tf_volume"] >= 5
        and e["_tf_sent_count"] > 0
        and (e["_tf_sent_sum"] / e["_tf_sent_count"]) < 0.45
    ]
    risk_candidates.sort(key=lambda e: e["_tf_volume"], reverse=True)
    emerging_risks = []
    seen_risk_names = set()
    for e in risk_candidates:
        name = e["id"] if len(e["id"]) <= 15 else e["label"][:20]
        if name.lower() in seen_risk_names:
            continue
        seen_risk_names.add(name.lower())
        avg = e["_tf_sent_sum"] / e["_tf_sent_count"]
        emerging_risks.append({
            "name": name,
            "score": round((1.0 - avg) * 100),
            "volume": e["_tf_volume"],
            "sources": dict(e["sources"]) if isinstance(e["sources"], dict) else {},
        })
        if len(emerging_risks) >= 5:
            break

    # --- economicSentiments: entities matching economic keywords (word-boundary) ---
    # Aggregate by canonical category so "CPI" and "inflation" both map to "Inflation"
    _econ_category_agg: dict[str, dict] = {}  # category -> {volume, sent_sum, sent_count, sources}
    for e in entities:
        # Search label, id, and keywords for economic terms
        search_text = f"{e.get('label', '')} {e.get('id', '')} {' '.join(e.get('keywords', []))}"
        matched_category = None
        for _kw, (pattern, category) in _ECONOMIC_PATTERNS.items():
            if pattern.search(search_text):
                matched_category = category
                break
        if not matched_category:
            continue
        if matched_category not in _econ_category_agg:
            _econ_category_agg[matched_category] = {
                "volume": 0, "sent_sum": 0.0, "sent_count": 0, "sources": defaultdict(int),
            }
        agg = _econ_category_agg[matched_category]
        agg["volume"] += e["_tf_volume"]
        agg["sent_sum"] += e["_tf_sent_sum"]
        agg["sent_count"] += e["_tf_sent_count"]
        src_dict = e.get("sources", {})
        if isinstance(src_dict, dict):
            for sk, sv in src_dict.items():
                agg["sources"][sk] += sv

    economic_sentiments = []
    for category, agg in _econ_category_agg.items():
        if agg["sent_count"] > 0:
            avg = agg["sent_sum"] / agg["sent_count"]
        else:
            avg = 0.5
        direction = "bullish" if avg > 0.5 else "bearish"
        economic_sentiments.append({
            "name": category,
            "score": round(avg * 100),
            "direction": direction,
            "volume": agg["volume"],
            "sources": dict(agg["sources"]),
        })
    # Sort by volume descending, take top 5
    economic_sentiments.sort(key=lambda x: x.get("volume", 0), reverse=True)
    economic_sentiments = economic_sentiments[:5]

    # --- macroTimeline: yfinance live data + pipeline sentiment overlay ---
    macro_timeline = _fetch_macro_timeline()

    # Overlay pipeline sentiment from entity_mentions sample_docs
    if macro_timeline:
        import math as _math
        # Build a map of week -> [sentiment_values] from all entity sample_docs
        _week_sentiments: dict[str, list[float]] = defaultdict(list)
        for e in entities:
            for doc in e.get("sample_docs", []):
                doc_ts = doc.get("created_at") or ""
                doc_score = doc.get("score")
                if doc_ts and doc_score is not None and doc_score > 0:
                    doc_day = doc_ts[:10]  # YYYY-MM-DD
                    norm = 0.3 + 0.6 * min(1.0, _math.log1p(doc_score) / _math.log1p(1000))
                    _week_sentiments[doc_day].append(norm)

        # For each timeline entry, find sentiment values in the surrounding week
        for entry in macro_timeline:
            entry_date = entry["date"]  # YYYY-MM-DD
            # Collect sentiments from this date +/- 3 days
            from datetime import timedelta as _td
            try:
                center = datetime.strptime(entry_date, "%Y-%m-%d")
                week_vals: list[float] = []
                for delta in range(-3, 4):
                    check_day = (center + _td(days=delta)).strftime("%Y-%m-%d")
                    week_vals.extend(_week_sentiments.get(check_day, []))
                if week_vals:
                    entry["sentiment"] = round((sum(week_vals) / len(week_vals)) * 100)
            except Exception:
                pass  # Keep the default sentiment=50

    return {
        "topSectors": top_sectors,
        "emergingRisks": emerging_risks,
        "economicSentiments": economic_sentiments,
        "macroTimeline": macro_timeline,
    }


@app.get("/api/search")
def search_entities(
    q: str = Query("", min_length=1, max_length=200, description="Search query"),
):
    """Search entity_mentions + ticker lookup + yfinance fallback. Returns top 10 matches."""
    query = q.lower().strip()
    if not query:
        return []

    # Common ticker symbol → company name mapping for search
    TICKER_MAP = {
        "aapl": "Apple", "tsla": "Tesla", "msft": "Microsoft", "googl": "Google",
        "goog": "Google", "amzn": "Amazon", "meta": "Meta", "nvda": "NVIDIA",
        "spy": "SPY", "qqq": "QQQ", "amd": "AMD", "intc": "Intel",
        "nflx": "Netflix", "dis": "Disney", "baba": "Alibaba", "jpm": "JPMorgan",
        "v": "Visa", "ma": "Mastercard", "pypl": "PayPal", "sq": "Square",
        "crm": "Salesforce", "adbe": "Adobe", "orcl": "Oracle", "ibm": "IBM",
        "csco": "Cisco", "xom": "Exxon", "cvx": "Chevron", "wmt": "Walmart",
        "ko": "Coca-Cola", "pep": "PepsiCo", "mrna": "Moderna", "pfe": "Pfizer",
        "btcusdt": "Bitcoin", "ethusdt": "Ethereum", "solusdt": "Solana",
    }

    results = []
    seen_ids = set()

    # 1. Search entity_mentions (pipeline data)
    with _lock:
        # Also search by ticker symbol → try finding the company name
        search_terms = [query]
        if query.upper() in {k.upper() for k in TICKER_MAP}:
            mapped = TICKER_MAP.get(query, "")
            if mapped:
                search_terms.append(mapped.lower())

        for eid, em in entity_mentions.items():
            score = 0
            eid_lower = eid.lower()
            label_lower = (em.get("label") or "").lower()
            keywords_lower = [k.lower() for k in em.get("keywords", [])]

            for term in search_terms:
                if term == eid_lower or term == label_lower:
                    score = max(score, 100)
                elif term in eid_lower:
                    score = max(score, 80)
                elif term in label_lower:
                    score = max(score, 60)
                elif any(term in kw for kw in keywords_lower):
                    score = max(score, 40)
                else:
                    for word in term.split():
                        if len(word) < 2:
                            continue
                        if word in eid_lower or word in label_lower:
                            score = max(score, 30)
                        elif any(word in kw for kw in keywords_lower):
                            score = max(score, 20)

            if score > 0 and eid not in seen_ids:
                sentiment = 0.5
                if em.get("sentiment_count", 0) > 0:
                    sentiment = em["sentiment_sum"] / em["sentiment_count"]
                seen_ids.add(eid)
                # Infer entity type from id pattern
                eid_upper = em["id"].upper()
                if len(eid_upper) <= 5 and eid_upper.isalpha():
                    etype = "TICKER"
                elif eid_upper.startswith("R/"):
                    etype = "GROUP"
                elif " " not in em["id"] and len(em["id"]) <= 10:
                    etype = "COMPANY"
                else:
                    etype = "ENTITY"

                results.append({
                    "id": em["id"],
                    "label": em.get("label", eid),
                    "volume": em.get("volume", 0),
                    "sentiment": round(sentiment, 3),
                    "entity_type": etype,
                    "source": "pipeline",
                    "_score": score,
                })

    # 2. If few results, try yfinance ticker lookup as fallback
    if len(results) < 3:
        ticker_query = query.upper().strip()
        # Check if it looks like a ticker (1-5 uppercase letters)
        if 1 <= len(ticker_query) <= 5 and ticker_query.isalpha():
            try:
                import yfinance as yf
                info = yf.Ticker(ticker_query).info
                name = info.get("shortName") or info.get("longName")
                if name and ticker_query not in seen_ids:
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                    results.append({
                        "id": ticker_query,
                        "label": f"{ticker_query} — {name}",
                        "volume": 0,
                        "sentiment": 0.5,
                        "source": "yfinance",
                        "price": round(price, 2) if price else None,
                        "_score": 90,
                    })
            except Exception:
                pass

        # Also check if query matches a known ticker in our map
        if query in TICKER_MAP and TICKER_MAP[query].upper() not in seen_ids:
            company = TICKER_MAP[query]
            results.append({
                "id": query.upper(),
                "label": f"{query.upper()} — {company}",
                "volume": 0,
                "sentiment": 0.5,
                "source": "ticker_map",
                "_score": 85,
            })

    # Sort by score then volume
    results.sort(key=lambda r: (-r["_score"], -r.get("volume", 0)))
    for r in results:
        r.pop("_score", None)

    return results[:10]


# ---------------------------------------------------------------------------
# Financial Alpha Endpoint
# ---------------------------------------------------------------------------

# Cache for yfinance data: {ticker: (fetch_time, info_dict, price_df)}
_yf_cache: dict[str, tuple[float, dict, object]] = {}
_YF_CACHE_TTL = 300  # 5 minutes


def _get_yf_data(ticker: str) -> tuple[dict, object]:
    """Fetch yfinance Ticker info and price history with 5-minute caching."""
    import yfinance as yf

    cached = _yf_cache.get(ticker)
    if cached:
        fetch_time, info, hist = cached
        if time.time() - fetch_time < _YF_CACHE_TTL:
            return info, hist

    t = yf.Ticker(ticker)
    info = t.info or {}
    hist = t.history(period="3mo")
    _yf_cache[ticker] = (time.time(), info, hist)
    return info, hist


@app.get("/api/alpha/{ticker}")
async def get_alpha(ticker: str):
    """Comprehensive data for the Financial Alpha dashboard page."""
    ticker = ticker.upper().strip()

    # --- 1. Fetch price data and metadata via yfinance ---
    candles = []
    technicals_snapshot = {}
    technicals_series = []
    company = ticker
    price = None
    change_pct = None
    change_amt = None
    range_52w = {"low": None, "high": None}
    info = {}
    hist = None

    try:
        info, hist = _get_yf_data(ticker)

        company = info.get("longName") or info.get("shortName") or ticker
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose")
        if price is not None and prev_close:
            change_amt = round(price - prev_close, 2)
            change_pct = round((change_amt / prev_close) * 100, 2)
        range_52w = {
            "low": info.get("fiftyTwoWeekLow"),
            "high": info.get("fiftyTwoWeekHigh"),
        }

        if hist is not None and not hist.empty:
            # Standardize columns
            import pandas as pd
            hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]

            # Build candles list
            for idx, row in hist.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                candles.append({
                    "date": date_str,
                    "open": round(float(row.get("open", 0)), 2),
                    "high": round(float(row.get("high", 0)), 2),
                    "low": round(float(row.get("low", 0)), 2),
                    "close": round(float(row.get("close", 0)), 2),
                    "volume": int(row.get("volume", 0)),
                })

            # Fall back to last close if currentPrice unavailable
            if price is None and len(candles) > 0:
                price = candles[-1]["close"]

    except Exception as e:
        log.warning("yfinance fetch failed for %s: %s", ticker, e)

    # --- 2. Compute technical indicators ---
    try:
        if hist is not None and not hist.empty:
            from stock_alpha.technicals import compute_technicals
            import pandas as pd
            import numpy as np

            tech_df = compute_technicals(hist)
            latest = tech_df.iloc[-1]

            def _safe(val):
                if val is None:
                    return None
                try:
                    if pd.isna(val):
                        return None
                except (TypeError, ValueError):
                    return None
                return round(float(val), 4)

            sma20 = _safe(latest.get("sma_20"))
            sma50 = _safe(latest.get("sma_50"))
            close = _safe(latest.get("close"))

            # Determine trend
            trend = "neutral"
            if close is not None and sma20 is not None and sma50 is not None:
                if close > sma20 > sma50:
                    trend = "bullish"
                elif close < sma20 < sma50:
                    trend = "bearish"

            technicals_snapshot = {
                "rsi": _safe(latest.get("rsi")),
                "macd": {
                    "value": _safe(latest.get("macd")),
                    "signal": _safe(latest.get("macd_signal")),
                    "histogram": _safe(latest.get("macd_hist")),
                },
                "bb": {
                    "pctb": _safe(latest.get("bb_pctb")),
                    "upper": _safe(latest.get("bb_upper")),
                    "lower": _safe(latest.get("bb_lower")),
                    "middle": _safe(latest.get("bb_mid")),
                },
                "sma20": sma20,
                "sma50": sma50,
                "ema12": _safe(latest.get("ema_12")),
                "ema26": _safe(latest.get("ema_26")),
                "atr": _safe(latest.get("atr")),
                "obv": _safe(latest.get("obv")),
                "trend": trend,
            }

            # Build technicals_series (last 60 rows)
            series_df = tech_df.tail(60).copy()
            series_df = series_df.replace({np.nan: None})
            for idx, row in series_df.iterrows():
                date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)
                technicals_series.append({
                    "date": date_str,
                    "close": _safe(row.get("close")),
                    "rsi": _safe(row.get("rsi")),
                    "macd": _safe(row.get("macd")),
                    "macd_signal": _safe(row.get("macd_signal")),
                    "macd_hist": _safe(row.get("macd_hist")),
                    "bb_upper": _safe(row.get("bb_upper")),
                    "bb_lower": _safe(row.get("bb_lower")),
                    "bb_mid": _safe(row.get("bb_mid")),
                    "bb_pctb": _safe(row.get("bb_pctb")),
                    "sma20": _safe(row.get("sma_20")),
                    "sma50": _safe(row.get("sma_50")),
                    "ema12": _safe(row.get("ema_12")),
                    "ema26": _safe(row.get("ema_26")),
                    "atr": _safe(row.get("atr")),
                    "obv": _safe(row.get("obv")),
                    "volume": _safe(row.get("volume")),
                })

    except Exception as e:
        log.warning("Technicals computation failed for %s: %s", ticker, e)

    # --- 3. Sentiment data from entity_mentions cache ---
    sentiment_data = {
        "score": None,
        "label": "neutral",
        "volume": 0,
        "sources": {},
        "keywords": [],
        "sample_docs": [],
        "timeline": [],
    }

    # Common ticker → company name aliases
    _TICKER_ALIASES = {
        "AAPL": ["APPLE", "Apple Inc", "Apple"],
        "TSLA": ["TESLA", "Tesla Inc", "Tesla"],
        "NVDA": ["NVIDIA", "Nvidia Corp", "Nvidia"],
        "MSFT": ["MICROSOFT", "Microsoft Corp", "Microsoft"],
        "AMZN": ["AMAZON", "Amazon.com", "Amazon"],
        "GOOGL": ["GOOGLE", "Alphabet", "Google"],
        "META": ["META PLATFORMS", "Facebook", "Meta"],
        "JPM": ["JP MORGAN", "JPMorgan", "JPMorgan Chase"],
        "XOM": ["EXXON", "Exxon Mobil", "ExxonMobil"],
        "BTC": ["BITCOIN", "Bitcoin", "BTC-USD"],
        "ETH": ["ETHEREUM", "Ethereum", "ETH-USD"],
    }

    with _lock:
        # Try various key forms for the ticker + aliases
        em = (
            entity_mentions.get(ticker)
            or entity_mentions.get(ticker.upper())
            or entity_mentions.get("$" + ticker)
        )
        # Try aliases — merge all matching entity_mentions
        if not em or em.get("volume", 0) < 5:
            aliases = _TICKER_ALIASES.get(ticker.upper(), [])
            best = em
            best_vol = em.get("volume", 0) if em else 0
            for alias in aliases:
                candidate = entity_mentions.get(alias) or entity_mentions.get(alias.upper())
                if candidate and candidate.get("volume", 0) > best_vol:
                    best = candidate
                    best_vol = candidate.get("volume", 0)
            # Also fuzzy search — any entity containing the ticker
            if best_vol < 10:
                for eid, edata in entity_mentions.items():
                    if ticker.upper() in eid.upper() and edata.get("volume", 0) > best_vol:
                        best = edata
                        best_vol = edata.get("volume", 0)
            em = best
        if em:
            vol = em.get("volume", 0)
            score = None
            if em.get("sentiment_count", 0) > 0:
                score = round(em["sentiment_sum"] / em["sentiment_count"], 4)
            label = "neutral"
            if score is not None:
                if score > 0.6:
                    label = "bullish"
                elif score < 0.4:
                    label = "bearish"

            sentiment_data = {
                "score": score,
                "label": label,
                "volume": vol,
                "sources": dict(em.get("sources", {})),
                "keywords": em.get("keywords", [])[:8],
                "sample_docs": em.get("sample_docs", [])[:10],
                "timeline": [],
            }

            # Build sentiment timeline from sample_docs timestamps
            from collections import defaultdict as _dd
            date_buckets: dict[str, list[float]] = _dd(list)
            for doc in em.get("sample_docs", []):
                created = doc.get("created_at") or ""
                doc_score = doc.get("score")
                if created and doc_score is not None:
                    day = created[:10]  # YYYY-MM-DD
                    import math
                    norm = 0.3 + 0.6 * min(1.0, math.log1p(doc_score) / math.log1p(1000))
                    date_buckets[day].append(norm)
            for day in sorted(date_buckets.keys()):
                vals = date_buckets[day]
                sentiment_data["timeline"].append({
                    "date": day,
                    "sentiment": round(sum(vals) / len(vals), 4),
                    "count": len(vals),
                })

    # --- 4. Signal scorecard from signals deque ---
    signal_data = None
    with _lock:
        # Search signals by ticker and known aliases
        # Prefer non-anomaly signals over anomaly-only signals
        alias_set = {ticker}
        for alias in _TICKER_ALIASES.get(ticker, []):
            alias_set.add(alias.upper())

        best_signal = None
        anomaly_signal = None
        for sig in reversed(list(signals)):
            sig_ticker = (sig.get("ticker") or "").upper()
            if sig_ticker in alias_set:
                headline = sig.get("headline", "")
                if "Anomaly" not in headline:
                    best_signal = sig
                    break
                elif not anomaly_signal:
                    anomaly_signal = sig

        chosen = best_signal or anomaly_signal
        if chosen:
            signal_data = {
                "confidence": chosen.get("confidence"),
                "type": chosen.get("type"),
                "headline": chosen.get("headline"),
                "sources": list(chosen.get("sources", {}).keys()) if isinstance(chosen.get("sources"), dict) else [],
            }

    # Always generate a context-rich signal from entity data
    if em and em.get("volume", 0) > 0:
        vol = em.get("volume", 0)
        sources_dict = dict(em.get("sources", {})) if isinstance(em.get("sources"), dict) else {}
        num_sources = len(sources_dict)
        sent_label = sentiment_data.get("label", "neutral")
        sent_score = sentiment_data.get("score")
        sent_pct = f" ({int(sent_score * 100)}%)" if sent_score is not None else ""

        entity_headline = (
            f"{ticker} detected across {num_sources} source{'s' if num_sources != 1 else ''} "
            f"({', '.join(sources_dict.keys())}) with {vol:,} mentions — "
            f"sentiment {sent_label}{sent_pct}"
        )

        if not signal_data:
            signal_data = {
                "confidence": min(0.8, 0.3 + num_sources * 0.15),
                "type": "volume_spike" if vol > 100 else "multi_source_convergence" if num_sources > 1 else "monitoring",
                "headline": entity_headline,
                "sources": list(sources_dict.keys()),
            }
        elif signal_data.get("headline", "").startswith("Anomaly"):
            # Replace cryptic anomaly headline with informative context
            signal_data["headline"] = entity_headline
            signal_data["anomaly_note"] = "Pipeline flagged unusual mention patterns — may indicate coordinated activity or batch ingestion artifacts"

    # --- 5. Build score breakdown ---
    score_data = {
        "overall": None,
        "direction": "neutral",
        "sentiment_weight": 0.25,
        "svc_weight": 0.15,
        "technical_weight": 0.20,
        "microstructure_weight": 0.15,
        "order_flow_weight": 0.15,
        "correlation_weight": 0.10,
        "components_available": [],
    }

    try:
        components = []
        raw_parts = {}

        # Sentiment component
        if sentiment_data["score"] is not None:
            components.append("sentiment")
            raw_parts["sentiment"] = (sentiment_data["score"] - 0.5) * 2

        # SVC (Sentiment Volume Convergence) component
        # Compute from sentiment timeline: sentiment_shift x volume_change
        try:
            timeline = sentiment_data.get("timeline", [])
            if len(timeline) >= 4:
                recent = timeline[-2:]
                prior = timeline[-4:-2]
                recent_avg = sum(t["sentiment"] for t in recent) / len(recent)
                prior_avg = sum(t["sentiment"] for t in prior) / len(prior)
                sentiment_shift = recent_avg - prior_avg
                recent_vol = sum(t.get("count", 1) for t in recent)
                prior_vol = sum(t.get("count", 1) for t in prior)
                volume_change = (recent_vol - prior_vol) / max(prior_vol, 1)
                svc_value = sentiment_shift * volume_change
                svc_normalized = max(-1.0, min(1.0, svc_value * 10))
                components.append("svc")
                raw_parts["svc"] = svc_normalized
            elif sentiment_data.get("volume", 0) > 10 and sentiment_data["score"] is not None:
                # Fallback: derive SVC from overall sentiment deviation from neutral
                deviation = (sentiment_data["score"] - 0.5) * 2
                volume_factor = min(1.0, sentiment_data["volume"] / 100)
                components.append("svc")
                raw_parts["svc"] = max(-1.0, min(1.0, deviation * volume_factor))
        except Exception:
            pass

        # Technical component
        if technicals_snapshot and technicals_snapshot.get("rsi") is not None:
            components.append("technical")
            rsi_val = technicals_snapshot["rsi"]
            tech_s = 0.0
            if rsi_val < 30:
                tech_s += 0.5
            elif rsi_val > 70:
                tech_s -= 0.5
            macd_h = (technicals_snapshot.get("macd") or {}).get("histogram")
            if macd_h is not None:
                tech_s += max(-1.0, min(1.0, macd_h * 5))
            bb_pctb = (technicals_snapshot.get("bb") or {}).get("pctb")
            if bb_pctb is not None:
                if bb_pctb < 0.2:
                    tech_s += 0.3
                elif bb_pctb > 0.8:
                    tech_s -= 0.3
            sma20 = technicals_snapshot.get("sma20")
            if sma20 and price:
                if price > sma20:
                    tech_s += 0.2
                else:
                    tech_s -= 0.2
            raw_parts["technical"] = max(-1.0, min(1.0, tech_s / 2))

        # Microstructure component
        # Derive from price position relative to key levels
        try:
            if technicals_snapshot and price:
                micro_signals = []
                # Price vs SMA20 (proxy for VWAP when no real-time data)
                sma20 = technicals_snapshot.get("sma20")
                if sma20 and sma20 > 0:
                    vwap_pos = (price - sma20) / sma20
                    micro_signals.append(max(-1.0, min(1.0, vwap_pos * 10)))
                # BB %B as volume profile proxy
                bb_pctb = (technicals_snapshot.get("bb") or {}).get("pctb")
                if bb_pctb is not None:
                    micro_signals.append((bb_pctb - 0.5) * 2)
                # ATR-based volatility
                atr = technicals_snapshot.get("atr")
                if atr and price and price > 0:
                    atr_pct = atr / price
                    # High volatility = opportunity but also risk
                    micro_signals.append(-0.3 if atr_pct > 0.03 else 0.1)
                if micro_signals:
                    components.append("microstructure")
                    raw_parts["microstructure"] = sum(micro_signals) / len(micro_signals)
        except Exception:
            pass

        # Order Flow component
        # Derive from volume patterns and price action
        try:
            if hist is not None and not hist.empty and len(hist) >= 5:
                import numpy as np
                recent_5 = hist.tail(5)
                # Volume trend (rising volume = conviction)
                vol_trend = 0.0
                if "volume" in recent_5.columns:
                    vols = recent_5["volume"].values
                    if len(vols) >= 2 and vols[-2] > 0:
                        vol_trend = (vols[-1] - vols[-2]) / vols[-2]
                # Price-volume divergence
                price_trend = 0.0
                if "close" in recent_5.columns:
                    closes = recent_5["close"].values
                    if len(closes) >= 2 and closes[-2] > 0:
                        price_trend = (closes[-1] - closes[-2]) / closes[-2]
                # If price up + volume up = bullish flow
                # If price down + volume up = bearish flow (selling pressure)
                flow_score = 0.0
                if vol_trend > 0.1:
                    flow_score = 0.5 if price_trend > 0 else -0.5
                elif vol_trend < -0.1:
                    flow_score = -0.2 if price_trend < 0 else 0.2
                components.append("order_flow")
                raw_parts["order_flow"] = max(-1.0, min(1.0, flow_score))
        except Exception:
            pass

        # Correlation component
        if signal_data and signal_data.get("confidence") is not None:
            components.append("correlation")
            raw_parts["correlation"] = signal_data["confidence"] * 2 - 1

        # Compute weighted overall
        if raw_parts:
            base_weights = {
                "sentiment": 0.25, "svc": 0.15, "technical": 0.20,
                "microstructure": 0.15, "order_flow": 0.15, "correlation": 0.10,
            }
            active_total = sum(base_weights.get(k, 0) for k in raw_parts)
            if active_total > 0:
                scale = 1.0 / active_total
                overall = sum(base_weights.get(k, 0) * scale * v for k, v in raw_parts.items())
                overall = max(-1.0, min(1.0, overall))

                direction = "neutral"
                if overall > 0.15:
                    direction = "bullish"
                elif overall < -0.15:
                    direction = "bearish"

                score_data["overall"] = round(overall, 4)
                score_data["direction"] = direction

        score_data["components_available"] = components

    except Exception as e:
        log.warning("Score computation failed for %s: %s", ticker, e)

    # --- Phase 2: Sentiment velocity, insider scoring, macro regime ---
    velocity_data = None
    try:
        from stock_alpha.sentiment_velocity import SentimentVelocityTracker
        # Build velocity from sentiment timeline if available
        if sentiment_data.get("timeline"):
            tracker = SentimentVelocityTracker()
            for pt in sentiment_data["timeline"]:
                from datetime import datetime as _dt
                try:
                    ts = _dt.fromisoformat(pt["date"]) if "T" in pt.get("date", "") else _dt.strptime(pt["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    tracker.add_observation(ticker, ts, pt.get("avg_sentiment", 0.5) * 2 - 1, pt.get("count", 1))
                except Exception:
                    pass
            vel = tracker.compute(ticker)
            if vel:
                velocity_data = {
                    "velocity": round(vel.velocity, 4),
                    "acceleration": round(vel.acceleration, 4),
                    "momentum_score": round(vel.momentum_score, 4),
                    "regime": vel.regime,
                    "window_hours": round(vel.window_hours, 1),
                    "data_points": vel.data_points,
                }
    except Exception as e:
        log.debug("Velocity computation failed for %s: %s", ticker, e)

    insider_data = None
    try:
        from stock_alpha.insider_scoring import InsiderScorer
        scorer = InsiderScorer()
        # Ingest any insider trades from entity_mentions sample_docs
        # (This is a lightweight check — full scoring requires the engine)
        with _lock:
            for s in signals:
                if s.get("ticker", "").upper() == ticker and "insider" in s.get("headline", "").lower():
                    insider_data = {
                        "score": 0.0,
                        "regime": "neutral",
                        "headline": s.get("headline", ""),
                        "detected": True,
                    }
                    break
    except Exception as e:
        log.debug("Insider scoring failed for %s: %s", ticker, e)

    macro_data = None
    try:
        from stock_alpha.macro_regime import MacroRegimeDetector
        detector = MacroRegimeDetector()
        # Feed FRED data from Kafka cache
        with _lock:
            fred_docs = [d for d in recent_docs.get("finance.macro.fred", []) if isinstance(d, dict)]
        for doc in fred_docs:
            detector.ingest(doc)
        regime = detector.classify()
        if regime:
            macro_data = {
                "regime": regime.regime,
                "confidence": round(regime.confidence, 2),
                "regime_score": round(regime.regime_score, 2),
                "description": regime.description,
                "indicators": regime.indicators,
            }
    except Exception as e:
        log.debug("Macro regime failed: %s", e)

    # --- ML Prediction data ---
    ml_prediction = None
    feature_importance = []
    try:
        from stock_alpha.ml_scorer import MLScorer
        from stock_alpha.feature_store import FeatureStore

        ml_scorer = MLScorer()
        if ml_scorer.is_trained:
            ml_store = FeatureStore()
            ml_data = ml_store.get_training_data(ticker=ticker)
            if ml_data:
                latest_snapshot = ml_data[-1]
                prediction = ml_scorer.predict(latest_snapshot)
                if prediction:
                    # Blend ML score with rule-based score
                    rule_score = score_data.get("overall", 0) or 0
                    blended = ml_scorer.blend_with_rule_based(rule_score, prediction, blend_weight=0.4)
                    ml_prediction = {
                        **prediction,
                        "blended_score": round(blended, 4),
                        "snapshot_time": latest_snapshot.get("timestamp"),
                        "features_used": len(ml_scorer._feature_names),
                        "training_stats": ml_scorer.training_stats,
                    }
            # Top 10 feature importances
            feature_importance = ml_scorer.get_feature_importance()[:10]
    except Exception as e:
        log.debug("ML prediction failed for %s: %s", ticker, e)

    return {
        "ticker": ticker,
        "company": company,
        "price": price,
        "change_pct": change_pct,
        "change_amt": change_amt,
        "range_52w": range_52w,
        "candles": candles,
        "technicals": technicals_snapshot,
        "technicals_series": technicals_series,
        "sentiment": sentiment_data,
        "signal": signal_data,
        "score": score_data,
        "velocity": velocity_data,
        "insider": insider_data,
        "macro": macro_data,
        "ml_prediction": ml_prediction,
        "feature_importance": feature_importance,
    }


# ---------------------------------------------------------------------------
# Product Innovation Endpoint
# ---------------------------------------------------------------------------

# Sources considered product-related for the innovation page
_PRODUCT_TOPICS = [
    "consumer.reviews.trustpilot",
    "consumer.reviews.amazon",
    "finance.reddit.posts",
    "tech.hn.stories",
]

# Keywords that indicate a feature request
_REQUEST_KEYWORDS = {
    "wish", "want", "need", "should", "missing",
    "please", "feature", "request", "improve", "add",
}


@app.get("/api/innovation")
def get_innovation(
    source: Optional[str] = Query(None, description="Filter by source platform"),
    limit: int = Query(20, ge=1, le=100),
    since: Optional[str] = Query(None, description="ISO timestamp or preset: 1D,5D,1M,YTD,1Y,5Y"),
):
    """Data for the Product Innovation dashboard page."""
    import math

    cutoff = _parse_since(since)

    with _lock:
        # ----- channels: doc counts per source -----
        channels: dict[str, int] = {}
        for src in ("reddit", "hacker_news", "trustpilot", "amazon", "producthunt"):
            channels[src] = pipeline_stats["sources"].get(src, 0)

        # ----- collect entities, optionally filtered by source -----
        raw_filtered: list[dict] = []
        for em in entity_mentions.values():
            if source:
                src_dict = em.get("sources", {})
                if isinstance(src_dict, dict) and src_dict.get(source, 0) > 0:
                    raw_filtered.append(em)
            else:
                raw_filtered.append(em)

    # Apply timeframe filtering: compute per-entity timeframe volumes
    filtered_entities: list[dict] = []
    for em in raw_filtered:
        tf_vol, tf_sent_sum, tf_sent_count = _compute_timeframe_volume(em, cutoff)
        if tf_vol <= 0:
            continue
        filtered_entities.append({
            **em,
            "volume": tf_vol,
            "sentiment_sum": tf_sent_sum,
            "sentiment_count": tf_sent_count,
        })

    # ----- criticisms: negative-sentiment entities -----
    criticisms: list[dict] = []
    for em in filtered_entities:
        s_count = em.get("sentiment_count", 0)
        if s_count <= 0:
            continue
        avg_sent = em["sentiment_sum"] / s_count
        if avg_sent >= 0.45:
            continue
        # intensity: lower sentiment = higher intensity (0-1 scale)
        intensity = round(1.0 - avg_sent, 3)
        sample_quote = ""
        sample_docs_list = em.get("sample_docs", [])
        if sample_docs_list:
            sample_quote = sample_docs_list[0].get("content") or sample_docs_list[0].get("title") or ""
        src_dict = em.get("sources", {})
        source_list = sorted(src_dict.keys()) if isinstance(src_dict, dict) else []
        criticisms.append({
            "feature": em.get("label", em["id"]),
            "volume": em.get("volume", 0),
            "intensity": intensity,
            "sources": source_list,
            "sample_quote": sample_quote[:300],
        })
    criticisms.sort(key=lambda c: c["volume"], reverse=True)
    criticisms = criticisms[:limit]

    # ----- requests: entities with request-like keywords or product types -----
    requests_list: list[dict] = []
    for em in filtered_entities:
        kws = [k.lower() for k in em.get("keywords", [])]
        match_count = sum(1 for kw in kws for rk in _REQUEST_KEYWORDS if rk in kw)
        etype = (em.get("entity_type") or "").upper()
        is_product_type = etype in ("PRODUCT", "TECHNOLOGY")

        if match_count == 0 and not is_product_type:
            continue

        # intensity based on keyword match density
        if kws:
            intensity = round(min(1.0, match_count / max(len(kws), 1)), 3)
        else:
            intensity = 0.3 if is_product_type else 0.0

        sample_quote = ""
        sample_docs_list = em.get("sample_docs", [])
        if sample_docs_list:
            sample_quote = sample_docs_list[0].get("content") or sample_docs_list[0].get("title") or ""
        src_dict = em.get("sources", {})
        source_list = sorted(src_dict.keys()) if isinstance(src_dict, dict) else []
        requests_list.append({
            "feature": em.get("label", em["id"]),
            "volume": em.get("volume", 0),
            "intensity": intensity,
            "sources": source_list,
            "sample_quote": sample_quote[:300],
        })
    requests_list.sort(key=lambda r: r["volume"], reverse=True)
    requests_list = requests_list[:limit]

    # ----- gap_map: satisfaction vs importance scatter -----
    gap_map: list[dict] = []
    # Find max volume for log-normalization
    volumes = [em.get("volume", 0) for em in filtered_entities if em.get("volume", 0) >= 3]
    max_vol = max(volumes) if volumes else 1
    log_max = math.log(max_vol) if max_vol > 1 else 1.0

    for em in filtered_entities:
        vol = em.get("volume", 0)
        if vol < 3:
            continue
        s_count = em.get("sentiment_count", 0)
        satisfaction = round(em["sentiment_sum"] / s_count, 3) if s_count > 0 else 0.5
        importance = round(min(1.0, math.log(max(vol, 1)) / log_max), 3) if log_max > 0 else 0.0
        gap_map.append({
            "feature": em.get("label", em["id"]),
            "satisfaction": satisfaction,
            "importance": importance,
            "volume": vol,
            "entity_id": em["id"],
        })
    gap_map.sort(key=lambda g: g["importance"], reverse=True)
    gap_map = gap_map[:limit]

    # ----- trending_topics: top keywords across all entities -----
    keyword_counts: dict[str, int] = defaultdict(int)
    for em in filtered_entities:
        for kw in em.get("keywords", []):
            keyword_counts[kw] += 1
    trending_topics = sorted(
        [{"keyword": kw, "count": cnt} for kw, cnt in keyword_counts.items()],
        key=lambda t: t["count"],
        reverse=True,
    )[:20]

    # ----- recent_insights: latest docs from product-related sources -----
    recent_insights: list[dict] = []
    with _lock:
        for topic_key in _PRODUCT_TOPICS:
            for doc in recent_docs.get(topic_key, []):
                doc_source = doc.get("source", "")
                if source and doc_source != source:
                    continue
                doc_ts = doc.get("created_at") or doc.get("ingested_at") or ""
                if cutoff and doc_ts < cutoff:
                    continue
                recent_insights.append({
                    "title": (doc.get("title") or "")[:200],
                    "source": doc_source,
                    "content": (doc.get("content_text") or "")[:300],
                    "url": doc.get("url"),
                    "created_at": doc_ts,
                })
    # Sort by created_at descending, take latest N
    recent_insights.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    recent_insights = recent_insights[:limit]

    # ----- source_breakdown: aggregate stats for active filter -----
    total_entities = len(filtered_entities)
    total_mentions = sum(em.get("volume", 0) for em in filtered_entities)
    sent_sum = sum(em.get("sentiment_sum", 0) for em in filtered_entities if em.get("sentiment_count", 0) > 0)
    sent_cnt = sum(em.get("sentiment_count", 0) for em in filtered_entities)
    avg_sentiment = round(sent_sum / sent_cnt, 3) if sent_cnt > 0 else 0.5

    # ----- opportunity_scores: add score to each gap_map entry -----
    for gap in gap_map:
        vol = gap["volume"]
        log_vol = math.log(max(vol, 1)) if vol > 0 else 0.0
        gap["opportunity_score"] = round(
            (1.0 - gap["satisfaction"]) * gap["importance"] * min(1.0, log_vol / 5.0), 3
        )

    # ----- sentiment_trends: add trend to criticisms and requests -----
    # Build entity_id lookup from filtered_entities
    _entity_by_label: dict[str, dict] = {}
    for em in filtered_entities:
        _entity_by_label[em.get("label", em["id"])] = em

    def _compute_trend(feature_label: str) -> str:
        """Compare first-half vs second-half sentiment of sample_docs."""
        em = _entity_by_label.get(feature_label)
        if not em:
            return "stable"
        docs = em.get("sample_docs", [])
        if len(docs) < 2:
            return "stable"
        # Sort by created_at ascending
        sorted_docs = sorted(docs, key=lambda d: d.get("created_at") or d.get("ingested_at") or "")
        mid = len(sorted_docs) // 2
        first_half = sorted_docs[:mid]
        second_half = sorted_docs[mid:]

        def _avg_sentiment_approx(doc_list: list) -> float:
            """Approximate sentiment from content: negative words -> lower score."""
            _neg = {"bad", "worst", "terrible", "horrible", "awful", "hate", "slow",
                    "bug", "crash", "broken", "fail", "disappointing", "poor", "useless"}
            _pos = {"good", "great", "love", "fast", "excellent", "awesome", "perfect",
                    "amazing", "best", "reliable", "helpful", "easy"}
            total_score = 0.0
            count = 0
            for d in doc_list:
                text = ((d.get("content") or "") + " " + (d.get("title") or "")).lower()
                words = set(text.split())
                neg_count = len(words & _neg)
                pos_count = len(words & _pos)
                total = neg_count + pos_count
                if total > 0:
                    total_score += pos_count / total
                    count += 1
            return total_score / count if count > 0 else 0.5

        sent_first = _avg_sentiment_approx(first_half)
        sent_second = _avg_sentiment_approx(second_half)
        diff = sent_second - sent_first
        if diff < -0.1:
            return "worsening"
        elif diff > 0.1:
            return "improving"
        return "stable"

    for item in criticisms:
        item["trend"] = _compute_trend(item["feature"])
    for item in requests_list:
        item["trend"] = _compute_trend(item["feature"])

    # ----- competitive_landscape: co-occurring entities -----
    competitive_landscape: list[dict] = []
    # Build a mapping of entity -> set of doc titles for co-occurrence
    entity_doc_titles: dict[str, set[str]] = {}
    top_entities = sorted(filtered_entities, key=lambda e: e.get("volume", 0), reverse=True)[:10]
    top_entity_ids = {em["id"] for em in top_entities}
    top_entity_labels = {em["id"]: em.get("label", em["id"]) for em in top_entities}

    # Collect doc title sets for each top entity
    for em in top_entities:
        titles = set()
        for doc in em.get("sample_docs", []):
            t = (doc.get("title") or "") + " " + (doc.get("content") or "")
            if t.strip():
                titles.add(t.strip()[:200])
        entity_doc_titles[em["id"]] = titles

    # Find competitors by checking if other top entities' labels appear in docs
    for em in top_entities:
        docs_text = " ".join(entity_doc_titles.get(em["id"], [])).lower()
        if not docs_text:
            continue
        competitors: list[str] = []
        shared_count = 0
        for other in top_entities:
            if other["id"] == em["id"]:
                continue
            other_label = other.get("label", other["id"]).lower()
            # Check if the other entity's label appears in this entity's docs
            if other_label and len(other_label) > 1 and other_label in docs_text:
                competitors.append(top_entity_labels[other["id"]])
                shared_count += 1
        if competitors:
            competitive_landscape.append({
                "entity": top_entity_labels[em["id"]],
                "competitors": competitors,
                "shared_mentions": shared_count,
            })

    return {
        "channels": channels,
        "criticisms": criticisms,
        "requests": requests_list,
        "gap_map": gap_map,
        "trending_topics": trending_topics,
        "recent_insights": recent_insights,
        "source_breakdown": {
            "total_entities": total_entities,
            "total_mentions": total_mentions,
            "avg_sentiment": avg_sentiment,
        },
        "competitive_landscape": competitive_landscape,
    }


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
    source_list = ", ".join(f"{k} ({v} mentions)" for k, v in sources.items()) if sources else "no source data"
    sample_texts = ""
    for d in (sample_docs or [])[:5]:
        title = d.get("title") or d.get("headline") or ""
        content = d.get("content", "")
        src = d.get("source", "?")
        line = f"- [{src}] {title}"
        if content and content != title:
            line += f"\n  {content[:200]}"
        sample_texts += line + "\n"
    sample_texts = sample_texts.strip() or "No sample content available."

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
# Chat (context-aware AI assistant)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(request: dict):
    """Context-aware chat powered by Claude. Gathers pipeline data for the
    user's question and responds with analysis grounded in real data."""
    user_message = request.get("message", "")
    if not user_message.strip():
        return {"response": "Please ask a question."}

    # Gather relevant pipeline context
    context_parts = []

    # Ticker aliases for entity lookup
    _chat_aliases = {
        "AAPL": ["APPLE"], "TSLA": ["TESLA"], "NVDA": ["NVIDIA"],
        "MSFT": ["MICROSOFT"], "AMZN": ["AMAZON"], "GOOGL": ["GOOGLE"],
        "META": ["META PLATFORMS"], "JPM": ["JP MORGAN"], "XOM": ["EXXON"],
        "BTC": ["BITCOIN"], "ETH": ["ETHEREUM"], "RKLB": ["ROCKET LAB"],
    }

    # 1. Check if user mentions a ticker/entity — pull alpha data
    import re
    # Extract potential tickers (all-caps 2-5 letters) but filter common words
    _CHAT_STOP_WORDS = {
        "THE", "AND", "FOR", "NOT", "ARE", "BUT", "WAS", "HAS", "ALL", "ANY",
        "NEW", "NOW", "OLD", "OUR", "YOU", "HER", "HIS", "WHO", "HOW", "WHY",
        "CAN", "MAY", "LET", "SAY", "GET", "GOT", "PUT", "RUN", "SET", "USE",
        "WHAT", "WHEN", "WHERE", "WHICH", "WITH", "FROM", "ABOUT", "INTO",
        "BEEN", "SOME", "THAN", "THEM", "THEN", "THIS", "THAT", "JUST",
        "WILL", "DOES", "MAKE", "LIKE", "HAVE", "BEEN", "MORE", "ALSO",
        "EACH", "MUCH", "MOST", "ONLY", "OVER", "SUCH", "TAKE", "THAN",
        "TELL", "VERY", "WELL", "WHAT", "WHEN", "SHOW", "GIVE", "KEEP",
        "PRICE", "STOCK", "MARKET", "TRADE", "LONG", "SHORT", "SELL", "BUY",
    }
    raw_tickers = re.findall(r'\b([A-Z]{2,5})\b', user_message.upper())
    tickers_mentioned = [t for t in raw_tickers if t not in _CHAT_STOP_WORDS]
    # Also check for $TICKER pattern
    dollar_tickers = re.findall(r'\$([A-Z]{1,5})\b', user_message.upper())
    for dt in dollar_tickers:
        if dt not in tickers_mentioned:
            tickers_mentioned.insert(0, dt)
    ticker_data = {}

    for t in tickers_mentioned[:3]:  # Limit to 3 tickers
        with _lock:
            # Search aliases
            alias_set = {t}
            for alias in _chat_aliases.get(t, []):
                alias_set.add(alias.upper())
            for eid, em in entity_mentions.items():
                if eid.upper() in alias_set:
                    vol = em.get("volume", 0)
                    sent = em["sentiment_sum"] / em["sentiment_count"] if em.get("sentiment_count", 0) > 0 else None
                    sources = dict(em.get("sources", {})) if isinstance(em.get("sources"), dict) else {}
                    ticker_data[t] = {
                        "volume": vol, "sentiment": sent, "sources": sources,
                        "keywords": em.get("keywords", [])[:5],
                    }
                    break

    if ticker_data:
        for t, td in ticker_data.items():
            sent_str = f"{td['sentiment']:.0%}" if td['sentiment'] else "unknown"
            context_parts.append(
                f"Pipeline data for {t}: {td['volume']} mentions, "
                f"sentiment {sent_str}, sources: {', '.join(td['sources'].keys())}, "
                f"keywords: {', '.join(td['keywords'])}"
            )

    # 2. Try to get alpha data (price, technicals) — with timeout
    import concurrent.futures
    def _fetch_price(t):
        try:
            info, _hist = _get_yf_data(t)
            if info:
                price = info.get("currentPrice") or info.get("regularMarketPrice")
                prev = info.get("previousClose")
                change = round((price - prev) / prev * 100, 2) if price and prev else None
                return f"Market data for {t}: price ${price}, change {change}%, 52w range ${info.get('fiftyTwoWeekLow')}-${info.get('fiftyTwoWeekHigh')}"
        except Exception:
            return None
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(_fetch_price, t): t for t in tickers_mentioned[:2]}
        for future in concurrent.futures.as_completed(futures, timeout=8):
            try:
                result = future.result(timeout=5)
                if result:
                    context_parts.append(result)
            except Exception:
                pass

    # 3. ML prediction if available
    for t in tickers_mentioned[:2]:
        try:
            from stock_alpha.ml_scorer import MLScorer
            from stock_alpha.feature_store import FeatureStore
            scorer = MLScorer()
            if scorer.is_trained:
                store = FeatureStore()
                data = store.get_training_data(ticker=t)
                if data:
                    pred = scorer.predict(data[-1])
                    if pred:
                        context_parts.append(
                            f"ML forecast for {t}: {pred['direction_1d']} (1D), "
                            f"{pred['direction_5d']} (5D), confidence {pred['confidence']:.0%}, "
                            f"ML score {pred['ml_score']:.2f}"
                        )
        except Exception:
            pass

    # 4. Pipeline stats
    context_parts.append(
        f"Pipeline stats: {pipeline_stats['total_ingested']:,} docs ingested, "
        f"{pipeline_stats['total_normalized']:,} normalized, "
        f"{pipeline_stats['total_correlated']:,} correlated, "
        f"{len(entity_mentions):,} entities tracked"
    )

    context = "\n".join(context_parts) if context_parts else "No specific pipeline data found for this query."

    prompt = f"""You are Sentinel AI, the intelligent assistant for the Sentinel OSINT analytics platform. You have access to real-time pipeline data from multiple sources (HackerNews, Reddit, GitHub, SEC EDGAR, Yahoo Finance, Alpaca, FRED, etc.).

Answer the user's question using the pipeline context below. Be concise, data-driven, and actionable. If you have price/sentiment data, reference specific numbers. If you don't have data for what they're asking, say so and suggest what data sources might help.

PIPELINE CONTEXT:
{context}

USER QUESTION: {user_message}

Respond in 2-4 paragraphs. Use specific data points from the context. End with a brief actionable recommendation if relevant."""

    try:
        import anthropic
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"response": message.content[0].text}
    except ImportError:
        return {"response": f"Sentinel AI requires ANTHROPIC_API_KEY. Context gathered:\n\n{context}"}
    except Exception as e:
        log.warning("Chat API error: %s", e)
        return {"response": f"Analysis error. Pipeline context:\n\n{context}"}


# ---------------------------------------------------------------------------
# Watchlist ML Training
# ---------------------------------------------------------------------------

@app.post("/api/ml/train-watchlist")
def train_watchlist(request: dict = {}):
    """Backfill feature store for watchlist tickers and train ML model."""
    tickers = request.get("tickers", [])

    # Fall back to settings watchlist
    if not tickers:
        settings = db.get_settings() if hasattr(db, 'get_settings') else {}
        tickers = settings.get("watchlist", [
            "AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "SPY", "QQQ"
        ])

    log.info("Training ML on watchlist: %s", tickers)

    try:
        import yfinance as yf
        import pandas as pd
        import pandas_ta as ta
        import numpy as np
        from stock_alpha.feature_store import FeatureStore
        from stock_alpha.ml_scorer import MLScorer

        store = FeatureStore()
        total_snapshots = 0

        for ticker in tickers:
            try:
                hist = yf.Ticker(ticker).history(period="6mo")
                if hist is None or hist.empty:
                    continue
                hist.columns = [c.lower().replace(" ", "_") for c in hist.columns]

                hist["rsi"] = ta.rsi(hist["close"], length=14)
                macd = ta.macd(hist["close"])
                if macd is not None:
                    for col in macd.columns:
                        if col.startswith("MACDh"):
                            hist["macd_hist"] = macd[col]
                bb = ta.bbands(hist["close"])
                if bb is not None:
                    bb_cols = {c[:3]: c for c in bb.columns}
                    if "BBL" in bb_cols and "BBU" in bb_cols:
                        bw = bb[bb_cols["BBU"]] - bb[bb_cols["BBL"]]
                        hist["bb_pctb"] = (hist["close"] - bb[bb_cols["BBL"]]) / bw.replace(0, float("nan"))
                hist["sma_20"] = ta.sma(hist["close"], length=20)
                hist["atr"] = ta.atr(hist["high"], hist["low"], hist["close"], length=14)

                count = 0
                for i in range(30, len(hist)):
                    row = hist.iloc[i]
                    if pd.isna(row.get("rsi")):
                        continue
                    ts = hist.index[i]
                    sma20 = row.get("sma_20", 0)
                    sma20_dist = ((row["close"] - sma20) / sma20 * 100) if sma20 and sma20 > 0 else 0
                    atr_pct = (row.get("atr", 0) / row["close"] * 100) if row["close"] > 0 and not pd.isna(row.get("atr")) else 0

                    store.snapshot(
                        ticker=ticker,
                        timestamp=ts.to_pydatetime().replace(tzinfo=timezone.utc),
                        price=float(row["close"]),
                        volume=float(row.get("volume", 0)),
                        rsi=float(row.get("rsi", 50)),
                        macd_histogram=float(row.get("macd_hist", 0) if not pd.isna(row.get("macd_hist")) else 0),
                        bb_pctb=float(row.get("bb_pctb", 0.5) if not pd.isna(row.get("bb_pctb")) else 0.5),
                        sma20_distance_pct=float(sma20_dist),
                        atr_pct=float(atr_pct),
                        sentiment_score=0.5,
                        svc_value=0.0,
                        correlation_confidence=0.3,
                    )
                    count += 1
                total_snapshots += count
                log.info("  %s: %d snapshots", ticker, count)
            except Exception as e:
                log.warning("  %s: backfill failed: %s", ticker, e)

        # Fill missing feature columns with defaults
        import sqlite3
        defaults = {
            "price_change_1d_pct": 0.0, "volume_z_score": 0.0, "sma50_distance_pct": 0.0,
            "obv_slope": 0.0, "sentiment_velocity": 0.0, "sentiment_acceleration": 0.0,
            "mention_volume": 0, "source_count": 1, "vwap_distance_pct": 0.0,
            "fvg_bias": 0.0, "volume_profile_poc_distance_pct": 0.0,
            "cumulative_delta_normalized": 0.0, "imbalance_ratio": 0.0,
            "insider_score": 0.0, "macro_regime_score": 0.0, "sentiment_dispersion": 0.0,
            "hour_sin": 0.0, "hour_cos": 1.0, "day_of_week": 2,
            "news_recency_score": 0.0, "source_weighted_sentiment": 0.5,
            "volume_momentum": 0.0, "cross_sector_relative": 0.0,
        }
        conn = sqlite3.connect(store._db_path)
        for col, val in defaults.items():
            try:
                conn.execute(f"UPDATE snapshots SET {col} = ? WHERE {col} IS NULL", (val,))
            except Exception:
                pass
        conn.commit()
        conn.close()

        # Label with forward returns
        store.label_with_returns()

        # Train
        scorer = MLScorer()
        stats = store.get_stats()
        if stats.get("labeled_snapshots", 0) >= 50:
            result = scorer.train(store)
            return {
                "status": "trained",
                "tickers": tickers,
                "snapshots": total_snapshots,
                "labeled": stats.get("labeled_snapshots", 0),
                "accuracy_1d": result.get("accuracy_1d"),
                "accuracy_5d": result.get("accuracy_5d"),
            }
        else:
            return {
                "status": "insufficient_data",
                "tickers": tickers,
                "snapshots": total_snapshots,
                "labeled": stats.get("labeled_snapshots", 0),
            }
    except Exception as e:
        log.error("Watchlist training failed: %s", e, exc_info=True)
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    # Restore persisted state from SQLite before starting consumers
    try:
        _restore_state_from_db()
    except Exception as e:
        log.warning("Failed to restore state from DB (starting fresh): %s", e)

    # Start background Kafka consumers
    t1 = threading.Thread(target=_consume_raw_topics, daemon=True)
    t1.start()
    t2 = threading.Thread(target=_consume_signals, daemon=True)
    t2.start()
    t3 = threading.Thread(target=_consume_predictions, daemon=True)
    t3.start()
    log.info("Sentinel API started — Kafka consumers running")


@app.on_event("shutdown")
async def shutdown():
    """Flush final state to SQLite on shutdown."""
    try:
        with _lock:
            db.save_stats(pipeline_stats, topic_counts)
        log.info("Final stats saved to DB")
    except Exception as e:
        log.warning("Failed to save final stats: %s", e)
    db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8000, reload=False)
