"""
Normalization layer configuration.
"""


class NormalizationConfig:
    """All tunables in one place. Override via env vars in production."""

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    CONSUMER_GROUP = "normalization-pipeline"

    INPUT_TOPICS = [
        "tech.hn.stories",
        "tech.hn.comments",
        "tech.github.events",
        "consumer.reviews.trustpilot",
        "consumer.reviews.amazon",
        "finance.sec.insider",
        "finance.congress.trades",
        "finance.reddit.posts",
        "finance.reddit.comments",
    ]

    OUTPUT_TOPIC = "osint.normalized"
    DEADLETTER_TOPIC = "osint.deadletter"

    # Consumer settings
    MAX_POLL_TIMEOUT = 1.0  # seconds
    AUTO_OFFSET_RESET = "earliest"

    # Models
    SPACY_MODEL = "en_core_web_lg"
    FASTTEXT_MODEL_PATH = "models/lid.176.bin"

    # SimHash
    SIMHASH_THRESHOLD = 3       # Max hamming distance for near-duplicate
    SIMHASH_INDEX_SIZE = 50000  # Max entries in dedup index

    # Presidio
    PRESIDIO_ENTITIES = [
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
        "US_SSN",
        "IP_ADDRESS",
    ]

    # NER
    TICKER_PATTERN = r"\$[A-Z]{1,5}\b"  # e.g., $AAPL, $TSLA


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        NormalizationConfig.KAFKA_BOOTSTRAP = bootstrap

    group = os.environ.get("CONSUMER_GROUP")
    if group:
        NormalizationConfig.CONSUMER_GROUP = group

    spacy_model = os.environ.get("SPACY_MODEL")
    if spacy_model:
        NormalizationConfig.SPACY_MODEL = spacy_model

    ft_path = os.environ.get("FASTTEXT_MODEL_PATH")
    if ft_path:
        NormalizationConfig.FASTTEXT_MODEL_PATH = ft_path
