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
import { useSignals, useSectors, useStats, usePulseCharts, useApiData } from "@/hooks/useApiData";
import { RotateCcw, TrendingUp, TrendingDown, ArrowRight } from "lucide-react";
import InfoTooltip from "@/components/InfoTooltip";
import Link from "next/link";

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
  const pulseCharts = usePulseCharts(activeFilter);

  const displaySignals = liveSignals.length > 0 ? liveSignals : signalsData;
  const displaySectors = liveSectors.length > 0 ? liveSectors : sectorsData;

  // Use live chart data when available, fall back to mock sectorSentiment
  const chartMacro = pulseCharts.macroTimeline.length > 0 ? pulseCharts.macroTimeline : sectorSentiment.macroTimeline;
  const chartTopSectors = pulseCharts.topSectors.length > 0 ? pulseCharts.topSectors : sectorSentiment.topSectors;
  const chartRisks = pulseCharts.emergingRisks.length > 0 ? pulseCharts.emergingRisks : sectorSentiment.emergingRisks;
  const chartEconomic = pulseCharts.economicSentiments.length > 0 ? pulseCharts.economicSentiments : sectorSentiment.economicSentiments;

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
            <ChartCards.Macro data={chartMacro} />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.HBar title="Top 5 Sector Sentiment" data={chartTopSectors} color="#00FFC2" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.HBar title="Emerging Risks" data={chartRisks} color="#FF4B2B" />
          </ResizableCard>
          <ResizableCard defaultHeight={200} minHeight={120} maxHeight={500} handlePosition="top" resetKey={resetKey}>
            <ChartCards.Economic data={chartEconomic as any} />
          </ResizableCard>
        </div>

        {/* Stocks to Watch */}
        <StocksToWatch />

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

/* ─── Stocks to Watch Component ─── */

interface WatchStock {
  ticker: string;
  dominant_direction: string;
  conviction_score: number;
  avg_confidence: number;
  total_predictions: number;
  evaluated: number;
  correct: number;
  accuracy: number | null;
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  price?: number;
  change_5d?: number;
}

function StocksToWatch() {
  const data = useApiData<{ bullish: WatchStock[]; bearish: WatchStock[] }>(
    "/stocks-to-watch",
    { bullish: [], bearish: [] },
    30000,
  );

  const hasBullish = data.bullish.length > 0;
  const hasBearish = data.bearish.length > 0;

  if (!hasBullish && !hasBearish) return null;

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-1.5">
          <h3 className="text-sm font-semibold text-text-primary">Stocks to Watch</h3>
          <InfoTooltip title="Stocks to Watch">
            <p>Tickers with the strongest directional signals backed by prediction accuracy. Conviction score combines signal consensus, model confidence, and historical accuracy for each ticker. Higher conviction = more sources agree AND the model has been right before.</p>
          </InfoTooltip>
        </div>
        <span className="text-[10px] text-text-muted font-mono">
          {data.bullish.length + data.bearish.length} signals
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Bullish picks */}
        <div>
          <div className="flex items-center gap-1.5 mb-3">
            <TrendingUp size={14} className="text-bullish" />
            <span className="text-xs font-semibold text-bullish uppercase tracking-wider">Bullish</span>
          </div>
          {hasBullish ? (
            <div className="space-y-1.5">
              {data.bullish.slice(0, 5).map((s) => (
                <WatchRow key={s.ticker} stock={s} />
              ))}
            </div>
          ) : (
            <p className="text-xs text-text-muted py-4 text-center">No strong bullish signals</p>
          )}
        </div>

        {/* Bearish picks */}
        <div>
          <div className="flex items-center gap-1.5 mb-3">
            <TrendingDown size={14} className="text-bearish" />
            <span className="text-xs font-semibold text-bearish uppercase tracking-wider">Bearish</span>
          </div>
          {hasBearish ? (
            <div className="space-y-1.5">
              {data.bearish.slice(0, 5).map((s) => (
                <WatchRow key={s.ticker} stock={s} />
              ))}
            </div>
          ) : (
            <p className="text-xs text-text-muted py-4 text-center">No strong bearish signals</p>
          )}
        </div>
      </div>
    </div>
  );
}

function WatchRow({ stock }: { stock: WatchStock }) {
  const isBull = stock.dominant_direction === "bullish";
  const accentColor = isBull ? "text-bullish" : "text-bearish";
  const accentBg = isBull ? "bg-bullish/10" : "bg-bearish/10";

  return (
    <Link
      href={`/alpha/${stock.ticker}`}
      className={`flex items-center gap-3 px-3 py-2.5 rounded-md ${accentBg} border border-transparent hover:border-border transition-colors group`}
    >
      {/* Ticker + direction */}
      <div className="flex items-center gap-2 min-w-[80px]">
        <span className="font-mono font-semibold text-sm text-text-primary">{stock.ticker}</span>
        {isBull ? (
          <TrendingUp size={12} className="text-bullish" />
        ) : (
          <TrendingDown size={12} className="text-bearish" />
        )}
      </div>

      {/* Price + 5d change */}
      <div className="flex-1 min-w-0">
        {stock.price ? (
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-text-secondary">${stock.price}</span>
            {stock.change_5d != null && (
              <span className={`font-mono text-[10px] ${stock.change_5d >= 0 ? "text-bullish" : "text-bearish"}`}>
                {stock.change_5d >= 0 ? "+" : ""}{stock.change_5d}%
              </span>
            )}
          </div>
        ) : (
          <span className="text-[10px] text-text-muted">—</span>
        )}
      </div>

      {/* Accuracy */}
      <div className="text-right min-w-[50px]">
        {stock.accuracy != null ? (
          <span className={`font-mono text-xs ${stock.accuracy >= 0.6 ? "text-bullish" : stock.accuracy >= 0.5 ? "text-text-secondary" : "text-bearish"}`}>
            {(stock.accuracy * 100).toFixed(0)}%
          </span>
        ) : (
          <span className="text-[10px] text-text-muted">new</span>
        )}
      </div>

      {/* Conviction bar */}
      <div className="w-16 h-1.5 bg-surface rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${isBull ? "bg-bullish" : "bg-bearish"}`}
          style={{ width: `${Math.min(100, stock.conviction_score)}%` }}
        />
      </div>

      {/* Arrow */}
      <ArrowRight size={12} className="text-text-muted opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0" />
    </Link>
  );
}
