"""
OSINT Pipeline — FRED (Federal Reserve Economic Data) Connector

Ingests macroeconomic time series from the FRED API:
  1. Key macro series: GDP, CPI, unemployment, fed funds rate, treasuries,
     yield curve, jobless claims, consumer sentiment, ISM, retail sales,
     housing starts, M2 money supply
  2. Release calendar for upcoming economic events

FRED API docs: https://fred.stlouisfed.org/docs/api/fred/
Free API key required via FRED_API_KEY environment variable.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
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

MACRO_SERIES = {
    # series_id: (human name, units hint)
    "GDP":       ("Gross Domestic Product", "Billions of Dollars"),
    "CPIAUCSL":  ("Consumer Price Index for All Urban Consumers", "Index 1982-1984=100"),
    "UNRATE":    ("Unemployment Rate", "Percent"),
    "FEDFUNDS":  ("Federal Funds Effective Rate", "Percent"),
    "DGS10":     ("10-Year Treasury Constant Maturity Rate", "Percent"),
    "DGS2":      ("2-Year Treasury Constant Maturity Rate", "Percent"),
    "T10Y2Y":    ("10-Year Treasury Minus 2-Year Treasury Yield Spread", "Percent"),
    "ICSA":      ("Initial Claims (Jobless Claims)", "Number"),
    "UMCSENT":   ("University of Michigan Consumer Sentiment", "Index 1966:Q1=100"),
    "MANEMP":    ("All Employees: Manufacturing", "Thousands of Persons"),
    "RSAFS":     ("Advance Retail Sales: Retail and Food Services", "Millions of Dollars"),
    "HOUST":     ("Housing Starts: Total New Privately Owned", "Thousands of Units"),
    "M2SL":      ("M2 Money Supply", "Billions of Dollars"),
}


class FredConfig:
    FRED_BASE = "https://api.stlouisfed.org/fred"
    FRED_API_KEY: Optional[str] = None

    SERIES_IDS: dict[str, tuple[str, str]] = dict(MACRO_SERIES)

    POLL_INTERVAL = 3600       # Macro data updates slowly — 1 hour
    REQUEST_DELAY = 1.0        # FRED allows 120 req/min; self-impose 1s gap
    OBSERVATION_LIMIT = 5      # Fetch last N observations per series

    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.macro.fred"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fred-connector")


# ---------------------------------------------------------------------------
# Document builders
# ---------------------------------------------------------------------------

def observation_to_document(
    series_id: str,
    series_name: str,
    units: str,
    frequency: str,
    observation: dict,
    prior_observation: Optional[dict],
) -> Optional[OsintDocument]:
    """Convert a single FRED observation into an OsintDocument."""
    obs_date = observation.get("date", "")
    raw_value = observation.get("value", "")

    # FRED returns "." for missing values
    if not obs_date or raw_value in (".", ""):
        return None

    try:
        value = float(raw_value)
    except (ValueError, TypeError):
        return None

    # Compute change from prior observation
    prior_value: Optional[float] = None
    change: Optional[float] = None
    pct_change: Optional[float] = None
    if prior_observation and prior_observation.get("value") not in (".", "", None):
        try:
            prior_value = float(prior_observation["value"])
            change = round(value - prior_value, 6)
            if prior_value != 0:
                pct_change = round((change / abs(prior_value)) * 100, 4)
        except (ValueError, TypeError):
            pass

    # Build human-readable content
    title = f"FRED: {series_name} ({series_id}) \u2014 {obs_date}: {value}"

    content_parts = [
        f"Series: {series_name} ({series_id})",
        f"Observation Date: {obs_date}",
        f"Value: {value} ({units})",
        f"Frequency: {frequency}",
    ]
    if prior_value is not None:
        content_parts.append(f"Prior Value: {prior_value}")
    if change is not None:
        content_parts.append(f"Change: {change:+.4f}")
    if pct_change is not None:
        content_parts.append(f"Percent Change: {pct_change:+.4f}%")
    content_parts.append(f"Source: Federal Reserve Economic Data (FRED)")

    content_text = "\n".join(content_parts)

    # Parse observation date as created_at
    try:
        created_at = datetime.strptime(obs_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        created_at = datetime.now(timezone.utc)

    # Dedup key: series_id + observation_date guarantees uniqueness
    source_id = f"fred:{series_id}:{obs_date}"

    doc = OsintDocument(
        source=SourcePlatform.FRED,
        source_id=source_id,
        content_type=ContentType.METRIC,
        title=title,
        content_text=content_text,
        url=f"https://fred.stlouisfed.org/series/{series_id}",
        created_at=created_at,
        entity_id=series_id,
        quality_signals=QualitySignals(
            score=None,
            engagement_count=None,
            is_verified=True,
        ),
        metadata={
            "series_id": series_id,
            "series_name": series_name,
            "value": value,
            "units": units,
            "frequency": frequency,
            "observation_date": obs_date,
            "prior_value": prior_value,
            "change": change,
            "pct_change": pct_change,
            "source_reliability": 0.95,
        },
    )
    doc.compute_dedup_hash()
    return doc


def release_event_to_document(release: dict) -> Optional[OsintDocument]:
    """Convert a FRED release calendar entry into an OsintDocument."""
    release_id = release.get("release_id", "")
    release_name = release.get("release_name", "")
    release_date = release.get("date", "")

    if not release_id or not release_name:
        return None

    title = f"FRED Release: {release_name} \u2014 {release_date}"
    content_text = (
        f"Upcoming Economic Release: {release_name}\n"
        f"Release ID: {release_id}\n"
        f"Scheduled Date: {release_date}\n"
        f"Source: Federal Reserve Economic Data (FRED)"
    )

    try:
        created_at = datetime.strptime(release_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        created_at = datetime.now(timezone.utc)

    source_id = f"fred:release:{release_id}:{release_date}"

    doc = OsintDocument(
        source=SourcePlatform.FRED,
        source_id=source_id,
        content_type=ContentType.ARTICLE,
        title=title,
        content_text=content_text,
        url=f"https://fred.stlouisfed.org/releases/{release_id}",
        created_at=created_at,
        entity_id=f"RELEASE:{release_id}",
        quality_signals=QualitySignals(
            score=None,
            engagement_count=None,
            is_verified=True,
        ),
        metadata={
            "release_id": str(release_id),
            "release_name": release_name,
            "release_date": release_date,
            "source_reliability": 0.95,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class FredFetcher:
    """Handles all HTTP requests to the FRED API."""

    def __init__(self):
        self._seen_ids: set[str] = set()

    async def fetch_series_observations(
        self,
        session: aiohttp.ClientSession,
        series_id: str,
    ) -> list[dict]:
        """Fetch recent observations for a FRED series."""
        if not FredConfig.FRED_API_KEY:
            return []

        params = {
            "series_id": series_id,
            "api_key": FredConfig.FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": FredConfig.OBSERVATION_LIMIT,
        }

        try:
            async with session.get(
                f"{FredConfig.FRED_BASE}/series/observations",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return data.get("observations", [])
                elif resp.status == 429:
                    log.warning("FRED rate limit hit for %s — backing off", series_id)
                    await asyncio.sleep(5)
                else:
                    log.warning("FRED observations for %s returned status %d", series_id, resp.status)
        except Exception as e:
            log.warning("FRED fetch failed for %s: %s", series_id, e)

        return []

    async def fetch_series_info(
        self,
        session: aiohttp.ClientSession,
        series_id: str,
    ) -> dict:
        """Fetch series metadata (frequency, units, etc.)."""
        if not FredConfig.FRED_API_KEY:
            return {}

        params = {
            "series_id": series_id,
            "api_key": FredConfig.FRED_API_KEY,
            "file_type": "json",
        }

        try:
            async with session.get(
                f"{FredConfig.FRED_BASE}/series",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    serieses = data.get("seriess", [])
                    if serieses:
                        return serieses[0]
        except Exception as e:
            log.warning("FRED series info failed for %s: %s", series_id, e)

        return {}

    async def fetch_release_dates(
        self,
        session: aiohttp.ClientSession,
    ) -> list[dict]:
        """Fetch upcoming release calendar entries."""
        if not FredConfig.FRED_API_KEY:
            return []

        params = {
            "api_key": FredConfig.FRED_API_KEY,
            "file_type": "json",
            "include_release_dates_with_no_data": "true",
            "limit": 30,
        }

        try:
            async with session.get(
                f"{FredConfig.FRED_BASE}/releases/dates",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return data.get("release_dates", [])
                else:
                    log.warning("FRED release dates returned status %d", resp.status)
        except Exception as e:
            log.warning("FRED release dates fetch failed: %s", e)

        return []

    def is_new(self, source_id: str) -> bool:
        """Check if we've already published this document."""
        if source_id in self._seen_ids:
            return False
        self._seen_ids.add(source_id)
        # Prevent unbounded memory growth
        if len(self._seen_ids) > 10000:
            self._seen_ids = set(list(self._seen_ids)[-5000:])
        return True


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class FredConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = FredFetcher()
        # Cache series metadata so we only fetch it once
        self._series_meta: dict[str, dict] = {}

    async def _ensure_series_meta(
        self, session: aiohttp.ClientSession, series_id: str
    ) -> dict:
        """Get cached series metadata, fetching from API on first access."""
        if series_id not in self._series_meta:
            info = await self._fetcher.fetch_series_info(session, series_id)
            self._series_meta[series_id] = info
            await asyncio.sleep(FredConfig.REQUEST_DELAY)
        return self._series_meta.get(series_id, {})

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "FRED connector started — polling %d series every %ds",
            len(FredConfig.SERIES_IDS),
            FredConfig.POLL_INTERVAL,
        )

        while True:
            try:
                published = await self._poll_cycle(session)
                if published:
                    self._publisher.flush(timeout=5.0)
                    stats = self._publisher.stats
                    log.info(
                        "Cycle complete: published %d docs (total delivered: %d, errors: %d)",
                        published, stats["delivered"], stats["errors"],
                    )
                else:
                    log.info("Cycle complete: no new observations")
            except Exception as e:
                log.error("FRED connector error: %s", e, exc_info=True)

            await asyncio.sleep(FredConfig.POLL_INTERVAL)

    async def _poll_cycle(self, session: aiohttp.ClientSession) -> int:
        """Run one full poll cycle: fetch all series + release calendar."""
        published = 0

        # --- Macro series observations ---
        for series_id, (series_name, units_hint) in FredConfig.SERIES_IDS.items():
            observations = await self._fetcher.fetch_series_observations(
                session, series_id
            )
            if not observations:
                await asyncio.sleep(FredConfig.REQUEST_DELAY)
                continue

            # Get real metadata from API (frequency, units)
            meta = await self._ensure_series_meta(session, series_id)
            frequency = meta.get("frequency", "Unknown")
            units = meta.get("units", units_hint)

            # Observations come newest-first; reverse for chronological pairing
            observations_chrono = list(reversed(observations))

            for i, obs in enumerate(observations_chrono):
                prior = observations_chrono[i - 1] if i > 0 else None
                doc = observation_to_document(
                    series_id=series_id,
                    series_name=series_name,
                    units=units,
                    frequency=frequency,
                    observation=obs,
                    prior_observation=prior,
                )
                if doc and self._fetcher.is_new(doc.source_id):
                    self._publisher.publish(
                        FredConfig.KAFKA_TOPIC,
                        doc.to_kafka_key(),
                        doc.to_kafka_value(),
                    )
                    published += 1

            await asyncio.sleep(FredConfig.REQUEST_DELAY)

        # --- Release calendar ---
        releases = await self._fetcher.fetch_release_dates(session)
        for release in releases:
            doc = release_event_to_document(release)
            if doc and self._fetcher.is_new(doc.source_id):
                self._publisher.publish(
                    FredConfig.KAFKA_TOPIC,
                    doc.to_kafka_key(),
                    doc.to_kafka_value(),
                )
                published += 1

        return published


