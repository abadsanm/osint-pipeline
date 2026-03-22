"""
OSINT Pipeline — Seeking Alpha RSS Connector

Ingests financial articles from Seeking Alpha RSS feeds:
  1. Main feed (seekingalpha.com/feed.xml)
  2. Market news feed (seekingalpha.com/market-news/feed)

Extracts ticker symbols from titles/descriptions and publishes
normalized OsintDocuments to Kafka.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
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


class SeekingAlphaConfig:
    RSS_FEEDS = [
        "https://seekingalpha.com/feed.xml",
        "https://seekingalpha.com/market-news/feed",
    ]
    POLL_INTERVAL = 600  # 10 minutes
    REQUEST_DELAY = 3.0  # Delay between feed requests
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.seeking_alpha.articles"


# User-Agent rotation to avoid blocking
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.0; rv:121.0) Gecko/20100101 Firefox/121.0",
]

# Regex patterns for ticker extraction
_TICKER_PATTERNS = [
    re.compile(r"\$([A-Z]{1,5})\b"),         # $AAPL style
    re.compile(r"\b([A-Z]{1,5}):\s"),         # AAPL: style
    re.compile(r"\(([A-Z]{1,5})\)"),          # (AAPL) style
    re.compile(r"\b(NYSE|NASDAQ):\s*([A-Z]{1,5})\b"),  # NYSE:AAPL style
]

# Common words that look like tickers but aren't
_TICKER_BLACKLIST = {
    "A", "I", "AM", "PM", "CEO", "CFO", "IPO", "ETF", "GDP", "CPI",
    "FBI", "SEC", "FDA", "FED", "AI", "US", "UK", "EU", "THE", "FOR",
    "AND", "BUT", "NOT", "ALL", "NEW", "OLD", "BIG", "TOP", "LOW",
    "ARE", "HAS", "WAS", "HAD", "CAN", "MAY", "NOW", "OUR", "HIS",
    "HER", "ITS", "WHO", "HOW", "WHY", "EPS", "PE", "RSI", "ATH",
    "API", "RSS", "USA", "USD", "IMF", "ECB", "BOE", "RBI",
}


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("seeking-alpha-connector")


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


def _extract_tickers(text: str) -> list[str]:
    """Extract ticker symbols from text using multiple patterns."""
    tickers: list[str] = []
    seen: set[str] = set()

    for pattern in _TICKER_PATTERNS:
        for match in pattern.finditer(text):
            # NYSE:AAPL pattern has ticker in group 2
            ticker = match.group(match.lastindex or 1)
            ticker = ticker.upper()
            if ticker not in seen and ticker not in _TICKER_BLACKLIST and len(ticker) >= 1:
                seen.add(ticker)
                tickers.append(ticker)

    return tickers


def article_to_document(article: dict) -> Optional[OsintDocument]:
    """Convert a parsed RSS article dict into an OsintDocument."""
    title = article.get("title", "")
    if not title:
        return None

    description = _strip_html(article.get("description", ""))
    url = article.get("link", "")
    created_at = _parse_pubdate(article.get("pubDate", ""))

    content_text = f"{title}\n{description}".strip()

    # Extract tickers from title and description
    tickers = _extract_tickers(f"{title} {description}")
    entity_id = tickers[0] if tickers else None

    doc_id = hashlib.sha256(f"{title}:{url}".encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"seekingalpha:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=entity_id,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": "Seeking Alpha",
            "tickers": tickers,
            "feed_source": article.get("feed_source", ""),
            "data_source": "seeking_alpha_rss",
        },
    )
    doc.compute_dedup_hash()
    return doc


class SeekingAlphaFetcher:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_rss(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch and parse all Seeking Alpha RSS feeds."""
        articles: list[dict] = []

        for feed_url in SeekingAlphaConfig.RSS_FEEDS:
            ua = random.choice(_USER_AGENTS)
            try:
                async with session.get(
                    feed_url,
                    headers={
                        "User-Agent": ua,
                        "Accept": "application/rss+xml, application/xml, text/xml, */*",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Referer": "https://seekingalpha.com/",
                    },
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        log.warning(
                            "Seeking Alpha RSS %s returned status %d",
                            feed_url, resp.status,
                        )
                        continue
                    xml_text = await resp.text()
            except Exception as e:
                log.warning("Seeking Alpha RSS fetch failed for %s: %s", feed_url, e)
                continue

            try:
                root = ElementTree.fromstring(xml_text)
                for item in root.findall(".//item"):
                    articles.append({
                        "title": item.findtext("title", ""),
                        "description": item.findtext("description", ""),
                        "link": item.findtext("link", ""),
                        "pubDate": item.findtext("pubDate", ""),
                        "feed_source": feed_url,
                    })
            except ElementTree.ParseError:
                log.warning("Seeking Alpha RSS parse error for %s", feed_url)

            await asyncio.sleep(SeekingAlphaConfig.REQUEST_DELAY)

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


class SeekingAlphaConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = SeekingAlphaFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "Seeking Alpha connector started — poll every %ds",
            SeekingAlphaConfig.POLL_INTERVAL,
        )

        while True:
            try:
                published = 0

                articles = await self._fetcher.fetch_rss(session)
                for a in articles:
                    doc = article_to_document(a)
                    if doc:
                        self._publisher.publish(
                            SeekingAlphaConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d Seeking Alpha articles", published)

            except Exception as e:
                log.error("Seeking Alpha error: %s", e, exc_info=True)

            await asyncio.sleep(SeekingAlphaConfig.POLL_INTERVAL)


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
        SeekingAlphaConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("SEEKING_ALPHA_POLL_INTERVAL")
    if poll:
        SeekingAlphaConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(SeekingAlphaConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        SeekingAlphaConfig.KAFKA_BOOTSTRAP,
        client_id="seeking-alpha-connector",
    )
    connector = SeekingAlphaConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Seeking Alpha Connector running")
        log.info("  Feeds: %s", SeekingAlphaConfig.RSS_FEEDS)
        log.info("  Topic: %s", SeekingAlphaConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", SeekingAlphaConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
