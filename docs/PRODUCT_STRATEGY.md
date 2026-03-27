# Sentinel | Social Alpha — Product Strategy & Roadmap

## Executive Summary

Sentinel is an open-source OSINT analytics platform that ingests real-time public data from 6 sources, cross-correlates signals across platforms, and routes intelligence to two value engines: stock alpha generation and product opportunity discovery. The platform is currently functional end-to-end but lacks market data depth, prediction track record, and consumer-grade UX. This document lays out a phased roadmap to close competitive gaps, deepen the moat, introduce novel features, and monetize effectively.

The core thesis: **Sentinel should feel like the morning news feed people actually trust.** Not rage bait. Not noise. A curated, calibrated stream of insights about money, markets, and products — where every signal has a confidence score you can verify, and every prediction gets graded in public.

### Engagement Philosophy

Sentinel uses the same psychological mechanisms that make social media addictive — variable reward schedules, loss aversion, streaks, social validation, novelty seeking — but redirects every one of them toward real-world outcomes. The dopamine hit comes from being right about reality, not from collecting likes. Three governing principles shape every design decision:

1. **Finite by default, infinite by choice.** Every feed, briefing, and notification has a "you're caught up" endpoint. Depth is available on demand but never pushed. The user pulls engagement through curiosity; the platform never pushes it through manufactured urgency.

2. **Every dopamine hit tied to real-world outcomes.** No likes, no follower counts, no engagement metrics. Prediction accuracy is verified by the market. Reputation is based on being right, not being popular. Streaks are tied to consecutive correct predictions, not consecutive logins.

3. **Radical honesty as a feature.** When Sentinel is wrong, it says so prominently. Misses appear before hits in the daily scorecard. When accuracy drops, a banner warns users. The cancellation flow is one click with no guilt screens. In a world where every platform manipulates, honesty is the ultimate competitive advantage.

The target engagement profile: 8-15 minutes per day across 2-3 sessions. Every minute delivers measurable insight. This is deliberately less time than social media — and it drives stronger retention because users think "that was worth my time."

A companion document, the **Behavioral Design Specification**, provides detailed implementation rules for every engagement loop, notification tier, anti-pattern, and visual design decision.

---

## Part 1: Close Competitive Gaps (Single Laptop)

These are the features competitors already have that Sentinel lacks. Each can be built and run on a single machine with Docker and Python. Priority ordered by impact on user value.

### Gap 1: Market Data & Order Flow (vs. Unusual Whales)

**The gap:** Unusual Whales' core value is real-time options flow and dark pool data. Sentinel has zero market microstructure data.

**The fix (already designed, ready to build):**
- Binance WebSocket connector for order book depth + aggregated trades (free, no auth)
- Alpaca REST connector for OHLCV bars with VWAP (free with account)
- Computation modules: anchored VWAP, volume profiles, fair value gaps, order flow delta, liquidity sweep detection
- New Kafka topics: market.binance.depth, market.binance.trades, market.alpaca.bars

**Laptop feasibility:** Binance WebSocket is lightweight. Alpaca polls every 60s. Both run comfortably alongside existing connectors. Total additional RAM: ~200MB.

**Estimated build time:** 2-3 Claude Code sessions.

### Gap 2: Options Flow Data (vs. Unusual Whales)

**The gap:** Options flow is Unusual Whales' moat. Large options trades often signal institutional positioning before price moves.

**The fix:**
- CBOE options data is not freely available for real-time flow
- Workaround: Use Unusual Whales' own API ($57/mo starter) as a data source, ingesting their flow signals into Sentinel's cross-correlation engine
- Alternative: Polygon.io options data ($29/mo) provides trades, quotes, and aggregates
- Cheapest path: scrape publicly reported unusual options activity from free sources (Barchart unusual options, Finviz options screener) on a 5-minute poll

**Laptop feasibility:** Yes — just another connector.

**Estimated build time:** 1 session for the free scraping approach, 2 sessions for Polygon integration.

### Gap 3: Prediction Track Record (vs. everyone)

