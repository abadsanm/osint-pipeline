"""
Insider Trading Scoring — composite insider sentiment per ticker.

Aggregates SEC Form 4 insider trading data (ingested via the OpenInsider
connector) and produces a bounded score in [-1, +1] reflecting net insider
sentiment.  Cluster buys and C-suite transactions receive elevated weight
because they historically carry the strongest alpha signal.

Kafka source topic: finance.insider.trades
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("stock_alpha.insider_scoring")

# ---------------------------------------------------------------------------
# Title classification helpers
# ---------------------------------------------------------------------------

_C_SUITE_TITLES = {"ceo", "cfo", "coo", "president", "chairman"}
_DIRECTOR_TITLES = {"director", "dir"}


def _classify_title(raw_title: str) -> str:
    """Return 'c_suite', 'director', or 'other' based on the insider title."""
    lower = raw_title.lower().strip()
    for keyword in _C_SUITE_TITLES:
        if keyword in lower:
            return "c_suite"
    for keyword in _DIRECTOR_TITLES:
        if keyword in lower:
            return "director"
    return "other"


def _weight_for_title(classification: str) -> float:
    """Multiplier applied to the dollar value of a transaction."""
    if classification == "c_suite":
        return 2.0
    if classification == "director":
        return 1.2
    return 1.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class InsiderTransaction:
    """A single insider trade parsed from a Kafka message."""

    ticker: str
    insider_name: str
    insider_title: str
    trade_type: str  # "buy" or "sell"
    trade_date: datetime
    filing_date: datetime
    price: float
    quantity: int
    value: float
    delta_own_pct: Optional[float] = None
    is_cluster_buy: bool = False


@dataclass
class InsiderScore:
    """Composite insider sentiment score for a single ticker."""

    ticker: str
    score: float  # -1 (heavy selling) to +1 (heavy buying)
    buy_count_30d: int
    sell_count_30d: int
    buy_value_30d: float
    sell_value_30d: float
    net_value_30d: float
    cluster_buy_detected: bool
    c_suite_activity: bool
    largest_transaction: Optional[dict] = None
    regime: str = "neutral"
    data_points: int = 0


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class InsiderScorer:
    """Stateful per-ticker insider transaction aggregator and scorer.

    Usage::

        scorer = InsiderScorer()
        scorer.ingest(kafka_msg_value)          # call per message
        result = scorer.compute("AAPL")         # on-demand scoring
    """

    def __init__(self) -> None:
        self._transactions: dict[str, list[InsiderTransaction]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, data: dict) -> Optional[InsiderTransaction]:
        """Parse a Kafka message dict and store the transaction.

        The message is expected to carry an OsintDocument whose ``metadata``
        dict contains the fields produced by the OpenInsider connector
        (ticker, insider_name, insider_title, trade_type, price, quantity,
        value, trade_date, filing_date, delta_own_pct, is_cluster_buy).

        Returns the parsed ``InsiderTransaction`` on success, or ``None``
        if the message could not be parsed.
        """
        try:
            meta = data.get("metadata") or data
            ticker = (meta.get("ticker") or data.get("entity_id") or "").upper()
            if not ticker:
                log.debug("Skipping message with no ticker: %s", data.get("source_id"))
                return None

            insider_name = meta.get("insider_name", "Unknown")
            insider_title = meta.get("insider_title", "")
            trade_type = (meta.get("trade_type") or "unknown").lower()
            if trade_type not in ("buy", "sell"):
                log.debug("Unknown trade_type '%s' for %s — skipping", trade_type, ticker)
                return None

            price = _safe_float(meta.get("price"), 0.0)
            quantity = _safe_int(meta.get("quantity"), 0)
            value = _safe_float(meta.get("value"), price * quantity)

            trade_date = _parse_date(meta.get("trade_date"))
            filing_date = _parse_date(meta.get("filing_date"))
            now = datetime.now(timezone.utc)
            if trade_date is None:
                trade_date = filing_date or now
            if filing_date is None:
                filing_date = trade_date

            delta_own_pct = _safe_float(meta.get("delta_own_pct"))
            is_cluster = bool(meta.get("is_cluster_buy", False))

            txn = InsiderTransaction(
                ticker=ticker,
                insider_name=insider_name,
                insider_title=insider_title,
                trade_type=trade_type,
                trade_date=trade_date,
                filing_date=filing_date,
                price=price,
                quantity=quantity,
                value=value,
                delta_own_pct=delta_own_pct,
                is_cluster_buy=is_cluster,
            )
            self._transactions[ticker].append(txn)
            log.debug(
                "Ingested %s %s by %s (%s) — $%,.0f",
                ticker, trade_type, insider_name, insider_title, value,
            )
            return txn

        except Exception:
            log.exception("Failed to ingest insider trade message")
            return None

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------

    def compute(self, ticker: str) -> Optional[InsiderScore]:
        """Compute the insider sentiment score for *ticker*.

        Considers only transactions within the last 30 days.  Returns
        ``None`` if there are no recent transactions.
        """
        all_txns = self._transactions.get(ticker)
        if not all_txns:
            return None

        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        recent = [t for t in all_txns if t.trade_date >= cutoff]

        if not recent:
            return None

        buys = [t for t in recent if t.trade_type == "buy"]
        sells = [t for t in recent if t.trade_type == "sell"]

        buy_value = sum(t.value for t in buys)
        sell_value = sum(t.value for t in sells)
        net_value = buy_value - sell_value

        cluster_buy_detected = any(t.is_cluster_buy for t in recent)
        c_suite_activity = any(
            _classify_title(t.insider_title) == "c_suite" for t in recent
        )

        # Detect insider clustering: 3+ unique insiders buying within 7 days
        cluster_window = timedelta(days=7)
        recent_buyers: dict[str, datetime] = {}
        for t in sorted(buys, key=lambda x: x.trade_date):
            recent_buyers[t.insider_name] = t.trade_date
            # Prune buyers outside window
            cutoff_cluster = t.trade_date - cluster_window
            recent_buyers = {k: v for k, v in recent_buyers.items() if v >= cutoff_cluster}
            if len(recent_buyers) >= 3:
                cluster_buy_detected = True
                break

        # --- Weighted ratio with time decay -----------------------------------
        now = datetime.now(timezone.utc)
        weighted_buy = 0.0
        weighted_sell = 0.0
        for txn in recent:
            title_class = _classify_title(txn.insider_title)
            w = _weight_for_title(title_class)
            # Exponential time decay: half-life of 7 days
            days_ago = (now - txn.trade_date).total_seconds() / 86400
            time_weight = math.exp(-days_ago / 10.0)  # 10-day decay constant
            if txn.trade_type == "buy":
                cluster_mult = 1.5 if txn.is_cluster_buy else 1.0
                weighted_buy += txn.value * w * cluster_mult * time_weight
            else:
                weighted_sell += txn.value * w * time_weight

        total_weighted = weighted_buy + weighted_sell
        if total_weighted > 0:
            weighted_ratio = (weighted_buy - weighted_sell) / total_weighted
        else:
            weighted_ratio = 0.0

        score = math.tanh(weighted_ratio * 2)

        # Boost score when clustering detected (3+ insiders buying together)
        if cluster_buy_detected and score > 0:
            score = min(1.0, score * 1.5)
        # C-suite buying is higher conviction
        if c_suite_activity and score > 0:
            score = min(1.0, score * 1.25)

        # --- Regime ----------------------------------------------------------
        if score > 0.5:
            regime = "strong_buy"
        elif score > 0.15:
            regime = "moderate_buy"
        elif score > -0.15:
            regime = "neutral"
        elif score > -0.5:
            regime = "moderate_sell"
        else:
            regime = "strong_sell"

        # --- Largest transaction ---------------------------------------------
        largest = max(recent, key=lambda t: t.value)
        largest_transaction = {
            "insider": largest.insider_name,
            "title": largest.insider_title,
            "type": largest.trade_type,
            "value": largest.value,
            "date": largest.trade_date.isoformat(),
        }

        return InsiderScore(
            ticker=ticker,
            score=round(score, 4),
            buy_count_30d=len(buys),
            sell_count_30d=len(sells),
            buy_value_30d=round(buy_value, 2),
            sell_value_30d=round(sell_value, 2),
            net_value_30d=round(net_value, 2),
            cluster_buy_detected=cluster_buy_detected,
            c_suite_activity=c_suite_activity,
            largest_transaction=largest_transaction,
            regime=regime,
            data_points=len(recent),
        )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, max_age_days: int = 90) -> None:
        """Remove transactions older than *max_age_days*."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        empty: list[str] = []
        for ticker, txns in self._transactions.items():
            self._transactions[ticker] = [t for t in txns if t.trade_date >= cutoff]
            if not self._transactions[ticker]:
                empty.append(ticker)
        for t in empty:
            del self._transactions[t]
        if empty:
            log.info("Cleaned up stale insider data for %d tickers", len(empty))

    def get_tracked_tickers(self) -> list[str]:
        """Return tickers with at least one stored transaction."""
        return list(self._transactions.keys())


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _safe_float(val: object, default: Optional[float] = None) -> Optional[float]:
    """Coerce *val* to float, returning *default* on failure."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: object, default: int = 0) -> int:
    """Coerce *val* to int, returning *default* on failure."""
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _parse_date(val: object) -> Optional[datetime]:
    """Best-effort date parsing from str or datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val
    if not isinstance(val, str) or not val.strip():
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(val.strip(), fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    log.debug("Could not parse date: %s", val)
    return None
