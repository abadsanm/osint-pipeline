"use client";

import Header from "@/components/Header";
import HeatSphere from "@/components/HeatSphere";
import SignalFeed from "@/components/SignalFeed";
import ChartCards from "@/components/ChartCards";
import TimeframeSelector from "@/components/TimeframeSelector";
import LiveStats from "@/components/LiveStats";
import ResizableCard from "@/components/ResizableCard";
import { useSignals, useSectors, useStats } from "@/hooks/useApiData";

import sectorsData from "../../data/sectors.json";
import signalsData from "../../data/signals.json";
import sectorSentiment from "../../data/sectorSentiment.json";

export default function GlobalPulsePage() {
  const liveSignals = useSignals();
  const liveSectors = useSectors();
  const stats = useStats();

  const displaySignals = liveSignals.length > 0 ? liveSignals : signalsData;
  const displaySectors = liveSectors.length > 0 ? liveSectors : sectorsData;

  return (
    <div className="flex flex-col min-h-screen">
      <Header title="Global Pulse" />

      <div className="flex-1 p-3 space-y-1 overflow-auto">
        {/* Live stats */}
        <LiveStats stats={stats} />

        {/* Main: Heat Sphere + Signal Feed — resizable */}
        <ResizableCard defaultHeight={480} minHeight={200} maxHeight={800}>
          <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-3 h-full">
            <HeatSphere data={displaySectors as any} />
            <SignalFeed signals={displaySignals as any} />
          </div>
        </ResizableCard>

        {/* Chart cards — each individually resizable with top handle */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-2">
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top">
            <ChartCards.Macro data={sectorSentiment.macroTimeline} />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top">
            <ChartCards.HBar title="Top 5 Sector Sentiment" data={sectorSentiment.topSectors} color="#00FFC2" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top">
            <ChartCards.HBar title="Emerging Risks" data={sectorSentiment.emergingRisks} color="#FF4B2B" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top">
            <ChartCards.Economic data={sectorSentiment.economicSentiments as any} />
          </ResizableCard>
        </div>

        {/* Timeframe selector */}
        <TimeframeSelector />
      </div>
    </div>
  );
}
