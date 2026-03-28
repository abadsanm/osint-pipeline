"""
OSINT Pipeline — OpenInsider Connector

Ingests insider trading data from openinsider.com by scraping HTML tables:
  1. Latest insider buying  (/latest-insider-buying)
  2. Latest insider selling (/latest-insider-selling)
  3. Latest cluster buys    (/latest-cluster-buys)
  4. Main screener          (/screener)

OpenInsider aggregates SEC Form 4 filings into an easily parseable format.
Cluster buys (multiple insiders buying the same stock) are especially high-
signal events.

Rate limiting:
  - Self-imposed 5s delay between requests (respectful to free service)
  - Poll interval: 900s (15 minutes)
  - Proper User-Agent header

Kafka topic: finance.insider.trades (partitions 3)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
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

class OpenInsiderConfig:
    """All tunables in one place. Override via env vars in production."""

    BASE_URL = "https://openinsider.com"

    # Pages to scrape (in order of signal value)
    PAGES = {
        "cluster_buys": "/latest-cluster-buys",
        "insider_buying": "/latest-insider-buying",
        "insider_selling": "/latest-insider-selling",
        "screener": "/screener?s=&o=&pl=&ph=&ll=&lh=&fd=7&fdr=&td=0&tdr=&feession=&cik=&ic=&sid=&ptype=&cnt=100&page=1",
    }

    # Request settings
    REQUEST_DELAY = 5.0          # seconds between requests (respectful scraping)
    REQUEST_TIMEOUT = 30         # seconds
    USER_AGENT = "Mozilla/5.0 (compatible; OSINTPipeline/1.0; +https://github.com/osint-pipeline)"

    # Polling
    POLL_INTERVAL = 900          # 15 minutes

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.insider.trades"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("openinsider-connector")


# ---------------------------------------------------------------------------
# HTML Table Columns (openinsider.com table structure)
# ---------------------------------------------------------------------------
# The main tables on openinsider.com have these columns (0-indexed):
#   0: X (link)
#   1: Filing Date
#   2: Trade Date
#   3: Ticker
#   4: Company Name
#   5: Insider Name
#   6: Title
#   7: Trade Type
#   8: Price
#   9: Qty
#  10: Owned
#  11: ΔOwn
#  12: Value
#
# Cluster buys page has a slightly different layout — we detect and adapt.
# ---------------------------------------------------------------------------

# Column indices for the standard insider trading table
COLS_STANDARD = {
    "filing_date": 1,
    "trade_date": 2,
    "ticker": 3,
    "company": 4,
    "insider_name": 5,
    "title": 6,
    "trade_type": 7,
    "price": 8,
    "qty": 9,
    "owned": 10,
    "delta_own": 11,
    "value": 12,
}


def _clean_text(text: str) -> str:
    """Strip whitespace and collapse internal spaces."""
    return re.sub(r"\s+", " ", text.strip())


def _parse_number(text: str) -> Optional[float]:
    """Parse a number string like '$1,234,567' or '+12,345' or '-5%' into a float."""
    cleaned = text.replace("$", "").replace(",", "").replace("+", "").replace("%", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(text: str) -> Optional[datetime]:
    """Parse date string from openinsider (typically YYYY-MM-DD or MM/DD/YYYY)."""
    text = text.strip()
    if not text:
        return None

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _detect_trade_type(trade_type_text: str) -> str:
    """Classify trade type as 'buy' or 'sell' from the trade type column."""
    lower = trade_type_text.lower()
    if "purchase" in lower or "buy" in lower or lower.startswith("p"):
        return "buy"
    if "sale" in lower or "sell" in lower or lower.startswith("s"):
        return "sell"
    return lower or "unknown"


# ---------------------------------------------------------------------------
# HTML Parser
# ---------------------------------------------------------------------------

class OpenInsiderParser:
    """Parses openinsider.com HTML tables into structured trade records."""

    @staticmethod
    def parse_trades_table(html: str, page_key: str) -> list[dict]:
        """Extract trades from an openinsider.com page.

        Returns a list of dicts with keys matching COLS_STANDARD.
        """
        soup = BeautifulSoup(html, "html.parser")
        trades = []

        # Find the insider trading table — it's a <table> with class "tinytable"
        table = soup.find("table", class_="tinytable")
        if not table:
            # Fallback: find the largest table on the page
            tables = soup.find_all("table")
            if not tables:
                log.warning("No tables found on page: %s", page_key)
                return []
            table = max(tables, key=lambda t: len(t.find_all("tr")))

        rows = table.find_all("tr")
        if len(rows) < 2:
            return []

        # Detect column mapping from header row
        header_row = rows[0]
        headers = [_clean_text(th.get_text()) for th in header_row.find_all(["th", "td"])]
        col_map = _build_column_map(headers)

        if not col_map:
            # Fall back to positional mapping
            col_map = COLS_STANDARD

        is_cluster = page_key == "cluster_buys"

        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 10:
                continue

            try:
                trade = _extract_trade_from_row(cells, col_map, is_cluster)
                if trade and trade.get("ticker"):
                    trades.append(trade)
            except Exception as e:
                log.debug("Failed to parse row: %s", e)
                continue

        return trades


def _build_column_map(headers: list[str]) -> Optional[dict]:
    """Build column index map from header text. Returns None if headers
    don't match expected patterns."""
    if not headers or len(headers) < 8:
        return None

    col_map = {}
    for idx, header in enumerate(headers):
        h = header.lower()
        if "filing" in h and "date" in h:
            col_map["filing_date"] = idx
        elif "trade" in h and "date" in h:
            col_map["trade_date"] = idx
        elif h in ("ticker", "symbol"):
            col_map["ticker"] = idx
        elif "company" in h or "issuer" in h:
            col_map["company"] = idx
        elif "insider" in h and "name" in h:
            col_map["insider_name"] = idx
        elif h == "title" or (h == "insider title"):
            col_map["title"] = idx
        elif "trade type" in h or "type" == h:
            col_map["trade_type"] = idx
        elif h == "price":
            col_map["price"] = idx
        elif h in ("qty", "quantity", "shares"):
            col_map["qty"] = idx
        elif h in ("owned", "shares owned"):
            col_map["owned"] = idx
        elif "own" in h and ("%" in h or "delta" in h or "chg" in h):
            col_map["delta_own"] = idx
        elif h == "value" or "value" in h:
            col_map["value"] = idx

    # Need at least ticker and some trade info
    if "ticker" not in col_map:
        return None

    return col_map


