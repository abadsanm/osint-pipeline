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
  onRangeChange?: (sinceISO: string) => void;
}

function getPresetHours(label: string): number {
  const now = new Date();
  if (label === "YTD") {
    return (now.getTime() - new Date(now.getFullYear(), 0, 1).getTime()) / 3600000;
  }
  return presets.find((p) => p.label === label)?.hours || 24;
}

function generateTimeMarkers(totalHours: number): string[] {
  const now = new Date();

  if (totalHours <= 24) {
    const markers: string[] = [];
    for (let i = 0; i <= 24; i += 4) {
      const t = new Date(now.getTime() - i * 3600000);
      markers.push(t.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }));
    }
    return markers;
  }
  if (totalHours <= 120) {
    const markers: string[] = [];
    for (let i = 0; i <= 5; i++) {
      const t = new Date(now.getTime() - i * 86400000);
      markers.push(t.toLocaleDateString("en-US", { weekday: "short", day: "numeric" }));
    }
    return markers;
  }
  if (totalHours <= 720) {
    const markers: string[] = [];
    for (let i = 0; i <= 4; i++) {
      const t = new Date(now.getTime() - i * 7 * 86400000);
      markers.push(t.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
    }
    return markers;
  }
  if (totalHours <= 8760) {
    const markers: string[] = [];
    for (let i = 0; i <= 6; i++) {
      const t = new Date(now.getFullYear(), now.getMonth() - i * 2, 1);
      markers.push(t.toLocaleDateString("en-US", { month: "short", year: "2-digit" }));
    }
    return markers;
  }
  const markers: string[] = [];
  for (let i = 0; i <= 5; i++) {
    markers.push(String(now.getFullYear() - i));
  }
  return markers;
}

export default function TimeframeSelector({
  active: controlledActive,
  onSelect,
  onRangeChange,
}: TimeframeSelectorProps) {
  const [active, setActive] = useState(controlledActive || "1D");
  const [sliderLeft, setSliderLeft] = useState(0);
  const [sliderRight, setSliderRight] = useState(100);
  const trackRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (controlledActive) setActive(controlledActive);
  }, [controlledActive]);

  // Emit range change with debounce
  const emitRangeChange = useCallback((left: number, right: number, preset: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const totalHours = getPresetHours(preset);
      // Left slider = how far back from "now" the window starts (0% = full range ago, 100% = now)
      // Right slider = where the window ends
      const sinceHoursAgo = totalHours * (1 - left / 100);
      const sinceDate = new Date(Date.now() - sinceHoursAgo * 3600000);
      onRangeChange?.(sinceDate.toISOString());
    }, 300);
  }, [onRangeChange]);

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
      const currentLeft = sliderLeft;
      const currentRight = sliderRight;

      const onMove = (ev: MouseEvent) => {
        const x = ev.clientX - trackRect.left;
        const pct = Math.max(0, Math.min(100, (x / trackRect.width) * 100));
        if (handle === "left") {
          const newLeft = Math.min(pct, currentRight - 5);
          setSliderLeft(newLeft);
          emitRangeChange(newLeft, currentRight, active);
        } else {
          const newRight = Math.max(pct, currentLeft + 5);
          setSliderRight(newRight);
          emitRangeChange(currentLeft, newRight, active);
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
    [sliderLeft, sliderRight, active, emitRangeChange]
  );

  const totalHours = getPresetHours(active);
  const markers = useMemo(() => generateTimeMarkers(totalHours), [totalHours]);

  // Compute displayed range labels
  const fromHoursAgo = totalHours * (1 - sliderLeft / 100);
  const toHoursAgo = totalHours * (1 - sliderRight / 100);
  const formatLabel = (h: number) => {
    if (h < 1) return "Now";
    if (h < 24) return `${Math.round(h)}h ago`;
    if (h < 720) return `${Math.round(h / 24)}d ago`;
    return `${Math.round(h / 720)}mo ago`;
  };

  return (
    <div className="card px-4 py-2">
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

        {/* Slider with range labels */}
        <div className="flex-1 flex items-center gap-2">
          <span className="text-[9px] font-mono text-text-muted w-12 text-right flex-shrink-0">
            {formatLabel(fromHoursAgo)}
          </span>

          <div ref={trackRef} className="flex-1 relative h-5 flex items-center">
            <div className="w-full h-1.5 bg-border/50 rounded-full relative">
              {/* Tick marks */}
              {[0, 20, 40, 60, 80, 100].map((pct) => (
                <div
                  key={pct}
                  className="absolute top-[-3px] w-px h-[12px] bg-neutral/20"
                  style={{ left: `${pct}%` }}
                />
              ))}

              {/* Active range */}
              <div
                className="absolute h-1.5 bg-bullish/60 rounded-full"
                style={{ left: `${sliderLeft}%`, right: `${100 - sliderRight}%` }}
              />

              {/* Left handle */}
              <div
                onMouseDown={startDrag("left")}
                className="absolute w-3 h-4 bg-bullish rounded-sm -top-[5px] cursor-ew-resize border border-bullish/80 hover:scale-110 transition-transform z-10"
                style={{ left: `calc(${sliderLeft}% - 6px)` }}
              />

              {/* Right handle */}
              <div
                onMouseDown={startDrag("right")}
                className="absolute w-3 h-4 bg-bullish rounded-sm -top-[5px] cursor-ew-resize border border-bullish/80 hover:scale-110 transition-transform z-10"
                style={{ left: `calc(${sliderRight}% - 6px)` }}
              />
            </div>
          </div>

          <span className="text-[9px] font-mono text-text-muted w-12 flex-shrink-0">
            {formatLabel(toHoursAgo)}
          </span>
        </div>
      </div>

      {/* Time markers below slider */}
      <div className="flex items-center gap-4 mt-0.5">
        <div className="flex-shrink-0" style={{ width: "clamp(180px, 22%, 280px)" }} />
        <div className="flex-1 flex justify-between px-3 ml-12 mr-12">
          {markers.map((m, i) => (
            <span key={i} className="text-[8px] font-mono text-text-muted/50">
              {m}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
