# Sentinel | Social Alpha — Behavioral Design Specification

## Document Purpose

This specification defines the engagement architecture for Sentinel. It governs how every user-facing feature is designed, not just what features exist. Every screen, notification, feed, and interaction must pass through the principles in this document before implementation.

The core thesis: Sentinel uses the same psychological mechanisms that make social media addictive, but redirects them toward real-world value. The user should feel compelled to return — not because we're manipulating them, but because every session makes them measurably smarter, more calibrated, and better equipped to make decisions about money, markets, and products.

---

## Part 1: Three Governing Principles

Every design decision in Sentinel must satisfy all three principles. If a proposed feature violates any one of them, it does not ship.

### Principle 1: Finite by Default, Infinite by Choice

The platform never pushes infinite content. Every feed, briefing, and notification stream has a natural stopping point — a clear "you're caught up" state where the app honestly tells the user there is nothing more worth their time right now.

However, depth is always available on demand. The "Explain" button, scenario simulator, evidence chains, and backtesting tools offer unlimited exploration for users who want it. The critical distinction: the user pulls engagement through curiosity. The platform never pushes it through manufactured urgency.

**Implementation rules:**

- The morning briefing contains a maximum of 8 cards. After the last card, the screen displays the "caught up" state. There is no "load more" button.
- The signal feed displays only signals above the user's configured confidence threshold. When no signals exceed the threshold, the feed displays: "No high-confidence signals for your watchlist right now. We'll notify you when something changes." There is no filler content, no "you might also like," no algorithmically surfaced content below the threshold.
- The predictions page shows only active and recently resolved predictions. There is no infinite scroll through historical predictions. Users who want historical data can access it through the backtesting tool, which is a deliberate action, not a passive scroll.
- Every page in the dashboard has a defined "empty state" that communicates completeness, not absence. "You're caught up" is a positive statement, not a failure state.
- Deep-dive content (evidence chains, scenario simulations, historical analogs) is always accessible via explicit user action (tap "Explain," tap "Simulate," tap "View Evidence"). It is never auto-loaded, auto-played, or algorithmically recommended.

**Design test:** For every screen, ask: "Can the user reach a state where the app tells them they're done?" If the answer is no, the screen violates Principle 1.

### Principle 2: Every Dopamine Hit Tied to Real-World Outcomes

The platform never generates engagement through artificial metrics (likes, followers, engagement counts) or emotional manipulation (outrage, fear, envy). Every rewarding moment in the product is grounded in an objective, verifiable real-world outcome.

Prediction accuracy is verified by the market. Reputation scores are computed from tracked prediction outcomes. Streaks are tied to consecutive correct predictions against actual price movements. Contrarian alerts are backed by three or more independent data sources. The morning briefing contains only signals that specifically affect the user's watchlist positions.

**Implementation rules:**

- There are no "like" or "upvote" buttons anywhere in the product. User-contributed signals are evaluated by prediction accuracy, not popularity.
- There are no follower counts. Users cannot "follow" other users in the social media sense. They can subscribe to high-accuracy signal contributors, but the subscription is based on the contributor's verified accuracy score, not their social following.
- Notification counts never appear as badges on the app icon or navigation items. When the user opens the app, relevant content is displayed. When they are not in the app, they receive only alerts that pass their configured confidence threshold.
- Prediction streaks celebrate correct predictions but do not punish misses. A broken streak is displayed as: "Your 7-day tech streak ended. The model missed because [brief explanation]. Your 30-day accuracy is still 64%." The broken streak is educational, not punitive.
- The contrarian detector never surfaces "the crowd is wrong" without at least three independent data sources supporting the contrarian view. The threshold is non-negotiable. Two sources is not enough — that could be coincidence. Three sources with temporal ordering constitutes a signal.
- Shareable accuracy badges always display the actual percentage, never a qualitative label like "top performer" or "expert." If the user's accuracy is 58%, the badge says 58%. Honesty is the brand.

**Design test:** For every rewarding moment in the product, ask: "Is this reward verified by something external to the platform?" If the reward is generated entirely within Sentinel (engagement metrics, streak length divorced from accuracy, popularity rankings), it violates Principle 2.

### Principle 3: Radical Honesty as a Feature

