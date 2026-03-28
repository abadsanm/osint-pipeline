"""
Product Ideation Engine entrypoint.

Consumes CorrelatedSignals from osint.correlated.product_ideation,
accumulates review data, and periodically produces ProductIdeationReports
with gap analysis, feature requests, and question clusters.

Usage:
    python -m product_ideation
"""

import logging
import signal
import time

from connectors.kafka_publisher import KafkaPublisher, get_consumer, wait_for_bus

try:
    from confluent_kafka import KafkaError
except ImportError:
    KafkaError = None
from product_ideation.config import ProductIdeationConfig, load_config_from_env
from product_ideation.engine import ProductIdeationEngine
from schemas.signal import CorrelatedSignal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("product_ideation")

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

    wait_for_kafka(ProductIdeationConfig.KAFKA_BOOTSTRAP)

    # Initialize engine
    engine = ProductIdeationEngine()
    engine.setup()

    # Kafka consumer
    consumer = get_consumer({
        "bootstrap.servers": ProductIdeationConfig.KAFKA_BOOTSTRAP,
        "group.id": ProductIdeationConfig.CONSUMER_GROUP,
        "auto.offset.reset": ProductIdeationConfig.AUTO_OFFSET_RESET,
        "enable.auto.commit": False,
        "max.poll.interval.ms": 600000,
    })
    consumer.subscribe([ProductIdeationConfig.INPUT_TOPIC])

    # Kafka publisher
    publisher = KafkaPublisher(
        ProductIdeationConfig.KAFKA_BOOTSTRAP,
        client_id="product-ideation-engine",
    )

    log.info("=" * 60)
    log.info("Product Ideation Engine running")
    log.info("  Input:    %s", ProductIdeationConfig.INPUT_TOPIC)
    log.info("  Output:   %s", ProductIdeationConfig.OUTPUT_TOPIC)
    log.info("  Consumer: %s", ProductIdeationConfig.CONSUMER_GROUP)
    log.info("=" * 60)

    ingested_count = 0
    reports_emitted = 0
    last_evaluation = time.time()

    try:
        while _running:
            msg = consumer.poll(timeout=ProductIdeationConfig.MAX_POLL_TIMEOUT)

            if msg is not None and not msg.error():
                try:
                    correlated = CorrelatedSignal.model_validate_json(msg.value())
                    engine.ingest_signal(correlated)
                    ingested_count += 1
                except Exception as e:
                    log.warning("Failed to process message: %s", e)

                consumer.commit(message=msg)

            elif msg is not None and msg.error():
                if KafkaError and msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("Consumer error: %s", msg.error())

            # Periodic evaluation
            now = time.time()
            if now - last_evaluation >= ProductIdeationConfig.EVAL_INTERVAL_SECONDS:
                reports = engine.evaluate()

                for report in reports:
                    publisher.publish(
                        ProductIdeationConfig.OUTPUT_TOPIC,
                        report.to_kafka_key(),
                        report.to_kafka_value(),
                    )
                    reports_emitted += 1

                if reports:
                    publisher.flush(timeout=5.0)

                last_evaluation = now

                if ingested_count > 0:
                    log.info(
                        "Stats: %d ingested, %d reports | Engine: %s | Kafka: %s",
                        ingested_count, reports_emitted,
                        engine.stats, publisher.stats,
                    )

    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")

        # Final evaluation
        reports = engine.evaluate()
        for report in reports:
            publisher.publish(
                ProductIdeationConfig.OUTPUT_TOPIC,
                report.to_kafka_key(),
                report.to_kafka_value(),
            )
            reports_emitted += 1

        consumer.close()
        publisher.flush(timeout=10.0)
        engine.teardown()
        log.info("Final: %d ingested, %d reports | Kafka: %s",
                 ingested_count, reports_emitted, publisher.stats)
        log.info("Product Ideation Engine shutdown complete.")


if __name__ == "__main__":
    main()