def _extract_trade_from_row(
    cells: list, col_map: dict, is_cluster: bool
) -> Optional[dict]:
    """Extract a trade dict from a table row."""

    def cell_text(key: str) -> str:
        idx = col_map.get(key)
        if idx is None or idx >= len(cells):
            return ""
        return _clean_text(cells[idx].get_text())

    def cell_link(key: str) -> str:
        idx = col_map.get(key)
        if idx is None or idx >= len(cells):
            return ""
        a = cells[idx].find("a")
        if a and a.get("href"):
            return a["href"]
        return ""

    ticker = cell_text("ticker").upper().strip()
    if not ticker or len(ticker) > 10:
        return None

    filing_date_str = cell_text("filing_date")
    trade_date_str = cell_text("trade_date")
    company = cell_text("company")
    insider_name = cell_text("insider_name")
    insider_title = cell_text("title")
    trade_type_raw = cell_text("trade_type")
    price_str = cell_text("price")
    qty_str = cell_text("qty")
    owned_str = cell_text("owned")
    delta_own_str = cell_text("delta_own")
    value_str = cell_text("value")

    trade_type = _detect_trade_type(trade_type_raw)
    price = _parse_number(price_str)
    quantity = _parse_number(qty_str)
    shares_owned = _parse_number(owned_str)
    delta_own_pct = _parse_number(delta_own_str)
    value = _parse_number(value_str)

    filing_date = _parse_date(filing_date_str)
    trade_date = _parse_date(trade_date_str)

    # Get the SEC filing link if available
    filing_link = cell_link("filing_date") or cell_link("ticker")

    return {
        "ticker": ticker,
        "company": company,
        "insider_name": insider_name,
        "insider_title": insider_title,
        "trade_type": trade_type,
        "trade_type_raw": trade_type_raw,
        "price": price,
        "quantity": quantity,
        "shares_owned_after": shares_owned,
        "delta_own_pct": delta_own_pct,
        "value": value,
        "filing_date": filing_date,
        "trade_date": trade_date,
        "filing_date_str": filing_date_str,
        "trade_date_str": trade_date_str,
        "filing_link": filing_link,
        "is_cluster_buy": is_cluster,
    }


# ---------------------------------------------------------------------------
# Trade → OsintDocument
# ---------------------------------------------------------------------------

