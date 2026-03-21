"""
OSINT Pipeline — Unusual Whales Connector

Ingests congressional trading data and options flow from Unusual Whales.
Uses the public data available without API key (recent trades, flow alerts).
Optional API key for premium endpoints.

Data source: unusualwhales.com
  - Congressional trades (House + Senate)
  - Options flow (unusual activity)
  - Dark pool activity

Rate limits: Self-imposed 5s between requests.
"""

from __future__ import annotations

import asyncio
import json
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

class UnusualWhalesConfig:
    """All tunables. Override via env vars."""

    # API (optional — works with public data scraping too)
    API_BASE = "https://api.unusualwhales.com"
    API_KEY: Optional[str] = None

    # Public data endpoints (web scraping fallback)
    WEB_BASE = "https://unusualwhales.com"

    # Polling
    POLL_INTERVAL = 600  # 10 minutes
    REQUEST_DELAY = 5.0

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC_CONGRESS = "finance.congress.trades"
    KAFKA_TOPIC_FLOW = "finance.options.flow"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("unusual-whales-connector")


# ---------------------------------------------------------------------------
# Data → OsintDocument
# ---------------------------------------------------------------------------

def congress_trade_to_document(trade: dict) -> Optional[OsintDocument]:
    """Convert an Unusual Whales congressional trade to OsintDocument."""
    politician = trade.get("politician", "")
    ticker = trade.get("ticker", trade.get("asset_ticker", ""))
    trade_type = trade.get("trade_type", trade.get("type", ""))
    amount = trade.get("amount", "")
    filed_date = trade.get("filed_date", trade.get("disclosure_date", ""))
    trade_date = trade.get("trade_date", trade.get("transaction_date", ""))
    chamber = trade.get("chamber", "")

    if not politician or not ticker:
        return None

    content_text = (
        f"Congressional trade: {politician} ({chamber}) {trade_type} {ticker} "
        f"amount: {amount} — filed {filed_date}"
    )

    try:
        created_at = datetime.strptime(trade_date or filed_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        created_at = datetime.now(timezone.utc)

    doc = OsintDocument(
        source=SourcePlatform.SEC_EDGAR,
        source_id=f"uw:congress:{politician}:{ticker}:{trade_date}",
        content_type=ContentType.FILING,
        title=f"{politician} {trade_type} {ticker}",
        content_text=content_text,
        created_at=created_at,
        entity_id=ticker.upper(),
        quality_signals=QualitySignals(),
        metadata={
            "data_source": "unusual_whales",
            "politician": politician,
            "chamber": chamber,
            "ticker": ticker,
            "trade_type": trade_type,
            "amount": amount,
            "filed_date": filed_date,
            "trade_date": trade_date,
        },
    )
    doc.compute_dedup_hash()
    return doc


def flow_to_document(flow: dict) -> Optional[OsintDocument]:
    """Convert an options flow alert to OsintDocument."""
    ticker = flow.get("ticker", flow.get("underlying_symbol", ""))
    sentiment = flow.get("sentiment", "")
    volume = flow.get("volume", 0)
    premium = flow.get("premium", flow.get("total_premium", 0))
    strike = flow.get("strike_price", "")
    expiry = flow.get("expiry", flow.get("expires_at", ""))
    option_type = flow.get("put_call", flow.get("option_type", ""))

    if not ticker:
        return None

    content_text = (
        f"Options flow: {ticker} {option_type} ${strike} exp {expiry} "
        f"vol={volume} premium=${premium} sentiment={sentiment}"
    )

    doc = OsintDocument(
        source=SourcePlatform.SEC_EDGAR,
        source_id=f"uw:flow:{ticker}:{option_type}:{strike}:{expiry}",
        content_type=ContentType.FILING,
        title=f"Options: {ticker} {option_type} ${strike}",
        content_text=content_text,
        created_at=datetime.now(timezone.utc),
        entity_id=ticker.upper(),
        quality_signals=QualitySignals(
            score=float(premium) if premium else None,
            engagement_count=volume,
        ),
        metadata={
            "data_source": "unusual_whales",
            "signal_type": "options_flow",
            "ticker": ticker,
            "option_type": option_type,
            "strike": strike,
            "expiry": expiry,
            "volume": volume,
            "premium": premium,
            "sentiment": sentiment,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class UnusualWhalesFetcher:
    """Fetches congressional trades and options flow."""

    def __init__(self):
        self._seen_ids: set[str] = set()

    async def fetch_congress_trades(
        self, session: aiohttp.ClientSession,
    ) -> list[dict]:
        """Fetch recent congressional trades."""
        headers = {"User-Agent": "OSINTPipeline/1.0"}
        if UnusualWhalesConfig.API_KEY:
            headers["Authorization"] = f"Bearer {UnusualWhalesConfig.API_KEY}"

        url = f"{UnusualWhalesConfig.API_BASE}/congress/recent-trades"

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 401:
                    log.warning("Unusual Whales API: unauthorized (key may be invalid)")
                    return []
                if resp.status != 200:
                    log.warning("Unusual Whales congress API returned %d", resp.status)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Unusual Whales congress fetch failed: %s", e)
            return []

        trades = data if isinstance(data, list) else data.get("data", [])
        new_trades = []
        for t in trades:
            tid = f"{t.get('politician', '')}:{t.get('ticker', '')}:{t.get('trade_date', '')}"
            if tid not in self._seen_ids:
                self._seen_ids.add(tid)
                new_trades.append(t)

        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2500:])

        return new_trades

    async def fetch_flow_alerts(
        self, session: aiohttp.ClientSession,
    ) -> list[dict]:
        """Fetch recent options flow alerts."""
        headers = {"User-Agent": "OSINTPipeline/1.0"}
        if UnusualWhalesConfig.API_KEY:
            headers["Authorization"] = f"Bearer {UnusualWhalesConfig.API_KEY}"

        url = f"{UnusualWhalesConfig.API_BASE}/flow/recent"

        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Unusual Whales flow fetch failed: %s", e)
            return []

        return data if isinstance(data, list) else data.get("data", [])


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class UnusualWhalesConnector:
    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = UnusualWhalesFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info("Unusual Whales connector started — poll every %ds", UnusualWhalesConfig.POLL_INTERVAL)

        while True:
            try:
                # Congress trades
                trades = await self._fetcher.fetch_congress_trades(session)
                published = 0
                for t in trades:
                    doc = congress_trade_to_document(t)
                    if doc:
                        self._publisher.publish(
                            UnusualWhalesConfig.KAFKA_TOPIC_CONGRESS,
                            doc.to_kafka_key(), doc.to_kafka_value(),
                        )
                        published += 1
                await asyncio.sleep(UnusualWhalesConfig.REQUEST_DELAY)

                # Options flow
                flows = await self._fetcher.fetch_flow_alerts(session)
                for f in flows:
                    doc = flow_to_document(f)
                    if doc:
                        self._publisher.publish(
                            UnusualWhalesConfig.KAFKA_TOPIC_FLOW,
                            doc.to_kafka_key(), doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d items (congress + flow)", published)

            except Exception as e:
                log.error("Unusual Whales error: %s", e, exc_info=True)

            await asyncio.sleep(UnusualWhalesConfig.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
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
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable after {timeout}s")


def load_config_from_env():
    import os
    key = os.environ.get("UNUSUAL_WHALES_API_KEY")
    if key:
        UnusualWhalesConfig.API_KEY = key
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        UnusualWhalesConfig.KAFKA_BOOTSTRAP = bootstrap


async def main():
    load_config_from_env()
    await wait_for_kafka(UnusualWhalesConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(UnusualWhalesConfig.KAFKA_BOOTSTRAP, client_id="unusual-whales-connector")
    connector = UnusualWhalesConnector(publisher)

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=5)) as session:
        log.info("=" * 60)
        log.info("Unusual Whales Connector running")
        log.info("  API key:  %s", "set" if UnusualWhalesConfig.API_KEY else "not set (limited)")
        log.info("  Topics:   %s, %s", UnusualWhalesConfig.KAFKA_TOPIC_CONGRESS, UnusualWhalesConfig.KAFKA_TOPIC_FLOW)
        log.info("=" * 60)
        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