The platform never hides its own failures. When predictions are wrong, the miss is displayed as prominently as the hit. When the model's accuracy drops, the leaderboard reflects it immediately. When there is nothing important happening, the app says so instead of manufacturing urgency.

This radical honesty is itself a competitive advantage and an engagement driver. In a media landscape where every platform manipulates attention, a product that consistently tells the truth becomes the thing users trust most. Trust creates the deepest form of retention — users return not because they are hooked, but because they believe what the platform tells them.

**Implementation rules:**

- The public prediction leaderboard displays accuracy, miss rate, calibration error, and worst-performing tickers. It does not cherry-pick favorable metrics.
- Every prediction card shows its outcome once resolved: a clear checkmark for correct, a clear X for incorrect, and a dash for unresolved. There is no ambiguity and no spin.
- The morning briefing includes a "yesterday's scorecard" section showing how previous predictions performed. Misses appear before hits in the ordering — the user sees failures first, not buried at the bottom.
- When Sentinel's overall accuracy drops below 55% for any time horizon, a banner appears on the predictions page: "Our [X]-hour predictions are performing below our target accuracy. We're investigating. Consider reducing position sizes on these signals." The platform actively warns users when it is underperforming.
- The "Explain" button on incorrect predictions provides a candid post-mortem: what the model expected, what actually happened, and which signals were misleading. This is not a disclaimer — it is a genuine analysis of what went wrong.
- Subscription cancellation is one click with no guilt screens, no "are you sure?" modals, no countdown timers, and no loss-framing ("you'll lose access to..."). The cancellation page says: "Cancel anytime. Your prediction history and accuracy data remain accessible. We hope to earn your subscription back by improving our accuracy."

**Design test:** For every piece of information the platform displays, ask: "If this information were unfavorable to Sentinel, would we still show it this prominently?" If the answer is no, the display violates Principle 3.

---

## Part 2: Engagement Loop Architecture

Sentinel has five engagement loops. Each loop exploits a real psychological mechanism but redirects it from toxic outcomes (anxiety, outrage, addiction to approval) toward nourishing outcomes (calibration, understanding, analytical confidence).

### Loop 1: The Prediction Accountability Loop

**Psychological mechanism:** Variable reward schedule (the same mechanism that makes slot machines and social media feeds addictive — you never know what you will find when you check).

**Toxic version (social media):** You open Twitter and find outrage, gossip, hot takes. The "reward" is emotional arousal — anger, schadenfreude, tribal validation. The reward has nothing to do with truth or utility. A wrong take with good framing gets more engagement than a right one.

**Sentinel version:** You open the app and find prediction outcomes. Did the AAPL signal hit? Did the contrarian play work? What is the new high-confidence alert? The "reward" is being right about the real world. The variable element — you genuinely do not know if the prediction was correct until you check — creates the same dopamine loop, but the feedback is objective and the outcome is educational.

**The cycle:**

1. Signal fires with a prediction and confidence score
2. User sees the prediction and forms their own view
3. Time passes (1 hour to 7 days depending on the horizon)
4. The market resolves the prediction objectively
5. The user's streak and accuracy metrics update
6. The user returns to check the next prediction's outcome

**Critical design detail:** The delayed gratification is the feature, not a bug. Social media rewards are instant (post → likes in seconds). Sentinel rewards are delayed (prediction → outcome in hours or days). This delay is what makes the engagement loop healthy — it trains the user in calibration (knowing what you actually know) rather than performance (looking smart in the moment). The delay also creates a natural, non-manufactured reason to return to the app.

**UX implementation:**

- Prediction cards display a countdown timer showing when the prediction resolves. The timer is subtle (small text, muted color) — it creates anticipation without anxiety.
- When a prediction resolves, it animates briefly: a green pulse for correct, a red pulse for incorrect. The animation is 0.5 seconds, not prolonged. It is a moment of feedback, not a celebration or punishment.
- The predictions page defaults to "pending" predictions sorted by resolution time (soonest first). The user sees what is about to resolve, creating a natural "check back soon" motivation.
- Resolved predictions are accessible but not the default view. The user must tap "Resolved" to see history. This prevents dwelling on misses or basking in hits — the focus stays on what is coming next.

### Loop 2: The Morning Ritual Replacement

**Psychological mechanism:** Loss aversion and FOMO (the fear of missing something everyone else saw, which drives compulsive checking).

