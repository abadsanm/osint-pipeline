"""
Stock Alpha signal scorer.

Combines FinBERT sentiment, SVC metric, technical indicators, and
cross-correlation confidence into a unified alpha signal.

Two modes:
  1. RULE-BASED (default): Weighted combination of indicators with
     configurable thresholds. No training data needed.
  2. ML-BASED (optional): LightGBM model trained on historical features.
     Requires labeled training data.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from stock_alpha.config import StockAlphaConfig
from stock_alpha.svc import SVCResult

log = logging.getLogger("stock_alpha.scorer")

# Directory where trained models are stored
_MODELS_DIR = Path(__file__).parent / "models"


@dataclass
class AlphaSignal:
    """A scored stock alpha signal ready for output."""
    ticker: str
    signal_score: float        # -1 (strong sell) to +1 (strong buy)
    signal_direction: str      # "bullish", "bearish", "neutral"
    confidence: float          # 0-1

    # Component scores
    sentiment_score: float
    svc_value: float
    technical_score: float
    correlation_confidence: float

    # Context
    contributing_signals: int
    top_sources: list[str]
    timestamp: datetime

    # Technical snapshot
    rsi: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_pctb: Optional[float] = None
    price: Optional[float] = None

    # Microstructure & order flow
    microstructure_score: Optional[float] = None
    order_flow_score: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "signal_score": round(self.signal_score, 4),
            "signal_direction": self.signal_direction,
            "confidence": round(self.confidence, 4),
            "sentiment_score": round(self.sentiment_score, 4),
            "svc_value": round(self.svc_value, 6),
            "technical_score": round(self.technical_score, 4),
            "correlation_confidence": round(self.correlation_confidence, 4),
            "microstructure_score": round(self.microstructure_score, 4) if self.microstructure_score is not None else None,
            "order_flow_score": round(self.order_flow_score, 4) if self.order_flow_score is not None else None,
            "contributing_signals": self.contributing_signals,
            "top_sources": self.top_sources,
            "timestamp": self.timestamp.isoformat(),
            "rsi": round(self.rsi, 2) if self.rsi is not None else None,
            "macd_hist": round(self.macd_hist, 4) if self.macd_hist is not None else None,
            "bb_pctb": round(self.bb_pctb, 4) if self.bb_pctb is not None else None,
            "price": round(self.price, 2) if self.price is not None else None,
        }


class RuleBasedScorer:
    """Combines sentiment, SVC, technicals, microstructure, and order flow
    into a weighted alpha signal.

    Base weights (when all components present):
      - Sentiment (FinBERT): 25%
      - SVC metric: 15%
      - Technical indicators: 20%
      - Microstructure: 15%
      - Order flow: 15%
      - Cross-correlation confidence: 10%

    When microstructure and/or order_flow are None (no market data for that
    ticker), their weight is redistributed proportionally to the other factors.
    """

    # Base weights — must sum to 1.0
    _BASE_WEIGHTS = {
        "sentiment": 0.25,
        "svc": 0.15,
        "technical": 0.20,
        "microstructure": 0.15,
        "order_flow": 0.15,
        "correlation": 0.10,
    }

    # Cached ML models (class-level so they persist across calls)
    _xgb_model = None
    _calibrator = None
    _models_loaded = False

    def score(self, *args, **kwargs) -> AlphaSignal:
        """Backward-compatible wrapper."""
        return self.score_rule_based(*args, **kwargs)

    def score_rule_based(
        self,
        ticker: str,
        sentiment_score: float,
        svc: Optional[SVCResult],
        technicals: Optional[pd.DataFrame],
        correlation_confidence: float,
        contributing_signals: int = 0,
        top_sources: Optional[list[str]] = None,
        microstructure_signals: Optional[dict] = None,
        order_flow_signals: Optional[dict] = None,
    ) -> AlphaSignal:
        """Compute a unified alpha signal from all components."""
        # Normalize SVC to -1..+1 range (tanh-like clamping)
        svc_value = svc.svc_value if svc else 0.0
        svc_normalized = max(-1.0, min(1.0, svc_value * 10))  # Scale up small values

        # Compute technical score
        tech_score, rsi, macd_hist, bb_pctb, price = self._score_technicals(technicals)

        # Compute microstructure score (-1..+1)
        micro_score = self._score_microstructure(microstructure_signals)

        # Compute order flow score (-1..+1)
        flow_score = self._score_order_flow(order_flow_signals)

        # Build active weights — exclude components with no data
        active = {
            "sentiment": sentiment_score,
            "svc": svc_normalized,
            "technical": tech_score,
            "correlation": correlation_confidence * 2 - 1,  # Map 0-1 to -1..+1
        }
        if micro_score is not None:
            active["microstructure"] = micro_score
        if flow_score is not None:
            active["order_flow"] = flow_score

        # Redistribute missing weights proportionally
        weights = self._redistribute_weights(set(active.keys()))

        # Weighted combination
        raw_score = sum(weights[k] * active[k] for k in active)

        # Clamp to -1..+1
        signal_score = max(-1.0, min(1.0, raw_score))

        # Determine direction
        if signal_score > 0.15:
            direction = "bullish"
        elif signal_score < -0.15:
            direction = "bearish"
        else:
            direction = "neutral"

        # Confidence = average of component confidences
        components_present = sum([
            abs(sentiment_score) > 0.01,
            svc is not None,
            technicals is not None and not technicals.empty,
            correlation_confidence > 0,
            micro_score is not None,
            flow_score is not None,
        ])
        confidence = min(1.0, (correlation_confidence + abs(signal_score)) / 2)
        if components_present < 2:
            confidence *= 0.5  # Low confidence with few components

        return AlphaSignal(
            ticker=ticker,
            signal_score=signal_score,
            signal_direction=direction,
            confidence=confidence,
            sentiment_score=sentiment_score,
            svc_value=svc_value,
            technical_score=tech_score,
            correlation_confidence=correlation_confidence,
            contributing_signals=contributing_signals,
            top_sources=top_sources or [],
            timestamp=datetime.now(timezone.utc),
            rsi=rsi,
            macd_hist=macd_hist,
            bb_pctb=bb_pctb,
            price=price,
            microstructure_score=micro_score,
            order_flow_score=flow_score,
        )

    def _load_models(self):
        """Load XGBoost and calibrator models from disk (once)."""
        if RuleBasedScorer._models_loaded:
            return
        RuleBasedScorer._models_loaded = True

        xgb_path = _MODELS_DIR / "scorer_latest.joblib"
        cal_path = _MODELS_DIR / "calibrator_latest.joblib"

        try:
            import joblib

            if xgb_path.exists():
                RuleBasedScorer._xgb_model = joblib.load(xgb_path)
                log.info("Loaded XGBoost scorer model from %s", xgb_path)
            else:
                log.debug("No XGBoost model found at %s — using rule-based fallback", xgb_path)

            if cal_path.exists():
                RuleBasedScorer._calibrator = joblib.load(cal_path)
                log.info("Loaded calibrator model from %s", cal_path)
            else:
                log.debug("No calibrator model found at %s", cal_path)
        except Exception as exc:
            log.warning("Failed to load ML models: %s", exc)

    def score_ensemble(
        self,
        ticker: str,
        sentiment_score: float,
        svc: Optional[SVCResult],
        technicals: Optional[pd.DataFrame],
        correlation_confidence: float,
        contributing_signals: int = 0,
        top_sources: Optional[list[str]] = None,
        microstructure_signals: Optional[dict] = None,
        order_flow_signals: Optional[dict] = None,
    ) -> tuple[AlphaSignal, dict]:
        """Score using an XGBoost ensemble if available, else fall back to rules.

        Returns a tuple of (AlphaSignal, feature_vector_dict).
        """
        # Normalize SVC
        svc_value = svc.svc_value if svc else 0.0
        svc_normalized = max(-1.0, min(1.0, svc_value * 10))

        # Technical scores
        tech_score, rsi, macd_hist, bb_pctb, price = self._score_technicals(technicals)

        # SMA20 distance
        sma20_distance = 0.0
        if technicals is not None and not technicals.empty:
            latest = technicals.iloc[-1]
            sma_20 = latest.get("sma_20")
            px = latest.get("close")
            if sma_20 is not None and px is not None and not pd.isna(sma_20) and sma_20 != 0:
                sma20_distance = (px - sma_20) / sma_20

        # Microstructure & order flow scores
        micro_score = self._score_microstructure(microstructure_signals)
        flow_score = self._score_order_flow(order_flow_signals)

        # Build feature vector
        feature_vector: dict = {
            "sentiment_score": sentiment_score,
            "svc_normalized": svc_normalized,
            "tech_score": tech_score,
            "rsi": rsi if rsi is not None and not pd.isna(rsi) else 0.0,
            "macd_hist": macd_hist if macd_hist is not None and not pd.isna(macd_hist) else 0.0,
            "bb_pctb": bb_pctb if bb_pctb is not None and not pd.isna(bb_pctb) else 0.0,
            "sma20_distance": sma20_distance,
            "obv_slope": 0.0,  # Not directly available; placeholder
            "correlation_confidence": correlation_confidence,
            "microstructure_score": micro_score if micro_score is not None else 0.0,
            "order_flow_score": flow_score if flow_score is not None else 0.0,
        }

        # Attempt to load ML models
        self._load_models()

        if RuleBasedScorer._xgb_model is not None:
            try:
                import numpy as np

                # Build ordered feature array matching training column order
                feature_names = [
                    "sentiment_score", "svc_normalized", "tech_score",
                    "rsi", "macd_hist", "bb_pctb", "sma20_distance",
                    "obv_slope", "correlation_confidence",
                    "microstructure_score", "order_flow_score",
                ]
                X = np.array([[feature_vector[f] for f in feature_names]])

                model = RuleBasedScorer._xgb_model
                prediction = model.predict(X)[0]  # class label
                probabilities = model.predict_proba(X)[0]
                max_prob = float(probabilities.max())

                # Map predicted class to signal score
                # Convention: 0 = bearish, 1 = neutral, 2 = bullish
                classes = list(model.classes_)
                if prediction == 2 or (isinstance(prediction, str) and prediction == "bullish"):
                    signal_score = max_prob
                elif prediction == 0 or (isinstance(prediction, str) and prediction == "bearish"):
                    signal_score = -max_prob
                else:
                    signal_score = 0.0

                confidence = max_prob

                # Apply calibration if available
                if RuleBasedScorer._calibrator is not None:
                    try:
                        cal_probs = RuleBasedScorer._calibrator.predict_proba(X)[0]
                        confidence = float(cal_probs.max())
                    except Exception:
                        pass  # Keep uncalibrated confidence

                signal_score = max(-1.0, min(1.0, signal_score))

                if signal_score > 0.15:
                    direction = "bullish"
                elif signal_score < -0.15:
                    direction = "bearish"
                else:
                    direction = "neutral"

                alpha = AlphaSignal(
                    ticker=ticker,
                    signal_score=signal_score,
                    signal_direction=direction,
                    confidence=min(1.0, confidence),
                    sentiment_score=sentiment_score,
                    svc_value=svc_value,
                    technical_score=tech_score,
                    correlation_confidence=correlation_confidence,
                    contributing_signals=contributing_signals,
                    top_sources=top_sources or [],
                    timestamp=datetime.now(timezone.utc),
                    rsi=rsi,
                    macd_hist=macd_hist,
                    bb_pctb=bb_pctb,
                    price=price,
                    microstructure_score=micro_score,
                    order_flow_score=flow_score,
                )
                return alpha, feature_vector

            except Exception as exc:
                log.warning("XGBoost prediction failed, falling back to rule-based: %s", exc)

        # Fallback to rule-based scoring
        alpha = self.score_rule_based(
            ticker=ticker,
            sentiment_score=sentiment_score,
            svc=svc,
            technicals=technicals,
            correlation_confidence=correlation_confidence,
            contributing_signals=contributing_signals,
            top_sources=top_sources,
            microstructure_signals=microstructure_signals,
            order_flow_signals=order_flow_signals,
        )
        return alpha, feature_vector

    @classmethod
    def _redistribute_weights(cls, active_keys: set[str]) -> dict[str, float]:
        """Return weights for active components, redistributing missing weight."""
        active_total = sum(
            cls._BASE_WEIGHTS[k] for k in active_keys if k in cls._BASE_WEIGHTS
        )
        if active_total <= 0:
            return {k: 0.0 for k in active_keys}
        scale = 1.0 / active_total
        return {k: cls._BASE_WEIGHTS[k] * scale for k in active_keys}

    @staticmethod
    def _score_microstructure(signals: Optional[dict]) -> Optional[float]:
        """Derive a -1..+1 score from microstructure indicators.

        Expects keys: anchored_vwap_position (-1..+1 where price is vs VWAP),
        volume_profile (dict with poc_price), fvg_bias (-1..+1).
        """
        if signals is None:
            return None

        components = []

        # VWAP position: positive = price above VWAP (bullish)
        vwap_pos = signals.get("anchored_vwap_position")
        if vwap_pos is not None:
            components.append(max(-1.0, min(1.0, vwap_pos)))

        # FVG bias: net direction of recent fair value gaps
        fvg_bias = signals.get("fvg_bias")
        if fvg_bias is not None:
            components.append(max(-1.0, min(1.0, fvg_bias)))

        # Volume profile: price relative to POC
        vp_position = signals.get("volume_profile_position")
        if vp_position is not None:
            components.append(max(-1.0, min(1.0, vp_position * 0.5)))

        if not components:
            return None
        return sum(components) / len(components)

    @staticmethod
    def _score_order_flow(signals: Optional[dict]) -> Optional[float]:
        """Derive a -1..+1 score from order flow indicators.

        Expects keys: imbalance_ratio, cumulative_delta_normalized (-1..+1),
        sweep_bias (-1..+1).
        """
        if signals is None:
            return None

        components = []

        # Delta direction: positive cumulative delta = buying pressure
        delta_norm = signals.get("cumulative_delta_normalized")
        if delta_norm is not None:
            components.append(max(-1.0, min(1.0, delta_norm)))

        # Imbalance ratio scaled by delta direction
        imbalance = signals.get("imbalance_ratio", 0.0)
        delta_sign = 1.0 if signals.get("cumulative_delta", 0) >= 0 else -1.0
        if imbalance > 0.1:
            components.append(delta_sign * min(1.0, imbalance * 2))

        # Sweep bias: net direction of liquidity sweeps
        sweep_bias = signals.get("sweep_bias")
        if sweep_bias is not None:
            components.append(max(-1.0, min(1.0, sweep_bias)))

        if not components:
            return None
        return sum(components) / len(components)

    @staticmethod
    def _score_technicals(
        df: Optional[pd.DataFrame],
    ) -> tuple[float, Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Derive a -1..+1 technical score from indicators.

        Returns (score, rsi, macd_hist, bb_pctb, price).
        """
        if df is None or df.empty:
            return 0.0, None, None, None, None

        latest = df.iloc[-1]
        signals = []

        price = latest.get("close")
        rsi = latest.get("rsi")
        macd_hist = latest.get("macd_hist")
        bb_pctb = latest.get("bb_pctb")

        # RSI signal
        if rsi is not None and not pd.isna(rsi):
            if rsi < 30:
                signals.append(0.5)    # Oversold → bullish
            elif rsi > 70:
                signals.append(-0.5)   # Overbought → bearish
            else:
                signals.append(0.0)

        # MACD histogram signal
        if macd_hist is not None and not pd.isna(macd_hist):
            # Positive histogram = bullish momentum
            signals.append(max(-1.0, min(1.0, macd_hist * 5)))

        # Bollinger Band %B signal
        if bb_pctb is not None and not pd.isna(bb_pctb):
            if bb_pctb < 0.2:
                signals.append(0.3)    # Near lower band → bounce potential
            elif bb_pctb > 0.8:
                signals.append(-0.3)   # Near upper band → pullback risk
            else:
                signals.append(0.0)

        # SMA trend (price vs SMA20)
        sma_20 = latest.get("sma_20")
        if sma_20 is not None and price is not None and not pd.isna(sma_20):
            if price > sma_20:
                signals.append(0.2)
            else:
                signals.append(-0.2)

        if not signals:
            return 0.0, rsi, macd_hist, bb_pctb, price

        tech_score = sum(signals) / len(signals)
        return max(-1.0, min(1.0, tech_score)), rsi, macd_hist, bb_pctb, price
