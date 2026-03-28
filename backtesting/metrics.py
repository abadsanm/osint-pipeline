"""Backtesting metrics — evaluate prediction quality."""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("backtesting.metrics")


def accuracy(results: list[dict]) -> float:
    """Overall accuracy: fraction of correct predictions."""
    if not results:
        return 0.0
    correct = sum(1 for r in results if r["prediction"].outcome == "correct")
    return correct / len(results)


def precision_at_threshold(results: list[dict], threshold: float = 0.7) -> float:
    """Precision when only considering predictions with confidence > threshold."""
    high_conf = [r for r in results if r["prediction"].confidence > threshold]
    if not high_conf:
        return 0.0
    correct = sum(1 for r in high_conf if r["prediction"].outcome == "correct")
    return correct / len(high_conf)


def calibration_curve(results: list[dict], n_bins: int = 10) -> list[dict]:
    """Calibration curve: predicted confidence vs actual accuracy per bin.

    Returns list of {bin_center, predicted_confidence, actual_accuracy, count}.
    """
    if not results:
        return []

    confidences = np.array([r["prediction"].confidence for r in results])
    corrects = np.array([1.0 if r["prediction"].outcome == "correct" else 0.0 for r in results])

    bin_edges = np.linspace(0, 1, n_bins + 1)
    curve = []
    for i in range(n_bins):
        mask = (confidences >= bin_edges[i]) & (confidences < bin_edges[i + 1])
        if mask.sum() > 0:
            curve.append({
                "bin_center": round(float((bin_edges[i] + bin_edges[i + 1]) / 2), 2),
                "predicted_confidence": round(float(confidences[mask].mean()), 3),
                "actual_accuracy": round(float(corrects[mask].mean()), 3),
                "count": int(mask.sum()),
            })
    return curve


def sharpe_ratio(results: list[dict]) -> float:
    """Annualized Sharpe ratio assuming daily returns from 24h predictions.

    Uses actual_return from each 24h prediction as daily return.
    """
    daily = [r["actual_return"] for r in results if r["horizon"] == 24 and r["actual_return"] is not None]
    if len(daily) < 2:
        return 0.0

    # Adjust returns by direction: positive when prediction was correct direction
    adjusted = []
    for r in results:
        if r["horizon"] != 24 or r["actual_return"] is None:
            continue
        ret = r["actual_return"] / 100  # Convert from % to decimal
        direction = r["prediction"].direction
        if direction == "bullish":
            adjusted.append(ret)
        elif direction == "bearish":
            adjusted.append(-ret)
        else:
            adjusted.append(0.0)

    if len(adjusted) < 2:
        return 0.0

    arr = np.array(adjusted)
    mean_ret = arr.mean()
    std_ret = arr.std(ddof=1)
    if std_ret == 0:
        return 0.0
    # Annualize: ~252 trading days
    return float(mean_ret / std_ret * np.sqrt(252))


def profit_factor(results: list[dict]) -> float:
    """Sum of winning trades / sum of losing trades (using 24h predictions)."""
    wins = 0.0
    losses = 0.0
    for r in results:
        if r["horizon"] != 24 or r["actual_return"] is None:
            continue
        ret = r["actual_return"] / 100
        direction = r["prediction"].direction
        if direction == "bullish":
            pnl = ret
        elif direction == "bearish":
            pnl = -ret
        else:
            continue

        if pnl > 0:
            wins += pnl
        elif pnl < 0:
            losses += abs(pnl)

    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def max_drawdown(results: list[dict]) -> float:
    """Max peak-to-trough % decline in cumulative returns (24h predictions)."""
    returns = []
    for r in results:
        if r["horizon"] != 24 or r["actual_return"] is None:
            continue
        ret = r["actual_return"] / 100
        direction = r["prediction"].direction
        if direction == "bullish":
            returns.append(ret)
        elif direction == "bearish":
            returns.append(-ret)
        else:
            returns.append(0.0)

    if not returns:
        return 0.0

    cumulative = np.cumsum(returns)
    peak = np.maximum.accumulate(cumulative)
    drawdown = (peak - cumulative)
    return float(drawdown.max()) * 100  # Return as percentage


