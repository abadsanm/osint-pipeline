"""
Historical feature store for ML training data.

Snapshots all indicator values per ticker at a point in time and labels
them with actual forward returns once enough time has passed.  Backed by
SQLite for durability across restarts.

Usage::

    store = FeatureStore()
    # Option 1: snapshot manually
    store.snapshot("AAPL", price=185.0, rsi=42.3, sentiment_score=0.35)

    # Option 2: extract everything from a running engine
    store.snapshot_from_engine("AAPL", engine)

    # After some days, back-fill labels
    store.label_with_returns()

    # Export for training
    X, y_1d, y_5d, y_10d, names = store.get_feature_matrix()
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, fields, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("stock_alpha.feature_store")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Feature column names (deterministic order used everywhere)
# ---------------------------------------------------------------------------

FEATURE_COLUMNS: list[str] = [
    "price",
    "price_change_1d_pct",
    "volume",
    "volume_z_score",
    "rsi",
    "macd_histogram",
    "bb_pctb",
    "sma20_distance_pct",
    "sma50_distance_pct",
    "atr_pct",
    "obv_slope",
    "sentiment_score",
    "sentiment_velocity",
    "sentiment_acceleration",
    "mention_volume",
    "source_count",
    "svc_value",
    "vwap_distance_pct",
    "fvg_bias",
    "volume_profile_poc_distance_pct",
    "cumulative_delta_normalized",
    "imbalance_ratio",
    "insider_score",
    "macro_regime_score",
    "correlation_confidence",
]

LABEL_COLUMNS: list[str] = [
    "return_1d",
    "return_5d",
    "return_10d",
    "direction_1d",
    "direction_5d",
    "direction_10d",
]


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class FeatureSnapshot:
    """All features captured at a point in time for a ticker."""

    ticker: str
    timestamp: datetime

    # Price context
    price: float
    price_change_1d_pct: Optional[float] = None
    volume: Optional[float] = None
    volume_z_score: Optional[float] = None

    # Technical indicators
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    bb_pctb: Optional[float] = None
    sma20_distance_pct: Optional[float] = None
    sma50_distance_pct: Optional[float] = None
    atr_pct: Optional[float] = None
    obv_slope: Optional[float] = None

    # Sentiment (from pipeline)
    sentiment_score: Optional[float] = None
    sentiment_velocity: Optional[float] = None
    sentiment_acceleration: Optional[float] = None
    mention_volume: Optional[int] = None
    source_count: Optional[int] = None
    svc_value: Optional[float] = None

    # Microstructure
    vwap_distance_pct: Optional[float] = None
    fvg_bias: Optional[float] = None
    volume_profile_poc_distance_pct: Optional[float] = None

    # Order flow
    cumulative_delta_normalized: Optional[float] = None
    imbalance_ratio: Optional[float] = None

    # Insider
    insider_score: Optional[float] = None

    # Macro
    macro_regime_score: Optional[float] = None

    # Correlation
    correlation_confidence: Optional[float] = None

    # Labels (filled in later by label_with_returns)
    return_1d: Optional[float] = None
    return_5d: Optional[float] = None
    return_10d: Optional[float] = None
    direction_1d: Optional[str] = None
    direction_5d: Optional[str] = None
    direction_10d: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(val) -> Optional[float]:
    """Convert a value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _direction_label(ret: Optional[float]) -> Optional[str]:
    """Classify a % return into a direction label."""
    if ret is None:
        return None
    if ret > 0.5:
        return "up"
    if ret < -0.5:
        return "down"
    return "flat"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL,
    price_change_1d_pct REAL,
    volume REAL,
    volume_z_score REAL,
    rsi REAL,
    macd_histogram REAL,
    bb_pctb REAL,
    sma20_distance_pct REAL,
    sma50_distance_pct REAL,
    atr_pct REAL,
    obv_slope REAL,
    sentiment_score REAL,
    sentiment_velocity REAL,
    sentiment_acceleration REAL,
    mention_volume INTEGER,
    source_count INTEGER,
    svc_value REAL,
    vwap_distance_pct REAL,
    fvg_bias REAL,
    volume_profile_poc_distance_pct REAL,
    cumulative_delta_normalized REAL,
    imbalance_ratio REAL,
    insider_score REAL,
    macro_regime_score REAL,
    correlation_confidence REAL,
    return_1d REAL,
    return_5d REAL,
    return_10d REAL,
    direction_1d TEXT,
    direction_5d TEXT,
    direction_10d TEXT,
    UNIQUE(ticker, timestamp)
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_ts ON snapshots(ticker, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_unlabeled ON snapshots(return_1d) WHERE return_1d IS NULL;",
]


