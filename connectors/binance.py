"""
OSINT Pipeline — Binance WebSocket Connector

Streams real-time market data from Binance public WebSocket API:
  1. ORDER BOOK DEPTH: Top 20 bid/ask levels at 100ms intervals
  2. AGGREGATED TRADES: Real-time trade executions with buyer/maker flag

Uses the combined stream endpoint so all symbols share a single WebSocket
connection. No authentication required — these are public market data streams.

Binance WebSocket docs: https://binance-docs.github.io/apidocs/spot/en/#websocket-market-streams
Rate limits: 5 incoming messages/sec per connection (we only receive, so no limit needed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from typing import Optional

import websockets
from connectors.kafka_publisher import KafkaPublisher, wait_for_bus

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class BinanceConfig:
    """All tunables in one place. Override via env vars in production."""

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    DEPTH_TOPIC = "market.binance.depth"
    TRADES_TOPIC = "market.binance.trades"

    # Binance WebSocket
    WS_BASE = "wss://stream.binance.us:9443/stream"
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # Reconnection
    RECONNECT_DELAY = 5        # seconds, initial backoff
    MAX_RECONNECT_DELAY = 60   # seconds, cap for exponential backoff


def load_config_from_env():
    """Override config from environment variables."""
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        BinanceConfig.KAFKA_BOOTSTRAP = bootstrap

    symbols = os.environ.get("BINANCE_SYMBOLS")
    if symbols:
        BinanceConfig.SYMBOLS = [s.strip().upper() for s in symbols.split(",") if s.strip()]

    ws_base = os.environ.get("BINANCE_WS_BASE")
    if ws_base:
        BinanceConfig.WS_BASE = ws_base

    depth_topic = os.environ.get("BINANCE_DEPTH_TOPIC")
    if depth_topic:
        BinanceConfig.DEPTH_TOPIC = depth_topic

    trades_topic = os.environ.get("BINANCE_TRADES_TOPIC")
    if trades_topic:
        BinanceConfig.TRADES_TOPIC = trades_topic

    reconnect = os.environ.get("BINANCE_RECONNECT_DELAY")
    if reconnect:
        BinanceConfig.RECONNECT_DELAY = int(reconnect)

    max_reconnect = os.environ.get("BINANCE_MAX_RECONNECT_DELAY")
    if max_reconnect:
        BinanceConfig.MAX_RECONNECT_DELAY = int(max_reconnect)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("binance-connector")


# ---------------------------------------------------------------------------
# Stream URL Builder
# ---------------------------------------------------------------------------

def build_stream_url(symbols: list[str]) -> str:
    """Build the combined stream URL for all symbols.

    Each symbol gets two streams: depth@100ms and aggTrade.
    Example: wss://stream.binance.com:9443/stream?streams=btcusdt@depth@100ms/btcusdt@aggTrade/...
    """
    streams = []
    for sym in symbols:
        lower = sym.lower()
        streams.append(f"{lower}@depth20@100ms")
        streams.append(f"{lower}@aggTrade")
    stream_param = "/".join(streams)
    return f"{BinanceConfig.WS_BASE}?streams={stream_param}"


# ---------------------------------------------------------------------------
# Message Handlers
# ---------------------------------------------------------------------------

def handle_depth_message(stream: str, data: dict, publisher: KafkaPublisher) -> None:
    """Process an order book depth snapshot and publish to Kafka.

    Depth data format from Binance:
      {
        "lastUpdateId": 123456,
        "bids": [["price", "qty"], ...],  # sorted best (highest) first
        "asks": [["price", "qty"], ...],  # sorted best (lowest) first
      }
    """
    # Extract symbol from stream name: "btcusdt@depth@100ms" -> "BTCUSDT"
    symbol = stream.split("@")[0].upper()

    # Binance uses "bids"/"asks" for REST and "b"/"a" for WebSocket streams
    raw_bids = data.get("bids") or data.get("b") or []
    raw_asks = data.get("asks") or data.get("a") or []
    bids = [[float(price), float(qty)] for price, qty in raw_bids[:20]]
    asks = [[float(price), float(qty)] for price, qty in raw_asks[:20]]

    message = json.dumps({
        "symbol": symbol,
        "timestamp": int(time.time() * 1000),
        "last_update_id": data.get("lastUpdateId"),
        "bids": bids,
        "asks": asks,
    })

    publisher.publish(BinanceConfig.DEPTH_TOPIC, symbol, message)


def handle_trade_message(stream: str, data: dict, publisher: KafkaPublisher) -> None:
    """Process an aggregated trade and publish to Kafka.

    AggTrade data format from Binance:
      {
        "e": "aggTrade",
        "s": "BTCUSDT",
        "p": "50000.00",     # price
        "q": "0.001",        # quantity
        "T": 1625000000000,  # trade time (ms)
        "m": true,           # is buyer the maker?
      }
    """
    symbol = data.get("s", stream.split("@")[0].upper())

    message = json.dumps({
        "symbol": symbol,
        "timestamp": data.get("T", int(time.time() * 1000)),
        "price": float(data.get("p", 0)),
        "quantity": float(data.get("q", 0)),
        "is_buyer_maker": data.get("m", False),
        "agg_trade_id": data.get("a"),
        "first_trade_id": data.get("f"),
        "last_trade_id": data.get("l"),
    })

    publisher.publish(BinanceConfig.TRADES_TOPIC, symbol, message)


# ---------------------------------------------------------------------------
# WebSocket Consumer
# ---------------------------------------------------------------------------

class BinanceWebSocketConsumer:
    """Connects to the Binance combined WebSocket stream and dispatches
    incoming messages to the appropriate handler."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._running = True
        self._message_count = 0
        self._depth_count = 0
        self._trade_count = 0

    def stop(self):
        self._running = False

    async def run(self):
        """Main loop with exponential backoff reconnection."""
        delay = BinanceConfig.RECONNECT_DELAY

        while self._running:
            try:
                await self._connect_and_consume()
                # If we exit cleanly (shutdown), break
                if not self._running:
                    break
                # Otherwise, connection dropped — reset backoff on successful session
                delay = BinanceConfig.RECONNECT_DELAY
            except (
                websockets.ConnectionClosed,
                websockets.exceptions.InvalidStatus,
                ConnectionError,
                OSError,
            ) as e:
                log.warning("WebSocket connection lost: %s", e)
            except Exception as e:
                log.error("Unexpected WebSocket error: %s", e, exc_info=True)

            if not self._running:
                break

            log.info("Reconnecting in %ds...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, BinanceConfig.MAX_RECONNECT_DELAY)

    async def _connect_and_consume(self):
        """Establish WebSocket connection and process messages."""
        url = build_stream_url(BinanceConfig.SYMBOLS)
        log.info("Connecting to %s", url)

        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=10 * 1024 * 1024,  # 10 MB max message size
        ) as ws:
            log.info("WebSocket connected — streaming %d symbols", len(BinanceConfig.SYMBOLS))

            while self._running:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    # No message in 30s — Binance should send data constantly,
                    # so this likely means the connection is stale
                    log.warning("No data received in 30s, reconnecting...")
                    return

                self._message_count += 1

                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Malformed JSON received, skipping")
                    continue

                stream = envelope.get("stream", "")
                data = envelope.get("data")

                if not stream or data is None:
                    continue

                if "@depth" in stream and "@aggTrade" not in stream:
                    handle_depth_message(stream, data, self._publisher)
                    self._depth_count += 1
                elif "@aggTrade" in stream:
                    handle_trade_message(stream, data, self._publisher)
                    self._trade_count += 1

                # Periodic flush and stats logging
                if self._message_count % 1000 == 0:
                    self._publisher.flush(timeout=5.0)
                    log.info(
                        "Messages processed: %d (depth=%d, trades=%d) | Kafka: %s",
                        self._message_count,
                        self._depth_count,
                        self._trade_count,
                        self._publisher.stats,
                    )

    @property
    def stats(self) -> dict:
        return {
            "total_messages": self._message_count,
            "depth_messages": self._depth_count,
            "trade_messages": self._trade_count,
        }


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


