"""
OSINT Pipeline — Financial News Connector

Ingests financial news from multiple free sources:
  1. Finnhub market news (general + company-specific)
  2. Yahoo Finance RSS feeds
  3. Alpha Vantage news sentiment (if API key set)

Provides the "catalyst" signal that validates other OSINT sources.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
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

class FinancialNewsConfig:
    FINNHUB_BASE = "https://finnhub.io/api/v1"
    FINNHUB_API_KEY: Optional[str] = None

    YAHOO_RSS_FEEDS = [
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL,TSLA,NVDA,MSFT,AMZN,GOOGL,META,BTC-USD&region=US&lang=en-US",
    ]

    ALPHA_VANTAGE_BASE = "https://www.alphavantage.co/query"
    ALPHA_VANTAGE_KEY: Optional[str] = None

    WATCHLIST = ["AAPL", "TSLA", "NVDA", "MSFT", "AMZN", "GOOGL", "META", "JPM", "XOM"]

    POLL_INTERVAL = 300  # 5 minutes
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.news.articles"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("financial-news-connector")


def news_to_document(article: dict) -> Optional[OsintDocument]:
    headline = article.get("headline", article.get("title", ""))
    summary = article.get("summary", article.get("description", ""))
    url = article.get("url", article.get("link", ""))
    source_name = article.get("source", "")
    related = article.get("related", article.get("tickers", ""))

    if not headline:
        return None

    content_text = f"{headline}\n{summary}".strip()

    # Parse timestamp
    ts = article.get("datetime", article.get("pubDate", article.get("time_published", "")))
    if isinstance(ts, (int, float)):
        created_at = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(ts, str) and ts:
        try:
            created_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Extract primary ticker
    if isinstance(related, str):
        tickers = [t.strip() for t in related.split(",") if t.strip()]
    elif isinstance(related, list):
        tickers = related
    else:
        tickers = []

    entity_id = tickers[0] if tickers else None

    doc_id = hashlib.sha256(f"{headline}:{url}".encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"news:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=headline,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=entity_id,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": source_name,
            "tickers": tickers,
            "data_source": article.get("data_source", "finnhub"),
        },
    )
    doc.compute_dedup_hash()
    return doc


class FinancialNewsFetcher:
    def __init__(self):
        self._seen_ids: set[str] = set()

    async def fetch_finnhub_news(self, session: aiohttp.ClientSession) -> list[dict]:
        if not FinancialNewsConfig.FINNHUB_API_KEY:
            return []

        articles = []

        # General market news
        try:
            async with session.get(
                f"{FinancialNewsConfig.FINNHUB_BASE}/news",
                params={"category": "general", "token": FinancialNewsConfig.FINNHUB_API_KEY},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for a in (data or [])[:20]:
                        a["data_source"] = "finnhub"
                        articles.append(a)
        except Exception as e:
            log.warning("Finnhub general news failed: %s", e)

        # Company-specific news
        for ticker in FinancialNewsConfig.WATCHLIST[:5]:
            try:
                now = datetime.now(timezone.utc)
                async with session.get(
                    f"{FinancialNewsConfig.FINNHUB_BASE}/company-news",
                    params={
                        "symbol": ticker,
                        "from": now.strftime("%Y-%m-%d"),
                        "to": now.strftime("%Y-%m-%d"),
                        "token": FinancialNewsConfig.FINNHUB_API_KEY,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for a in (data or [])[:5]:
                            a["data_source"] = "finnhub"
                            a["related"] = ticker
                            articles.append(a)
            except Exception as e:
                log.warning("Finnhub company news for %s failed: %s", ticker, e)
            await asyncio.sleep(FinancialNewsConfig.REQUEST_DELAY)

        return self._deduplicate(articles)

    async def fetch_yahoo_rss(self, session: aiohttp.ClientSession) -> list[dict]:
        articles = []
        for feed_url in FinancialNewsConfig.YAHOO_RSS_FEEDS:
            try:
                async with session.get(
                    feed_url,
                    headers={"User-Agent": "OSINTPipeline/1.0"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        continue
                    xml_text = await resp.text()
            except Exception as e:
                log.warning("Yahoo RSS fetch failed: %s", e)
                continue

            try:
                root = ElementTree.fromstring(xml_text)
                for item in root.findall(".//item")[:20]:
                    articles.append({
                        "title": item.findtext("title", ""),
                        "description": item.findtext("description", ""),
                        "link": item.findtext("link", ""),
                        "pubDate": item.findtext("pubDate", ""),
                        "source": "Yahoo Finance",
                        "data_source": "yahoo_rss",
                    })
            except ElementTree.ParseError:
                log.warning("Yahoo RSS parse error")

        return self._deduplicate(articles)

    def _deduplicate(self, articles: list[dict]) -> list[dict]:
        new = []
        for a in articles:
            key = (a.get("headline") or a.get("title", ""))[:60]
            if key and key not in self._seen_ids:
                self._seen_ids.add(key)
                new.append(a)
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2500:])
        return new


class FinancialNewsConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = FinancialNewsFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Financial News connector started — poll every %ds", FinancialNewsConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                # Finnhub news
                finnhub_articles = await self._fetcher.fetch_finnhub_news(session)
                for a in finnhub_articles:
                    doc = news_to_document(a)
                    if doc:
                        self._publisher.publish(FinancialNewsConfig.KAFKA_TOPIC, doc.to_kafka_key(), doc.to_kafka_value())
                        published += 1

                await asyncio.sleep(FinancialNewsConfig.REQUEST_DELAY)

                # Yahoo RSS
                yahoo_articles = await self._fetcher.fetch_yahoo_rss(session)
                for a in yahoo_articles:
                    doc = news_to_document(a)
                    if doc:
                        self._publisher.publish(FinancialNewsConfig.KAFKA_TOPIC, doc.to_kafka_key(), doc.to_kafka_value())
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d articles (Finnhub: %d, Yahoo: %d)",
                             published, len(finnhub_articles), len(yahoo_articles))

            except Exception as e:
                log.error("Financial News error: %s", e, exc_info=True)

            await asyncio.sleep(FinancialNewsConfig.POLL_INTERVAL)


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
    key = os.environ.get("FINNHUB_API_KEY")
    if key:
        FinancialNewsConfig.FINNHUB_API_KEY = key
    av = os.environ.get("ALPHA_VANTAGE_KEY")
    if av:
        FinancialNewsConfig.ALPHA_VANTAGE_KEY = av
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        FinancialNewsConfig.KAFKA_BOOTSTRAP = bootstrap
    watchlist = os.environ.get("NEWS_WATCHLIST", "")
    if watchlist:
        FinancialNewsConfig.WATCHLIST = [t.strip() for t in watchlist.split(",") if t.strip()]


async def main():
    load_config_from_env()
    await wait_for_kafka(FinancialNewsConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(FinancialNewsConfig.KAFKA_BOOTSTRAP, client_id="financial-news-connector")
    connector = FinancialNewsConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Financial News Connector running")
        log.info("  Finnhub:   %s", "enabled" if FinancialNewsConfig.FINNHUB_API_KEY else "disabled")
        log.info("  Yahoo RSS: enabled")
        log.info("  Watchlist: %s", FinancialNewsConfig.WATCHLIST)
        log.info("  Topic:     %s", FinancialNewsConfig.KAFKA_TOPIC)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
