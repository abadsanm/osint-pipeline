"""Market microstructure indicators — pure computation functions.

Provides anchored VWAP, volume profile analysis, and fair value gap
detection.  All functions are stateless and perform no I/O; they operate
solely on lists of ``MarketBar`` instances.
"""

from __future__ import annotations

from datetime import datetime

from schemas.market_data import MarketBar


# ---------------------------------------------------------------------------
# Anchored VWAP
# ---------------------------------------------------------------------------

def compute_anchored_vwap(bars: list[MarketBar], anchor_time: datetime) -> list[float]:
    """Cumulative VWAP starting from *anchor_time*.

    Uses typical price ``(high + low + close) / 3`` for each bar.

    Returns one VWAP value per bar whose timestamp is at or after
    *anchor_time*.  Returns an empty list when no qualifying bars exist.
    """
    cum_pv = 0.0
    cum_vol = 0.0
    result: list[float] = []

    for bar in bars:
        if bar.timestamp < anchor_time:
            continue
        typical = (bar.high + bar.low + bar.close) / 3.0
        cum_pv += typical * bar.volume
        cum_vol += bar.volume
        result.append(cum_pv / cum_vol if cum_vol != 0.0 else typical)

    return result


# ---------------------------------------------------------------------------
# Volume Profile
# ---------------------------------------------------------------------------

def compute_volume_profile(bars: list[MarketBar], num_levels: int = 24) -> dict:
    """Build a volume-at-price profile over *num_levels* equal buckets.

    Returns a dict with keys ``poc_price``, ``value_area_high``,
    ``value_area_low``, and ``profile`` (list of ``{price, volume}``
    dicts ordered low-to-high).

    The **value area** captures the narrowest set of contiguous levels
    around the POC that contains at least 70 % of total volume.
    """
    if not bars:
        return {
            "poc_price": 0.0,
            "value_area_high": 0.0,
            "value_area_low": 0.0,
            "profile": [],
        }

    typicals = [(bar.high + bar.low + bar.close) / 3.0 for bar in bars]
    volumes = [bar.volume for bar in bars]

    price_low = min(typicals)
    price_high = max(typicals)

    # Edge case: all bars at the same price
    if price_high == price_low:
        total_vol = sum(volumes)
        single_price = price_low
        return {
            "poc_price": single_price,
            "value_area_high": single_price,
            "value_area_low": single_price,
            "profile": [{"price": single_price, "volume": total_vol}],
        }

    bucket_size = (price_high - price_low) / num_levels

    # Initialise buckets — each represented by its midpoint price
    bucket_volumes = [0.0] * num_levels
    bucket_prices = [
        price_low + bucket_size * (i + 0.5) for i in range(num_levels)
    ]

    for tp, vol in zip(typicals, volumes):
        idx = int((tp - price_low) / bucket_size)
        # Clamp the top edge into the last bucket
        if idx >= num_levels:
            idx = num_levels - 1
        bucket_volumes[idx] += vol

    # Point of Control — level with highest volume
    poc_idx = max(range(num_levels), key=lambda i: bucket_volumes[i])
    poc_price = bucket_prices[poc_idx]

    # Value area: expand outward from POC until >= 70 % of total volume
    total_vol = sum(bucket_volumes)
    va_vol = bucket_volumes[poc_idx]
    lo = poc_idx
    hi = poc_idx

    while va_vol < 0.70 * total_vol:
        expand_lo = bucket_volumes[lo - 1] if lo > 0 else -1.0
        expand_hi = bucket_volumes[hi + 1] if hi < num_levels - 1 else -1.0

        if expand_lo < 0 and expand_hi < 0:
            break  # no room to expand

        if expand_lo >= expand_hi:
            lo -= 1
            va_vol += bucket_volumes[lo]
        else:
            hi += 1
            va_vol += bucket_volumes[hi]

    value_area_low = bucket_prices[lo] - bucket_size / 2.0
    value_area_high = bucket_prices[hi] + bucket_size / 2.0

    profile = [
        {"price": bucket_prices[i], "volume": bucket_volumes[i]}
        for i in range(num_levels)
    ]

    return {
        "poc_price": poc_price,
        "value_area_high": value_area_high,
        "value_area_low": value_area_low,
        "profile": profile,
    }


# ---------------------------------------------------------------------------
# Fair Value Gaps
# ---------------------------------------------------------------------------

def detect_fair_value_gaps(bars: list[MarketBar]) -> list[dict]:
    """Detect Fair Value Gaps (FVGs) across a sequence of bars.

    A **bullish** FVG exists when ``bar[i].low > bar[i-2].high`` (price
    gapped up leaving an unfilled zone).  A **bearish** FVG exists when
    ``bar[i].high < bar[i-2].low`` (price gapped down).

    Returns a list of dicts with ``type``, ``gap_high``, ``gap_low``, and
    ``candle_index`` (the index of bar *i*).
    """
    gaps: list[dict] = []

    for i in range(2, len(bars)):
        if bars[i].low > bars[i - 2].high:
            gaps.append({
                "type": "bullish",
                "gap_high": bars[i].low,
                "gap_low": bars[i - 2].high,
                "candle_index": i,
            })
        elif bars[i].high < bars[i - 2].low:
            gaps.append({
                "type": "bearish",
                "gap_high": bars[i - 2].low,
                "gap_low": bars[i].high,
                "candle_index": i,
            })

    return gaps
