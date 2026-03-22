"""
OSINT Pipeline — SBIR.gov Connector

Ingests Small Business Innovation Research (SBIR) and Small Business
Technology Transfer (STTR) award data from the SBIR.gov API.
Publishes normalized OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
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


class SBIRConfig:
    API_URL = "https://www.sbir.gov/api/awards.json"
    POLL_INTERVAL = 7200  # 2 hours
    REQUEST_DELAY = 1.5
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "gov.sbir.awards"
    ROWS = 25


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sbir-connector")


def _parse_date(text: str) -> datetime:
    """Parse date string to timezone-aware datetime."""
    if not text:
        return datetime.now(timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d", "%m/%d/%Y", "%Y"):
        try:
            dt = datetime.strptime(str(text).strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def award_to_document(award: dict) -> Optional[OsintDocument]:
    """Convert an SBIR award dict into an OsintDocument."""
    title = award.get("award_title") or award.get("title") or ""
    if not title.strip():
        return None

    firm = award.get("firm") or ""
    amount = award.get("award_amount")
    agency = award.get("agency") or ""
    program = award.get("program") or ""  # SBIR or STTR
    award_year = award.get("award_year") or ""
    abstract = award.get("abstract") or ""

    # Build content
    content_parts = [title]
    if abstract:
        content_parts.append(abstract)
    if firm:
        content_parts.append(f"Firm: {firm}")
    if agency:
        content_parts.append(f"Agency: {agency}")
    if program:
        content_parts.append(f"Program: {program}")
    if amount is not None:
        try:
            content_parts.append(f"Amount: ${float(amount):,.2f}")
        except (ValueError, TypeError):
            content_parts.append(f"Amount: {amount}")
    content_text = "\n".join(content_parts)

    entity_id = firm if firm else None

    dedup_key = f"{firm}:{title}:{award_year}"
    doc_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    # Parse amount as float for quality_signals.score
    score = None
    if amount is not None:
        try:
            score = float(amount)
        except (ValueError, TypeError):
            pass

    created_at = _parse_date(str(award_year)) if award_year else datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"sbir:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url="https://www.sbir.gov/",
        created_at=created_at,
        entity_id=entity_id,
        quality_signals=QualitySignals(score=score),
        metadata={
            "news_source": "SBIR.gov",
            "firm": firm,
            "agency": agency,
            "program": program,
            "award_year": str(award_year),
            "award_amount": amount,
            "abstract": abstract[:500] if abstract else "",
            "data_source": "sbir_api",
        },
    )
    doc.compute_dedup_hash()
    return doc


class SBIRFetcher:
    def __init__(self):
        self._seen_awards: set[str] = set()

    async def fetch_awards(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch recent SBIR/STTR awards."""
        params = {
            "rows": str(SBIRConfig.ROWS),
            "start": "0",
        }

        try:
            async with session.get(
                SBIRConfig.API_URL,
                params=params,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("SBIR API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
        except Exception as e:
            log.warning("SBIR API fetch failed: %s", e)
            return []

        # The API may return a list directly or a dict with results
        if isinstance(data, list):
            results = data
        elif isinstance(data, dict):
            results = data.get("results", data.get("awards", []))
            if not results and "response" in data:
                results = data["response"].get("docs", [])
        else:
            results = []

        log.info("Fetched %d awards from SBIR.gov", len(results))
        return self._deduplicate(results)

    def _deduplicate(self, awards: list[dict]) -> list[dict]:
        """Deduplicate awards by firm+title."""
        new = []
        for award in awards:
            firm = award.get("firm") or ""
            title = award.get("award_title") or award.get("title") or ""
            year = award.get("award_year") or ""
            key = f"{firm}:{title}:{year}"
            if key not in self._seen_awards:
                self._seen_awards.add(key)
                new.append(award)
        if len(self._seen_awards) > 5000:
            self._seen_awards = set(list(self._seen_awards)[-2500:])
        return new


class SBIRConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = SBIRFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("SBIR connector started — poll every %ds", SBIRConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                awards = await self._fetcher.fetch_awards(session)
                for award in awards:
                    doc = award_to_document(award)
                    if doc:
                        self._publisher.publish(
                            SBIRConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d SBIR awards", published)

            except Exception as e:
                log.error("SBIR error: %s", e, exc_info=True)

            await asyncio.sleep(SBIRConfig.POLL_INTERVAL)


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

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        SBIRConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("SBIR_POLL_INTERVAL")
    if poll:
        SBIRConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(SBIRConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(SBIRConfig.KAFKA_BOOTSTRAP, client_id="sbir-connector")
    connector = SBIRConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("SBIR.gov Connector running")
        log.info("  Topic: %s", SBIRConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", SBIRConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
