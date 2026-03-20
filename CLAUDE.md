# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A multi-pillar OSINT analytics platform that ingests public data from multiple sources, normalizes and cross-correlates it, then routes signals to two downstream value engines: **Stock Alpha** (FinBERT sentiment + time-series forecasting) and **Product Ideation** (ABSA + gap analysis on consumer reviews). An ethical compliance layer (GDPR/CCPA/EU AI Act, PII scrubbing via Presidio) wraps the entire system.

**Current state:** The foundation data pipeline is being built. The HackerNews connector is complete and operational. Remaining connectors, normalization layer, cross-correlation engine, and value engines are TODO.

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

# Run the HN connector (requires Kafka running)
python -m connectors.hacker_news

# Verify data flow via CLI
docker exec osint-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic tech.hn.stories \
  --from-beginning --max-messages 5

# Kafka UI is at http://localhost:8080
```

No test suite exists yet.

## Architecture

### Data Flow

```
Source Connectors → Kafka Topics (raw) → Normalization Layer → Kafka Topics (normalized) → Cross-Correlation Engine → Value Engines
```

### Key Components

- **`schemas/document.py`** — `OsintDocument` is the universal Pydantic model that all connectors produce. Every piece of data in the pipeline gets normalized into this schema before hitting Kafka. Downstream consumers only understand this format.

- **`connectors/`** — Each connector encapsulates its own auth pattern and publishes `OsintDocument` instances to source-specific Kafka topics. Currently only `hacker_news.py` is implemented, with two concurrent ingestion modes: Firebase polling (live stream) and Algolia keyword search.

- **`docker-compose.yml`** — Runs the full Kafka stack. The `init-topics` service auto-creates all pipeline topics on first boot. Known issue: the init container sometimes runs before Kafka is fully ready (needs a better wait-for-kafka script).

### Kafka Topic Naming

Topics follow `<domain>.<source>` for raw data and `osint.<stage>` for processed data:

| Topic | Purpose |
|-------|---------|
| `tech.hn.stories` / `tech.hn.comments` | HN connector output (live) |
| `tech.github.events` | GitHub events (Phase 2) |
| `consumer.reviews.trustpilot` / `consumer.reviews.amazon` | Review data (Phase 2) |
| `osint.normalized` | Post-NLP normalized documents |
| `osint.deadletter` | Failed documents for inspection |

### Connector Build Order

Each connector teaches the pipeline a new auth/ingestion pattern:

1. ~~HackerNews~~ (done) — public API, polling + diffing
2. TrustPilot — API key auth, review pagination
3. GitHub — PAT + GraphQL, token rotation + rate limiting
4. Congressional/SEC — public data scraping, possibly OCR for disclosure PDFs
5. Reddit — OAuth flow

## Conventions

- All connectors must produce `OsintDocument` instances (never raw dicts to Kafka)
- `dedup_hash` must be computed before publishing (`doc.compute_dedup_hash()`)
- Kafka messages use `entity_id` or `source_id` as partition key (see `to_kafka_key()`)
- Cross-correlation confidence scores must accompany all signals passed to value engines — never act on single-source signals without cross-validation
- `HNConfig` class pattern: all tunables in one config class, overridable via env vars

## Tech Stack

- **Python 3.12+**, **Pydantic v2** for data validation
- **confluent-kafka** for Kafka producer/consumer
- **aiohttp** for async HTTP in connectors
- **Docker / docker-compose** for infrastructure (Confluent Kafka images v7.6.1)
- **Planned:** fastText (language detection), spaCy (NER), Microsoft Presidio (PII), FinBERT (sentiment)

## Known Issues

- `docker-compose.yml` `init-topics` container needs a longer wait-for-kafka before creating topics
- `docker-compose.yml` has deprecated `version` attribute (remove it)
- `__pycache__` directories are not gitignored

## Project Context

- Related to AssetBoss project (domain: assetboss.app, admin: mas-admin@assetboss.app)
- Google Drive has folders for Product Management, Marketing, Legal