# ---------------------------------------------------------------------------
# Kafka health check
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Env config loader
# ---------------------------------------------------------------------------

def load_config_from_env():
    key = os.environ.get("FRED_API_KEY")
    if key:
        FredConfig.FRED_API_KEY = key

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        FredConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("FRED_POLL_INTERVAL")
    if poll:
        FredConfig.POLL_INTERVAL = int(poll)

    limit = os.environ.get("FRED_OBSERVATION_LIMIT")
    if limit:
        FredConfig.OBSERVATION_LIMIT = int(limit)

    # Allow overriding series list via comma-separated env var
    series_env = os.environ.get("FRED_SERIES")
    if series_env:
        custom = {}
        for sid in series_env.split(","):
            sid = sid.strip()
            if sid and sid in MACRO_SERIES:
                custom[sid] = MACRO_SERIES[sid]
            elif sid:
                custom[sid] = (sid, "Unknown")
        if custom:
            FredConfig.SERIES_IDS = custom


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    load_config_from_env()

    if not FredConfig.FRED_API_KEY:
        log.error("FRED_API_KEY environment variable is required")
        log.error("Get a free key at: https://fred.stlouisfed.org/docs/api/api_key.html")
        return

    await wait_for_kafka(FredConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        FredConfig.KAFKA_BOOTSTRAP, client_id="fred-connector"
    )
    connector = FredConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=5)
    ) as session:
        log.info("=" * 60)
        log.info("FRED Macro Connector running")
        log.info("  Series:   %d tracked", len(FredConfig.SERIES_IDS))
        log.info("  IDs:      %s", ", ".join(FredConfig.SERIES_IDS.keys()))
        log.info("  Topic:    %s", FredConfig.KAFKA_TOPIC)
        log.info("  Interval: %ds", FredConfig.POLL_INTERVAL)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
