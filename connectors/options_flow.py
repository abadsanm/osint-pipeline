"""
Options flow connector — free alternative to paid services.

Fetches options data from Yahoo Finance for watchlist tickers:
- Put/call ratio
- Unusual volume (options volume vs open interest)
- Near-term implied volatility changes

No API key required. Uses yfinance library.

Usage:
    python -m connectors.options_flow
"""

import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from typing import Optional

from connectors.kafka_publisher import KafkaPublisher, apply_poll_multiplier, wait_for_bus
from schemas.document import (
    ContentType,
    OsintDocument,
    QualitySignals,
    SourcePlatform,
)

log = logging.getLogger("options-flow-connector")


class OptionsFlowConfig:
    KAFKA_BOOTSTRAP = "localhost:9092"
    KAFKA_TOPIC = "finance.options.flow"
    POLL_INTERVAL = 1800  # 30 min — options data doesn't change fast
    WATCHLIST = [
        "AAPL", "TSLA", "NVDA", "MSFT", "GOOGL", "META", "AMZN",
        "SPY", "QQQ", "AMD", "PLTR", "RKLB",
    ]


def load_config_from_env():
    from pathlib import Path

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

    bootstrap = os.environ.get("KAFKA_BOOTSTRAP")
    if bootstrap:
        OptionsFlowConfig.KAFKA_BOOTSTRAP = bootstrap

    symbols = os.environ.get("OPTIONS_WATCHLIST")
    if symbols:
        OptionsFlowConfig.WATCHLIST = [s.strip() for s in symbols.split(",")]

    poll = os.environ.get("OPTIONS_POLL_INTERVAL")
    if poll:
        OptionsFlowConfig.POLL_INTERVAL = int(poll)

    apply_poll_multiplier(OptionsFlowConfig, "POLL_INTERVAL")


def fetch_options_data(ticker: str) -> Optional[dict]:
    """Fetch options chain data for a ticker using yfinance."""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        # Get available expiration dates
        expirations = stock.options
        if not expirations:
            return None

        # Get the nearest expiration
        nearest_exp = expirations[0]
        chain = stock.option_chain(nearest_exp)

        calls = chain.calls
        puts = chain.puts

        if calls.empty and puts.empty:
            return None

        # Compute metrics
        total_call_volume = int(calls["volume"].sum()) if "volume" in calls.columns else 0
        total_put_volume = int(puts["volume"].sum()) if "volume" in puts.columns else 0
        total_call_oi = int(calls["openInterest"].sum()) if "openInterest" in calls.columns else 0
        total_put_oi = int(puts["openInterest"].sum()) if "openInterest" in puts.columns else 0

        # Put/call ratio
        pc_ratio = total_put_volume / total_call_volume if total_call_volume > 0 else 0.0

        # Unusual activity: volume >> open interest
        call_vol_oi = total_call_volume / total_call_oi if total_call_oi > 0 else 0.0
        put_vol_oi = total_put_volume / total_put_oi if total_put_oi > 0 else 0.0

        # Average implied volatility
        avg_call_iv = float(calls["impliedVolatility"].mean()) if "impliedVolatility" in calls.columns else 0.0
        avg_put_iv = float(puts["impliedVolatility"].mean()) if "impliedVolatility" in puts.columns else 0.0

        # Find most active strike
        most_active_call = None
        if not calls.empty and "volume" in calls.columns:
            top = calls.nlargest(1, "volume").iloc[0]
            most_active_call = {
                "strike": float(top.get("strike", 0)),
                "volume": int(top.get("volume", 0)),
                "oi": int(top.get("openInterest", 0)),
                "iv": float(top.get("impliedVolatility", 0)),
            }

        most_active_put = None
        if not puts.empty and "volume" in puts.columns:
            top = puts.nlargest(1, "volume").iloc[0]
            most_active_put = {
                "strike": float(top.get("strike", 0)),
                "volume": int(top.get("volume", 0)),
                "oi": int(top.get("openInterest", 0)),
                "iv": float(top.get("impliedVolatility", 0)),
            }

        return {
            "ticker": ticker,
            "expiration": nearest_exp,
            "total_call_volume": total_call_volume,
            "total_put_volume": total_put_volume,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "put_call_ratio": round(pc_ratio, 3),
            "call_volume_oi_ratio": round(call_vol_oi, 3),
            "put_volume_oi_ratio": round(put_vol_oi, 3),
            "avg_call_iv": round(avg_call_iv, 4),
            "avg_put_iv": round(avg_put_iv, 4),
            "most_active_call": most_active_call,
            "most_active_put": most_active_put,
        }
    except Exception as e:
        log.debug("Options data fetch failed for %s: %s", ticker, e)
        return None


