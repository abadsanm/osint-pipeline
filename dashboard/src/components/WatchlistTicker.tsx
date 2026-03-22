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
      const CACHE_TTL = 60_000; // 60s cache

      const results: WatchlistItem[] = await Promise.all(
        watchlist.map(async (ticker) => {
          const cached = cacheRef.current[ticker];
          if (cached && now - cached.ts < CACHE_TTL) {
            return { ticker, price: cached.price, change_pct: cached.change_pct };
          }

          try {
            const alphaRes = await fetch(`${API_BASE}/alpha/${encodeURIComponent(ticker)}`);
            if (alphaRes.ok) {
              const data = await alphaRes.json();
              // API returns price as a flat number, not nested
              const price = typeof data.price === "number" ? data.price : null;
              const change_pct = typeof data.change_pct === "number" ? data.change_pct : null;
              if (price !== null) {
                cacheRef.current[ticker] = { price, change_pct: change_pct ?? 0, ts: now };
              }
              return { ticker, price, change_pct };
            }
          } catch {
            // API unavailable
          }
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
    const interval = setInterval(fetchWatchlist, 60_000);
    return () => clearInterval(interval);
  }, [fetchWatchlist]);

  if (items.length === 0) return null;

  return (
    <div className="hidden lg:block relative overflow-hidden max-w-[500px] xl:max-w-[700px] mx-2">
      {/* Fade edges */}
      <div className="absolute right-0 top-0 bottom-0 z-10 w-6 bg-gradient-to-l from-surface to-transparent pointer-events-none" />
      <div className="absolute left-0 top-0 bottom-0 z-10 w-6 bg-gradient-to-r from-surface to-transparent pointer-events-none" />

      <div className="flex items-center h-full overflow-hidden">
        <div className="watchlist-ticker-track flex items-center whitespace-nowrap text-[11px]">
          {items.map((item, i) => (
            <TickerItem key={`a-${item.ticker}-${i}`} item={item} onClick={() => router.push(`/alpha/${item.ticker}`)} />
          ))}
          {items.map((item, i) => (
            <TickerItem key={`b-${item.ticker}-${i}`} item={item} onClick={() => router.push(`/alpha/${item.ticker}`)} />
          ))}
        </div>
      </div>
    </div>
  );
}

function TickerItem({ item, onClick }: { item: WatchlistItem; onClick: () => void }) {
  const bullish = (item.change_pct ?? 0) >= 0;
  const colorCls = bullish ? "text-bullish" : "text-bearish";

  return (
    <button
      onClick={onClick}
      className="inline-flex items-center gap-1 mx-3 flex-shrink-0 hover:opacity-80 transition-opacity"
    >
      <span className="font-mono font-semibold text-text-primary">{item.ticker}</span>
      {item.price !== null ? (
        <>
          <span className="font-mono text-text-secondary">${item.price.toFixed(2)}</span>
          <span className={`font-mono font-medium ${colorCls}`}>
            {bullish ? "▲" : "▼"}
            {item.change_pct !== null ? `${item.change_pct >= 0 ? "+" : ""}${item.change_pct.toFixed(2)}%` : ""}
          </span>
        </>
      ) : (
        <span className="font-mono text-text-muted">...</span>
      )}
    </button>
  );
}
