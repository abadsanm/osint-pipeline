"use client";

import { useState } from "react";

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
    <div className="card-lg flex flex-col h-full">
      <h2 className="text-sm font-semibold mb-3 text-text-secondary">
        Signal Feed
      </h2>

      <div className="flex-1 overflow-y-auto space-y-2 min-h-0">
        {signals.map((signal) => (
          <div
            key={signal.id}
            className="flex items-start gap-2.5 p-2.5 rounded-md hover:bg-surface-alt transition-colors duration-150 cursor-pointer"
          >
            <span
              className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${dotColor[signal.type]}`}
            />
            <div className="min-w-0">
              <p className="text-sm text-text-primary leading-snug">
                <span className="font-mono font-medium">{signal.ticker}</span>
                <span className="text-text-secondary">: {signal.headline}</span>
              </p>
              <p className="text-xs text-text-muted mt-0.5">
                {signal.source} &middot;{" "}
                {new Date(signal.timestamp).toLocaleTimeString("en-US", {
                  hour: "2-digit",
                  minute: "2-digit",
                  hour12: false,
                })}
              </p>
            </div>
          </div>
        ))}
      </div>

      <div className="mt-3 pt-3 border-t border-border">
        <input
          type="text"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Real Feed Insights..."
          className="w-full bg-surface-alt border border-border rounded-md px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors"
        />
      </div>
    </div>
  );
}
