"""
SQLite persistence layer for the Sentinel API.

Replaces volatile in-memory deques/dicts with durable storage that survives
restarts.  The in-memory structures remain the primary source for API reads
(fast); the DB is the persistence backup that restores state on startup.

Database file: data/sentinel.db  (auto-creates the data/ directory)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("sentinel-db")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class SentinelDB:
    """SQLite-backed storage for Sentinel API state."""

    def __init__(self, db_path: str = "data/sentinel.db"):
        # Resolve relative to project root
        if not os.path.isabs(db_path):
            db_path = str(_PROJECT_ROOT / db_path)

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()
        log.info("SentinelDB opened: %s", db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self):
        cur = self._conn.cursor()

        cur.executescript("""
            CREATE TABLE IF NOT EXISTS entity_mentions (
                id          TEXT PRIMARY KEY,
                label       TEXT,
                volume      INTEGER DEFAULT 0,
                sentiment_sum   REAL DEFAULT 0.0,
                sentiment_count INTEGER DEFAULT 0,
                sources     TEXT DEFAULT '{}',
                keywords    TEXT DEFAULT '[]',
                sample_docs TEXT DEFAULT '[]',
                last_seen   TEXT,
                entity_type TEXT DEFAULT '',
                created_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_entity_volume
                ON entity_mentions(volume DESC);

            CREATE TABLE IF NOT EXISTS signals (
                id          TEXT PRIMARY KEY,
                type        TEXT,
                ticker      TEXT,
                headline    TEXT,
                source      TEXT,
                timestamp   TEXT,
                confidence  REAL,
                sources     TEXT DEFAULT '{}',
                volume      INTEGER DEFAULT 0,
                signal_type TEXT DEFAULT '',
                keywords    TEXT DEFAULT '[]',
                sample_docs TEXT DEFAULT '[]',
                created_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_signals_ts
                ON signals(timestamp DESC);

            CREATE TABLE IF NOT EXISTS alerts (
                id          TEXT PRIMARY KEY,
                type        TEXT,
                priority    TEXT,
                ticker      TEXT,
                headline    TEXT,
                message     TEXT,
                timestamp   TEXT,
                read        INTEGER DEFAULT 0,
                created_at  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_ts_read
                ON alerts(timestamp DESC, read);

            CREATE TABLE IF NOT EXISTS backtest_records (
                signal_id       TEXT PRIMARY KEY,
                ticker          TEXT,
                direction       TEXT,
                signal_score    REAL,
                confidence      REAL,
                price_at_signal REAL,
                timestamp       TEXT,
                price_1d        REAL,
                price_5d        REAL,
                price_10d       REAL,
                correct_1d      INTEGER,
                correct_5d      INTEGER,
                correct_10d     INTEGER
            );

            CREATE TABLE IF NOT EXISTS pipeline_stats (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS research_items (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                entity_label TEXT NOT NULL,
                action TEXT NOT NULL,
                source_context TEXT,
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'active',
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_analysis TEXT,
                alert_count INTEGER DEFAULT 0,
                sentiment_at_creation REAL,
                current_sentiment REAL,
                mention_count_at_creation INTEGER DEFAULT 0,
                current_mention_count INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_research_status
                ON research_items(status, priority);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Entity Mentions
    # ------------------------------------------------------------------

    def upsert_entity(self, eid: str, data: dict):
        """Insert or replace an entity mention record."""
        now = datetime.now(timezone.utc).isoformat()
        sources = data.get("sources", {})
        if not isinstance(sources, str):
            sources = json.dumps(dict(sources) if hasattr(sources, "items") else sources)
        keywords = data.get("keywords", [])
        if not isinstance(keywords, str):
            keywords = json.dumps(keywords)
        sample_docs = data.get("sample_docs", [])
        if not isinstance(sample_docs, str):
            sample_docs = json.dumps(sample_docs)

        self._conn.execute(
            """INSERT OR REPLACE INTO entity_mentions
               (id, label, volume, sentiment_sum, sentiment_count,
                sources, keywords, sample_docs, last_seen, entity_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(
                   (SELECT created_at FROM entity_mentions WHERE id = ?), ?))""",
            (
                eid,
                data.get("label", eid),
                data.get("volume", 0),
                data.get("sentiment_sum", 0.0),
                data.get("sentiment_count", 0),
                sources,
                keywords,
                sample_docs,
                data.get("last_seen"),
                data.get("entity_type", ""),
                eid,
                now,
            ),
        )
        self._conn.commit()

    def get_entities(self, limit: int = 100, source_filter: Optional[str] = None) -> list[dict]:
        """Return top entities by volume, optionally filtered by source."""
        rows = self._conn.execute(
            "SELECT * FROM entity_mentions ORDER BY volume DESC LIMIT ?",
            (limit,),
        ).fetchall()

        results = [self._row_to_entity(r) for r in rows]
        if source_filter:
            results = [
                e for e in results
                if source_filter in e.get("sources", {})
            ]
        return results

    def get_entity(self, eid: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM entity_mentions WHERE id = ?", (eid,)
        ).fetchone()
        return self._row_to_entity(row) if row else None

    def search_entities(self, query: str, limit: int = 10) -> list[dict]:
        """Search entities by id or label (case-insensitive LIKE)."""
        pattern = f"%{query}%"
        rows = self._conn.execute(
            """SELECT * FROM entity_mentions
               WHERE id LIKE ? OR label LIKE ?
               ORDER BY volume DESC LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def _row_to_entity(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["sources"] = json.loads(d.get("sources") or "{}")
        d["keywords"] = json.loads(d.get("keywords") or "[]")
        d["sample_docs"] = json.loads(d.get("sample_docs") or "[]")
        return d

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def add_signal(self, signal: dict):
        """Insert a signal (ignores duplicates by id)."""
        now = datetime.now(timezone.utc).isoformat()
        sources = signal.get("sources", {})
        if not isinstance(sources, str):
            sources = json.dumps(dict(sources) if hasattr(sources, "items") else sources)
        keywords = signal.get("keywords", [])
        if not isinstance(keywords, str):
            keywords = json.dumps(keywords)
        sample_docs = signal.get("sampleDocs", signal.get("sample_docs", []))
        if not isinstance(sample_docs, str):
            sample_docs = json.dumps(sample_docs)

        self._conn.execute(
            """INSERT OR IGNORE INTO signals
               (id, type, ticker, headline, source, timestamp,
                confidence, sources, volume, signal_type, keywords, sample_docs, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                signal.get("id", ""),
                signal.get("type", ""),
                signal.get("ticker", ""),
                signal.get("headline", ""),
                signal.get("source", ""),
                signal.get("timestamp", ""),
                signal.get("confidence", 0.0),
                sources,
                signal.get("volume", 0),
                signal.get("signal_type", ""),
                keywords,
                sample_docs,
                now,
            ),
        )
        self._conn.commit()

    def get_signals(self, limit: int = 50, since: Optional[str] = None) -> list[dict]:
        """Return recent signals, newest first."""
        if since:
            rows = self._conn.execute(
                """SELECT * FROM signals
                   WHERE timestamp >= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (since, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["sources"] = json.loads(d.get("sources") or "{}")
            d["keywords"] = json.loads(d.get("keywords") or "[]")
            d["sample_docs"] = json.loads(d.get("sample_docs") or "[]")
            # Map back to sampleDocs for API compatibility
            d["sampleDocs"] = d.pop("sample_docs")
            results.append(d)
        return results

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def add_alert(self, alert: dict):
        """Insert an alert (ignores duplicates by id)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR IGNORE INTO alerts
               (id, type, priority, ticker, headline, message, timestamp, read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                alert.get("id", ""),
                alert.get("type", ""),
                alert.get("priority", ""),
                alert.get("ticker", ""),
                alert.get("headline", ""),
                alert.get("message", ""),
                alert.get("timestamp", ""),
                1 if alert.get("read") else 0,
                now,
            ),
        )
        self._conn.commit()

    def get_alerts(self, limit: int = 50, unread_only: bool = False) -> list[dict]:
        """Return recent alerts, newest first."""
        if unread_only:
            rows = self._conn.execute(
                """SELECT * FROM alerts WHERE read = 0
                   ORDER BY timestamp DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            d["read"] = bool(d.get("read", 0))
            results.append(d)
        return results

    def mark_alert_read(self, alert_id: str):
        self._conn.execute(
            "UPDATE alerts SET read = 1 WHERE id = ?", (alert_id,)
        )
        self._conn.commit()

    def mark_all_alerts_read(self):
        self._conn.execute("UPDATE alerts SET read = 1 WHERE read = 0")
        self._conn.commit()

    # ------------------------------------------------------------------
    # Backtest Records
    # ------------------------------------------------------------------

    def add_backtest_record(self, record: dict):
        """Insert a backtest record (ignores duplicates by signal_id)."""
        ts = record.get("timestamp", "")
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()

        self._conn.execute(
            """INSERT OR IGNORE INTO backtest_records
               (signal_id, ticker, direction, signal_score, confidence,
                price_at_signal, timestamp, price_1d, price_5d, price_10d,
                correct_1d, correct_5d, correct_10d)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.get("signal_id", ""),
                record.get("ticker", ""),
                record.get("direction", ""),
                record.get("signal_score", 0.0),
                record.get("confidence", 0.0),
                record.get("price_at_signal"),
                ts,
                record.get("price_1d"),
                record.get("price_5d"),
                record.get("price_10d"),
                _bool_to_int(record.get("correct_1d")),
                _bool_to_int(record.get("correct_5d")),
                _bool_to_int(record.get("correct_10d")),
            ),
        )
        self._conn.commit()

    def get_backtest_records(self, limit: int = 100) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM backtest_records ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["correct_1d"] = _int_to_bool(d.get("correct_1d"))
            d["correct_5d"] = _int_to_bool(d.get("correct_5d"))
            d["correct_10d"] = _int_to_bool(d.get("correct_10d"))
            results.append(d)
        return results

    def update_backtest_record(self, signal_id: str, updates: dict):
        """Update specific fields on a backtest record."""
        if not updates:
            return
        allowed = {
            "price_1d", "price_5d", "price_10d",
            "correct_1d", "correct_5d", "correct_10d",
        }
        filtered = {}
        for k, v in updates.items():
            if k in allowed:
                if k.startswith("correct_"):
                    v = _bool_to_int(v)
                filtered[k] = v

        if not filtered:
            return

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [signal_id]
        self._conn.execute(
            f"UPDATE backtest_records SET {set_clause} WHERE signal_id = ?",
            values,
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Pipeline Stats
    # ------------------------------------------------------------------

    def save_stats(self, stats: dict, topic_counts: dict):
        """Persist pipeline_stats and topic_counts as JSON blobs."""
        # Ensure nested defaultdicts are serialisable
        stats_copy = dict(stats)
        if "sources" in stats_copy and hasattr(stats_copy["sources"], "items"):
            stats_copy["sources"] = dict(stats_copy["sources"])

        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_stats (key, value) VALUES (?, ?)",
            ("pipeline_stats", json.dumps(stats_copy)),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO pipeline_stats (key, value) VALUES (?, ?)",
            ("topic_counts", json.dumps(dict(topic_counts))),
        )
        self._conn.commit()

    def load_stats(self) -> tuple[dict, dict]:
        """Load persisted pipeline_stats and topic_counts.  Returns ({}, {}) if none."""
        stats: dict = {}
        topic_counts: dict = {}
        for key in ("pipeline_stats", "topic_counts"):
            row = self._conn.execute(
                "SELECT value FROM pipeline_stats WHERE key = ?", (key,)
            ).fetchone()
            if row:
                data = json.loads(row["value"])
                if key == "pipeline_stats":
                    stats = data
                else:
                    topic_counts = data
        return stats, topic_counts

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return row["value"]

    def set_setting(self, key: str, value: Any):
        serialized = json.dumps(value) if not isinstance(value, str) else value
        self._conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, serialized),
        )
        self._conn.commit()

    def get_all_settings(self) -> dict:
        rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        result = {}
        for r in rows:
            try:
                result[r["key"]] = json.loads(r["value"])
            except (json.JSONDecodeError, TypeError):
                result[r["key"]] = r["value"]
        return result

    # ------------------------------------------------------------------
    # Research Items
    # ------------------------------------------------------------------

    def add_research_item(self, item: dict):
        """Insert a research item."""
        self._conn.execute(
            """INSERT OR IGNORE INTO research_items
               (id, entity_id, entity_label, action, source_context, priority,
                status, notes, created_at, updated_at, last_analysis,
                alert_count, sentiment_at_creation, current_sentiment,
                mention_count_at_creation, current_mention_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.get("id", ""),
                item.get("entity_id", ""),
                item.get("entity_label", ""),
                item.get("action", ""),
                item.get("source_context", ""),
                item.get("priority", "medium"),
                item.get("status", "active"),
                item.get("notes", ""),
                item.get("created_at", ""),
                item.get("updated_at", ""),
                item.get("last_analysis"),
                item.get("alert_count", 0),
                item.get("sentiment_at_creation"),
                item.get("current_sentiment"),
                item.get("mention_count_at_creation", 0),
                item.get("current_mention_count", 0),
            ),
        )
        self._conn.commit()

    def get_research_items(self, status: str = None, limit: int = 50) -> list[dict]:
        """Return research items, optionally filtered by status."""
        if status:
            rows = self._conn.execute(
                """SELECT * FROM research_items
                   WHERE status = ?
                   ORDER BY
                       CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                            WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                       updated_at DESC
                   LIMIT ?""",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM research_items
                   ORDER BY
                       CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                            WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                       updated_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_research_item(self, item_id: str, updates: dict):
        """Update specific fields on a research item."""
        if not updates:
            return
        allowed = {
            "entity_label", "action", "source_context", "priority", "status",
            "notes", "updated_at", "last_analysis", "alert_count",
            "current_sentiment", "current_mention_count",
        }
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return

        set_clause = ", ".join(f"{k} = ?" for k in filtered)
        values = list(filtered.values()) + [item_id]
        self._conn.execute(
            f"UPDATE research_items SET {set_clause} WHERE id = ?",
            values,
        )
        self._conn.commit()

    def delete_research_item(self, item_id: str):
        """Delete a research item by id."""
        self._conn.execute("DELETE FROM research_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def get_research_item(self, item_id: str) -> Optional[dict]:
        """Return a single research item by id, or None."""
        row = self._conn.execute(
            "SELECT * FROM research_items WHERE id = ?", (item_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_watched_entities(self) -> set[str]:
        """Return entity_ids of all active research items (for alert sensitivity)."""
        rows = self._conn.execute(
            "SELECT entity_id FROM research_items WHERE status = 'active'"
        ).fetchall()
        return {r["entity_id"].upper() for r in rows}

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, max_age_days: int = 30):
        """Remove records older than max_age_days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        tables_ts_col = [
            ("entity_mentions", "last_seen"),
            ("signals", "timestamp"),
            ("alerts", "timestamp"),
            ("backtest_records", "timestamp"),
            ("research_items", "updated_at"),
        ]
        total = 0
        for table, col in tables_ts_col:
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE {col} < ?", (cutoff,)
            )
            total += cur.rowcount
        self._conn.commit()
        if total:
            log.info("Cleanup: removed %d records older than %d days", total, max_age_days)
        return total

    def close(self):
        if self._conn:
            self._conn.close()
            log.info("SentinelDB closed")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _bool_to_int(val) -> Optional[int]:
    if val is None:
        return None
    return 1 if val else 0


def _int_to_bool(val) -> Optional[bool]:
    if val is None:
        return None
    return bool(val)
