"""
Cross-correlation engine configuration.
"""


class CorrelationConfig:
    """All tunables in one place. Override via env vars in production."""

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    CONSUMER_GROUP = "correlation-engine"
    INPUT_TOPIC = "osint.normalized"

    OUTPUT_TOPIC = "osint.correlated"
    STOCK_ALPHA_TOPIC = "osint.correlated.stock_alpha"
    PRODUCT_IDEATION_TOPIC = "osint.correlated.product_ideation"
    ANOMALY_TOPIC = "osint.correlated.anomalies"

    # Consumer
    MAX_POLL_TIMEOUT = 1.0
    AUTO_OFFSET_RESET = "earliest"

    # Window parameters
    WINDOW_SIZE_MINUTES = 60       # Each window spans 1 hour
    WINDOW_SLIDE_MINUTES = 5       # Evaluate every 5 minutes
    WINDOW_RETENTION_HOURS = 24    # Keep history for baseline
    MIN_MENTIONS_TO_EMIT = 3       # Minimum mentions to emit a signal
    MIN_SOURCES_FOR_STRONG = 2     # Need 2+ platforms for strong signal

    # Source reliability weights (0.0-1.0)
    SOURCE_WEIGHTS = {
        "sec_edgar": 1.0,
        "news": 0.8,       # Google Trends
        "github": 0.7,
        "trustpilot": 0.6,
        "hacker_news": 0.5,
        "reddit": 0.4,
        "amazon": 0.5,
        "twitter_x": 0.3,
        "bluesky": 0.3,
    }

    # Confidence scoring weights
    WEIGHT_SOURCE_DIVERSITY = 0.4
    WEIGHT_VOLUME_ANOMALY = 0.3
    WEIGHT_QUALITY = 0.3

    # Anomaly detection thresholds
    ANOMALY_LOW_ACCOUNT_AGE_DAYS = 30
    ANOMALY_LOW_ACCOUNT_AGE_FRACTION = 0.4
    ANOMALY_BURST_FRACTION = 0.7
    ANOMALY_BURST_WINDOW_MINUTES = 5
    ANOMALY_CROSS_PLATFORM_SYNC_MINUTES = 2
    ANOMALY_TEMPLATE_SIMHASH_DISTANCE = 5
    ANOMALY_TEMPLATE_FRACTION = 0.3

    # Anomaly confidence penalty
    ANOMALY_PENALTY = 0.6  # Multiply confidence by this when anomalies detected


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        CorrelationConfig.KAFKA_BOOTSTRAP = bootstrap

    group = os.environ.get("CORRELATION_CONSUMER_GROUP")
    if group:
        CorrelationConfig.CONSUMER_GROUP = group

    window = os.environ.get("CORRELATION_WINDOW_MINUTES")
    if window:
        CorrelationConfig.WINDOW_SIZE_MINUTES = int(window)

    slide = os.environ.get("CORRELATION_SLIDE_MINUTES")
    if slide:
        CorrelationConfig.WINDOW_SLIDE_MINUTES = int(slide)

    min_mentions = os.environ.get("CORRELATION_MIN_MENTIONS")
    if min_mentions:
        CorrelationConfig.MIN_MENTIONS_TO_EMIT = int(min_mentions)
