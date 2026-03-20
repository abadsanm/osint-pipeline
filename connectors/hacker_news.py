"""
OSINT Pipeline — Hacker News Connector

Two ingestion modes running concurrently:
  1. LIVE STREAM: Polls Firebase /v0/topstories and /v0/newstories every 30s,
     fetches new items, and publishes to Kafka.
  2. KEYWORD MONITOR: Queries Algolia HN Search API on a configurable interval
     for specific keywords (tickers, product names, technologies).

Why not Firebase SSE?
  Firebase SSE on /v0/topstories emits the full 500-item array on every change,
  which is noisy. Polling + diffing is more efficient for our use case.

No rate limit on the Firebase API (confirmed via official docs).
Algolia HN API: ~10K requests/hour is safe; we use ~720/hour (one query/5s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timezone
from html import unescape
from typing import Optional

import aiohttp
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient

from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class HNConfig:
    """All tunables in one place. Override via env vars in production."""

    # Firebase HN API
    FIREBASE_BASE = "https://hacker-news.firebaseio.com/v0"
    ITEM_URL = f"{FIREBASE_BASE}/item/{{item_id}}.json"
    TOP_STORIES_URL = f"{FIREBASE_BASE}/topstories.json"
    NEW_STORIES_URL = f"{FIREBASE_BASE}/newstories.json"

    # Algolia HN Search API
    ALGOLIA_BASE = "https://hn.algolia.com/api/v1"
    ALGOLIA_SEARCH_URL = f"{ALGOLIA_BASE}/search"
    ALGOLIA_RECENT_URL = f"{ALGOLIA_BASE}/search_by_date"

    # Polling intervals (seconds)
    STORIES_POLL_INTERVAL = 30
    ALGOLIA_POLL_INTERVAL = 300  # 5 minutes

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC_STORIES = "tech.hn.stories"
    KAFKA_TOPIC_COMMENTS = "tech.hn.comments"

    # Connector limits
    MAX_CONCURRENT_FETCHES = 20
    COMMENT_DEPTH_LIMIT = 3  # Don't recurse deeper than 3 levels
    ITEM_FETCH_TIMEOUT = 10  # seconds

    # Keywords to monitor via Algolia (extend as needed)
    MONITOR_KEYWORDS = [
        "AI startup",
        "LLM",
        "GPT",
        "vector database",
        "Show HN",
        "YC",
    ]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("hn-connector")


# ---------------------------------------------------------------------------
# Kafka Producer
# ---------------------------------------------------------------------------

class KafkaPublisher:
    """Thin wrapper around confluent_kafka.Producer with delivery callbacks."""

    def __init__(self, bootstrap_servers: str):
        self._producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "client.id": "hn-connector",
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
        # Trigger delivery callbacks without blocking
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


# ---------------------------------------------------------------------------
# HN Item Fetcher
# ---------------------------------------------------------------------------

def strip_html(text: Optional[str]) -> str:
    """Remove HTML tags from HN text fields (they come as HTML)."""
    if not text:
        return ""
    import re
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = unescape(clean)
    return " ".join(clean.split())


def hn_item_to_document(item: dict) -> Optional[OsintDocument]:
    """Convert a raw HN API item to our unified OsintDocument."""
    item_type = item.get("type")
    if item_type not in ("story", "comment"):
        return None

    # Skip dead/deleted items
    if item.get("dead") or item.get("deleted"):
        return None

    content_type = ContentType.STORY if item_type == "story" else ContentType.COMMENT
    topic = HNConfig.KAFKA_TOPIC_STORIES if item_type == "story" else HNConfig.KAFKA_TOPIC_COMMENTS

    # Build content text: for stories it's the title + optional text;
    # for comments it's the comment body
    title = item.get("title", "")
    text = strip_html(item.get("text", ""))
    content_text = f"{title}\n{text}".strip() if title and text else (title or text)

    if not content_text:
        return None

    doc = OsintDocument(
        source=SourcePlatform.HACKER_NEWS,
        source_id=str(item["id"]),
        content_type=content_type,
        title=title or None,
        content_text=content_text,
        url=item.get("url"),
        created_at=item.get("time", int(time.time())),
        quality_signals=QualitySignals(
            score=item.get("score"),
            engagement_count=item.get("descendants"),
        ),
        parent_id=str(item["parent"]) if item.get("parent") else None,
        metadata={
            "hn_type": item_type,
            "hn_by": item.get("by", "anonymous"),
            "hn_kids_count": len(item.get("kids", [])),
            "kafka_topic": topic,
        },
    )
    doc.compute_dedup_hash()
    return doc


async def fetch_item(session: aiohttp.ClientSession, item_id: int) -> Optional[dict]:
    """Fetch a single HN item with timeout and error handling."""
    url = HNConfig.ITEM_URL.format(item_id=item_id)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=HNConfig.ITEM_FETCH_TIMEOUT)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data if data else None
            else:
                log.warning("HN API returned %d for item %d", resp.status, item_id)
                return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("Failed to fetch item %d: %s", item_id, e)
        return None


async def fetch_items_batch(
    session: aiohttp.ClientSession,
    item_ids: list[int],
    semaphore: asyncio.Semaphore,
) -> list[dict]:
    """Fetch multiple items concurrently with a concurrency limiter."""
    async def _fetch(item_id):
        async with semaphore:
            return await fetch_item(session, item_id)

    results = await asyncio.gather(*[_fetch(iid) for iid in item_ids])
    return [r for r in results if r is not None]


# ---------------------------------------------------------------------------
# Live Stream: Top Stories + New Stories Polling
# ---------------------------------------------------------------------------

class LiveStoryStream:
    """Polls Firebase for top/new stories, diffs against seen IDs,
    fetches new items and their top-level comments, publishes to Kafka."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._seen_ids: set[int] = set()
        self._semaphore = asyncio.Semaphore(HNConfig.MAX_CONCURRENT_FETCHES)

    async def run(self, session: aiohttp.ClientSession):
        log.info("LiveStoryStream started — polling every %ds", HNConfig.STORIES_POLL_INTERVAL)

        while True:
            try:
                await self._poll_cycle(session)
            except Exception as e:
                log.error("LiveStoryStream poll error: %s", e, exc_info=True)

            await asyncio.sleep(HNConfig.STORIES_POLL_INTERVAL)

    async def _poll_cycle(self, session: aiohttp.ClientSession):
        # Fetch both lists concurrently
        top_task = self._fetch_story_list(session, HNConfig.TOP_STORIES_URL)
        new_task = self._fetch_story_list(session, HNConfig.NEW_STORIES_URL)
        top_ids, new_ids = await asyncio.gather(top_task, new_task)

        all_ids = set(top_ids + new_ids)
        new_item_ids = all_ids - self._seen_ids

        if not new_item_ids:
            return

        log.info("Discovered %d new items (top=%d, new=%d, seen=%d)",
                 len(new_item_ids), len(top_ids), len(new_ids), len(self._seen_ids))

        # Fetch the new items
        items = await fetch_items_batch(session, list(new_item_ids), self._semaphore)

        published = 0
        comment_ids_to_fetch = []

        for item in items:
            doc = hn_item_to_document(item)
            if doc:
                topic = doc.metadata.get("kafka_topic", HNConfig.KAFKA_TOPIC_STORIES)
                self._publisher.publish(topic, doc.to_kafka_key(), doc.to_kafka_value())
                published += 1

                # Collect top-level comment IDs for high-engagement stories
                if item.get("type") == "story" and (item.get("descendants", 0) > 5):
                    kids = item.get("kids", [])[:10]  # Top 10 comments only
                    comment_ids_to_fetch.extend(kids)

        # Fetch top-level comments for popular stories
        if comment_ids_to_fetch:
            comments = await fetch_items_batch(session, comment_ids_to_fetch, self._semaphore)
            for citem in comments:
                cdoc = hn_item_to_document(citem)
                if cdoc:
                    self._publisher.publish(
                        HNConfig.KAFKA_TOPIC_COMMENTS,
                        cdoc.to_kafka_key(),
                        cdoc.to_kafka_value(),
                    )
                    published += 1

        self._seen_ids.update(all_ids)

        # Prevent unbounded memory growth — keep last 5000 IDs
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(sorted(self._seen_ids)[-3000:])

        self._publisher.flush(timeout=5.0)
        log.info("Published %d documents | Kafka stats: %s", published, self._publisher.stats)

    @staticmethod
    async def _fetch_story_list(session: aiohttp.ClientSession, url: str) -> list[int]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
        except Exception as e:
            log.warning("Failed to fetch story list from %s: %s", url, e)
        return []