**Toxic version (social media):** "Everyone is talking about this and you haven't seen it." Manufactured urgency. An infinite timeline that makes you feel perpetually behind. No matter how much you scroll, there is always more. You close the app feeling vaguely anxious and inadequately informed.

**Sentinel version:** A finite, personalized, 2-minute briefing that ends. Five to eight cards specific to your watchlist. Each card has a confidence score and a one-line actionable insight. Then a "you're caught up" screen that genuinely means it. You close the app feeling informed and ready to start your day.

**UX implementation:**

- The morning briefing is generated at 6:00 AM local time and delivered as a push notification at the user's configured time (default 7:00 AM). The notification text is: "Your morning briefing is ready. [N] signals for your watchlist."
- The briefing is a vertical stack of cards. Each card contains: ticker symbol (bold, monospace), signal type icon, confidence percentage (color-coded: green above 70%, amber 50-70%, red below 50%), one-line insight (maximum 120 characters), and a timestamp.
- The cards are ordered by confidence score descending. The highest-confidence signal is always first. The user sees the most actionable information immediately.
- After the last card, the "caught up" screen appears. Design: a generous whitespace area with centered text reading "You're caught up" in 18px medium weight, followed by "We'll notify you if something changes for your watchlist" in 14px secondary color. There is no button to load more, no suggestion to explore other tickers, and no algorithmic recommendation. The screen is genuinely empty by design.
- If there are zero signals above the user's threshold, the briefing notification is not sent. The user is not notified of the absence of news. They receive the notification only when there is something worth seeing.
- The briefing includes a "yesterday's scorecard" section at the bottom: a compact row showing predictions made yesterday and their outcomes (checkmarks and X marks). This is the start of Loop 1 for the day — the user sees whether yesterday's predictions were correct, which creates the motivation to check today's predictions tomorrow.

### Loop 3: The Contrarian Confidence Builder

**Psychological mechanism:** Social proof and herding (the instinct to follow the crowd for safety, which social media amplifies into mob behavior and echo chambers).

**Toxic version (social media):** Dissent gets downvoted. Popular opinions get algorithmically amplified. Echo chambers form. Being right and being popular are completely uncorrelated, but the platform rewards popularity. Users learn to perform consensus views rather than think independently.

**Sentinel version:** The contrarian detector explicitly rewards going against the crowd when evidence supports it. The signal says: "Here are three data-backed reasons the majority might be wrong." Over time, users develop genuine analytical confidence — the ability to hold a position that differs from consensus because they have examined the evidence — rather than herd reflexes.

**UX implementation:**

- Contrarian alerts have a distinct visual treatment: a amber/gold accent border on the signal card, distinguishing them from standard bullish/bearish signals. The visual language communicates "this is different, pay attention."
- The card layout for contrarian alerts: "Contrarian: [TICKER]" as the header, followed by "Crowd sentiment: [bearish/bullish]" on the left, "Evidence against crowd:" on the right, with three bullet points listing the contradicting sources (e.g., "Congressional insider bought $200K on 3/18," "Polymarket odds at 72% for earnings beat," "Binance order flow shows accumulation"). Minimum three sources required for the alert to fire.
- The "Explain" view for contrarian alerts includes a temporal evidence chain showing the chronological order of signals: which source signaled first, how long before the others followed, and what the information cascade looks like. This is educational — the user learns how information propagates across platforms.
- Contrarian alerts are never framed as "Sentinel thinks the crowd is wrong." They are framed as "Multiple independent data sources disagree with prevailing sentiment." The distinction matters: Sentinel presents evidence, not opinions. The user decides.

### Loop 4: The Understanding Deepener

