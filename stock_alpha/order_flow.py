"""Order flow analysis — pure computation functions.

Provides delta profile computation across price levels and liquidity sweep
detection by correlating aggregated trades against order book depth snapshots.
No I/O or side effects; all inputs and outputs are plain data structures.
"""

from __future__ import annotations

from schemas.market_data import AggregatedTrade, OrderBookSnapshot


def compute_order_flow_delta(
    trades: list[AggregatedTrade],
    price_levels: int = 20,
) -> dict:
    """Compute cumulative delta and per-level buy/sell volume profile.

    Splits the price range of *trades* into *price_levels* equal buckets and
    accumulates buyer-initiated volume (positive) and seller-initiated volume
    (negative) in each bucket.

    Parameters
    ----------
    trades:
        Aggregated trade events.  ``is_buyer_maker=True`` marks a
        seller-initiated trade; ``False`` marks a buyer-initiated trade.
    price_levels:
        Number of equal-width price buckets across the observed range.

    Returns
    -------
    dict with keys ``cumulative_delta``, ``delta_per_level``, ``buy_volume``,
    ``sell_volume``, and ``imbalance_ratio``.
    """
    if not trades:
        return {
            "cumulative_delta": 0.0,
            "delta_per_level": [],
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "imbalance_ratio": 0.0,
        }

    prices = [t.price for t in trades]
    min_price = min(prices)
    max_price = max(prices)

    # When all trades are at the same price, collapse to a single bucket.
    price_range = max_price - min_price
    if price_range == 0:
        price_range = 1.0  # avoid division by zero; single-bucket scenario

    bucket_width = price_range / price_levels

    # Initialise per-level accumulators.
    buy_vols = [0.0] * price_levels
    sell_vols = [0.0] * price_levels

    total_buy = 0.0
    total_sell = 0.0

    for trade in trades:
        # Map price to a bucket index (clamp the top edge into the last bucket).
        idx = int((trade.price - min_price) / bucket_width)
        if idx >= price_levels:
            idx = price_levels - 1

        if trade.is_buyer_maker:
            # Seller-initiated trade.
            sell_vols[idx] += trade.quantity
            total_sell += trade.quantity
        else:
            # Buyer-initiated trade.
            buy_vols[idx] += trade.quantity
            total_buy += trade.quantity

    cumulative_delta = total_buy - total_sell

    delta_per_level = []
    for i in range(price_levels):
        level_price = min_price + bucket_width * (i + 0.5)
        delta_per_level.append({
            "price": level_price,
            "delta": buy_vols[i] - sell_vols[i],
            "buy_vol": buy_vols[i],
            "sell_vol": sell_vols[i],
        })

    total_volume = total_buy + total_sell
    imbalance_ratio = abs(cumulative_delta) / total_volume if total_volume > 0 else 0.0

    return {
        "cumulative_delta": cumulative_delta,
        "delta_per_level": delta_per_level,
        "buy_volume": total_buy,
        "sell_volume": total_sell,
        "imbalance_ratio": imbalance_ratio,
    }


def detect_liquidity_sweeps(
    depth_snapshots: list[OrderBookSnapshot],
    trades: list[AggregatedTrade],
    threshold_pct: float = 0.5,
) -> list[dict]:
    """Detect trades that sweep through visible liquidity levels.

    For every trade, the closest prior (or equal-time) depth snapshot is found.
    A **bid sweep** occurs when a seller-initiated trade executes at or below
    ``best_bid * (1 - threshold_pct / 100)``.  An **ask sweep** occurs when a
    buyer-initiated trade executes at or above
    ``best_ask * (1 + threshold_pct / 100)``.

    Parameters
    ----------
    depth_snapshots:
        Order book snapshots sorted (or unsorted) by timestamp.
    trades:
        Aggregated trade events to evaluate.
    threshold_pct:
        Percentage distance from the best bid/ask that qualifies as a sweep.

    Returns
    -------
    List of dicts, each containing ``sweep_type``, ``price_level``,
    ``volume_swept``, and ``timestamp``.
    """
    if not depth_snapshots or not trades:
        return []

    # Sort snapshots by timestamp for efficient look-up.
    sorted_snaps = sorted(depth_snapshots, key=lambda s: s.timestamp)

    sweeps: list[dict] = []

    for trade in trades:
        # Find the closest snapshot at or before the trade time via linear scan
        # (a bisect would be faster but avoids an extra import for clarity; the
        # lists flowing through Kafka are bounded in practice).
        best_snap: OrderBookSnapshot | None = None
        for snap in sorted_snaps:
            if snap.timestamp <= trade.timestamp:
                best_snap = snap
            else:
                break

        if best_snap is None:
            continue

        # Safely extract best bid / best ask.
        has_bids = best_snap.bids and len(best_snap.bids) > 0 and len(best_snap.bids[0]) >= 2
        has_asks = best_snap.asks and len(best_snap.asks) > 0 and len(best_snap.asks[0]) >= 2

        if trade.is_buyer_maker and has_bids:
            # Seller-initiated — check for bid sweep.
            best_bid = best_snap.bids[0][0]
            sweep_threshold = best_bid * (1 - threshold_pct / 100)
            if trade.price <= sweep_threshold:
                sweeps.append({
                    "sweep_type": "bid",
                    "price_level": trade.price,
                    "volume_swept": trade.quantity,
                    "timestamp": trade.timestamp.isoformat(),
                })

        elif not trade.is_buyer_maker and has_asks:
            # Buyer-initiated — check for ask sweep.
            best_ask = best_snap.asks[0][0]
            sweep_threshold = best_ask * (1 + threshold_pct / 100)
            if trade.price >= sweep_threshold:
                sweeps.append({
                    "sweep_type": "ask",
                    "price_level": trade.price,
                    "volume_swept": trade.quantity,
                    "timestamp": trade.timestamp.isoformat(),
                })

    return sweeps