async def main():
    load_config_from_env()

    # Wait for Kafka to be available
    await wait_for_kafka(BinanceConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(BinanceConfig.KAFKA_BOOTSTRAP, client_id="binance-connector")
    consumer = BinanceWebSocketConsumer(publisher)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    def _signal_handler():
        log.info("Shutdown signal received")
        consumer.stop()
        shutdown_event.set()

    # Windows-compatible signal handling
    loop = asyncio.get_running_loop()
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except NotImplementedError:
        # Windows doesn't support add_signal_handler for SIGTERM
        signal.signal(signal.SIGINT, lambda s, f: _signal_handler())
        signal.signal(signal.SIGTERM, lambda s, f: _signal_handler())

    log.info("=" * 60)
    log.info("Binance Connector running — WebSocket streaming")
    log.info("  Symbols:        %s", ", ".join(BinanceConfig.SYMBOLS))
    log.info("  Depth topic:    %s", BinanceConfig.DEPTH_TOPIC)
    log.info("  Trades topic:   %s", BinanceConfig.TRADES_TOPIC)
    log.info("  Reconnect:      %ds initial, %ds max",
             BinanceConfig.RECONNECT_DELAY, BinanceConfig.MAX_RECONNECT_DELAY)
    log.info("=" * 60)

    # Run the WebSocket consumer
    task = asyncio.create_task(consumer.run())

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass

    consumer.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    publisher.flush(timeout=10.0)
    log.info("Final stats: connector=%s | kafka=%s", consumer.stats, publisher.stats)
    log.info("Binance Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