**The gap:** No competitor publishes calibrated prediction accuracy. But Sentinel can't claim superiority without proving it either. The backtesting framework is built but hasn't been run.

**The fix:**
- Run the backtester against 14 months of historical data for 10 tickers
- Publish results on the Backtesting dashboard page
- Establish baseline accuracy numbers
- Set up the feedback loop (tracker) to accumulate live prediction outcomes
- After 30 days of live tracking, publish a "Sentinel Accuracy Report" — this becomes marketing material

**Laptop feasibility:** Yes — backtesting is CPU-intensive but runs in a single Python process. Budget 2-4 hours for a 14-month backtest across 10 tickers.

**Estimated build time:** 1 session to run and debug, 1 session to polish the results page.

### Gap 4: Mobile Responsiveness (vs. Unusual Whales, Quiver)

**The gap:** Both competitors have polished mobile experiences. Sentinel's dashboard is desktop-only.

**The fix:**
- The DESIGN_SPEC.md already defines mobile breakpoints (< 768px: bottom tab bar, single column, simplified charts)
- Implement responsive Tailwind breakpoints across all pages
- Replace D3 heat-sphere with a sortable list view on mobile
- Signal feed becomes a full-screen pull-up sheet
- Chart cards stack vertically with sparkline variants

**Laptop feasibility:** Yes — purely frontend work.

**Estimated build time:** 2-3 sessions.

### Gap 5: Real-Time Alerting (vs. Benzinga Pro, Dataminr)

**The gap:** Benzinga Pro's killer feature is speed — breaking news faster than anyone. Dataminr does real-time event detection. Sentinel ingests data but doesn't push alerts.

**The fix:**
- Add a WebSocket endpoint to the API server that pushes signals to the dashboard in real time (instead of polling every 3-5 seconds)
- Add configurable alert rules: "notify me when confidence > 0.8 for any TICKER entity", "notify me when a congressional trade is detected for my watchlist"
- Browser notifications via the Notifications API
- Future: Telegram/Discord bot integration for mobile alerts

**Laptop feasibility:** Yes — WebSocket server is lightweight.

**Estimated build time:** 2 sessions.

---

## Part 2: Cloud Deployment Requirements

Everything above runs on a laptop. Below is what changes when you move to production scale.

### Infrastructure

**Kafka cluster:** Move from single-broker Docker to a 3-broker cluster. Options:
- Confluent Cloud (managed Kafka, free tier up to 300GB/month)
- AWS MSK Serverless (pay per throughput)
- Self-managed on 3 VMs (cheapest long-term, most ops burden)

**Compute:** The pipeline has distinct workload profiles:
- Connectors (I/O bound, low CPU): Run on small instances (t3.small) or serverless containers
- Normalization + NLP (CPU bound): Needs dedicated compute. c6i.xlarge or equivalent
- FinBERT inference (GPU optional but 10x faster): g4dn.xlarge for model inference, or use CPU with batching
- Dashboard (Next.js): Vercel free tier handles this easily

**Storage:**
- S3 for raw data lake (immutable archive, lifecycle to Glacier after 90 days)
- ClickHouse or TimescaleDB for time-series aggregates (predictions, accuracy metrics, sentiment trends)
- Qdrant Cloud for vector store (semantic search over review text)

**Estimated monthly cost at startup scale:**
- Confluent Cloud free tier: $0
- 2x t3.small (connectors + API): ~$30/mo
- 1x c6i.xlarge (NLP): ~$120/mo
- Vercel free tier (dashboard): $0
- S3 (50GB): ~$1/mo
- Total: ~$150/month for a production-grade deployment

### CI/CD & DevOps

- GitHub Actions for automated testing on every PR (use the testing prompt we built)
- Docker Compose for local dev, Kubernetes (or ECS) for production
- Terraform for infrastructure-as-code
- Sentry for error tracking across all Python services
- Grafana + Prometheus for pipeline health monitoring (Kafka lag, processing latency, model inference time)

### Data Retention & Compliance

