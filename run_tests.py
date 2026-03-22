"""Sentinel Platform Comprehensive Test Suite."""

import sys
import json
import time
import os
import importlib
from datetime import datetime, timezone
from collections import defaultdict

results = {"pass": 0, "fail": 0, "skip": 0}
report_lines = []


def log(msg, status="INFO"):
    report_lines.append(f"[{status}] {msg}")
    print(f"[{status}] {msg}")


def PASS(msg):
    results["pass"] += 1
    log(msg, "PASS")


def FAIL(msg):
    results["fail"] += 1
    log(msg, "FAIL")


def SKIP(msg):
    results["skip"] += 1
    log(msg, "SKIP")


log("=" * 60)
log("SENTINEL PLATFORM COMPREHENSIVE TEST")
log(f"Started: {datetime.now(timezone.utc).isoformat()}")
log("=" * 60)

# === PRE-FLIGHT ===
log("")
log("--- PRE-FLIGHT: Infrastructure ---")

try:
    from confluent_kafka.admin import AdminClient
    admin = AdminClient({"bootstrap.servers": "localhost:9092"})
    meta = admin.list_topics(timeout=5)
    topics = set(meta.topics.keys())
    log(f"Kafka running: {len(topics)} topics found")

    expected_topics = [
        "tech.hn.stories", "tech.hn.comments", "tech.github.events",
        "consumer.reviews.trustpilot", "consumer.reviews.amazon",
        "finance.sec.insider", "finance.congress.trades",
        "finance.reddit.posts", "finance.reddit.comments",
        "trends.google.interest", "osint.normalized", "osint.deadletter",
        "osint.correlated", "osint.correlated.stock_alpha",
        "osint.correlated.product_ideation", "osint.correlated.anomalies",
        "stock.alpha.signals", "product.ideation.gaps",
        "stock.alpha.predictions", "stock.alpha.outcomes",
    ]
    missing = [t for t in expected_topics if t not in topics]
    if missing:
        FAIL(f"Missing topics: {missing}")
    else:
        PASS(f"All {len(expected_topics)} expected Kafka topics exist")
except Exception as e:
    FAIL(f"Kafka connection: {e}")

# === TEST 1: SCHEMAS ===
log("")
log("--- TEST 1: Schema Validation ---")

try:
    from schemas.document import OsintDocument, SourcePlatform, ContentType, QualitySignals
    doc = OsintDocument(
        source=SourcePlatform.HACKER_NEWS, source_id="12345",
        content_type=ContentType.STORY, title="Test", content_text="Test content.",
        created_at=int(time.time()),
        quality_signals=QualitySignals(score=42, engagement_count=10),
    )
    doc.compute_dedup_hash()
    assert doc.to_kafka_key()
    json_str = doc.to_kafka_value()
    assert json.loads(json_str)
    rt = OsintDocument.model_validate_json(json_str)
    assert rt.source_id == "12345"
    PASS("OsintDocument schema")
except Exception as e:
    FAIL(f"OsintDocument: {e}")

try:
    from schemas.signal import CorrelatedSignal
    sig = CorrelatedSignal(
        entity_text="TEST", entity_type="TICKER",
        confidence_score=0.8, total_mentions=15,
        unique_sources=3, source_breakdown={},
    )
    assert sig.to_kafka_key()
    assert json.loads(sig.to_kafka_value())
    PASS("CorrelatedSignal schema")
except Exception as e:
    FAIL(f"CorrelatedSignal: {e}")

try:
    from schemas.product_gap import ProductGap, GapType
    gap = ProductGap(entity_id="test", aspect="battery", gap_type=GapType.NEGATIVE_SENTIMENT)
    assert gap.to_kafka_key()
    assert json.loads(gap.to_kafka_value())
    PASS("ProductGap schema")
except Exception as e:
    FAIL(f"ProductGap: {e}")

