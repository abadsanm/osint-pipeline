"""
OSINT Pipeline -- Alpaca Market Data Connector

Polls Alpaca Data API v2 for 1-minute OHLCV bars on a configurable
list of symbols. Uses the IEX feed (free tier).

During market hours (09:30-16:00 ET, Mon-Fri) polls every 60 seconds.
Outside market hours polls every 300 seconds to stay alive and ready.

Auth: Requires ALPACA_API_KEY and ALPACA_API_SECRET environment variables.
These are sent as APCA-API-KEY-ID / APCA-API-SECRET-KEY headers.

Multi-symbol endpoint: /v2/stocks/bars?symbols=SPY,QQQ,...
Tracks already-seen bar timestamps per symbol to avoid republishing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone

import aiohttp
from confluent_kafka.admin import AdminClient

from connectors.kafka_publisher import KafkaPublisher

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class AlpacaConfig:
    """All tunables in one place. Override via env vars in production."""

    KAFKA_BOOTSTRAP = "localhost:9092"
    API_BASE = "https://data.alpaca.markets/v2"
    API_KEY: str | None = None
    API_SECRET: str | None = None
    SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META"]
    TOPIC = "market.alpaca.bars"
    POLL_INTERVAL = 60
    POLL_INTERVAL_OFF_HOURS = 300


def load_config_from_env():
    """Load configuration from environment variables."""
    # Load .env file
    from pathlib import Path
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    k, v = k.strip(), v.strip()
                    if v and k not in os.environ:
                        os.environ[k] = v

    api_key = os.environ.get("ALPACA_API_KEY")
    if api_key:
        AlpacaConfig.API_KEY = api_key

    api_secret = os.environ.get("ALPACA_API_SECRET")
    if api_secret:
        AlpacaConfig.API_SECRET = api_secret

    symbols = os.environ.get("ALPACA_SYMBOLS", "")
    if symbols:
        AlpacaConfig.SYMBOLS = [s.strip() for s in symbols.split(",") if s.strip()]

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        AlpacaConfig.KAFKA_BOOTSTRAP = bootstrap

    topic = os.environ.get("ALPACA_TOPIC")
    if topic:
        AlpacaConfig.TOPIC = topic

    poll = os.environ.get("ALPACA_POLL_INTERVAL")
    if poll:
        AlpacaConfig.POLL_INTERVAL = int(poll)

    poll_off = os.environ.get("ALPACA_POLL_INTERVAL_OFF_HOURS")
    if poll_off:
        AlpacaConfig.POLL_INTERVAL_OFF_HOURS = int(poll_off)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("alpaca-connector")


# ---------------------------------------------------------------------------
# Market Hours Helper
# ---------------------------------------------------------------------------

def is_market_hours() -> bool:
    """Return True if current time is within US equity market hours
    (09:30-16:00 ET, Monday-Friday)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

    now_et = datetime.now(ZoneInfo("America/New_York"))

    # Weekend check: Monday=0 ... Friday=4
    if now_et.weekday() > 4:
        return False

    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


# ---------------------------------------------------------------------------
# Bar Poller
# ---------------------------------------------------------------------------

