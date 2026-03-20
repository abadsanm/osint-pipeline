"""
Stock Alpha Engine entrypoint.

Consumes CorrelatedSignals from osint.correlated.stock_alpha,
processes them through FinBERT sentiment + SVC + technicals,
and publishes scored AlphaSignals.

Usage:
    python -m stock_alpha
"""

import json
import logging
import signal
import time

from confluent_kafka import Consumer, KafkaError

from connectors.kafka_publisher import KafkaPublisher
from stock_alpha.config import StockAlphaConfig, load_config_from_env
from stock_alpha.engine import StockAlphaEngine
from schemas.signal import CorrelatedSignal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("stock_alpha")

_running = True


def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received")
    _running = False


def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until Kafka broker is reachable."""
    from confluent_kafka.admin import AdminClient

    log.info("Waiting for Kafka at %s...", bootstrap_servers)
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    start = time.time()
    while time.time() - start < timeout:
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.topics:
                log.info("Kafka is ready — %d topics found", len(metadata.topics))
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


def main():
    global _running

    load_config_from_env()
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    wait_for_kafka(StockAlphaConfig.KAFKA_BOOTSTRAP)

    # Initialize engine (loads FinBERT model)
    engine = StockAlphaEngine()
    engine.setup()

    # Kafka consumer
    consumer = Consumer({
        "bootstrap.servers": StockAlphaConfig.KAFKA_BOOTSTRAP,
        "group.id": StockAlphaConfig.CONSUMER_GROUP,
        "auto.offset.reset": StockAlphaConfig.AUTO_OFFSET_RESET,
        "enable.auto.commit": False,
        "max.poll.interval.ms": 600000,  # 10 min — FinBERT + yfinance can be slow
    })
    consumer.subscribe([StockAlphaConfig.INPUT_TOPIC])

    # Kafka publisher
    publisher = KafkaPublisher(
        StockAlphaConfig.KAFKA_BOOTSTRAP,
        client_id="stock-alpha-engine",
    )

    log.info("=" * 60)
    log.info("Stock Alpha Engine running")
    log.info("  Input:     %s", StockAlphaConfig.INPUT_TOPIC)
    log.info("  Output:    %s", StockAlphaConfig.OUTPUT_TOPIC)
    log.info("  FinBERT:   %s", StockAlphaConfig.FINBERT_MODEL)
    log.info("  Consumer:  %s", StockAlphaConfig.CONSUMER_GROUP)
    log.info("=" * 60)

    processed_count = 0
    signals_emitted = 0

    try:
        while _running:
            msg = consumer.poll(timeout=StockAlphaConfig.MAX_POLL_TIMEOUT)

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    log.error("Consumer error: %s", msg.error())
                continue

            try:
                correlated = CorrelatedSignal.model_validate_json(msg.value())
            except Exception as e:
                log.warning("Failed to deserialize signal: %s", e)
                consumer.commit(message=msg)
                continue

            # Process through the engine
            try:
                alpha = engine.process_signal(correlated)
            except Exception as e:
                log.error("Engine error on %s: %s", correlated.entity_text, e)
                alpha = None

            if alpha:
                publisher.publish(
                    StockAlphaConfig.OUTPUT_TOPIC,
                    alpha.ticker,
                    json.dumps(alpha.to_dict()),
                )
                signals_emitted += 1

            consumer.commit(message=msg)
            processed_count += 1

            if processed_count % 50 == 0:
                log.info(
                    "Stats: %d processed, %d signals | Engine: %s | Kafka: %s",
                    processed_count, signals_emitted,
                    engine.stats, publisher.stats,
                )

    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down...")
        consumer.close()
        publisher.flush(timeout=10.0)
        engine.teardown()
        log.info(
            "Final: %d processed, %d signals | Kafka: %s",
            processed_count, signals_emitted, publisher.stats,
        )
        log.info("Stock Alpha Engine shutdown complete.")


if __name__ == "__main__":
    main()
