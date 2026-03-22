"""
Backtesting CLI

Usage:
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --tickers SPY,QQQ,AAPL,MSFT,NVDA
"""

import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
log = logging.getLogger("backtesting")

def main():
    parser = argparse.ArgumentParser(description="OSINT Pipeline Backtesting")
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument("--tickers", required=True, help="Comma-separated ticker list")
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",")]

    log.info("=" * 60)
    log.info("OSINT Pipeline Backtesting")
    log.info("  Period: %s to %s", args.start, args.end)
    log.info("  Tickers: %s", ", ".join(tickers))
    log.info("=" * 60)

    # Step 1: Load data
    from backtesting.data_loader import load_price_data, simulate_sentiment_features
    prices = load_price_data(tickers, args.start, args.end)
    features = {t: simulate_sentiment_features(df) for t, df in prices.items()}

    # Step 2: Run simulation
    from backtesting.simulator import run_simulation
    results = run_simulation(prices, features)

    if not results:
        log.error("No results generated. Check data availability.")
        sys.exit(1)

    # Step 3: Print metrics
    from backtesting.metrics import print_summary
    print_summary(results)

    # Step 4: Train scorer
    from backtesting.trainer import train_scorer
    train_stats = train_scorer(results)
    if "error" not in train_stats:
        log.info("Scorer trained: %.1f%% accuracy", train_stats["accuracy"] * 100)

    # Step 5: Fit calibrator
    from backtesting.calibrator import fit_calibrator
    cal_stats = fit_calibrator(results)
    if "error" not in cal_stats:
        log.info("Calibrator fitted: %.3f error", cal_stats["calibration_error"])

    log.info("=" * 60)
    log.info("Backtesting complete. Models saved to stock_alpha/models/")
    log.info("=" * 60)

if __name__ == "__main__":
    main()
