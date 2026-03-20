"""
OSINT Pipeline — SEC Insider Trading & Congressional Trading Connector

Two ingestion modes running concurrently:
  1. SEC EDGAR: Polls the EFTS full-text search API for recent Form 3/4/5
     filings, fetches and parses the ownership XML for each filing.
  2. CONGRESSIONAL TRADES: Polls the Finnhub API for congressional trading
     disclosures (pre-parsed House/Senate periodic transaction reports).

SEC EDGAR rate limits:
  - 10 requests/second max
  - User-Agent header required (company + contact email)
  - No API key needed

Finnhub rate limits:
  - 60 calls/minute (free tier)
  - API key required (free registration at finnhub.io)

Key transaction codes (Form 4):
  P = open market purchase (strong bullish signal)
  S = open market sale (bearish but noisy)
  M = option exercise
  A = grant/award (compensation, not discretionary)
  F = tax withholding sale (automatic)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
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

class SECConfig:
    """All tunables in one place. Override via env vars in production."""

    # EDGAR endpoints
    EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

    # EDGAR rate limiting (10 req/s max, we use 5 req/s for safety)
    EDGAR_REQUEST_DELAY = 0.2  # 200ms between requests
    USER_AGENT = "AssetBoss mas-admin@assetboss.app"

    # Finnhub (congressional trades)
    FINNHUB_BASE = "https://finnhub.io/api/v1"
    FINNHUB_API_KEY: Optional[str] = None  # Set via FINNHUB_API_KEY env var

    # Polling intervals
    EDGAR_POLL_INTERVAL = 300      # 5 minutes
    CONGRESS_POLL_INTERVAL = 600   # 10 minutes

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC_SEC = "finance.sec.insider"
    KAFKA_TOPIC_CONGRESS = "finance.congress.trades"

    # Search parameters
    FILING_FORMS = ["3", "4", "5"]
    LOOKBACK_DAYS = 1              # How far back to search for new filings
    MAX_FILINGS_PER_POLL = 200     # Safety cap

    # Watchlist: tickers to monitor for congressional trades
    CONGRESS_WATCHLIST: list[str] = []  # Empty = monitor all via recent endpoint


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sec-insider-connector")


# ---------------------------------------------------------------------------
# EDGAR Rate Limiter
# ---------------------------------------------------------------------------

class EdgarRateLimiter:
    """Enforces SEC EDGAR rate limit of 10 req/s (we target 5 req/s)."""

    def __init__(self, delay: float = SECConfig.EDGAR_REQUEST_DELAY):
        self._delay = delay
        self._last_request = 0.0

    async def wait(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._delay:
            await asyncio.sleep(self._delay - elapsed)
        self._last_request = time.time()


# ---------------------------------------------------------------------------
# SEC EDGAR: Filing Discovery + XML Parsing
# ---------------------------------------------------------------------------

class EdgarFilingFetcher:
    """Discovers and parses SEC insider trading filings (Forms 3/4/5)."""

    def __init__(self):
        self._rate_limiter = EdgarRateLimiter()
        self._seen_accessions: set[str] = set()
        self._headers = {"User-Agent": SECConfig.USER_AGENT}

    async def search_recent_filings(self, session: aiohttp.ClientSession) -> list[dict]:
        """Search EFTS for recent insider trading filings."""
        await self._rate_limiter.wait()

        start_date = (
            datetime.now(timezone.utc) - timedelta(days=SECConfig.LOOKBACK_DAYS)
        ).strftime("%Y-%m-%d")
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        params = {
            "q": "",
            "forms": ",".join(SECConfig.FILING_FORMS),
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
        }

        try:
            async with session.get(
                "https://efts.sec.gov/LATEST/search-index",
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

        for hit in hits[:SECConfig.MAX_FILINGS_PER_POLL]:
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

    async def fetch_filing_xml(
        self,
        session: aiohttp.ClientSession,
        cik: str,
        accession: str,
    ) -> Optional[str]:
        """Fetch the ownership XML document for a filing."""
        await self._rate_limiter.wait()

        # Accession format: 0001234567-26-012345 → remove dashes for URL path
        accession_path = accession.replace("-", "")
        # Primary document is typically the accession number with .xml
        url = f"{SECConfig.ARCHIVES_BASE}/{cik}/{accession_path}"

        try:
            # First fetch the filing index to find the XML document
            async with session.get(
                f"{url}/index.json",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    log.warning("Filing index returned %d for %s", resp.status, accession)
                    return None
                index_data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Failed to fetch filing index for %s: %s", accession, e)
            return None

        # Find the XML ownership document in the index
        xml_filename = None
        for item in index_data.get("directory", {}).get("item", []):
            name = item.get("name", "")
            if name.endswith(".xml") and name != "primary_doc.xml":
                xml_filename = name
                break

        if not xml_filename:
            # Fallback: try primary_doc.xml
            for item in index_data.get("directory", {}).get("item", []):
                if item.get("name", "").endswith(".xml"):
                    xml_filename = item["name"]
                    break

        if not xml_filename:
            return None

        await self._rate_limiter.wait()

        try:
            async with session.get(
                f"{url}/{xml_filename}",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Failed to fetch XML for %s: %s", accession, e)
            return None


def parse_ownership_xml(xml_text: str, accession: str) -> list[OsintDocument]:
    """Parse an SEC ownership XML document into OsintDocuments.

    Each transaction within the filing becomes a separate document.
    """
    documents = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("XML parse error for %s: %s", accession, e)
        return []

    # Extract issuer info
    issuer = root.find("issuer")
    if issuer is None:
        return []

    issuer_cik = _xml_text(issuer, "issuerCik")
    issuer_name = _xml_text(issuer, "issuerName")
    ticker = _xml_text(issuer, "issuerTradingSymbol")

    # Extract reporting owner info
    owner_elem = root.find("reportingOwner")
    if owner_elem is None:
        return []

    owner_id = owner_elem.find("reportingOwnerId")
    owner_name = _xml_text(owner_id, "rptOwnerName") if owner_id is not None else ""

    relationship = owner_elem.find("reportingOwnerRelationship")
    is_officer = _xml_text(relationship, "isOfficer") == "true" if relationship is not None else False
    is_director = _xml_text(relationship, "isDirector") == "true" if relationship is not None else False
    officer_title = _xml_text(relationship, "officerTitle") if relationship is not None else ""
    is_ten_pct = _xml_text(relationship, "isTenPercentOwner") == "true" if relationship is not None else False

    doc_type = _xml_text(root, "documentType") or "4"
    period_of_report = _xml_text(root, "periodOfReport")

    # Parse non-derivative transactions
    nd_table = root.find("nonDerivativeTable")
    if nd_table is not None:
        for txn in nd_table.findall("nonDerivativeTransaction"):
            doc = _parse_transaction(
                txn, accession, doc_type, ticker, issuer_name, issuer_cik,
                owner_name, is_officer, is_director, officer_title, is_ten_pct,
                period_of_report, is_derivative=False,
            )
            if doc:
                documents.append(doc)

    # Parse derivative transactions
    d_table = root.find("derivativeTable")
    if d_table is not None:
        for txn in d_table.findall("derivativeTransaction"):
            doc = _parse_transaction(
                txn, accession, doc_type, ticker, issuer_name, issuer_cik,
                owner_name, is_officer, is_director, officer_title, is_ten_pct,
                period_of_report, is_derivative=True,
            )
            if doc:
                documents.append(doc)

    return documents


def _parse_transaction(
    txn, accession, doc_type, ticker, issuer_name, issuer_cik,
    owner_name, is_officer, is_director, officer_title, is_ten_pct,
    period_of_report, is_derivative,
) -> Optional[OsintDocument]:
    """Parse a single transaction element into an OsintDocument."""
    security_title = _xml_value(txn, "securityTitle")
    txn_date = _xml_value(txn, "transactionDate")

    coding = txn.find("transactionCoding")
    txn_code = _xml_text(coding, "transactionCode") if coding is not None else ""

    amounts = txn.find("transactionAmounts")
    shares = _xml_value(amounts, "transactionShares") if amounts is not None else ""
    price = _xml_value(amounts, "transactionPricePerShare") if amounts is not None else ""
    acq_disp = _xml_value(amounts, "transactionAcquiredDisposedCode") if amounts is not None else ""

    post = txn.find("postTransactionAmounts")
    shares_after = _xml_value(post, "sharesOwnedFollowingTransaction") if post is not None else ""

    # Build human-readable content
    action = "acquired" if acq_disp == "A" else "disposed" if acq_disp == "D" else "transacted"
    deriv_label = " (derivative)" if is_derivative else ""
    content_text = (
        f"{owner_name} {action} {shares} shares of {ticker} ({issuer_name}) "
        f"at ${price}/share — {security_title}{deriv_label} "
        f"[Form {doc_type}, code {txn_code}]"
    )

    # Parse transaction date
    if txn_date:
        try:
            created_at = datetime.strptime(txn_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Parse numeric values for quality signals
    try:
        shares_float = float(shares) if shares else 0.0
    except ValueError:
        shares_float = 0.0
    try:
        price_float = float(price) if price else 0.0
    except ValueError:
        price_float = 0.0

    total_value = shares_float * price_float

    doc = OsintDocument(
        source=SourcePlatform.SEC_EDGAR,
        source_id=f"{accession}:{txn_code}:{txn_date}",
        content_type=ContentType.FILING,
        title=f"Form {doc_type}: {owner_name} — {ticker}",
        content_text=content_text,
        url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={issuer_cik}&type=4&dateb=&owner=only&count=40",
        created_at=created_at,
        entity_id=ticker.upper() if ticker else issuer_cik,
        quality_signals=QualitySignals(
            score=total_value,  # Dollar value as score
            engagement_count=int(shares_float) if shares_float else None,
        ),
        metadata={
            "form_type": doc_type,
            "accession": accession,
            "ticker": ticker,
            "issuer_name": issuer_name,
            "issuer_cik": issuer_cik,
            "owner_name": owner_name,
            "is_officer": is_officer,
            "is_director": is_director,
            "officer_title": officer_title,
            "is_ten_percent_owner": is_ten_pct,
            "transaction_code": txn_code,
            "shares": shares,
            "price_per_share": price,
            "acquired_or_disposed": acq_disp,
            "shares_after_transaction": shares_after,
            "security_title": security_title,
            "is_derivative": is_derivative,
            "period_of_report": period_of_report,
            "total_value": total_value,
        },
    )
    doc.compute_dedup_hash()
    return doc


def _xml_text(parent, tag: str) -> str:
    """Get text content of a direct child element."""
    if parent is None:
        return ""
    elem = parent.find(tag)
    return (elem.text or "").strip() if elem is not None else ""


def _xml_value(parent, tag: str) -> str:
    """Get text content of a child element's <value> sub-element (EDGAR pattern)."""
    if parent is None:
        return ""
    elem = parent.find(tag)
    if elem is None:
        return ""
    val = elem.find("value")
    return (val.text or "").strip() if val is not None else (elem.text or "").strip()


