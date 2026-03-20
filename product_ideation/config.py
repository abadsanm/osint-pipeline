"""
Product Ideation Engine configuration.
"""


class ProductIdeationConfig:
    """All tunables in one place. Override via env vars in production."""

    # Kafka
    KAFKA_BOOTSTRAP = "localhost:9092"
    CONSUMER_GROUP = "product-ideation-engine"
    INPUT_TOPIC = "osint.correlated.product_ideation"
    OUTPUT_TOPIC = "product.ideation.gaps"

    # Consumer
    MAX_POLL_TIMEOUT = 1.0
    AUTO_OFFSET_RESET = "earliest"

    # ABSA
    ABSA_MODEL = "multilingual"  # PyABSA checkpoint name
    MIN_ASPECT_CONFIDENCE = 0.5

    # Question clustering
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # sentence-transformers model
    CLUSTER_EPS = 0.3              # DBSCAN epsilon
    CLUSTER_MIN_SAMPLES = 2        # Minimum cluster size

    # Gap analysis
    MIN_REVIEWS_FOR_GAP = 3        # Minimum mentions before flagging a gap
    NEGATIVE_THRESHOLD = 0.6       # Fraction of negative sentiment to flag
    MIN_FEATURE_REQUESTS = 2       # Minimum identical requests to surface

    # Evaluation
    EVAL_INTERVAL_SECONDS = 600    # 10 minutes
    MAX_REVIEWS_IN_MEMORY = 5000   # Cap for review accumulation


def load_config_from_env():
    """Load configuration from environment variables."""
    import os

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        ProductIdeationConfig.KAFKA_BOOTSTRAP = bootstrap

    absa_model = os.environ.get("ABSA_MODEL")
    if absa_model:
        ProductIdeationConfig.ABSA_MODEL = absa_model

    embedding = os.environ.get("EMBEDDING_MODEL")
    if embedding:
        ProductIdeationConfig.EMBEDDING_MODEL = embedding
