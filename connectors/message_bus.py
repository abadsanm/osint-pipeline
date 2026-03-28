"""
SQLite-backed message bus — drop-in replacement for Kafka.

Provides Publisher and Consumer classes with the same interface as
confluent_kafka but backed by SQLite WAL-mode tables. Each "topic"
is a table with auto-incrementing IDs for ordered consumption.

Usage:
    # Publishing (same as KafkaPublisher)
    pub = SQLitePublisher(db_path, client_id="my-connector")
    pub.publish("tech.hn.stories", key, value)
    pub.flush()

    # Consuming
    consumer = SQLiteConsumer(db_path, group_id="my-group", topics=["tech.hn.stories"])
    while True:
        msg = consumer.poll(timeout=1.0)
        if msg:
            process(msg)
            consumer.commit(msg)

Environment variable MESSAGE_BUS controls backend:
    MESSAGE_BUS=sqlite  (default) — use SQLite
    MESSAGE_BUS=kafka   — use Kafka (legacy)
"""

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("message_bus")

# Default database path
_DEFAULT_DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "message_bus.db")


def get_bus_mode() -> str:
    """Return 'sqlite' or 'kafka' based on MESSAGE_BUS env var."""
    return os.environ.get("MESSAGE_BUS", "sqlite").lower()


class Message:
    """A message from the bus — mirrors confluent_kafka.Message interface."""

    def __init__(self, topic: str, key: str, value: bytes, offset: int, error: Optional[str] = None):
        self.topic = topic
        self._key = key
        self._value = value
        self.offset = offset
        self._error = error

    def error(self):
        return self._error

    def key(self):
        return self._key

    def value(self):
        return self._value


class SQLiteBus:
    """Shared SQLite connection manager with WAL mode for concurrent access."""

    _instances: dict[str, "SQLiteBus"] = {}
    _lock = threading.Lock()

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or _DEFAULT_DB_PATH
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        # Initialize on main thread
        self._get_conn()
        log.info("SQLite message bus: %s", self._db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self._db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=10000")
            self._local.conn = conn
        return self._local.conn

    @classmethod
    def get_instance(cls, db_path: str = "") -> "SQLiteBus":
        path = db_path or _DEFAULT_DB_PATH
        with cls._lock:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    def ensure_topic(self, topic: str):
        """Create the table for a topic if it doesn't exist."""
        table = _topic_to_table(topic)
        conn = self._get_conn()
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS [{table}] (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT,
                value TEXT,
                timestamp REAL DEFAULT (unixepoch('now','subsec'))
            )
        """)
        conn.commit()

    def publish(self, topic: str, key: str, value: str):
        """Write a message to a topic table."""
        table = _topic_to_table(topic)
        conn = self._get_conn()
        try:
            conn.execute(
                f"INSERT INTO [{table}] (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()
        except sqlite3.OperationalError:
            # Table might not exist yet
            self.ensure_topic(topic)
            conn.execute(
                f"INSERT INTO [{table}] (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def poll(self, topics: list[str], group_id: str, last_offsets: dict[str, int],
             max_messages: int = 1) -> list[tuple[str, int, str, bytes]]:
        """Poll for new messages across topics.

        Returns list of (topic, offset, key, value) tuples.
        """
        conn = self._get_conn()
        results = []
        for topic in topics:
            table = _topic_to_table(topic)
            last_id = last_offsets.get(topic, 0)
            try:
                rows = conn.execute(
                    f"SELECT id, key, value FROM [{table}] WHERE id > ? ORDER BY id LIMIT ?",
                    (last_id, max_messages),
                ).fetchall()
                for row_id, key, value in rows:
                    results.append((topic, row_id, key or "", (value or "").encode("utf-8")))
            except sqlite3.OperationalError:
                # Table doesn't exist yet — that's fine
                pass
        return results

    def get_latest_offset(self, topic: str) -> int:
        """Get the latest message ID for a topic."""
        table = _topic_to_table(topic)
        conn = self._get_conn()
        try:
            row = conn.execute(f"SELECT MAX(id) FROM [{table}]").fetchone()
            return row[0] or 0
        except sqlite3.OperationalError:
            return 0

    def list_topics(self) -> list[str]:
        """List all topic tables."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != '_offsets'"
        ).fetchall()
        return [_table_to_topic(r[0]) for r in rows]


