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

    for eid in entities_to_track:
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

    with _lock:
        # Try various key forms for the ticker
        em = (
            entity_mentions.get(ticker)
            or entity_mentions.get(ticker.upper())
            or entity_mentions.get("$" + ticker)
        )
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
        for sig in reversed(signals):
            sig_ticker = (sig.get("ticker") or "").upper()
            if sig_ticker == ticker:
                signal_data = {
                    "confidence": sig.get("confidence"),
                    "type": sig.get("type"),
                    "headline": sig.get("headline"),
                    "sources": list(sig.get("sources", {}).keys()) if isinstance(sig.get("sources"), dict) else [],
                }
                break

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
            # Map 0-1 sentiment to -1..+1
            raw_parts["sentiment"] = (sentiment_data["score"] - 0.5) * 2

        # Technical component
        if technicals_snapshot and technicals_snapshot.get("rsi") is not None:
            components.append("technical")
            # Simple tech score from RSI and MACD
            rsi_val = technicals_snapshot["rsi"]
            tech_s = 0.0
            if rsi_val < 30:
                tech_s += 0.5
            elif rsi_val > 70:
                tech_s -= 0.5
            macd_h = (technicals_snapshot.get("macd") or {}).get("histogram")
            if macd_h is not None:
                tech_s += max(-1.0, min(1.0, macd_h * 5))
            raw_parts["technical"] = max(-1.0, min(1.0, tech_s / 2))

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
):
    """Data for the Product Innovation dashboard page."""
    import math

    with _lock:
        # ----- channels: doc counts per source -----
        channels: dict[str, int] = {}
        for src in ("reddit", "hacker_news", "trustpilot", "amazon", "producthunt"):
            channels[src] = pipeline_stats["sources"].get(src, 0)

        # ----- collect entities, optionally filtered by source -----
        filtered_entities: list[dict] = []
        for em in entity_mentions.values():
            if source:
                src_dict = em.get("sources", {})
                if isinstance(src_dict, dict) and src_dict.get(source, 0) > 0:
                    filtered_entities.append(em)
            else:
                filtered_entities.append(em)

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
        for topic_key in _PRODUCT_TOPICS:
            for doc in recent_docs.get(topic_key, []):
                doc_source = doc.get("source", "")
                if source and doc_source != source:
                    continue
                recent_insights.append({
                    "title": (doc.get("title") or "")[:200],
                    "source": doc_source,
                    "content": (doc.get("content_text") or "")[:300],
                    "url": doc.get("url"),
                    "created_at": doc.get("created_at") or doc.get("ingested_at"),
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