def trade_to_document(trade: dict) -> Optional[OsintDocument]:
    """Convert a parsed trade dict into an OsintDocument."""
    ticker = trade.get("ticker", "")
    insider_name = trade.get("insider_name", "Unknown")
    insider_title = trade.get("insider_title", "")
    trade_type = trade.get("trade_type", "unknown")
    price = trade.get("price")
    quantity = trade.get("quantity")
    value = trade.get("value")
    company = trade.get("company", "")
    is_cluster = trade.get("is_cluster_buy", False)

    if not ticker:
        return None

    # Build descriptive title
    action = "bought" if trade_type == "buy" else "sold" if trade_type == "sell" else trade_type
    title_parts = [f"Insider {'Purchase' if trade_type == 'buy' else 'Sale'}:"]
    title_parts.append(f"{insider_name}")
    if insider_title:
        title_parts.append(f"({insider_title})")
    title_parts.append(action)
    if quantity is not None:
        title_parts.append(f"{int(quantity):,} shares of")
    title_parts.append(ticker)
    if price is not None:
        title_parts.append(f"at ${price:,.2f}")
    if is_cluster:
        title_parts.append("[CLUSTER BUY]")

    title = " ".join(title_parts)

    # Build detailed content text
    content_lines = [
        f"Insider Trading Alert — {ticker} ({company})",
        f"Insider: {insider_name}" + (f" ({insider_title})" if insider_title else ""),
        f"Action: {trade_type.upper()} — {trade.get('trade_type_raw', '')}",
    ]
    if trade.get("trade_date_str"):
        content_lines.append(f"Trade Date: {trade['trade_date_str']}")
    if trade.get("filing_date_str"):
        content_lines.append(f"Filing Date: {trade['filing_date_str']}")
    if quantity is not None:
        content_lines.append(f"Shares: {int(quantity):,}")
    if price is not None:
        content_lines.append(f"Price: ${price:,.2f}")
    if value is not None:
        content_lines.append(f"Value: ${value:,.0f}")
    if trade.get("shares_owned_after") is not None:
        content_lines.append(f"Shares Owned After: {int(trade['shares_owned_after']):,}")
    if trade.get("delta_own_pct") is not None:
        content_lines.append(f"Change in Ownership: {trade['delta_own_pct']:+.1f}%")
    if is_cluster:
        content_lines.append("** CLUSTER BUY — Multiple insiders buying the same stock **")

    content_text = "\n".join(content_lines)

    # Use trade date as created_at, fall back to filing date, then now
    created_at = trade.get("trade_date") or trade.get("filing_date") or datetime.now(timezone.utc)

    # Build dedup-friendly source_id from key trade attributes
    trade_date_str = trade.get("trade_date_str", "")
    dedup_key = f"{ticker}:{insider_name}:{trade_date_str}:{trade_type}"
    source_id = f"openinsider:{hashlib.sha256(dedup_key.encode()).hexdigest()[:12]}"

    # URL to the ticker's insider page on openinsider
    url = f"{OpenInsiderConfig.BASE_URL}/screener?s={ticker}"
    if trade.get("filing_link"):
        filing_link = trade["filing_link"]
        if filing_link.startswith("/"):
            url = f"{OpenInsiderConfig.BASE_URL}{filing_link}"
        elif filing_link.startswith("http"):
            url = filing_link

    doc = OsintDocument(
        source=SourcePlatform.OPENINSIDER,
        source_id=source_id,
        content_type=ContentType.FILING,
        title=title,
        content_text=content_text,
        url=url,
        created_at=created_at,
        entity_id=ticker,
        quality_signals=QualitySignals(
            score=value,                             # Dollar value of the trade
            engagement_count=int(quantity) if quantity else None,
            is_verified=True,                        # SEC filings are authoritative
        ),
        metadata={
            "data_source": "openinsider",
            "ticker": ticker,
            "company": company,
            "insider_name": insider_name,
            "insider_title": insider_title,
            "trade_type": trade_type,
            "trade_type_raw": trade.get("trade_type_raw", ""),
            "trade_date": trade_date_str,
            "filing_date": trade.get("filing_date_str", ""),
            "price": price,
            "quantity": quantity,
            "value": value,
            "shares_owned_after": trade.get("shares_owned_after"),
            "delta_own_pct": trade.get("delta_own_pct"),
            "is_cluster_buy": is_cluster,
            "source_reliability": 0.9,
        },
    )
    doc.compute_dedup_hash()
    return doc


# ---------------------------------------------------------------------------
# Fetcher
# ---------------------------------------------------------------------------