def options_to_document(data: dict) -> OsintDocument:
    """Convert options data to OsintDocument."""
    ticker = data["ticker"]
    pc_ratio = data["put_call_ratio"]

    # Determine signal
    if pc_ratio > 1.5:
        signal = "high put/call ratio (bearish sentiment)"
    elif pc_ratio < 0.5:
        signal = "low put/call ratio (bullish sentiment)"
    else:
        signal = "neutral put/call ratio"

    # Check for unusual activity
    unusual = []
    if data["call_volume_oi_ratio"] > 2.0:
        unusual.append(f"unusual call volume ({data['call_volume_oi_ratio']:.1f}x open interest)")
    if data["put_volume_oi_ratio"] > 2.0:
        unusual.append(f"unusual put volume ({data['put_volume_oi_ratio']:.1f}x open interest)")

    unusual_str = "; ".join(unusual) if unusual else "no unusual activity"

    title = f"{ticker} options flow: P/C ratio {pc_ratio:.2f} — {signal}"
    content = (
        f"{ticker} options expiring {data['expiration']}: "
        f"Call volume {data['total_call_volume']:,}, Put volume {data['total_put_volume']:,}, "
        f"P/C ratio {pc_ratio:.2f}. "
        f"Call OI {data['total_call_oi']:,}, Put OI {data['total_put_oi']:,}. "
        f"Avg call IV {data['avg_call_iv']:.1%}, Avg put IV {data['avg_put_iv']:.1%}. "
        f"{unusual_str}."
    )

    # Most active strikes
    if data.get("most_active_call"):
        mc = data["most_active_call"]
        content += f" Most active call: ${mc['strike']:.0f} strike, {mc['volume']:,} volume."
    if data.get("most_active_put"):
        mp = data["most_active_put"]
        content += f" Most active put: ${mp['strike']:.0f} strike, {mp['volume']:,} volume."

    # Quality based on total volume
    total_vol = data["total_call_volume"] + data["total_put_volume"]
    quality = min(1.0, total_vol / 100000)  # High volume = high quality

    return OsintDocument(
        source=SourcePlatform.NEWS,
        source_id=f"options-{ticker}-{data['expiration']}",
        content_type=ContentType.METRIC,
        title=title,
        content_text=content,
        entity_id=ticker,
        created_at=datetime.now(timezone.utc),
        quality_signals=QualitySignals(score=quality * 10, engagement_count=total_vol),
        metadata={
            "options_data": data,
            "signal_type": "options_flow",
        },
    )


async def wait_for_kafka(bootstrap_servers: str, timeout: int = 60):
    wait_for_bus(bootstrap_servers, timeout)


async def main():
    load_config_from_env()
    await wait_for_kafka(OptionsFlowConfig.KAFKA_BOOTSTRAP)

    publisher = KafkaPublisher(
        OptionsFlowConfig.KAFKA_BOOTSTRAP,
        client_id="options-flow-connector",
    )

    log.info("=" * 60)
    log.info("Options Flow Connector running")
    log.info("  Watchlist: %s", OptionsFlowConfig.WATCHLIST)
    log.info("  Topic:     %s", OptionsFlowConfig.KAFKA_TOPIC)
    log.info("  Poll:      %ds", OptionsFlowConfig.POLL_INTERVAL)
    log.info("=" * 60)

    while True:
        try:
            published = 0
            for ticker in OptionsFlowConfig.WATCHLIST:
                data = await asyncio.get_event_loop().run_in_executor(
                    None, fetch_options_data, ticker
                )
                if data:
                    doc = options_to_document(data)
                    doc.compute_dedup_hash()
                    publisher.publish(
                        OptionsFlowConfig.KAFKA_TOPIC,
                        doc.to_kafka_key(),
                        doc.to_kafka_value(),
                    )
                    published += 1

            if published:
                publisher.flush(timeout=5.0)
                log.info("Published %d options flow documents", published)
            else:
                log.info("No options data available (market may be closed)")

        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Options flow error: %s", e, exc_info=True)

        await asyncio.sleep(OptionsFlowConfig.POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
