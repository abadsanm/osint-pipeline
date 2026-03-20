"""
Shared Kafka publisher used by all OSINT connectors.
"""

import logging
from confluent_kafka import Producer

log = logging.getLogger(__name__)


class KafkaPublisher:
    """Thin wrapper around confluent_kafka.Producer with delivery callbacks."""

    def __init__(self, bootstrap_servers: str, client_id: str = "osint-connector"):
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
        self._delivery_count = 0
        self._error_count = 0

    def _on_delivery(self, err, msg):
        if err:
            log.error("Kafka delivery failed: %s", err)
            self._error_count += 1
        else:
            self._delivery_count += 1

    def publish(self, topic: str, key: str, value: str):
        self._producer.produce(
            topic=topic,
            key=key.encode("utf-8"),
            value=value.encode("utf-8"),
            callback=self._on_delivery,
        )
        self._producer.poll(0)

    def flush(self, timeout: float = 10.0):
        remaining = self._producer.flush(timeout)
        if remaining > 0:
            log.warning("Kafka flush: %d messages still in queue", remaining)

    @property
    def stats(self) -> dict:
        return {
            "delivered": self._delivery_count,
            "errors": self._error_count,
        }
