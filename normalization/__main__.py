"""
Normalization layer entrypoint.

Consumes OsintDocuments from all raw Kafka topics, runs them through
the normalization pipeline (language → NER → dedup → PII), and publishes
to osint.normalized. Failed documents go to osint.deadletter.

Usage:
    python -m normalization

Sync consumer (not async) because the pipeline is CPU-bound (NLP inference).
Scale by running multiple instances in the same consumer group.
"""

import logging
import signal
import time

from connectors.kafka_publisher import KafkaPublisher, get_consumer, wait_for_bus

try:
    from confluent_kafka import KafkaError
except ImportError:
    KafkaError = None
from normalization.config import NormalizationConfig, load_config_from_env
from normalization.pipeline import NormalizationPipeline
from schemas.document import OsintDocument

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("normalization")

# Graceful shutdown flag
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

    wait_for_kafka(NormalizationConfig.KAFKA_BOOTSTRAP)

    # Initialize pipeline (loads models)
    pipeline = NormalizationPipeline()
    pipeline.setup()

    # Kafka consumer
    consumer = get_consumer({
        "bootstrap.servers": NormalizationConfig.KAFKA_BOOTSTRAP,
        "group.id": NormalizationConfig.CONSUMER_GROUP,
        "auto.offset.reset": NormalizationConfig.AUTO_OFFSET_RESET,
        "enable.auto.commit": False,
        "max.poll.interval.ms": 300000,  # 5 min for slow NLP
    })
    consumer.subscribe(NormalizationConfig.INPUT_TOPICS)

    # Kafka publisher
    publisher = KafkaPublisher(
        NormalizationConfig.KAFKA_BOOTSTRAP,
        client_id="normalization-pipeline",
    )

    log.info("=" * 60)
    log.info("Normalization Pipeline running")
    log.info("  Input topics:  %s", NormalizationConfig.INPUT_TOPICS)
    log.info("  Output topic:  %s", NormalizationConfig.OUTPUT_TOPIC)
    log.info("  Dead letter:   %s", NormalizationConfig.DEADLETTER_TOPIC)
    log.info("  Consumer group: %s", NormalizationConfig.CONSUMER_GROUP)
    log.info("=" * 60)

    processed_count = 0
    error_count = 0

    try:
        while _running:
            msg = consumer.poll(timeout=NormalizationConfig.MAX_POLL_TIMEOUT)

            if msg is None:
                continue

            if msg.error():
                if KafkaError and msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                log.error("Consumer error: %s", msg.error())
                continue

            # Deserialize
            try:
                doc = OsintDocument.model_validate_json(msg.value())
            except Exception as e:
                log.warning(
                    "Failed to deserialize message from %s: %s",
                    msg.topic(), e,
                )
                error_count += 1
                consumer.commit(message=msg)
                continue

            # Process through pipeline
            result = pipeline.process(doc)

            if result.success:
                publisher.publish(
                    NormalizationConfig.OUTPUT_TOPIC,
                    result.doc.to_kafka_key(),
                    result.doc.to_kafka_value(),
                )
            else:
                publisher.publish(
                    NormalizationConfig.DEADLETTER_TOPIC,
                    result.doc.to_kafka_key(),
                    result.doc.to_kafka_value(),
                )
                error_count += 1

            # Commit after processing (at-least-once semantics)
            consumer.commit(message=msg)
            processed_count += 1

            if processed_count % 100 == 0:
                log.info(
                    "Progress: %d processed, %d errors | Kafka: %s",
                    processed_count, error_count, publisher.stats,
                )

    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        consumer.close()
        publisher.flush(timeout=10.0)
        pipeline.teardown()
        log.info(
            "Final stats: %d processed, %d errors | Kafka: %s",
            processed_count, error_count, publisher.stats,
        )
        log.info("Normalization Pipeline shutdown complete.")


if __name__ == "__main__":
    main()
