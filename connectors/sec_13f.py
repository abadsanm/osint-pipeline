"""
OSINT Pipeline — SEC 13F Institutional Holdings Connector

Polls SEC EDGAR EFTS full-text search API for recent 13F-HR filings.
Institutional investment managers with >$100M AUM must file 13F quarterly,
listing all their equity positions.

SEC EDGAR rate limits:
  - 10 requests/second max
  - User-Agent header required (company + contact email)
  - No API key needed

13F filings are quarterly, so a 2-hour poll interval is sufficient.
The connector extracts filing metadata (filer name, CIK, date) and
publishes as OsintDocument. The NER processor downstream will extract
ticker/company names from the content text.
"""

from __future__ import annotations

import asyncio
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
from connectors.kafka_publisher import KafkaPublisher, wait_for_bus, apply_poll_multiplier
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class Config13F:
    """All tunables in one place. Override via env vars in production."""

    # EDGAR endpoints
    EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

    # EDGAR rate limiting (10 req/s max, we use 5 req/s for safety)
    EDGAR_REQUEST_DELAY = 0.2  # 200ms between requests
    USER_AGENT = "Sentinel OSINT sentinel@example.com"

    # Polling interval (13F is quarterly — no need to poll fast)
    POLL_INTERVAL = 7200  # 2 hours

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.sec.13f"

    # Search parameters
    LOOKBACK_DAYS = 7  # How far back to search for new filings
    MAX_FILINGS_PER_POLL = 100  # Safety cap


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sec-13f-connector")


# ---------------------------------------------------------------------------
# EDGAR Rate Limiter
# ---------------------------------------------------------------------------

