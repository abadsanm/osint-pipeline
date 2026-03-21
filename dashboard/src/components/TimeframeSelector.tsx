"use client";

import { useState } from "react";

const presets = ["1D", "5D", "1M", "YTD", "1Y", "5Y"];

interface TimeframeSelectorProps {
  onSelect?: (timeframe: string) => void;
}

export default function TimeframeSelector({
  onSelect,
}: TimeframeSelectorProps) {
  const [active, setActive] = useState("1M");

  const handleSelect = (tf: string) => {
    setActive(tf);
    onSelect?.(tf);
  };

  return (
    <div className="card flex items-center justify-between gap-4">
      <div className="flex items-center gap-1">
        {presets.map((p) => (
          <button
            key={p}
            onClick={() => handleSelect(p)}
            className={`px-3 py-1 text-xs font-medium rounded-md transition-colors duration-150 ${
              active === p
                ? "bg-accent-blue text-base"
                : "bg-surface text-text-muted hover:text-text-primary"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {/* Range slider placeholder */}
      <div className="flex-1 max-w-md">
        <div className="h-1 bg-border rounded-full relative">
          <div
            className="absolute h-1 bg-accent-blue rounded-full"
            style={{ left: "20%", right: "20%" }}
          />
          <div
            className="absolute w-3 h-3 bg-accent-blue rounded-full -top-1 cursor-pointer"
            style={{ left: "20%" }}
          />
          <div
            className="absolute w-3 h-3 bg-accent-blue rounded-full -top-1 cursor-pointer"
            style={{ left: "80%" }}
          />
        </div>
      </div>
    </div>
  );
}