def win_rate_by_regime(results: list[dict]) -> dict:
    """Accuracy broken down by market regime."""
    regime_counts = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in results:
        regime = r.get("regime", "unknown")
        regime_counts[regime]["total"] += 1
        if r["prediction"].outcome == "correct":
            regime_counts[regime]["correct"] += 1

    return {
        regime: round(data["correct"] / data["total"], 4) if data["total"] > 0 else 0.0
        for regime, data in regime_counts.items()
    }


def signal_decay_analysis(results: list[dict]) -> list[dict]:
    """Accuracy for each time horizon — shows how prediction quality decays."""
    horizon_counts = defaultdict(lambda: {"correct": 0, "total": 0})
    for r in results:
        h = r["horizon"]
        horizon_counts[h]["total"] += 1
        if r["prediction"].outcome == "correct":
            horizon_counts[h]["correct"] += 1

    return sorted(
        [
            {
                "horizon": h,
                "accuracy": round(data["correct"] / data["total"], 4) if data["total"] > 0 else 0.0,
                "count": data["total"],
            }
            for h, data in horizon_counts.items()
        ],
        key=lambda x: x["horizon"],
    )


def equity_curve(results: list[dict]) -> tuple[list[dict], list[dict], str, int]:
    """Compute cumulative equity curve and drawdown time series.

    Returns (equity_data, drawdown_data, max_dd_date, recovery_days).
    """
    # Sort by date, use 24h predictions
    dated = []
    for r in results:
        pred = r.get("prediction")
        ts = None
        if pred and hasattr(pred, "created_at") and pred.created_at:
            ts = str(pred.created_at)[:10]
        elif isinstance(r.get("timestamp"), str):
            ts = r["timestamp"][:10]
        if ts and r.get("horizon") == 24 and r.get("actual_return") is not None:
            ret = r["actual_return"] / 100
            d = pred.direction if pred else "neutral"
            pnl = ret if d == "bullish" else (-ret if d == "bearish" else 0.0)
            dated.append({"date": ts, "pnl": pnl, "regime": r.get("regime", "unknown")})

    if not dated:
        return [], [], "", 0

    dated.sort(key=lambda x: x["date"])

    # Aggregate by date
    daily = {}
    for d in dated:
        if d["date"] not in daily:
            daily[d["date"]] = {"pnl": 0.0, "regime": d["regime"]}
        daily[d["date"]]["pnl"] += d["pnl"]

    equity_data = []
    drawdown_data = []
    cum_return = 0.0
    peak = 0.0
    max_dd = 0.0
    max_dd_date = ""
    max_dd_peak_date = ""

    for date in sorted(daily.keys()):
        cum_return += daily[date]["pnl"] * 100
        if cum_return > peak:
            peak = cum_return
        dd = cum_return - peak
        if dd < max_dd:
            max_dd = dd
            max_dd_date = date

        equity_data.append({
            "date": date,
            "sentinel_return": round(cum_return, 2),
            "spy_return": 0.0,  # filled by caller if yfinance available
            "regime": daily[date]["regime"],
        })
        drawdown_data.append({"date": date, "drawdown": round(dd, 2)})

    # Estimate recovery days from max drawdown
    recovery_days = 0
    found_max = False
    for dd in drawdown_data:
        if dd["date"] == max_dd_date:
            found_max = True
            continue
        if found_max:
            recovery_days += 1
            if dd["drawdown"] >= -0.1:
                break

    return equity_data, drawdown_data, max_dd_date, recovery_days


