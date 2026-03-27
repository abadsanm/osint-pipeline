## UX Design Principles (Behavioral Design Spec)

These rules govern ALL user-facing feature implementation. See docs/BEHAVIORAL_DESIGN_SPEC.md for full rationale.

### Three Laws (every feature must satisfy all three)
1. **Finite by default, infinite by choice.** Every feed and page has a "you're caught up" state. Deep dives are available on-demand but never auto-loaded or recommended.
2. **Every reward tied to real-world outcomes.** No likes, no follower counts, no engagement metrics. Streaks = consecutive correct predictions. Reputation = prediction accuracy.
3. **Radical honesty.** Misses shown before hits. Accuracy drops displayed prominently. One-click cancel, no guilt screens.

### Feed & Notification Rules
- Morning briefing: max 8 cards, then "caught up" screen. No "load more."
- Signal feed: only signals above user's confidence threshold. No algorithmic recommendations between items.
- If zero signals above threshold: do NOT send notification. Silence is honest.
- Notification budget: max 8/day across all tiers. High-confidence alerts: max 5/day.
- Notifications must be self-contained (user can decide without opening app).

### Prediction Display Rules
- Every prediction card shows outcome when resolved: ✓ (correct), ✗ (incorrect), — (pending).
- Prediction streaks celebrate correct predictions. Broken streaks display explanation: "Streak ended because [reason]. Your 30-day accuracy is still X%."
- Default predictions view: "pending" sorted by soonest resolution. Not sorted by engagement.
- Contrarian alerts require minimum 3 independent data sources. Never fire with fewer.

### Anti-Patterns (NEVER implement these)
- NO notification count badges anywhere
- NO "X people are looking at this" displays
- NO infinite scroll on any page
- NO auto-play of next signal/card
- NO content ranked by engagement/popularity
- NO re-engagement notifications ("You haven't opened Sentinel in 3 days")
- NO dark patterns in subscription flows (countdown timers, guilt screens, loss framing)
- NO pull-to-refresh with loading spinner (use WebSocket real-time updates)
- NO progress bars that create artificial completion goals

### Visual Language
- Correct prediction: teal pulse (0.5s). Incorrect: coral pulse (0.5s).
- Confidence coloring: ≥80% = teal left-border accent, 60-79% = no accent, <60% = gray accent.
- Caught-up state: gray tones only. No color = nothing to act on.
- Total animation per session: never more than 3-5 seconds. Calm, not frenetic.

### "Explain" Button
- Present on EVERY signal card. Not premium-gated.
- Opens as slide-up panel (maintains feed context), not new page.
- Contains: plain-language summary, evidence chain timeline, historical analogs, confidence breakdown.
- Has natural endpoint. No "related signals" or follow-up recommendations.
