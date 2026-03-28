# CLAUDE.md

Context for Claude Code when working in this repository.

## Project Overview

Sentinel | Social Alpha — OSINT analytics platform. Ingests public data from **24 source connectors**, normalizes via NLP, cross-correlates across platforms, routes to two value engines: **Stock Alpha** (FinBERT + SVC + technicals + ML ensemble + microstructure) and **Product Ideation** (ABSA + gap analysis). Includes ML prediction engine (LightGBM + XGBoost + Prophet), backtesting framework, Sentinel AI chat, and Next.js dashboard.

**Current state:** End-to-end functional — all 24 connectors, normalization layer, cross-correlation engine, both value engines, ML prediction engine, backtesting framework, and Sentinel dashboard frontend with live data.

## UX Design Rules

See `docs/BEHAVIORAL_DESIGN_SPEC.md` for full rationale. These rules govern ALL user-facing code:

### Three Laws (every feature must satisfy all three)
1. **Finite by default, infinite by choice.** Every feed/page has a "you're caught up" state. No infinite scroll. No "load more."
2. **Every reward tied to real-world outcomes.** No likes, followers, engagement metrics. Streaks = consecutive correct predictions only. Reputation = prediction accuracy.
3. **Radical honesty.** Misses shown before hits. Accuracy drops displayed prominently. One-click cancel, no guilt screens.

### Feed & Notification Rules
- Morning briefing: max 8 cards → "caught up" screen. Zero signals = no notification sent.
- Signal feed: only signals above user's confidence threshold. No algorithmic recommendations.
- Notification budget: max 8/day total, max 5 high-confidence alerts/day. Self-contained content.
- Contrarian alerts require 3+ independent data sources. Never fewer.

### Anti-Patterns (NEVER implement these)
- NO notification count badges, "X people viewing," infinite scroll, auto-play, engagement ranking
- NO re-engagement notifications, dark subscription patterns, pull-to-refresh spinners
- Correct prediction: teal pulse 0.5s. Incorrect: coral pulse 0.5s. Caught-up states: gray only.
- "Explain" button on EVERY signal card (not premium-gated). Slide-up panel, no recommendations.

## Development Commands

```bash
# === Infrastructure ===
docker-compose up -d                    # Start Kafka stack
docker-compose ps                       # Verify health (~30s)

# === Python Backend ===
python -m venv .venv
source .venv/bin/activate               # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m normalization.models.download  # Download fastText + spaCy models

# === Source Connectors (each needs Kafka running) ===
# Social / Community
python -m connectors.hacker_news                # No auth needed
python -m connectors.reddit                     # optional REDDIT_CLIENT_ID/SECRET
python -m connectors.producthunt                # optional PRODUCTHUNT_TOKEN
# Consumer Reviews
python -m connectors.trustpilot                 # TRUSTPILOT_DOMAINS required
# Code / Tech
python -m connectors.github                     # optional GITHUB_TOKEN(S)
# Financial Data
python -m connectors.sec_insider                # optional FINNHUB_API_KEY
python -m connectors.financial_news             # optional FINNHUB_API_KEY, ALPHA_VANTAGE_KEY
python -m connectors.unusual_whales             # optional UNUSUAL_WHALES_API_KEY
python -m connectors.fred                       # FRED_API_KEY required
python -m connectors.openinsider                # No auth (scraper)
# Market Data
python -m connectors.binance                    # No auth (WebSocket)
python -m connectors.alpaca_market              # ALPACA_API_KEY/SECRET required
# Macro / Government
python -m connectors.google_trends              # optional GTRENDS_KEYWORDS
python -m connectors.sam_gov                    # No auth
python -m connectors.usaspending                # No auth
python -m connectors.federal_register           # No auth
python -m connectors.sbir                       # No auth
python -m connectors.economic_data              # No auth (BLS)
# News / Newsletters
python -m connectors.techcrunch                 # RSS, no auth
python -m connectors.seeking_alpha              # RSS, no auth
python -m connectors.finviz                     # Scraper, no auth
python -m connectors.techmeme                   # RSS, no auth
python -m connectors.newsletter_feeds           # RSS, no auth

# === Processing Pipeline ===
python -m normalization                 # raw → osint.normalized
python -m correlation                   # normalized → correlated signals

# === Value Engines ===
python -m stock_alpha                   # correlated → predictions (needs FinBERT)
python -m product_ideation              # correlated → product gaps (needs PyABSA)
python -m stock_alpha.tracker           # evaluate predictions against outcomes

# === Backtesting ===
python -m backtesting --start 2024-01-01 --end 2025-03-01 --tickers SPY,QQQ,AAPL,MSFT,NVDA

# === Dashboard ===
cd dashboard && npm install && npm run dev  # http://localhost:3000
```

