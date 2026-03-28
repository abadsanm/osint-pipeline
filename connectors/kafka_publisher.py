"""
Shared message publisher used by all OSINT connectors.

Supports two backends controlled by MESSAGE_BUS env var:
  MESSAGE_BUS=sqlite  (default) — SQLite WAL-mode, no Docker needed
  MESSAGE_BUS=kafka   — Kafka via confluent_kafka (requires Docker)
"""

import logging
import os

log = logging.getLogger(__name__)


def _get_bus_mode() -> str:
    return os.environ.get("MESSAGE_BUS", "sqlite").lower()


class KafkaPublisher:
    """Publisher that auto-selects SQLite or Kafka backend.

    The interface is identical regardless of backend:
        pub = KafkaPublisher(bootstrap_servers, client_id="my-connector")
        pub.publish(topic, key, value)
        pub.flush()
    """

    def __init__(self, bootstrap_servers: str = "", client_id: str = "osint-connector"):
        self._mode = _get_bus_mode()

        if self._mode == "kafka":
            from confluent_kafka import Producer
            self._producer = Producer({
                "bootstrap.servers": bootstrap_servers,
                "client.id": client_id,
                "acks": "all",
                "enable.idempotence": True,
                "max.in.flight.requests.per.connection": 5,
                "compression.type": "lz4",
                "linger.ms": 50,
                "batch.size": 65536,
            })
        else:
            from connectors.message_bus import SQLitePublisher
            self._producer = SQLitePublisher(client_id=client_id)

        self._delivery_count = 0
        self._error_count = 0

    def _on_delivery(self, err, msg):
        if err:
            log.error("Kafka delivery failed: %s", err)
            self._error_count += 1
        else:
            self._delivery_count += 1

    def publish(self, topic: str, key: str, value: str):
        if self._mode == "kafka":
            self._producer.produce(
                topic=topic,
                key=key.encode("utf-8"),
                value=value.encode("utf-8"),
                callback=self._on_delivery,
            )
            self._producer.poll(0)
        else:
            try:
                self._producer.publish(topic, key, value)
                self._delivery_count += 1
            except Exception as e:
                log.error("SQLite publish failed: %s", e)
                self._error_count += 1

    def flush(self, timeout: float = 10.0):
        if self._mode == "kafka":
            remaining = self._producer.flush(timeout)
            if remaining > 0:
                log.warning("Kafka flush: %d messages still in queue", remaining)
        # SQLite writes are synchronous — no flush needed

    @property
    def stats(self) -> dict:
        return {
            "delivered": self._delivery_count,
            "errors": self._error_count,
        }


def get_consumer(config: dict):
    """Create a consumer using the active backend.

    Args:
        config: dict with keys like 'bootstrap.servers', 'group.id',
                'auto.offset.reset', 'enable.auto.commit'

    Returns a consumer with .subscribe(), .poll(), .commit(), .close() methods.
    """
    mode = _get_bus_mode()
    if mode == "kafka":
        from confluent_kafka import Consumer
        return Consumer(config)
    else:
        from connectors.message_bus import SQLiteConsumer
        return SQLiteConsumer(config)


def wait_for_bus(bootstrap_servers: str = "", timeout: int = 5) -> bool:
    """Wait for the message bus to be ready.

    For SQLite: always ready immediately.
    For Kafka: waits for broker to be reachable.
    """
    mode = _get_bus_mode()
    if mode != "kafka":
        log.info("SQLite message bus ready")
        return True

    from confluent_kafka.admin import AdminClient
    log.info("Waiting for Kafka at %s...", bootstrap_servers)
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.topics:
                log.info("Kafka is ready — %d topics found", len(metadata.topics))
                return True
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


def get_poll_multiplier() -> float:
    """Read POLL_INTERVAL_MULTIPLIER from env (default 1.0).

    Set POLL_INTERVAL_MULTIPLIER=5 for laptop use — all connectors poll 5x
    less frequently, reducing CPU/network load.
    """
    return float(os.environ.get("POLL_INTERVAL_MULTIPLIER", "1.0"))


def apply_poll_multiplier(config_cls, *attrs: str) -> None:
    """Multiply the named numeric attributes on a config class by the
    global POLL_INTERVAL_MULTIPLIER."""
    m = get_poll_multiplier()
    if m == 1.0:
        return
    for attr in attrs:
        orig = getattr(config_cls, attr, None)
        if orig is not None:
            setattr(config_cls, attr, orig * m)
    log.info("Poll multiplier %.1fx applied to %s: %s", m, config_cls.__name__, ", ".join(attrs))
