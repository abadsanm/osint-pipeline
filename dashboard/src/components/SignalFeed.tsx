"use client";

import { useState } from "react";
import { MoreHorizontal, Send } from "lucide-react";

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

export default function SignalFeed({ signals }: SignalFeedProps) {
  const [note, setNote] = useState("");

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
        {signals.map((signal, i) => (
          <div
            key={`${signal.id}-${i}`}
            className="flex items-center gap-1.5 py-1 px-0.5 hover:bg-surface-alt/50 transition-colors duration-100 cursor-pointer border-b border-border/50 last:border-0"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotColor[signal.type]}`}
            />
            <p className="text-[11px] text-text-primary leading-tight truncate">
              <span className="font-mono font-semibold text-text-primary">{signal.ticker}</span>
              <span className="text-text-muted">: {signal.headline}</span>
            </p>
          </div>
        ))}
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
