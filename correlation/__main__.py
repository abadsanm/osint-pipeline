"""
Cross-correlation engine entrypoint.

Consumes normalized OsintDocuments from osint.normalized, tracks entity
mentions in sliding time windows, and emits CorrelatedSignals when
multi-source convergence or anomalies are detected.

Usage:
    python -m correlation

Sync consumer with periodic window evaluation. Scale by running multiple
instances in the same consumer group (Kafka partitions provide parallelism).
"""

import logging
import signal
import time

from connectors.kafka_publisher import KafkaPublisher, get_consumer, wait_for_bus

try:
    from confluent_kafka import KafkaError
except ImportError:
    KafkaError = None
from correlation.config import CorrelationConfig, load_config_from_env
from correlation.engine import CrossCorrelationEngine
from correlation.router import route_signal
from schemas.document import OsintDocument

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("correlation")

_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received")
    _running = False


def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def main():
    global _running

    load_config_from_env()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    wait_for_kafka(CorrelationConfig.KAFKA_BOOTSTRAP)

    # Initialize engine
    engine = CrossCorrelationEngine()

    # Kafka consumer
    consumer = get_consumer({
        "bootstrap.servers": CorrelationConfig.KAFKA_BOOTSTRAP,
        "group.id": CorrelationConfig.CONSUMER_GROUP,
        "auto.offset.reset": CorrelationConfig.AUTO_OFFSET_RESET,
        "enable.auto.commit": False,
        "max.poll.interval.ms": 300000,
    })
    consumer.subscribe([CorrelationConfig.INPUT_TOPIC])

    # Kafka publisher
    publisher = KafkaPublisher(
        CorrelationConfig.KAFKA_BOOTSTRAP,
        client_id="correlation-engine",
    )

    log.info("=" * 60)
    log.info("Cross-Correlation Engine running")
    log.info("  Input:       %s", CorrelationConfig.INPUT_TOPIC)
    log.info("  Output:      %s", CorrelationConfig.OUTPUT_TOPIC)
    log.info("  Window:      %d min (slide every %d min)",
             CorrelationConfig.WINDOW_SIZE_MINUTES,
             CorrelationConfig.WINDOW_SLIDE_MINUTES)
    log.info("  Min mentions: %d", CorrelationConfig.MIN_MENTIONS_TO_EMIT)
    log.info("  Consumer group: %s", CorrelationConfig.CONSUMER_GROUP)
    log.info("=" * 60)

    ingested_count = 0
    signals_emitted = 0
    last_evaluation = time.time()
    slide_seconds = CorrelationConfig.WINDOW_SLIDE_MINUTES * 60

    try:
        while _running:
            msg = consumer.poll(timeout=CorrelationConfig.MAX_POLL_TIMEOUT)

            if msg is not None and not msg.error():
                try:
                    doc = OsintDocument.model_validate_json(msg.value())
                    engine.ingest_document(doc)
                    ingested_count += 1
                except Exception as e:
                    log.warning("Failed to process message: %s", e)

                consumer.commit(message=msg)

            elif msg is not None and msg.error():
                if KafkaError and msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("Consumer error: %s", msg.error())

            # Periodic window evaluation
            now = time.time()
            if now - last_evaluation >= slide_seconds:
                signals = engine.evaluate_windows()

                for signal_obj in signals:
                    topics = route_signal(signal_obj)
                    for topic in topics:
                        publisher.publish(
                            topic,
                            signal_obj.to_kafka_key(),
                            signal_obj.to_kafka_value(),
                        )
                    signals_emitted += 1

                if signals:
                    publisher.flush(timeout=5.0)

                last_evaluation = now

                if ingested_count > 0 and ingested_count % 100 == 0:
                    log.info(
                        "Stats: %d ingested, %d signals emitted | Engine: %s | Kafka: %s",
                        ingested_count, signals_emitted,
                        engine.stats, publisher.stats,
                    )

    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")

        # Final evaluation before shutdown
        signals = engine.evaluate_windows()
        for signal_obj in signals:
            topics = route_signal(signal_obj)
            for topic in topics:
                publisher.publish(
                    topic, signal_obj.to_kafka_key(), signal_obj.to_kafka_value(),
                )
            signals_emitted += 1

        consumer.close()
        publisher.flush(timeout=10.0)
        log.info(
            "Final stats: %d ingested, %d signals emitted | Kafka: %s",
            ingested_count, signals_emitted, publisher.stats,
        )
        log.info("Cross-Correlation Engine shutdown complete.")


if __name__ == "__main__":
    main()
