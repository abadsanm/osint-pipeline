"""
OSINT Pipeline — USAspending.gov Connector

Ingests federal award data from USAspending.gov REST API.
Tracks contract awards, grants, loans, and other financial assistance
with NAICS code enrichment for sector classification.
Publishes normalized OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
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


class USAspendingConfig:
    AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
    NAICS_URL = "https://api.usaspending.gov/api/v2/references/naics/"
    POLL_INTERVAL = 3600  # 1 hour
    REQUEST_DELAY = 1.5
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "gov.usaspending.awards"
    LOOKBACK_DAYS = 7


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("usaspending-connector")


def _parse_date(text: str) -> datetime:
    """Parse date string to timezone-aware datetime."""
    if not text:
        return datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            dt = datetime.strptime(text.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def award_to_document(award: dict, naics_lookup: dict) -> Optional[OsintDocument]:
    """Convert a USAspending award dict into an OsintDocument."""
    description = award.get("Description") or award.get("description") or ""
    recipient = award.get("Recipient Name") or award.get("recipient_name") or ""
    award_id = award.get("Award ID") or award.get("award_id") or ""
    amount = award.get("Award Amount") or award.get("award_amount")
    agency = award.get("Awarding Agency") or award.get("awarding_agency") or ""
    start_date = award.get("Start Date") or award.get("start_date") or ""
    naics_code = award.get("NAICS Code") or award.get("naics_code") or ""

    title = description[:200] if description else f"Award to {recipient}"
    if not title.strip():
        return None

    # Enrich NAICS description
    naics_desc = naics_lookup.get(naics_code, "")
    content_parts = [title]
    if recipient:
        content_parts.append(f"Recipient: {recipient}")
    if agency:
        content_parts.append(f"Agency: {agency}")
    if naics_desc:
        content_parts.append(f"NAICS: {naics_code} - {naics_desc}")
    if amount is not None:
        content_parts.append(f"Amount: ${amount:,.2f}" if isinstance(amount, (int, float)) else f"Amount: {amount}")
    content_text = "\n".join(content_parts)

    entity_id = naics_code if naics_code else recipient if recipient else None

    dedup_key = award_id or f"{recipient}:{description[:100]}"
    doc_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    # Parse amount as float for quality_signals.score
    score = None
    if amount is not None:
        try:
            score = float(amount)
        except (ValueError, TypeError):
            pass

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"usaspending:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=f"https://www.usaspending.gov/award/{award_id}" if award_id else None,
        created_at=_parse_date(start_date),
        entity_id=entity_id,
        quality_signals=QualitySignals(score=score),
        metadata={
            "news_source": "USAspending.gov",
            "award_id": award_id,
            "recipient_name": recipient,
            "awarding_agency": agency,
            "naics_code": naics_code,
            "naics_description": naics_desc,
            "award_amount": amount,
            "data_source": "usaspending_api",
        },
    )
    doc.compute_dedup_hash()
    return doc


class USAspendingFetcher:
    def __init__(self):
        self._seen_awards: set[str] = set()
        self._naics_cache: dict[str, str] = {}

    async def fetch_naics_lookup(self, session: aiohttp.ClientSession) -> dict[str, str]:
        """Fetch and cache NAICS code descriptions."""
        if self._naics_cache:
            return self._naics_cache

        try:
            async with session.get(
                USAspendingConfig.NAICS_URL,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("NAICS lookup returned status %d", resp.status)
                    return {}
                data = await resp.json()

            results = data.get("results", [])
            for item in results:
                code = str(item.get("naics_code", ""))
                desc = item.get("naics_description", "")
                if code and desc:
                    self._naics_cache[code] = desc

            log.info("Cached %d NAICS codes", len(self._naics_cache))
        except Exception as e:
            log.warning("NAICS lookup failed: %s", e)

        return self._naics_cache

    async def fetch_awards(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch recent awards from USAspending.gov."""
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(days=USAspendingConfig.LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        payload = {
            "filters": {
                "time_period": [{"start_date": start_date, "end_date": end_date}],
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": [
                "Award ID", "Recipient Name", "Award Amount",
                "Awarding Agency", "Description", "Start Date", "NAICS Code",
            ],
            "limit": 25,
            "order": "desc",
            "sort": "Award Amount",
        }

        try:
            async with session.post(
                USAspendingConfig.AWARDS_URL,
                json=payload,
                headers={
                    "User-Agent": "OSINTPipeline/1.0",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("USAspending API returned status %d", resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            log.warning("USAspending API fetch failed: %s", e)
            return []

        results = data.get("results", [])
        log.info("Fetched %d awards from USAspending.gov", len(results))
        return self._deduplicate(results)

    def _deduplicate(self, awards: list[dict]) -> list[dict]:
        """Deduplicate awards by Award ID."""
        new = []
        for award in awards:
            key = award.get("Award ID") or award.get("award_id") or ""
            if not key:
                key = hashlib.sha256(str(award).encode()).hexdigest()[:16]
            if key not in self._seen_awards:
                self._seen_awards.add(key)
                new.append(award)
        if len(self._seen_awards) > 5000:
            self._seen_awards = set(list(self._seen_awards)[-2500:])
        return new


class USAspendingConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = USAspendingFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("USAspending connector started — poll every %ds", USAspendingConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                # Fetch NAICS lookup (cached after first call)
                naics_lookup = await self._fetcher.fetch_naics_lookup(session)
                await asyncio.sleep(USAspendingConfig.REQUEST_DELAY)

                # Fetch awards
                awards = await self._fetcher.fetch_awards(session)
                for award in awards:
                    doc = award_to_document(award, naics_lookup)
                    if doc:
                        self._publisher.publish(
                            USAspendingConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d USAspending awards", published)

            except Exception as e:
                log.error("USAspending error: %s", e, exc_info=True)

            await asyncio.sleep(USAspendingConfig.POLL_INTERVAL)


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    import os
    from pathlib import Path

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        USAspendingConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("USASPENDING_POLL_INTERVAL")
    if poll:
        USAspendingConfig.POLL_INTERVAL = int(poll)

    lookback = os.environ.get("USASPENDING_LOOKBACK_DAYS")
    if lookback:
        USAspendingConfig.LOOKBACK_DAYS = int(lookback)

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(USAspendingConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()
    await wait_for_kafka(USAspendingConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(USAspendingConfig.KAFKA_BOOTSTRAP, client_id="usaspending-connector")
    connector = USAspendingConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("USAspending.gov Connector running")
        log.info("  Topic: %s", USAspendingConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", USAspendingConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
