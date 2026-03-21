# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A multi-pillar OSINT analytics platform that ingests public data from multiple sources, normalizes and cross-correlates it, then routes signals to two downstream value engines: **Stock Alpha** (FinBERT sentiment + SVC metrics + technical indicators) and **Product Ideation** (ABSA + gap analysis on consumer reviews). An ethical compliance layer (GDPR/CCPA/EU AI Act, PII scrubbing via Presidio) wraps the entire system. A **Sentinel dashboard** (Next.js) provides the visualization frontend.

**Current state:** The entire platform is built end-to-end — 6 connectors, normalization layer, cross-correlation engine, both value engines, and the Sentinel dashboard frontend.

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

# Run connectors (each requires Kafka)
python -m connectors.hacker_news
python -m connectors.trustpilot          # needs TRUSTPILOT_DOMAINS
python -m connectors.github              # needs GITHUB_TOKEN(S)
python -m connectors.sec_insider         # optional FINNHUB_API_KEY
python -m connectors.reddit              # optional REDDIT_CLIENT_ID/SECRET
python -m connectors.google_trends       # optional GTRENDS_KEYWORDS

# Run processing layers
python -m normalization                  # raw topics → osint.normalized
python -m correlation                    # osint.normalized → correlated signals

# Run value engines
python -m stock_alpha                    # correlated → stock alpha signals (needs FinBERT)
python -m product_ideation               # correlated → product gaps (needs PyABSA)

# Verify data flow
docker exec osint-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic tech.hn.stories --from-beginning --max-messages 5

# Kafka UI at http://localhost:8080

# === Dashboard Frontend ===
cd dashboard
npm install
npm run dev                              # http://localhost:3000
npm run build                            # Production build
npx next lint                            # Lint check
```

## Architecture

### Data Flow

```
Source Connectors → Raw Kafka Topics → Normalization (fastText/spaCy/SimHash/Presidio)
  → osint.normalized → Cross-Correlation Engine
    → osint.correlated.stock_alpha → Stock Alpha Engine (FinBERT + SVC + technicals)
    → osint.correlated.product_ideation → Product Ideation Engine (ABSA + gap analysis)
    → osint.correlated.anomalies → Review/alerting
  → Sentinel Dashboard (Next.js)
