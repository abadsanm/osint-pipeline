"""
OSINT Pipeline — Google Trends Connector

Three ingestion modes running concurrently:
  1. INTEREST OVER TIME: Tracks search interest for configured keywords
     (tickers, companies, technologies) over configurable time windows.
  2. TRENDING SEARCHES: Polls for daily trending search terms by country.
  3. RELATED QUERIES: Discovers rising/breakout queries related to keywords.

Uses pytrends (unofficial Google Trends wrapper). No auth required but
Google aggressively rate-limits by IP.

Rate limit strategy:
  - 5-10 second random delay between requests
  - Exponential backoff on 429 errors
  - Keywords batched in groups of 5 (pytrends limit)

Data note: Google Trends values are RELATIVE (0-100 scale normalized
per-request), not absolute search volume. Cross-request comparisons
require a common reference keyword.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

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

class GoogleTrendsConfig:
    """All tunables in one place. Override via env vars in production."""

    # Keywords to track (batched in groups of 5)
    MONITOR_KEYWORDS: list[str] = [
        "NVIDIA", "Tesla", "Bitcoin", "AI startup", "interest rates",
    ]

    # Trending search countries
    TRENDING_COUNTRIES: list[str] = ["united_states"]

    # Timeframe for interest_over_time
    TIMEFRAME = "now 7-d"  # Last 7 days (hourly granularity)
    GEO = ""               # Worldwide (set to "US" for US only)

    # Polling intervals (seconds)
    INTEREST_POLL_INTERVAL = 1800   # 30 minutes
    TRENDING_POLL_INTERVAL = 3600   # 1 hour
    RELATED_POLL_INTERVAL = 3600    # 1 hour

    # Rate limiting
    MIN_REQUEST_DELAY = 5.0   # seconds
    MAX_REQUEST_DELAY = 10.0  # seconds
    BACKOFF_BASE = 30.0       # seconds on 429
    MAX_BACKOFF = 300.0       # 5 minutes max

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "trends.google.interest"

    # pytrends config
    RETRIES = 3
    BACKOFF_FACTOR = 0.5
    PROXIES: list[str] = []   # HTTPS proxies for rotation


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("google-trends-connector")


# ---------------------------------------------------------------------------
# pytrends wrapper with rate limiting
# ---------------------------------------------------------------------------

class TrendsClient:
    """Wraps pytrends with rate limiting and error handling."""

    def __init__(self):
        self._pytrends = None
        self._last_request = 0.0
        self._current_backoff = 0.0

    def setup(self):
        """Initialize pytrends. Tries pytrends-modern first, falls back to pytrends."""
        try:
            from pytrends.request import TrendReq
        except ImportError:
            raise ImportError(
                "pytrends not installed. Run: pip install pytrends "
                "or pip install pytrends-modern"
            )

        kwargs = {
            "hl": "en-US",
            "tz": 360,
            "timeout": (10, 25),
            "retries": GoogleTrendsConfig.RETRIES,
            "backoff_factor": GoogleTrendsConfig.BACKOFF_FACTOR,
        }
        if GoogleTrendsConfig.PROXIES:
            kwargs["proxies"] = GoogleTrendsConfig.PROXIES

        self._pytrends = TrendReq(**kwargs)
        log.info("pytrends initialized")

    async def _rate_limit(self):
        """Wait for rate limit and add random jitter."""
        if self._current_backoff > 0:
            log.info("Backing off %.1fs", self._current_backoff)
            await asyncio.sleep(self._current_backoff)
            self._current_backoff = 0

        elapsed = time.time() - self._last_request
        delay = random.uniform(
            GoogleTrendsConfig.MIN_REQUEST_DELAY,
            GoogleTrendsConfig.MAX_REQUEST_DELAY,
        )
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request = time.time()

    def _handle_error(self, e: Exception) -> bool:
        """Handle rate limit errors. Returns True if should retry."""
        error_str = str(e).lower()
        if "429" in error_str or "too many" in error_str:
            self._current_backoff = min(
                self._current_backoff * 2 or GoogleTrendsConfig.BACKOFF_BASE,
                GoogleTrendsConfig.MAX_BACKOFF,
            )
            log.warning("Rate limited (429) — next backoff: %.0fs", self._current_backoff)
            return True
        return False

    async def get_interest_over_time(
        self, keywords: list[str], timeframe: str = "", geo: str = "",
    ) -> list[dict]:
        """Fetch interest over time for up to 5 keywords.

        Returns list of dicts: {keyword, date, value, timeframe, geo}
        """
        await self._rate_limit()

        tf = timeframe or GoogleTrendsConfig.TIMEFRAME
        g = geo or GoogleTrendsConfig.GEO

        try:
            self._pytrends.build_payload(keywords[:5], timeframe=tf, geo=g)
            df = self._pytrends.interest_over_time()
        except Exception as e:
            if self._handle_error(e):
                return []
            log.warning("interest_over_time failed: %s", e)
            return []

        if df is None or df.empty:
            return []

        results = []
        for date_idx, row in df.iterrows():
            for kw in keywords[:5]:
                if kw in df.columns:
                    results.append({
                        "keyword": kw,
                        "date": str(date_idx),
                        "value": int(row[kw]),
                        "timeframe": tf,
                        "geo": g,
                    })

        self._current_backoff = 0  # Reset backoff on success
        return results

    async def get_trending_searches(self, country: str = "united_states") -> list[dict]:
        """Fetch current trending searches for a country."""
        await self._rate_limit()

        try:
            df = self._pytrends.trending_searches(pn=country)
        except Exception as e:
            if self._handle_error(e):
                return []
            log.warning("trending_searches failed: %s", e)
            return []

        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            query = str(row.iloc[0]).strip()
            if query:
                results.append({
                    "query": query,
                    "country": country,
                })

        self._current_backoff = 0
        return results

    async def get_related_queries(
        self, keywords: list[str], geo: str = "",
    ) -> list[dict]:
        """Fetch related queries (top + rising) for keywords."""
        await self._rate_limit()

        g = geo or GoogleTrendsConfig.GEO

        try:
            self._pytrends.build_payload(keywords[:5], geo=g)
            related = self._pytrends.related_queries()
        except Exception as e:
            if self._handle_error(e):
                return []
            log.warning("related_queries failed: %s", e)
            return []

        if not related:
            return []

        results = []
        for keyword, data in related.items():
            for query_type in ("top", "rising"):
                df = data.get(query_type)
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    query = row.get("query", "")
                    value = row.get("value", 0)
                    if query:
                        results.append({
                            "source_keyword": keyword,
                            "related_query": str(query),
                            "query_type": query_type,
                            "value": int(value) if str(value).isdigit() else str(value),
                            "geo": g,
                        })

        self._current_backoff = 0
        return results


# ---------------------------------------------------------------------------
# Data → OsintDocument Converters
# ---------------------------------------------------------------------------

def interest_to_document(data: dict) -> Optional[OsintDocument]:
    """Convert an interest_over_time data point to OsintDocument."""
    keyword = data.get("keyword", "")
    value = data.get("value", 0)
    date_str = data.get("date", "")

    if not keyword:
        return None

    content_text = (
        f"Google Trends: '{keyword}' interest={value}/100 "
        f"({data.get('timeframe', '')}, geo={data.get('geo', 'worldwide')})"
    )

    try:
        # pandas Timestamp string parsing
        created_at = datetime.fromisoformat(str(date_str).replace(" ", "T").split("+")[0])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        created_at = datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.NEWS,  # Using NEWS as closest match
        source_id=f"gtrends:interest:{keyword}:{date_str}",
        content_type=ContentType.ARTICLE,
        title=f"Google Trends: {keyword}",
        content_text=content_text,
        created_at=created_at,
        entity_id=keyword,
        quality_signals=QualitySignals(
            score=float(value),
        ),
        metadata={
            "data_source": "google_trends",
            "signal_type": "interest_over_time",
            "keyword": keyword,
            "interest_value": value,
            "timeframe": data.get("timeframe", ""),
            "geo": data.get("geo", ""),
        },
    )
    doc.compute_dedup_hash()
    return doc


def trending_to_document(data: dict) -> Optional[OsintDocument]:
    """Convert a trending search to OsintDocument."""
    query = data.get("query", "")
    country = data.get("country", "")

    if not query:
        return None

    content_text = f"Trending on Google: '{query}' ({country})"
    now = datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"gtrends:trending:{query}:{now.strftime('%Y%m%d')}",
        content_type=ContentType.ARTICLE,
        title=f"Trending: {query}",
        content_text=content_text,
        created_at=now,
        entity_id=query,
        quality_signals=QualitySignals(
            score=100.0,  # Trending = peak interest
        ),
        metadata={
            "data_source": "google_trends",
            "signal_type": "trending_search",
            "query": query,
            "country": country,
        },
    )
    doc.compute_dedup_hash()
    return doc


def related_to_document(data: dict) -> Optional[OsintDocument]:
    """Convert a related query to OsintDocument."""
    source_kw = data.get("source_keyword", "")
    related_query = data.get("related_query", "")
    query_type = data.get("query_type", "")
    value = data.get("value", 0)

    if not source_kw or not related_query:
        return None

    content_text = (
        f"Google Trends: '{related_query}' is {query_type} related to '{source_kw}' "
        f"(value={value})"
    )
    now = datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"gtrends:related:{source_kw}:{related_query}:{now.strftime('%Y%m%d')}",
        content_type=ContentType.ARTICLE,
        title=f"Related to {source_kw}: {related_query}",
        content_text=content_text,
        created_at=now,
        entity_id=source_kw,
        quality_signals=QualitySignals(
            score=float(value) if isinstance(value, (int, float)) else None,
        ),
        metadata={
            "data_source": "google_trends",
            "signal_type": "related_query",
            "source_keyword": source_kw,
            "related_query": related_query,
            "query_type": query_type,
            "value": value,
            "geo": data.get("geo", ""),
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class GoogleTrendsConnector:
    """Orchestrates Google Trends data ingestion."""

    def __init__(self, publisher: KafkaPublisher, client: TrendsClient):
        self._publisher = publisher
        self._client = client

    async def run(self):
        """Run all ingestion modes concurrently."""
        tasks = [
            asyncio.create_task(self._interest_loop()),
            asyncio.create_task(self._trending_loop()),
            asyncio.create_task(self._related_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _interest_loop(self):
        log.info(
            "Interest tracking started — %d keywords, poll every %ds",
            len(GoogleTrendsConfig.MONITOR_KEYWORDS),
            GoogleTrendsConfig.INTEREST_POLL_INTERVAL,
        )

        while True:
            try:
                published = 0
                # Batch keywords in groups of 5
                keywords = GoogleTrendsConfig.MONITOR_KEYWORDS
                for i in range(0, len(keywords), 5):
                    batch = keywords[i : i + 5]
                    results = await self._client.get_interest_over_time(batch)
                    for data in results:
                        doc = interest_to_document(data)
                        if doc:
                            self._publisher.publish(
                                GoogleTrendsConfig.KAFKA_TOPIC,
                                doc.to_kafka_key(),
                                doc.to_kafka_value(),
                            )
                            published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Interest loop published %d data points", published)

            except Exception as e:
                log.error("Interest loop error: %s", e, exc_info=True)

            await asyncio.sleep(GoogleTrendsConfig.INTEREST_POLL_INTERVAL)

    async def _trending_loop(self):
        log.info(
            "Trending searches started — countries=%s, poll every %ds",
            GoogleTrendsConfig.TRENDING_COUNTRIES,
            GoogleTrendsConfig.TRENDING_POLL_INTERVAL,
        )

        while True:
            try:
                published = 0
                for country in GoogleTrendsConfig.TRENDING_COUNTRIES:
                    results = await self._client.get_trending_searches(country)
                    for data in results:
                        doc = trending_to_document(data)
                        if doc:
                            self._publisher.publish(
                                GoogleTrendsConfig.KAFKA_TOPIC,
                                doc.to_kafka_key(),
                                doc.to_kafka_value(),
                            )
                            published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Trending loop published %d searches", published)

            except Exception as e:
                log.error("Trending loop error: %s", e, exc_info=True)

            await asyncio.sleep(GoogleTrendsConfig.TRENDING_POLL_INTERVAL)

    async def _related_loop(self):
        log.info(
            "Related queries started — poll every %ds",
            GoogleTrendsConfig.RELATED_POLL_INTERVAL,
        )

        while True:
            try:
                published = 0
                keywords = GoogleTrendsConfig.MONITOR_KEYWORDS
                for i in range(0, len(keywords), 5):
                    batch = keywords[i : i + 5]
                    results = await self._client.get_related_queries(batch)
                    for data in results:
                        doc = related_to_document(data)
                        if doc:
                            self._publisher.publish(
                                GoogleTrendsConfig.KAFKA_TOPIC,
                                doc.to_kafka_key(),
                                doc.to_kafka_value(),
                            )
                            published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Related queries published %d entries", published)

            except Exception as e:
                log.error("Related queries error: %s", e, exc_info=True)

            await asyncio.sleep(GoogleTrendsConfig.RELATED_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
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
        time.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    keywords = os.environ.get("GTRENDS_KEYWORDS", "")
    if keywords:
        GoogleTrendsConfig.MONITOR_KEYWORDS = [k.strip() for k in keywords.split(",") if k.strip()]

    countries = os.environ.get("GTRENDS_COUNTRIES", "")
    if countries:
        GoogleTrendsConfig.TRENDING_COUNTRIES = [c.strip() for c in countries.split(",") if c.strip()]

    geo = os.environ.get("GTRENDS_GEO")
    if geo is not None:
        GoogleTrendsConfig.GEO = geo

    timeframe = os.environ.get("GTRENDS_TIMEFRAME")
    if timeframe:
        GoogleTrendsConfig.TIMEFRAME = timeframe

    proxies = os.environ.get("GTRENDS_PROXIES", "")
    if proxies:
        GoogleTrendsConfig.PROXIES = [p.strip() for p in proxies.split(",") if p.strip()]

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        GoogleTrendsConfig.KAFKA_BOOTSTRAP = bootstrap


async def async_main():
    load_config_from_env()

    wait_for_kafka(GoogleTrendsConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        GoogleTrendsConfig.KAFKA_BOOTSTRAP, client_id="google-trends-connector",
    )

    client = TrendsClient()
    client.setup()

    connector = GoogleTrendsConnector(publisher, client)

    log.info("=" * 60)
    log.info("Google Trends Connector running")
    log.info("  Keywords:   %s", GoogleTrendsConfig.MONITOR_KEYWORDS)
    log.info("  Countries:  %s", GoogleTrendsConfig.TRENDING_COUNTRIES)
    log.info("  Timeframe:  %s", GoogleTrendsConfig.TIMEFRAME)
    log.info("  Geo:        %s", GoogleTrendsConfig.GEO or "worldwide")
    log.info("  Kafka topic: %s", GoogleTrendsConfig.KAFKA_TOPIC)
    log.info("=" * 60)

    try:
        await connector.run()
    except asyncio.CancelledError:
        pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("Google Trends Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(async_main())
