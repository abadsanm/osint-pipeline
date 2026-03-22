"""
Prediction Tracker

Kafka consumer that evaluates predictions against actual price movements.
Consumes from stock.alpha.predictions, evaluates expired predictions,
and publishes outcomes to stock.alpha.outcomes.

Usage:
    python -m stock_alpha.tracker
"""

from __future__ import annotations

import json
import logging
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from confluent_kafka import Consumer, KafkaError

from connectors.kafka_publisher import KafkaPublisher
from schemas.prediction import Prediction
from stock_alpha.technicals import PriceDataProvider

log = logging.getLogger("stock_alpha.tracker")

_running = True

def _signal_handler(sig, frame):
    global _running
    log.info("Shutdown signal received")
    _running = False


class PredictionTracker:
    """Tracks predictions and evaluates them against actual outcomes."""

    def __init__(self):
        self._pending: dict[str, Prediction] = {}  # prediction_id -> Prediction
        self._prices = PriceDataProvider()
        self._eval_count = 0
        self._correct_count = 0

        # Rolling stats
        self._by_regime: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        self._by_horizon: dict[int, dict] = defaultdict(lambda: {"total": 0, "correct": 0})
        self._calibration_pairs: list[tuple[float, bool]] = []  # (confidence, was_correct)

    def ingest(self, prediction: Prediction):
        """Store a prediction for later evaluation."""
        self._pending[prediction.prediction_id] = prediction

    def evaluate_expired(self) -> list[Prediction]:
        """Check all pending predictions, evaluate those past expiry.

        Returns list of evaluated predictions.
        """
        evaluated = []
        expired_ids = [
            pid for pid, pred in self._pending.items()
            if pred.is_expired() and pred.outcome is None
        ]

        if not expired_ids:
            return evaluated

        # Group by ticker to minimize yfinance calls
        by_ticker: dict[str, list[str]] = defaultdict(list)
        for pid in expired_ids:
            by_ticker[self._pending[pid].ticker].append(pid)

        for ticker, pids in by_ticker.items():
            df = self._prices.get_prices(ticker)
            if df is None or df.empty:
                continue

            # Get close column
            close_col = "close" if "close" in df.columns else "Close"
            if close_col not in df.columns:
                continue

            current_price = float(df[close_col].iloc[-1])

            for pid in pids:
                pred = self._pending[pid]

                # Try to get price at prediction time
                # Use the current price as approximation since we don't have intraday
                # The prediction's raw_score was based on a price at creation time
                # For now, use current_price vs a rough backward estimate

                # Simple approach: compute return from price at prediction creation
                # We'd need the price at pred.created_at — use yfinance history
                try:
                    # Get the close price on the prediction creation date
                    pred_date = pred.created_at.strftime("%Y-%m-%d")
                    if pred_date in df.index.strftime("%Y-%m-%d"):
                        mask = df.index.strftime("%Y-%m-%d") == pred_date
                        price_at_pred = float(df.loc[mask, close_col].iloc[-1])
                    else:
                        # Use the most recent price before prediction date
                        before = df[df.index <= pred.created_at]
                        if len(before) > 0:
                            price_at_pred = float(before[close_col].iloc[-1])
                        else:
                            price_at_pred = current_price

                    actual_return = (current_price - price_at_pred) / price_at_pred * 100

                    # Evaluate
                    if pred.direction == "bullish":
                        correct = actual_return > 0
                    elif pred.direction == "bearish":
                        correct = actual_return < 0
                    else:  # neutral
                        correct = abs(actual_return) < 1.0

                    pred.outcome = "correct" if correct else "incorrect"
                    pred.actual_return = round(actual_return, 4)
                    pred.evaluated_at = datetime.now(timezone.utc)

                    # Update stats
                    self._eval_count += 1
                    if correct:
                        self._correct_count += 1

                    self._by_regime[pred.regime]["total"] += 1
                    if correct:
                        self._by_regime[pred.regime]["correct"] += 1

                    self._by_horizon[pred.time_horizon_hours]["total"] += 1
                    if correct:
                        self._by_horizon[pred.time_horizon_hours]["correct"] += 1

                    self._calibration_pairs.append((pred.confidence, correct))

                    evaluated.append(pred)

                except Exception as e:
                    log.debug("Failed to evaluate %s for %s: %s", pid, ticker, e)
                    pred.outcome = "expired"
                    pred.evaluated_at = datetime.now(timezone.utc)
                    evaluated.append(pred)

        # Remove evaluated predictions from pending
        for pred in evaluated:
            self._pending.pop(pred.prediction_id, None)

        return evaluated

    def get_stats(self) -> dict:
        """Get rolling accuracy statistics."""
        overall_acc = self._correct_count / self._eval_count if self._eval_count > 0 else 0

        regime_stats = {}
        for regime, counts in self._by_regime.items():
            regime_stats[regime] = round(counts["correct"] / counts["total"], 3) if counts["total"] > 0 else 0

        horizon_stats = {}
        for h, counts in self._by_horizon.items():
            horizon_stats[f"{h}h"] = round(counts["correct"] / counts["total"], 3) if counts["total"] > 0 else 0

        # Check calibration drift
        cal_drift = self._check_calibration_drift()

        return {
            "total_evaluated": self._eval_count,
            "total_correct": self._correct_count,
            "accuracy": round(overall_acc, 3),
            "pending": len(self._pending),
            "by_regime": regime_stats,
            "by_horizon": horizon_stats,
            "calibration_drift": cal_drift,
        }

    def _check_calibration_drift(self) -> dict:
        """Check if stated confidence matches actual accuracy."""
        if len(self._calibration_pairs) < 100:
            return {"status": "insufficient_data", "pairs": len(self._calibration_pairs)}

        # Group into confidence bins
        pairs = self._calibration_pairs[-500:]  # Last 500
        stated = [c for c, _ in pairs]
        actual = [1.0 if correct else 0.0 for _, correct in pairs]

        avg_stated = sum(stated) / len(stated)
        avg_actual = sum(actual) / len(actual)
        drift = abs(avg_stated - avg_actual)

        needs_recal = drift > 0.10
        if needs_recal:
            log.warning("Calibration drift detected: stated=%.3f actual=%.3f gap=%.3f — recalibration recommended", avg_stated, avg_actual, drift)

        return {
            "status": "drift_detected" if needs_recal else "ok",
            "avg_stated_confidence": round(avg_stated, 3),
            "avg_actual_accuracy": round(avg_actual, 3),
            "drift": round(drift, 3),
            "needs_recalibration": needs_recal,
            "sample_size": len(pairs),
        }


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
    raise RuntimeError(f"Kafka not reachable after {timeout}s")


