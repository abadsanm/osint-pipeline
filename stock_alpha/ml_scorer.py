"""
LightGBM-based ML scoring model for the Stock Alpha engine.

Trains on historical feature snapshots and predicts forward returns
(1d/5d/10d). Designed to blend with the rule-based scorer for a
hybrid approach: rule-based intuition + learned feature weights.

Walk-forward validation ensures the model never trains on future data.

Enhanced with multi-horizon ensembling, probability calibration,
rolling retrain, and feature drift detection.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

try:
    from lightgbm import LGBMClassifier, LGBMRegressor
    import lightgbm as lgb
except ImportError:
    LGBMClassifier = None  # type: ignore[assignment,misc]
    LGBMRegressor = None  # type: ignore[assignment,misc]
    lgb = None  # type: ignore[assignment]

try:
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.metrics import accuracy_score, precision_score, recall_score
except ImportError:
    CalibratedClassifierCV = None  # type: ignore[assignment,misc]
    accuracy_score = None  # type: ignore[assignment]
    precision_score = None  # type: ignore[assignment]
    recall_score = None  # type: ignore[assignment]

log = logging.getLogger("stock_alpha.ml_scorer")

# ---------------------------------------------------------------------------
# Direction encoding
# ---------------------------------------------------------------------------
DIRECTION_MAP = {"down": 0, "flat": 1, "up": 2}
DIRECTION_INV = {v: k for k, v in DIRECTION_MAP.items()}

# ---------------------------------------------------------------------------
# LightGBM hyper-parameters — base config
# ---------------------------------------------------------------------------
LGBM_PARAMS_CLASSIFIER: dict[str, Any] = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "verbose": -1,
    "n_estimators": 200,
    "min_child_samples": 10,
}

# Ensemble variant: deeper trees
LGBM_PARAMS_DEEP: dict[str, Any] = {
    **LGBM_PARAMS_CLASSIFIER,
    "num_leaves": 63,
    "n_estimators": 300,
}

# Ensemble variant: more regularization
LGBM_PARAMS_REGULARIZED: dict[str, Any] = {
    **LGBM_PARAMS_CLASSIFIER,
    "lambda_l1": 0.5,
    "lambda_l2": 0.5,
    "num_leaves": 15,
}

# All ensemble configurations
ENSEMBLE_CONFIGS: list[tuple[str, dict[str, Any]]] = [
    ("base", LGBM_PARAMS_CLASSIFIER),
    ("deep", LGBM_PARAMS_DEEP),
    ("regularized", LGBM_PARAMS_REGULARIZED),
]

LGBM_PARAMS_REGRESSOR: dict[str, Any] = {
    "objective": "regression",
    "metric": "rmse",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "verbose": -1,
    "n_estimators": 200,
    "min_child_samples": 10,
}

# Sentinel value for missing features (LightGBM handles this natively)
_MISSING = -999.0

# Minimum labeled samples required to train
_MIN_SAMPLES = 50

# Number of bins for PSI computation
_PSI_BINS = 10

# PSI thresholds
_PSI_NONE = 0.1
_PSI_MODERATE = 0.25


def _compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = _PSI_BINS) -> float:
    """Compute Population Stability Index between two distributions.

    Parameters
    ----------
    expected:
        Reference distribution (training data feature values).
    actual:
        Current distribution (recent data feature values).
    bins:
        Number of bins for discretization.

    Returns
    -------
    float
        PSI value. < 0.1 = no drift, 0.1-0.25 = moderate, > 0.25 = significant.
    """
    eps = 1e-6

    # Create bins from the expected distribution
    breakpoints = np.linspace(
        min(np.min(expected), np.min(actual)) - eps,
        max(np.max(expected), np.max(actual)) + eps,
        bins + 1,
    )

    expected_counts = np.histogram(expected, bins=breakpoints)[0].astype(float)
    actual_counts = np.histogram(actual, bins=breakpoints)[0].astype(float)

    # Convert to percentages
    expected_pct = expected_counts / (expected_counts.sum() + eps)
    actual_pct = actual_counts / (actual_counts.sum() + eps)

    # Avoid log(0) by adding epsilon
    expected_pct = np.clip(expected_pct, eps, None)
    actual_pct = np.clip(actual_pct, eps, None)

    psi = float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))
    return psi


class MLScorer:
    """LightGBM ensemble model trained on historical feature snapshots.

    Predicts forward returns (1d/5d/10d) from the same features used
    by the rule-based scorer, but with learned weights.

    Enhanced with:
    - Multi-horizon ensemble (3 models per horizon, weighted by validation accuracy)
    - Probability calibration (isotonic regression)
    - Rolling retrain (staleness check)
    - Feature drift detection (Population Stability Index)
    """

    def __init__(self, model_dir: str = "data/models") -> None:
        self._model_1d: Optional[LGBMClassifier] = None
        self._model_5d: Optional[LGBMClassifier] = None
        self._regressor_1d: Optional[LGBMRegressor] = None
        self._feature_names: list[str] = []
        self._model_dir = model_dir
        self._is_trained = False
        self._training_stats: dict[str, Any] = {}

        # Ensemble: list of (model_or_calibrated, weight) per horizon
        self._ensemble_1d: list[tuple[Any, float]] = []
        self._ensemble_5d: list[tuple[Any, float]] = []

        # Feature drift baseline: {feature_name: np.ndarray of training values}
        self._drift_baseline: dict[str, np.ndarray] = {}

        # Try to restore persisted models on startup
        if self.load_models():
            log.info("Loaded pre-trained ML models from %s", self._model_dir)
        else:
            log.info("No pre-trained ML models found; call train() to create them")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_trained(self) -> bool:
        """Whether the models are trained (or loaded) and ready for inference."""
        return self._is_trained

    @property
    def training_stats(self) -> dict[str, Any]:
        """Statistics captured during the last training run."""
        return self._training_stats

    # ------------------------------------------------------------------
    # Internal: calibration
    # ------------------------------------------------------------------

    def _calibrate_model(
        self,
        model: LGBMClassifier,
        X_val: np.ndarray,
        y_val: np.ndarray,
        label: str,
    ) -> Any:
        """Wrap a trained classifier with isotonic probability calibration.

        Parameters
        ----------
        model:
            Already-fitted LGBMClassifier.
        X_val:
            Validation feature matrix.
        y_val:
            Validation labels.
        label:
            Human-readable label for logging.

        Returns
        -------
        CalibratedClassifierCV or the original model if calibration fails.
        """
        if CalibratedClassifierCV is None:
            log.warning("sklearn not available; skipping calibration for %s", label)
            return model

        # Need enough samples for isotonic calibration (at least ~15 per class)
        unique_classes, counts = np.unique(y_val, return_counts=True)
        if len(unique_classes) < 2 or counts.min() < 5:
            log.warning(
                "Too few samples for calibration of %s (classes=%s, counts=%s); "
                "using uncalibrated model",
                label,
                unique_classes.tolist(),
                counts.tolist(),
            )
            return model

        try:
            calibrated = CalibratedClassifierCV(
                model, method="isotonic", cv="prefit"
            )
            calibrated.fit(X_val, y_val)
            log.info("Calibrated probabilities for %s using isotonic regression", label)
            return calibrated
        except Exception:
            log.exception("Calibration failed for %s; using uncalibrated model", label)
            return model

    @staticmethod
    def _calibration_error(
        model: Any, X_val: np.ndarray, y_val: np.ndarray, n_bins: int = 10
    ) -> float:
        """Compute mean absolute calibration error.

        For each probability bin, measures |mean predicted probability - actual frequency|.
        """
        try:
            proba = model.predict_proba(X_val)
            # Use the max probability as the confidence
            confidences = np.max(proba, axis=1)
            predictions = np.argmax(proba, axis=1)
            correct = (predictions == y_val).astype(float)

            bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
            total_error = 0.0
            total_samples = 0

            for i in range(n_bins):
                lo, hi = bin_boundaries[i], bin_boundaries[i + 1]
                mask = (confidences > lo) & (confidences <= hi)
                n_in_bin = mask.sum()
                if n_in_bin == 0:
                    continue
                avg_confidence = float(confidences[mask].mean())
                avg_accuracy = float(correct[mask].mean())
                total_error += abs(avg_confidence - avg_accuracy) * n_in_bin
                total_samples += n_in_bin

            return total_error / max(total_samples, 1)
        except Exception:
            return -1.0

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train(self, feature_store: Any) -> dict[str, Any]:
        """Train ensemble models on labeled data from the feature store.

        Uses walk-forward (temporal) validation to avoid look-ahead bias.
        Trains 3 model variants per horizon and calibrates probabilities.

        Parameters
        ----------
        feature_store:
            Any object that exposes ``get_feature_matrix()`` returning a tuple
            ``(X: np.ndarray, y_direction_1d: np.ndarray, y_direction_5d: np.ndarray,
              y_return_1d: np.ndarray, feature_names: list[str],
              timestamps: np.ndarray, tickers: list[str])``.

        Returns
        -------
        dict
            Training statistics including accuracy, feature importance, and
            sample counts.  Also stored on ``self.training_stats``.
        """
        if LGBMClassifier is None:
            msg = "LightGBM is not installed; cannot train ML models"
            log.error(msg)
            return {"error": msg}

        log.info("Starting ML ensemble model training...")

        # 1. Fetch labelled feature matrix
        (
            X,
            y_dir_1d,
            y_dir_5d,
            y_ret_1d,
            feature_names,
            timestamps,
            tickers,
        ) = feature_store.get_feature_matrix()

        n_samples = len(X)
        if n_samples < _MIN_SAMPLES:
            msg = (
                f"Not enough labeled samples to train: {n_samples} < {_MIN_SAMPLES}. "
                "Collect more data before training."
            )
            log.warning(msg)
            return {"error": msg, "samples_total": n_samples}

        self._feature_names = list(feature_names)
        log.info(
            "Feature matrix: %d samples, %d features (%s)",
            n_samples,
            len(self._feature_names),
            ", ".join(self._feature_names[:10])
            + ("..." if len(self._feature_names) > 10 else ""),
        )

        # 2. Replace NaN with sentinel
        X = np.where(np.isnan(X), _MISSING, X)

        # 3. Temporal split: first 80% train, last 20% validation
        sort_idx = np.argsort(timestamps)
        X = X[sort_idx]
        y_dir_1d = y_dir_1d[sort_idx]
        y_dir_5d = y_dir_5d[sort_idx]
        y_ret_1d = y_ret_1d[sort_idx]
        sorted_timestamps = timestamps[sort_idx]
        sorted_tickers = [tickers[i] for i in sort_idx]

        split = int(n_samples * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_dir_1d_train, y_dir_1d_val = y_dir_1d[:split], y_dir_1d[split:]
        y_dir_5d_train, y_dir_5d_val = y_dir_5d[:split], y_dir_5d[split:]
        y_ret_1d_train, y_ret_1d_val = y_ret_1d[:split], y_ret_1d[split:]

        log.info("Train samples: %d | Validation samples: %d", split, n_samples - split)

        # 4. Store drift baseline from training data
        self._drift_baseline = {}
        for i, fname in enumerate(self._feature_names):
            col = X_train[:, i]
            # Exclude sentinel values from drift baseline
            valid = col[col != _MISSING]
            if len(valid) > 0:
                self._drift_baseline[fname] = valid.copy()

        # 5. Train ensemble for 1d direction
        self._ensemble_1d = []
        accuracy_1d_per_model: list[float] = []

        for config_name, params in ENSEMBLE_CONFIGS:
            try:
                model = LGBMClassifier(**params)
                model.fit(X_train, y_dir_1d_train)
                pred = model.predict(X_val)
                acc = float(accuracy_score(y_dir_1d_val, pred))
                accuracy_1d_per_model.append(acc)

                # Calibrate probabilities
                calibrated = self._calibrate_model(
                    model, X_val, y_dir_1d_val, f"1d-{config_name}"
                )
                self._ensemble_1d.append((calibrated, acc))
                log.info("1d ensemble [%s]: accuracy=%.4f", config_name, acc)
            except Exception:
                log.exception("Failed to train 1d ensemble model [%s]; skipping", config_name)
                accuracy_1d_per_model.append(0.0)

        if not self._ensemble_1d:
            msg = "All 1d ensemble models failed to train"
            log.error(msg)
            return {"error": msg, "samples_total": n_samples}

        # Keep first model as the legacy _model_1d for feature importance
        first_1d = self._ensemble_1d[0][0]
        self._model_1d = (
            first_1d.estimator if hasattr(first_1d, "estimator") else first_1d
        )

        # 6. Train ensemble for 5d direction
        self._ensemble_5d = []
        accuracy_5d_per_model: list[float] = []

        for config_name, params in ENSEMBLE_CONFIGS:
            try:
                model = LGBMClassifier(**params)
                model.fit(X_train, y_dir_5d_train)
                pred = model.predict(X_val)
                acc = float(accuracy_score(y_dir_5d_val, pred))
                accuracy_5d_per_model.append(acc)

                calibrated = self._calibrate_model(
                    model, X_val, y_dir_5d_val, f"5d-{config_name}"
                )
                self._ensemble_5d.append((calibrated, acc))
                log.info("5d ensemble [%s]: accuracy=%.4f", config_name, acc)
            except Exception:
                log.exception("Failed to train 5d ensemble model [%s]; skipping", config_name)
                accuracy_5d_per_model.append(0.0)

        if not self._ensemble_5d:
            msg = "All 5d ensemble models failed to train"
            log.error(msg)
            return {"error": msg, "samples_total": n_samples}

        self._model_5d = None  # ensemble handles 5d now

        # 7. Train 1-day return regressor (single model, no ensemble needed)
        self._regressor_1d = LGBMRegressor(**LGBM_PARAMS_REGRESSOR)
        self._regressor_1d.fit(X_train, y_ret_1d_train)

        # 8. Evaluate ensemble accuracy on validation set
        pred_ret = self._regressor_1d.predict(X_val)
        rmse_1d = float(np.sqrt(np.mean((y_ret_1d_val - pred_ret) ** 2)))

        # Ensemble predictions for accuracy
        ens_pred_1d = self._ensemble_predict_classes(self._ensemble_1d, X_val)
        ens_pred_5d = self._ensemble_predict_classes(self._ensemble_5d, X_val)

        acc_1d = float(accuracy_score(y_dir_1d_val, ens_pred_1d))
        acc_5d = float(accuracy_score(y_dir_5d_val, ens_pred_5d))

        prec_1d = float(
            precision_score(y_dir_1d_val, ens_pred_1d, average="weighted", zero_division=0)
        )
        recall_1d = float(
            recall_score(y_dir_1d_val, ens_pred_1d, average="weighted", zero_division=0)
        )

        # Calibration error (from the first 1d calibrated model)
        cal_error = self._calibration_error(self._ensemble_1d[0][0], X_val, y_dir_1d_val)

        log.info(
            "Ensemble validation — 1d accuracy: %.3f, 5d accuracy: %.3f, "
            "1d RMSE: %.4f, calibration error: %.4f",
            acc_1d,
            acc_5d,
            rmse_1d,
            cal_error,
        )

        # 9. Feature importance (from base 1d direction model)
        importance = self.get_feature_importance()

        # 10. Compute data date range
        try:
            ts_min = str(datetime.fromtimestamp(float(np.min(sorted_timestamps)), tz=timezone.utc).date())
            ts_max = str(datetime.fromtimestamp(float(np.max(sorted_timestamps)), tz=timezone.utc).date())
        except Exception:
            ts_min = str(np.min(sorted_timestamps))
            ts_max = str(np.max(sorted_timestamps))

        # 11. Persist
        self._is_trained = True
        unique_tickers = sorted(set(sorted_tickers))
        self._training_stats = {
            "samples_total": n_samples,
            "samples_train": split,
            "samples_val": n_samples - split,
            "ensemble_models": len(ENSEMBLE_CONFIGS),
            "accuracy_1d": acc_1d,
            "accuracy_5d": acc_5d,
            "accuracy_1d_per_model": accuracy_1d_per_model,
            "accuracy_5d_per_model": accuracy_5d_per_model,
            "precision_1d": prec_1d,
            "recall_1d": recall_1d,
            "rmse_1d": rmse_1d,
            "calibration_error": cal_error,
            "feature_importance": importance,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "tickers_in_training": unique_tickers,
            "data_date_range": {"min": ts_min, "max": ts_max},
        }

        self.save_models()
        log.info("Training complete. Models saved to %s", self._model_dir)

        return self._training_stats

    # ------------------------------------------------------------------
    # Ensemble helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensemble_predict_proba(
        ensemble: list[tuple[Any, float]], X: np.ndarray
    ) -> np.ndarray:
        """Weighted average of predicted probabilities from ensemble models.

        Parameters
        ----------
        ensemble:
            List of (model, weight) tuples.
        X:
            Feature matrix.

        Returns
        -------
        np.ndarray
            Weighted average probability array of shape (n_samples, n_classes).
        """
        if not ensemble:
            raise ValueError("Empty ensemble; cannot predict")

        total_weight = sum(w for _, w in ensemble)
        if total_weight <= 0:
            total_weight = len(ensemble)
            ensemble = [(m, 1.0) for m, _ in ensemble]

        proba_sum = None
        for model, weight in ensemble:
            try:
                proba = model.predict_proba(X)
                if proba_sum is None:
                    proba_sum = proba * (weight / total_weight)
                else:
                    proba_sum += proba * (weight / total_weight)
            except Exception:
                log.warning("Ensemble model failed during predict_proba; skipping")
                continue

        if proba_sum is None:
            raise RuntimeError("All ensemble models failed during prediction")

        return proba_sum

    @staticmethod
    def _ensemble_predict_classes(
        ensemble: list[tuple[Any, float]], X: np.ndarray
    ) -> np.ndarray:
        """Predict classes from weighted ensemble probabilities."""
        proba = MLScorer._ensemble_predict_proba(ensemble, X)
        return np.argmax(proba, axis=1)

    # ------------------------------------------------------------------
    # Rolling Retrain
    # ------------------------------------------------------------------

    def retrain_if_stale(
        self, feature_store: Any, max_age_hours: float = 168
    ) -> Optional[dict[str, Any]]:
        """Retrain models if they are older than ``max_age_hours``.

        Parameters
        ----------
        feature_store:
            Feature store to pass to ``train()`` if retraining is needed.
        max_age_hours:
            Maximum allowed model age in hours (default 168 = 7 days).

        Returns
        -------
        dict or None
            Training stats if retrained, None if models are fresh.
        """
        trained_at_str = self._training_stats.get("trained_at")
        if trained_at_str:
            try:
                trained_at = datetime.fromisoformat(trained_at_str)
                if trained_at.tzinfo is None:
                    trained_at = trained_at.replace(tzinfo=timezone.utc)
                age_hours = (
                    datetime.now(timezone.utc) - trained_at
                ).total_seconds() / 3600.0
                if age_hours < max_age_hours:
                    log.info(
                        "Models are %.1f hours old (max %d); skipping retrain",
                        age_hours,
                        max_age_hours,
                    )
                    return None
                log.info(
                    "Models are %.1f hours old (max %d); triggering retrain",
                    age_hours,
                    max_age_hours,
                )
            except (ValueError, TypeError):
                log.warning("Cannot parse trained_at timestamp; triggering retrain")
        else:
            log.info("No trained_at timestamp found; triggering retrain")

        return self.train(feature_store)

    # ------------------------------------------------------------------
    # Feature Drift Detection
    # ------------------------------------------------------------------

    def detect_drift(self, feature_store: Any) -> dict[str, Any]:
        """Detect feature drift between training data and recent data.

        Computes Population Stability Index (PSI) for each feature
        comparing the training distribution against the last 7 days.

        Parameters
        ----------
        feature_store:
            Must expose ``get_feature_matrix()`` returning recent data.

        Returns
        -------
        dict
            Per-feature PSI values with drift levels, overall drift score,
            and a ``needs_retrain`` boolean flag.
        """
        if not self._drift_baseline:
            return {
                "error": "No drift baseline available; train the model first",
                "needs_retrain": True,
            }

        try:
            (
                X_recent,
                _y1,
                _y5,
                _yr,
                feature_names,
                timestamps,
                _tickers,
            ) = feature_store.get_feature_matrix()
        except Exception:
            log.exception("Failed to fetch recent feature matrix for drift detection")
            return {"error": "Failed to fetch recent data", "needs_retrain": False}

        if len(X_recent) == 0:
            return {"error": "No recent data available", "needs_retrain": False}

        X_recent = np.where(np.isnan(X_recent), _MISSING, X_recent)

        # Filter to last 7 days if timestamps available
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            seven_days_ago = now_ts - 7 * 24 * 3600
            recent_mask = timestamps >= seven_days_ago
            if recent_mask.sum() > 0:
                X_recent = X_recent[recent_mask]
        except Exception:
            pass  # Use all data if timestamp filtering fails

        drift_results: dict[str, Any] = {}
        psi_values: list[float] = []

        for i, fname in enumerate(self._feature_names):
            if fname not in self._drift_baseline:
                continue
            if i >= X_recent.shape[1]:
                continue

            baseline = self._drift_baseline[fname]
            current = X_recent[:, i]
            # Exclude sentinel values
            current = current[current != _MISSING]

            if len(current) < 5 or len(baseline) < 5:
                drift_results[fname] = {
                    "psi": 0.0,
                    "drift_level": "none",
                    "note": "insufficient data",
                }
                continue

            psi = _compute_psi(baseline, current, bins=_PSI_BINS)
            psi_values.append(psi)

            if psi < _PSI_NONE:
                level = "none"
            elif psi < _PSI_MODERATE:
                level = "moderate"
            else:
                level = "significant"

            drift_results[fname] = {"psi": round(psi, 6), "drift_level": level}

        overall_drift = float(np.mean(psi_values)) if psi_values else 0.0
        needs_retrain = overall_drift > _PSI_NONE

        log.info(
            "Feature drift detection: overall PSI=%.4f, needs_retrain=%s, "
            "features with significant drift: %d",
            overall_drift,
            needs_retrain,
            sum(1 for v in drift_results.values() if v.get("drift_level") == "significant"),
        )

        return {
            **drift_results,
            "overall_drift": round(overall_drift, 6),
            "needs_retrain": needs_retrain,
        }

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, features: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Predict forward returns from a feature dict.

        Parameters
        ----------
        features:
            Dict whose keys match ``self._feature_names``.  Missing keys are
            filled with the sentinel value.

        Returns
        -------
        dict or None
            Prediction result with direction, probabilities, predicted return,
            confidence, and an ``ml_score`` in [-1, +1] suitable for blending.
            Returns ``None`` if the model is not trained.
        """
        if not self._is_trained:
            return None

        # Need at least some ensemble models or the legacy single model
        has_ensemble = bool(self._ensemble_1d)
        has_legacy = self._model_1d is not None
        if not has_ensemble and not has_legacy:
            return None

        # Build feature vector in training-order
        x = np.array(
            [float(features.get(f, _MISSING)) for f in self._feature_names],
            dtype=np.float64,
        )
        x = np.where(np.isnan(x), _MISSING, x)
        x = x.reshape(1, -1)

        # Direction probabilities via ensemble or single model
        if has_ensemble:
            try:
                proba_1d = self._ensemble_predict_proba(self._ensemble_1d, x)[0]
            except Exception:
                log.warning("Ensemble 1d prediction failed; falling back to legacy model")
                if has_legacy:
                    proba_1d = self._model_1d.predict_proba(x)[0]
                else:
                    return None

            try:
                proba_5d = self._ensemble_predict_proba(self._ensemble_5d, x)[0]
            except Exception:
                log.warning("Ensemble 5d prediction failed; using 1d probabilities")
                proba_5d = proba_1d
        else:
            proba_1d = self._model_1d.predict_proba(x)[0]  # type: ignore[union-attr]
            proba_5d = (
                self._model_5d.predict_proba(x)[0]
                if self._model_5d is not None
                else proba_1d
            )

        dir_1d_idx = int(np.argmax(proba_1d))
        dir_5d_idx = int(np.argmax(proba_5d))

        prob_up_1d = float(proba_1d[DIRECTION_MAP["up"]])
        prob_down_1d = float(proba_1d[DIRECTION_MAP["down"]])
        prob_up_5d = float(proba_5d[DIRECTION_MAP["up"]])

        # Return magnitude
        predicted_return_1d = 0.0
        if self._regressor_1d is not None:
            predicted_return_1d = float(self._regressor_1d.predict(x)[0])

        # Confidence: margin between top-1 and top-2 class probabilities
        sorted_proba = sorted(proba_1d, reverse=True)
        confidence = float(sorted_proba[0] - sorted_proba[1])

        # ml_score: (p_up - p_down) scaled by predicted return magnitude
        raw_ml = prob_up_1d - prob_down_1d
        magnitude_scale = 1.0 + min(abs(predicted_return_1d), 5.0) / 5.0  # 1..2x
        ml_score = max(-1.0, min(1.0, raw_ml * magnitude_scale))

        return {
            "direction_1d": DIRECTION_INV[dir_1d_idx],
            "direction_5d": DIRECTION_INV[dir_5d_idx],
            "probability_up_1d": prob_up_1d,
            "probability_up_5d": prob_up_5d,
            "predicted_return_1d": predicted_return_1d,
            "confidence": confidence,
            "ml_score": ml_score,
        }

    # ------------------------------------------------------------------
    # Blending
    # ------------------------------------------------------------------

    @staticmethod
    def blend_with_rule_based(
        rule_score: float,
        ml_prediction: Optional[dict[str, Any]],
        blend_weight: float = 0.5,
    ) -> float:
        """Blend rule-based score with ML prediction.

        Parameters
        ----------
        rule_score:
            Output of ``RuleBasedScorer.score().signal_score`` (range -1..+1).
        ml_prediction:
            Output of ``self.predict()``.  If ``None``, the rule score is
            returned unchanged.
        blend_weight:
            0 = all rule-based, 1 = all ML.

        Returns
        -------
        float
            Blended score clamped to [-1, +1].
        """
        if ml_prediction is None:
            return rule_score

        ml_score = ml_prediction.get("ml_score", 0.0)
        blended = rule_score * (1.0 - blend_weight) + ml_score * blend_weight
        return max(-1.0, min(1.0, blended))

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------

    def get_feature_importance(self) -> list[dict[str, Any]]:
        """Return feature importance from the 1d direction model.

        Returns
        -------
        list[dict]
            ``[{"feature": str, "importance": float}, ...]`` sorted by
            importance descending.
        """
        if self._model_1d is None or not self._feature_names:
            return []

        try:
            raw = self._model_1d.feature_importances_
        except AttributeError:
            return []

        total = float(raw.sum()) if raw.sum() > 0 else 1.0
        pairs = [
            {"feature": name, "importance": round(float(imp / total), 6)}
            for name, imp in zip(self._feature_names, raw)
        ]
        pairs.sort(key=lambda p: p["importance"], reverse=True)
        return pairs

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_models(self) -> None:
        """Persist trained ensemble models and metadata to ``self._model_dir``."""
        if not self._is_trained:
            log.warning("save_models() called but no trained models to save")
            return

        model_dir = Path(self._model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        saved_count = 0

        # Save ensemble models for 1d
        for i, (model, weight) in enumerate(self._ensemble_1d):
            try:
                # Extract the underlying LGBMClassifier from calibrated wrapper
                raw_model = model.estimator if hasattr(model, "estimator") else model
                raw_model.booster_.save_model(str(model_dir / f"model_1d_{i}.txt"))
                saved_count += 1
            except Exception:
                log.exception("Failed to save 1d ensemble model %d", i)

        # Save ensemble models for 5d
        for i, (model, weight) in enumerate(self._ensemble_5d):
            try:
                raw_model = model.estimator if hasattr(model, "estimator") else model
                raw_model.booster_.save_model(str(model_dir / f"model_5d_{i}.txt"))
                saved_count += 1
            except Exception:
                log.exception("Failed to save 5d ensemble model %d", i)

        # Save legacy single models for backward compatibility
        if self._model_1d is not None:
            try:
                self._model_1d.booster_.save_model(str(model_dir / "model_1d.txt"))
            except Exception:
                pass
        if self._model_5d is not None:
            try:
                self._model_5d.booster_.save_model(str(model_dir / "model_5d.txt"))
            except Exception:
                pass
        if self._regressor_1d is not None:
            try:
                self._regressor_1d.booster_.save_model(str(model_dir / "regressor_1d.txt"))
                saved_count += 1
            except Exception:
                log.exception("Failed to save regressor_1d")

        # Save calibration wrappers via pickle
        try:
            calibration_data = {
                "ensemble_1d": [(None, w) for _, w in self._ensemble_1d],
                "ensemble_5d": [(None, w) for _, w in self._ensemble_5d],
            }
            # Save full calibrated models if possible
            for i, (model, w) in enumerate(self._ensemble_1d):
                if hasattr(model, "calibrated_classifiers_"):
                    calibration_data["ensemble_1d"][i] = (model, w)
            for i, (model, w) in enumerate(self._ensemble_5d):
                if hasattr(model, "calibrated_classifiers_"):
                    calibration_data["ensemble_5d"][i] = (model, w)

            cal_path = model_dir / "calibration.pkl"
            with open(cal_path, "wb") as f:
                pickle.dump(calibration_data, f)
        except Exception:
            log.warning("Failed to save calibration data; models will load uncalibrated")

        # Save drift baseline
        try:
            drift_path = model_dir / "drift_baseline.pkl"
            with open(drift_path, "wb") as f:
                pickle.dump(self._drift_baseline, f)
        except Exception:
            log.warning("Failed to save drift baseline data")

        # Save metadata (feature names + training stats)
        meta = {
            "feature_names": self._feature_names,
            "training_stats": self._training_stats,
            "ensemble_1d_count": len(self._ensemble_1d),
            "ensemble_5d_count": len(self._ensemble_5d),
            "ensemble_1d_weights": [w for _, w in self._ensemble_1d],
            "ensemble_5d_weights": [w for _, w in self._ensemble_5d],
        }
        meta_path = model_dir / "ml_scorer_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

        log.info(
            "Saved %d models + metadata to %s",
            saved_count,
            model_dir,
        )

    def load_models(self) -> bool:
        """Load persisted models from ``self._model_dir``.

        Returns
        -------
        bool
            ``True`` if models were loaded successfully.
        """
        if lgb is None:
            log.warning("LightGBM not installed; cannot load models")
            return False

        model_dir = Path(self._model_dir)
        meta_path = model_dir / "ml_scorer_meta.json"

        if not meta_path.exists():
            return False

        try:
            # Load metadata
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._feature_names = meta.get("feature_names", [])
            self._training_stats = meta.get("training_stats", {})

            n_features = len(self._feature_names)
            if n_features == 0:
                log.warning("Metadata has no feature names; skipping model load")
                return False

            # Load ensemble models
            ensemble_1d_count = meta.get("ensemble_1d_count", 0)
            ensemble_5d_count = meta.get("ensemble_5d_count", 0)
            ensemble_1d_weights = meta.get("ensemble_1d_weights", [])
            ensemble_5d_weights = meta.get("ensemble_5d_weights", [])

            # Try loading calibration data
            calibration_data = None
            cal_path = model_dir / "calibration.pkl"
            if cal_path.exists():
                try:
                    with open(cal_path, "rb") as f:
                        calibration_data = pickle.load(f)
                except Exception:
                    log.warning("Failed to load calibration data; using uncalibrated models")

            # Load drift baseline
            drift_path = model_dir / "drift_baseline.pkl"
            if drift_path.exists():
                try:
                    with open(drift_path, "rb") as f:
                        self._drift_baseline = pickle.load(f)
                except Exception:
                    log.warning("Failed to load drift baseline")
                    self._drift_baseline = {}

            # Load ensemble 1d models
            self._ensemble_1d = []
            for i in range(ensemble_1d_count):
                model_path = model_dir / f"model_1d_{i}.txt"
                if not model_path.exists():
                    continue
                try:
                    booster = lgb.Booster(model_file=str(model_path))
                    clf = LGBMClassifier(**ENSEMBLE_CONFIGS[min(i, len(ENSEMBLE_CONFIGS) - 1)][1])
                    clf._Booster = booster
                    clf._n_features = n_features
                    clf._n_features_in = n_features
                    clf.fitted_ = True
                    clf._n_classes = 3
                    clf.classes_ = np.array([0, 1, 2])
                    clf._le = _IdentityLabelEncoder()
                    clf.feature_importances_ = np.array(
                        booster.feature_importance(), dtype=np.float64
                    )

                    weight = ensemble_1d_weights[i] if i < len(ensemble_1d_weights) else 1.0

                    # Try to use calibrated wrapper if available
                    if (
                        calibration_data
                        and "ensemble_1d" in calibration_data
                        and i < len(calibration_data["ensemble_1d"])
                    ):
                        cal_model, _ = calibration_data["ensemble_1d"][i]
                        if cal_model is not None and hasattr(cal_model, "calibrated_classifiers_"):
                            # Re-attach the underlying estimator
                            cal_model.estimator = clf
                            self._ensemble_1d.append((cal_model, weight))
                            continue

                    self._ensemble_1d.append((clf, weight))
                except Exception:
                    log.exception("Failed to load 1d ensemble model %d", i)

            # Load ensemble 5d models
            self._ensemble_5d = []
            for i in range(ensemble_5d_count):
                model_path = model_dir / f"model_5d_{i}.txt"
                if not model_path.exists():
                    continue
                try:
                    booster = lgb.Booster(model_file=str(model_path))
                    clf = LGBMClassifier(**ENSEMBLE_CONFIGS[min(i, len(ENSEMBLE_CONFIGS) - 1)][1])
                    clf._Booster = booster
                    clf._n_features = n_features
                    clf._n_features_in = n_features
                    clf.fitted_ = True
                    clf._n_classes = 3
                    clf.classes_ = np.array([0, 1, 2])
                    clf._le = _IdentityLabelEncoder()

                    weight = ensemble_5d_weights[i] if i < len(ensemble_5d_weights) else 1.0

                    if (
                        calibration_data
                        and "ensemble_5d" in calibration_data
                        and i < len(calibration_data["ensemble_5d"])
                    ):
                        cal_model, _ = calibration_data["ensemble_5d"][i]
                        if cal_model is not None and hasattr(cal_model, "calibrated_classifiers_"):
                            cal_model.estimator = clf
                            self._ensemble_5d.append((cal_model, weight))
                            continue

                    self._ensemble_5d.append((clf, weight))
                except Exception:
                    log.exception("Failed to load 5d ensemble model %d", i)

            # Load legacy single model for feature importance
            legacy_1d_path = model_dir / "model_1d.txt"
            if legacy_1d_path.exists():
                booster_1d = lgb.Booster(model_file=str(legacy_1d_path))
                self._model_1d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
                self._model_1d._Booster = booster_1d
                self._model_1d._n_features = n_features
                self._model_1d._n_features_in = n_features
                self._model_1d.fitted_ = True
                self._model_1d._n_classes = 3
                self._model_1d.classes_ = np.array([0, 1, 2])
                self._model_1d._le = _IdentityLabelEncoder()
                self._model_1d.feature_importances_ = np.array(
                    booster_1d.feature_importance(), dtype=np.float64
                )
            elif self._ensemble_1d:
                # Use first ensemble model as legacy
                first = self._ensemble_1d[0][0]
                self._model_1d = (
                    first.estimator if hasattr(first, "estimator") else first
                )

            # Load legacy 5d if available (optional now with ensemble)
            legacy_5d_path = model_dir / "model_5d.txt"
            if legacy_5d_path.exists():
                booster_5d = lgb.Booster(model_file=str(legacy_5d_path))
                self._model_5d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
                self._model_5d._Booster = booster_5d
                self._model_5d._n_features = n_features
                self._model_5d._n_features_in = n_features
                self._model_5d.fitted_ = True
                self._model_5d._n_classes = 3
                self._model_5d.classes_ = np.array([0, 1, 2])
                self._model_5d._le = _IdentityLabelEncoder()

            # Load regressor
            reg_path = model_dir / "regressor_1d.txt"
            if reg_path.exists():
                booster_reg = lgb.Booster(model_file=str(reg_path))
                self._regressor_1d = LGBMRegressor(**LGBM_PARAMS_REGRESSOR)
                self._regressor_1d._Booster = booster_reg
                self._regressor_1d._n_features = n_features
                self._regressor_1d._n_features_in = n_features
                self._regressor_1d.fitted_ = True

            # Mark as trained if we have at least ensemble or legacy models
            if self._ensemble_1d or self._model_1d is not None:
                self._is_trained = True
                log.info(
                    "Loaded ML models (%d features, %d 1d-ensemble, %d 5d-ensemble, trained %s)",
                    n_features,
                    len(self._ensemble_1d),
                    len(self._ensemble_5d),
                    self._training_stats.get("trained_at", "unknown"),
                )
                return True

            return False

        except Exception:
            log.exception("Failed to load ML models from %s", model_dir)
            self._is_trained = False
            return False


class _IdentityLabelEncoder:
    """Minimal stand-in for sklearn's LabelEncoder used internally by
    LGBMClassifier when loading from a saved booster.  The classes are
    already encoded as 0/1/2 so this is a no-op transform.
    """

    def inverse_transform(self, y: np.ndarray) -> np.ndarray:
        return y

    def transform(self, y: np.ndarray) -> np.ndarray:
        return y
