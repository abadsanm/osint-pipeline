"""
LightGBM-based ML scoring model for the Stock Alpha engine.

Trains on historical feature snapshots and predicts forward returns
(1d/5d/10d). Designed to blend with the rule-based scorer for a
hybrid approach: rule-based intuition + learned feature weights.

Walk-forward validation ensures the model never trains on future data.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.metrics import accuracy_score, precision_score, recall_score

log = logging.getLogger("stock_alpha.ml_scorer")

# ---------------------------------------------------------------------------
# Direction encoding
# ---------------------------------------------------------------------------
DIRECTION_MAP = {"down": 0, "flat": 1, "up": 2}
DIRECTION_INV = {v: k for k, v in DIRECTION_MAP.items()}

# ---------------------------------------------------------------------------
# LightGBM hyper-parameters
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


class MLScorer:
    """LightGBM model trained on historical feature snapshots.

    Predicts forward returns (1d/5d/10d) from the same features used
    by the rule-based scorer, but with learned weights.
    """

    def __init__(self, model_dir: str = "data/models") -> None:
        self._model_1d: Optional[LGBMClassifier] = None
        self._model_5d: Optional[LGBMClassifier] = None
        self._regressor_1d: Optional[LGBMRegressor] = None
        self._feature_names: list[str] = []
        self._model_dir = model_dir
        self._is_trained = False
        self._training_stats: dict[str, Any] = {}

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
    # Training
    # ------------------------------------------------------------------

    def train(self, feature_store: Any) -> dict[str, Any]:
        """Train models on labeled data from the feature store.

        Uses walk-forward (temporal) validation to avoid look-ahead bias.

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
        log.info("Starting ML model training...")

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
        sorted_tickers = [tickers[i] for i in sort_idx]

        split = int(n_samples * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_dir_1d_train, y_dir_1d_val = y_dir_1d[:split], y_dir_1d[split:]
        y_dir_5d_train, y_dir_5d_val = y_dir_5d[:split], y_dir_5d[split:]
        y_ret_1d_train, y_ret_1d_val = y_ret_1d[:split], y_ret_1d[split:]

        log.info("Train samples: %d | Validation samples: %d", split, n_samples - split)

        # 4. Train 1-day direction classifier
        self._model_1d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
        self._model_1d.fit(X_train, y_dir_1d_train)

        # 5. Train 5-day direction classifier
        self._model_5d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
        self._model_5d.fit(X_train, y_dir_5d_train)

        # 6. Train 1-day return regressor
        self._regressor_1d = LGBMRegressor(**LGBM_PARAMS_REGRESSOR)
        self._regressor_1d.fit(X_train, y_ret_1d_train)

        # 7. Evaluate on validation set
        pred_1d = self._model_1d.predict(X_val)
        pred_5d = self._model_5d.predict(X_val)
        pred_ret = self._regressor_1d.predict(X_val)

        acc_1d = float(accuracy_score(y_dir_1d_val, pred_1d))
        acc_5d = float(accuracy_score(y_dir_5d_val, pred_5d))
        rmse_1d = float(np.sqrt(np.mean((y_ret_1d_val - pred_ret) ** 2)))

        prec_1d = float(
            precision_score(y_dir_1d_val, pred_1d, average="weighted", zero_division=0)
        )
        recall_1d = float(
            recall_score(y_dir_1d_val, pred_1d, average="weighted", zero_division=0)
        )

        log.info(
            "Validation — 1d accuracy: %.3f, 5d accuracy: %.3f, 1d RMSE: %.4f",
            acc_1d,
            acc_5d,
            rmse_1d,
        )

        # 8. Feature importance (from 1d direction model)
        importance = self.get_feature_importance()

        # 9. Persist
        self._is_trained = True
        unique_tickers = sorted(set(sorted_tickers))
        self._training_stats = {
            "samples_total": n_samples,
            "samples_train": split,
            "samples_val": n_samples - split,
            "accuracy_1d": acc_1d,
            "accuracy_5d": acc_5d,
            "precision_1d": prec_1d,
            "recall_1d": recall_1d,
            "rmse_1d": rmse_1d,
            "feature_importance": importance,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "tickers_in_training": unique_tickers,
        }

        self.save_models()
        log.info("Training complete. Models saved to %s", self._model_dir)

        return self._training_stats

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
        if not self._is_trained or self._model_1d is None:
            return None

        # Build feature vector in training-order
        x = np.array(
            [float(features.get(f, _MISSING)) for f in self._feature_names],
            dtype=np.float64,
        )
        x = np.where(np.isnan(x), _MISSING, x)
        x = x.reshape(1, -1)

        # Direction probabilities
        proba_1d = self._model_1d.predict_proba(x)[0]  # [p_down, p_flat, p_up]
        proba_5d = self._model_5d.predict_proba(x)[0] if self._model_5d else proba_1d

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

        raw = self._model_1d.feature_importances_
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
        """Persist trained models and metadata to ``self._model_dir``."""
        if not self._is_trained:
            log.warning("save_models() called but no trained models to save")
            return

        model_dir = Path(self._model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)

        # Save LightGBM native text format
        if self._model_1d is not None:
            self._model_1d.booster_.save_model(str(model_dir / "model_1d.txt"))
        if self._model_5d is not None:
            self._model_5d.booster_.save_model(str(model_dir / "model_5d.txt"))
        if self._regressor_1d is not None:
            self._regressor_1d.booster_.save_model(str(model_dir / "regressor_1d.txt"))

        # Save metadata (feature names + training stats)
        meta = {
            "feature_names": self._feature_names,
            "training_stats": self._training_stats,
        }
        meta_path = model_dir / "ml_scorer_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

        log.info(
            "Saved %d models + metadata to %s",
            sum(
                1
                for m in [self._model_1d, self._model_5d, self._regressor_1d]
                if m is not None
            ),
            model_dir,
        )

    def load_models(self) -> bool:
        """Load persisted models from ``self._model_dir``.

        Returns
        -------
        bool
            ``True`` if all models were loaded successfully.
        """
        model_dir = Path(self._model_dir)
        meta_path = model_dir / "ml_scorer_meta.json"

        required_files = [
            model_dir / "model_1d.txt",
            model_dir / "model_5d.txt",
            model_dir / "regressor_1d.txt",
            meta_path,
        ]

        if not all(f.exists() for f in required_files):
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

            # Reconstruct classifiers by fitting a dummy dataset then overriding
            # the booster with the saved one.  LightGBM's Python wrapper does not
            # expose a direct ``load_model`` on the sklearn estimator, so we
            # create a booster from file and wrap it.

            import lightgbm as lgb

            # 1d direction classifier
            booster_1d = lgb.Booster(model_file=str(model_dir / "model_1d.txt"))
            self._model_1d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
            self._model_1d._Booster = booster_1d
            self._model_1d._n_features = n_features
            self._model_1d._n_features_in = n_features
            self._model_1d.fitted_ = True
            self._model_1d._n_classes = LGBM_PARAMS_CLASSIFIER["num_class"]
            self._model_1d.classes_ = np.array([0, 1, 2])
            self._model_1d._le = _IdentityLabelEncoder()
            self._model_1d.feature_importances_ = np.array(
                booster_1d.feature_importance(), dtype=np.float64
            )

            # 5d direction classifier
            booster_5d = lgb.Booster(model_file=str(model_dir / "model_5d.txt"))
            self._model_5d = LGBMClassifier(**LGBM_PARAMS_CLASSIFIER)
            self._model_5d._Booster = booster_1d  # intentional: reuse setup
            self._model_5d._Booster = booster_5d
            self._model_5d._n_features = n_features
            self._model_5d._n_features_in = n_features
            self._model_5d.fitted_ = True
            self._model_5d._n_classes = LGBM_PARAMS_CLASSIFIER["num_class"]
            self._model_5d.classes_ = np.array([0, 1, 2])
            self._model_5d._le = _IdentityLabelEncoder()

            # 1d return regressor
            booster_reg = lgb.Booster(model_file=str(model_dir / "regressor_1d.txt"))
            self._regressor_1d = LGBMRegressor(**LGBM_PARAMS_REGRESSOR)
            self._regressor_1d._Booster = booster_reg
            self._regressor_1d._n_features = n_features
            self._regressor_1d._n_features_in = n_features
            self._regressor_1d.fitted_ = True

            self._is_trained = True
            log.info(
                "Loaded ML models (%d features, trained %s)",
                n_features,
                self._training_stats.get("trained_at", "unknown"),
            )
            return True

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