try:
    from schemas.prediction import Prediction, PredictionBatch
    pred = Prediction(
        ticker="AAPL", direction="bullish", magnitude=2.5, confidence=0.73,
        raw_score=0.65, time_horizon_hours=24, decay_rate=24.0, regime="trending_up",
        contributing_signals=[{"source": "test", "value": 0.8}], model_version="v1",
    )
    assert pred.prediction_id
    conf = pred.current_confidence()
    assert 0 <= conf <= 1
    assert json.loads(pred.to_kafka_value())
    PASS(f"Prediction schema (decay_conf={conf:.3f})")
except ImportError:
    SKIP("schemas/prediction.py not found")
except Exception as e:
    FAIL(f"Prediction: {e}")

try:
    from schemas.market_data import OrderBookSnapshot, AggregatedTrade, MarketBar
    bar = MarketBar(symbol="AAPL", timestamp=datetime.now(timezone.utc),
                    open=185, high=186, low=184, close=185.5, volume=1000)
    assert bar.to_kafka_key() == "AAPL"
    assert json.loads(bar.to_kafka_value())
    PASS("MarketData schemas")
except Exception as e:
    FAIL(f"MarketData: {e}")

# === TEST 2: CONNECTORS ===
log("")
log("--- TEST 2: Connectors ---")

connectors_list = [
    "connectors.kafka_publisher", "connectors.hacker_news", "connectors.trustpilot",
    "connectors.github", "connectors.sec_insider", "connectors.reddit",
    "connectors.google_trends", "connectors.financial_news", "connectors.unusual_whales",
    "connectors.fred", "connectors.openinsider", "connectors.producthunt",
    "connectors.binance", "connectors.alpaca_market", "connectors.bluesky",
    "connectors.stocktwits",
]
for mod_name in connectors_list:
    try:
        importlib.import_module(mod_name)
        PASS(mod_name)
    except Exception as e:
        FAIL(f"{mod_name}: {e}")

# === TEST 3: NORMALIZATION ===
log("")
log("--- TEST 3: Normalization ---")

for mod_name in ["normalization.processors.ner", "normalization.processors.dedup",
                  "normalization.config", "normalization.pipeline"]:
    try:
        importlib.import_module(mod_name)
        PASS(mod_name)
    except Exception as e:
        FAIL(f"{mod_name}: {e}")

# === TEST 4: CORRELATION ===
log("")
log("--- TEST 4: Correlation ---")

for mod_name in ["correlation.window_store", "correlation.scoring", "correlation.anomaly",
                  "correlation.router", "correlation.engine", "correlation.config"]:
    try:
        importlib.import_module(mod_name)
        PASS(mod_name)
    except Exception as e:
        FAIL(f"{mod_name}: {e}")

# === TEST 5: STOCK ALPHA ===
log("")
log("--- TEST 5: Stock Alpha Engine ---")

try:
    from stock_alpha.sentiment import FinBERTAnalyzer
    PASS("FinBERTAnalyzer imports")
except Exception as e:
    FAIL(f"FinBERTAnalyzer: {e}")

try:
    from stock_alpha.svc import SVCComputer, SentimentDataPoint
    svc = SVCComputer()
    for i in range(20):
        svc.add_data_point("TEST", SentimentDataPoint(
            timestamp=datetime.now(timezone.utc), sentiment_score=0.5 + i * 0.02, mention_count=10 + i))
    r = svc.compute("TEST")
    assert r and r.ticker == "TEST"
    PASS(f"SVC computation (svc={r.svc_value:.4f})")
except Exception as e:
    FAIL(f"SVC: {e}")

try:
    from stock_alpha.technicals import PriceDataProvider, compute_technicals
    provider = PriceDataProvider()
    df = provider.get_prices("SPY")
    if df is not None and not df.empty:
        tech = compute_technicals(df)
        assert "rsi" in tech.columns
        rsi_val = tech["rsi"].iloc[-1]
        PASS(f"Technicals ({len(tech)} rows, RSI={rsi_val:.1f})")
    else:
        SKIP("SPY price data unavailable")
except Exception as e:
    FAIL(f"Technicals: {e}")

