import Header from "@/components/Header";
import HeatSphere from "@/components/HeatSphere";
import SignalFeed from "@/components/SignalFeed";
import ChartCards from "@/components/ChartCards";
import TimeframeSelector from "@/components/TimeframeSelector";

import sectorsData from "../../data/sectors.json";
import signalsData from "../../data/signals.json";
import sectorSentiment from "../../data/sectorSentiment.json";

export default function GlobalPulsePage() {
  return (
    <div className="flex flex-col h-screen">
      <Header title="Global Pulse" />

      <div className="flex-1 p-module-gap-lg overflow-auto">
        {/* Main grid: Heat Sphere + Signal Feed */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-module-gap-lg mb-module-gap-lg">
          <HeatSphere data={sectorsData} />
          <SignalFeed signals={signalsData as any} />
        </div>

        {/* Bottom chart cards */}
        <div className="mb-module-gap-lg">
          <ChartCards
            topSectors={sectorSentiment.topSectors}
            emergingRisks={sectorSentiment.emergingRisks}
            economicSentiments={sectorSentiment.economicSentiments as any}
            macroTimeline={sectorSentiment.macroTimeline}
          />
        </div>

        {/* Timeframe selector */}
        <TimeframeSelector />
      </div>
    </div>
  );
}
