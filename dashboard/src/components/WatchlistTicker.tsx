"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";

const API_BASE = "http://localhost:8000/api";

interface WatchlistItem {
  ticker: string;
  price: number | null;
  change_pct: number | null;
}

export default function WatchlistTicker() {
  const router = useRouter();
  const [items, setItems] = useState<WatchlistItem[]>([]);
  const cacheRef = useRef<Record<string, { price: number; change_pct: number; ts: number }>>({});

  const fetchWatchlist = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/settings`);
      if (!res.ok) return;
      const settings = await res.json();
      const watchlist: string[] = settings.watchlist || [];
      if (watchlist.length === 0) {
        setItems([]);
        return;
      }

      const now = Date.now();
      const CACHE_TTL = 30_000; // 30s cache per ticker

      const results: WatchlistItem[] = await Promise.all(
        watchlist.map(async (ticker) => {
          // Use cache if fresh
          const cached = cacheRef.current[ticker];
          if (cached && now - cached.ts < CACHE_TTL) {
            return { ticker, price: cached.price, change_pct: cached.change_pct };
          }

          try {
            const alphaRes = await fetch(`${API_BASE}/alpha/${encodeURIComponent(ticker)}`);
            if (alphaRes.ok) {
              const data = await alphaRes.json();
              const price = data.price?.current ?? data.price?.close ?? null;
              const change_pct = data.price?.change_pct ?? null;
              if (price !== null) {
                cacheRef.current[ticker] = { price, change_pct: change_pct ?? 0, ts: now };
              }
              return { ticker, price, change_pct };
            }
          } catch {
            // API unavailable for this ticker
          }
          // Return cached stale data if fetch fails
          if (cached) {
            return { ticker, price: cached.price, change_pct: cached.change_pct };
          }
          return { ticker, price: null, change_pct: null };
        }),
      );

      setItems(results);
    } catch {
      // API not available
    }
  }, []);

  useEffect(() => {
    fetchWatchlist();
    const interval = setInterval(fetchWatchlist, 30_000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  if (items.length === 0) return null;

  const renderItem = (item: WatchlistItem, idx: number) => {
    const bullish = (item.change_pct ?? 0) >= 0;
    const arrow = bullish ? "\u25B2" : "\u25BC";
    const colorCls = bullish ? "text-bullish" : "text-bearish";

    return (
      <button
        key={`${item.ticker}-${idx}`}
        onClick={() => router.push(`/alpha/${item.ticker}`)}
        className="inline-flex items-center gap-1.5 mx-4 flex-shrink-0 hover:opacity-80 transition-opacity"
      >
        <span className="font-mono font-semibold text-text-primary whitespace-nowrap">
          {item.ticker}
        </span>
        {item.price !== null ? (
          <>
            <span className="font-mono text-text-secondary whitespace-nowrap">
              ${item.price.toFixed(2)}
            </span>
            <span className={`font-mono font-medium whitespace-nowrap ${colorCls}`}>
              {arrow}
              {item.change_pct !== null
                ? `${item.change_pct >= 0 ? "+" : ""}${item.change_pct.toFixed(2)}%`
                : "--"}
            </span>
          </>
        ) : (
          <span className="font-mono text-text-muted whitespace-nowrap">--</span>
        )}
      </button>
    );
  };

  return (
    <div className="relative overflow-hidden flex-1 min-w-0 mx-4">
      {/* Right fade */}
      <div className="absolute right-0 top-0 bottom-0 z-10 w-8 bg-gradient-to-l from-surface to-transparent pointer-events-none" />
      {/* Left fade */}
      <div className="absolute left-0 top-0 bottom-0 z-10 w-8 bg-gradient-to-r from-surface to-transparent pointer-events-none" />

      {/* Scrolling track */}
      <div className="flex items-center h-full overflow-hidden">
        <div className="watchlist-ticker-track flex items-center whitespace-nowrap text-[11px]">
          {items.map((item, i) => renderItem(item, i))}
          {items.map((item, i) => renderItem(item, items.length + i))}
        </div>
      </div>
    </div>
  );
}