class AlpacaBarPoller:
    """Polls Alpaca Data API v2 for 1-minute OHLCV bars and publishes
    new bars to Kafka."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        # Track seen bar timestamps per symbol to avoid republishing
        self._seen: dict[str, set[str]] = {sym: set() for sym in AlpacaConfig.SYMBOLS}

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "AlpacaBarPoller started -- symbols=%s, topic=%s",
            AlpacaConfig.SYMBOLS,
            AlpacaConfig.TOPIC,
        )

        while True:
            try:
                await self._poll_cycle(session)
            except Exception as e:
                log.error("AlpacaBarPoller poll error: %s", e, exc_info=True)

            interval = (
                AlpacaConfig.POLL_INTERVAL
                if is_market_hours()
                else AlpacaConfig.POLL_INTERVAL_OFF_HOURS
            )
            log.debug(
                "Sleeping %ds (%s market hours)",
                interval,
                "during" if is_market_hours() else "outside",
            )
            await asyncio.sleep(interval)

    async def _poll_cycle(self, session: aiohttp.ClientSession):
        url = f"{AlpacaConfig.API_BASE}/stocks/bars"
        from datetime import timedelta
        start = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "symbols": ",".join(AlpacaConfig.SYMBOLS),
            "timeframe": "1Min",
            "start": start,
            "limit": "10",
        }

        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._process_bars(data)
                elif resp.status == 403:
                    log.error("Alpaca 403 Forbidden -- check API key/secret")
                elif resp.status == 429:
                    log.warning("Alpaca 429 rate limited -- backing off")
                else:
                    body = await resp.text()
                    log.warning("Alpaca API returned %d: %s", resp.status, body[:200])
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Alpaca request failed: %s", e)

    def _process_bars(self, data: dict):
        """Process the multi-symbol bars response and publish new bars."""
        bars_map = data.get("bars") or {}
        published = 0

        for symbol, bars in bars_map.items():
            if symbol not in self._seen:
                self._seen[symbol] = set()

            for bar in bars:
                bar_ts = bar.get("t", "")
                if bar_ts in self._seen[symbol]:
                    continue
                self._seen[symbol].add(bar_ts)

                message = {
                    "symbol": symbol,
                    "timestamp": bar_ts,
                    "open": bar.get("o"),
                    "high": bar.get("h"),
                    "low": bar.get("l"),
                    "close": bar.get("c"),
                    "volume": bar.get("v"),
                    "vwap": bar.get("vw"),
                    "trade_count": bar.get("n"),
                }

                self._publisher.publish(
                    AlpacaConfig.TOPIC,
                    symbol,
                    json.dumps(message),
                )
                published += 1

        # Prevent unbounded memory growth -- keep last 500 timestamps per symbol
        for symbol in self._seen:
            if len(self._seen[symbol]) > 500:
                self._seen[symbol] = set(sorted(self._seen[symbol])[-300:])

        if published > 0:
            self._publisher.flush(timeout=5.0)
            log.info("Published %d new bars | Kafka stats: %s", published, self._publisher.stats)
        else:
            log.debug("No new bars this cycle")


# ---------------------------------------------------------------------------
# Kafka Readiness
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until Kafka broker is reachable."""
    log.info("Waiting for Kafka at %s...", bootstrap_servers)
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    start = time.time()
    while time.time() - start < timeout:
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.topics:
                log.info("Kafka is ready -- %d topics found", len(metadata.topics))
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def main():
    load_config_from_env()

    if not AlpacaConfig.API_KEY or not AlpacaConfig.API_SECRET:
        log.error(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set. "
            "Sign up at https://alpaca.markets for free API keys."
        )
        return

    # Wait for Kafka to be available
    await wait_for_kafka(AlpacaConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(AlpacaConfig.KAFKA_BOOTSTRAP, client_id="alpaca-connector")

    headers = {
        "APCA-API-KEY-ID": AlpacaConfig.API_KEY,
        "APCA-API-SECRET-KEY": AlpacaConfig.API_SECRET,
        "User-Agent": "OSINTPipeline/1.0 (research)",
    }

    async with aiohttp.ClientSession(
        headers=headers,
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        poller = AlpacaBarPoller(publisher)

        task = asyncio.create_task(poller.run(session))

        log.info("=" * 60)
        log.info("Alpaca Market Connector running")
        log.info("  Symbols:         %s", AlpacaConfig.SYMBOLS)
        log.info("  Topic:           %s", AlpacaConfig.TOPIC)
        log.info("  Poll interval:   %ds (market) / %ds (off-hours)",
                 AlpacaConfig.POLL_INTERVAL, AlpacaConfig.POLL_INTERVAL_OFF_HOURS)
        log.info("  Feed:            iex (free tier)")
        log.info("=" * 60)

        try:
            await task
        except asyncio.CancelledError:
            pass

        log.info("Cancelling tasks...")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("Alpaca Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