class OpenInsiderFetcher:
    """Fetches and parses pages from openinsider.com."""

    def __init__(self):
        self._seen_keys: set[str] = set()
        self._parser = OpenInsiderParser()
        self._headers = {
            "User-Agent": OpenInsiderConfig.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def fetch_page(
        self, session: aiohttp.ClientSession, page_key: str, path: str
    ) -> list[dict]:
        """Fetch a single openinsider.com page and parse its trades table."""
        url = f"{OpenInsiderConfig.BASE_URL}{path}"

        try:
            async with session.get(
                url,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=OpenInsiderConfig.REQUEST_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    log.warning("OpenInsider %s returned HTTP %d", page_key, resp.status)
                    return []
                html = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            log.warning("OpenInsider %s fetch failed: %s", page_key, e)
            return []

        trades = self._parser.parse_trades_table(html, page_key)
        log.debug("Parsed %d trades from %s", len(trades), page_key)

        # Deduplicate against previously seen trades
        new_trades = []
        for trade in trades:
            key = (
                f"{trade.get('ticker', '')}:{trade.get('insider_name', '')}:"
                f"{trade.get('trade_date_str', '')}:{trade.get('trade_type', '')}"
            )
            if key not in self._seen_keys:
                self._seen_keys.add(key)
                new_trades.append(trade)

        # Prevent unbounded memory growth
        if len(self._seen_keys) > 20000:
            self._seen_keys = set(list(self._seen_keys)[-10000:])

        return new_trades

    async def fetch_all_pages(self, session: aiohttp.ClientSession) -> dict[str, list[dict]]:
        """Fetch all configured pages with rate limiting between requests."""
        results = {}

        for page_key, path in OpenInsiderConfig.PAGES.items():
            trades = await self.fetch_page(session, page_key, path)
            results[page_key] = trades
            # Respectful delay between requests
            await asyncio.sleep(OpenInsiderConfig.REQUEST_DELAY)

        return results


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------

class OpenInsiderConnector:
    """Orchestrates OpenInsider scraping and Kafka publishing."""

    def __init__(self, publisher: KafkaPublisher):
        self._publisher = publisher
        self._fetcher = OpenInsiderFetcher()

    async def run(self, session: aiohttp.ClientSession):
        log.info(
            "OpenInsider connector started — poll every %ds, %d pages",
            OpenInsiderConfig.POLL_INTERVAL,
            len(OpenInsiderConfig.PAGES),
        )

        while True:
            try:
                published = 0
                page_counts = {}

                results = await self._fetcher.fetch_all_pages(session)

                for page_key, trades in results.items():
                    count = 0
                    for trade in trades:
                        doc = trade_to_document(trade)
                        if doc:
                            self._publisher.publish(
                                OpenInsiderConfig.KAFKA_TOPIC,
                                doc.to_kafka_key(),
                                doc.to_kafka_value(),
                            )
                            count += 1
                    page_counts[page_key] = count
                    published += count

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info(
                        "Published %d trades — cluster_buys: %d, buying: %d, selling: %d, screener: %d",
                        published,
                        page_counts.get("cluster_buys", 0),
                        page_counts.get("insider_buying", 0),
                        page_counts.get("insider_selling", 0),
                        page_counts.get("screener", 0),
                    )
                else:
                    log.debug("No new trades found this cycle")

            except Exception as e:
                log.error("OpenInsider connector error: %s", e, exc_info=True)

            await asyncio.sleep(OpenInsiderConfig.POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------

async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        OpenInsiderConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("OPENINSIDER_POLL_INTERVAL")
    if poll:
        try:
            OpenInsiderConfig.POLL_INTERVAL = int(poll)
        except ValueError:
            pass

    delay = os.environ.get("OPENINSIDER_REQUEST_DELAY")
    if delay:
        try:
            OpenInsiderConfig.REQUEST_DELAY = float(delay)
        except ValueError:
            pass



    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(OpenInsiderConfig, "POLL_INTERVAL")
# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def main():
    load_config_from_env()

    await wait_for_kafka(OpenInsiderConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        OpenInsiderConfig.KAFKA_BOOTSTRAP,
        client_id="openinsider-connector",
    )
    connector = OpenInsiderConnector(publisher)

    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=5),
    ) as session:
        log.info("=" * 60)
        log.info("OpenInsider Connector running")
        log.info("  Pages:     %s", list(OpenInsiderConfig.PAGES.keys()))
        log.info("  Topic:     %s", OpenInsiderConfig.KAFKA_TOPIC)
        log.info("  Poll:      %ds", OpenInsiderConfig.POLL_INTERVAL)
        log.info("  Delay:     %.1fs between requests", OpenInsiderConfig.REQUEST_DELAY)
        log.info("=" * 60)

        try:
            await connector.run(session)
        except asyncio.CancelledError:
            pass

    publisher.flush(timeout=10.0)
    log.info("Final Kafka stats: %s", publisher.stats)
    log.info("OpenInsider Connector shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
