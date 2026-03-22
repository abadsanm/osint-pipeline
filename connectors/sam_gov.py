"""
OSINT Pipeline — SAM.gov Connector

Ingests federal contract opportunities from SAM.gov.
Uses the REST API with API key if available, otherwise falls back to the
public search RSS endpoint.
Publishes normalized OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
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


class SamGovConfig:
    API_URL = "https://api.sam.gov/opportunities/v2/search"
    PUBLIC_RSS_URL = (
        "https://sam.gov/api/prod/sgs/v1/search/"
        "?index=opp&q=technology&sort=-modifiedDate&size=25"
    )
    API_KEY: Optional[str] = None
    POLL_INTERVAL = 3600  # 1 hour
    REQUEST_DELAY = 2.0
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "gov.sam.opportunities"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sam-gov-connector")


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_date(text: str) -> datetime:
    """Parse various date formats to timezone-aware datetime."""
    if not text:
        return datetime.now(timezone.utc)
    # Try ISO format first (API responses)
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


def opportunity_to_document(opp: dict) -> Optional[OsintDocument]:
    """Convert a parsed SAM.gov opportunity dict into an OsintDocument."""
    title = opp.get("title", "")
    if not title:
        return None

    description = opp.get("description", "")
    solicitation_number = opp.get("solicitationNumber", "")
    opp_type = opp.get("type", "")
    posted_date = _parse_date(opp.get("postedDate", ""))
    response_deadline = opp.get("responseDeadLine", "")
    naics_code = opp.get("naicsCode", "")
    url = opp.get("url", "")

    content_text = f"{title}\n{description}".strip()

    # Use NAICS code or solicitation number as entity_id
    entity_id = naics_code if naics_code else solicitation_number if solicitation_number else None

    # Deduplicate by solicitation number
    dedup_key = solicitation_number or title
    doc_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"sam_gov:{doc_id}",
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=url,
        created_at=posted_date,
        entity_id=entity_id,
        quality_signals=QualitySignals(),
        metadata={
            "news_source": "SAM.gov",
            "solicitation_number": solicitation_number,
            "opportunity_type": opp_type,
            "response_deadline": response_deadline,
            "naics_code": naics_code,
            "data_source": "sam_gov_api",
        },
    )
    doc.compute_dedup_hash()
    return doc


class SamGovFetcher:
    def __init__(self):
        self._seen_solicitations: set[str] = set()

    async def fetch_opportunities(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch opportunities from SAM.gov API or public RSS fallback."""
        if SamGovConfig.API_KEY:
            return await self._fetch_api(session)
        return await self._fetch_public(session)

    async def _fetch_api(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch from the official SAM.gov REST API (requires API key)."""
        opportunities: list[dict] = []

        posted_from = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%m/%d/%Y")
        params = {
            "api_key": SamGovConfig.API_KEY,
            "postedFrom": posted_from,
            "limit": "25",
            "offset": "0",
        }

        try:
            async with session.get(
                SamGovConfig.API_URL,
                params=params,
                headers={"User-Agent": "OSINTPipeline/1.0"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("SAM.gov API returned status %d", resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            log.warning("SAM.gov API fetch failed: %s", e)
            return []

        opp_list = data.get("opportunitiesData", [])
        if not opp_list:
            opp_list = data.get("_embedded", {}).get("results", [])

        for item in opp_list:
            solicitation = item.get("solicitationNumber", "")
            opp_url = f"https://sam.gov/opp/{item.get('noticeId', '')}/view" if item.get("noticeId") else ""
            opportunities.append({
                "title": item.get("title", ""),
                "description": _strip_html(item.get("description", "")),
                "solicitationNumber": solicitation,
                "type": item.get("type", item.get("noticeType", "")),
                "postedDate": item.get("postedDate", ""),
                "responseDeadLine": item.get("responseDeadLine", item.get("responseDeadline", "")),
                "naicsCode": item.get("naicsCode", ""),
                "url": opp_url,
            })

        return self._deduplicate(opportunities)

    async def _fetch_public(self, session: aiohttp.ClientSession) -> list[dict]:
        """Fetch from the public SAM.gov search endpoint (no API key needed)."""
        opportunities: list[dict] = []

        try:
            async with session.get(
                SamGovConfig.PUBLIC_RSS_URL,
                headers={
                    "User-Agent": "OSINTPipeline/1.0",
                    "Accept": "application/json,application/xml,text/xml",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("SAM.gov public search returned status %d", resp.status)
                    return []
                content_type = resp.headers.get("Content-Type", "")

                if "json" in content_type:
                    data = await resp.json()
                    results = data.get("_embedded", {}).get("results", [])
                    if not results:
                        results = data.get("opportunitiesData", [])
                    for item in results:
                        solicitation = item.get("solicitationNumber", item.get("_id", ""))
                        opportunities.append({
                            "title": item.get("title", item.get("_source", {}).get("title", "")),
                            "description": _strip_html(
                                item.get("description", item.get("_source", {}).get("description", ""))
                            ),
                            "solicitationNumber": solicitation,
                            "type": item.get("type", item.get("_source", {}).get("type", "")),
                            "postedDate": item.get("postedDate", item.get("_source", {}).get("postedDate", "")),
                            "responseDeadLine": item.get(
                                "responseDeadLine",
                                item.get("_source", {}).get("responseDeadLine", ""),
                            ),
                            "naicsCode": item.get("naicsCode", item.get("_source", {}).get("naicsCode", "")),
                            "url": f"https://sam.gov/opp/{solicitation}/view" if solicitation else "",
                        })
                else:
                    # Try XML/RSS parsing
                    xml_text = await resp.text()
                    try:
                        root = ElementTree.fromstring(xml_text)
                        for item in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
                            ns = "{http://www.w3.org/2005/Atom}"
                            title = item.findtext(f"{ns}title", "")
                            link_el = item.find(f"{ns}link")
                            link = link_el.get("href", "") if link_el is not None else ""
                            summary = _strip_html(item.findtext(f"{ns}summary", ""))
                            opportunities.append({
                                "title": title,
                                "description": summary,
                                "solicitationNumber": hashlib.sha256(title.encode()).hexdigest()[:12],
                                "type": "",
                                "postedDate": item.findtext(f"{ns}updated", ""),
                                "responseDeadLine": "",
                                "naicsCode": "",
                                "url": link,
                            })
                    except ElementTree.ParseError:
                        log.warning("SAM.gov public response parse error")

        except Exception as e:
            log.warning("SAM.gov public search fetch failed: %s", e)
            return []

        log.info("Parsed %d opportunities from SAM.gov", len(opportunities))
        return self._deduplicate(opportunities)

    def _deduplicate(self, opportunities: list[dict]) -> list[dict]:
        """Deduplicate opportunities by solicitation number."""
        new = []
        for opp in opportunities:
            key = opp.get("solicitationNumber", "") or opp.get("title", "")
            if key and key not in self._seen_solicitations:
                self._seen_solicitations.add(key)
                new.append(opp)
        # Prevent unbounded memory growth
        if len(self._seen_solicitations) > 5000:
            self._seen_solicitations = set(list(self._seen_solicitations)[-2500:])
        return new


class SamGovConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = SamGovFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("SAM.gov connector started — poll every %ds", SamGovConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                opportunities = await self._fetcher.fetch_opportunities(session)
                for opp in opportunities:
                    doc = opportunity_to_document(opp)
                    if doc:
                        self._publisher.publish(
                            SamGovConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d SAM.gov opportunities", published)

            except Exception as e:
                log.error("SAM.gov error: %s", e, exc_info=True)

            await asyncio.sleep(SamGovConfig.POLL_INTERVAL)


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
        SamGovConfig.KAFKA_BOOTSTRAP = bootstrap

    api_key = os.environ.get("SAM_GOV_API_KEY")
    if api_key:
        SamGovConfig.API_KEY = api_key

    poll = os.environ.get("SAM_GOV_POLL_INTERVAL")
    if poll:
        SamGovConfig.POLL_INTERVAL = int(poll)


async def main():
    load_config_from_env()
    await wait_for_kafka(SamGovConfig.KAFKA_BOOTSTRAP)

    mode = "API" if SamGovConfig.API_KEY else "Public RSS"
    publisher = KafkaPublisher(SamGovConfig.KAFKA_BOOTSTRAP, client_id="sam-gov-connector")
    connector = SamGovConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("SAM.gov Connector running")
        log.info("  Mode:  %s", mode)
        log.info("  Topic: %s", SamGovConfig.KAFKA_TOPIC)
        log.info("  Poll:  %ds", SamGovConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