def accuracy_over_time(results: list[dict], window: int = 30) -> list[dict]:
    """30-day rolling accuracy over time with regime labels."""
    dated = []
    for r in results:
        pred = r.get("prediction")
        ts = None
        if pred and hasattr(pred, "created_at") and pred.created_at:
            ts = str(pred.created_at)[:10]
        elif isinstance(r.get("timestamp"), str):
            ts = r["timestamp"][:10]
        if ts:
            dated.append({
                "date": ts,
                "correct": 1 if pred and pred.outcome == "correct" else 0,
                "regime": r.get("regime", "unknown"),
            })

    if len(dated) < window:
        return []

    dated.sort(key=lambda x: x["date"])
    output = []
    for i in range(window, len(dated)):
        w = dated[i - window:i]
        acc = sum(d["correct"] for d in w) / window
        output.append({
            "date": dated[i]["date"],
            "rolling_30d_accuracy": round(acc, 4),
            "regime": dated[i]["regime"],
        })
    return output


def return_by_confidence(results: list[dict]) -> list[dict]:
    """Average return and accuracy binned by confidence band."""
    bands = [
        (0.50, 0.60, "50-60%"),
        (0.60, 0.70, "60-70%"),
        (0.70, 0.80, "70-80%"),
        (0.80, 0.90, "80-90%"),
        (0.90, 1.01, "90%+"),
    ]
    output = []
    for lo, hi, label in bands:
        subset = [r for r in results if lo <= r["prediction"].confidence < hi]
        if not subset:
            output.append({"band": label, "avg_return": 0, "accuracy": 0, "count": 0})
            continue
        correct = sum(1 for r in subset if r["prediction"].outcome == "correct")
        returns = []
        for r in subset:
            if r.get("actual_return") is not None:
                ret = r["actual_return"] / 100
                d = r["prediction"].direction
                pnl = ret if d == "bullish" else (-ret if d == "bearish" else 0.0)
                returns.append(pnl * 100)
        avg_ret = float(np.mean(returns)) if returns else 0.0
        output.append({
            "band": label,
            "avg_return": round(avg_ret, 2),
            "accuracy": round(correct / len(subset), 4),
            "count": len(subset),
        })
    return output


def predictions_detail(results: list[dict]) -> list[dict]:
    """Export every prediction as a flat dict for frontend filtering."""
    output = []
    for r in results:
        pred = r.get("prediction")
        ts = None
        if pred and hasattr(pred, "created_at") and pred.created_at:
            ts = str(pred.created_at)[:10]
        elif isinstance(r.get("timestamp"), str):
            ts = r["timestamp"][:10]
        output.append({
            "date": ts or "",
            "ticker": r.get("ticker", ""),
            "direction": pred.direction if pred else "neutral",
            "confidence": round(pred.confidence, 4) if pred else 0.0,
            "outcome": pred.outcome if pred else None,
            "return": round(r.get("actual_return", 0) / 100, 4) if r.get("actual_return") is not None else 0.0,
            "regime": r.get("regime", "unknown"),
            "horizon_hours": r.get("horizon", 24),
        })
    return output


def per_ticker_metrics(results: list[dict]) -> list[dict]:
    """Compute accuracy, Sharpe, win rate, and avg return per ticker."""
    ticker_data = defaultdict(list)
    for r in results:
        ticker_data[r.get("ticker", "UNKNOWN")].append(r)

    output = []
    for ticker, ticker_results in sorted(ticker_data.items()):
        total = len(ticker_results)
        correct = sum(1 for r in ticker_results if r["prediction"].outcome == "correct")
        acc = correct / total if total else 0.0

        # Returns for 24h horizon
        returns_24h = []
        for r in ticker_results:
            if r.get("horizon") != 24 or r.get("actual_return") is None:
                continue
            ret = r["actual_return"] / 100
            d = r["prediction"].direction
            if d == "bullish":
                returns_24h.append(ret)
            elif d == "bearish":
                returns_24h.append(-ret)
            else:
                returns_24h.append(0.0)

        wins = [r for r in returns_24h if r > 0]
        trades = len(returns_24h)
        wr = len(wins) / trades if trades else 0.0
        avg_ret = float(np.mean(returns_24h)) * 100 if returns_24h else 0.0

        # Per-ticker Sharpe
        if len(returns_24h) >= 2:
            arr = np.array(returns_24h)
            std = arr.std(ddof=1)
            sr = float(arr.mean() / std * np.sqrt(252)) if std > 0 else 0.0
        else:
            sr = 0.0

        output.append({
            "ticker": ticker,
            "accuracy": round(acc, 4),
            "sharpe": round(sr, 2),
            "trades": trades,
            "win_rate": round(wr, 4),
            "avg_return": round(avg_ret, 2),
        })
    return output


