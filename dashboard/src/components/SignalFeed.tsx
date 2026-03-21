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
    <div className="card flex flex-col h-full">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-text-secondary">
          Signal Feed
        </h2>
        <button className="text-text-muted hover:text-text-primary p-1">
          <MoreHorizontal size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {signals.map((signal, i) => (
          <div key={signal.id}>
            <div className="flex items-start gap-2.5 py-3 px-1 hover:bg-surface-alt/50 transition-colors duration-150 cursor-pointer rounded">
              <span
                className={`w-2 h-2 rounded-full mt-1 flex-shrink-0 ${dotColor[signal.type]}`}
              />
              <div className="min-w-0">
                <p className="text-sm text-text-primary leading-snug">
                  <span className="font-mono font-semibold">{signal.ticker}</span>
                  <span className="text-text-secondary">: {signal.headline}</span>
                </p>
              </div>
            </div>
            {i < signals.length - 1 && (
              <div className="border-b border-border mx-1" />
            )}
          </div>
        ))}
      </div>

      <div className="mt-3 pt-3 border-t border-border relative">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Real Feed Insights..."
          className="w-full bg-surface-alt border border-border rounded-lg px-3 py-2 pr-10 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors"
        />
        <button className="absolute right-3 top-1/2 mt-1.5 -translate-y-1/2 text-text-muted hover:text-bullish transition-colors">
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}