class EdgarRateLimiter:
    """Enforces SEC EDGAR rate limit of 10 req/s (we target 5 req/s)."""

    def __init__(self, delay: float = Config13F.EDGAR_REQUEST_DELAY):
        self._delay = delay
        self._last_request = 0.0

    async def wait(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._delay:
            await asyncio.sleep(self._delay - elapsed)
        self._last_request = time.time()


# ---------------------------------------------------------------------------
# SEC EDGAR: 13F Filing Discovery + Metadata Extraction
# ---------------------------------------------------------------------------

class Filing13FFetcher:
    """Discovers and extracts metadata from SEC 13F-HR filings."""

    def __init__(self):
        self._rate_limiter = EdgarRateLimiter()
        self._seen_accessions: set[str] = set()
        self._headers = {"User-Agent": Config13F.USER_AGENT}

    async def search_recent_filings(self, session: aiohttp.ClientSession) -> list[dict]:
        """Search EFTS for recent 13F-HR filings."""
        await self._rate_limiter.wait()

        start_date = (
            datetime.now(timezone.utc) - timedelta(days=Config13F.LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        params = {
            "q": '"13F-HR"',
            "forms": "13F-HR",
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
        }

        try:
            async with session.get(
                Config13F.EFTS_SEARCH_URL,
                params=params,
                headers={
                    **self._headers,
                    "Accept": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("EFTS search returned %d", resp.status)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("EFTS search failed: %s", e)
            return []

        hits = data.get("hits", {}).get("hits", [])
        new_filings = []

        for hit in hits[:Config13F.MAX_FILINGS_PER_POLL]:
            source = hit.get("_source", {})
            accession = source.get("adsh", "")
            if not accession or accession in self._seen_accessions:
                continue
            self._seen_accessions.add(accession)
            new_filings.append(source)

        # Prevent unbounded memory growth
        if len(self._seen_accessions) > 10000:
            self._seen_accessions = set(list(self._seen_accessions)[-5000:])

        return new_filings

    async def fetch_filing_index(
        self,
        session: aiohttp.ClientSession,
        cik: str,
        accession: str,
    ) -> Optional[dict]:
        """Fetch the filing index JSON to find the primary document and info table."""
        await self._rate_limiter.wait()

        accession_path = accession.replace("-", "")
        url = f"{Config13F.ARCHIVES_BASE}/{cik}/{accession_path}/index.json"

        try:
            async with session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("Filing index returned %d for %s", resp.status, accession)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Failed to fetch filing index for %s: %s", accession, e)
            return None

    async def fetch_primary_doc(
        self,
        session: aiohttp.ClientSession,
        cik: str,
        accession: str,
        filename: str,
    ) -> Optional[str]:
        """Fetch the primary document (cover page) of a 13F filing."""
        await self._rate_limiter.wait()

        accession_path = accession.replace("-", "")
        url = f"{Config13F.ARCHIVES_BASE}/{cik}/{accession_path}/{filename}"

        try:
            async with session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Failed to fetch primary doc for %s: %s", accession, e)
            return None


# ---------------------------------------------------------------------------
# 13F Filing → OsintDocument
# ---------------------------------------------------------------------------

def _extract_xml_cover_info(xml_text: str) -> dict:
    """Try to extract cover page info from the 13F XML primary document."""
    info = {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return info

    # 13F XML uses namespaces — try both with and without
    # Common namespace: http://www.sec.gov/edgar/document/thirteenf/informationtable
    for elem in root.iter():
        tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
        tag_lower = tag.lower()
        text = (elem.text or "").strip()
        if not text:
            continue

        if tag_lower in ("filingmanager", "name") and "manager_name" not in info:
            # The name element inside filingManager
            if tag_lower == "name":
                info["manager_name"] = text
        elif tag_lower == "form13ffilenumber":
            info["file_number"] = text
        elif tag_lower == "reportcalendarorquarter":
            info["report_quarter"] = text
        elif tag_lower in ("tableentrytotal", "tablevaluetotal"):
            if tag_lower == "tableentrytotal":
                info["total_entries"] = text
            else:
                info["total_value"] = text
        elif tag_lower == "othermanagersinfo":
            info["other_managers"] = text

    return info


def filing_to_document(filing: dict, cover_info: dict) -> Optional[OsintDocument]:
    """Convert a 13F filing search result + cover info into an OsintDocument."""
    accession = filing.get("adsh", "")
    display_names = filing.get("display_names", [])
    entity_name = display_names[0] if display_names else filing.get("entity_name", "Unknown Fund")
    ciks = filing.get("ciks", [])
    cik = ciks[0] if ciks else ""
    file_date = filing.get("file_date", "")
    period = filing.get("period_of_report", "")

    # Prefer cover page info for manager name
    manager_name = cover_info.get("manager_name", entity_name)
    report_quarter = cover_info.get("report_quarter", period)
    total_entries = cover_info.get("total_entries", "")
    total_value = cover_info.get("total_value", "")

    # Build content text
    content_parts = [
        f"13F-HR institutional holdings filing by {manager_name}.",
    ]
    if report_quarter:
        content_parts.append(f"Report for quarter ending {report_quarter}.")
    if total_entries:
        content_parts.append(f"Total holdings: {total_entries} positions.")
    if total_value:
        # total_value is in thousands (13F convention)
        try:
            val = int(total_value.replace(",", ""))
            content_parts.append(f"Total portfolio value: ${val * 1000:,.0f} ({val:,} x $1,000).")
        except (ValueError, TypeError):
            content_parts.append(f"Total portfolio value: {total_value} (x $1,000).")

    content_text = " ".join(content_parts)

    # Parse filing date
    if file_date:
        try:
            created_at = datetime.strptime(file_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Importance score based on total portfolio value (larger fund = more important)
    score = None
    if total_value:
        try:
            val = int(total_value.replace(",", ""))
            score = float(val * 1000)  # Convert from $1000 units to dollars
        except (ValueError, TypeError):
            pass

    title = f"13F Filing: {manager_name}"
    if report_quarter:
        title += f" — Q{_quarter_from_date(report_quarter)} {report_quarter[:4]}"

    doc = OsintDocument(
        source=SourcePlatform.SEC_EDGAR,
        source_id=f"13f:{accession}",
        content_type=ContentType.FILING,
        title=title,
        content_text=content_text,
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=13F-HR&dateb=&owner=include&count=40",
        created_at=created_at,
        entity_id=cik or accession,
        quality_signals=QualitySignals(
            score=score,
        ),
        metadata={
            "form_type": "13F-HR",
            "accession": accession,
            "cik": cik,
            "entity_name": entity_name,
            "manager_name": manager_name,
            "file_date": file_date,
            "period_of_report": period,
            "report_quarter": report_quarter,
            "total_entries": total_entries,
            "total_value_thousands": total_value,
        },
    )
    doc.compute_dedup_hash()
    return doc


def _quarter_from_date(date_str: str) -> str:
    """Extract quarter number from a date string like '2026-03-31'."""
    try:
        month = int(date_str.split("-")[1])
        return str((month - 1) // 3 + 1)
    except (IndexError, ValueError):
        return "?"


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class SEC13FConnector:
    """Orchestrates SEC EDGAR 13F filing ingestion."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = Filing13FFetcher()

    async def run(self, session: aiohttp.ClientSession):
        """Main polling loop."""
        log.info("13F filing loop started — poll every %ds", Config13F.POLL_INTERVAL)

        while True:
            try:
                filings = await self._fetcher.search_recent_filings(session)
                published = 0

                for filing in filings:
                    accession = filing.get("adsh", "")
                    ciks = filing.get("ciks", [])
                    cik = ciks[0] if ciks else ""

                    if not accession or not cik:
                        continue

                    # Try to fetch filing index and primary doc for richer metadata
                    cover_info = {}
                    index_data = await self._fetcher.fetch_filing_index(session, cik, accession)
                    if index_data:
                        # Look for XML primary document
                        for item in index_data.get("directory", {}).get("item", []):
                            name = item.get("name", "")
                            if name.endswith(".xml") and "primary" in name.lower():
                                xml_text = await self._fetcher.fetch_primary_doc(
                                    session, cik, accession, name,
                                )
                                if xml_text:
                                    cover_info = _extract_xml_cover_info(xml_text)
                                break
                        else:
                            # Fallback: try any XML file that isn't the info table
                            for item in index_data.get("directory", {}).get("item", []):
                                name = item.get("name", "")
                                if name.endswith(".xml") and "infotable" not in name.lower():
                                    xml_text = await self._fetcher.fetch_primary_doc(
                                        session, cik, accession, name,
                                    )
                                    if xml_text:
                                        cover_info = _extract_xml_cover_info(xml_text)
                                    break

                    doc = filing_to_document(filing, cover_info)
                    if doc:
                        self._publisher.publish(
                            Config13F.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d 13F filings", published)
                else:
                    log.info("No new 13F filings found this poll")

            except Exception as e:
                log.error("13F polling loop error: %s", e, exc_info=True)

            await asyncio.sleep(Config13F.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    user_agent = os.environ.get("SEC_USER_AGENT")
    if user_agent:
        Config13F.USER_AGENT = user_agent

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        Config13F.KAFKA_BOOTSTRAP = bootstrap

    lookback = os.environ.get("SEC_13F_LOOKBACK_DAYS")
    if lookback:
        try:
            Config13F.LOOKBACK_DAYS = int(lookback)
        except ValueError:
            pass

    apply_poll_multiplier(Config13F, "POLL_INTERVAL")


async def main():
    load_config_from_env()

    await wait_for_kafka(Config13F.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(Config13F.KAFKA_BOOTSTRAP, client_id="sec-13f-connector")
    connector = SEC13FConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("SEC 13F Institutional Holdings Connector running")
        log.info("  Search:     13F-HR filings, lookback %d days", Config13F.LOOKBACK_DAYS)
        log.info("  Poll:       every %ds", Config13F.POLL_INTERVAL)
        log.info("  Kafka:      %s", Config13F.KAFKA_TOPIC)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("SEC 13F Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
