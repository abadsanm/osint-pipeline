# OSINT Data Pipeline — Phase 1: Kafka + Hacker News Connector

## Architecture

```
HN Firebase API ──┐
                   ├──▸ HN Connector ──▸ Kafka ──▸ [downstream consumers]
HN Algolia API  ──┘         │
                             ▼
                     Unified OsintDocument
                     (schemas/document.py)
```

## Quickstart

### 1. Start the Kafka stack

```bash
docker-compose up -d
```

This spins up:
- **Zookeeper** (port 2181)
- **Kafka broker** (port 9092 host, 29092 internal)
- **Schema Registry** (port 8081)
- **Kafka UI** (port 8080) — open http://localhost:8080 to inspect topics
- **init-topics** — auto-creates all pipeline topics on first boot

Wait ~30 seconds for all services to be healthy:

```bash
docker-compose ps
```

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Run the Hacker News connector

```bash
python -m connectors.hacker_news
```

You should see:
```
HN Connector running — 2 ingestion modes active
  Live stream:      polling every 30s
  Algolia keywords: ['AI startup', 'LLM', 'GPT', ...]
  Kafka topics:     tech.hn.stories, tech.hn.comments
```

### 4. Verify data is flowing

Open Kafka UI at http://localhost:8080 and check:
- `tech.hn.stories` — should have messages within 30 seconds
- `tech.hn.comments` — populates as high-engagement stories are discovered

Or use the CLI:

```bash
docker exec osint-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic tech.hn.stories \
  --from-beginning \
  --max-messages 5
```

## Kafka Topics

| Topic | Partitions | Purpose |
|-------|-----------|---------|
| `tech.hn.stories` | 3 | HN stories (top, new, Show HN, Ask HN) |
| `tech.hn.comments` | 6 | HN comments on high-engagement stories |
| `tech.github.events` | 3 | GitHub trending repos, issues (Phase 2) |
| `consumer.reviews.trustpilot` | 3 | TrustPilot reviews (Phase 2) |
| `consumer.reviews.amazon` | 3 | Amazon product metadata (Phase 2) |
| `osint.normalized` | 6 | Normalized documents post-NLP processing |
| `osint.deadletter` | 1 | Failed documents for manual inspection |

## Unified Schema (OsintDocument)

Every connector produces `OsintDocument` instances (see `schemas/document.py`).
Key fields:

- `source` — which platform (hacker_news, github, trustpilot, etc.)
- `content_text` — the primary text for NLP processing
- `quality_signals.score` — platform engagement score (HN points, GitHub stars)
- `quality_signals.engagement_count` — discussion volume
- `dedup_hash` — content fingerprint for deduplication
- `entity_id` — the entity being discussed (repo, product, company)

## Configuration

Edit `HNConfig` in `connectors/hacker_news.py`:

- `STORIES_POLL_INTERVAL` — how often to check for new stories (default: 30s)
- `ALGOLIA_POLL_INTERVAL` — how often to run keyword searches (default: 300s)
- `MONITOR_KEYWORDS` — list of keywords to track via Algolia
- `MAX_CONCURRENT_FETCHES` — concurrency limit for Firebase item fetches

## Next Steps

1. **TrustPilot connector** — public API, API key auth, review pagination
2. **GitHub connector** — PAT auth, REST trending + GraphQL issues
3. **Normalization layer** — language detection, NER, dedup across sources
4. **Cross-correlation engine** — multi-source signal verification
