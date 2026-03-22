"""Calibration model — maps raw scores to calibrated probabilities."""

import logging
import json
from pathlib import Path

import numpy as np

log = logging.getLogger("backtesting.calibrator")

MODELS_DIR = Path("stock_alpha/models")

def fit_calibrator(results: list[dict], model_name: str = "calibrator_latest") -> dict:
    """Fit isotonic regression: raw_confidence -> actual_accuracy.

    Saves calibration model to stock_alpha/models/{model_name}.joblib
    Returns calibration curve data.
    """
    from sklearn.isotonic import IsotonicRegression
    import joblib

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Use 24h predictions for calibration
    confidences = []
    corrects = []
    for r in results:
        if r["horizon"] != 24:
            continue
        pred = r["prediction"]
        if pred.outcome not in ("correct", "incorrect"):
            continue
        confidences.append(pred.confidence)
        corrects.append(1.0 if pred.outcome == "correct" else 0.0)

    if len(confidences) < 50:
        log.warning("Too few samples for calibration: %d", len(confidences))
        return {"error": "insufficient_samples", "count": len(confidences)}

    X = np.array(confidences)
    y = np.array(corrects)

    # Fit isotonic regression
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(X, y)

    # Save
    model_path = MODELS_DIR / f"{model_name}.joblib"
    joblib.dump(calibrator, model_path)
    log.info("Calibrator saved to %s", model_path)

    # Generate calibration curve
    n_bins = 10
    bin_edges = np.linspace(0, 1, n_bins + 1)
    curve = []
    for i in range(n_bins):
        mask = (X >= bin_edges[i]) & (X < bin_edges[i + 1])
        if mask.sum() > 0:
            curve.append({
                "bin_center": round(float((bin_edges[i] + bin_edges[i + 1]) / 2), 2),
                "predicted_confidence": round(float(X[mask].mean()), 3),
                "actual_accuracy": round(float(y[mask].mean()), 3),
                "count": int(mask.sum()),
            })

    # Save curve as JSON
    curve_path = MODELS_DIR / f"{model_name}_curve.json"
    curve_path.write_text(json.dumps(curve, indent=2))

    # Calibration error
    cal_error = np.mean(np.abs(X - y))

    stats = {
        "samples": len(confidences),
        "calibration_error": round(float(cal_error), 4),
        "curve": curve,
        "model_path": str(model_path),
    }

    log.info("Calibration error: %.3f across %d samples", cal_error, len(confidences))
    return stats
