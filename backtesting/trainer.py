"""Train XGBoost classifier on backtesting results."""

import logging
import json
from pathlib import Path

log = logging.getLogger("backtesting.trainer")

MODELS_DIR = Path("stock_alpha/models")

def train_scorer(results: list[dict], model_name: str = "scorer_latest") -> dict:
    """Train XGBoost classifier: features -> {bullish, bearish, neutral}.

    Walk-forward validation: train on first 70%, test on last 30%.
    Saves model to stock_alpha/models/{model_name}.joblib
    Returns training stats dict.
    """
    import numpy as np
    from xgboost import XGBClassifier
    from sklearn.metrics import classification_report, accuracy_score
    from sklearn.preprocessing import LabelEncoder
    import joblib

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Extract features and labels (use 24h horizon for training)
    features = []
    labels = []
    for r in results:
        if r["horizon"] != 24:
            continue
        fv = r["features"]
        # Skip if any critical feature is None
        if any(fv.get(k) is None for k in ["rsi", "macd_hist", "bb_pctb"]):
            continue
        features.append([
            fv.get("sentiment_score", 0),
            fv.get("svc_value", 0),
            fv.get("rsi", 50),
            fv.get("macd_hist", 0),
            fv.get("bb_pctb", 0.5),
            fv.get("sma20_distance", 0),
            fv.get("correlation_confidence", 0.5),
            fv.get("source_count", 1),
            fv.get("volume_zscore", 0),
        ])
        # Label: actual direction from return
        actual = r["actual_return"]
        if actual > 0.1:
            labels.append("bullish")
        elif actual < -0.1:
            labels.append("bearish")
        else:
            labels.append("neutral")

    if len(features) < 100:
        log.warning("Too few samples for training: %d (need 100+)", len(features))
        return {"error": "insufficient_samples", "count": len(features)}

    X = np.array(features)
    le = LabelEncoder()
    y = le.fit_transform(labels)

    # Walk-forward split: 70% train, 30% test
    split = int(len(X) * 0.7)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    feature_names = [
        "sentiment_score", "svc_value", "rsi", "macd_hist", "bb_pctb",
        "sma20_distance", "correlation_confidence", "source_count", "volume_zscore"
    ]

    log.info("Training XGBoost: %d train, %d test samples", len(X_train), len(X_test))

    model = XGBClassifier(
        n_estimators=200,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=10,
        eval_metric="mlogloss",
        use_label_encoder=False,
        verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    y_pred = model.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    report = classification_report(y_test, y_pred, target_names=le.classes_, output_dict=True)

    # Feature importance
    importance = sorted(
        zip(feature_names, model.feature_importances_),
        key=lambda x: -x[1]
    )

    log.info("Test accuracy: %.1f%%", acc * 100)
    for feat, imp in importance[:5]:
        log.info("  %s: %.3f", feat, imp)

    # Save model and metadata
    model_path = MODELS_DIR / f"{model_name}.joblib"
    joblib.dump({"model": model, "label_encoder": le, "feature_names": feature_names}, model_path)
    log.info("Model saved to %s", model_path)

    # Save feature importance as JSON
    importance_path = MODELS_DIR / f"{model_name}_importance.json"
    importance_path.write_text(json.dumps([{"feature": f, "importance": round(float(i), 4)} for f, i in importance]))

    stats = {
        "samples_total": len(X),
        "samples_train": len(X_train),
        "samples_test": len(X_test),
        "accuracy": round(acc, 4),
        "report": report,
        "feature_importance": [{"feature": f, "importance": round(float(i), 4)} for f, i in importance],
        "model_path": str(model_path),
    }

    return stats