- Raw data: 90 days in S3, then Glacier
- Normalized data: 1 year in ClickHouse
- Predictions + outcomes: Indefinite (this is your track record)
- PII: Never stored — Presidio scrubs at ingestion
- GDPR right-to-deletion: Not applicable since no PII is retained
- Robots.txt compliance: Logged and auditable per connector

---

## Part 3: Forward-Looking Roadmap — Deepening the Moat

These features go beyond closing gaps. They create capabilities no competitor has, increasing switching costs and defensibility.

### Moat 1: Public Prediction Leaderboard

**What:** Every prediction Sentinel makes is logged, timestamped, and graded. Publish a live leaderboard showing rolling accuracy by ticker, time horizon, and regime. Anyone can audit it.

**Why this is a moat:** No competitor does this. Unusual Whales shows you data. Quiver shows you data. Sentifi generates scores. None of them say "here's how accurate we've been, verified against actual market outcomes." Radical transparency in prediction accuracy builds trust that marketing can't buy. It also creates a self-improving flywheel — the more predictions you make, the more training data you generate, the better the models get, the more accurate the leaderboard becomes.

**Build plan:** The tracker and backtesting framework already exist. Add a public-facing page that surfaces the stats without requiring login.

### Moat 2: Community Intelligence Layer

**What:** Let users contribute their own signals — analyst notes, thesis snippets, contrarian views — that get ingested into the pipeline alongside automated OSINT. User signals get the same cross-correlation treatment as machine signals. Over time, the best human analysts rise to the top based on their prediction accuracy.

**Why this is a moat:** This creates a network effect. More users → more signals → better cross-correlation → better predictions → more users. It also solves the "cold start" problem for novel events that OSINT doesn't catch (e.g., private conversations, industry insider knowledge that doesn't appear on HN or Reddit). Quiver has no community layer. Unusual Whales has a Discord but it doesn't feed back into analysis.

**Build plan:** New "Human Signal" document type in the schema. API endpoint for user submissions. Reputation scoring based on prediction accuracy of submitted signals.

### Moat 3: Prediction Markets Integration

**What:** Ingest data from prediction markets (Polymarket, Kalshi, Metaculus) as an additional signal source. Prediction market prices are the most efficient aggregation of crowd wisdom for binary events — election outcomes, Fed decisions, product launches, regulatory actions.

**Why this is a moat:** Unusual Whales just launched "Unusual Predictions" for prediction markets in January 2026, but they treat it as a standalone feature. Sentinel would ingest prediction market prices as just another signal source and cross-correlate them against social sentiment, congressional trades, and news. When prediction market odds diverge sharply from sentiment, that's an actionable signal.

**Build plan:** New connector for Polymarket API (free, public). New entity type PREDICTION_MARKET in the schema. Cross-correlation engine treats prediction market prices as a high-reliability source (weight: 0.9, just below SEC).

### Moat 4: Causal Reasoning Engine

**What:** Move beyond correlation to causation. When Sentinel detects that NVDA sentiment spiked across 4 platforms, the causal engine tries to answer "why?" by tracing the signal back to a root cause: a specific SEC filing, a specific HN post, a specific congressional trade. It constructs an evidence chain.

**Why this is a moat:** Every other platform says "NVDA is trending." Sentinel would say "NVDA is trending because Senator X bought $500K of NVDA options 3 days before the CHIPS Act amendment was introduced, and HN picked up the amendment 6 hours later, and Reddit followed 12 hours after that. Confidence: 82%." That's a qualitatively different product.

**Build plan:** Extend the correlation engine to store temporal ordering of entity mentions. Build a provenance graph that links signals back through the pipeline to their originating documents. Use LLM (Claude API, already integrated) to generate narrative explanations from the evidence chain.

### Moat 5: Sector-Specific Prediction Models

**What:** Instead of one general scorer for all tickers, train specialized models per sector. A biotech stock responds differently to sentiment than a consumer tech stock. FDA approval sentiment for MRNA is a completely different signal than earnings sentiment for AAPL.

