"""
OSINT Pipeline — Earnings Calendar Connector

Ingests upcoming earnings dates, EPS estimates, and revenue estimates
for a configurable watchlist using yfinance. Publishes normalized
OsintDocuments to Kafka for downstream processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

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

DEFAULT_WATCHLIST = [
    "AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "META", "AMZN", "SPY", "QQQ",
]


class EarningsCalendarConfig:
    WATCHLIST: list[str] = list(DEFAULT_WATCHLIST)
    POLL_INTERVAL = 3600  # 1 hour — earnings dates don't change fast
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.earnings.calendar"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("earnings-calendar-connector")


def _format_currency(value) -> str:
    """Format a numeric value as a human-readable currency string."""
    if value is None:
        return "N/A"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(val) >= 1e12:
        return f"${val / 1e12:.2f}T"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.2f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.2f}M"
    return f"${val:,.2f}"


def _days_until(target_date: datetime) -> Optional[int]:
    """Return the number of days from now until the target date."""
    if target_date is None:
        return None
    now = datetime.now(timezone.utc)
    target = target_date if target_date.tzinfo else target_date.replace(tzinfo=timezone.utc)
    delta = (target - now).days
    return delta


def _importance_from_days(days: Optional[int]) -> float:
    """Compute an importance score (0.0–1.0) based on days until earnings.

    Closer earnings dates are more important:
      0-3 days  → 1.0
      4-7 days  → 0.8
      8-14 days → 0.6
      15-30 days → 0.4
      31+ days  → 0.2
    """
    if days is None:
        return 0.1
    if days < 0:
        return 0.3  # already reported — still relevant briefly
    if days <= 3:
        return 1.0
    if days <= 7:
        return 0.8
    if days <= 14:
        return 0.6
    if days <= 30:
        return 0.4
    return 0.2


def earnings_to_document(symbol: str, calendar_data: dict) -> Optional[OsintDocument]:
    """Convert yfinance calendar data for a ticker into an OsintDocument."""
    earnings_date = calendar_data.get("earnings_date")
    if earnings_date is None:
        return None

    eps_estimate = calendar_data.get("eps_estimate")
    revenue_estimate = calendar_data.get("revenue_estimate")
    days = _days_until(earnings_date)
    importance = _importance_from_days(days)

    # Build title
    if days is not None and days >= 0:
        title = f"{symbol} reports earnings in {days} day{'s' if days != 1 else ''}"
    elif days is not None and days < 0:
        title = f"{symbol} earnings report on {earnings_date.strftime('%Y-%m-%d')}"
    else:
        title = f"{symbol} earnings report on {earnings_date.strftime('%Y-%m-%d')}"

    # Build content
    parts = []
    if eps_estimate is not None:
        parts.append(f"EPS estimate ${eps_estimate:.2f}")
    if revenue_estimate is not None:
        parts.append(f"Revenue estimate {_format_currency(revenue_estimate)}")

    if parts:
        content = f"Analyst consensus: {', '.join(parts)}."
    else:
        content = f"{symbol} has an upcoming earnings report."

    if days is not None and days >= 0:
        content += f" {days} day{'s' if days != 1 else ''} until report."
    elif days is not None:
        content += f" Reported {abs(days)} day{'s' if abs(days) != 1 else ''} ago."

    date_str = earnings_date.strftime("%Y-%m-%d")
    doc_id = hashlib.sha256(f"{symbol}:earnings:{date_str}".encode()).hexdigest()[:16]

    doc = OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"earnings:{symbol}:{date_str}:{doc_id}",
        content_type=ContentType.METRIC,
        title=title,
        content_text=content,
        url=f"https://finance.yahoo.com/quote/{symbol}/",
        created_at=datetime.now(timezone.utc),
        entity_id=symbol,
        quality_signals=QualitySignals(
            score=importance,
        ),
        metadata={
            "data_source": "yahoo_finance_earnings",
            "ticker": symbol,
            "earnings_date": date_str,
            "eps_estimate": eps_estimate,
            "revenue_estimate": revenue_estimate,
            "days_until_earnings": days,
            "importance": importance,
        },
    )
    doc.compute_dedup_hash()
    return doc


class EarningsCalendarFetcher:
    """Fetches earnings calendar data from yfinance for each ticker."""

    def __init__(self, watchlist: list[str]):
        self._watchlist = watchlist
        self._seen_keys: set[str] = set()

    async def fetch_all(self) -> list[dict]:
        """Fetch calendar data for all tickers in the watchlist.

        Runs yfinance calls in an executor since yfinance is synchronous.
        """
        loop = asyncio.get_event_loop()
        results = []
        for symbol in self._watchlist:
            try:
                data = await loop.run_in_executor(None, self._fetch_ticker, symbol)
                if data is not None:
                    results.append(data)
            except Exception as e:
                log.warning("Failed to fetch earnings for %s: %s", symbol, e)
        return self._deduplicate(results)

    def _fetch_ticker(self, symbol: str) -> Optional[dict]:
        """Synchronous yfinance call for a single ticker."""
        import yfinance as yf

        try:
            ticker = yf.Ticker(symbol)
            cal = ticker.calendar
        except Exception as e:
            log.warning("yfinance error for %s: %s", symbol, e)
            return None

        if cal is None:
            return None

        # yfinance returns calendar as a dict or DataFrame depending on version
        earnings_date = None
        eps_estimate = None
        revenue_estimate = None

        if isinstance(cal, dict):
            # Modern yfinance returns a dict
            earnings_dates = cal.get("Earnings Date", [])
            if isinstance(earnings_dates, list) and len(earnings_dates) > 0:
                earnings_date = earnings_dates[0]
            elif hasattr(earnings_dates, "__iter__"):
                for d in earnings_dates:
                    earnings_date = d
                    break

            eps_estimate = cal.get("EPS Estimate")
            revenue_estimate = cal.get("Revenue Estimate")
        else:
            # Older yfinance may return a DataFrame
            try:
                if hasattr(cal, "to_dict"):
                    cal_dict = cal.to_dict()
                    # DataFrame .to_dict() returns {col: {row: val}}
                    # Try to extract first column values
                    for col_vals in cal_dict.values():
                        for row_key, val in col_vals.items():
                            row_str = str(row_key).lower()
                            if "earnings date" in row_str and earnings_date is None:
                                earnings_date = val
                            elif "eps" in row_str and "estimate" in row_str:
                                eps_estimate = val
                            elif "revenue" in row_str and "estimate" in row_str:
                                revenue_estimate = val
                        break  # only first column
            except Exception as e:
                log.debug("Calendar parsing fallback failed for %s: %s", symbol, e)

        if earnings_date is None:
            return None

        # Normalize earnings_date to datetime
        if isinstance(earnings_date, str):
            try:
                earnings_date = datetime.fromisoformat(earnings_date.replace("Z", "+00:00"))
            except ValueError:
                return None
        elif hasattr(earnings_date, "to_pydatetime"):
            # pandas Timestamp
            earnings_date = earnings_date.to_pydatetime()
        elif not isinstance(earnings_date, datetime):
            try:
                earnings_date = datetime.fromtimestamp(float(earnings_date), tz=timezone.utc)
            except (TypeError, ValueError):
                return None

        if earnings_date.tzinfo is None:
            earnings_date = earnings_date.replace(tzinfo=timezone.utc)

        # Normalize numeric values
        try:
            eps_estimate = float(eps_estimate) if eps_estimate is not None else None
        except (TypeError, ValueError):
            eps_estimate = None
        try:
            revenue_estimate = float(revenue_estimate) if revenue_estimate is not None else None
        except (TypeError, ValueError):
            revenue_estimate = None

        return {
            "symbol": symbol,
            "earnings_date": earnings_date,
            "eps_estimate": eps_estimate,
            "revenue_estimate": revenue_estimate,
        }

    def _deduplicate(self, results: list[dict]) -> list[dict]:
        """Deduplicate by symbol + earnings_date within a polling cycle."""
        new = []
        for r in results:
            date_str = r["earnings_date"].strftime("%Y-%m-%d")
            key = f"{r['symbol']}:{date_str}"
            if key not in self._seen_keys:
                self._seen_keys.add(key)
                new.append(r)
        # Prevent unbounded memory growth
        if len(self._seen_keys) > 5000:
            self._seen_keys = set(list(self._seen_keys)[-2500:])
        return new


class EarningsCalendarConnector:
    def __init__(self, publisher: KafkaPublisher, watchlist: list[str]):
        self._publisher = publisher
        self._fetcher = EarningsCalendarFetcher(watchlist)

    async def run(self):
        log.info(
            "Earnings Calendar connector started — poll every %ds, %d tickers",
            EarningsCalendarConfig.POLL_INTERVAL,
            len(EarningsCalendarConfig.WATCHLIST),
        )

        while True:
            try:
                published = 0
                results = await self._fetcher.fetch_all()

                for r in results:
                    doc = earnings_to_document(r["symbol"], r)
                    if doc:
                        self._publisher.publish(
                            EarningsCalendarConfig.KAFKA_TOPIC,
                            doc.to_kafka_key(),
                            doc.to_kafka_value(),
                        )
                        published += 1

                if published:
                    self._publisher.flush(timeout=5.0)
                    log.info("Published %d earnings calendar documents", published)
                else:
                    log.info("No new earnings calendar data this cycle")

            except Exception as e:
                log.error("Earnings calendar error: %s", e, exc_info=True)

            await asyncio.sleep(EarningsCalendarConfig.POLL_INTERVAL)


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    """Block until message bus is reachable."""
    wait_for_bus(bootstrap_servers, timeout)


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
        EarningsCalendarConfig.KAFKA_BOOTSTRAP = bootstrap

    poll = os.environ.get("EARNINGS_POLL_INTERVAL")
    if poll:
        EarningsCalendarConfig.POLL_INTERVAL = int(poll)

    watchlist = os.environ.get("EARNINGS_WATCHLIST")
    if watchlist:
        EarningsCalendarConfig.WATCHLIST = [
            s.strip().upper() for s in watchlist.split(",") if s.strip()
        ]

    from connectors.kafka_publisher import apply_poll_multiplier
    apply_poll_multiplier(EarningsCalendarConfig, "POLL_INTERVAL")


async def main():
    load_config_from_env()
    await wait_for_kafka(EarningsCalendarConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        EarningsCalendarConfig.KAFKA_BOOTSTRAP,
        client_id="earnings-calendar-connector",
    )
    connector = EarningsCalendarConnector(publisher, EarningsCalendarConfig.WATCHLIST)

    log.info("=" * 60)
    log.info("Earnings Calendar Connector running")
    log.info("  Watchlist: %s", ", ".join(EarningsCalendarConfig.WATCHLIST))
    log.info("  Topic:     %s", EarningsCalendarConfig.KAFKA_TOPIC)
    log.info("  Poll:      %ds", EarningsCalendarConfig.POLL_INTERVAL)
    log.info("=" * 60)
    try:
        await connector.run()
    except asyncio.CancelledError:
        pass
    publisher.flush(timeout=10.0)


if __name__ == "__main__":
    asyncio.run(main())