```

### Package Structure

- **`schemas/`** — Pydantic models shared across all subsystems
  - `document.py` — `OsintDocument`, `EntityMention`, `QualitySignals` — universal document format
  - `signal.py` — `CorrelatedSignal`, `SourceContribution` — cross-correlation output
  - `product_gap.py` — `ProductGap`, `ProductIdeationReport` — product ideation output

- **`connectors/`** — Source connectors (async, aiohttp + confluent-kafka Producer)
  - `kafka_publisher.py` — Shared `KafkaPublisher` wrapper used by all connectors
  - `hacker_news.py` — Firebase polling + Algolia keyword search
  - `trustpilot.py` — Official API (cursor pagination) + web scrape fallback
  - `github.py` — GraphQL search + REST events, PAT token pool with rate-limit-aware rotation
  - `sec_insider.py` — EDGAR EFTS search + ownership XML parsing (Forms 3/4/5), Finnhub congressional trades
  - `reddit.py` — OAuth API + JSON fallback, financial subreddits
  - `google_trends.py` — pytrends: interest over time, trending searches, related queries

- **`normalization/`** — NLP pipeline (sync Kafka Consumer, CPU-bound)
  - `processors/language.py` — fastText lid.176 language detection
  - `processors/ner.py` — spaCy NER with ticker pattern matching + financial entity mapping
  - `processors/dedup.py` — SimHash 64-bit fingerprinting with near-duplicate detection
  - `processors/pii.py` — Microsoft Presidio PII scrubbing (shares spaCy model with NER)
  - `pipeline.py` — Chains all 4 processors; failed docs → `osint.deadletter`

- **`correlation/`** — Cross-correlation engine (sync Kafka Consumer)
  - `window_store.py` — Sliding window entity store with deque-based MentionEvent tracking
  - `scoring.py` — 3-factor confidence: source diversity (40%), volume anomaly z-score (30%), quality (30%)
  - `anomaly.py` — 4 heuristics: account age clustering, temporal bursts, cross-platform sync, template content
  - `router.py` — Routes signals to stock_alpha / product_ideation / anomalies topics
  - `engine.py` — Orchestrates ingestion, periodic window evaluation, signal emission

- **`stock_alpha/`** — Stock Alpha value engine
  - `sentiment.py` — FinBERT (ProsusAI/finbert) financial sentiment with batch inference
  - `svc.py` — Sentiment Volume Convergence metric (sentiment_shift x volume_change)
  - `technicals.py` — yfinance OHLCV + pandas-ta indicators (RSI, MACD, BB, SMA/EMA, ATR, OBV)
  - `scorer.py` — Rule-based weighted scorer: sentiment 30% + SVC 20% + technicals 30% + correlation 20%
  - `engine.py` — Orchestrates FinBERT → SVC → technicals → scoring per ticker

- **`product_ideation/`** — Product Ideation value engine
  - `absa.py` — PyABSA aspect extraction with spaCy noun-chunk fallback
  - `feature_requests.py` — 9 regex patterns (wish/want/should/missing/why_cant/etc.)
  - `questions.py` — Question detection + sentence-transformers embedding + DBSCAN clustering
  - `gap_analysis.py` — Aggregates into ranked ProductGap objects with opportunity scores
  - `engine.py` — Orchestrates ABSA + feature mining + question clustering → reports

- **`dashboard/`** — Sentinel frontend (Next.js 14, TypeScript, Tailwind, D3.js, Recharts)
  - `src/app/page.tsx` — Global Pulse: D3 heat-sphere, signal feed, 4 chart cards, timeframe selector
  - `src/app/alpha/[ticker]/page.tsx` — Financial Alpha: price + sentiment overlay, prediction ghost, news panel
  - `src/app/innovation/page.tsx` — Product Innovation: friction board, gap map scatter plot
  - `src/components/` — Sidebar, Header, HeatSphere, SignalFeed, ChartCards, TimeframeSelector
  - `data/` — Mock JSON data files for development
  - See `dashboard/CLAUDE.md` for frontend-specific design system and build instructions

### Kafka Topics

| Topic | Partitions | Purpose |
|-------|-----------|---------|
| `tech.hn.stories` / `tech.hn.comments` | 3/6 | HackerNews stories and comments |
| `tech.github.events` | 3 | GitHub repos, issues, events |
| `consumer.reviews.trustpilot` / `consumer.reviews.amazon` | 3/3 | Product reviews |
| `finance.sec.insider` / `finance.congress.trades` | 3/3 | SEC filings and congressional trades |
| `finance.reddit.posts` / `finance.reddit.comments` | 6/6 | Reddit financial subreddit content |
| `trends.google.interest` | 3 | Google Trends data |
| `osint.normalized` | 6 | Post-NLP normalized documents |
| `osint.deadletter` | 1 | Failed documents (infinite retention) |
| `osint.correlated` | 6 | All correlated signals |
| `osint.correlated.stock_alpha` | 3 | Signals routed to Stock Alpha Engine |
| `osint.correlated.product_ideation` | 3 | Signals routed to Product Ideation Engine |
| `osint.correlated.anomalies` | 1 | Coordinated inauthenticity flags |
| `stock.alpha.signals` | 3 | Scored stock alpha signals |
| `product.ideation.gaps` | 3 | Product opportunity reports |

## Conventions

- All connectors produce `OsintDocument` instances (never raw dicts to Kafka)
- `dedup_hash` must be computed before publishing (`doc.compute_dedup_hash()`)
- Kafka messages use `entity_id` or `source_id` as partition key (see `to_kafka_key()`)
- Every subsystem follows the Config class pattern: class-level attributes, `load_config_from_env()` function
- Connectors are async (aiohttp); normalization, correlation, and value engines are sync (CPU-bound)
- Cross-correlation confidence scores must accompany all signals — never act on single-source signals
- Source reliability weights: SEC (1.0) > News/Trends (0.8) > GitHub (0.7) > TrustPilot (0.6) > HN (0.5) > Reddit (0.4)
- Dashboard: Tailwind-only styling, all chart components accept data as props, D3 logic in custom hooks
- No sentiment model guarantees profitable trades — these are research tools for informational edges

## Tech Stack

### Python Backend
- **Python 3.12+**, **Pydantic v2** for data validation
- **confluent-kafka** for Kafka producer/consumer
- **aiohttp** for async HTTP in connectors
- **spaCy** (en_core_web_lg) for NER, **fastText** (lid.176) for language detection
- **Microsoft Presidio** for PII detection/anonymization
- **pytrends** for Google Trends
- **transformers + torch** (ProsusAI/finbert) for financial sentiment
- **yfinance + pandas-ta** for stock price data and technical indicators
- **PyABSA** for aspect-based sentiment, **sentence-transformers** for question clustering
- **Docker / docker-compose** (Confluent Kafka v7.6.1)

### Dashboard Frontend
- **Next.js 14** (App Router, TypeScript strict)
- **Tailwind CSS** (dark mode, custom design tokens)
- **D3.js** for force-directed heat-sphere visualization
- **Recharts** for standard charts (line, bar, scatter, composed)
- **Lucide React** for icons, **Inter + Roboto Mono** fonts

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `KAFKA_BOOTSTRAP` | All | Kafka broker address (default: localhost:9092) |
| `TRUSTPILOT_DOMAINS` | trustpilot | Comma-separated domains to monitor |
| `TRUSTPILOT_API_KEY` | trustpilot | Official API key (enables API mode) |
| `GITHUB_TOKEN(S)` | github | PAT token(s), comma-separated for rotation |
| `FINNHUB_API_KEY` | sec_insider | Enables congressional trade tracking |
| `CONGRESS_WATCHLIST` | sec_insider | Tickers to monitor for congressional trades |
| `REDDIT_CLIENT_ID/SECRET` | reddit | OAuth credentials (enables OAuth mode) |
| `REDDIT_SUBREDDITS` | reddit | Override default subreddit list |
| `GTRENDS_KEYWORDS` | google_trends | Keywords to track interest for |
| `SPACY_MODEL` | normalization | Override spaCy model (default: en_core_web_lg) |
| `FASTTEXT_MODEL_PATH` | normalization | Path to lid.176.bin |
| `FINBERT_MODEL` | stock_alpha | Override FinBERT model (default: ProsusAI/finbert) |

## Known Issues

- `docker-compose.yml` `init-topics` container needs a longer wait-for-kafka before creating topics
- `docker-compose.yml` has deprecated `version` attribute (remove it)
- `wait_for_kafka()` is duplicated across every subsystem — should extract to shared utility
- Dashboard `npm run build` has prerender warnings for client-only pages (dev server works fine)

## Project Context

- Related to AssetBoss project (domain: assetboss.app, admin: mas-admin@assetboss.app)
- Google Drive has folders for Product Management, Marketing, Legal