class _OffsetStore:
    """Tracks consumer group offsets in SQLite."""

    def __init__(self, bus: SQLiteBus):
        self._bus = bus
        conn = bus._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _offsets (
                group_id TEXT,
                topic TEXT,
                offset_id INTEGER,
                PRIMARY KEY (group_id, topic)
            )
        """)
        conn.commit()

    def get(self, group_id: str, topic: str) -> int:
        conn = self._bus._get_conn()
        row = conn.execute(
            "SELECT offset_id FROM _offsets WHERE group_id=? AND topic=?",
            (group_id, topic),
        ).fetchone()
        return row[0] if row else 0

    def set(self, group_id: str, topic: str, offset_id: int):
        conn = self._bus._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO _offsets (group_id, topic, offset_id) VALUES (?, ?, ?)",
            (group_id, topic, offset_id),
        )
        conn.commit()


class SQLitePublisher:
    """Drop-in replacement for KafkaPublisher backed by SQLite."""

    def __init__(self, db_path: str = "", client_id: str = ""):
        self._bus = SQLiteBus.get_instance(db_path)
        self._client_id = client_id
        self._delivery_count = 0
        self._error_count = 0

    def publish(self, topic: str, key: str, value: str):
        try:
            self._bus.publish(topic, key, value)
            self._delivery_count += 1
        except Exception as e:
            log.error("SQLite publish failed: %s", e)
            self._error_count += 1

    def flush(self, timeout: float = 10.0):
        """No-op for SQLite (writes are synchronous)."""
        pass

    @property
    def stats(self) -> dict:
        return {
            "delivered": self._delivery_count,
            "errors": self._error_count,
        }


class SQLiteConsumer:
    """Drop-in replacement for confluent_kafka.Consumer backed by SQLite."""

    def __init__(self, config: dict):
        """Accept same config dict as confluent_kafka.Consumer.

        Relevant keys: group.id, auto.offset.reset
        """
        self._group_id = config.get("group.id", "default")
        self._auto_reset = config.get("auto.offset.reset", "earliest")
        self._bus = SQLiteBus.get_instance()
        self._offsets_store = _OffsetStore(self._bus)
        self._topics: list[str] = []
        self._last_offsets: dict[str, int] = {}
        self._auto_commit = config.get("enable.auto.commit", True)

    def subscribe(self, topics: list[str]):
        self._topics = topics
        # Ensure all topic tables exist
        for t in topics:
            self._bus.ensure_topic(t)
        # Load committed offsets
        for t in topics:
            committed = self._offsets_store.get(self._group_id, t)
            if committed > 0:
                self._last_offsets[t] = committed
            elif self._auto_reset == "latest":
                self._last_offsets[t] = self._bus.get_latest_offset(t)
            else:
                self._last_offsets[t] = 0

    def poll(self, timeout: float = 1.0) -> Optional[Message]:
        """Poll for a single message. Returns None if no messages available."""
        results = self._bus.poll(self._topics, self._group_id, self._last_offsets, max_messages=1)
        if not results:
            if timeout > 0:
                time.sleep(min(timeout, 0.1))  # Small sleep to avoid busy-wait
            return None
        topic, offset, key, value = results[0]
        self._last_offsets[topic] = offset
        if self._auto_commit:
            self._offsets_store.set(self._group_id, topic, offset)
        return Message(topic, key, value, offset)

    def commit(self, message=None):
        """Commit current offsets."""
        if message:
            self._offsets_store.set(self._group_id, message.topic, message.offset)
        else:
            for topic, offset in self._last_offsets.items():
                self._offsets_store.set(self._group_id, topic, offset)

    def close(self):
        """No-op for SQLite."""
        pass


def _topic_to_table(topic: str) -> str:
    """Convert topic name to valid SQLite table name."""
    return topic.replace(".", "_").replace("-", "_")


def _table_to_topic(table: str) -> str:
    """Convert table name back to topic name (best effort)."""
    return table.replace("_", ".")