**Why this is a moat:** Generic sentiment models are commoditized. Sector-specific models trained on your own cross-correlated data are not. The more data you accumulate per sector, the better the sector model gets, and the harder it is for competitors to replicate without the same data history.

**Build plan:** Extend the backtesting framework to segment by GICS sector. Train separate XGBoost models per sector. The regime detector already provides market-context conditioning — sector conditioning is the next dimension.

---

## Part 4: Novel Feature Ideation — "Addictive but Nourishing"

The goal: a platform people check compulsively because it makes them smarter, richer, and more informed — not because it makes them angry.

### The Core Insight: Same Psychology, Different Fuel

Social media and Sentinel exploit identical psychological mechanisms. The difference is what the mechanism runs on:

| Mechanism | Social Media Fuel (toxic) | Sentinel Fuel (nourishing) |
|-----------|--------------------------|---------------------------|
| Variable reward | Rage bait, outrage, shocking content. Reward = emotional arousal. | "Did my prediction hit?" Reward = being right about reality. |
| Loss aversion / FOMO | "Everyone is talking about this." Manufactured urgency. Infinite timeline. | Finite briefing with "caught up" state. Alerts only above your threshold. No synthetic urgency. |
| Streaks | Meaningless login streaks. Breaking feels catastrophic. | Prediction accuracy streaks tied to real outcomes. Breaks teach you about model limits. |
| Social validation | Follower counts, likes. Value from others' approval. Comparison anxiety. | Reputation from prediction accuracy. Can't buy or fake a 68% accuracy score. |
| Identity investment | Curated persona. Years of followers. Leaving = losing identity. | Prediction track record. Portable (export anytime). Investment in knowledge, not vanity. |
| Novelty seeking | Infinite scroll. Each item just interesting enough to prevent closing. | Finite signal feed with "caught up" endpoint. Depth on demand, not pushed. |
| Social proof / herding | Viral mobs. Pile-on culture. Popular = amplified. | Congressional trades + cross-correlated signals. Contrarian detector explicitly pushes back against herds. |

### The Metric That Separates Sentinel From Social Media

Social media optimizes for **time on platform** (more minutes = more ads = more revenue). The user's wellbeing is inversely correlated with the business metric.

Sentinel optimizes for **insight per minute** (actionable signals consumed + predictions resolved + explanations viewed, divided by total minutes in app). Features that increase time without increasing insight are rejected. Features that increase insight without increasing time are prioritized.

Target daily engagement: **8-15 minutes across 2-3 sessions.** This is deliberately less than social media's 30-58 minutes — and drives stronger retention because users think "that was worth my time."

### Feature: Morning Briefing ("Your Daily Alpha")

**What it is:** Every morning at 7:00 AM, Sentinel generates a personalized 2-minute briefing based on your watchlist. Five to eight cards, each with ticker, signal type, confidence %, and a one-line insight (max 120 characters). Below the cards: yesterday's scorecard showing how previous predictions resolved — misses shown before hits.

**Why it's addictive:** It replaces the doom-scrolling-through-news ritual with a curated, actionable digest. Users open it because it's short, specific to them, and directly tied to their money.

**Critical design detail — the "caught up" screen:** After the last card, the screen displays: "That's everything for this morning. Your next briefing arrives tomorrow at [time]. We'll alert you during the day only for signals above [X]% confidence." There is no "load more" button, no trending section, no algorithmic recommendations. The app genuinely has nothing more to push. This is the most important UX element in the entire product.

**Behavioral design integration:** If there are zero signals above the user's threshold overnight, the morning briefing notification is not sent. The user is not notified of the absence of news. Silence is honest.

### Feature: Prediction Streaks & Accuracy Badges

**What it is:** Prediction accuracy streaks tied to real market outcomes. "Sentinel correct on NVDA for 7 consecutive 24h predictions." Badges: "64% accurate on tech sector over 90 days."

**Why it's addictive:** Streaks create daily check-in habits. But instead of meaningless Snapchat streaks, these are grounded in reality — the market decides, not an algorithm.

