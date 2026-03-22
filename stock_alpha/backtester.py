"""
Signal backtesting and accuracy tracking.

Records signal predictions from the Stock Alpha engine and evaluates them
against actual price movements at 1-day, 5-day, and 10-day horizons.

Usage:
    backtester = SignalBacktester()
    backtester.record_signal(signal_id, ticker, "bullish", 0.65, 0.8, 150.0, now)
    # ... later ...
    backtester.evaluate_pending()
    stats = backtester.get_accuracy_stats()
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Optional

from stock_alpha.technicals import PriceDataProvider

log = logging.getLogger("stock_alpha.backtester")

MAX_RECORDS = 1000

# Horizons: name -> (timedelta, price field, correct field)
_HORIZONS = [
    ("1d", timedelta(days=1), "price_1d", "correct_1d"),
    ("5d", timedelta(days=5), "price_5d", "correct_5d"),
    ("10d", timedelta(days=10), "price_10d", "correct_10d"),
]


@dataclass
class SignalRecord:
    """A recorded signal prediction."""

    signal_id: str
    ticker: str
    direction: str  # "bullish", "bearish", "neutral"
    signal_score: float  # -1 to +1
    confidence: float
    price_at_signal: float
    timestamp: datetime

    # Filled in after evaluation
    price_1d: Optional[float] = None
    price_5d: Optional[float] = None
    price_10d: Optional[float] = None
    correct_1d: Optional[bool] = None
    correct_5d: Optional[bool] = None
    correct_10d: Optional[bool] = None

    def return_pct(self, horizon: str) -> Optional[float]:
        """Percentage return for a given horizon."""
        price_field = f"price_{horizon}"
        price = getattr(self, price_field, None)
        if price is None or self.price_at_signal <= 0:
            return None
        return (price - self.price_at_signal) / self.price_at_signal * 100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        for h in ("1d", "5d", "10d"):
            ret = self.return_pct(h)
            d[f"return_{h}"] = round(ret, 4) if ret is not None else None
        return d


class SignalBacktester:
    """Tracks signal predictions and evaluates accuracy against actual prices."""

    def __init__(self):
        self._records: dict[str, SignalRecord] = {}  # signal_id -> record
        self._price_provider = PriceDataProvider()
        self._lock = threading.Lock()

    def record_signal(
        self,
        signal_id: str,
        ticker: str,
        direction: str,
        signal_score: float,
        confidence: float,
        price_at_signal: float,
        timestamp: datetime,
    ) -> None:
        """Record a new signal prediction."""
        if price_at_signal is None or price_at_signal <= 0:
            log.debug("Skipping signal %s: invalid price %s", signal_id, price_at_signal)
            return

        record = SignalRecord(
            signal_id=signal_id,
            ticker=ticker,
            direction=direction,
            signal_score=signal_score,
            confidence=confidence,
            price_at_signal=price_at_signal,
            timestamp=timestamp,
        )

        with self._lock:
            self._records[signal_id] = record
            # Trim oldest if over capacity
            if len(self._records) > MAX_RECORDS:
                oldest_ids = sorted(
                    self._records,
                    key=lambda sid: self._records[sid].timestamp,
                )[: len(self._records) - MAX_RECORDS]
                for sid in oldest_ids:
                    del self._records[sid]

        log.info("Recorded signal %s: %s %s @ $%.2f", signal_id, direction, ticker, price_at_signal)

    def evaluate_pending(self) -> int:
        """Check pending signals against actual price movement.

        For each unresolved horizon, fetches the current price and evaluates:
        - 1d: if 24h+ have passed, compare price
        - 5d: if 5+ days have passed, compare price
        - 10d: if 10+ days have passed, compare price

        A bullish signal is 'correct' if price went up, bearish if price went
        down, and neutral is 'correct' if price moved less than 1%.

        Returns the number of newly evaluated horizons.
        """
        now = datetime.now(timezone.utc)
        evaluated = 0

        with self._lock:
            pending = [
                r for r in self._records.values()
                if r.correct_1d is None or r.correct_5d is None or r.correct_10d is None
            ]

        # Group by ticker to minimise yfinance calls
        tickers: dict[str, list[SignalRecord]] = {}
        for rec in pending:
            tickers.setdefault(rec.ticker, []).append(rec)

        for ticker, records in tickers.items():
            df = self._price_provider.get_prices(ticker)
            if df is None or df.empty:
                continue

            # Get the latest close price
            close_col = "close" if "close" in df.columns else "Close"
            if close_col not in df.columns:
                continue

            current_price = float(df[close_col].iloc[-1])

            with self._lock:
                for rec in records:
                    for _label, horizon_delta, price_attr, correct_attr in _HORIZONS:
                        # Already evaluated for this horizon
                        if getattr(rec, correct_attr) is not None:
                            continue

                        # Not enough time has passed
                        if now - rec.timestamp < horizon_delta:
                            continue

                        # Try to get the close price on the exact horizon date
                        target_date = rec.timestamp + horizon_delta
                        horizon_price = self._get_price_on_date(df, close_col, target_date)
                        if horizon_price is None:
                            # Fall back to current price if target date is recent
                            if now - target_date < timedelta(days=3):
                                horizon_price = current_price
                            else:
                                continue

                        setattr(rec, price_attr, horizon_price)
                        ret_pct = (horizon_price - rec.price_at_signal) / rec.price_at_signal * 100

                        correct = self._evaluate_correctness(rec.direction, ret_pct)
                        setattr(rec, correct_attr, correct)
                        evaluated += 1

        if evaluated > 0:
            log.info("Evaluated %d signal horizons", evaluated)
        return evaluated

    @staticmethod
    def _get_price_on_date(df, close_col: str, target: datetime) -> Optional[float]:
        """Get the close price on or just after the target date.

        Handles weekends/holidays by using the next available trading day.
        """
        try:
            import pandas as pd

            # Normalise target to date for comparison
            if hasattr(target, "date"):
                target_date = target.date()
            else:
                target_date = target

            # df index is typically DatetimeIndex from yfinance
            for i in range(5):  # Look up to 5 days forward (covers weekends + holidays)
                check_date = target_date + timedelta(days=i)
                mask = df.index.date == check_date if hasattr(df.index, "date") else None
                if mask is not None and mask.any():
                    return float(df.loc[mask, close_col].iloc[-1])

            return None
        except Exception:
            return None

    @staticmethod
    def _evaluate_correctness(direction: str, return_pct: float) -> bool:
        """Determine if a signal's prediction was correct."""
        if direction == "bullish":
            return return_pct > 0
        elif direction == "bearish":
            return return_pct < 0
        else:
            # Neutral: correct if price moved less than 1%
            return abs(return_pct) < 1.0

    def get_accuracy_stats(self) -> dict:
        """Return aggregate accuracy statistics."""
        with self._lock:
            records = list(self._records.values())

        total = len(records)
        if total == 0:
            return {
                "total_signals": 0,
                "evaluated_1d": 0,
                "evaluated_5d": 0,
                "evaluated_10d": 0,
                "accuracy_1d": None,
                "accuracy_5d": None,
                "accuracy_10d": None,
                "avg_return_bullish_1d": None,
                "avg_return_bearish_1d": None,
                "best_signal": None,
                "worst_signal": None,
                "by_confidence_bucket": {
                    "high": {"count": 0, "accuracy_1d": None},
                    "medium": {"count": 0, "accuracy_1d": None},
                    "low": {"count": 0, "accuracy_1d": None},
                },
                "recent_signals": [],
            }

        # Horizon accuracy
        def _accuracy(attr: str) -> tuple[int, Optional[float]]:
            evaluated = [r for r in records if getattr(r, attr) is not None]
            count = len(evaluated)
            if count == 0:
                return 0, None
            correct = sum(1 for r in evaluated if getattr(r, attr))
            return count, round(correct / count, 4)

        eval_1d, acc_1d = _accuracy("correct_1d")
        eval_5d, acc_5d = _accuracy("correct_5d")
        eval_10d, acc_10d = _accuracy("correct_10d")

        # Average returns by direction (1d horizon)
        bullish_returns = [
            r.return_pct("1d") for r in records
            if r.direction == "bullish" and r.return_pct("1d") is not None
        ]
        bearish_returns = [
            r.return_pct("1d") for r in records
            if r.direction == "bearish" and r.return_pct("1d") is not None
        ]
        avg_bull = round(sum(bullish_returns) / len(bullish_returns), 4) if bullish_returns else None
        avg_bear = round(sum(bearish_returns) / len(bearish_returns), 4) if bearish_returns else None

        # Best/worst signal by 1d return
        evaluated_1d = [r for r in records if r.return_pct("1d") is not None]
        best = None
        worst = None
        if evaluated_1d:
            best_rec = max(evaluated_1d, key=lambda r: r.return_pct("1d"))
            worst_rec = min(evaluated_1d, key=lambda r: r.return_pct("1d"))
            best = best_rec.to_dict()
            worst = worst_rec.to_dict()

        # Confidence buckets
        def _bucket_accuracy(recs: list[SignalRecord]) -> dict:
            count = len(recs)
            evaluated = [r for r in recs if r.correct_1d is not None]
            if not evaluated:
                return {"count": count, "accuracy_1d": None}
            correct = sum(1 for r in evaluated if r.correct_1d)
            return {"count": count, "accuracy_1d": round(correct / len(evaluated), 4)}

        high = [r for r in records if r.confidence > 0.7]
        medium = [r for r in records if 0.4 <= r.confidence <= 0.7]
        low = [r for r in records if r.confidence < 0.4]

        # Recent signals (last 20)
        sorted_records = sorted(records, key=lambda r: r.timestamp, reverse=True)
        recent = [r.to_dict() for r in sorted_records[:20]]

        return {
            "total_signals": total,
            "evaluated_1d": eval_1d,
            "evaluated_5d": eval_5d,
            "evaluated_10d": eval_10d,
            "accuracy_1d": acc_1d,
            "accuracy_5d": acc_5d,
            "accuracy_10d": acc_10d,
            "avg_return_bullish_1d": avg_bull,
            "avg_return_bearish_1d": avg_bear,
            "best_signal": best,
            "worst_signal": worst,
            "by_confidence_bucket": {
                "high": _bucket_accuracy(high),
                "medium": _bucket_accuracy(medium),
                "low": _bucket_accuracy(low),
            },
            "recent_signals": recent,
        }

    def cleanup(self, max_age_days: int = 30) -> int:
        """Remove signals older than N days. Returns count removed."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        removed = 0
        with self._lock:
            to_remove = [
                sid for sid, rec in self._records.items()
                if rec.timestamp < cutoff
            ]
            for sid in to_remove:
                del self._records[sid]
                removed += 1
        if removed:
            log.info("Cleaned up %d signals older than %d days", removed, max_age_days)
        return removed