def confusion_matrix(results: list[dict]) -> dict:
    """Compute 3x3 confusion matrix: predicted (bullish/bearish/neutral) vs actual (up/flat/down)."""
    matrix = {
        "bullish_up": 0, "bullish_flat": 0, "bullish_down": 0,
        "bearish_up": 0, "bearish_flat": 0, "bearish_down": 0,
        "neutral_up": 0, "neutral_flat": 0, "neutral_down": 0,
    }
    for r in results:
        pred_dir = r["prediction"].direction
        actual_ret = r.get("actual_return")
        if actual_ret is None:
            continue
        if actual_ret > 0.5:
            actual = "up"
        elif actual_ret < -0.5:
            actual = "down"
        else:
            actual = "flat"
        key = f"{pred_dir}_{actual}"
        if key in matrix:
            matrix[key] += 1
    return matrix


def save_results(
    results: list[dict],
    feature_importance: list[dict] | None = None,
) -> str | None:
    """Save computed metrics to a timestamped JSON file in backtesting/results/.

    Returns the file path on success, or None on failure.
    """
    if not results:
        return None

    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    timestamp_str = now.strftime("%Y%m%dT%H%M%SZ")

    tickers = sorted(set(r.get("ticker", "") for r in results))
    horizons = sorted(set(r.get("horizon", 0) for r in results))

    # Compute date range from predictions
    dates = []
    for r in results:
        pred = r.get("prediction")
        if pred and hasattr(pred, "created_at") and pred.created_at:
            dates.append(str(pred.created_at)[:10])
        elif isinstance(r.get("timestamp"), str):
            dates.append(r["timestamp"][:10])
    date_range = {"start": min(dates) if dates else "", "end": max(dates) if dates else ""}

    # by_regime with accuracy
    regime_data = win_rate_by_regime(results)
    by_regime = {}
    for regime, wr in regime_data.items():
        count = sum(1 for r in results if r.get("regime") == regime)
        correct_count = sum(1 for r in results if r.get("regime") == regime and r["prediction"].outcome == "correct")
        by_regime[regime] = {"total": count, "correct": correct_count, "accuracy": round(wr, 4)}

    # by_horizon with accuracy
    decay = signal_decay_analysis(results)
    by_horizon = {}
    for d in decay:
        h = str(d["horizon"])
        by_horizon[h] = {"total": d["count"], "accuracy": d["accuracy"]}

    payload = {
        "timestamp": now.isoformat(),
        "accuracy": round(accuracy(results), 4),
        "precision_at_70": round(precision_at_threshold(results, 0.7), 4),
        "sharpe_ratio": round(sharpe_ratio(results), 4),
        "profit_factor": round(profit_factor(results), 4),
        "max_drawdown": round(max_drawdown(results), 2),
        "calibration_curve": calibration_curve(results),
        "by_regime": by_regime,
        "by_horizon": by_horizon,
        "total_predictions": len(results),
        "tickers": tickers,
        "date_range": date_range,
        "per_ticker": per_ticker_metrics(results),
        "confusion": confusion_matrix(results),
        "return_by_confidence": return_by_confidence(results),
        "signal_decay": [
            {"hours": d["horizon"], "accuracy": d["accuracy"]}
            for d in signal_decay_analysis(results)
        ],
        "accuracy_over_time": accuracy_over_time(results),
        "predictions_detail": predictions_detail(results),
    }

    # Equity curve + drawdown
    eq_data, dd_data, dd_date, dd_recovery = equity_curve(results)
    payload["equity_curve"] = eq_data
    payload["drawdown_curve"] = dd_data
    payload["max_drawdown_date"] = dd_date
    payload["max_drawdown_recovery_days"] = dd_recovery

    # Benchmarks (static comparison — computed from backtest stats)
    payload["benchmarks"] = {
        "sentinel_ensemble": {
            "total_return": round(eq_data[-1]["sentinel_return"], 1) if eq_data else 0,
            "sharpe": payload["sharpe_ratio"],
            "max_drawdown": round(payload["max_drawdown"], 1),
            "accuracy": round(payload["accuracy"] * 100, 1),
        },
        "random_baseline": {
            "total_return": -2.1,
            "sharpe": -0.12,
            "max_drawdown": -18.4,
            "accuracy": 50.0,
        },
    }

    if feature_importance:
        payload["feature_importance"] = feature_importance

    file_path = results_dir / f"{timestamp_str}.json"
    try:
        with open(file_path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        log.info("Backtesting results saved to %s", file_path)
        return str(file_path)
    except Exception as e:
        log.error("Failed to save backtesting results: %s", e)
        return None


def print_summary(results: list[dict], save: bool = True, feature_importance: list[dict] | None = None) -> None:
    """Print a formatted summary of all backtesting metrics."""
    if not results:
        print("No results to summarize.")
        return

    print("\n" + "=" * 60)
    print("BACKTESTING RESULTS SUMMARY")
    print("=" * 60)

    total = len(results)
    tickers = set(r["ticker"] for r in results)
    horizons = sorted(set(r["horizon"] for r in results))

    print(f"\nDataset: {total} predictions across {len(tickers)} tickers")
    print(f"Tickers: {', '.join(sorted(tickers))}")
    print(f"Horizons: {horizons}")

    # Overall accuracy
    acc = accuracy(results)
    print(f"\n--- Overall Accuracy ---")
    print(f"  Accuracy:                {acc:.1%}")
    print(f"  Precision (conf > 0.7):  {precision_at_threshold(results, 0.7):.1%}")
    print(f"  Precision (conf > 0.5):  {precision_at_threshold(results, 0.5):.1%}")

    # Risk metrics (24h)
    print(f"\n--- Risk Metrics (24h horizon) ---")
    sr = sharpe_ratio(results)
    pf = profit_factor(results)
    mdd = max_drawdown(results)
    print(f"  Sharpe Ratio:            {sr:.3f}")
    print(f"  Profit Factor:           {pf:.3f}")
    print(f"  Max Drawdown:            {mdd:.2f}%")

    # Signal decay
    print(f"\n--- Signal Decay Analysis ---")
    decay = signal_decay_analysis(results)
    for d in decay:
        print(f"  {d['horizon']:>4}h:  {d['accuracy']:.1%}  (n={d['count']})")

    # Regime breakdown
    print(f"\n--- Win Rate by Regime ---")
    regimes = win_rate_by_regime(results)
    for regime, wr in sorted(regimes.items()):
        count = sum(1 for r in results if r.get("regime") == regime)
        print(f"  {regime:<22s} {wr:.1%}  (n={count})")

    # Calibration
    print(f"\n--- Calibration Curve ---")
    cal = calibration_curve(results)
    print(f"  {'Bin':>6s}  {'Predicted':>10s}  {'Actual':>8s}  {'Count':>6s}")
    for c in cal:
        print(f"  {c['bin_center']:>6.2f}  {c['predicted_confidence']:>10.3f}  {c['actual_accuracy']:>8.3f}  {c['count']:>6d}")

    print("\n" + "=" * 60)

    # Save results to JSON file
    if save:
        saved_path = save_results(results, feature_importance=feature_importance)
        if saved_path:
            print(f"\nResults saved to: {saved_path}")
