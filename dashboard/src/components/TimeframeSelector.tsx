"use client";

import { useState } from "react";
import { MoreHorizontal } from "lucide-react";

const presets = ["1D", "5D", "1M", "YTD", "1Y", "5Y"];

interface TimeframeSelectorProps {
  onSelect?: (timeframe: string) => void;
}

export default function TimeframeSelector({
  onSelect,
}: TimeframeSelectorProps) {
  const [active, setActive] = useState("1D");

  const handleSelect = (tf: string) => {
    setActive(tf);
    onSelect?.(tf);
  };

  return (
    <div className="card flex items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide whitespace-nowrap">
          Select Timeframe:
        </span>
        <div className="flex items-center gap-0.5">
          {presets.map((p) => (
            <button
              key={p}
              onClick={() => handleSelect(p)}
              className={`px-2.5 py-1 text-xs font-medium rounded transition-colors duration-150 ${
                active === p
                  ? "bg-bullish/20 text-bullish"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-3 flex-1 max-w-lg">
        {/* Range slider */}
        <div className="flex-1 relative h-6 flex items-center">
          <div className="w-full h-1 bg-border rounded-full relative">
            <div
              className="absolute h-1 bg-bullish/60 rounded-full"
              style={{ left: "10%", right: "60%" }}
            />
            <div
              className="absolute w-3 h-3 bg-bullish rounded-full -top-1 border-2 border-base cursor-pointer hover:scale-110 transition-transform"
              style={{ left: "10%" }}
            />
            <div
              className="absolute w-3 h-3 bg-bullish rounded-full -top-1 border-2 border-base cursor-pointer hover:scale-110 transition-transform"
              style={{ left: "40%" }}
            />
          </div>
          <span className="ml-3 text-xs font-mono text-text-muted whitespace-nowrap">1D</span>
        </div>
        <button className="text-text-muted hover:text-text-primary p-0.5">
          <MoreHorizontal size={14} />
        </button>
      </div>
    </div>
  );
}
