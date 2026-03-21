"use client";

import { useState } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import HeatSphere from "@/components/HeatSphere";
import SignalFeed from "@/components/SignalFeed";
import ChartCards from "@/components/ChartCards";
import TimeframeSelector from "@/components/TimeframeSelector";
import LiveStats from "@/components/LiveStats";
import ResizableCard from "@/components/ResizableCard";
import { useSignals, useSectors, useStats } from "@/hooks/useApiData";
import { RotateCcw } from "lucide-react";

import sectorsData from "../../data/sectors.json";
import signalsData from "../../data/signals.json";
import sectorSentiment from "../../data/sectorSentiment.json";

export default function GlobalPulsePage() {
  const [timeframe, setTimeframe] = useState("1D");
  const [sinceISO, setSinceISO] = useState<string | undefined>(undefined);
  const [resetKey, setResetKey] = useState(0);

  // Use slider ISO if set, otherwise preset label
  const activeFilter = sinceISO || timeframe;
  const stats = useStats();
  const liveSignals = useSignals(20, activeFilter);
  const liveSectors = useSectors(30, activeFilter);

  const displaySignals = liveSignals.length > 0 ? liveSignals : signalsData;
  const displaySectors = liveSectors.length > 0 ? liveSectors : sectorsData;

  return (
    <div className="flex flex-col min-h-screen">
      <Header title="Global Pulse" />
      <SourceTicker />

      <div className="flex-1 p-3 space-y-1 overflow-auto">
        {/* Live stats + reset button */}
        <div className="flex items-center gap-2">
          <div className="flex-1">
            <LiveStats stats={stats} />
          </div>
          <button
            onClick={() => setResetKey((k) => k + 1)}
            title="Reset all panels to default size"
            className="flex items-center gap-1.5 px-2.5 py-1 text-[11px] text-text-muted hover:text-text-primary bg-surface border border-border rounded-md hover:border-neutral transition-colors"
          >
            <RotateCcw size={11} />
            Reset Layout
          </button>
        </div>

        {/* Main: Heat Sphere + Signal Feed */}
        <ResizableCard defaultHeight={480} minHeight={200} maxHeight={800} resetKey={resetKey}>
          <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-3 h-full">
            <HeatSphere data={displaySectors as any} />
            <SignalFeed signals={displaySignals as any} />
          </div>
        </ResizableCard>

        {/* Chart cards — each individually resizable */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.Macro data={sectorSentiment.macroTimeline} />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.HBar title="Top 5 Sector Sentiment" data={sectorSentiment.topSectors} color="#00FFC2" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.HBar title="Emerging Risks" data={sectorSentiment.emergingRisks} color="#FF4B2B" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.Economic data={sectorSentiment.economicSentiments as any} />
          </ResizableCard>
        </div>

        {/* Timeframe selector */}
        <TimeframeSelector
          active={timeframe}
          onSelect={(tf) => { setTimeframe(tf); setSinceISO(undefined); }}
          onRangeChange={setSinceISO}
        />
      </div>
    </div>
  );
}
