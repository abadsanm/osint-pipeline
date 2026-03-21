"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { MoreHorizontal } from "lucide-react";

const presets = [
  { label: "1D", hours: 24 },
  { label: "5D", hours: 120 },
  { label: "1M", hours: 720 },
  { label: "YTD", hours: 0 },  // special
  { label: "1Y", hours: 8760 },
  { label: "5Y", hours: 43800 },
];

interface TimeframeSelectorProps {
  active?: string;
  onSelect?: (timeframe: string) => void;
}

export default function TimeframeSelector({
  active: controlledActive,
  onSelect,
}: TimeframeSelectorProps) {
  const [active, setActive] = useState(controlledActive || "1D");
  const [sliderLeft, setSliderLeft] = useState(0);    // 0-100
  const [sliderRight, setSliderRight] = useState(100); // 0-100
  const trackRef = useRef<HTMLDivElement>(null);

  // Sync with parent
  useEffect(() => {
    if (controlledActive) setActive(controlledActive);
  }, [controlledActive]);

  // When preset changes, reset slider to full range
  const handlePresetClick = (label: string) => {
    setActive(label);
    setSliderLeft(0);
    setSliderRight(100);
    onSelect?.(label);
  };

  // Slider drag logic
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

  // Compute time labels for slider positions
  const preset = presets.find((p) => p.label === active);
  const totalHours = preset?.hours || 24;
  const fromHoursAgo = Math.round(totalHours * (1 - sliderLeft / 100));
  const toHoursAgo = Math.round(totalHours * (1 - sliderRight / 100));

  const formatTimeLabel = (hoursAgo: number) => {
    if (hoursAgo <= 0) return "Now";
    if (hoursAgo < 24) return `${hoursAgo}h ago`;
    const days = Math.round(hoursAgo / 24);
    if (days < 30) return `${days}d ago`;
    const months = Math.round(days / 30);
    return `${months}mo ago`;
  };

  return (
    <div className="card flex items-center gap-4">
      <div className="flex items-center gap-3 flex-shrink-0">
        <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide whitespace-nowrap">
          Select Timeframe:
        </span>
        <div className="flex items-center gap-0.5">
          {presets.map((p) => (
            <button
              key={p.label}
              onClick={() => handlePresetClick(p.label)}
              className={`px-2.5 py-1 text-xs font-medium rounded transition-colors duration-150 ${
                active === p.label
                  ? "bg-bullish/20 text-bullish"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Range slider */}
      <div className="flex-1 flex items-center gap-3">
        <span className="text-[10px] font-mono text-text-muted w-14 text-right">
          {formatTimeLabel(fromHoursAgo)}
        </span>

        <div ref={trackRef} className="flex-1 relative h-6 flex items-center">
          {/* Track background */}
          <div className="w-full h-1.5 bg-border rounded-full relative">
            {/* Tick marks */}
            {[0, 25, 50, 75, 100].map((pct) => (
              <div
                key={pct}
                className="absolute top-[-2px] w-px h-[10px] bg-neutral/30"
                style={{ left: `${pct}%` }}
              />
            ))}

            {/* Active range */}
            <div
              className="absolute h-1.5 bg-bullish/50 rounded-full"
              style={{ left: `${sliderLeft}%`, right: `${100 - sliderRight}%` }}
            />

            {/* Left handle */}
            <div
              onMouseDown={startDrag("left")}
              className="absolute w-4 h-4 bg-bullish rounded-full -top-[5px] cursor-ew-resize border-2 border-base hover:scale-125 transition-transform z-10"
              style={{ left: `calc(${sliderLeft}% - 8px)` }}
            />

            {/* Right handle */}
            <div
              onMouseDown={startDrag("right")}
              className="absolute w-4 h-4 bg-bullish rounded-full -top-[5px] cursor-ew-resize border-2 border-base hover:scale-125 transition-transform z-10"
              style={{ left: `calc(${sliderRight}% - 8px)` }}
            />
          </div>
        </div>

        <span className="text-[10px] font-mono text-text-muted w-14">
          {formatTimeLabel(toHoursAgo)}
        </span>
      </div>

      <button className="text-text-muted hover:text-text-primary p-0.5 flex-shrink-0">
        <MoreHorizontal size={14} />
      </button>
    </div>
  );
}
