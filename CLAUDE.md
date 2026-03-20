# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A multi-pillar OSINT analytics platform that ingests public data from multiple sources, normalizes and cross-correlates it, then routes signals to two downstream value engines: **Stock Alpha** (FinBERT sentiment + time-series forecasting) and **Product Ideation** (ABSA + gap analysis on consumer reviews). An ethical compliance layer (GDPR/CCPA/EU AI Act, PII scrubbing via Presidio) wraps the entire system.

**Current state:** The foundation pipeline is fully built — all 6 connectors, normalization layer, and cross-correlation engine are operational. Value engines (Stock Alpha + Product Ideation) are the next phase.

## Development Commands

```bash
# Start Kafka stack (Zookeeper, Kafka, Schema Registry, Kafka UI, topic init)
docker-compose up -d

# Verify services are healthy (~30s after startup)
docker-compose ps

# Set up Python environment
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Download NLP models for normalization layer
python -m normalization.models.download

# Run individual connectors (each requires Kafka running)
python -m connectors.hacker_news
python -m connectors.trustpilot        # needs TRUSTPILOT_DOMAINS env var
python -m connectors.github            # needs GITHUB_TOKEN(S) env var
python -m connectors.sec_insider       # optional FINNHUB_API_KEY for congressional trades
python -m connectors.reddit            # optional REDDIT_CLIENT_ID/SECRET for OAuth
python -m connectors.google_trends     # optional GTRENDS_KEYWORDS env var

# Run normalization layer (consumes from all raw topics → osint.normalized)
python -m normalization

# Run cross-correlation engine (consumes osint.normalized → correlated signals)
python -m correlation

# Verify data flow via CLI
docker exec osint-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic tech.hn.stories \
  --from-beginning --max-messages 5

# Kafka UI at http://localhost:8080
```

No formal test suite yet — tests are run inline during development.

## Architecture

### Data Flow

```
Source Connectors → Raw Kafka Topics → Normalization (fastText/spaCy/SimHash/Presidio)
  → osint.normalized → Cross-Correlation Engine → osint.correlated.{stock_alpha,product_ideation,anomalies}
  → Value Engines (TODO)
```

### Package Structure

- **`schemas/`** — Pydantic models shared across all subsystems
  - `document.py` — `OsintDocument`, `EntityMention`, `QualitySignals` — the universal document format
  - `signal.py` — `CorrelatedSignal`, `SourceContribution` — cross-correlation output format

- **`connectors/`** — Source connectors (async, aiohttp + confluent-kafka Producer)
  - `kafka_publisher.py` — Shared `KafkaPublisher` wrapper used by all connectors
  - `hacker_news.py` — Firebase polling + Algolia keyword search (2 concurrent modes)
  - `trustpilot.py` — Official API (cursor pagination) + web scrape fallback (__NEXT_DATA__)
  - `github.py` — GraphQL trending search + REST events stream, PAT token pool with rate-limit-aware rotation
  - `sec_insider.py` — EDGAR EFTS filing search + XML parsing (Forms 3/4/5), Finnhub congressional trades
  - `reddit.py` — OAuth API + JSON fallback, financial subreddits (WSB, r/stocks, r/options, r/investing)
  - `google_trends.py` — pytrends: interest over time, trending searches, related queries

- **`normalization/`** — NLP pipeline (sync confluent-kafka Consumer, CPU-bound)
  - `processors/language.py` — fastText lid.176 language detection
  - `processors/ner.py` — spaCy NER with ticker pattern matching + financial entity mapping
  - `processors/dedup.py` — SimHash 64-bit fingerprinting with sliding window near-duplicate detection
  - `processors/pii.py` — Microsoft Presidio PII scrubbing (shares spaCy model with NER)
  - `pipeline.py` — Chains all 4 processors; failed docs route to `osint.deadletter`
  - `models/download.py` — Downloads fastText lid.176.bin + spaCy en_core_web_lg

- **`correlation/`** — Cross-correlation engine (sync confluent-kafka Consumer)
  - `window_store.py` — Sliding window entity store with deque-based MentionEvent tracking
  - `scoring.py` — 3-factor confidence: source diversity (40%), volume anomaly z-score (30%), quality (30%)
  - `anomaly.py` — 4 heuristics: account age clustering, temporal bursts, cross-platform sync, template content
  - `router.py` — Routes signals to stock_alpha / product_ideation / anomalies topics
  - `engine.py` — Orchestrates ingestion, periodic evaluation, scoring, anomaly detection

### Kafka Topics

| Topic | Partitions | Purpose |
|-------|-----------|---------|
| `tech.hn.stories` / `tech.hn.comments` | 3/6 | HackerNews stories and comments |
| `tech.github.events` | 3 | GitHub trending repos, issues, events |
| `consumer.reviews.trustpilot` / `consumer.reviews.amazon` | 3/3 | Product reviews |
| `finance.sec.insider` / `finance.congress.trades` | 3/3 | SEC filings and congressional trades |
| `finance.reddit.posts` / `finance.reddit.comments` | 6/6 | Reddit financial subreddit content |
| `trends.google.interest` | 3 | Google Trends data |
| `osint.normalized` | 6 | Post-NLP normalized documents |
| `osint.deadletter` | 1 | Failed documents (infinite retention) |
| `osint.correlated` | 6 | All correlated signals |
| `osint.correlated.stock_alpha` | 3 | Signals routed to Stock Alpha Engine |
| `osint.correlated.product_ideation` | 3 | Signals routed to Product Ideation Engine |
| `osint.correlated.anomalies` | 1 | Signals with coordinated inauthenticity flags |

## Conventions

- All connectors must produce `OsintDocument` instances (never raw dicts to Kafka)
- `dedup_hash` must be computed before publishing (`doc.compute_dedup_hash()`)
- Kafka messages use `entity_id` or `source_id` as partition key (see `to_kafka_key()`)
- Every subsystem follows the Config class pattern: class-level attributes, `load_config_from_env()` function
- Connectors are async (aiohttp); normalization and correlation are sync (CPU-bound NLP / stateful windowing)
- Cross-correlation confidence scores must accompany all signals to value engines — never act on single-source signals
- Source reliability weights: SEC (1.0) > News/Trends (0.8) > GitHub (0.7) > TrustPilot (0.6) > HN (0.5) > Reddit (0.4)

## Tech Stack

- **Python 3.12+**, **Pydantic v2** for data validation
- **confluent-kafka** for Kafka producer/consumer
- **aiohttp** for async HTTP in connectors
- **spaCy** (en_core_web_lg) for NER
- **fastText** (lid.176) for language detection
- **Microsoft Presidio** for PII detection/anonymization
- **pytrends** for Google Trends ingestion
- **Docker / docker-compose** for infrastructure (Confluent Kafka images v7.6.1)
- **Planned:** FinBERT (financial sentiment), ABSA pipeline (product reviews)

## Environment Variables

Each connector/subsystem loads config from env vars. Key ones:

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

## Known Issues

- `docker-compose.yml` `init-topics` container needs a longer wait-for-kafka before creating topics
- `docker-compose.yml` has deprecated `version` attribute (remove it)
- `wait_for_kafka()` is duplicated across every connector/subsystem — should extract to shared utility

## Project Context

- Related to AssetBoss project (domain: assetboss.app, admin: mas-admin@assetboss.app)
- Google Drive has folders for Product Management, Marketing, Legal
