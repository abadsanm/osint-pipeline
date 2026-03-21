"use client";

import { useState } from "react";
import { MoreHorizontal, Send, ChevronRight, ExternalLink } from "lucide-react";
import Link from "next/link";

interface Signal {
  id: string;
  type: "bullish" | "bearish" | "volume";
  ticker: string;
  headline: string;
  source: string;
  timestamp: string;
  confidence: number;
}

interface SignalFeedProps {
  signals: Signal[];
}

const dotColor = {
  bullish: "bg-bullish",
  bearish: "bg-bearish",
  volume: "bg-volume-spike",
};

const typeLabel: Record<string, { text: string; cls: string }> = {
  bullish: { text: "Bullish", cls: "text-bullish" },
  bearish: { text: "Bearish", cls: "text-bearish" },
  volume: { text: "Volume", cls: "text-volume-spike" },
};

function formatTimestamp(ts: string): string {
  try {
    const date = new Date(ts);
    return date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return ts;
  }
}

export default function SignalFeed({ signals }: SignalFeedProps) {
  const [note, setNote] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggleExpand = (id: string, index: number) => {
    const key = `${id}-${index}`;
    setExpandedId((prev) => (prev === key ? null : key));
  };

  return (
    <div className="card flex flex-col h-full min-h-0 overflow-hidden">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Signal Feed
        </h2>
        <button className="text-text-muted hover:text-text-primary p-0.5">
          <MoreHorizontal size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {signals.map((signal, i) => {
          const key = `${signal.id}-${i}`;
          const isExpanded = expandedId === key;
          const tl = typeLabel[signal.type] || typeLabel.volume;
          const ticker = signal.ticker.replace(/[^a-zA-Z0-9]/g, "");

          return (
            <div
              key={key}
              className="border-b border-border/50 last:border-0"
            >
              {/* Collapsed row */}
              <button
                onClick={() => toggleExpand(signal.id, i)}
                className="w-full flex items-center gap-1.5 py-1 px-0.5 hover:bg-surface-alt/50 transition-colors duration-100 text-left"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor[signal.type]}`}
                />
                <p className="text-[11px] text-text-primary leading-tight truncate flex-1">
                  <span className="font-mono font-semibold text-text-primary">{signal.ticker}</span>
                  <span className="text-text-muted">: {signal.headline}</span>
                </p>
                <ChevronRight
                  size={10}
                  className={`flex-shrink-0 text-text-muted transition-transform duration-150 ${
                    isExpanded ? "rotate-90" : ""
                  }`}
                />
              </button>

              {/* Expanded detail */}
              {isExpanded && (
                <div className="px-3 pb-2 pt-0.5 bg-surface-alt/30 border-l-2 border-bullish/20 ml-1">
                  <p className="text-[11px] text-text-primary leading-relaxed mb-1.5">
                    {signal.headline}
                  </p>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
                    <span className={`font-semibold ${tl.cls}`}>
                      {tl.text}
                    </span>
                    <span className="text-text-muted">
                      Confidence: <span className="font-mono text-text-primary">{(signal.confidence * 100).toFixed(0)}%</span>
                    </span>
                    <span className="text-text-muted">
                      {formatTimestamp(signal.timestamp)}
                    </span>
                    {signal.source && (
                      <span className="text-text-muted">
                        via {signal.source}
                      </span>
                    )}
                  </div>
                  {ticker && (
                    <Link
                      href={`/alpha/${ticker}`}
                      className="inline-flex items-center gap-1 mt-1.5 text-[10px] text-accent-blue hover:underline"
                    >
                      View Alpha <ExternalLink size={9} />
                    </Link>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-1 pt-1 border-t border-border relative">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Real Feed Insights..."
          className="w-full bg-surface-alt border border-border rounded px-2 py-1 pr-8 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors"
        />
        <button className="absolute right-2 top-1/2 mt-0.5 -translate-y-1/2 text-text-muted hover:text-bullish transition-colors">
          <Send size={11} />
        </button>
      </div>
    </div>
  );
}