class FeatureStore:
    """Stores historical feature snapshots in SQLite for ML training."""

    def __init__(self, db_path: str = "data/features.db"):
        # Resolve relative to project root
        if not os.path.isabs(db_path):
            db_path = str(_PROJECT_ROOT / db_path)

        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()
        log.info("FeatureStore opened: %s", db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self):
        cur = self._conn.cursor()
        cur.execute(_CREATE_TABLE)
        for idx_sql in _CREATE_INDEXES:
            cur.execute(idx_sql)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Snapshot creation
    # ------------------------------------------------------------------

    def snapshot(self, ticker: str, **kwargs) -> FeatureSnapshot:
        """Create and store a feature snapshot.

        Parameters
        ----------
        ticker : str
            Stock ticker symbol.
        **kwargs
            Any FeatureSnapshot fields (except ticker).  ``timestamp``
            defaults to now (UTC) if not provided.
        """
        kwargs.setdefault("timestamp", datetime.now(timezone.utc))
        snap = FeatureSnapshot(ticker=ticker, **kwargs)
        self._store(snap)
        return snap

    def snapshot_from_engine(self, ticker: str, engine) -> Optional[FeatureSnapshot]:
        """Extract a full feature snapshot from a StockAlphaEngine instance.

        Reads all cached data from the engine for this ticker.  Returns
        ``None`` if no price data is available.
        """
        import pandas as pd

        # 1. Price + technicals
        df = engine._prices.get_prices(ticker)
        if df is None or df.empty:
            return None

        from stock_alpha.technicals import compute_technicals
        tech_df = compute_technicals(df)
        latest = tech_df.iloc[-1]

        price = _safe_float(latest.get("close"))
        if price is None or price <= 0:
            return None

        # Price change (1-day)
        price_change_1d = None
        if len(tech_df) >= 2:
            prev_close = _safe_float(tech_df.iloc[-2].get("close"))
            if prev_close and prev_close > 0:
                price_change_1d = (price - prev_close) / prev_close * 100

        # Volume and volume z-score
        vol = _safe_float(latest.get("volume"))
        vol_sma = _safe_float(latest.get("volume_sma"))
        vol_z = None
        if vol is not None and vol_sma is not None and vol_sma > 0:
            # Approximate z-score using volume_sma as mean
            # std estimation: use recent 20 bars if available
            vol_series = tech_df["volume"].tail(20)
            vol_std = float(vol_series.std())
            if vol_std > 0:
                vol_z = (vol - vol_sma) / vol_std

        # Technical indicators
        rsi = _safe_float(latest.get("rsi"))
        macd_hist = _safe_float(latest.get("macd_hist"))
        bb_pctb = _safe_float(latest.get("bb_pctb"))

        sma20 = _safe_float(latest.get("sma_20"))
        sma50 = _safe_float(latest.get("sma_50"))
        sma20_dist = ((price - sma20) / sma20 * 100) if sma20 and sma20 > 0 else None
        sma50_dist = ((price - sma50) / sma50 * 100) if sma50 and sma50 > 0 else None

        atr = _safe_float(latest.get("atr"))
        atr_pct = (atr / price * 100) if atr and price > 0 else None

        # OBV slope over last 5 bars
        obv_slope = None
        if "obv" in tech_df.columns and len(tech_df) >= 5:
            obv_tail = tech_df["obv"].tail(5).dropna()
            if len(obv_tail) >= 2:
                obv_slope = float(obv_tail.iloc[-1] - obv_tail.iloc[0]) / len(obv_tail)

        # 2. Sentiment velocity
        sent_vel = None
        sent_acc = None
        sent_score_from_vel = None
        try:
            velocity = engine._velocity.compute(ticker)
            if velocity:
                sent_vel = velocity.velocity
                sent_acc = velocity.acceleration
        except Exception:
            pass

        # 3. SVC
        svc_val = None
        try:
            svc_result = engine._svc.compute(ticker)
            if svc_result:
                svc_val = svc_result.svc_value
        except Exception:
            pass

        # 4. Insider
        insider_val = None
        try:
            insider = engine._insider.compute(ticker)
            if insider:
                insider_val = insider.score
        except Exception:
            pass

        # 5. Macro
        macro_val = None
        try:
            macro = engine._macro.classify()
            if macro:
                macro_val = macro.regime_score
        except Exception:
            pass

        # 6. Microstructure
        vwap_dist = None
        fvg_bias_val = None
        vp_poc_dist = None
        try:
            micro = engine._compute_microstructure(ticker)
            if micro:
                vwap_pos = micro.get("anchored_vwap_position")
                if vwap_pos is not None:
                    vwap_dist = vwap_pos * 100  # convert to %

                fvg_bias_val = _safe_float(micro.get("fvg_bias"))

                vp_pos = micro.get("volume_profile_position")
                if vp_pos is not None:
                    vp_poc_dist = vp_pos * 100
        except Exception:
            pass

        # 7. Order flow
        cum_delta_norm = None
        imbalance = None
        try:
            flow = engine._compute_order_flow(ticker)
            if flow:
                cum_delta_norm = _safe_float(flow.get("cumulative_delta_normalized"))
                imbalance = _safe_float(flow.get("imbalance_ratio"))
        except Exception:
            pass

        snap = FeatureSnapshot(
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            price=price,
            price_change_1d_pct=price_change_1d,
            volume=vol,
            volume_z_score=vol_z,
            rsi=rsi,
            macd_histogram=macd_hist,
            bb_pctb=bb_pctb,
            sma20_distance_pct=sma20_dist,
            sma50_distance_pct=sma50_dist,
            atr_pct=atr_pct,
            obv_slope=obv_slope,
            sentiment_score=sent_score_from_vel,
            sentiment_velocity=sent_vel,
            sentiment_acceleration=sent_acc,
            mention_volume=None,
            source_count=None,
            svc_value=svc_val,
            vwap_distance_pct=vwap_dist,
            fvg_bias=fvg_bias_val,
            volume_profile_poc_distance_pct=vp_poc_dist,
            cumulative_delta_normalized=cum_delta_norm,
            imbalance_ratio=imbalance,
            insider_score=insider_val,
            macro_regime_score=macro_val,
            correlation_confidence=None,
        )

        self._store(snap)
        return snap

    # ------------------------------------------------------------------
    # Labeling
    # ------------------------------------------------------------------

    def label_with_returns(self, max_age_days: int = 30):
        """Label unlabeled snapshots with actual forward returns.

        For each snapshot without ``return_1d`` set, fetches the actual
        close price at T+1d, T+5d, T+10d using yfinance and computes
        percentage returns.  Groups by ticker to minimise API calls.
        Only processes snapshots older than 1 trading day.
        """
        from stock_alpha.technicals import PriceDataProvider

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        min_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, ticker, timestamp, price
                FROM snapshots
                WHERE return_1d IS NULL
                  AND timestamp >= ?
                  AND timestamp <= ?
                ORDER BY ticker, timestamp
                """,
                (min_date.isoformat(), cutoff.isoformat()),
            ).fetchall()

        if not rows:
            log.debug("label_with_returns: no unlabeled snapshots to process")
            return

        # Group by ticker
        ticker_rows: dict[str, list[dict]] = {}
        for row in rows:
            t = row["ticker"]
            ticker_rows.setdefault(t, []).append({
                "id": row["id"],
                "timestamp": row["timestamp"],
                "price": row["price"],
            })

        provider = PriceDataProvider()
        labeled_count = 0

        for ticker, entries in ticker_rows.items():
            df = provider.get_prices(ticker)
            if df is None or df.empty:
                log.debug("label_with_returns: no price data for %s", ticker)
                continue

            # Ensure index is timezone-aware for comparison
            import pandas as pd
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")

            for entry in entries:
                snap_ts = datetime.fromisoformat(entry["timestamp"])
                if snap_ts.tzinfo is None:
                    snap_ts = snap_ts.replace(tzinfo=timezone.utc)

                snap_price = entry["price"]
                if snap_price is None or snap_price <= 0:
                    continue

                # Find forward close prices at T+1d, T+5d, T+10d
                returns = {}
                for horizon_name, horizon_days in [("1d", 1), ("5d", 5), ("10d", 10)]:
                    target_date = snap_ts + timedelta(days=horizon_days)
                    # Find the nearest trading day close at or after target_date
                    future_bars = df[df.index >= target_date]
                    if future_bars.empty:
                        returns[horizon_name] = None
                        continue
                    future_close = _safe_float(future_bars.iloc[0].get("close"))
                    if future_close is not None:
                        returns[horizon_name] = (future_close - snap_price) / snap_price * 100
                    else:
                        returns[horizon_name] = None

                # Only update if we have at least the 1d return
                if returns.get("1d") is None:
                    continue

                r1d = returns["1d"]
                r5d = returns.get("5d")
                r10d = returns.get("10d")

                with self._lock:
                    self._conn.execute(
                        """
                        UPDATE snapshots SET
                            return_1d = ?,
                            return_5d = ?,
                            return_10d = ?,
                            direction_1d = ?,
                            direction_5d = ?,
                            direction_10d = ?
                        WHERE id = ?
                        """,
                        (
                            r1d,
                            r5d,
                            r10d,
                            _direction_label(r1d),
                            _direction_label(r5d),
                            _direction_label(r10d),
                            entry["id"],
                        ),
                    )
                labeled_count += 1

        with self._lock:
            self._conn.commit()

        log.info("label_with_returns: labeled %d snapshots across %d tickers",
                 labeled_count, len(ticker_rows))

    # ------------------------------------------------------------------
    # Query / export
    # ------------------------------------------------------------------

    def get_training_data(
        self,
        ticker: Optional[str] = None,
        min_date: Optional[str] = None,
    ) -> list[dict]:
        """Return labeled snapshots as list of dicts for ML training.

        Only returns rows where ``return_1d`` is not None (i.e., labeled).
        """
        query = "SELECT * FROM snapshots WHERE return_1d IS NOT NULL"
        params: list = []

        if ticker:
            query += " AND ticker = ?"
            params.append(ticker)
        if min_date:
            query += " AND timestamp >= ?"
            params.append(min_date)

        query += " ORDER BY ticker, timestamp"

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()

        return [dict(row) for row in rows]

    def get_feature_matrix(self, ticker: Optional[str] = None) -> tuple:
        """Return (X, y_1d, y_5d, y_10d, feature_names) as numpy arrays.

        X has shape (n_samples, n_features).  Rows with any None in
        feature columns are skipped.  Only labeled rows are included.

        Returns
        -------
        tuple
            (X, y_1d, y_5d, y_10d, feature_names) where X and y are
            numpy ndarrays, feature_names is a list of str.

        Raises
        ------
        ImportError
            If numpy is not installed.
        """
        try:
            import numpy as np
        except ImportError:
            raise ImportError(
                "numpy is required for get_feature_matrix(). "
                "Install it with: pip install numpy"
            )

        data = self.get_training_data(ticker=ticker)
        if not data:
            empty = np.empty((0, len(FEATURE_COLUMNS)))
            return empty, np.array([]), np.array([]), np.array([]), list(FEATURE_COLUMNS)

        X_rows = []
        y1_list = []
        y5_list = []
        y10_list = []

        for row in data:
            # Check all feature columns are present and non-None
            feature_vals = []
            skip = False
            for col in FEATURE_COLUMNS:
                val = row.get(col)
                if val is None:
                    skip = True
                    break
                try:
                    feature_vals.append(float(val))
                except (TypeError, ValueError):
                    skip = True
                    break
            if skip:
                continue

            X_rows.append(feature_vals)
            y1_list.append(float(row.get("return_1d", 0) or 0))
            y5_list.append(float(row.get("return_5d", 0) or 0))
            y10_list.append(float(row.get("return_10d", 0) or 0))

        X = np.array(X_rows, dtype=np.float64) if X_rows else np.empty((0, len(FEATURE_COLUMNS)))
        y_1d = np.array(y1_list, dtype=np.float64)
        y_5d = np.array(y5_list, dtype=np.float64)
        y_10d = np.array(y10_list, dtype=np.float64)

        return X, y_1d, y_5d, y_10d, list(FEATURE_COLUMNS)

    def get_stats(self) -> dict:
        """Return summary statistics about the feature store."""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            labeled = self._conn.execute(
                "SELECT COUNT(*) FROM snapshots WHERE return_1d IS NOT NULL"
            ).fetchone()[0]
            ticker_count = self._conn.execute(
                "SELECT COUNT(DISTINCT ticker) FROM snapshots"
            ).fetchone()[0]
            date_range = self._conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM snapshots"
            ).fetchone()

        return {
            "total_snapshots": total,
            "labeled_snapshots": labeled,
            "unlabeled_snapshots": total - labeled,
            "ticker_count": ticker_count,
            "date_min": date_range[0],
            "date_max": date_range[1],
            "feature_count": len(FEATURE_COLUMNS),
            "db_path": self._db_path,
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, max_age_days: int = 90):
        """Remove snapshots older than ``max_age_days``."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM snapshots WHERE timestamp < ?", (cutoff,)
            )
            self._conn.commit()
        deleted = cur.rowcount
        if deleted:
            log.info("cleanup: removed %d snapshots older than %d days", deleted, max_age_days)

    def close(self):
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _store(self, snap: FeatureSnapshot):
        """Insert or replace a snapshot in the database."""
        ts_str = snap.timestamp.isoformat() if isinstance(snap.timestamp, datetime) else str(snap.timestamp)

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    ticker, timestamp,
                    price, price_change_1d_pct, volume, volume_z_score,
                    rsi, macd_histogram, bb_pctb,
                    sma20_distance_pct, sma50_distance_pct, atr_pct, obv_slope,
                    sentiment_score, sentiment_velocity, sentiment_acceleration,
                    mention_volume, source_count, svc_value,
                    vwap_distance_pct, fvg_bias, volume_profile_poc_distance_pct,
                    cumulative_delta_normalized, imbalance_ratio,
                    insider_score, macro_regime_score, correlation_confidence,
                    return_1d, return_5d, return_10d,
                    direction_1d, direction_5d, direction_10d
                ) VALUES (
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    snap.ticker, ts_str,
                    _safe_float(snap.price),
                    _safe_float(snap.price_change_1d_pct),
                    _safe_float(snap.volume),
                    _safe_float(snap.volume_z_score),
                    _safe_float(snap.rsi),
                    _safe_float(snap.macd_histogram),
                    _safe_float(snap.bb_pctb),
                    _safe_float(snap.sma20_distance_pct),
                    _safe_float(snap.sma50_distance_pct),
                    _safe_float(snap.atr_pct),
                    _safe_float(snap.obv_slope),
                    _safe_float(snap.sentiment_score),
                    _safe_float(snap.sentiment_velocity),
                    _safe_float(snap.sentiment_acceleration),
                    snap.mention_volume,
                    snap.source_count,
                    _safe_float(snap.svc_value),
                    _safe_float(snap.vwap_distance_pct),
                    _safe_float(snap.fvg_bias),
                    _safe_float(snap.volume_profile_poc_distance_pct),
                    _safe_float(snap.cumulative_delta_normalized),
                    _safe_float(snap.imbalance_ratio),
                    _safe_float(snap.insider_score),
                    _safe_float(snap.macro_regime_score),
                    _safe_float(snap.correlation_confidence),
                    _safe_float(snap.return_1d),
                    _safe_float(snap.return_5d),
                    _safe_float(snap.return_10d),
                    snap.direction_1d,
                    snap.direction_5d,
                    snap.direction_10d,
                ),
            )
            self._conn.commit()