**Critical design detail — streak breaks are educational, not punitive:** When a streak breaks, the display reads: "Your 7-day tech streak ended. The model missed NVDA because sentiment diverged from order flow during low-volume after-hours trading. Your 30-day accuracy is still 64%." The broken streak teaches something about where the model struggles.

**Behavioral design integration:** Streaks never appear as notification badges. There is no "don't lose your streak!" push notification. If the user doesn't open the app, their streaks still update silently. Opening the app is rewarded with information, not punished by absence.

### Feature: "What If" Scenario Simulator

**What it is:** Users input a hypothetical event — "What if the Fed cuts by 50bps?" — and Sentinel simulates impact across their portfolio using historical analogs and prediction models.

**Why it's addictive:** Turns Sentinel from reactive ("here's what happened") to proactive ("here's what could happen"). Active scenario planning replaces passive content consumption.

**Behavioral design integration:** The simulator has a clear endpoint. Results are displayed once, with no "run another scenario" recommendation or auto-suggested follow-ups. The user decides when to explore more. The simulator is accessed via explicit tap, never auto-loaded.

### Feature: Contrarian Detector

**What it is:** Surfaces moments when NLP sentiment strongly disagrees with price action, prediction market odds, or insider trades. "Everyone is bearish on META but the price is rising, insiders are buying, and Polymarket odds favor positive earnings."

**Why it's addictive:** People love being told "the crowd is wrong." But instead of feeding confirmation bias, this backs it up with multi-source evidence.

**Critical design detail — the 3-source rule is non-negotiable:** Contrarian alerts never fire with fewer than three independent data sources supporting the contrarian view. Two sources could be coincidence. Three sources with temporal ordering constitutes a signal. This rule prevents the platform from manufacturing contrarian excitement where the evidence doesn't support it.

**Behavioral design integration:** Contrarian alerts are never framed as "Sentinel thinks the crowd is wrong." They are framed as "Multiple independent data sources disagree with prevailing sentiment." Sentinel presents evidence, not opinions. The user decides.

### Feature: Product Opportunity Alerts for Entrepreneurs

**What it is:** Push notifications from the Product Ideation Engine: "434 TrustPilot reviewers asking for better wind noise cancellation in wireless earbuds. No product addresses this. Opportunity score: 0.87."

**Why it's addictive for a different audience:** Every alert is a potential business idea backed by quantified demand. Replaces scrolling Product Hunt with data-driven opportunity discovery.

**Behavioral design integration:** Alerts respect the notification tier system and daily budget. Opportunity alerts are Tier 2 (opt-in, threshold-gated). Maximum 5 per day combined with all other Tier 2 alerts.

### Feature: "Explain Like I'm Investing" (ELI-I)

**What it is:** For every signal, tap "Explain" for a plain-language breakdown: what this means for your portfolio, historical context, what the confidence level implies.

**Why it's addictive:** Makes sophisticated analysis accessible without dumbing it down. "This stock is being discussed much more than usual, and the tone is unusually positive. Historically, when this pattern occurs, the stock rises 60% of the time within 3 days."

**Behavioral design integration:** The Explain panel is a slide-up (maintains signal feed context), has a natural endpoint (information displayed, panel done), and contains no "related signals" recommendations. If the user wants to explore another signal, they close the panel and return to the feed. The Explain button is available to all users from day one — never gated as premium. Understanding is the product, not the upsell.

### Anti-Patterns: Things Sentinel Must Never Do

These are explicitly prohibited across all features:

