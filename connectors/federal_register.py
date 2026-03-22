"""
OSINT Pipeline — Federal Register Connector

Ingests federal regulatory documents from the Federal Register API.
Tracks proposed rules, final rules, and notices from all federal agencies.
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


class FederalRegisterConfig:
    API_URL = "https://www.federalregister.gov/api/v1/documents.json"
    POLL_INTERVAL = 3600  # 1 hour
    REQUEST_DELAY = 1.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "gov.federal_register.documents"
    LOOKBACK_DAYS = 7

    # Significance scoring by document type
    TYPE_SCORES = {
        "Rule": 100.0,
        "Proposed Rule": 80.0,
        "Notice": 50.0,
        "Presidential Document": 90.0,
        "Correction": 30.0,
    }


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("federal-register-connector")


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


def document_to_osint(doc_data: dict) -> Optional[OsintDocument]:
    """Convert a Federal Register document dict into an OsintDocument."""
    title = doc_data.get("title", "")
    if not title:
        return None

    abstract = doc_data.get("abstract") or ""
    doc_type = doc_data.get("type", "")
    agencies = doc_data.get("agencies", [])
    pub_date = doc_data.get("publication_date", "")
    html_url = doc_data.get("html_url", "")
    citation = doc_data.get("citation", "")
    document_number = doc_data.get("document_number", "")

    # Build agency names list
    agency_names = []
    for agency in agencies:
        name = agency.get("name") or agency.get("raw_name") or ""
        if name:
            agency_names.append(name)

    # entity_id = first agency name
    entity_id = agency_names[0] if agency_names else None

    # Build content
    content_parts = [title]
    if abstract:
        content_parts.append(abstract)
    if agency_names:
        content_parts.append(f"Agencies: {', '.join(agency_names)}")
    if doc_type:
        content_parts.append(f"Type: {doc_type}")
    if citation:
        content_parts.append(f"Citation: {citation}")
    content_text = "\n".join(content_parts)

    # Score by document type significance
    score = FederalRegisterConfig.TYPE_SCORES.get(doc_type, 40.0)

    dedup_key = document_number or title
    doc_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"federal_register:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=html_url or None,
        created_at=_parse_date(pub_date),
        entity_id=entity_id,
        quality_signals=QualitySignals(score=score),
        metadata={
            "news_source": "Federal Register",
            "document_number": document_number,
            "document_type": doc_type,
            "agencies": agency_names,
            "citation": citation,
            "abstract": abstract,
            "publication_date": pub_date,
            "data_source": "federal_register_api",
        },
    )
    doc.compute_dedup_hash()
    return doc


class FederalRegisterFetcher:
    def __init__(self):
        self._seen_documents: set[str] = set()

    async def fetch_documents(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch recent documents from the Federal Register API."""
        start_date = (datetime.now(timezone.utc) - timedelta(
            days=FederalRegisterConfig.LOOKBACK_DAYS
        )).strftime("%Y-%m-%d")

        params = {
            "conditions[publication_date][gte]": start_date,
            "per_page": "25",
            "order": "newest",
        }

        try:
            async with session.get(
                FederalRegisterConfig.API_URL,
                params=params,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("Federal Register API returned status %d", resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            log.warning("Federal Register API fetch failed: %s", e)
            return []

        results = data.get("results", [])
        log.info("Fetched %d documents from Federal Register", len(results))
        return self._deduplicate(results)

    def _deduplicate(self, documents: list[dict]) -> list[dict]:
        """Deduplicate documents by document_number."""
        new = []
        for doc in documents:
            key = doc.get("document_number", "") or doc.get("title", "")
            if key and key not in self._seen_documents:
                self._seen_documents.add(key)
                new.append(doc)
        if len(self._seen_documents) > 5000:
            self._seen_documents = set(list(self._seen_documents)[-2500:])
        return new


class FederalRegisterConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = FederalRegisterFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Federal Register connector started — poll every %ds",
                 FederalRegisterConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                documents = await self._fetcher.fetch_documents(session)
                for doc_data in documents:
                    doc = document_to_osint(doc_data)
                    if doc:
                        self._publisher.publish(
                            FederalRegisterConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d Federal Register documents", published)

            except Exception as e:
                log.error("Federal Register error: %s", e, exc_info=True)

            await asyncio.sleep(FederalRegisterConfig.POLL_INTERVAL)


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
        FederalRegisterConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("FEDERAL_REGISTER_POLL_INTERVAL")
    if poll:
        FederalRegisterConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(FederalRegisterConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(FederalRegisterConfig.KAFKA_BOOTSTRAP,
                               client_id="federal-register-connector")
    connector = FederalRegisterConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Federal Register Connector running")
        log.info("  Topic: %s", FederalRegisterConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", FederalRegisterConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
