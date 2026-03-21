"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";

const presets = [
  { label: "1D", hours: 24 },
  { label: "5D", hours: 120 },
  { label: "1M", hours: 720 },
  { label: "YTD", hours: 0 },
  { label: "1Y", hours: 8760 },
  { label: "5Y", hours: 43800 },
];

interface TimeframeSelectorProps {
  active?: string;
  onSelect?: (timeframe: string) => void;
}

function generateTimeMarkers(totalHours: number): string[] {
  const now = new Date();

  if (totalHours <= 24) {
    // Every 2 hours: 16:00, 14:00, 12:00, ...
    const markers: string[] = [];
    for (let i = 0; i <= 24; i += 2) {
      const t = new Date(now.getTime() - i * 3600000);
      markers.push(t.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }));
    }
    return markers;
  }
  if (totalHours <= 120) {
    // Every day
    const markers: string[] = [];
    for (let i = 0; i <= 5; i++) {
      const t = new Date(now.getTime() - i * 86400000);
      markers.push(t.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }));
    }
    return markers;
  }
  if (totalHours <= 720) {
    // Every week
    const markers: string[] = [];
    for (let i = 0; i <= 4; i++) {
      const t = new Date(now.getTime() - i * 7 * 86400000);
      markers.push(t.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
    }
    return markers;
  }
  if (totalHours <= 8760) {
    // Every 2 months
    const markers: string[] = [];
    for (let i = 0; i <= 6; i++) {
      const t = new Date(now.getFullYear(), now.getMonth() - i * 2, 1);
      markers.push(t.toLocaleDateString("en-US", { month: "short", year: "2-digit" }));
    }
    return markers;
  }
  // 5Y: every year
  const markers: string[] = [];
  for (let i = 0; i <= 5; i++) {
    markers.push(String(now.getFullYear() - i));
  }
  return markers;
}

export default function TimeframeSelector({
  active: controlledActive,
  onSelect,
}: TimeframeSelectorProps) {
  const [active, setActive] = useState(controlledActive || "1D");
  const [sliderLeft, setSliderLeft] = useState(0);
  const [sliderRight, setSliderRight] = useState(100);
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (controlledActive) setActive(controlledActive);
  }, [controlledActive]);

  const handlePresetClick = (label: string) => {
    setActive(label);
    setSliderLeft(0);
    setSliderRight(100);
    onSelect?.(label);
  };

  const startDrag = useCallback(
    (handle: "left" | "right") => (e: React.MouseEvent) => {
      e.preventDefault();
      const track = trackRef.current;
      if (!track) return;
      const trackRect = track.getBoundingClientRect();

      const onMove = (ev: MouseEvent) => {
        const x = ev.clientX - trackRect.left;
        const pct = Math.max(0, Math.min(100, (x / trackRect.width) * 100));
        if (handle === "left") {
          setSliderLeft(Math.min(pct, sliderRight - 5));
        } else {
          setSliderRight(Math.max(pct, sliderLeft + 5));
        }
      };

      const onUp = () => {
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      document.body.style.cursor = "ew-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [sliderLeft, sliderRight]
  );

  const preset = presets.find((p) => p.label === active);
  const totalHours = preset?.hours || 24;
  const markers = useMemo(() => generateTimeMarkers(totalHours), [totalHours]);

  return (
    <div className="card px-4 py-2">
      {/* Top row: presets + slider */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-[11px] font-semibold text-text-secondary uppercase tracking-wide whitespace-nowrap">
            Select Timeframe:
          </span>
          <div className="flex items-center">
            {presets.map((p, i) => (
              <button
                key={p.label}
                onClick={() => handlePresetClick(p.label)}
                className={`px-2 py-0.5 text-[11px] font-medium transition-colors duration-150 ${
                  active === p.label
                    ? "text-bullish"
                    : "text-text-muted hover:text-text-primary"
                }`}
              >
                {p.label}
                {i < presets.length - 1 && (
                  <span className="text-border ml-1.5">|</span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Slider */}
        <div ref={trackRef} className="flex-1 relative h-5 flex items-center">
          {/* Track bg */}
          <div className="w-full h-1.5 bg-border/50 rounded-full relative">
            {/* Active range */}
            <div
              className="absolute h-1.5 bg-bullish/60 rounded-full"
              style={{ left: `${sliderLeft}%`, right: `${100 - sliderRight}%` }}
            />

            {/* Left handle */}
            <div
              onMouseDown={startDrag("left")}
              className="absolute w-3.5 h-3.5 bg-bullish rounded-sm -top-[4px] cursor-ew-resize border border-bullish/80 hover:scale-110 transition-transform z-10"
              style={{ left: `calc(${sliderLeft}% - 7px)` }}
            />

            {/* Right handle */}
            <div
              onMouseDown={startDrag("right")}
              className="absolute w-3.5 h-3.5 bg-bullish rounded-sm -top-[4px] cursor-ew-resize border border-bullish/80 hover:scale-110 transition-transform z-10"
              style={{ left: `calc(${sliderRight}% - 7px)` }}
            />
          </div>
        </div>
      </div>

      {/* Bottom row: time markers aligned under slider */}
      <div className="flex items-center gap-4 mt-0.5">
        {/* Spacer matching the presets width */}
        <div className="flex-shrink-0" style={{ width: "clamp(180px, 22%, 280px)" }} />

        {/* Time markers */}
        <div className="flex-1 flex justify-between px-1">
          {markers.map((m, i) => (
            <span key={i} className="text-[9px] font-mono text-text-muted/60">
              {m}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