def main():
    global _running

    from stock_alpha.config import StockAlphaConfig, load_config_from_env
    load_config_from_env()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    bootstrap = StockAlphaConfig.KAFKA_BOOTSTRAP
    wait_for_kafka(bootstrap)

    consumer = Consumer({
        "bootstrap.servers": bootstrap,
        "group.id": "prediction-tracker",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe(["stock.alpha.predictions"])

    publisher = KafkaPublisher(bootstrap, client_id="prediction-tracker")
    tracker = PredictionTracker()

    log.info("=" * 60)
    log.info("Prediction Tracker running")
    log.info("  Input:  stock.alpha.predictions")
    log.info("  Output: stock.alpha.outcomes")
    log.info("=" * 60)

    last_eval = time.time()
    last_log = time.time()

    try:
        while _running:
            msg = consumer.poll(1.0)

            if msg is not None and not msg.error():
                try:
                    data = json.loads(msg.value())
                    # Could be a PredictionBatch or individual Prediction
                    if "predictions" in data:
                        for p in data["predictions"]:
                            pred = Prediction.model_validate(p)
                            tracker.ingest(pred)
                    else:
                        pred = Prediction.model_validate(data)
                        tracker.ingest(pred)
                except Exception as e:
                    log.debug("Failed to parse prediction: %s", e)
            elif msg is not None and msg.error() and msg.error().code() != KafkaError._PARTITION_EOF:
                log.error("Consumer error: %s", msg.error())

            # Evaluate expired predictions every 60 seconds
            now = time.time()
            if now - last_eval >= 60:
                evaluated = tracker.evaluate_expired()
                for pred in evaluated:
                    publisher.publish(
                        "stock.alpha.outcomes",
                        pred.to_kafka_key(),
                        pred.to_kafka_value(),
                    )
                last_eval = now

                if evaluated:
                    log.info("Evaluated %d predictions", len(evaluated))

            # Log stats every 5 minutes
            if now - last_log >= 300:
                stats = tracker.get_stats()
                log.info(
                    "Tracker stats: %d evaluated, %.1f%% accuracy, %d pending | "
                    "Regime: %s | Horizon: %s",
                    stats["total_evaluated"],
                    stats["accuracy"] * 100,
                    stats["pending"],
                    stats["by_regime"],
                    stats["by_horizon"],
                )
                last_log = now

    except KeyboardInterrupt:
        pass
    finally:
        log.info("Shutting down tracker...")
        consumer.close()
        publisher.flush(10.0)
        stats = tracker.get_stats()
        log.info("Final stats: %s", stats)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
