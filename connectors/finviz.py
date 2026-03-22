"""
OSINT Pipeline — Finviz News Connector

Scrapes the Finviz news page for financial headlines and links.
Publishes normalized OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
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


class FinvizConfig:
    NEWS_URL = "https://finviz.com/news.ashx"
    POLL_INTERVAL = 900  # 15 minutes
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.finviz.news"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("finviz-connector")

# Regex to find ticker symbols in headline text (e.g., AAPL, TSLA, NVDA)
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Common English words to exclude from ticker matching
_TICKER_EXCLUDE = {
    "A", "I", "AM", "AN", "AS", "AT", "BE", "BY", "DO", "GO", "HE", "IF",
    "IN", "IS", "IT", "ME", "MY", "NO", "OF", "OK", "ON", "OR", "SO", "TO",
    "UP", "US", "WE", "CEO", "CFO", "CTO", "COO", "IPO", "SEC", "ETF",
    "GDP", "CPI", "FBI", "FDA", "FED", "THE", "AND", "FOR", "ARE", "BUT",
    "NOT", "YOU", "ALL", "CAN", "HER", "WAS", "ONE", "OUR", "OUT", "HAS",
    "NEW", "NOW", "OLD", "SEE", "WAY", "WHO", "DID", "GET", "HIT", "HOW",
    "ITS", "LET", "MAY", "SAY", "SHE", "TOO", "USE", "AI", "EV", "UK",
    "EU", "UN", "VS", "PE", "CEO", "TOP", "BIG", "LOW", "HIGH", "DEAL",
    "WEEK", "YEAR", "SAYS", "WILL", "ALSO", "JUST", "MORE", "THAN", "OVER",
    "MOST", "SOME", "INTO", "HAVE", "BEEN", "LIKE", "WHAT", "WHEN", "WITH",
    "THIS", "THAT", "FROM", "AFTER", "DOWN", "BACK", "TECH", "DATA", "BANK",
    "FUND", "RATE", "LOSS", "GAIN", "RISE", "FALL", "DROP", "JUMP", "PUSH",
    "PULL", "SELL", "BUY", "HOLD", "MOVE", "PLAN", "SIGN", "NEWS", "WARN",
    "CASH", "DEBT", "BOND", "GOLD", "DRUG", "AUTO", "FIRM", "CALL", "POST",
    "POLL", "VOTE", "MAKE", "TAKE", "GIVE", "LOOK", "COME", "VERY", "ONLY",
    "MUCH", "SAME", "LONG", "NEAR", "AMID", "LAST", "NEXT", "LATE", "PART",
    "SET", "PUT", "RUN", "CUT", "ADD", "OIL", "GAS", "WAR", "PAY", "TAX",
}


def _extract_tickers(text: str) -> list[str]:
    """Extract potential ticker symbols from headline text."""
    matches = _TICKER_RE.findall(text)
    return [m for m in matches if m not in _TICKER_EXCLUDE and len(m) >= 2]


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_finviz_html(html: str) -> list[dict]:
    """Parse Finviz news HTML to extract headlines and links.

    Finviz news page contains tables with rows of news items.
    Each row has a timestamp, headline link, and source.
    """
    articles: list[dict] = []

    # Match news table rows: <a ... href="URL" ...>TITLE</a>
    # Finviz news rows are in <tr> with class "nn-date" for timestamps
    # and links in the adjacent cell
    link_pattern = re.compile(
        r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*class="nn-tab-link"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    for match in link_pattern.finditer(html):
        url = match.group(1).strip()
        title = _strip_html(match.group(2)).strip()
        if title and url:
            articles.append({
                "title": title,
                "url": url,
            })

    # Fallback: broader link matching if the specific class didn't match
    if not articles:
        # Try matching any links within news table cells
        fallback_pattern = re.compile(
            r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        for match in fallback_pattern.finditer(html):
            url = match.group(1).strip()
            title = _strip_html(match.group(2)).strip()
            # Filter out non-article links (navigation, ads, etc.)
            if title and url and len(title) > 15 and "finviz.com" not in url:
                articles.append({
                    "title": title,
                    "url": url,
                })

    return articles


def article_to_document(article: dict) -> Optional[OsintDocument]:
    """Convert a parsed Finviz article dict into an OsintDocument."""
    title = article.get("title", "")
    if not title:
        return None

    url = article.get("url", "")
    tickers = _extract_tickers(title)
    entity_id = tickers[0] if tickers else None

    doc_id = hashlib.sha256(url.encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"finviz:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=title,
        url=url,
        created_at=datetime.now(timezone.utc),
        entity_id=entity_id,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": "Finviz",
            "tickers_mentioned": tickers,
            "data_source": "finviz_scrape",
        },
    )
    doc.compute_dedup_hash()
    return doc


class FinvizFetcher:
    def __init__(self):
        self._seen_urls: set[str] = set()

    async def fetch_news(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch and parse the Finviz news page."""
        try:
            async with session.get(
                FinvizConfig.NEWS_URL,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    log.warning("Finviz news returned status %d", resp.status)
                    return []
                html = await resp.text()
        except Exception as e:
            log.warning("Finviz news fetch failed: %s", e)
            return []

        articles = _parse_finviz_html(html)
        log.info("Parsed %d articles from Finviz HTML", len(articles))
        return self._deduplicate(articles)

    def _deduplicate(self, articles: list[dict]) -> list[dict]:
        """Deduplicate articles by URL."""
        new = []
        for a in articles:
            url = a.get("url", "")
            if url and url not in self._seen_urls:
                self._seen_urls.add(url)
                new.append(a)
        # Prevent unbounded memory growth
        if len(self._seen_urls) > 5000:
            self._seen_urls = set(list(self._seen_urls)[-2500:])
        return new


class FinvizConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = FinvizFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Finviz connector started — poll every %ds", FinvizConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                articles = await self._fetcher.fetch_news(session)
                for a in articles:
                    doc = article_to_document(a)
                    if doc:
                        self._publisher.publish(
                            FinvizConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d Finviz news articles", published)

            except Exception as e:
                log.error("Finviz error: %s", e, exc_info=True)

            await asyncio.sleep(FinvizConfig.POLL_INTERVAL)


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
        FinvizConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("FINVIZ_POLL_INTERVAL")
    if poll:
        FinvizConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(FinvizConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(FinvizConfig.KAFKA_BOOTSTRAP, client_id="finviz-connector")
    connector = FinvizConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Finviz News Connector running")
        log.info("  URL:   %s", FinvizConfig.NEWS_URL)
        log.info("  Topic: %s", FinvizConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", FinvizConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
