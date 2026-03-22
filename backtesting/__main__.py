"""
Backtesting CLI

Usage:
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --tickers SPY,QQQ,AAPL,MSFT,NVDA
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --universe sp500
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --universe nasdaq100
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --universe mega_cap
    python -m backtesting --start 2024-01-01 --end 2025-03-01 --universe large_cap

Universes:
    sp500      — Full S&P 500 (~503 tickers, ~2 hours runtime)
    nasdaq100  — NASDAQ-100 (~100 tickers, ~30 minutes)
    mega_cap   — Top 50 by market cap (~15 minutes)
    large_cap  — Top 200 S&P 500 (~1 hour)
"""

import argparse
import logging
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("backtesting")


def main():
    parser = argparse.ArgumentParser(
        description="OSINT Pipeline Backtesting",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="End date (YYYY-MM-DD)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tickers", help="Comma-separated ticker list")
    group.add_argument(
        "--universe",
        choices=["sp500", "nasdaq100", "mega_cap", "large_cap"],
        help="Predefined ticker universe",
    )

    parser.add_argument(
        "--batch-size", type=int, default=20,
        help="Number of tickers to download per yfinance batch (default: 20)",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Disable local parquet cache (re-download everything)",
    )
    args = parser.parse_args()

    # Resolve tickers
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        from backtesting.data_loader import resolve_universe
        tickers = resolve_universe(args.universe)

    start_time = time.time()

    log.info("=" * 60)
    log.info("OSINT Pipeline Backtesting")
    log.info("  Period:   %s to %s", args.start, args.end)
    log.info("  Universe: %s (%d tickers)", args.universe or "custom", len(tickers))
    if len(tickers) <= 20:
        log.info("  Tickers:  %s", ", ".join(tickers))
    else:
        log.info("  Tickers:  %s ... and %d more", ", ".join(tickers[:10]), len(tickers) - 10)
    log.info("  Cache:    %s", "disabled" if args.no_cache else "enabled")
    log.info("=" * 60)

    # Step 1: Load data
    log.info("")
    log.info("Step 1/5: Loading price data...")
    from backtesting.data_loader import load_price_data, simulate_sentiment_features

    prices = load_price_data(
        tickers, args.start, args.end,
        batch_size=args.batch_size,
        use_cache=not args.no_cache,
    )

    if not prices:
        log.error("No price data loaded. Check network and ticker symbols.")
        sys.exit(1)

    log.info("Generating synthetic sentiment features for %d tickers...", len(prices))
    features = {t: simulate_sentiment_features(df) for t, df in prices.items()}

    # Step 2: Run simulation
    log.info("")
    log.info("Step 2/5: Running simulation across %d tickers...", len(prices))
    from backtesting.simulator import run_simulation

    results = run_simulation(prices, features)

    if not results:
        log.error("No results generated. Check data availability.")
        sys.exit(1)

    log.info("Simulation produced %d predictions", len(results))

    # Step 3: Print metrics
    log.info("")
    log.info("Step 3/5: Computing metrics...")
    from backtesting.metrics import print_summary

    print_summary(results, save=True)

    # Step 4: Train scorer
    log.info("")
    log.info("Step 4/5: Training XGBoost scorer...")
    from backtesting.trainer import train_scorer

    train_stats = train_scorer(results)
    if "error" not in train_stats:
        log.info("Scorer trained: %.1f%% accuracy on %d test samples",
                 train_stats["accuracy"] * 100, train_stats["samples_test"])
    else:
        log.warning("Scorer training skipped: %s", train_stats.get("error"))

    # Step 5: Fit calibrator
    log.info("")
    log.info("Step 5/5: Fitting calibration model...")
    from backtesting.calibrator import fit_calibrator

    cal_stats = fit_calibrator(results)
    if "error" not in cal_stats:
        log.info("Calibrator fitted: %.3f error across %d samples",
                 cal_stats["calibration_error"], cal_stats["samples"])
    else:
        log.warning("Calibration skipped: %s", cal_stats.get("error"))

    elapsed = time.time() - start_time
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    log.info("")
    log.info("=" * 60)
    log.info("Backtesting complete in %dm %ds", mins, secs)
    log.info("  Tickers processed: %d / %d", len(prices), len(tickers))
    log.info("  Predictions generated: %d", len(results))
    log.info("  Models saved to: stock_alpha/models/")
    log.info("  Results saved to: backtesting/results/")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
