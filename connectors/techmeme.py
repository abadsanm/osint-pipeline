"""
OSINT Pipeline — Techmeme RSS Connector

Ingests articles from the Techmeme RSS feed.
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


class TechmemeConfig:
    RSS_FEED = "https://www.techmeme.com/feed.xml"
    POLL_INTERVAL = 600  # 10 minutes
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "tech.techmeme.articles"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("techmeme-connector")


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


def article_to_document(article: dict) -> Optional[OsintDocument]:
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
        source_id=f"techmeme:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=None,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": "Techmeme",
            "data_source": "techmeme_rss",
        },
    )
    doc.compute_dedup_hash()
    return doc


class TechmemeFetcher:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_rss(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch and parse the Techmeme RSS feed."""
        articles: list[dict] = []

        try:
            async with session.get(
                TechmemeConfig.RSS_FEED,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("Techmeme RSS returned status %d", resp.status)
                    return []
                xml_text = await resp.text()
        except Exception as e:
            log.warning("Techmeme RSS fetch failed: %s", e)
            return []

        try:
            root = ElementTree.fromstring(xml_text)
            for item in root.findall(".//item"):
                articles.append({
                    "title": item.findtext("title", ""),
                    "description": item.findtext("description", ""),
                    "link": item.findtext("link", ""),
                    "pubDate": item.findtext("pubDate", ""),
                })
        except ElementTree.ParseError:
            log.warning("Techmeme RSS parse error")

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


class TechmemeConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = TechmemeFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Techmeme connector started — poll every %ds", TechmemeConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                articles = await self._fetcher.fetch_rss(session)
                for a in articles:
                    doc = article_to_document(a)
                    if doc:
                        self._publisher.publish(
                            TechmemeConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d Techmeme articles", published)

            except Exception as e:
                log.error("Techmeme error: %s", e, exc_info=True)

            await asyncio.sleep(TechmemeConfig.POLL_INTERVAL)


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
        TechmemeConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("TECHMEME_POLL_INTERVAL")
    if poll:
        TechmemeConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(TechmemeConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(TechmemeConfig.KAFKA_BOOTSTRAP, client_id="techmeme-connector")
    connector = TechmemeConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Techmeme Connector running")
        log.info("  Feed:  %s", TechmemeConfig.RSS_FEED)
        log.info("  Topic: %s", TechmemeConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", TechmemeConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
