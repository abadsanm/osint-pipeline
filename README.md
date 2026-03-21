# Sentinel | Social Alpha — OSINT Analytics Platform

A three-pillar OSINT analytics platform that ingests public data from multiple sources, normalizes and cross-correlates it, then routes signals to two value engines for stock alpha generation and product opportunity discovery. Includes a real-time dashboard frontend.

```
6 Source Connectors → Kafka → Normalization (NLP) → Cross-Correlation
  → Stock Alpha Engine (FinBERT + SVC + Technicals)
  → Product Ideation Engine (ABSA + Gap Analysis)
  → Sentinel Dashboard (Next.js)
```

## Quickstart

### 1. Start infrastructure

```bash
docker-compose up -d
docker-compose ps          # Wait ~30s for all services healthy
```

Services: Zookeeper (2181), Kafka (9092), Schema Registry (8081), Kafka UI (http://localhost:8080)

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m normalization.models.download   # fastText + spaCy models (~700MB)
```

### 3. Run the pipeline

```bash
# Start a connector (HN works out of the box, no API keys needed)
python -m connectors.hacker_news

# Start normalization (consumes raw topics → osint.normalized)
python -m normalization

# Start cross-correlation (osint.normalized → correlated signals)
python -m correlation

# Start value engines
python -m stock_alpha          # Needs FinBERT model (~440MB auto-download)
python -m product_ideation     # Needs PyABSA model (~500MB auto-download)
```

### 4. Start the dashboard

```bash
cd dashboard
npm install
npm run dev                    # http://localhost:3000
```

### 5. Verify data flow

```bash
# Kafka UI
open http://localhost:8080

# CLI
docker exec osint-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic tech.hn.stories --from-beginning --max-messages 5
```

## Source Connectors

| Connector | Command | Auth Required | Data Source |
|-----------|---------|---------------|------------|
| HackerNews | `python -m connectors.hacker_news` | None | Firebase API + Algolia search |
| TrustPilot | `python -m connectors.trustpilot` | `TRUSTPILOT_DOMAINS` (+ optional API key) | Official API or web scrape |
| GitHub | `python -m connectors.github` | `GITHUB_TOKEN` (optional) | GraphQL search + REST events |
| SEC/Congress | `python -m connectors.sec_insider` | None (+ optional `FINNHUB_API_KEY`) | EDGAR EFTS + Finnhub |
| Reddit | `python -m connectors.reddit` | Optional OAuth credentials | OAuth API or JSON fallback |
| Google Trends | `python -m connectors.google_trends` | None | pytrends (unofficial) |

Each connector publishes `OsintDocument` instances to source-specific Kafka topics.

## Processing Pipeline

### Normalization Layer

Consumes from all raw topics, enriches each document through 4 processors:

1. **Language Detection** — fastText lid.176 → sets `doc.language`
2. **Named Entity Recognition** — spaCy en_core_web_lg + ticker patterns → populates `doc.entities`
3. **Near-Duplicate Detection** — SimHash 64-bit fingerprinting → flags duplicates in metadata
4. **PII Scrubbing** — Microsoft Presidio → replaces PII with typed placeholders

Output: `osint.normalized`. Failed docs: `osint.deadletter`.

### Cross-Correlation Engine

Clusters entity mentions across platforms within sliding time windows:

- **Confidence Scoring**: source diversity (40%) + volume anomaly z-score (30%) + quality signals (30%)
- **Anomaly Detection**: account age clustering, temporal bursts, cross-platform sync without catalyst, template content
- **Signal Routing**: TICKER/COMPANY → stock_alpha, PRODUCT → product_ideation, anomalies → review queue

Source reliability weights: SEC (1.0) > News (0.8) > GitHub (0.7) > TrustPilot (0.6) > HN (0.5) > Reddit (0.4)

## Value Engines

### Stock Alpha Engine

Processes correlated financial signals through:

- **FinBERT** (ProsusAI/finbert) — financial sentiment analysis
- **SVC Metric** — Sentiment Volume Convergence (sentiment_shift x volume_change)
- **Technical Indicators** — RSI, MACD, Bollinger Bands, SMA/EMA, ATR, OBV via pandas-ta + yfinance
- **Scorer** — Weighted combination → -1 (strong sell) to +1 (strong buy) alpha signals

> No sentiment model guarantees profitable trades. These are research tools that surface informational edges in noisy, adversarial markets.

### Product Ideation Engine

Analyzes consumer feedback for product opportunities:

- **ABSA** — PyABSA aspect extraction with per-aspect sentiment
- **Feature Request Mining** — 9 regex patterns (wish/want/should/missing/why_cant/etc.)
- **Question Clustering** — sentence-transformers embeddings + DBSCAN
- **Gap Analysis** — Ranked ProductGap objects with opportunity scores

## Dashboard

**Sentinel** — a "Bloomberg Terminal for OSINT" built with Next.js 14, Tailwind CSS, D3.js, and Recharts.

Three views:

1. **Global Pulse** (`/`) — D3 force-directed sentiment heat-sphere, signal feed, 4 chart cards, timeframe selector
2. **Financial Alpha** (`/alpha/[ticker]`) — Price + sentiment overlay with prediction ghost and news panel
3. **Product Innovation** (`/innovation`) — Feature friction board + innovation gap map scatter plot

Dark-mode financial terminal aesthetic. See `dashboard/DESIGN_SPEC.md` for full design specification.

## Kafka Topics

| Topic | Purpose |
|-------|---------|
| `tech.hn.stories` / `tech.hn.comments` | HackerNews content |
| `tech.github.events` | GitHub repos, issues, events |
| `consumer.reviews.trustpilot` / `consumer.reviews.amazon` | Product reviews |
| `finance.sec.insider` / `finance.congress.trades` | SEC filings, congressional trades |
| `finance.reddit.posts` / `finance.reddit.comments` | Reddit financial subreddits |
| `trends.google.interest` | Google Trends data |
| `osint.normalized` | Post-NLP normalized documents |
| `osint.deadletter` | Failed documents |
| `osint.correlated` | All correlated signals |
| `osint.correlated.stock_alpha` | Routed to Stock Alpha Engine |
| `osint.correlated.product_ideation` | Routed to Product Ideation Engine |
| `osint.correlated.anomalies` | Coordinated inauthenticity flags |
| `stock.alpha.signals` | Scored stock alpha signals |
| `product.ideation.gaps` | Product opportunity reports |

## Tech Stack

**Backend:** Python 3.12+, Pydantic v2, confluent-kafka, aiohttp, spaCy, fastText, Presidio, transformers (FinBERT), PyABSA, sentence-transformers, pandas-ta, yfinance, pytrends

**Frontend:** Next.js 14, TypeScript, Tailwind CSS, D3.js, Recharts, Lucide React

**Infrastructure:** Docker, Apache Kafka (Confluent v7.6.1), Zookeeper, Schema Registry

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `KAFKA_BOOTSTRAP` | Kafka broker address (default: localhost:9092) |
| `TRUSTPILOT_DOMAINS` | Domains to monitor (comma-separated) |
| `TRUSTPILOT_API_KEY` | TrustPilot API key (enables API mode) |
| `GITHUB_TOKEN` / `GITHUB_TOKENS` | GitHub PAT(s) for higher rate limits |
| `FINNHUB_API_KEY` | Enables congressional trade tracking |
| `CONGRESS_WATCHLIST` | Tickers to monitor for congressional trades |
| `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` | Reddit OAuth (enables OAuth mode) |
| `REDDIT_SUBREDDITS` | Override default subreddit list |
| `GTRENDS_KEYWORDS` | Google Trends keywords to track |
| `SPACY_MODEL` | Override spaCy model (default: en_core_web_lg) |
| `FASTTEXT_MODEL_PATH` | Path to fastText lid.176.bin |
| `FINBERT_MODEL` | Override FinBERT model (default: ProsusAI/finbert) |

## Project Structure

```
osint-pipeline/
  schemas/              # Pydantic models (OsintDocument, CorrelatedSignal, ProductGap)
  connectors/           # 6 source connectors + shared KafkaPublisher
  normalization/        # NLP pipeline (language, NER, dedup, PII)
  correlation/          # Cross-correlation engine (windowing, scoring, anomaly, routing)
  stock_alpha/          # FinBERT sentiment + SVC + technicals → alpha signals
  product_ideation/     # ABSA + feature mining + question clustering → gap analysis
  dashboard/            # Next.js 14 Sentinel frontend
  docker-compose.yml    # Kafka infrastructure
  requirements.txt      # Python dependencies
```