**Psychological mechanism:** Novelty seeking (the brain's craving for new information, which drives endless scrolling through feeds with superficial, disposable content).

**Toxic version (social media):** Infinite scroll of bite-sized content. Each item is just interesting enough to stop you from closing the app, but not substantial enough to teach you anything. You consume 200 pieces of content in 30 minutes and retain none of it. The novelty craving is fed but never satisfied.

**Sentinel version:** Every signal is an entry point into genuine understanding. The "Explain" button and causal evidence chains turn a data point into a story: why this signal matters, how the information cascaded across platforms, what historical precedent exists, and what it means for your specific situation. The novelty craving is satisfied with depth instead of breadth.

**UX implementation:**

- Every signal card has an "Explain" button in the bottom-right corner. The button is always present and always functional. It is not a premium feature — it is the core of the learning experience.
- Tapping "Explain" opens a slide-up panel (not a new page — the user maintains context of the signal feed). The panel contains:
  - A plain-language summary of what the signal means (2-3 sentences, written by Claude API, personalized to the user's watchlist context).
  - An evidence chain: a vertical timeline showing when each source first mentioned the entity, in chronological order, with timestamps and source labels. This shows how information propagated.
  - Historical context: "Similar patterns in the past" with 2-3 analogous situations and their outcomes. This grounds the current signal in precedent.
  - Confidence breakdown: a horizontal bar showing how much each factor (sentiment, SVC, technicals, correlation, microstructure, order flow) contributed to the overall confidence score.
- The Explain panel has a natural endpoint — it shows the information and stops. There is no "related signals" recommendation, no "you might also want to explore" section. If the user wants to explore another signal, they close the panel and return to the feed.
- The scenario simulator ("What If" button) is accessible from within the Explain panel. If the user's curiosity extends to hypotheticals, they can explore without leaving the context. But the simulator is behind an explicit tap — never auto-loaded.

### Loop 5: The Earned Reputation System

**Psychological mechanism:** Identity investment (the more you build your profile on a platform, the harder it is to leave — but also the more meaningful the identity becomes).

**Toxic version (social media):** Curated personas. Years of followers. Sunk cost of your "brand." Leaving means losing your identity. The identity is based on performance and popularity, not substance. A user with 100K followers and consistently wrong predictions has more "status" than a user with 100 followers and 70% accuracy.

**Sentinel version:** Your identity on Sentinel is your prediction track record. It is portable (exportable as CSV or JSON anytime), verifiable (against the public leaderboard), and based on one metric: how often you were right. The investment is in knowledge and skill, not vanity. The more you use Sentinel, the more data backs your reputation — but you can leave anytime and take your track record with you.

**UX implementation:**

- Every user has a profile page showing: overall accuracy percentage (prominent, large font), total predictions tracked, accuracy by sector, accuracy by time horizon, best and worst tickers, and a calibration curve (plotting stated confidence vs. actual accuracy).
- The profile page is public by default. Any user can see any other user's accuracy data. This transparency prevents gaming — you cannot present yourself as accurate if your track record says otherwise.
- Reputation scores are purely mathematical: prediction accuracy over the last 90 days, weighted by confidence (high-confidence predictions that resolve correctly contribute more than low-confidence ones). The formula is published and auditable.
- Users who contribute human signals to the community intelligence layer see a separate "signal contribution accuracy" metric: how often their contributed signals improved the pipeline's overall accuracy. This is the hardest metric to game — it requires consistently providing non-obvious, correct information that the automated pipeline missed.
- Shareable accuracy badges are generated from the profile data. The badge design: a card showing the user's accuracy percentage, the time period, the number of predictions, and a "verified by Sentinel" mark with a link to the public leaderboard. The badge cannot be edited or customized — it shows exactly what the data says.
- Data portability: every user can export their complete prediction history, accuracy metrics, and contributed signals as a CSV or JSON file at any time. There is no lock-in. If a user leaves Sentinel, they take their entire track record with them. This is the opposite of social media's data hostage strategy — and it builds trust that drives retention far more effectively than lock-in.

---

## Part 3: Anti-Patterns — Things Sentinel Must Never Do

These are specific design patterns that are explicitly prohibited. If any of these patterns appear in a design review, the feature must be redesigned before shipping.

### Category 1: Manufactured Urgency

- **Never show notification count badges.** Not on the app icon, not on navigation items, not anywhere. Badges create anxiety about unread content. Sentinel shows relevant content when the user opens the app.
- **Never display "X people are looking at this right now."** Real-time viewer counts manufacture social urgency and herd behavior. If a signal is important, its confidence score communicates that. The number of other people viewing it is irrelevant.
- **Never use countdown timers on limited offers, subscription promotions, or feature access.** Artificial scarcity is a dark pattern. If a price is going to change, state it plainly: "Price increases to $X on [date]."
- **Never send re-engagement notifications.** "You haven't opened Sentinel in 3 days" is manipulation. If the user hasn't opened the app, it means their watchlist has no high-confidence signals — which is the correct behavior. The app should be quiet when it has nothing useful to say.
- **Never auto-play the next signal, card, or piece of content.** The user decides when to engage with the next item. Auto-play removes agency and creates the passive consumption loop that defines toxic social media.

### Category 2: Engagement Inflation

- **Never rank content by engagement metrics.** Signals are ranked by confidence score and recency. How many users viewed, clicked, or shared a signal is never used as a ranking factor. Popularity is not a proxy for accuracy.
- **Never insert algorithmic recommendations between feed items.** The signal feed shows the user's watchlist signals above their confidence threshold, in chronological order. There are no "recommended for you," "trending," or "popular" insertions between signals.
- **Never create artificial streaks.** Login streaks, daily check-in rewards, and "maintain your streak" reminders are prohibited. The only streaks in Sentinel are prediction accuracy streaks, which are tied to real-world outcomes. If the user does not log in for a week, they miss nothing — their predictions still resolve and their accuracy still updates.
- **Never implement pull-to-refresh with a loading spinner.** Slot machine psychology relies on the variable-delay reveal. Sentinel's feed updates in real time via WebSocket. If new signals arrive while the user is viewing the feed, they appear at the top with a subtle animation. No manual refresh, no anticipatory delay.
- **Never use progress bars or completion indicators that create artificial goals.** "You've viewed 7 of 12 signals today!" creates a compulsion to reach 12. Sentinel has no concept of "how much you've consumed today."

### Category 3: Dark UX Patterns

- **Never use negative framing in cancellation flows.** "You'll lose access to your predictions" is loss-framing. The cancellation flow is: one button labeled "Cancel subscription," a confirmation page that says "Your subscription will end on [date]. Your prediction history remains accessible. Cancel?" with two buttons: "Cancel subscription" and "Keep subscription." No guilt. No countdown. No "last chance" offers.
- **Never pre-check opt-in boxes.** Email notifications, marketing communications, and data sharing preferences default to off. The user opts in explicitly.
- **Never use confusing double negatives in settings.** "Disable notification suppression" is prohibited. Settings use affirmative language: "Receive alerts for signals above [threshold]."
- **Never hide the unsubscribe mechanism.** Every email has a one-click unsubscribe link at the top (not the bottom). Every notification has a "Turn off" option directly on the notification.
- **Never A/B test manipulative patterns.** A/B testing is permitted for layout, color, copy, and UX flow. It is never permitted for testing whether a more manipulative version converts better. If the test variant would violate any principle in this document, the test does not run.

### Category 4: Information Asymmetry

- **Never hide accuracy data.** If Sentinel's prediction accuracy drops, it is displayed prominently, not buried in a settings page.
- **Never cherry-pick favorable time periods for displayed metrics.** The default accuracy display always shows the most recent 30-day rolling window. Users can change the window, but the default is always the most recent data, including any recent drawdowns.
- **Never display accuracy for a subset of predictions that makes the platform look better.** "83% accuracy on high-confidence signals" is prohibited unless accompanied by overall accuracy on all signals. Selective disclosure is dishonest.
- **Never suppress negative reviews or user feedback.** If the community intelligence layer includes user-submitted signals, negative sentiment about Sentinel itself is not filtered.
- **Never amplify negative sentiment disproportionately.** Bearish signals receive exactly the same visual treatment, ranking weight, and prominence as bullish signals. The platform does not favor fear or greed.

---

## Part 4: Notification Architecture

Notifications are the highest-stakes design decision in the product. Every notification that reaches the user's phone competes with their attention, their focus, and their peace of mind. Sentinel's notification system must add value proportional to the interruption cost.

### Notification Tiers

**Tier 1: Morning Briefing (daily, scheduled)**

Delivered at the user's configured time (default 7:00 AM local). Contains the summary of overnight signals for their watchlist. This is the only scheduled notification. If there are zero signals above the user's threshold, the notification is not sent.

Format: "Your morning briefing: [N] signals for your watchlist. Highest: [TICKER] at [X]% confidence."

**Tier 2: High-Confidence Alert (real-time, threshold-gated)**

Delivered immediately when a signal exceeds the user's configured confidence threshold (default: 80%). This is the only real-time notification. Maximum frequency: 5 per day. If more than 5 signals exceed the threshold in a single day, only the top 5 by confidence are delivered; the rest appear in the app when the user opens it.

Format: "[TICKER]: [Signal type] at [X]% confidence. [One-line insight]."

**Tier 3: Prediction Resolution (batched, daily)**

Delivered once per day (default: 5:00 PM local) as a single summary notification. Contains the outcomes of all predictions that resolved since the last batch. This is the prediction accountability loop — the user checks to see whether their predictions hit.

Format: "Today's results: [N] correct, [M] incorrect. Your 7-day accuracy: [X]%."

**Tier 4: Weekly Digest (weekly, scheduled)**

Delivered once per week (default: Sunday 9:00 AM local). Contains a summary of the week's prediction performance, notable contrarian signals, product ideation highlights, and any accuracy milestone changes. This is for users who prefer weekly engagement.

Format: "Your weekly Sentinel digest: [X]% accuracy this week across [N] predictions. [Notable highlight]."

### Notification Rules

- No notification type is enabled by default except Tier 1 (morning briefing). All other tiers must be explicitly opted into during onboarding or in settings.
- Every notification includes a "Mute for today" action button. One tap silences all Sentinel notifications until tomorrow. No confirmation dialog.
- The total notification budget across all tiers is 8 per day maximum. This includes the morning briefing, high-confidence alerts, and resolution summary. If the budget is exhausted, remaining alerts appear only in-app.
- Notifications never include engagement bait: no "You won't believe what happened to NVDA" framing, no "Breaking:" prefix (unless the signal is genuinely breaking — a new SEC filing or congressional trade within the last 30 minutes), no urgency language like "Act now" or "Don't miss this."
- Notification content must be self-contained. The user should be able to read the notification and decide whether to act without opening the app. This respects their time and reduces unnecessary app opens.

---

## Part 5: The "Caught Up" State System

The "caught up" state is the most important design element in the entire product. It is the single feature that most clearly distinguishes Sentinel from social media. Every major screen has a defined caught-up state.

### Signal Feed — Caught Up

**Trigger:** All signals in the user's watchlist are either below their confidence threshold or have been displayed in the current session.

**Display:** Centered text: "You're caught up" (18px, medium weight, primary color). Below: "No new signals above your [X]% confidence threshold for your watchlist. We'll notify you when something changes." (14px, secondary color). Below that: a subtle link: "Lower your threshold" in text-info color, which opens settings. This gives the user agency to see more signals if they choose, but does not push content.

**What is NOT displayed:** No "explore other tickers," no "trending signals," no "popular among other users," no algorithmically recommended content. The screen is genuinely done.

### Predictions Page — Caught Up

**Trigger:** All active predictions are displayed and no new predictions have been issued since the last session.

**Display:** The active predictions remain visible (they are still pending resolution). Below the last prediction: "No new predictions since your last visit. Predictions update as new signals enter the pipeline." No artificial call-to-action.

### Morning Briefing — Caught Up

**Trigger:** The user has scrolled past the last briefing card.

**Display:** Full-screen (below the last card) centered display: "That's everything for this morning" (18px, medium weight). Below: "Your next briefing arrives tomorrow at [configured time]. We'll alert you during the day only for signals above [X]% confidence." (14px, secondary color).

### Product Ideation — Caught Up

**Trigger:** All product gaps above the user's configured opportunity score threshold have been displayed.

**Display:** "No new product opportunities above your [X] score threshold. The pipeline is monitoring [N] review sources for new gaps." (14px, secondary color).

---

## Part 6: Onboarding Design

Onboarding is the user's first impression of Sentinel's engagement philosophy. It must communicate three things immediately: the platform is honest, the platform respects your time, and the platform measures itself by accuracy.

### Step 1: Watchlist Setup (mandatory)

The user selects 3-10 tickers for their watchlist. This is the only mandatory step. The watchlist determines what the morning briefing shows, what the signal feed displays, and what predictions are tracked. Without a watchlist, the platform has nothing personalized to show.

Design: A search field with autocomplete. Popular tickers displayed as quick-select chips below. No guidance on "what's trending" — the user picks what they care about, not what is popular.

### Step 2: Confidence Threshold (mandatory, with sensible default)

The user sets their confidence threshold. Default: 70%. A slider from 50% to 95% with an explanation: "You'll only see signals and receive alerts when Sentinel's confidence exceeds this threshold. Higher = fewer signals, but each is more likely to be correct. You can change this anytime."

Design: A simple slider with a preview: "At 70%, you'd have seen [N] signals last week for your watchlist." This grounds the abstract threshold in concrete experience.

### Step 3: Notification Preferences (optional, defaults to minimal)

Morning briefing: on by default (the only default-on notification).
High-confidence alerts: off by default. Explanation: "Real-time alerts when a signal exceeds your threshold. Maximum 5 per day."
Daily resolution summary: off by default.
Weekly digest: off by default.

Design: Toggle switches with clear descriptions. No dark patterns. No pre-checked boxes.

### Step 4: Transparency Introduction (mandatory, 10-second read)

A single screen that says: "Sentinel publishes its prediction accuracy publicly. You can see our track record — including our misses — at any time on the prediction leaderboard. We believe you should only trust a platform that shows you when it's wrong."

This communicates Principle 3 immediately and sets the expectation that Sentinel is different.

---

## Part 7: The Insight-Per-Minute Metric

Social media optimizes for time on platform. More minutes equals more ad impressions equals more revenue. The user's wellbeing is inversely correlated with the business metric — the platform profits from keeping you scrolling even when it makes you miserable.

Sentinel optimizes for insight per minute. The goal is maximum value delivered in minimum time. The internal metric that governs product decisions is:

**Insight per minute = (actionable signals consumed + predictions resolved + explanations viewed) / total minutes in app**

This metric is tracked internally and influences product decisions. Features that increase total time without increasing insight (infinite scroll, auto-play, recommended content) decrease the metric and are rejected. Features that increase insight without increasing time (better morning briefing curation, faster Explain loading, more concise signal cards) improve the metric and are prioritized.

**Target engagement profile:**

- Morning session: 2-3 minutes (briefing + yesterday's scorecard)
- Mid-day alerts: 0-2 minutes (read notification, optionally open one signal)
- Evening deep dive (optional): 5-10 minutes (explore evidence chains, run scenarios, contribute signals)
- Total daily engagement: 8-15 minutes across 2-3 sessions
- Weekly sessions: 5-7 days per week for paid users

This is deliberately less time than social media. A Twitter user averages 31 minutes per day. A TikTok user averages 58 minutes per day. Sentinel targets 8-15 minutes — and every one of those minutes delivers measurable value.

The business model supports this. Sentinel's revenue comes from subscriptions, not advertising. There is no incentive to maximize time on platform. The incentive is to maximize perceived value per session, which drives subscription retention. A user who spends 10 focused minutes and thinks "that was worth my time" renews. A user who spends 45 scattered minutes and feels vaguely manipulated does not.

---

## Part 8: Visual Design Language for Engagement States

### Color Semantics

- **Bullish / correct / positive outcome:** Teal (#00FFC2 in the Sentinel dark theme, or the teal ramp in light theme). Never bright green — green triggers "go/buy" associations that feel like financial advice. Teal is positive without being directive.
- **Bearish / incorrect / negative outcome:** Coral (#FF4B2B in dark theme, or coral ramp). Never bright red — red triggers panic. Coral communicates caution without alarm.
- **Contrarian / divergence:** Amber (#EF9F27 / amber ramp). Amber communicates "different, pay attention" without positive or negative valence.
- **Neutral / structural / caught-up states:** Gray (the gray ramp). The caught-up screen uses gray tones exclusively. No color = nothing to act on = you're done.
- **High confidence:** Signals above 80% confidence use a subtle teal left-border accent on the card. Signals between 60-80% have no accent. Signals below 60% have a subtle gray left-border (indicating lower certainty). The accent communicates signal strength without traffic-light anxiety.

### Animation Budget

- Prediction resolution: 0.5-second pulse (teal for correct, coral for incorrect). Single pulse, no looping.
- New signal arrival: 0.3-second slide-in from top. No bounce, no spring physics.
- Streak update: 0.4-second counter increment animation. The number counts up by one.
- Caught-up state: 0.6-second fade-in of the caught-up text. Gentle, calming.
- Page transitions: 0.2-second cross-fade. No slide, no zoom.
- Total animation per session: never more than 3-5 seconds of motion across the entire session. The product feels calm, not frenetic.

### Typography Hierarchy for Signal Cards

- Ticker symbol: 16px monospace, bold, primary color. Instantly scannable.
- Signal type: 12px, secondary color. "Bullish," "Bearish," "Contrarian."
- Confidence score: 14px, bold, color-coded (teal for 80%+, primary for 60-80%, gray for below 60%).
- Insight line: 13px, secondary color, maximum 120 characters. One sentence. No jargon.
- Timestamp: 11px, tertiary color, relative format ("3h ago," not "2026-03-22T14:47:00Z").
- Source badges: 11px, pill-shaped, background-secondary. "HN," "Reddit," "SEC," "Congress."

---

## Part 9: Integration with Product Roadmap

This behavioral design specification must be applied retroactively to all features in the product roadmap. Specifically:

### Phase 1 (Weeks 1-4): Foundation

- The backtesting results page must include a "model performance" section that honestly displays accuracy, miss rate, and worst-performing tickers.
- Prediction and backtesting pages must have defined caught-up states.

### Phase 2 (Weeks 5-8): User Experience

- **This phase should begin with a 1-week UX design sprint** that produces wireframes for: the morning briefing flow (including caught-up state), the notification architecture, the prediction resolution animation, the streak display (including streak break handling), and the Explain panel layout.
- The morning briefing must implement the finite card stack with caught-up state. No infinite scroll. No "load more."
- The prediction streak display must celebrate correct predictions without punishing misses. Streak breaks include a one-sentence explanation.
- The WebSocket alert system must respect the notification tier system and the 8-per-day budget.
- The "Explain" button must be present on every signal card from day one, not added later as a premium feature.

### Phase 3 (Weeks 9-14): Differentiation

- The contrarian detector must enforce the 3-source minimum before firing.
- The public prediction leaderboard must display misses as prominently as hits.
- The community signal submission system must use accuracy-based reputation, not engagement metrics.

### Phase 4 (Weeks 15-20): Cloud and Monetization

- The subscription flow must use honest cancellation (one click, no guilt screens).
- The pricing page must not use dark patterns (no countdown timers, no "most popular" badges that change based on the user's browsing behavior).
- The free tier must not feel degraded or nag-heavy. Free users see a "caught up" screen, not an "upgrade to see more" screen.

### Phase 5 (Weeks 21-30): Scale

- The scenario simulator must have a clear endpoint (simulation complete, results displayed, no follow-up recommendations).
- The Sentinel Academy must not use engagement metrics to gate content. All purchased content is permanently accessible. No "watch before it expires" patterns.
- The data licensing API documentation must honestly describe accuracy limitations and update frequency.

---

## Appendix: Daily User Journey Reference

**7:01 AM — Morning briefing (30 seconds)**
Open briefing. 5-8 cards for your watchlist, ordered by confidence. Each card: ticker, signal type, confidence %, one-line insight. No ads, no recommendations, no trending sidebar.

**7:02 AM — Yesterday's scorecard (40 seconds)**
Bottom of briefing: prediction outcomes from yesterday. Green checks, red X marks. Your streak status. Your rolling accuracy. Misses shown first.

**7:03 AM — Caught up (0 seconds)**
"You're caught up." Close the app. Start your day.

**11:47 AM — Mid-day alert (15 seconds)**
Push notification: "MSFT: Contrarian alert (87% confidence). Bearish crowd sentiment vs. congressional buys + Polymarket odds." Read it. Decide whether to open the app. The notification is self-contained — you can act on it or ignore it without opening anything.

**7:30 PM — Evening deep dive (5-10 minutes, optional)**
Open the MSFT signal. Tap Explain. See the evidence chain: Senator bought options on March 18, HN picked up the Azure outage story on March 19, Reddit went bearish on March 20, Polymarket never dropped below 68%. Historical analogs: 3 similar patterns, 2 resulted in positive earnings surprise. Run a "What If" scenario: "What if MSFT beats by 10%?" See projected impact across your watchlist. Submit a human signal you noticed in a niche publication. Close the app.

**Total: 8-13 minutes. Every minute delivered measurable value.**
