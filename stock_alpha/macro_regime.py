"""
Macroeconomic Regime Classifier.

Classifies the current macroeconomic regime from FRED data ingested via
the finance.macro.fred Kafka topic. Market behaviour changes dramatically
by regime — what works in risk-on doesn't work in risk-off.

Regimes:
  - goldilocks   (+0.8) — low unemployment, moderate CPI, strong sentiment
  - risk_on      (+0.5) — default / broadly favourable conditions
  - easing       (+0.3) — fed funds falling, positive yield curve
  - tightening   (-0.3) — fed funds rising, CPI rising
  - risk_off     (-0.6) — unemployment rising or sentiment collapsing
  - stagflation  (-0.9) — inverted curve + rising unemployment + high CPI
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

log = logging.getLogger("stock_alpha.macro_regime")

# ---------------------------------------------------------------------------
# Regime score mapping
# ---------------------------------------------------------------------------

REGIME_SCORES: dict[str, float] = {
    "goldilocks": 0.8,
    "risk_on": 0.5,
    "easing": 0.3,
    "tightening": -0.3,
    "risk_off": -0.6,
    "stagflation": -0.9,
}

# Thresholds per indicator (different series have different scales)
_DIRECTION_THRESHOLDS = {
    "T10Y2Y": 0.15,      # yield curve spread (bps)
    "FEDFUNDS": 0.15,     # fed funds rate (bps)
    "DGS10": 0.20,        # 10yr treasury (bps)
    "UNRATE": 0.3,        # unemployment rate (pct pts)
    "CPIAUCSL": 1.0,      # CPI YoY can move 100+ bps in 3 months
    "UMCSENT": 3.0,        # consumer sentiment index (points)
}
_DIRECTION_THRESHOLD = 0.15  # default fallback


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class MacroDataPoint:
    """A single FRED observation for a macro series."""

    series_id: str
    observation_date: datetime
    value: float
    prior_value: Optional[float] = None
    pct_change: Optional[float] = None


@dataclass
class MacroRegime:
    """Result of macro-regime classification."""

    regime: str
    confidence: float
    regime_score: float
    indicators: dict[str, object]
    description: str


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class MacroRegimeDetector:
    """Stateful detector that accumulates FRED observations and classifies
    the current macroeconomic regime on demand.

    Keeps the last 12 months of observations per series via bounded deques.
    """

    _MAX_HISTORY = 365  # daily observations, ~12 months

    def __init__(self) -> None:
        self._store: dict[str, deque[MacroDataPoint]] = {}

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def ingest(self, data: dict) -> None:
        """Parse a Kafka message from ``finance.macro.fred`` and store.

        Expected message shape::

            {
                "metadata": {
                    "series_id": "DGS10",
                    "value": 4.35,
                    "observation_date": "2026-03-14",
                    "prior_value": 4.28,
                    "pct_change": 0.0163
                },
                ...
            }
        """
        meta = data.get("metadata")
        if not meta:
            log.warning("Ignoring FRED message with no metadata: %s", data.get("source_id", "?"))
            return

        series_id = meta.get("series_id")
        raw_value = meta.get("value")
        if series_id is None or raw_value is None:
            log.warning("Missing series_id or value in FRED metadata")
            return

        obs_date_raw = meta.get("observation_date")
        if isinstance(obs_date_raw, str):
            try:
                obs_date = datetime.fromisoformat(obs_date_raw).replace(tzinfo=timezone.utc)
            except ValueError:
                obs_date = datetime.now(timezone.utc)
        elif isinstance(obs_date_raw, datetime):
            obs_date = obs_date_raw if obs_date_raw.tzinfo else obs_date_raw.replace(tzinfo=timezone.utc)
        else:
            obs_date = datetime.now(timezone.utc)

        dp = MacroDataPoint(
            series_id=series_id,
            observation_date=obs_date,
            value=float(raw_value),
            prior_value=float(meta["prior_value"]) if meta.get("prior_value") is not None else None,
            pct_change=float(meta["pct_change"]) if meta.get("pct_change") is not None else None,
        )

        if series_id not in self._store:
            self._store[series_id] = deque(maxlen=self._MAX_HISTORY)
        self._store[series_id].append(dp)
        log.debug("Ingested %s = %.4f (%s)", series_id, dp.value, obs_date.date())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_latest(self, series_id: str) -> Optional[MacroDataPoint]:
        """Return the most recent observation for *series_id*, or ``None``."""
        dq = self._store.get(series_id)
        if not dq:
            return None
        return dq[-1]

    def _get_direction(self, series_id: str) -> str:
        """Determine whether *series_id* is ``"rising"``, ``"falling"``, or
        ``"stable"`` by comparing the latest value to the value from roughly
        3 months ago.
        """
        dq = self._store.get(series_id)
        if not dq or len(dq) < 2:
            return "stable"

        latest = dq[-1]

        # Walk backwards to find a data point ~90 days ago
        target = latest.observation_date - timedelta(days=90)
        prior: Optional[MacroDataPoint] = None
        for dp in dq:
            if dp.observation_date <= target:
                prior = dp
            else:
                break

        # If we didn't find anything before the target, use the oldest point
        if prior is None:
            prior = dq[0]

        # Same point — can't determine direction
        if prior is latest:
            return "stable"

        diff = latest.value - prior.value
        threshold = _DIRECTION_THRESHOLDS.get(series_id, _DIRECTION_THRESHOLD)
        if abs(diff) <= threshold:
            return "stable"
        return "rising" if diff > 0 else "falling"

    def _value_or(self, series_id: str, default: float = 0.0) -> float:
        """Return the latest value for a series or *default*."""
        dp = self._get_latest(series_id)
        return dp.value if dp else default

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self) -> Optional[MacroRegime]:
        """Classify the current macroeconomic regime.

        Returns ``None`` if fewer than 2 key series have data (not enough
        information to make a meaningful call).

        Classification rules (checked in order):

        1. **Stagflation** — inverted yield curve, rising unemployment,
           high CPI (> 3 % YoY change).
        2. **Easing / risk_on** — fed funds falling, positive yield curve,
           stable or falling unemployment.
        3. **Tightening** — fed funds rising and CPI rising.
        4. **Goldilocks** — fed funds stable, low unemployment, moderate
           CPI, high consumer sentiment.
        5. **Risk_off** — unemployment rising or sentiment falling sharply.
        6. **Default** — risk_on with low confidence.
        """
        available = {sid for sid in self._store if len(self._store[sid]) > 0}
        key_series = {"T10Y2Y", "FEDFUNDS", "UNRATE", "CPIAUCSL", "UMCSENT", "DGS10"}
        if len(available & key_series) < 2:
            log.debug("Not enough macro data to classify (have %s)", available & key_series)
            return None

        # Gather indicator readings
        t10y2y = self._value_or("T10Y2Y", default=1.0)  # positive = normal curve
        fedfunds_dir = self._get_direction("FEDFUNDS")
        dgs10_dir = self._get_direction("DGS10")
        unrate_dir = self._get_direction("UNRATE")
        cpi_dir = self._get_direction("CPIAUCSL")
        umcsent_dir = self._get_direction("UMCSENT")

        unrate = self._value_or("UNRATE", default=4.0)
        cpi_pct = self._get_latest("CPIAUCSL")
        cpi_yoy = cpi_pct.pct_change if (cpi_pct and cpi_pct.pct_change is not None) else 0.0
        umcsent = self._value_or("UMCSENT", default=70.0)

        indicators: dict[str, object] = {
            "T10Y2Y": t10y2y,
            "FEDFUNDS_dir": fedfunds_dir,
            "DGS10_dir": dgs10_dir,
            "UNRATE": unrate,
            "UNRATE_dir": unrate_dir,
            "CPI_YoY": cpi_yoy,
            "CPI_dir": cpi_dir,
            "UMCSENT": umcsent,
            "UMCSENT_dir": umcsent_dir,
        }

        # --- Rule 1: Stagflation ---
        if t10y2y < 0 and unrate_dir == "rising" and cpi_yoy > 3.0:
            return MacroRegime(
                regime="stagflation",
                confidence=0.8,
                regime_score=REGIME_SCORES["stagflation"],
                indicators=indicators,
                description=(
                    f"Stagflation regime: yield curve inverted ({t10y2y:+.2f}), "
                    f"unemployment rising ({unrate:.1f}%), CPI YoY {cpi_yoy:.1f}%"
                ),
            )

        # --- Rule 2: Easing / risk_on ---
        if fedfunds_dir == "falling" and t10y2y >= 0 and unrate_dir in ("stable", "falling"):
            return MacroRegime(
                regime="easing",
                confidence=0.7,
                regime_score=REGIME_SCORES["easing"],
                indicators=indicators,
                description=(
                    f"Easing regime: rates falling, yield curve positive ({t10y2y:+.2f}), "
                    f"unemployment {unrate_dir} ({unrate:.1f}%)"
                ),
            )

        # --- Rule 3: Tightening ---
        if fedfunds_dir == "rising" and cpi_dir == "rising":
            return MacroRegime(
                regime="tightening",
                confidence=0.7,
                regime_score=REGIME_SCORES["tightening"],
                indicators=indicators,
                description=(
                    f"Tightening regime: fed funds rising, CPI rising (YoY {cpi_yoy:.1f}%)"
                ),
            )

        # --- Rule 4: Goldilocks ---
        if (
            fedfunds_dir == "stable"
            and unrate < 5.0
            and 0 <= cpi_yoy <= 3.0
            and umcsent > 75.0
        ):
            return MacroRegime(
                regime="goldilocks",
                confidence=0.6,
                regime_score=REGIME_SCORES["goldilocks"],
                indicators=indicators,
                description=(
                    f"Goldilocks regime: rates stable, unemployment {unrate:.1f}%, "
                    f"CPI YoY {cpi_yoy:.1f}%, sentiment {umcsent:.0f}"
                ),
            )

        # --- Rule 5: Risk-off ---
        if unrate_dir == "rising" or umcsent_dir == "falling":
            return MacroRegime(
                regime="risk_off",
                confidence=0.6,
                regime_score=REGIME_SCORES["risk_off"],
                indicators=indicators,
                description=(
                    f"Risk-off regime: unemployment {unrate_dir} ({unrate:.1f}%), "
                    f"consumer sentiment {umcsent_dir} ({umcsent:.0f})"
                ),
            )

        # --- Rule 6: Default ---
        return MacroRegime(
            regime="risk_on",
            confidence=0.3,
            regime_score=REGIME_SCORES["risk_on"],
            indicators=indicators,
            description=(
                f"Risk-on regime (default): no strong macro signal detected. "
                f"Yield curve {t10y2y:+.2f}, unemployment {unrate:.1f}%, "
                f"sentiment {umcsent:.0f}"
            ),
        )