## Architecture

```
24 Source Connectors → Raw Kafka Topics → Normalization (fastText/spaCy/SimHash/Presidio)
  → osint.normalized → Cross-Correlation Engine
    → osint.correlated.stock_alpha → Stock Alpha Engine (FinBERT + SVC + technicals + ML ensemble)
    → osint.correlated.product_ideation → Product Ideation Engine (ABSA + gaps)
    → osint.correlated.anomalies → Review queue
  → API Server (:8000) → Dashboard (:3000)
```

## Package Map

**`schemas/`** — Pydantic v2 models: `document.py` (OsintDocument, EntityMention, QualitySignals), `signal.py` (CorrelatedSignal), `product_gap.py` (ProductGap), `market_data.py` (OrderBookSnapshot, AggregatedTrade, MarketBar), `prediction.py` (Prediction, PredictionBatch)

**`connectors/`** — 24 async source connectors + shared `kafka_publisher.py`. Categories: social/community (hacker_news, reddit, producthunt), reviews (trustpilot), code/tech (github), financial (sec_insider, financial_news, unusual_whales, fred, openinsider), market data (binance, alpaca_market), macro/government (google_trends, sam_gov, usaspending, federal_register, sbir, economic_data), news/newsletters (techcrunch, seeking_alpha, finviz, techmeme, newsletter_feeds)

**`normalization/`** — NLP pipeline (sync, CPU-bound): `processors/language.py` (fastText), `ner.py` (spaCy + ticker patterns), `dedup.py` (SimHash), `pii.py` (Presidio). `pipeline.py` chains all 4; failures → `osint.deadletter`

**`correlation/`** — Cross-correlation (sync): `window_store.py`, `scoring.py` (diversity 40% + volume anomaly 30% + quality 30%), `anomaly.py` (4 heuristics), `router.py`, `engine.py`

