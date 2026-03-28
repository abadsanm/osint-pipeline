"""
OSINT Pipeline — Bluesky Social Sentiment Connector

Ingests public posts from the Bluesky AT Protocol network via the
public search API. No authentication required.

API: https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts
  - Search for financial and tech keywords
  - Returns 25 posts per query, sorted by latest
  - No rate-limit documentation, but we pace conservatively

Poll cycle:
  - Rotates through configured keywords every POLL_INTERVAL seconds
  - Deduplicates by post URI (at:// identifier)
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
from connectors.kafka_publisher import KafkaPublisher, wait_for_bus
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class BlueskyConfig:
    """All tunables in one place. Override via env vars in production."""

    # API
    SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"

    # Keywords to search
    KEYWORDS: list[str] = [
        "stock", "$AAPL", "$TSLA", "market", "investing",
        "earnings", "bullish", "bearish",
        "AI", "crypto", "startup",
    ]

    # Polling
    POLL_INTERVAL = 120       # seconds between full keyword rotation cycles
    REQUEST_DELAY = 2.0       # delay between individual keyword requests

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "social.bluesky.posts"

    # Ingestion
    POSTS_PER_QUERY = 25

    USER_AGENT = "OSINTPipeline/1.0 (research; contact: mas-admin@assetboss.app)"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bluesky-connector")


# ---------------------------------------------------------------------------
# Bluesky Post → OsintDocument Converter
# ---------------------------------------------------------------------------

def post_to_document(post: dict) -> Optional[OsintDocument]:
    """Convert a Bluesky search result post to OsintDocument."""
    uri = post.get("uri", "")
    cid = post.get("cid", "")

    record = post.get("record", {})
    text = record.get("text", "")

    if not text or not uri:
        return None

    # Author info
    author = post.get("author", {})
    handle = author.get("handle", "unknown")
    display_name = author.get("displayName", "")

    # Timestamps
    created_str = record.get("createdAt") or post.get("indexedAt", "")
    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created_at = datetime.now(timezone.utc)

    # Engagement counts
    like_count = post.get("likeCount", 0)
    repost_count = post.get("repostCount", 0)
    reply_count = post.get("replyCount", 0)
    total_engagement = like_count + repost_count + reply_count

    # Build a web URL from the AT URI
    # at://did:plc:xxx/app.bsky.feed.post/yyy → https://bsky.app/profile/handle/post/yyy
    post_rkey = uri.split("/")[-1] if "/" in uri else ""
    web_url = f"https://bsky.app/profile/{handle}/post/{post_rkey}" if post_rkey else None

    doc = OsintDocument(
        source=SourcePlatform.BLUESKY,
        source_id=cid or uri,
        content_type=ContentType.STORY,
        title=None,
        content_text=text,
        url=web_url,
        created_at=created_at,
        entity_id=handle,
        quality_signals=QualitySignals(
            score=float(like_count),
            engagement_count=total_engagement,
        ),
        metadata={
            "author_handle": handle,
            "author_display_name": display_name,
            "author_did": author.get("did", ""),
            "uri": uri,
            "like_count": like_count,
            "repost_count": repost_count,
            "reply_count": reply_count,
            "labels": [l.get("val", "") for l in post.get("labels", [])],
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Bluesky Fetcher
# ---------------------------------------------------------------------------

class BlueskyFetcher:
    """Fetches posts from Bluesky public search API."""

    def __init__(self):
        self._seen_uris: set[str] = set()

    async def search_posts(
        self,
        session: aiohttp.ClientSession,
        keyword: str,
        limit: int = 25,
    ) -> list[dict]:
        """Search Bluesky for posts matching a keyword."""
        params = {
            "q": keyword,
            "limit": str(limit),
            "sort": "latest",
        }

        try:
            async with session.get(
                BlueskyConfig.SEARCH_URL,
                params=params,
                headers={"User-Agent": BlueskyConfig.USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    log.warning("Bluesky rate limited (429) — backing off")
                    await asyncio.sleep(30)
                    return []
                if resp.status != 200:
                    log.warning("Bluesky search returned %d for '%s'", resp.status, keyword)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Bluesky search failed for '%s': %s", keyword, e)
            return []

        posts = data.get("posts", [])
        new_posts = []
        for post in posts:
            uri = post.get("uri", "")
            if uri and uri not in self._seen_uris:
                self._seen_uris.add(uri)
                new_posts.append(post)

        # Prevent unbounded growth
        if len(self._seen_uris) > 50000:
            self._seen_uris = set(list(self._seen_uris)[-25000:])

        return new_posts


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class BlueskyConnector:
    """Orchestrates Bluesky ingestion across configured keywords."""

    def __init__(self, publisher: KafkaPublisher, fetcher: BlueskyFetcher):
        self._publisher = publisher
        self._fetcher = fetcher

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "Bluesky connector started — keywords=%d, poll=%ds",
            len(BlueskyConfig.KEYWORDS), BlueskyConfig.POLL_INTERVAL,
        )

        while True:
            cycle_published = 0

            for keyword in BlueskyConfig.KEYWORDS:
                try:
                    count = await self._ingest_keyword(session, keyword)
                    cycle_published += count
                except Exception as e:
                    log.error("Error searching '%s': %s", keyword, e, exc_info=True)

                await asyncio.sleep(BlueskyConfig.REQUEST_DELAY)

            if cycle_published:
                self._publisher.flush(timeout=5.0)
                log.info("Bluesky cycle complete: published %d documents", cycle_published)

            await asyncio.sleep(BlueskyConfig.POLL_INTERVAL)

    async def _ingest_keyword(
        self, session: aiohttp.ClientSession, keyword: str,
    ) -> int:
        posts = await self._fetcher.search_posts(
            session, keyword, limit=BlueskyConfig.POSTS_PER_QUERY,
        )

        published = 0
        for post in posts:
            doc = post_to_document(post)
            if doc:
                self._publisher.publish(
                    BlueskyConfig.KAFKA_TOPIC,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published += 1

        if published:
            log.info("  keyword='%s': %d new posts", keyword, published)

        return published


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    keywords = os.environ.get("BLUESKY_KEYWORDS", "")
    if keywords:
        BlueskyConfig.KEYWORDS = [k.strip() for k in keywords.split(",") if k.strip()]

    poll_interval = os.environ.get("BLUESKY_POLL_INTERVAL")
    if poll_interval:
        BlueskyConfig.POLL_INTERVAL = int(poll_interval)

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        BlueskyConfig.KAFKA_BOOTSTRAP = bootstrap

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(BlueskyConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()

    await wait_for_kafka(BlueskyConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(BlueskyConfig.KAFKA_BOOTSTRAP, client_id="bluesky-connector")
    fetcher = BlueskyFetcher()
    connector = BlueskyConnector(publisher, fetcher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("Bluesky Social Sentiment Connector running")
        log.info("  Keywords:  %s", BlueskyConfig.KEYWORDS)
        log.info("  Poll:      %ds", BlueskyConfig.POLL_INTERVAL)
        log.info("  Kafka:     %s", BlueskyConfig.KAFKA_TOPIC)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("Bluesky Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