# ---------------------------------------------------------------------------
# Finnhub Congressional Trading
# ---------------------------------------------------------------------------

class CongressionalTrader:
    """Fetches congressional trading data from Finnhub API."""

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._seen_ids: set[str] = set()

    async def fetch_recent_trades(
        self, session: aiohttp.ClientSession, symbol: Optional[str] = None,
    ) -> list[dict]:
        """Fetch congressional trades, optionally filtered by symbol."""
        params = {"token": self._api_key}
        if symbol:
            params["symbol"] = symbol
            url = f"{SECConfig.FINNHUB_BASE}/stock/congressional-trading"
        else:
            url = f"{SECConfig.FINNHUB_BASE}/stock/congressional-trading"
            # Without a symbol, we need to query per-symbol from watchlist
            return []

        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    log.warning("Finnhub rate limited — backing off")
                    await asyncio.sleep(60)
                    return []
                if resp.status != 200:
                    log.warning("Finnhub returned %d", resp.status)
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("Finnhub request failed: %s", e)
            return []

        trades = data.get("data", [])
        new_trades = []
        for trade in trades:
            # Create a unique ID from the trade details
            trade_id = f"{trade.get('name', '')}:{trade.get('symbol', '')}:{trade.get('transactionDate', '')}"
            if trade_id not in self._seen_ids:
                self._seen_ids.add(trade_id)
                new_trades.append(trade)

        if len(self._seen_ids) > 10000:
            self._seen_ids = set(list(self._seen_ids)[-5000:])

        return new_trades