**`stock_alpha/`** — Full prediction engine:
- Core: `sentiment.py` (FinBERT), `svc.py`, `technicals.py`, `microstructure.py`, `order_flow.py`
- ML: `scorer.py` (rule + XGBoost), `ml_scorer.py` (LightGBM + calibration), `feature_store.py` (33 features, SQLite)
- Prediction: `regime.py`, `macro_regime.py` (FRED-based), `forecaster.py` (Prophet + LSTM), `ensemble.py`, `tracker.py`
- Signals: `sentiment_velocity.py`, `insider_scoring.py` (C-suite weighting), `cross_validation.py` (Fleiss' kappa)
- `backtester.py`, `models/` (saved XGBoost, LightGBM, calibrator)

**`backtesting/`** — Walk-forward validation: `data_loader.py`, `simulator.py`, `metrics.py`, `trainer.py`, `calibrator.py`, `__main__.py`

**`product_ideation/`** — Consumer feedback: `absa.py`, `feature_requests.py` (9 patterns), `questions.py` (DBSCAN), `gap_analysis.py`, `engine.py`

**`dashboard/`** — Next.js 14 + Tailwind + D3 + Recharts. Views: Global Pulse (/), Financial Alpha (/alpha/[ticker]), Product Innovation (/innovation). See `dashboard/CLAUDE.md` for design system.

**`api/`** — FastAPI at :8000. Endpoints: /api/health, /stats, /signals, /sectors, /topics, /search, /analyze

## Kafka Topics

| Topic | Part. | Purpose |
|-------|-------|---------|
| `tech.hn.stories` / `tech.hn.comments` | 3/6 | HackerNews |
| `tech.github.events` | 3 | GitHub repos, issues, events |
| `consumer.reviews.trustpilot` / `consumer.reviews.amazon` | 3/3 | Product reviews |
| `finance.sec.insider` / `finance.congress.trades` | 3/3 | SEC filings, congressional trades |
| `finance.reddit.posts` / `finance.reddit.comments` | 6/6 | Reddit financial subreddits |
| `trends.google.interest` | 3 | Google Trends |
| `finance.news.articles` | 3 | Finnhub + Yahoo + Alpha Vantage news |
| `finance.options.flow` | 3 | Unusual Whales options flow |
| `finance.macro.fred` | 3 | FRED macro economic data |
| `finance.insider.trades` | 3 | OpenInsider trades |
| `tech.producthunt.launches` / `tech.producthunt.comments` | 3/3 | ProductHunt |
| `market.binance.depth` / `market.binance.trades` | 3/3 | Binance order book + trades |
| `market.alpaca.bars` | 3 | Alpaca OHLCV + VWAP |
| `osint.normalized` | 6 | Post-NLP documents |
| `osint.deadletter` | 1 | Failed documents |
| `osint.correlated` / `.stock_alpha` / `.product_ideation` / `.anomalies` | 6/3/3/1 | Routed signals |
| `stock.alpha.signals` / `product.ideation.gaps` | 3/3 | Engine outputs |

## Conventions

- All connectors produce `OsintDocument` instances. Call `doc.compute_dedup_hash()` before publishing.
- Kafka key: `entity_id` or `source_id` via `to_kafka_key()`.
- Config pattern: class-level attrs + `load_config_from_env()`.
- Connectors: async (aiohttp). Normalization/correlation/engines: sync (CPU-bound).
- Cross-correlation confidence must accompany all signals — never act on single-source.
- Source weights: FRED(0.95) > SEC/OpenInsider(0.9-1.0) > News/Trends(0.8) > ProductHunt/GitHub(0.7) > TrustPilot(0.6) > HN(0.5) > Reddit(0.4)
- Dashboard: Tailwind-only, chart components accept data as props, D3 in custom hooks.

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `KAFKA_BOOTSTRAP` | No (default: localhost:9092) | Kafka broker address |
| `TRUSTPILOT_DOMAINS` | For trustpilot | Domains to monitor |
| `TRUSTPILOT_API_KEY` | Optional | Enables API mode |
| `GITHUB_TOKEN(S)` | Optional | PAT(s) for rate limits |
| `FINNHUB_API_KEY` | Configured | Congressional trades + market news |
| `EARNINGS_WATCHLIST` | Optional | Override tickers for earnings calendar |
| `CONGRESS_WATCHLIST` | Optional | Tickers for congressional trade monitoring |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Optional | OAuth mode |
| `REDDIT_SUBREDDITS` | Optional | Override default subreddit list |
| `GTRENDS_KEYWORDS` | Optional | Google Trends keywords |
| `SPACY_MODEL` | No (default: en_core_web_lg) | Override spaCy model |
| `FASTTEXT_MODEL_PATH` | Optional | Path to lid.176.bin |
| `FINBERT_MODEL` | No (default: ProsusAI/finbert) | Override FinBERT model |
| `ALPHA_VANTAGE_KEY` | Optional | Alpha Vantage news sentiment |
| `NEWS_WATCHLIST` | Optional | Override ticker watchlist for news |
| `UNUSUAL_WHALES_API_KEY` | Optional | Premium Unusual Whales endpoints |
| `FRED_API_KEY` | **Required for FRED** | Free from fred.stlouisfed.org |
| `FRED_SERIES` | Optional | Override default 13 macro series |
| `PRODUCTHUNT_TOKEN` | Optional | Enables GraphQL API mode |
| `OPENINSIDER_POLL_INTERVAL` | Optional | Override poll interval (default: 900s) |
| `BINANCE_SYMBOLS` | Optional | Override crypto symbols |
| `ALPACA_API_KEY` / `ALPACA_API_SECRET` | **Required for Alpaca** | Market data API credentials |
| `ALPACA_SYMBOLS` | Optional | Override equity symbols |
| `POLL_INTERVAL_MULTIPLIER` | No (default: 1.0) | Multiply all connector poll intervals (e.g. 5.0 = poll 5x less often) |
| `MESSAGE_BUS` | No (default: sqlite) | Message bus backend: `sqlite` (no Docker) or `kafka` (requires Docker) |

## Known Issues

- `docker-compose.yml` init-topics needs longer wait-for-kafka
- `wait_for_kafka()` duplicated across subsystems — extract to shared utility
- Dashboard `npm run build` prerender warnings (dev server works fine)
- `stock_alpha/__main__.py` signal.signal() crashes on Windows — use try/except on asyncio.gather

## Project Context

- Related to AssetBoss project (domain: assetboss.app, admin: mas-admin@assetboss.app)
- Google Drive has folders for Product Management, Marketing, Legal
