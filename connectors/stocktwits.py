"""
OSINT Pipeline — StockTwits Social Sentiment Connector

Ingests public messages from StockTwits via their public API.
No authentication required for public endpoints.

Endpoints:
  - https://api.stocktwits.com/api/2/streams/trending.json — trending messages
  - https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json — per-ticker stream

Key feature: StockTwits users self-tag sentiment as "Bullish" or "Bearish",
providing pre-labeled training data for sentiment models.

Rate limits:
  - 200 requests/hour for unauthenticated (strict)
  - We poll every 180s and limit symbol count to stay well under

Poll cycle:
  - Fetches trending stream first
  - Then rotates through configured ticker symbols
  - Deduplicates by message ID
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from confluent_kafka.admin import AdminClient

from connectors.kafka_publisher import KafkaPublisher
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class StocktwitsConfig:
    """All tunables in one place. Override via env vars in production."""

    # API endpoints
    BASE_URL = "https://api.stocktwits.com/api/2"
    TRENDING_URL = f"{BASE_URL}/streams/trending.json"

    # Symbols to track
    SYMBOLS: list[str] = [
        "AAPL", "TSLA", "NVDA", "MSFT", "SPY",
        "QQQ", "AMZN", "META", "GOOGL",
    ]

    # Polling
    POLL_INTERVAL = 180       # seconds between full cycles (conservative for rate limits)
    REQUEST_DELAY = 3.0       # delay between individual API requests

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "social.stocktwits.messages"

    # Ingestion
    MESSAGES_PER_REQUEST = 30  # StockTwits default max

    USER_AGENT = "OSINTPipeline/1.0 (research; contact: mas-admin@assetboss.app)"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("stocktwits-connector")


# ---------------------------------------------------------------------------
# StockTwits Message → OsintDocument Converter
# ---------------------------------------------------------------------------

def message_to_document(msg: dict, source_stream: str) -> Optional[OsintDocument]:
    """Convert a StockTwits message to OsintDocument."""
    msg_id = msg.get("id")
    body = msg.get("body", "")

    if not body or not msg_id:
        return None

    # User info
    user = msg.get("user", {})
    username = user.get("username", "unknown")

    # Timestamp
    created_str = msg.get("created_at", "")
    try:
        # StockTwits format: "2024-01-15T10:30:00Z"
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created_at = datetime.now(timezone.utc)

    # User-tagged sentiment (unique StockTwits feature)
    entities_sentiment = msg.get("entities", {}).get("sentiment", None)
    user_sentiment = None
    if entities_sentiment and isinstance(entities_sentiment, dict):
        user_sentiment = entities_sentiment.get("basic")  # "Bullish" or "Bearish"

    # Symbols mentioned in the message
    symbols_data = msg.get("symbols", [])
    symbol_list = [s.get("symbol", "") for s in symbols_data if s.get("symbol")]

    # Engagement
    likes_count = msg.get("likes", {}).get("total", 0) if isinstance(msg.get("likes"), dict) else 0
    reshares_count = msg.get("reshares", {}).get("reshared_count", 0) if isinstance(msg.get("reshares"), dict) else 0

    # Primary entity: first symbol mentioned, or the source stream
    entity_id = symbol_list[0] if symbol_list else source_stream

    doc = OsintDocument(
        source=SourcePlatform.STOCKTWITS,
        source_id=str(msg_id),
        content_type=ContentType.STORY,
        title=None,
        content_text=body,
        url=f"https://stocktwits.com/{username}/message/{msg_id}",
        created_at=created_at,
        entity_id=entity_id,
        quality_signals=QualitySignals(
            score=float(likes_count),
            engagement_count=likes_count + reshares_count,
        ),
        metadata={
            "username": username,
            "user_id": user.get("id"),
            "user_followers": user.get("followers", 0),
            "user_sentiment": user_sentiment,
            "symbols": symbol_list,
            "source_stream": source_stream,
            "likes_count": likes_count,
            "reshares_count": reshares_count,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# StockTwits Fetcher
# ---------------------------------------------------------------------------

class StocktwitsFetcher:
    """Fetches messages from StockTwits public API."""

    def __init__(self):
        self._seen_ids: set[int] = set()

    async def fetch_trending(
        self, session: aiohttp.ClientSession,
    ) -> list[dict]:
        """Fetch trending messages across StockTwits."""
        return await self._fetch(session, StocktwitsConfig.TRENDING_URL, "trending")

    async def fetch_symbol(
        self, session: aiohttp.ClientSession, symbol: str,
    ) -> list[dict]:
        """Fetch messages for a specific ticker symbol."""
        url = f"{StocktwitsConfig.BASE_URL}/streams/symbol/{symbol}.json"
        return await self._fetch(session, url, symbol)

    async def _fetch(
        self, session: aiohttp.ClientSession, url: str, label: str,
    ) -> list[dict]:
        try:
            async with session.get(
                url,
                headers={"User-Agent": StocktwitsConfig.USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    log.warning("StockTwits rate limited (429) — backing off")
                    await asyncio.sleep(60)
                    return []
                if resp.status != 200:
                    log.warning("StockTwits returned %d for '%s'", resp.status, label)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("StockTwits fetch failed for '%s': %s", label, e)
            return []

        messages = data.get("messages", [])
        new_messages = []
        for msg in messages:
            msg_id = msg.get("id")
            if msg_id and msg_id not in self._seen_ids:
                self._seen_ids.add(msg_id)
                new_messages.append(msg)

        # Prevent unbounded growth
        if len(self._seen_ids) > 50000:
            self._seen_ids = set(list(self._seen_ids)[-25000:])

        return new_messages


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class StocktwitsConnector:
    """Orchestrates StockTwits ingestion from trending and per-symbol streams."""

    def __init__(self, publisher: KafkaPublisher, fetcher: StocktwitsFetcher):
        self._publisher = publisher
        self._fetcher = fetcher

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "StockTwits connector started — symbols=%d, poll=%ds",
            len(StocktwitsConfig.SYMBOLS), StocktwitsConfig.POLL_INTERVAL,
        )

        while True:
            cycle_published = 0

            # 1. Fetch trending stream
            try:
                count = await self._ingest_stream(
                    session, "trending",
                    self._fetcher.fetch_trending(session),
                )
                cycle_published += count
            except Exception as e:
                log.error("Error fetching trending: %s", e, exc_info=True)

            await asyncio.sleep(StocktwitsConfig.REQUEST_DELAY)

            # 2. Fetch per-symbol streams
            for symbol in StocktwitsConfig.SYMBOLS:
                try:
                    count = await self._ingest_stream(
                        session, symbol,
                        self._fetcher.fetch_symbol(session, symbol),
                    )
                    cycle_published += count
                except Exception as e:
                    log.error("Error fetching %s: %s", symbol, e, exc_info=True)

                await asyncio.sleep(StocktwitsConfig.REQUEST_DELAY)

            if cycle_published:
                self._publisher.flush(timeout=5.0)
                log.info("StockTwits cycle complete: published %d documents", cycle_published)

            await asyncio.sleep(StocktwitsConfig.POLL_INTERVAL)

    async def _ingest_stream(
        self, session: aiohttp.ClientSession, stream_label: str, messages_coro,
    ) -> int:
        messages = await messages_coro

        published = 0
        for msg in messages:
            doc = message_to_document(msg, stream_label)
            if doc:
                self._publisher.publish(
                    StocktwitsConfig.KAFKA_TOPIC,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published += 1

        if published:
            log.info("  stream='%s': %d new messages", stream_label, published)

        return published


# ---------------------------------------------------------------------------
# Main Entrypoint
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
                log.info("Kafka is ready — %d topics found", len(metadata.topics))
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


def load_config_from_env():
    """Load configuration from environment variables."""
    symbols = os.environ.get("STOCKTWITS_SYMBOLS", "")
    if symbols:
        StocktwitsConfig.SYMBOLS = [s.strip() for s in symbols.split(",") if s.strip()]

    poll_interval = os.environ.get("STOCKTWITS_POLL_INTERVAL")
    if poll_interval:
        StocktwitsConfig.POLL_INTERVAL = int(poll_interval)

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        StocktwitsConfig.KAFKA_BOOTSTRAP = bootstrap


async def main():
    load_config_from_env()

    await wait_for_kafka(StocktwitsConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(StocktwitsConfig.KAFKA_BOOTSTRAP, client_id="stocktwits-connector")
    fetcher = StocktwitsFetcher()
    connector = StocktwitsConnector(publisher, fetcher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("StockTwits Social Sentiment Connector running")
        log.info("  Symbols:   %s", StocktwitsConfig.SYMBOLS)
        log.info("  Poll:      %ds", StocktwitsConfig.POLL_INTERVAL)
        log.info("  Kafka:     %s", StocktwitsConfig.KAFKA_TOPIC)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("StockTwits Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