def congressional_trade_to_document(trade: dict) -> Optional[OsintDocument]:
    """Convert a Finnhub congressional trade to OsintDocument."""
    name = trade.get("name", "")
    symbol = trade.get("symbol", "")
    txn_type = trade.get("transactionType", "")
    txn_date = trade.get("transactionDate", "")
    amount_from = trade.get("amountFrom")
    amount_to = trade.get("amountTo")
    asset_name = trade.get("assetName", "")
    owner_type = trade.get("ownerType", "")
    filing_date = trade.get("filingDate", "")
    position = trade.get("position", "")

    if not name or not symbol:
        return None

    # Build content text
    amount_str = ""
    if amount_from and amount_to:
        amount_str = f"${amount_from:,.0f}-${amount_to:,.0f}"
    elif amount_from:
        amount_str = f"${amount_from:,.0f}+"

    content_text = (
        f"Congressional trade: {name} ({position}) {txn_type} {symbol} "
        f"({asset_name}) {amount_str} — filed {filing_date}"
    )

    if txn_date:
        try:
            created_at = datetime.strptime(txn_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            created_at = datetime.now(timezone.utc)
    else:
        created_at = datetime.now(timezone.utc)

    # Use midpoint of amount range as score
    score = None
    if amount_from and amount_to:
        score = (amount_from + amount_to) / 2.0
    elif amount_from:
        score = float(amount_from)

    doc = OsintDocument(
        source=SourcePlatform.SEC_EDGAR,
        source_id=f"congress:{name}:{symbol}:{txn_date}",
        content_type=ContentType.FILING,
        title=f"Congressional: {name} {txn_type} {symbol}",
        content_text=content_text,
        created_at=created_at,
        entity_id=symbol.upper(),
        quality_signals=QualitySignals(
            score=score,
        ),
        metadata={
            "data_source": "finnhub_congressional",
            "politician_name": name,
            "position": position,
            "ticker": symbol,
            "transaction_type": txn_type,
            "transaction_date": txn_date,
            "filing_date": filing_date,
            "amount_from": amount_from,
            "amount_to": amount_to,
            "asset_name": asset_name,
            "owner_type": owner_type,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Connector Orchestrator
# ---------------------------------------------------------------------------

class SECInsiderConnector:
    """Orchestrates SEC EDGAR + congressional trading ingestion."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._edgar = EdgarFilingFetcher()
        self._congress = (
            CongressionalTrader(SECConfig.FINNHUB_API_KEY)
            if SECConfig.FINNHUB_API_KEY
            else None
        )

    async def run(self, session: aiohttp.ClientSession):
        tasks = [asyncio.create_task(self._edgar_loop(session))]

        if self._congress and SECConfig.CONGRESS_WATCHLIST:
            tasks.append(asyncio.create_task(self._congress_loop(session)))
        elif not self._congress:
            log.info("Finnhub API key not set — congressional trading disabled")
        elif not SECConfig.CONGRESS_WATCHLIST:
            log.info("No tickers in CONGRESS_WATCHLIST — congressional trading disabled")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _edgar_loop(self, session: aiohttp.ClientSession):
        log.info("EDGAR filing loop started — poll every %ds", SECConfig.EDGAR_POLL_INTERVAL)

        while True:
            try:
                filings = await self._edgar.search_recent_filings(session)
                published = 0

                for filing in filings:
                    accession = filing.get("adsh", "")
                    ciks = filing.get("ciks", [])
                    cik = ciks[0] if ciks else ""

                    if not accession or not cik:
                        continue

                    xml_text = await self._edgar.fetch_filing_xml(session, cik, accession)
                    if not xml_text:
                        continue

                    docs = parse_ownership_xml(xml_text, accession)
                    for doc in docs:
                        self._publisher.publish(
                            SECConfig.KAFKA_TOPIC_SEC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("EDGAR loop published %d transactions", published)

            except Exception as e:
                log.error("EDGAR loop error: %s", e, exc_info=True)

            await asyncio.sleep(SECConfig.EDGAR_POLL_INTERVAL)

    async def _congress_loop(self, session: aiohttp.ClientSession):
        log.info(
            "Congressional trading loop started — %d symbols, poll every %ds",
            len(SECConfig.CONGRESS_WATCHLIST), SECConfig.CONGRESS_POLL_INTERVAL,
        )

        while True:
            try:
                published = 0
                for symbol in SECConfig.CONGRESS_WATCHLIST:
                    trades = await self._congress.fetch_recent_trades(session, symbol)
                    for trade in trades:
                        doc = congressional_trade_to_document(trade)
                        if doc:
                            self._publisher.publish(
                                SECConfig.KAFKA_TOPIC_CONGRESS,
                                doc.to_kafka_key(),
                                doc.to_kafka_value(),
                            )
                            published += 1
                    # Respect Finnhub rate limit (60/min)
                    await asyncio.sleep(1.1)

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Congressional loop published %d trades", published)

            except Exception as e:
                log.error("Congressional loop error: %s", e, exc_info=True)

            await asyncio.sleep(SECConfig.CONGRESS_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
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
        await asyncio.sleep(2)
    raise RuntimeError(f"Kafka not reachable at {bootstrap_servers} after {timeout}s")


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    api_key = os.environ.get("FINNHUB_API_KEY")
    if api_key:
        SECConfig.FINNHUB_API_KEY = api_key

    watchlist = os.environ.get("CONGRESS_WATCHLIST", "")
    if watchlist:
        SECConfig.CONGRESS_WATCHLIST = [s.strip() for s in watchlist.split(",") if s.strip()]

    user_agent = os.environ.get("SEC_USER_AGENT")
    if user_agent:
        SECConfig.USER_AGENT = user_agent

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        SECConfig.KAFKA_BOOTSTRAP = bootstrap


async def main():
    load_config_from_env()

    await wait_for_kafka(SECConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(SECConfig.KAFKA_BOOTSTRAP, client_id="sec-insider-connector")
    connector = SECInsiderConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=10),
    ) as session:
        log.info("=" * 60)
        log.info("SEC Insider Trading Connector running")
        log.info("  EDGAR:      Forms %s, poll every %ds", SECConfig.FILING_FORMS, SECConfig.EDGAR_POLL_INTERVAL)
        log.info("  Congress:   %s", "enabled" if SECConfig.FINNHUB_API_KEY else "disabled (no API key)")
        log.info("  Watchlist:  %s", SECConfig.CONGRESS_WATCHLIST or "none")
        log.info("  Kafka:      %s, %s", SECConfig.KAFKA_TOPIC_SEC, SECConfig.KAFKA_TOPIC_CONGRESS)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("SEC Insider Trading Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