- **Never show notification count badges.** Content displays when the user opens the app. Badges create anxiety.
- **Never auto-play the next signal.** The user decides when to engage.
- **Never rank content by engagement.** Rank by confidence score and recency. Popularity is not accuracy.
- **Never show "X people are looking at this."** Manufactured urgency.
- **Never penalize users for not opening the app.** Streaks celebrate presence, never punish absence.
- **Never use dark patterns in subscription flows.** One-click cancel. No guilt screens.
- **Never amplify negative sentiment disproportionately.** Bearish and bullish signals get identical treatment.
- **Never hide accuracy data.** If Sentinel was wrong, show it prominently. Trust requires honesty about failures.
- **Never send re-engagement notifications.** "You haven't opened Sentinel in 3 days" is manipulation. Silence when there's nothing useful to say is honest.
- **Never insert algorithmic recommendations between feed items.** The signal feed shows your watchlist above your threshold. Nothing else.

---

## Part 5: Monetization Strategy

### Tier 1: Free (Self-Hosted Open Source)

**What's included:** Full platform — all connectors, normalization, correlation, both value engines, dashboard. Users provide their own compute, API keys, and Kafka infrastructure.

**Who uses it:** Developers, quant hobbyists, students, people who want to learn. This is your top-of-funnel and community growth engine. It also ensures the platform can never be shut down by a single company.

**Revenue:** $0 direct. Value: community contributions, bug reports, brand credibility, and a talent pipeline.

### Tier 2: Sentinel Cloud — "Analyst" ($29/month)

**What's included:**
- Hosted version — no Docker, no Kafka, no setup. Sign up and go
- All 6 connectors running continuously with managed infrastructure
- Real-time dashboard with all existing views
- Prediction monitor with accuracy tracking
- Morning briefing (daily email/notification)
- 5 tickers on watchlist
- 30-day signal history
- Community signal contribution (1 signal/day)

**Who uses it:** Retail investors, part-time traders, product managers who want insights without running infrastructure.

**Why $29:** This undercuts Unusual Whales ($57/mo minimum) and matches Quiver Premium ($25/mo) while offering more — NLP sentiment, cross-correlation, and product ideation that neither competitor has. It's also 1/67th the price of Bloomberg.

**Unit economics at scale:** If you run on AWS, the marginal cost per user is roughly $2-5/month (shared Kafka, shared NLP inference, minimal per-user storage). At $29/month, the margin is 80%+.

### Tier 3: Sentinel Cloud — "Alpha" ($99/month)

