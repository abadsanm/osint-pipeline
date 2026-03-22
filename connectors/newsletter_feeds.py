"""
OSINT Pipeline — Multi-Feed Newsletter RSS Connector

Ingests articles from multiple newsletter/tech RSS feeds in a single connector.
Publishes normalized OsintDocuments to Kafka for downstream processing.

Feeds:
  - TLDR Newsletter (tldr.tech)
  - Pragmatic Engineer (Substack)
  - Sifted (European tech/startup)
  - Tech.EU (European tech ecosystem)
  - Inside.VC (venture capital)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree

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


@dataclass
class FeedEntry:
    """Configuration for a single RSS feed."""
    name: str
    url: str
    fallback_url: Optional[str] = None


class MultiFeedConfig:
    POLL_INTERVAL = 1800  # 30 minutes
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "tech.newsletter.articles"

    FEEDS: list[FeedEntry] = [
        FeedEntry(
            name="tldr",
            url="https://tldr.tech/api/rss/tech",
            fallback_url="https://tldr.tech/rss",
        ),
        FeedEntry(
            name="pragmatic_engineer",
            url="https://newsletter.pragmaticengineer.com/feed",
        ),
        FeedEntry(
            name="sifted",
            url="https://sifted.eu/feed",
        ),
        FeedEntry(
            name="tech_eu",
            url="https://tech.eu/feed/",
        ),
        FeedEntry(
            name="inside_vc",
            url="https://inside.com/venture-capital/feed",
            fallback_url="https://inside.com/venture-capital.rss",
        ),
    ]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("newsletter-feeds-connector")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_pubdate(text: str) -> datetime:
    """Parse RFC 2822 pubDate to timezone-aware datetime."""
    if not text:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(text)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def article_to_document(article: dict, feed_name: str) -> Optional[OsintDocument]:
    """Convert a parsed RSS article dict into an OsintDocument."""
    title = article.get("title", "")
    if not title:
        return None

    description = _strip_html(article.get("description", ""))
    url = article.get("link", "")
    created_at = _parse_pubdate(article.get("pubDate", ""))

    content_text = f"{title}\n{description}".strip()

    doc_id = hashlib.sha256(url.encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"{feed_name}:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=None,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": feed_name,
            "data_source": feed_name,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------


class MultiFeedFetcher:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_rss(
        self, session: aiohttp.ClientSession, feed: FeedEntry
    ) -> list[dict]:
        """Fetch and parse an RSS feed, trying fallback URL on failure."""
        articles = await self._try_fetch(session, feed.url)
        if not articles and feed.fallback_url:
            log.info("Primary URL failed for %s, trying fallback", feed.name)
            articles = await self._try_fetch(session, feed.fallback_url)
        return self._deduplicate(articles)

    async def _try_fetch(
        self, session: aiohttp.ClientSession, url: str
    ) -> list[dict]:
        """Fetch and parse a single RSS URL."""
        articles: list[dict] = []

        try:
            async with session.get(
                url,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("RSS %s returned status %d", url, resp.status)
                    return []
                xml_text = await resp.text()
        except Exception as e:
            log.warning("RSS fetch failed (%s): %s", url, e)
            return []

        try:
            root = ElementTree.fromstring(xml_text)
            # Standard RSS <item> elements
            for item in root.findall(".//item"):
                articles.append({
                    "title": item.findtext("title", ""),
                    "description": item.findtext("description", ""),
                    "link": item.findtext("link", ""),
                    "pubDate": item.findtext("pubDate", ""),
                })
            # Atom <entry> elements (Substack feeds)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.findall(".//atom:entry", ns):
                link_el = entry.find("atom:link[@rel='alternate']", ns)
                if link_el is None:
                    link_el = entry.find("atom:link", ns)
                link_href = link_el.get("href", "") if link_el is not None else ""
                articles.append({
                    "title": entry.findtext("atom:title", "", ns),
                    "description": entry.findtext("atom:summary", "", ns)
                    or entry.findtext("atom:content", "", ns),
                    "link": link_href,
                    "pubDate": entry.findtext("atom:published", "", ns)
                    or entry.findtext("atom:updated", "", ns),
                })
        except ElementTree.ParseError:
            log.warning("RSS parse error for %s", url)

        return articles

    def _deduplicate(self, articles: list[dict]) -> list[dict]:
        """Deduplicate articles by URL."""
        new = []
        for a in articles:
            url = a.get("link", "")
            if url and url not in self._seen_urls:
                self._seen_urls.add(url)
                new.append(a)
        # Prevent unbounded memory growth
        if len(self._seen_urls) > 10000:
            self._seen_urls = set(list(self._seen_urls)[-5000:])
        return new


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------


class MultiFeedConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = MultiFeedFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "MultiFeed connector started — %d feeds, poll every %ds",
            len(MultiFeedConfig.FEEDS),
            MultiFeedConfig.POLL_INTERVAL,
        )

        while True:
            try:
                total_published = 0

                for feed in MultiFeedConfig.FEEDS:
                    try:
                        articles = await self._fetcher.fetch_rss(session, feed)
                        published = 0
                        for a in articles:
                            doc = article_to_document(a, feed.name)
                            if doc:
                                self._publisher.publish(
                                    MultiFeedConfig.KAFKA_TOPIC,
                                    doc.to_kafka_key(),
                                    doc.to_kafka_value(),
                                )
                                published += 1
                        if published:
                            log.info(
                                "Published %d articles from %s", published, feed.name
                            )
                        total_published += published
                    except Exception as e:
                        log.error("Error fetching %s: %s", feed.name, e, exc_info=True)

                    # Delay between feeds to be polite
                    await asyncio.sleep(MultiFeedConfig.REQUEST_DELAY)

                if total_published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Total published this cycle: %d articles", total_published)

            except Exception as e:
                log.error("MultiFeed cycle error: %s", e, exc_info=True)

            await asyncio.sleep(MultiFeedConfig.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Kafka wait + env loading + entrypoint
# ---------------------------------------------------------------------------


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    start = time.time()
    while time.time() - start < timeout:
        try:
            metadata = admin.list_topics(timeout=5)
            if metadata.topics:
                return
        except Exception:
            pass
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable after {timeout}s")


def load_config_from_env():
    import os
    from pathlib import Path

    # Load .env from project root
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        MultiFeedConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("NEWSLETTER_POLL_INTERVAL")
    if poll:
        MultiFeedConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(MultiFeedConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        MultiFeedConfig.KAFKA_BOOTSTRAP, client_id="newsletter-feeds-connector"
    )
    connector = MultiFeedConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10)
    ) as session:
        log.info("=" * 60)
        log.info("Newsletter Feeds Connector running")
        log.info("  Feeds: %d", len(MultiFeedConfig.FEEDS))
        for f in MultiFeedConfig.FEEDS:
            log.info("    - %s: %s", f.name, f.url)
        log.info("  Topic: %s", MultiFeedConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", MultiFeedConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