# ---------------------------------------------------------------------------
# Keyword Monitor: Algolia Search Polling
# ---------------------------------------------------------------------------

class AlgoliaKeywordMonitor:
    """Periodically queries Algolia HN Search for configured keywords.
    Finds stories/comments mentioning tickers, products, or technologies."""

    def __init__(self, publisher: KafkaPublisher, keywords: list[str]):
        self._publisher = publisher
        self._keywords = keywords
        self._seen_algolia_ids: set[str] = set()
        self._semaphore = asyncio.Semaphore(HNConfig.MAX_CONCURRENT_FETCHES)

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "AlgoliaKeywordMonitor started — %d keywords, polling every %ds",
            len(self._keywords), HNConfig.ALGOLIA_POLL_INTERVAL,
        )

        while True:
            try:
                await self._search_cycle(session)
            except Exception as e:
                log.error("Algolia search error: %s", e, exc_info=True)

            await asyncio.sleep(HNConfig.ALGOLIA_POLL_INTERVAL)

    async def _search_cycle(self, session: aiohttp.ClientSession):
        total_published = 0

        for keyword in self._keywords:
            hits = await self._search_keyword(session, keyword)
            for hit in hits:
                object_id = hit.get("objectID", "")
                if object_id in self._seen_algolia_ids:
                    continue
                self._seen_algolia_ids.add(object_id)

                doc = self._algolia_hit_to_document(hit)
                if doc:
                    topic = doc.metadata.get("kafka_topic", HNConfig.KAFKA_TOPIC_STORIES)
                    self._publisher.publish(topic, doc.to_kafka_key(), doc.to_kafka_value())
                    total_published += 1

            # Brief pause between keywords to be polite
            await asyncio.sleep(0.5)

        # Prevent unbounded memory growth
        if len(self._seen_algolia_ids) > 10000:
            self._seen_algolia_ids = set(list(self._seen_algolia_ids)[-5000:])

        if total_published > 0:
            self._publisher.flush(timeout=5.0)
            log.info("Algolia monitor published %d documents", total_published)

    async def _search_keyword(self, session: aiohttp.ClientSession, keyword: str) -> list[dict]:
        """Search Algolia for recent HN content matching a keyword."""
        params = {
            "query": keyword,
            "tags": "(story,comment)",
            "hitsPerPage": 50,
            "numericFilters": f"created_at_i>{int(time.time()) - HNConfig.ALGOLIA_POLL_INTERVAL - 60}",
        }
        try:
            async with session.get(
                HNConfig.ALGOLIA_RECENT_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("hits", [])
                else:
                    log.warning("Algolia returned %d for keyword '%s'", resp.status, keyword)
        except Exception as e:
            log.warning("Algolia search failed for '%s': %s", keyword, e)
        return []

    @staticmethod
    def _algolia_hit_to_document(hit: dict) -> Optional[OsintDocument]:
        """Convert an Algolia search hit to OsintDocument."""
        is_story = "story" in hit.get("_tags", [])
        content_type = ContentType.STORY if is_story else ContentType.COMMENT

        title = hit.get("title", "")
        comment_text = strip_html(hit.get("comment_text", ""))
        story_text = strip_html(hit.get("story_text", ""))
        content_text = title or comment_text or story_text

        if not content_text:
            return None

        topic = HNConfig.KAFKA_TOPIC_STORIES if is_story else HNConfig.KAFKA_TOPIC_COMMENTS

        doc = OsintDocument(
            source=SourcePlatform.HACKER_NEWS,
            source_id=hit.get("objectID", ""),
            content_type=content_type,
            title=title or None,
            content_text=content_text,
            url=hit.get("url"),
            created_at=hit.get("created_at_i", int(time.time())),
            quality_signals=QualitySignals(
                score=hit.get("points"),
                engagement_count=hit.get("num_comments"),
            ),
            parent_id=str(hit["parent_id"]) if hit.get("parent_id") else None,
            entity_id=hit.get("story_id") and str(hit["story_id"]),
            metadata={
                "hn_type": "story" if is_story else "comment",
                "hn_by": hit.get("author", "anonymous"),
                "algolia_tags": hit.get("_tags", []),
                "kafka_topic": topic,
            },
        )
        doc.compute_dedup_hash()
        return doc


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


async def main():
    # Wait for Kafka to be available
    await wait_for_kafka(HNConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(HNConfig.KAFKA_BOOTSTRAP)

    # Graceful shutdown
    shutdown_event = asyncio.Event()

    async with aiohttp.ClientSession(
        headers={"User-Agent": "OSINTPipeline/1.0 (research)"},
        connector=aiohttp.TCPConnector(limit=50),
    ) as session:
        live_stream = LiveStoryStream(publisher)
        algolia_monitor = AlgoliaKeywordMonitor(publisher, HNConfig.MONITOR_KEYWORDS)

        # Run both ingestion modes concurrently
        tasks = [
            asyncio.create_task(live_stream.run(session)),
            asyncio.create_task(algolia_monitor.run(session)),
        ]

        log.info("=" * 60)
        log.info("HN Connector running — 2 ingestion modes active")
        log.info("  Live stream:      polling every %ds", HNConfig.STORIES_POLL_INTERVAL)
        log.info("  Algolia keywords:  %s", HNConfig.MONITOR_KEYWORDS)
        log.info("  Kafka topics:      %s, %s", HNConfig.KAFKA_TOPIC_STORIES, HNConfig.KAFKA_TOPIC_COMMENTS)
        log.info("=" * 60)

        ## Wait until Ctrl+C
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

        log.info("Cancelling tasks...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("HN Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
