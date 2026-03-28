"""
OSINT Pipeline — TechCrunch RSS Connector

Ingests articles from the TechCrunch RSS feed.
Publishes normalized OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from xml.etree import ElementTree

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


class TechCrunchConfig:
    RSS_FEED = "https://techcrunch.com/feed/"
    POLL_INTERVAL = 600  # 10 minutes
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "tech.techcrunch.articles"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("techcrunch-connector")

# XML namespace for dc:creator
DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


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


def _extract_categories(item: ElementTree.Element) -> list[str]:
    """Extract category tags from an RSS item."""
    return [cat.text for cat in item.findall("category") if cat.text]


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def article_to_document(article: dict) -> Optional[OsintDocument]:
    """Convert a parsed RSS article dict into an OsintDocument."""
    title = article.get("title", "")
    if not title:
        return None

    description = _strip_html(article.get("description", ""))
    url = article.get("link", "")
    creator = article.get("creator", "")
    categories = article.get("categories", [])
    created_at = _parse_pubdate(article.get("pubDate", ""))

    content_text = f"{title}\n{description}".strip()

    # Use first category as entity_id if available
    entity_id = categories[0] if categories else None

    doc_id = hashlib.sha256(f"{title}:{url}".encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"techcrunch:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=entity_id,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": "TechCrunch",
            "creator": creator,
            "categories": categories,
            "data_source": "techcrunch_rss",
        },
    )
    doc.compute_dedup_hash()
    return doc


class TechCrunchFetcher:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_rss(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch and parse the TechCrunch RSS feed."""
        articles: list[dict] = []

        try:
            async with session.get(
                TechCrunchConfig.RSS_FEED,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("TechCrunch RSS returned status %d", resp.status)
                    return []
                xml_text = await resp.text()
        except Exception as e:
            log.warning("TechCrunch RSS fetch failed: %s", e)
            return []

        try:
            root = ElementTree.fromstring(xml_text)
            for item in root.findall(".//item"):
                link = item.findtext("link", "")
                articles.append({
                    "title": item.findtext("title", ""),
                    "description": item.findtext("description", ""),
                    "link": link,
                    "pubDate": item.findtext("pubDate", ""),
                    "creator": item.findtext("dc:creator", "", DC_NS),
                    "categories": _extract_categories(item),
                })
        except ElementTree.ParseError:
            log.warning("TechCrunch RSS parse error")

        return self._deduplicate(articles)

    def _deduplicate(self, articles: list[dict]) -> list[dict]:
        """Deduplicate articles by URL."""
        new = []
        for a in articles:
            url = a.get("link", "")
            if url and url not in self._seen_urls:
                self._seen_urls.add(url)
                new.append(a)
        # Prevent unbounded memory growth
        if len(self._seen_urls) > 5000:
            self._seen_urls = set(list(self._seen_urls)[-2500:])
        return new


class TechCrunchConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = TechCrunchFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("TechCrunch connector started — poll every %ds", TechCrunchConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                articles = await self._fetcher.fetch_rss(session)
                for a in articles:
                    doc = article_to_document(a)
                    if doc:
                        self._publisher.publish(
                            TechCrunchConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d TechCrunch articles", published)

            except Exception as e:
                log.error("TechCrunch error: %s", e, exc_info=True)

            await asyncio.sleep(TechCrunchConfig.POLL_INTERVAL)


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


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
        TechCrunchConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("TECHCRUNCH_POLL_INTERVAL")
    if poll:
        TechCrunchConfig.POLL_INTERVAL = int(poll)

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(TechCrunchConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()
    await wait_for_kafka(TechCrunchConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(TechCrunchConfig.KAFKA_BOOTSTRAP, client_id="techcrunch-connector")
    connector = TechCrunchConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("TechCrunch Connector running")
        log.info("  Feed:  %s", TechCrunchConfig.RSS_FEED)
        log.info("  Topic: %s", TechCrunchConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", TechCrunchConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