**What's included:** Everything in Analyst, plus:
- Unlimited watchlist
- 1-year signal history
- Calibrated predictions with confidence scores
- Backtesting access (run custom backtests on Sentinel's historical data)
- Contrarian detector alerts
- "What If" scenario simulator (5/month)
- API access (100K requests/month) for building custom integrations
- Priority alert delivery (< 30 second latency)
- Community signal contribution (unlimited)
- Prediction streak badges and shareable accuracy cards

**Who uses it:** Serious retail traders, small fund analysts, fintech developers building on top of Sentinel.

**Why $99:** This is where the unique features live — calibrated predictions, contrarian detection, scenario simulation. These have no free equivalent anywhere. Users who make even one better trade per month from a Sentinel signal recoup the cost instantly.

### Tier 4: Sentinel Enterprise — "Fund" ($499/month per seat)

**What's included:** Everything in Alpha, plus:
- Dedicated Kafka cluster (no shared infrastructure)
- Custom connector development (e.g., proprietary data sources)
- Sector-specific trained models
- Full historical data export
- SLA: 99.9% uptime, < 5 second alert latency
- White-label option (embed Sentinel views in your own platform)
- API access (unlimited)
- Dedicated support channel

**Who uses it:** Small hedge funds, RIA firms, prop trading desks, fintech companies building products on Sentinel data.

**Why $499:** This competes with RavenPack ($10K+/mo), Dataminr ($10K+/mo), and Sentifi (enterprise pricing) at a fraction of the cost. For a small fund managing $10M+, $499/month is negligible if it generates even one informational edge per quarter.

### Additional Revenue Streams

**Data licensing:** Sell anonymized, cross-correlated signal data via API to quant funds and academic researchers. This is how RavenPack makes most of its money. Sentinel's cross-correlated data (not raw scrapes) is a differentiated product.

**Prediction marketplace:** Let users publish their own predictions (with skin in the game via small wagers or reputation stakes). Sentinel takes a platform fee on resolved predictions. This is effectively a prediction market built on OSINT signals rather than pure speculation.

**Educational content:** "Sentinel Academy" — paid courses on OSINT-driven investing, NLP for finance, building alternative data pipelines. Monetize the expertise that built the platform. $199 per course or $39/month subscription.

**Affiliate revenue:** When a user sees a bullish signal for AAPL and wants to trade, Sentinel can link to partner brokerages (Alpaca, Interactive Brokers) with affiliate tracking. This is how many fintech platforms generate ancillary revenue.

---

## Implementation Timeline

### Phase 1: Foundation (Weeks 1-4) — Single Laptop

- Run backtester, establish baseline accuracy numbers
- Build Binance + Alpaca market data connectors
- Build microstructure computation modules
- Launch Predictions + Backtesting dashboard pages
- Fix all failing tests from the testing suite

### Phase 2: User Experience (Weeks 5-8) — Single Laptop

**Week 5: UX Design Sprint (CRITICAL — precedes all feature work)**
- Produce wireframes for: morning briefing flow including "caught up" state, notification architecture across all 4 tiers, prediction resolution animations, streak display with streak-break handling, and the Explain panel layout
- Define all "caught up" states for every page (signal feed, predictions, briefing, product ideation)
- Finalize the anti-pattern checklist and get it into the PR review process
- Document the notification tier system: morning briefing (default on), high-confidence alerts (opt-in), daily resolution summary (opt-in), weekly digest (opt-in), with 8/day maximum budget

**Weeks 6-8: Feature implementation governed by Behavioral Design Spec**
- Morning briefing: finite card stack (max 8 cards), caught-up screen, yesterday's scorecard with misses shown before hits
- Mobile responsive dashboard (Tailwind breakpoints, simplified mobile views)
- Real-time WebSocket alerts with configurable confidence threshold and tier-gated delivery
- Prediction streaks: celebrate correct predictions, explain broken streaks ("Your 7-day streak ended. The model missed because [explanation]. Your 30-day accuracy is still 64%.")
- Prediction resolution animations: 0.5s teal pulse (correct), 0.5s coral pulse (incorrect)
- "Explain" button on every signal card from day one (not gated as premium), slide-up panel with plain-language summary + evidence chain + historical analogs + confidence breakdown
- Honest cancellation flow: one-click cancel, no guilt screens, no countdown timers

### Phase 3: Differentiation (Weeks 9-14) — Single Laptop

- Prediction markets connector (Polymarket)
- Contrarian detector
- Causal reasoning engine (evidence chains)
- Options flow integration (free scraping approach)
- Public prediction leaderboard

### Phase 4: Cloud & Monetization (Weeks 15-20)

- Deploy to AWS/GCP (Terraform + ECS or K8s)
- Build auth + subscription system (Stripe + Auth0 or Clerk)
- Launch Analyst tier ($29/mo)
- Launch Alpha tier ($99/mo)
- Community signal submission system
- Prediction marketplace (beta)

### Phase 5: Scale & Moat (Weeks 21-30)

- Sector-specific models (train on accumulated data)
- "What If" scenario simulator
- Enterprise tier ($499/mo)
- Data licensing API
- Sentinel Academy (educational content)
- White-label option for fintech partners

---

## Success Metrics

**Prediction accuracy:** Target 62%+ directional accuracy on 24h predictions across top 10 liquid tickers. This is the north star — everything else is noise if predictions aren't accurate.

**Calibration error:** Target < 0.05 (stated 70% confidence should be correct 65-75% of the time).

**User engagement:** Target 5+ sessions per week for paying users. The morning briefing should drive daily opens.

**Conversion:** Target 5% free → Analyst, 15% Analyst → Alpha.

**Retention:** Target < 5% monthly churn for paid tiers. Prediction streaks and accuracy badges create daily habits.

**Revenue:** Target $10K MRR within 6 months of launch (200 Analyst + 30 Alpha subscribers). Target $50K MRR within 12 months.
