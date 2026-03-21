"use client";

import { useState, useCallback } from "react";
import Header from "@/components/Header";
import HeatSphere from "@/components/HeatSphere";
import SignalFeed from "@/components/SignalFeed";
import ChartCards from "@/components/ChartCards";
import TimeframeSelector from "@/components/TimeframeSelector";
import LiveStats from "@/components/LiveStats";
import ResizeHandle from "@/components/ResizeHandle";
import { useSignals, useSectors, useStats } from "@/hooks/useApiData";

import sectorsData from "../../data/sectors.json";
import signalsData from "../../data/signals.json";
import sectorSentiment from "../../data/sectorSentiment.json";

export default function GlobalPulsePage() {
  const liveSignals = useSignals();
  const liveSectors = useSectors();
  const stats = useStats();

  const [topHeight, setTopHeight] = useState(58);

  const handleResize = useCallback((deltaY: number) => {
    setTopHeight((prev) => {
      const newPct = prev + (deltaY / window.innerHeight) * 100;
      return Math.max(25, Math.min(75, newPct));
    });
  }, []);

  const displaySignals = liveSignals.length > 0 ? liveSignals : signalsData;
  const displaySectors = liveSectors.length > 0 ? liveSectors : sectorsData;

  const bottomHeight = 100 - topHeight;

  return (
    <div className="flex flex-col h-screen overflow-hidden">
      <Header title="Global Pulse" />

      <div className="flex-1 flex flex-col p-2 min-h-0 gap-0">
        {/* Live stats */}
        <div className="flex-shrink-0 mb-1.5">
          <LiveStats stats={stats} />
        </div>

        {/* Top: Heat Sphere + Signal Feed */}
        <div
          className="flex-shrink-0 grid grid-cols-1 xl:grid-cols-[1fr_280px] gap-2"
          style={{ height: `calc(${topHeight}vh - 100px)` }}
        >
          <HeatSphere data={displaySectors as any} />
          <SignalFeed signals={displaySignals as any} />
        </div>

        {/* Drag handle */}
        <div className="flex-shrink-0">
          <ResizeHandle onResize={handleResize} />
        </div>

        {/* Bottom: Chart cards — fills remaining space */}
        <div
          className="flex-shrink-0"
          style={{ height: `calc(${bottomHeight}vh - 60px)` }}
        >
          <ChartCards
            topSectors={sectorSentiment.topSectors}
            emergingRisks={sectorSentiment.emergingRisks}
            economicSentiments={sectorSentiment.economicSentiments as any}
            macroTimeline={sectorSentiment.macroTimeline}
          />
        </div>

        {/* Timeframe selector */}
        <div className="flex-shrink-0 mt-1">
          <TimeframeSelector />
        </div>
      </div>
    </div>
  );
}