try:
    from stock_alpha.scorer import RuleBasedScorer
    scorer = RuleBasedScorer()
    alpha = scorer.score(ticker="TEST", sentiment_score=0.6, svc=None,
                         technicals=None, correlation_confidence=0.7, contributing_signals=15)
    assert alpha.signal_direction in ("bullish", "bearish", "neutral")
    PASS(f"Scorer ({alpha.signal_direction}, score={alpha.signal_score:.3f})")
except Exception as e:
    FAIL(f"Scorer: {e}")

try:
    from stock_alpha.engine import StockAlphaEngine
    engine = StockAlphaEngine()
    PASS("StockAlphaEngine instantiates")
except Exception as e:
    FAIL(f"StockAlphaEngine: {e}")

# === TEST 6: PREDICTION ENGINE ===
log("")
log("--- TEST 6: Prediction Engine ---")

pred_modules = {
    "Regime detection": "stock_alpha.regime",
    "Forecaster": "stock_alpha.forecaster",
    "Ensemble": "stock_alpha.ensemble",
    "Tracker": "stock_alpha.tracker",
    "Microstructure": "stock_alpha.microstructure",
    "Order flow": "stock_alpha.order_flow",
    "Sentiment velocity": "stock_alpha.sentiment_velocity",
    "Insider scoring": "stock_alpha.insider_scoring",
    "Macro regime": "stock_alpha.macro_regime",
    "Feature store": "stock_alpha.feature_store",
    "ML scorer": "stock_alpha.ml_scorer",
    "Cross validation": "stock_alpha.cross_validation",
    "Backtester": "stock_alpha.backtester",
}
for name, mod in pred_modules.items():
    try:
        importlib.import_module(mod)
        PASS(name)
    except Exception as e:
        FAIL(f"{name}: {e}")

try:
    from stock_alpha.regime import classify_regime
    regime = classify_regime(ticker="SPY")
    PASS(f"Regime classify (SPY={regime.regime}, conf={regime.confidence:.2f})")
except Exception as e:
    FAIL(f"Regime classify: {e}")

try:
    from backtesting.data_loader import load_price_data
    from backtesting.simulator import run_simulation
    from backtesting.metrics import print_summary
    from backtesting.trainer import train_scorer
    from backtesting.calibrator import fit_calibrator
    PASS("Backtesting package (all modules)")
except Exception as e:
    FAIL(f"Backtesting: {e}")

# === TEST 7: PRODUCT IDEATION ===
log("")
log("--- TEST 7: Product Ideation ---")

for mod_name in ["product_ideation.absa", "product_ideation.feature_requests",
                  "product_ideation.questions", "product_ideation.gap_analysis",
                  "product_ideation.engine"]:
    try:
        importlib.import_module(mod_name)
        PASS(mod_name)
    except Exception as e:
        FAIL(f"{mod_name}: {e}")

# === TEST 8: API SERVER ===
log("")
log("--- TEST 8: API Server ---")

try:
    from api.server import app
    routes = [r.path for r in app.routes if hasattr(r, "path")]
    expected_endpoints = [
        "/api/health", "/api/stats", "/api/signals", "/api/sectors",
        "/api/search", "/api/analyze", "/api/alpha/{ticker}",
        "/api/innovation", "/api/settings", "/api/research",
        "/api/alerts", "/api/predictions", "/api/predictions/stats",
        "/api/predictions/outcomes", "/api/backtesting/results",
        "/api/backtest",
    ]
    for ep in expected_endpoints:
        if ep in routes:
            PASS(f"Endpoint {ep}")
        else:
            FAIL(f"Endpoint {ep} NOT registered")
except Exception as e:
    FAIL(f"API server: {e}")

# === TEST 9: DASHBOARD ===
log("")
log("--- TEST 9: Dashboard ---")

