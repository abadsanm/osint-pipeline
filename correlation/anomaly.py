"""
Coordinated inauthentic behavior detection.

Four heuristics to flag bot-driven hype:
  1. Account age clustering — high fraction of new accounts
  2. Temporal burst — mentions concentrated in a narrow sub-window
  3. Cross-platform sync — suspiciously tight timing without a catalyst
  4. Template content — near-identical text across different accounts
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from correlation.config import CorrelationConfig
from correlation.window_store import WindowSlice


def detect_anomalies(window: WindowSlice) -> list[str]:
    """Run all anomaly detection heuristics on a window.

    Returns a list of anomaly flag strings.
    """
    flags = []

    if len(window.mentions) < 3:
        return flags

    flag = _check_account_age(window)
    if flag:
        flags.append(flag)

    flag = _check_temporal_burst(window)
    if flag:
        flags.append(flag)

    flag = _check_cross_platform_sync(window)
    if flag:
        flags.append(flag)

    flag = _check_template_content(window)
    if flag:
        flags.append(flag)

    return flags


def _check_account_age(window: WindowSlice) -> str | None:
    """Flag if >40% of mentions come from accounts < 30 days old."""
    mentions_with_age = [
        m for m in window.mentions if m.account_age_days is not None
    ]
    if not mentions_with_age:
        return None

    young = sum(
        1 for m in mentions_with_age
        if m.account_age_days < CorrelationConfig.ANOMALY_LOW_ACCOUNT_AGE_DAYS
    )
    fraction = young / len(mentions_with_age)

    if fraction >= CorrelationConfig.ANOMALY_LOW_ACCOUNT_AGE_FRACTION:
        return "low_account_age_cluster"

    return None


def _check_temporal_burst(window: WindowSlice) -> str | None:
    """Flag if >70% of mentions arrive within a 5-minute sub-window.

    Requires at least 2 distinct sources to avoid false positives from
    single-source batch ingestion (e.g., HN connector polling).
    """
    if len(window.mentions) < 5:
        return None

    # Don't flag single-source bursts — these are usually batch ingestion
    sources = {m.source for m in window.mentions}
    if len(sources) < 2:
        return None

    burst_window = timedelta(
        minutes=CorrelationConfig.ANOMALY_BURST_WINDOW_MINUTES
    )
    sorted_mentions = sorted(window.mentions, key=lambda m: m.created_at)

    # Sliding sub-window check
    for i, anchor in enumerate(sorted_mentions):
        burst_end = anchor.created_at + burst_window
        count_in_burst = sum(
            1 for m in sorted_mentions[i:]
            if m.created_at <= burst_end
        )
        fraction = count_in_burst / len(sorted_mentions)
        if fraction >= CorrelationConfig.ANOMALY_BURST_FRACTION:
            return "coordinated_burst"

    return None


def _check_cross_platform_sync(window: WindowSlice) -> str | None:
    """Flag if mentions from different platforms appear within <2 minutes
    of each other without an SEC/news catalyst."""
    sync_window = timedelta(
        minutes=CorrelationConfig.ANOMALY_CROSS_PLATFORM_SYNC_MINUTES
    )

    # Group mentions by source
    by_source: dict[str, list] = {}
    for m in window.mentions:
        by_source.setdefault(m.source, []).append(m)

    sources = list(by_source.keys())
    if len(sources) < 2:
        return None

    # Check if there's a legitimate catalyst (SEC filing or news)
    has_catalyst = any(
        s in ("sec_edgar", "news") for s in sources
    )
    if has_catalyst:
        return None

    # Check for tight cross-platform timing
    for i, s1 in enumerate(sources):
        for s2 in sources[i + 1 :]:
            for m1 in by_source[s1]:
                for m2 in by_source[s2]:
                    delta = abs(m1.created_at - m2.created_at)
                    if delta <= sync_window:
                        return "suspicious_cross_platform_sync"

    return None


def _check_template_content(window: WindowSlice) -> str | None:
    """Flag if >30% of mentions have near-identical content (same dedup_hash).

    Requires at least 2 sources and 5+ mentions with hashes to avoid
    false positives from single-source content patterns.
    """
    hashes = [m.dedup_hash for m in window.mentions if m.dedup_hash]
    if len(hashes) < 5:
        return None

    # Don't flag single-source — similar short comments are normal
    sources = {m.source for m in window.mentions}
    if len(sources) < 2:
        return None

    # Count hash frequencies (exact matches via dedup_hash)
    hash_counts = Counter(hashes)
    most_common_count = hash_counts.most_common(1)[0][1]

    fraction = most_common_count / len(hashes)
    if fraction >= CorrelationConfig.ANOMALY_TEMPLATE_FRACTION:
        return "template_content"

    return None
