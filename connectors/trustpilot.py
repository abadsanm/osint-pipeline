"""
OSINT Pipeline — TrustPilot Connector

Two ingestion modes (selected by configuration):

  1. OFFICIAL API (preferred): Uses TrustPilot Business API with an API key.
     Fetches reviews via cursor-based pagination (/v1/business-units/{id}/all-reviews).
     Requires a registered API key from developers.trustpilot.com (business account).

  2. WEB SCRAPE FALLBACK: Parses the __NEXT_DATA__ JSON embedded in public
     trustpilot.com review pages. No API key needed, but subject to anti-bot
     measures and TrustPilot ToS restrictions. Use responsibly.

Rate limits (API mode):
  - 833 calls per 5 minutes (~2.8 req/s)
  - 10,000 calls per hour
  - We target ~1 req/s to stay well under limits.

Rate limits (scrape mode):
  - Self-imposed 2s delay between page fetches to avoid IP blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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

class TrustPilotConfig:
    """All tunables in one place. Override via env vars in production."""

    # Official API
    API_BASE = "https://api.trustpilot.com/v1"
    API_KEY: Optional[str] = None  # Set via env var TRUSTPILOT_API_KEY

    # Scrape fallback
    SCRAPE_BASE = "https://www.trustpilot.com/review"
    SCRAPE_DELAY = 2.0  # seconds between page fetches

    # Businesses to monitor — list of domain names
    # e.g., ["example.com", "anotherbiz.com"]
    MONITOR_DOMAINS: list[str] = []

    # Polling interval for re-checking businesses for new reviews
    POLL_INTERVAL = 600  # 10 minutes

    # API rate limiting
    API_REQUEST_DELAY = 1.0  # seconds between API calls (~1 req/s)

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "consumer.reviews.trustpilot"

    # Pagination
    REVIEWS_PER_PAGE = 20  # API default
    MAX_PAGES_PER_POLL = 50  # safety cap per business per poll cycle

    # Ingestion mode
    MODE = "scrape"  # "api" or "scrape"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trustpilot-connector")


# ---------------------------------------------------------------------------
# Shared: Review → OsintDocument
# ---------------------------------------------------------------------------

def review_to_document(
    review: dict,
    business_domain: str,
    business_name: Optional[str] = None,
) -> Optional[OsintDocument]:
    """Convert a TrustPilot review (from API or scrape) to OsintDocument."""
    review_id = review.get("id", "")
    title = review.get("title", "")
    text = review.get("text", "")
    content_text = f"{title}\n{text}".strip() if title and text else (title or text)

    if not content_text:
        return None

    # Normalize star rating (1-5) to 0.0-1.0
    stars = review.get("stars")
    rating = (stars / 5.0) if stars else None

    # Parse created_at
    created_at_raw = review.get("createdAt")
    if isinstance(created_at_raw, str):
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            created_at = datetime.now(timezone.utc)
    elif isinstance(created_at_raw, (int, float)):
        created_at = datetime.fromtimestamp(created_at_raw, tz=timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    consumer_info = review.get("consumer", {})
    is_verified = review.get("isVerified", False)
    likes = review.get("numberOfLikes", 0)

    company_reply = review.get("companyReply")

    doc = OsintDocument(
        source=SourcePlatform.TRUSTPILOT,
        source_id=review_id,
        content_type=ContentType.REVIEW,
        title=title or None,
        content_text=content_text,
        url=f"https://www.trustpilot.com/reviews/{review_id}",
        created_at=created_at,
        rating=rating,
        entity_id=business_domain,
        quality_signals=QualitySignals(
            score=float(stars) if stars else None,
            engagement_count=likes,
            is_verified=is_verified,
        ),
        metadata={
            "business_domain": business_domain,
            "business_name": business_name,
            "consumer_display_name": consumer_info.get("displayName"),
            "consumer_location": consumer_info.get("displayLocation"),
            "stars": stars,
            "language": review.get("language"),
            "has_company_reply": company_reply is not None,
            "experienced_at": review.get("experiencedAt"),
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Mode 1: Official API
# ---------------------------------------------------------------------------

class TrustPilotAPI:
    """Fetches reviews using the official TrustPilot API with API key auth."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._headers = {"apikey": api_key}
        # Cache: domain → business_unit_id
        self._business_ids: dict[str, str] = {}

    async def resolve_business_id(
        self, session: aiohttp.ClientSession, domain: str
    ) -> Optional[str]:
        """Look up a business unit ID by domain name."""
        if domain in self._business_ids:
            return self._business_ids[domain]

        url = f"{TrustPilotConfig.API_BASE}/business-units/find"
        try:
            async with session.get(
                url,
                params={"name": domain},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    biz_id = data.get("id")
                    if biz_id:
                        self._business_ids[domain] = biz_id
                        log.info("Resolved %s → business unit %s", domain, biz_id)
                        return biz_id
                elif resp.status == 404:
                    log.warning("Business not found on TrustPilot: %s", domain)
                elif resp.status == 429:
                    log.warning("Rate limited resolving %s — backing off", domain)
                else:
                    log.warning("API returned %d resolving %s", resp.status, domain)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Failed to resolve business %s: %s", domain, e)
        return None

    async def fetch_reviews(
        self,
        session: aiohttp.ClientSession,
        business_id: str,
        since: Optional[datetime] = None,
    ) -> list[dict]:
        """Fetch reviews using cursor-based pagination via /all-reviews."""
        all_reviews = []
        page_token = None
        pages_fetched = 0

        while pages_fetched < TrustPilotConfig.MAX_PAGES_PER_POLL:
            params = {}
            if page_token:
                params["pageToken"] = page_token

            url = f"{TrustPilotConfig.API_BASE}/business-units/{business_id}/all-reviews"

            try:
                async with session.get(
                    url,
                    params=params,
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", "60"))
                        log.warning("Rate limited — sleeping %ds", retry_after)
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status != 200:
                        log.warning("API returned %d fetching reviews", resp.status)
                        break

                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Failed to fetch reviews: %s", e)
                break

            reviews = data.get("reviews", [])
            if not reviews:
                break

            # If we have a `since` cutoff, stop when we hit older reviews
            if since:
                filtered = []
                stop = False
                for r in reviews:
                    created = r.get("createdAt", "")
                    try:
                        review_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        review_dt = datetime.now(timezone.utc)
                    if review_dt < since:
                        stop = True
                        break
                    filtered.append(r)
                all_reviews.extend(filtered)
                if stop:
                    break
            else:
                all_reviews.extend(reviews)

            page_token = data.get("nextPageToken")
            if not page_token:
                break

            pages_fetched += 1
            await asyncio.sleep(TrustPilotConfig.API_REQUEST_DELAY)

        return all_reviews


# ---------------------------------------------------------------------------
# Mode 2: Web Scrape Fallback
# ---------------------------------------------------------------------------

class TrustPilotScraper:
    """Parses review data from TrustPilot's public website via __NEXT_DATA__."""

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]

    def __init__(self):
        self._ua_index = 0

    def _next_ua(self) -> str:
        ua = self.USER_AGENTS[self._ua_index % len(self.USER_AGENTS)]
        self._ua_index += 1
        return ua

    async def fetch_reviews(
        self,
        session: aiohttp.ClientSession,
        domain: str,
        max_pages: int = 0,
    ) -> tuple[list[dict], Optional[str]]:
        """Scrape reviews from trustpilot.com/review/{domain}.

        Returns (reviews, business_name).
        max_pages=0 means use config default.
        """
        if max_pages <= 0:
            max_pages = TrustPilotConfig.MAX_PAGES_PER_POLL

        all_reviews = []
        business_name = None

        for page_num in range(1, max_pages + 1):
            url = f"{TrustPilotConfig.SCRAPE_BASE}/{domain}"
            params = {"page": str(page_num), "sort": "recency"}

            try:
                async with session.get(
                    url,
                    params=params,
                    headers={"User-Agent": self._next_ua()},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status == 403:
                        log.warning("Blocked by TrustPilot (403) on page %d for %s", page_num, domain)
                        break
                    if resp.status == 404:
                        log.warning("Business not found: %s", domain)
                        break
                    if resp.status != 200:
                        log.warning("HTTP %d on page %d for %s", resp.status, page_num, domain)
                        break

                    html = await resp.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("Scrape failed for %s page %d: %s", domain, page_num, e)
                break

            reviews, biz_name = self._parse_next_data(html, domain)

            if biz_name and not business_name:
                business_name = biz_name

            if not reviews:
                break

            all_reviews.extend(reviews)
            log.info("Scraped page %d for %s: %d reviews", page_num, domain, len(reviews))

            await asyncio.sleep(TrustPilotConfig.SCRAPE_DELAY)

        return all_reviews, business_name

    @staticmethod
    def _parse_next_data(html: str, domain: str) -> tuple[list[dict], Optional[str]]:
        """Extract review data from __NEXT_DATA__ script tag."""
        match = re.search(
            r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*({.*?})\s*</script>',
            html,
            re.DOTALL,
        )
        if not match:
            log.warning("Could not find __NEXT_DATA__ for %s", domain)
            return [], None

        try:
            next_data = json.loads(match.group(1))
        except json.JSONDecodeError as e:
            log.warning("Failed to parse __NEXT_DATA__ JSON for %s: %s", domain, e)
            return [], None

        # Navigate the Next.js page props structure
        page_props = next_data.get("props", {}).get("pageProps", {})
        business_name = page_props.get("businessUnit", {}).get("displayName")

        raw_reviews = page_props.get("reviews", [])
        reviews = []
        for r in raw_reviews:
            # Normalize scrape format to match API format
            review = {
                "id": r.get("id", ""),
                "stars": r.get("rating"),
                "title": r.get("title", ""),
                "text": r.get("text", ""),
                "createdAt": r.get("dates", {}).get("publishedDate"),
                "experiencedAt": r.get("dates", {}).get("experiencedDate"),
                "isVerified": r.get("labels", {}).get("verification", {}).get("isVerified", False),
                "numberOfLikes": r.get("likes", 0),
                "language": r.get("language"),
                "consumer": {
                    "displayName": r.get("consumer", {}).get("displayName"),
                    "displayLocation": r.get("consumer", {}).get("displayLocation"),
                },
                "companyReply": r.get("reply"),
            }
            reviews.append(review)

        return reviews, business_name


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class TrustPilotConnector:
    """Orchestrates review ingestion from TrustPilot, supports API and scrape modes."""

    def __init__(self, publisher: KafkaPublisher, config: type = TrustPilotConfig):
        self._publisher = publisher
        self._config = config
        self._api = TrustPilotAPI(config.API_KEY) if config.API_KEY else None
        self._scraper = TrustPilotScraper()
        # Track last poll time per domain for incremental fetching
        self._last_poll: dict[str, datetime] = {}

    async def run(self, session: aiohttp.ClientSession):
        mode = self._config.MODE
        if mode == "api" and not self._api:
            log.error("API mode selected but TRUSTPILOT_API_KEY is not set. Falling back to scrape mode.")
            mode = "scrape"

        log.info(
            "TrustPilot connector started — mode=%s, domains=%s, poll_interval=%ds",
            mode, self._config.MONITOR_DOMAINS, self._config.POLL_INTERVAL,
        )

        while True:
            for domain in self._config.MONITOR_DOMAINS:
                try:
                    if mode == "api":
                        await self._poll_api(session, domain)
                    else:
                        await self._poll_scrape(session, domain)
                except Exception as e:
                    log.error("Error polling %s: %s", domain, e, exc_info=True)

            await asyncio.sleep(self._config.POLL_INTERVAL)

    async def _poll_api(self, session: aiohttp.ClientSession, domain: str):
        business_id = await self._api.resolve_business_id(session, domain)
        if not business_id:
            return

        since = self._last_poll.get(domain)
        reviews = await self._api.fetch_reviews(session, business_id, since=since)

        published = self._publish_reviews(reviews, domain)
        self._last_poll[domain] = datetime.now(timezone.utc)
        if published:
            log.info("API: published %d reviews for %s", published, domain)

    async def _poll_scrape(self, session: aiohttp.ClientSession, domain: str):
        reviews, business_name = await self._scraper.fetch_reviews(session, domain)

        published = self._publish_reviews(reviews, domain, business_name)
        self._last_poll[domain] = datetime.now(timezone.utc)
        if published:
            log.info("Scrape: published %d reviews for %s", published, domain)

    def _publish_reviews(
        self,
        reviews: list[dict],
        domain: str,
        business_name: Optional[str] = None,
    ) -> int:
        published = 0
        for review in reviews:
            doc = review_to_document(review, domain, business_name)
            if doc:
                self._publisher.publish(
                    self._config.KAFKA_TOPIC,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published += 1

        if published:
            self._publisher.flush(timeout=5.0)
        return published


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    api_key = os.environ.get("TRUSTPILOT_API_KEY")
    if api_key:
        TrustPilotConfig.API_KEY = api_key
        TrustPilotConfig.MODE = "api"

    domains = os.environ.get("TRUSTPILOT_DOMAINS", "")
    if domains:
        TrustPilotConfig.MONITOR_DOMAINS = [d.strip() for d in domains.split(",") if d.strip()]

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        TrustPilotConfig.KAFKA_BOOTSTRAP = bootstrap

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(TrustPilotConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()

    if not TrustPilotConfig.MONITOR_DOMAINS:
        log.error(
            "No domains configured. Set TRUSTPILOT_DOMAINS env var "
            "(comma-separated list of domains, e.g. 'example.com,another.com')"
        )
        return

    await wait_for_kafka(TrustPilotConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(TrustPilotConfig.KAFKA_BOOTSTRAP, client_id="trustpilot-connector")
    connector = TrustPilotConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("TrustPilot Connector running")
        log.info("  Mode:       %s", TrustPilotConfig.MODE)
        log.info("  Domains:    %s", TrustPilotConfig.MONITOR_DOMAINS)
        log.info("  Kafka topic: %s", TrustPilotConfig.KAFKA_TOPIC)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("TrustPilot Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