pages = [
    "dashboard/src/app/page.tsx",
    "dashboard/src/app/alpha/[ticker]/page.tsx",
    "dashboard/src/app/innovation/page.tsx",
    "dashboard/src/app/settings/page.tsx",
    "dashboard/src/app/predictions/page.tsx",
    "dashboard/src/app/backtesting/page.tsx",
    "dashboard/src/app/layout.tsx",
]
for p in pages:
    if os.path.exists(p):
        PASS(p)
    else:
        FAIL(f"{p} MISSING")

components = [
    "dashboard/src/components/Header.tsx", "dashboard/src/components/Sidebar.tsx",
    "dashboard/src/components/HeatSphere.tsx", "dashboard/src/components/SignalFeed.tsx",
    "dashboard/src/components/ChartCards.tsx", "dashboard/src/components/AnalysisModal.tsx",
    "dashboard/src/components/SourceTicker.tsx", "dashboard/src/components/ResearchPanel.tsx",
    "dashboard/src/components/InfoTooltip.tsx",
]
for c in components:
    if os.path.exists(c):
        PASS(c)
    else:
        FAIL(f"{c} MISSING")

# === TEST 10: E2E KAFKA ===
log("")
log("--- TEST 10: E2E Kafka Round-Trip ---")

try:
    from confluent_kafka import Producer, Consumer, KafkaError
    producer = Producer({"bootstrap.servers": "localhost:9092"})
    test_doc = json.dumps({
        "doc_id": "test-e2e-001", "source": "hacker_news",
        "source_id": "99999999", "content_type": "story",
        "title": "E2E Test", "content_text": "E2E test.",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"test": True},
    })
    producer.produce("tech.hn.stories", key="test", value=test_doc)
    producer.flush(5)

    consumer = Consumer({
        "bootstrap.servers": "localhost:9092",
        "group.id": f"e2e-test-{int(time.time())}",
        "auto.offset.reset": "latest",
    })
    consumer.subscribe(["tech.hn.stories"])
    found = False
    deadline = time.time() + 10
    while time.time() < deadline:
        msg = consumer.poll(1.0)
        if msg and not msg.error():
            data = json.loads(msg.value())
            if data.get("doc_id") == "test-e2e-001":
                found = True
                break
    consumer.close()
    if found:
        PASS("E2E Kafka round-trip")
    else:
        FAIL("E2E round-trip: test doc not received in 10s")
except Exception as e:
    FAIL(f"E2E round-trip: {e}")

# === TEST 11: PERSISTENCE ===
log("")
log("--- TEST 11: Persistence ---")

try:
    from api.persistence import SentinelDB
    db = SentinelDB("data/test_verify.db")
    db.set_setting("test_key", "test_value")
    assert db.get_setting("test_key") == "test_value"
    db.upsert_entity("TEST", {"id": "TEST", "label": "Test", "volume": 1, "sources": {}})
    e = db.get_entity("TEST")
    assert e and e["id"] == "TEST"
    db.close()
    os.remove("data/test_verify.db")
    PASS("SQLite persistence (read/write/query)")
except Exception as e:
    FAIL(f"Persistence: {e}")

# === SUMMARY ===
log("")
log("=" * 60)
log("SENTINEL PLATFORM TEST REPORT")
log("=" * 60)
log(f"PASSED:  {results['pass']}")
log(f"FAILED:  {results['fail']}")
log(f"SKIPPED: {results['skip']}")
log(f"TOTAL:   {results['pass'] + results['fail'] + results['skip']}")
log("=" * 60)

# Save report
os.makedirs("test-results", exist_ok=True)
report_path = f"test-results/test-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
with open(report_path, "w") as f:
    f.write("# Sentinel Platform Test Report\n\n")
    f.write(f"**Date:** {datetime.now(timezone.utc).isoformat()}\n\n")
    f.write(f"**Results:** {results['pass']} passed, {results['fail']} failed, {results['skip']} skipped\n\n")
    f.write("## Detailed Results\n\n```\n")
    for line in report_lines:
        f.write(line + "\n")
    f.write("```\n")

print(f"\nReport saved: {report_path}")
sys.exit(1 if results["fail"] > 0 else 0)
