"""
Stock Alpha Engine configuration.
"""


class StockAlphaConfig:
    """All tunables in one place. Override via env vars in production."""

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    CONSUMER_GROUP = "stock-alpha-engine"
    INPUT_TOPIC = "osint.correlated.stock_alpha"
    OUTPUT_TOPIC = "stock.alpha.signals"

    # Consumer
    MAX_POLL_TIMEOUT = 1.0
    AUTO_OFFSET_RESET = "earliest"

    # FinBERT
    FINBERT_MODEL = "ProsusAI/finbert"
    MAX_TEXT_LENGTH = 512      # FinBERT token limit
    SENTIMENT_BATCH_SIZE = 16  # Texts per inference batch

    # SVC (Sentiment Volume Convergence)
    SENTIMENT_WINDOW = 5       # Rolling window for sentiment average
    VOLUME_WINDOW = 5          # Rolling window for volume average

    # Technical indicators
    RSI_PERIOD = 14
    MACD_FAST = 12
    MACD_SLOW = 26
    MACD_SIGNAL = 9
    BB_PERIOD = 20
    BB_STD = 2.0
    SMA_PERIODS = [20, 50]
    EMA_PERIODS = [12, 26]

    # Price data
    PRICE_LOOKBACK = "6mo"     # yfinance history period
    PRICE_REFRESH_MINUTES = 30 # How often to refresh price data

    # Signal scoring
    MIN_CONFIDENCE_TO_EMIT = 0.3  # Minimum cross-corr confidence to process
    SIGNAL_LOOKBACK_DAYS = 30     # Days of history for feature computation

    # Evaluation interval
    EVAL_INTERVAL_SECONDS = 300  # 5 minutes


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        StockAlphaConfig.KAFKA_BOOTSTRAP = bootstrap

    model = os.environ.get("FINBERT_MODEL")
    if model:
        StockAlphaConfig.FINBERT_MODEL = model

    lookback = os.environ.get("PRICE_LOOKBACK")
    if lookback:
        StockAlphaConfig.PRICE_LOOKBACK = lookback
