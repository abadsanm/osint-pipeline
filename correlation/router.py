"""
Signal routing to downstream value engine topics.

Routes correlated signals to stock_alpha and/or product_ideation
topics based on entity type and source composition.
"""

from __future__ import annotations

from correlation.config import CorrelationConfig
from schemas.signal import CorrelatedSignal

# Entity types that indicate financial/market signals
FINANCIAL_ENTITY_TYPES = {"TICKER", "COMPANY", "PERSON"}

# Entity types that indicate product/consumer signals
PRODUCT_ENTITY_TYPES = {"PRODUCT", "COMPANY", "TECHNOLOGY"}

# Sources that strengthen financial routing
FINANCIAL_SOURCES = {"sec_edgar", "reddit", "news"}

# Sources that strengthen product routing
PRODUCT_SOURCES = {"trustpilot", "amazon", "github"}


def route_signal(signal: CorrelatedSignal) -> list[str]:
    """Determine which output topics a signal should be routed to.

    Returns list of topic names.
    """
    topics = [CorrelationConfig.OUTPUT_TOPIC]  # Always goes to main topic
    routes = []

    sources_present = set(signal.source_breakdown.keys())

    # Stock Alpha routing
    if signal.entity_type in FINANCIAL_ENTITY_TYPES:
        routes.append("stock_alpha")
    if sources_present & FINANCIAL_SOURCES:
        if signal.entity_type in {"TICKER", "COMPANY"}:
            routes.append("stock_alpha")

    # Product Ideation routing
    if signal.entity_type in PRODUCT_ENTITY_TYPES:
        if sources_present & PRODUCT_SOURCES:
            routes.append("product_ideation")
    if "trustpilot" in sources_present or "amazon" in sources_present:
        routes.append("product_ideation")

    # Deduplicate routes
    routes = list(set(routes))

    if "stock_alpha" in routes:
        topics.append(CorrelationConfig.STOCK_ALPHA_TOPIC)
    if "product_ideation" in routes:
        topics.append(CorrelationConfig.PRODUCT_IDEATION_TOPIC)

    # Anomaly routing
    if signal.anomaly_flags:
        topics.append(CorrelationConfig.ANOMALY_TOPIC)

    signal.routed_to = routes
    return topics
