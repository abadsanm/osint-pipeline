"""
OSINT Pipeline — Census/BLS Economic Data Connector

Ingests economic indicator time series from the Bureau of Labor Statistics
(BLS) public API v2. Tracks CPI-U, Total Nonfarm Employment, and
Unemployment Rate. No API key needed (25 requests/day limit).
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


SERIES_DESCRIPTIONS = {
    "CUUR0000SA0": "CPI-U (Consumer Price Index for All Urban Consumers)",
    "CES0000000001": "Total Nonfarm Employment (thousands)",
    "LNS14000000": "Unemployment Rate (%)",
}


class EconomicDataConfig:
    BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
    SERIES_IDS = ["CUUR0000SA0", "CES0000000001", "LNS14000000"]
    BLS_API_KEY: Optional[str] = None  # Optional; without key: 25 requests/day
    POLL_INTERVAL = 86400  # daily
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "gov.economic.indicators"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("economic-data-connector")


def _period_to_month(period: str) -> int:
    """Convert BLS period code (M01-M13) to month number."""
    if period.startswith("M"):
        try:
            return int(period[1:])
        except ValueError:
            return 1
    return 1


def datapoint_to_document(series_id: str, point: dict) -> Optional[OsintDocument]:
    """Convert a BLS data point into an OsintDocument."""
    year = point.get("year", "")
    period = point.get("period", "")
    value = point.get("value", "")
    footnotes = point.get("footnotes", [])

    if not year or not value:
        return None

    # Skip annual averages (M13)
    if period == "M13":
        return None

    series_desc = SERIES_DESCRIPTIONS.get(series_id, series_id)
    month = _period_to_month(period)

    try:
        val_float = float(value)
    except (ValueError, TypeError):
        return None

    # Build date from year + period
    try:
        created_at = datetime(int(year), month, 1, tzinfo=timezone.utc)
    except (ValueError, TypeError):
        created_at = datetime.now(timezone.utc)

    # Build content
    footnote_text = ""
    if footnotes:
        fn_parts = [fn.get("text", "") for fn in footnotes if fn.get("text")]
        if fn_parts:
            footnote_text = f"\nFootnotes: {'; '.join(fn_parts)}"

    content_text = (
        f"{series_desc}: {value}\n"
        f"Series: {series_id}\n"
        f"Period: {year}-{period}{footnote_text}"
    )

    title = f"{series_desc} — {year} {period}: {value}"

    dedup_key = f"{series_id}:{year}:{period}"
    doc_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"bls:{doc_id}",
        content_type=ContentType.METRIC,
        title=title,
        content_text=content_text,
        url=f"https://data.bls.gov/timeseries/{series_id}",
        created_at=created_at,
        entity_id=series_id,
        quality_signals=QualitySignals(score=val_float),
        metadata={
            "news_source": "BLS",
            "series_id": series_id,
            "series_description": series_desc,
            "year": year,
            "period": period,
            "value": value,
            "value_float": val_float,
            "footnotes": [fn.get("text", "") for fn in footnotes if fn.get("text")],
            "data_source": "bls_api",
        },
    )
    doc.compute_dedup_hash()
    return doc


class EconomicDataFetcher:
    def __init__(self):
        self._seen_datapoints: set[str] = set()

    async def fetch_series(self, session: aiohttp.ClientSession) -> list[tuple[str, dict]]:
        """Fetch time series data from BLS API v2."""
        current_year = datetime.now(timezone.utc).year

        payload: dict = {
            "seriesid": EconomicDataConfig.SERIES_IDS,
            "startyear": str(current_year - 1),
            "endyear": str(current_year),
        }

        if EconomicDataConfig.BLS_API_KEY:
            payload["registrationkey"] = EconomicDataConfig.BLS_API_KEY

        headers = {
            "User-Agent": "OSINTPipeline/1.0",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(
                EconomicDataConfig.BLS_API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning("BLS API returned status %d", resp.status)
                    return []
                data = await resp.json(content_type=None)
        except Exception as e:
            log.warning("BLS API fetch failed: %s", e)
            return []

        status = data.get("status", "")
        if status != "REQUEST_SUCCEEDED":
            message = data.get("message", [])
            log.warning("BLS API status: %s — %s", status, message)
            if status == "REQUEST_FAILED":
                return []

        results: list[tuple[str, dict]] = []
        for series in data.get("Results", {}).get("series", []):
            series_id = series.get("seriesID", "")
            for point in series.get("data", []):
                key = f"{series_id}:{point.get('year')}:{point.get('period')}"
                if key not in self._seen_datapoints:
                    self._seen_datapoints.add(key)
                    results.append((series_id, point))

        if len(self._seen_datapoints) > 5000:
            self._seen_datapoints = set(list(self._seen_datapoints)[-2500:])

        log.info("Fetched %d data points from BLS", len(results))
        return results


class EconomicDataConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = EconomicDataFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Economic Data connector started — poll every %ds",
                 EconomicDataConfig.POLL_INTERVAL)

        while True:
            try:
                published = 0

                datapoints = await self._fetcher.fetch_series(session)
                for series_id, point in datapoints:
                    doc = datapoint_to_document(series_id, point)
                    if doc:
                        self._publisher.publish(
                            EconomicDataConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d economic data points", published)

            except Exception as e:
                log.error("Economic data error: %s", e, exc_info=True)

            await asyncio.sleep(EconomicDataConfig.POLL_INTERVAL)


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
        EconomicDataConfig.KAFKA_BOOTSTRAP = bootstrap

    api_key = os.environ.get("BLS_API_KEY")
    if api_key:
        EconomicDataConfig.BLS_API_KEY = api_key

    poll = os.environ.get("ECONOMIC_DATA_POLL_INTERVAL")
    if poll:
        EconomicDataConfig.POLL_INTERVAL = int(poll)

    series = os.environ.get("BLS_SERIES_IDS")
    if series:
        EconomicDataConfig.SERIES_IDS = [s.strip() for s in series.split(",") if s.strip()]


async def main():
    load_config_from_env()
    await wait_for_kafka(EconomicDataConfig.KAFKA_BOOTSTRAP)

    series_list = ", ".join(EconomicDataConfig.SERIES_IDS)
    publisher = KafkaPublisher(EconomicDataConfig.KAFKA_BOOTSTRAP,
                               client_id="economic-data-connector")
    connector = EconomicDataConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=10)) as session:
        log.info("=" * 60)
        log.info("Economic Data (BLS) Connector running")
        log.info("  Topic:  %s", EconomicDataConfig.KAFKA_TOPIC)
        log.info("  Series: %s", series_list)
        log.info("  Poll:   %ds", EconomicDataConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
