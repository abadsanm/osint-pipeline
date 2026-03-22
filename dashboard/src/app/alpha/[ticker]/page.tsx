"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import { useState, useEffect, useCallback, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import AnalysisModal from "@/components/AnalysisModal";
import InfoTooltip from "@/components/InfoTooltip";
import { Brain, ArrowLeft, ExternalLink, AlertTriangle, Loader2, Cpu, RefreshCw, CheckCircle, XCircle, Info } from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  BarChart,
  Cell,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AlphaData {
  ticker: string;
  company: string;
  price: number;
  change_pct: number;
  change_amt: number;
  range_52w: { low: number; high: number } | null;
  candles: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  technicals: {
    rsi: number | null;
    macd: { value: number; signal: number; histogram: number } | null;
    bb: { pctb: number; upper: number; lower: number; middle: number } | null;
    sma20: number | null;
    sma50: number | null;
    ema12: number | null;
    ema26: number | null;
    atr: number | null;
    obv: number | null;
    trend: string;
  };
  technicals_series: Array<Record<string, any>>;
  sentiment: {
    score: number;
    label: string;
    volume: number;
    sources: Record<string, number>;
    keywords: string[];
    sample_docs: Array<{
      title: string;
      source: string;
      url: string | null;
      created_at: string;
      content: string;
    }>;
  };
  signal: {
    confidence: number;
    type: string;
    headline: string;
    sources: string[];
  } | null;
  score: {
    overall: number;
    direction: string;
    sentiment_weight: number;
    svc_weight: number;
    technical_weight: number;
    microstructure_weight: number;
    order_flow_weight: number;
    correlation_weight: number;
    components_available: string[];
  };
  ml_prediction?: {
    direction_1d?: string;
    direction_5d?: string;
    probability_up_1d?: number;
    probability_down_1d?: number;
    probability_flat_1d?: number;
    probability_up_5d?: number;
    probability_down_5d?: number;
    probability_flat_5d?: number;
    predicted_return_1d?: number;
    confidence?: number;
    ml_score?: number;
    blended_score?: number;
    snapshot_time?: string;
    features_used?: number;
    training_stats?: {
      trained_at?: string;
      samples?: number;
      accuracy_1d?: number;
      accuracy_5d?: number;
      val_accuracy_1d?: number;
      val_accuracy_5d?: number;
    };
  } | null;
  feature_importance?: Array<{
    feature: string;
    importance: number;
  }>;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API_BASE = "http://localhost:8000/api";

const SCORE_COMPONENTS: {
  key: string;
  weightKey: keyof AlphaData["score"];
  label: string;
  color: string;
  bg: string;
}[] = [
  { key: "sentiment", weightKey: "sentiment_weight", label: "Sentiment", color: "#58A6FF", bg: "bg-accent-blue" },
  { key: "svc", weightKey: "svc_weight", label: "SVC", color: "#A78BFA", bg: "bg-[#A78BFA]" },
  { key: "technical", weightKey: "technical_weight", label: "Technicals", color: "#22D3EE", bg: "bg-[#22D3EE]" },
  { key: "microstructure", weightKey: "microstructure_weight", label: "Microstructure", color: "#FBBF24", bg: "bg-[#FBBF24]" },
  { key: "order_flow", weightKey: "order_flow_weight", label: "Order Flow", color: "#00FFC2", bg: "bg-bullish" },
  { key: "correlation", weightKey: "correlation_weight", label: "Correlation", color: "#F472B6", bg: "bg-[#F472B6]" },
];

const SOURCE_COLORS: Record<string, string> = {
  hacker_news: "bg-[#FF6600]",
  github: "bg-[#8B5CF6]",
  reddit: "bg-[#FF4500]",
  sec_edgar: "bg-bullish",
  news: "bg-[#FBBF24]",
  trustpilot: "bg-[#00B67A]",
  google_trends: "bg-[#4285F4]",
};

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

function useAlphaData(ticker: string) {
  const [data, setData] = useState<AlphaData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/alpha/${encodeURIComponent(ticker)}`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setError(false);
      } else {
        setError(true);
      }
    } catch {
      setError(true);
    }
    setLoading(false);
  }, [ticker]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  return { data, loading, error };
}

// ---------------------------------------------------------------------------
// RSI Gauge (SVG semicircle)
// ---------------------------------------------------------------------------

function RSIGauge({ value }: { value: number | null }) {
  const radius = 60;
  const stroke = 8;
  const cx = 70;
  const cy = 70;
  const startAngle = Math.PI;
  const endAngle = 0;

  const clampedValue = value !== null ? Math.max(0, Math.min(100, value)) : 50;
  const ratio = clampedValue / 100;

  // Arc path helper
  const describeArc = (startA: number, endA: number) => {
    const x1 = cx + radius * Math.cos(startA);
    const y1 = cy - radius * Math.sin(startA);
    const x2 = cx + radius * Math.cos(endA);
    const y2 = cy - radius * Math.sin(endA);
    const sweep = startA > endA ? 1 : 0;
    return `M ${x1} ${y1} A ${radius} ${radius} 0 0 ${sweep} ${x2} ${y2}`;
  };

  // The filled arc goes from PI (left) towards 0 (right) by ratio
  const filledEndAngle = Math.PI - ratio * Math.PI;

  let color = "#8B949E"; // neutral
  let label = "Neutral";
  if (value !== null) {
    if (value < 30) {
      color = "#00FFC2";
      label = "Oversold";
    } else if (value > 70) {
      color = "#FF4B2B";
      label = "Overbought";
    }
  }

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding flex flex-col items-center">
      <div className="flex items-center justify-between mb-3 self-stretch">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">RSI (14)</h4>
        <InfoTooltip title="Relative Strength Index (RSI)">
          <p>Measures momentum on a 0-100 scale over the last 14 trading days.</p>
          <p>Below 30 = oversold (price may bounce up — potential buying opportunity). Above 70 = overbought (price may pull back — consider taking profits).</p>
          <p>40-60 = neutral, no strong momentum signal.</p>
          <p>Most reliable when combined with other indicators — RSI alone can stay overbought in strong trends.</p>
        </InfoTooltip>
      </div>
      <svg width={140} height={80} viewBox="0 0 140 80">
        {/* Background arc */}
        <path
          d={describeArc(startAngle, endAngle)}
          fill="none"
          stroke="#21262D"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
        {/* Filled arc */}
        {value !== null && (
          <path
            d={describeArc(startAngle, filledEndAngle)}
            fill="none"
            stroke={color}
            strokeWidth={stroke}
            strokeLinecap="round"
          />
        )}
        {/* Value text */}
        <text
          x={cx}
          y={cy - 8}
          textAnchor="middle"
          fill={value !== null ? color : "#7D8590"}
          fontSize="22"
          fontFamily="Roboto Mono, monospace"
          fontWeight="600"
        >
          {value != null ? value.toFixed(1) : "--"}
        </text>
      </svg>
      {/* Zone labels */}
      <div className="flex justify-between w-full text-[10px] text-text-muted -mt-1 px-1">
        <span>0</span>
        <span style={{ color }} className="font-semibold text-xs">
          {value !== null ? label : "Awaiting data"}
        </span>
        <span>100</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MACD Card
// ---------------------------------------------------------------------------

function MACDCard({
  macd,
  series,
}: {
  macd: AlphaData["technicals"]["macd"];
  series: AlphaData["technicals_series"];
}) {
  // Get last 20 histogram values from series
  const histogramData = useMemo(() => {
    if (!series || series.length === 0) return [];
    return series.slice(-20).map((s, i) => ({
      idx: i,
      value: s.macd_histogram ?? s.MACD_histogram ?? s.histogram ?? 0,
    }));
  }, [series]);

  // Determine crossover label
  let crossoverLabel = "Awaiting data";
  let crossoverColor = "text-text-muted";
  if (macd) {
    if (histogramData.length >= 2) {
      const last = histogramData[histogramData.length - 1]?.value ?? 0;
      const prev = histogramData[histogramData.length - 2]?.value ?? 0;
      if (last > 0 && prev <= 0) {
        crossoverLabel = "Bullish Crossover";
        crossoverColor = "text-bullish";
      } else if (last < 0 && prev >= 0) {
        crossoverLabel = "Bearish Crossover";
        crossoverColor = "text-bearish";
      } else if (Math.abs(last) < Math.abs(prev)) {
        crossoverLabel = "Converging";
        crossoverColor = "text-[#FBBF24]";
      } else if (last > 0) {
        crossoverLabel = "Bullish Momentum";
        crossoverColor = "text-bullish";
      } else {
        crossoverLabel = "Bearish Momentum";
        crossoverColor = "text-bearish";
      }
    }
  }

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">MACD</h4>
        <InfoTooltip title="MACD (Moving Average Convergence Divergence)">
          <p>Tracks the relationship between two moving averages (12-day and 26-day EMA).</p>
          <p>Green histogram bars = bullish momentum, red = bearish. A bullish crossover (MACD crossing above signal) often precedes upward moves.</p>
          <p>Histogram shrinking toward zero signals momentum is weakening.</p>
          <p>Works best for confirming trend direction, not for predicting reversals.</p>
        </InfoTooltip>
      </div>

      {!macd ? (
        <p className="text-sm text-text-muted text-center py-6">Awaiting data</p>
      ) : (
        <>
          {/* Sparkline histogram */}
          {histogramData.length > 0 && (
            <div className="h-16 mb-3">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={histogramData} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
                  <Bar dataKey="value" radius={[1, 1, 0, 0]}>
                    {histogramData.map((entry, i) => (
                      <Cell
                        key={i}
                        fill={entry.value >= 0 ? "#00FFC2" : "#FF4B2B"}
                        fillOpacity={0.8}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Values */}
          <div className="grid grid-cols-3 gap-2 text-center mb-2">
            <div>
              <p className="text-[10px] text-text-muted">MACD</p>
              <p className="font-mono text-xs text-text-primary">{macd.value?.toFixed(3) ?? "--"}</p>
            </div>
            <div>
              <p className="text-[10px] text-text-muted">Signal</p>
              <p className="font-mono text-xs text-text-primary">{macd.signal?.toFixed(3) ?? "--"}</p>
            </div>
            <div>
              <p className="text-[10px] text-text-muted">Hist</p>
              <p className={`font-mono text-xs ${macd.histogram >= 0 ? "text-bullish" : "text-bearish"}`}>
                {macd.histogram?.toFixed(3) ?? "--"}
              </p>
            </div>
          </div>

          <p className={`text-xs font-semibold text-center ${crossoverColor}`}>{crossoverLabel}</p>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Bollinger Band %B Card
// ---------------------------------------------------------------------------

function BollingerCard({ bb }: { bb: AlphaData["technicals"]["bb"] }) {
  if (!bb || bb.pctb === null || bb.pctb === undefined) {
    return (
      <div className="bg-surface border border-border rounded-card p-card-padding">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Bollinger %B</h4>
          <InfoTooltip title="Bollinger Bands %B">
            <p>Shows where the current price sits within its volatility range.</p>
            <p>0% = at the lower band (2 std devs below average). 100% = at the upper band.</p>
            <p>Below 20% often signals oversold; above 80% signals overbought.</p>
          </InfoTooltip>
        </div>
        <p className="text-sm text-text-muted text-center py-6">Awaiting data</p>
      </div>
    );
  }

  const pctb = Math.max(0, Math.min(1, bb.pctb ?? 0.5));
  const pctbDisplay = (pctb * 100).toFixed(1);

  // Color zones
  let dotColor = "#8B949E";
  let zoneLabel = "Neutral Zone";
  if (pctb < 0.2) {
    dotColor = "#FF4B2B";
    zoneLabel = "Near Lower Band";
  } else if (pctb > 0.8) {
    dotColor = "#FF4B2B";
    zoneLabel = "Near Upper Band";
  } else {
    dotColor = "#00FFC2";
    zoneLabel = "Mid Band";
  }

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Bollinger %B</h4>
        <InfoTooltip title="Bollinger Bands %B">
          <p>Shows where the current price sits within its volatility range.</p>
          <p>0% = at the lower band (2 standard deviations below average). 100% = at the upper band (2 standard deviations above).</p>
          <p>Below 20% often signals oversold conditions; above 80% signals overbought.</p>
          <p>Bands widen during volatility, narrow during consolidation (a &ldquo;squeeze&rdquo; often precedes a big move).</p>
          <p>Upper/Middle/Lower values show the actual price levels of the bands.</p>
        </InfoTooltip>
      </div>

      {/* Horizontal position bar */}
      <div className="relative h-6 rounded-full overflow-hidden bg-surface-alt mb-2 mx-1">
        {/* Danger zones */}
        <div className="absolute left-0 top-0 bottom-0 w-[20%] bg-bearish/10" />
        <div className="absolute right-0 top-0 bottom-0 w-[20%] bg-bearish/10" />
        {/* Neutral center */}
        <div className="absolute left-[20%] top-0 bottom-0 right-[20%] bg-bullish/5" />
        {/* Position marker */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full border-2 border-white/80 shadow-md transition-all duration-500"
          style={{
            left: `calc(${pctb * 100}% - 6px)`,
            backgroundColor: dotColor,
          }}
        />
      </div>

      {/* Scale labels */}
      <div className="flex justify-between text-[10px] text-text-muted mx-1 mb-3">
        <span>0%</span>
        <span>50%</span>
        <span>100%</span>
      </div>

      {/* Current %B value */}
      <p className="text-center font-mono text-lg font-semibold text-text-primary mb-1">
        {pctbDisplay}%
      </p>
      <p className="text-center text-xs text-text-muted mb-3" style={{ color: dotColor }}>
        {zoneLabel}
      </p>

      {/* Band values */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div>
          <p className="text-[10px] text-text-muted">Upper</p>
          <p className="font-mono text-xs text-text-primary">${bb.upper?.toFixed(2) ?? "--"}</p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted">Middle</p>
          <p className="font-mono text-xs text-text-primary">${bb.middle?.toFixed(2) ?? "--"}</p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted">Lower</p>
          <p className="font-mono text-xs text-text-primary">${bb.lower?.toFixed(2) ?? "--"}</p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Signal Scorecard
// ---------------------------------------------------------------------------

function SignalScorecard({ score }: { score: AlphaData["score"] }) {
  const totalWeight =
    score.sentiment_weight +
    score.svc_weight +
    score.technical_weight +
    score.microstructure_weight +
    score.order_flow_weight +
    score.correlation_weight;

  const dirColor =
    score.direction === "bullish"
      ? "text-bullish bg-bullish/10 border-bullish/20"
      : score.direction === "bearish"
        ? "text-bearish bg-bearish/10 border-bearish/20"
        : "text-neutral bg-neutral/10 border-neutral/20";

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Alpha Score</h3>
        <InfoTooltip title="Alpha Signal Scorecard">
          <p>Combines 6 independent factors into a single directional score (-1 to +1).</p>
          <p>Sentiment (25%): FinBERT NLP analysis of pipeline content. SVC (15%): Sentiment Volume Convergence — sentiment shift x volume change. Technicals (20%): RSI, MACD, Bollinger Bands, moving averages combined.</p>
          <p>Microstructure (15%): VWAP position, volume profile, fair value gaps. Order Flow (15%): Buy/sell imbalance from trade data. Correlation (10%): Multi-source agreement strength.</p>
          <p>When factors with no data are absent, their weight redistributes proportionally. Higher confidence = more factors agree on the direction.</p>
        </InfoTooltip>
      </div>

      <div className="flex items-start gap-6 mb-4">
        {/* Overall score */}
        <div className="flex flex-col items-center flex-shrink-0">
          <p className="font-mono text-4xl font-bold text-text-primary leading-none">
            {score.overall?.toFixed(1) ?? "--"}
          </p>
          <span
            className={`mt-2 px-2.5 py-0.5 rounded-md text-xs font-semibold border capitalize ${dirColor}`}
          >
            {score.direction}
          </span>
        </div>

        {/* Segmented weight bar */}
        <div className="flex-1 min-w-0">
          <div className="flex h-5 rounded-full overflow-hidden">
            {SCORE_COMPONENTS.map((comp) => {
              const weight = (score[comp.weightKey] as number) || 0;
              const pct = totalWeight > 0 ? (weight / totalWeight) * 100 : 0;
              if (pct === 0) return null;
              const available = score.components_available.includes(comp.key);
              return (
                <div
                  key={comp.key}
                  className="relative group"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: available ? comp.color : "#21262D",
                    opacity: available ? 1 : 0.35,
                  }}
                >
                  {/* Tooltip on hover */}
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block z-10">
                    <div className="bg-surface-alt border border-border rounded px-2 py-1 text-[10px] text-text-primary whitespace-nowrap">
                      {comp.label}: {(weight * 100).toFixed(0)}%
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3">
            {SCORE_COMPONENTS.map((comp) => {
              const weight = (score[comp.weightKey] as number) || 0;
              const available = score.components_available.includes(comp.key);
              return (
                <div key={comp.key} className="flex items-center gap-1.5">
                  <span
                    className="w-2 h-2 rounded-full flex-shrink-0"
                    style={{
                      backgroundColor: available ? comp.color : "#21262D",
                      opacity: available ? 1 : 0.4,
                    }}
                  />
                  <span
                    className={`text-[11px] ${available ? "text-text-secondary" : "text-text-muted line-through"}`}
                  >
                    {comp.label}
                  </span>
                  <span
                    className={`font-mono text-[11px] ${available ? "text-text-primary" : "text-text-muted"}`}
                  >
                    {available ? `${(weight * 100).toFixed(0)}%` : "N/A"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Volume Profile (horizontal histogram + mini price chart)
// ---------------------------------------------------------------------------

function VolumeProfile({
  candles,
}: {
  candles: AlphaData["candles"];
}) {
  const BUCKET_COUNT = 20;

  const profileData = useMemo(() => {
    if (!candles || candles.length === 0) return null;

    const prices = candles.map((c) => c.close);
    const priceMin = Math.min(...prices);
    const priceMax = Math.max(...prices);
    const range = priceMax - priceMin || 1;
    const bucketSize = range / BUCKET_COUNT;

    // Build volume buckets keyed by typical price
    const buckets: { low: number; high: number; mid: number; volume: number }[] = [];
    for (let i = 0; i < BUCKET_COUNT; i++) {
      const low = priceMin + i * bucketSize;
      const high = low + bucketSize;
      buckets.push({ low, high, mid: (low + high) / 2, volume: 0 });
    }

    candles.forEach((c) => {
      const typicalPrice = (c.high + c.low + c.close) / 3;
      const idx = Math.min(
        Math.floor((typicalPrice - priceMin) / bucketSize),
        BUCKET_COUNT - 1,
      );
      if (idx >= 0) buckets[idx].volume += c.volume;
    });

    // POC = bucket with highest volume
    const maxVol = Math.max(...buckets.map((b) => b.volume), 1);
    const pocIdx = buckets.findIndex((b) => b.volume === maxVol);
    const poc = buckets[pocIdx];

    // Value Area: 70% of total volume centered on POC
    const totalVolume = buckets.reduce((s, b) => s + b.volume, 0);
    const vaTarget = totalVolume * 0.7;
    let vaVolume = poc.volume;
    let vaLowIdx = pocIdx;
    let vaHighIdx = pocIdx;

    while (vaVolume < vaTarget && (vaLowIdx > 0 || vaHighIdx < BUCKET_COUNT - 1)) {
      const below = vaLowIdx > 0 ? buckets[vaLowIdx - 1].volume : -1;
      const above = vaHighIdx < BUCKET_COUNT - 1 ? buckets[vaHighIdx + 1].volume : -1;
      if (below >= above && below >= 0) {
        vaLowIdx--;
        vaVolume += buckets[vaLowIdx].volume;
      } else if (above >= 0) {
        vaHighIdx++;
        vaVolume += buckets[vaHighIdx].volume;
      } else {
        break;
      }
    }

    return {
      buckets,
      maxVol,
      pocIdx,
      poc,
      vaLowIdx,
      vaHighIdx,
      vaLow: buckets[vaLowIdx].low,
      vaHigh: buckets[vaHighIdx].high,
      priceMin,
      priceMax,
      prices,
    };
  }, [candles]);

  if (!profileData) {
    return (
      <div className="bg-surface border border-border rounded-card p-card-padding-lg flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Volume Profile</h3>
          <InfoTooltip title="Volume Profile (Price x Volume)">
            <p>Horizontal histogram showing how much volume traded at each price level.</p>
            <p>POC (Point of Control): The price where most volume traded — acts as a magnet.</p>
            <p>Value Area (blue bars): Price range containing 70% of all volume — where &ldquo;fair value&rdquo; lives.</p>
          </InfoTooltip>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-text-muted">Awaiting data</p>
        </div>
      </div>
    );
  }

  const { buckets, maxVol, pocIdx, poc, vaLowIdx, vaHighIdx, vaLow, vaHigh, priceMin, priceMax, prices } = profileData;

  // SVG dimensions
  const svgW = 520;
  const svgH = 320;
  const chartW = svgW * 0.58;
  const histW = svgW * 0.38;
  const histOffset = chartW + svgW * 0.04;
  const topPad = 10;
  const bottomPad = 20;
  const plotH = svgH - topPad - bottomPad;

  // Price → Y mapping (high at top)
  const priceRange = priceMax - priceMin || 1;
  const priceToY = (p: number) => topPad + (1 - (p - priceMin) / priceRange) * plotH;

  // Mini price chart polyline
  const pricePoints = prices
    .map((p, i) => {
      const x = (i / (prices.length - 1)) * (chartW - 20) + 10;
      const y = priceToY(p);
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Volume Profile</h3>
        <InfoTooltip title="Volume Profile (Price x Volume)">
          <p>Horizontal histogram showing how much volume traded at each price level.</p>
          <p>POC (Point of Control): The price where most volume traded — acts as a magnet. Value Area (blue bars): Price range containing 70% of all volume — where &ldquo;fair value&rdquo; lives.</p>
          <p>If current price is above the value area, the market may be overextended. If price approaches POC from above or below, expect support/resistance at that level.</p>
          <p>Thin volume areas (gaps in the profile) often see fast price moves through them.</p>
        </InfoTooltip>
      </div>

      <svg
        viewBox={`0 0 ${svgW} ${svgH}`}
        className="w-full"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Mini price chart */}
        <polyline
          points={pricePoints}
          fill="none"
          stroke="#E6EDF3"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />

        {/* POC horizontal reference line across chart area */}
        <line
          x1={0}
          y1={priceToY(poc.mid)}
          x2={chartW}
          y2={priceToY(poc.mid)}
          stroke="#58A6FF"
          strokeWidth="1"
          strokeDasharray="4 3"
          opacity={0.5}
        />

        {/* Horizontal volume histogram */}
        {buckets.map((b, i) => {
          const barH = plotH / BUCKET_COUNT - 1;
          const y = priceToY(b.high) + 0.5;
          const barW = (b.volume / maxVol) * histW;
          const inVA = i >= vaLowIdx && i <= vaHighIdx;
          const isPOC = i === pocIdx;

          return (
            <rect
              key={i}
              x={histOffset}
              y={y}
              width={barW}
              height={Math.max(barH, 1)}
              rx={1}
              fill={isPOC ? "#58A6FF" : "#3B82F6"}
              opacity={inVA ? 0.85 : 0.4}
            />
          );
        })}

        {/* Value Area bracket labels on histogram side */}
        <text
          x={histOffset + histW + 4}
          y={priceToY(vaHigh)}
          fill="#8B949E"
          fontSize="9"
          fontFamily="Roboto Mono, monospace"
          dominantBaseline="middle"
        >
          VAH
        </text>
        <text
          x={histOffset + histW + 4}
          y={priceToY(vaLow)}
          fill="#8B949E"
          fontSize="9"
          fontFamily="Roboto Mono, monospace"
          dominantBaseline="middle"
        >
          VAL
        </text>
        <text
          x={histOffset + histW + 4}
          y={priceToY(poc.mid)}
          fill="#58A6FF"
          fontSize="9"
          fontFamily="Roboto Mono, monospace"
          fontWeight="600"
          dominantBaseline="middle"
        >
          POC
        </text>

        {/* Y-axis price labels (shared) */}
        {[priceMin, priceMin + priceRange * 0.25, priceMin + priceRange * 0.5, priceMin + priceRange * 0.75, priceMax].map(
          (p, i) => (
            <text
              key={i}
              x={chartW + 2}
              y={priceToY(p)}
              fill="#7D8590"
              fontSize="9"
              fontFamily="Roboto Mono, monospace"
              dominantBaseline="middle"
            >
              ${p.toFixed(0)}
            </text>
          ),
        )}
      </svg>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3 mt-3 text-center">
        <div>
          <p className="text-[10px] text-text-muted uppercase">POC</p>
          <p className="font-mono text-xs text-accent-blue font-semibold">
            ${poc.mid.toFixed(2)}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted uppercase">VA High</p>
          <p className="font-mono text-xs text-text-primary">${vaHigh.toFixed(2)}</p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted uppercase">VA Low</p>
          <p className="font-mono text-xs text-text-primary">${vaLow.toFixed(2)}</p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Volume & OBV Analysis (stacked bar + line)
// ---------------------------------------------------------------------------

function VolumeAnalysis({
  technicals_series,
}: {
  technicals_series: AlphaData["technicals_series"];
}) {
  const { chartData, avgVolume, volumeTrend, obvTrend } = useMemo(() => {
    if (!technicals_series || technicals_series.length === 0) {
      return { chartData: [], avgVolume: 0, volumeTrend: "Stable", obvTrend: "Accumulation" };
    }

    const series = technicals_series.slice(-60);

    const mapped = series.map((s) => ({
      date: (s.date ?? s.Date ?? "").length > 5 ? (s.date ?? s.Date ?? "").slice(5) : (s.date ?? s.Date ?? ""),
      volume: s.volume ?? s.Volume ?? 0,
      obv: s.obv ?? s.OBV ?? 0,
      close: s.close ?? s.Close ?? 0,
      open: s.open ?? s.Open ?? s.close ?? s.Close ?? 0,
    }));

    // Color-code volume bars
    const colored = mapped.map((d) => ({
      ...d,
      volumeUp: d.close >= d.open ? d.volume : 0,
      volumeDown: d.close < d.open ? d.volume : 0,
    }));

    // Stats from last 20 days
    const last20 = mapped.slice(-20);
    const avg = last20.length > 0 ? last20.reduce((s, d) => s + d.volume, 0) / last20.length : 0;

    // Volume trend: compare last 10 avg vs prior 10 avg
    let volTrend = "Stable";
    if (last20.length >= 20) {
      const recent10 = last20.slice(-10).reduce((s, d) => s + d.volume, 0) / 10;
      const prior10 = last20.slice(0, 10).reduce((s, d) => s + d.volume, 0) / 10;
      const ratio = prior10 > 0 ? recent10 / prior10 : 1;
      if (ratio > 1.15) volTrend = "Increasing";
      else if (ratio < 0.85) volTrend = "Decreasing";
    }

    // OBV trend: direction of last few OBV values
    let obvDir = "Accumulation";
    if (mapped.length >= 5) {
      const recentObv = mapped.slice(-5);
      const obvStart = recentObv[0].obv;
      const obvEnd = recentObv[recentObv.length - 1].obv;
      if (obvEnd < obvStart) obvDir = "Distribution";
    }

    return { chartData: colored, avgVolume: avg, volumeTrend: volTrend, obvTrend: obvDir };
  }, [technicals_series]);

  if (chartData.length === 0) {
    return (
      <div className="bg-surface border border-border rounded-card p-card-padding-lg flex flex-col">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Volume &amp; OBV Analysis</h3>
          <InfoTooltip title="Volume & On-Balance Volume (OBV)">
            <p>Green bars = up days (buying), red bars = down days (selling).</p>
            <p>OBV purple line tracks cumulative buying vs selling pressure.</p>
            <p>Rising OBV + rising price = confirmed uptrend (money flowing in). Falling OBV + rising price = divergence warning.</p>
          </InfoTooltip>
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-sm text-text-muted">Awaiting data</p>
        </div>
      </div>
    );
  }

  const trendColor = (t: string) =>
    t === "Increasing" || t === "Accumulation"
      ? "text-bullish"
      : t === "Decreasing" || t === "Distribution"
        ? "text-bearish"
        : "text-neutral";

  const formatVolume = (v: number) => {
    if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
    if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(0)}K`;
    return v.toFixed(0);
  };

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Volume &amp; OBV Analysis</h3>
        <InfoTooltip title="Volume & On-Balance Volume (OBV)">
          <p>Green bars = up days (buying), red bars = down days (selling). OBV purple line tracks cumulative buying vs selling pressure.</p>
          <p>Rising OBV + rising price = confirmed uptrend (money flowing in). Falling OBV + rising price = divergence warning (smart money may be selling).</p>
          <p>Volume spikes often signal the start or end of a trend. Average volume and trend indicators help identify if activity is increasing or decreasing.</p>
        </InfoTooltip>
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 5, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: "#8B949E" }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            yAxisId="vol"
            tick={{ fontSize: 10, fill: "#8B949E" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatVolume(v)}
          />
          <YAxis
            yAxisId="obv"
            orientation="right"
            tick={{ fontSize: 10, fill: "#A78BFA" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => formatVolume(v)}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#1C2128",
              border: "1px solid #21262D",
              borderRadius: 8,
              fontSize: 11,
            }}
            labelStyle={{ color: "#E6EDF3" }}
            formatter={(value: number, name: string) => [formatVolume(value), name]}
          />
          {/* Up-day volume (green) */}
          <Bar
            yAxisId="vol"
            dataKey="volumeUp"
            stackId="vol"
            fill="#00FFC2"
            fillOpacity={0.6}
            radius={[1, 1, 0, 0]}
            isAnimationActive={false}
            name="Vol (Up)"
          />
          {/* Down-day volume (red) */}
          <Bar
            yAxisId="vol"
            dataKey="volumeDown"
            stackId="vol"
            fill="#FF4B2B"
            fillOpacity={0.6}
            radius={[1, 1, 0, 0]}
            isAnimationActive={false}
            name="Vol (Down)"
          />
          {/* OBV line */}
          <Line
            yAxisId="obv"
            type="monotone"
            dataKey="obv"
            stroke="#A78BFA"
            strokeWidth={2}
            dot={false}
            name="OBV"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-3 mt-3 text-center">
        <div>
          <p className="text-[10px] text-text-muted uppercase">Avg Vol (20d)</p>
          <p className="font-mono text-xs text-text-primary font-semibold">
            {formatVolume(avgVolume)}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted uppercase">Vol Trend</p>
          <p className={`font-mono text-xs font-semibold ${trendColor(volumeTrend)}`}>
            {volumeTrend}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-text-muted uppercase">OBV Trend</p>
          <p className={`font-mono text-xs font-semibold ${trendColor(obvTrend)}`}>
            {obvTrend}
          </p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ML Prediction Panel
// ---------------------------------------------------------------------------

function MLPredictionPanel({ data }: { data: AlphaData }) {
  const [retraining, setRetraining] = useState(false);
  const [retrainResult, setRetrainResult] = useState<string | null>(null);

  const ml = data.ml_prediction;
  const features = data.feature_importance || [];
  const isTrained = !!ml;

  const handleRetrain = async () => {
    setRetraining(true);
    setRetrainResult(null);
    try {
      const res = await fetch(`${API_BASE}/ml/train`, { method: "POST" });
      const json = await res.json();
      if (json.status === "trained") {
        setRetrainResult("Model retrained successfully");
      } else if (json.status === "insufficient_data") {
        setRetrainResult(json.message || "Insufficient data");
      } else {
        setRetrainResult(json.message || "Training failed");
      }
    } catch {
      setRetrainResult("Network error during retrain");
    }
    setRetraining(false);
  };

  // Direction color helper
  const dirColor = (dir?: string) => {
    if (dir === "up") return "text-bullish";
    if (dir === "down") return "text-bearish";
    return "text-text-muted";
  };

  const dirBg = (dir?: string) => {
    if (dir === "up") return "bg-bullish";
    if (dir === "down") return "bg-bearish";
    return "bg-neutral";
  };

  // Max importance for scaling bars
  const maxImportance = features.length > 0 ? Math.max(...features.map((f) => f.importance)) : 1;

  if (!isTrained) {
    return (
      <div className="bg-surface border border-border rounded-card p-card-padding-lg">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Cpu size={16} className="text-accent-blue" />
            <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
              ML Forecast
            </h3>
            <InfoTooltip title="Machine Learning Prediction">
              <p>Uses a LightGBM ensemble model trained on historical feature snapshots.</p>
              <p>Predicts 1-day and 5-day price direction (up/down/flat) with probability estimates. Score Blend combines rule-based analysis (60%) with ML prediction (40%).</p>
              <p>Requires 50+ labeled samples to train — keep the pipeline running to accumulate data. Retrain periodically to adapt to changing market conditions.</p>
            </InfoTooltip>
          </div>
          <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-neutral/20 text-text-muted border border-neutral/30">
            Not Trained
          </span>
        </div>
        <div className="flex flex-col items-center justify-center py-10 text-text-muted">
          <Info size={24} className="mb-3 opacity-40" />
          <p className="text-sm font-medium mb-1">Model not trained yet</p>
          <p className="text-xs text-text-muted text-center max-w-md mb-4">
            The ML model requires at least 50 labeled feature snapshots before training.
            Keep the pipeline running to accumulate data, then trigger training.
          </p>
          <button
            onClick={handleRetrain}
            disabled={retraining}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-accent-blue/20 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/30 transition-colors disabled:opacity-50"
          >
            {retraining ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <RefreshCw size={12} />
            )}
            {retraining ? "Training..." : "Train Model"}
          </button>
          {retrainResult && (
            <p className="text-[11px] text-text-muted mt-2">{retrainResult}</p>
          )}
        </div>
      </div>
    );
  }

  const probUp1d = ml.probability_up_1d ?? 0;
  const probDown1d = ml.probability_down_1d ?? 0;
  const probFlat1d = ml.probability_flat_1d ?? 0;
  const probUp5d = ml.probability_up_5d ?? 0;
  const probDown5d = ml.probability_down_5d ?? 0;
  const probFlat5d = ml.probability_flat_5d ?? 0;
  const confidence = ml.confidence ?? 0;
  const predictedReturn1d = ml.predicted_return_1d ?? 0;
  const ruleScore = data.score?.overall ?? 0;
  const mlScore = ml.ml_score ?? 0;
  const blendedScore = ml.blended_score ?? ruleScore;
  const stats = ml.training_stats;

  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-2">
          <Cpu size={16} className="text-accent-blue" />
          <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">
            ML Forecast
          </h3>
          <InfoTooltip title="Machine Learning Prediction">
            <p>Uses a LightGBM ensemble model trained on historical feature snapshots.</p>
            <p>Predicts 1-day and 5-day price direction (up/down/flat) with probability estimates. Score Blend combines rule-based analysis (60%) with ML prediction (40%).</p>
            <p>Feature Importance shows which indicators the model found most predictive. The model improves over time as more labeled data accumulates in the feature store.</p>
            <p>Retrain periodically to adapt to changing market conditions.</p>
          </InfoTooltip>
        </div>
        <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-bullish/20 text-bullish border border-bullish/30">
          Trained
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* --- Direction Predictions --- */}
        <div className="lg:col-span-1 space-y-4">
          <h4 className="text-[11px] text-text-muted uppercase tracking-wider font-semibold">
            Direction Predictions
          </h4>

          {/* 1-Day */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-text-secondary">1-Day</span>
              <span className={`font-mono text-xs font-semibold capitalize ${dirColor(ml.direction_1d)}`}>
                {ml.direction_1d || "---"}
              </span>
            </div>
            <div className="flex h-2.5 rounded-full overflow-hidden bg-surface-alt">
              <div className="bg-bullish transition-all" style={{ width: `${probUp1d * 100}%` }} />
              <div className="bg-neutral transition-all" style={{ width: `${probFlat1d * 100}%` }} />
              <div className="bg-bearish transition-all" style={{ width: `${probDown1d * 100}%` }} />
            </div>
            <div className="flex justify-between mt-1 text-[10px] text-text-muted font-mono">
              <span className="text-bullish">{(probUp1d * 100).toFixed(0)}%</span>
              <span>{(probFlat1d * 100).toFixed(0)}%</span>
              <span className="text-bearish">{(probDown1d * 100).toFixed(0)}%</span>
            </div>
            <div className="flex items-center justify-between mt-1.5">
              <span className="text-[10px] text-text-muted">Predicted return</span>
              <span className={`font-mono text-xs font-semibold ${predictedReturn1d >= 0 ? "text-bullish" : "text-bearish"}`}>
                {predictedReturn1d >= 0 ? "+" : ""}{(predictedReturn1d * 100).toFixed(2)}%
              </span>
            </div>
          </div>

          {/* 5-Day */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-xs text-text-secondary">5-Day</span>
              <span className={`font-mono text-xs font-semibold capitalize ${dirColor(ml.direction_5d)}`}>
                {ml.direction_5d || "---"}
              </span>
            </div>
            <div className="flex h-2.5 rounded-full overflow-hidden bg-surface-alt">
              <div className="bg-bullish transition-all" style={{ width: `${probUp5d * 100}%` }} />
              <div className="bg-neutral transition-all" style={{ width: `${probFlat5d * 100}%` }} />
              <div className="bg-bearish transition-all" style={{ width: `${probDown5d * 100}%` }} />
            </div>
            <div className="flex justify-between mt-1 text-[10px] text-text-muted font-mono">
              <span className="text-bullish">{(probUp5d * 100).toFixed(0)}%</span>
              <span>{(probFlat5d * 100).toFixed(0)}%</span>
              <span className="text-bearish">{(probDown5d * 100).toFixed(0)}%</span>
            </div>
          </div>

          {/* Confidence */}
          <div className="flex items-center justify-between pt-2 border-t border-border">
            <span className="text-[11px] text-text-muted">Confidence</span>
            <span className="font-mono text-sm font-semibold text-accent-blue">
              {(confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>

        {/* --- Score Blend --- */}
        <div className="lg:col-span-1 space-y-4">
          <h4 className="text-[11px] text-text-muted uppercase tracking-wider font-semibold">
            Score Blend
          </h4>
          <div className="flex items-end gap-4 h-36">
            {/* Rule-Based */}
            <div className="flex-1 flex flex-col items-center">
              <span className={`font-mono text-sm font-bold mb-1 ${ruleScore >= 0 ? "text-bullish" : "text-bearish"}`}>
                {ruleScore >= 0 ? "+" : ""}{(ruleScore * 100).toFixed(0)}
              </span>
              <div className="w-full bg-surface-alt rounded-t-sm overflow-hidden relative" style={{ height: "100px" }}>
                <div
                  className={`absolute bottom-0 w-full rounded-t-sm transition-all ${ruleScore >= 0 ? "bg-bullish/60" : "bg-bearish/60"}`}
                  style={{ height: `${Math.min(Math.abs(ruleScore) * 100, 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-text-muted mt-1.5">Rule</span>
            </div>

            {/* ML */}
            <div className="flex-1 flex flex-col items-center">
              <span className={`font-mono text-sm font-bold mb-1 ${mlScore >= 0 ? "text-bullish" : "text-bearish"}`}>
                {mlScore >= 0 ? "+" : ""}{(mlScore * 100).toFixed(0)}
              </span>
              <div className="w-full bg-surface-alt rounded-t-sm overflow-hidden relative" style={{ height: "100px" }}>
                <div
                  className={`absolute bottom-0 w-full rounded-t-sm transition-all ${mlScore >= 0 ? "bg-accent-blue/60" : "bg-bearish/60"}`}
                  style={{ height: `${Math.min(Math.abs(mlScore) * 100, 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-text-muted mt-1.5">ML</span>
            </div>

            {/* Blended */}
            <div className="flex-1 flex flex-col items-center">
              <span className={`font-mono text-sm font-bold mb-1 ${blendedScore >= 0 ? "text-bullish" : "text-bearish"}`}>
                {blendedScore >= 0 ? "+" : ""}{(blendedScore * 100).toFixed(0)}
              </span>
              <div className="w-full bg-surface-alt rounded-t-sm overflow-hidden relative" style={{ height: "100px" }}>
                <div
                  className={`absolute bottom-0 w-full rounded-t-sm transition-all ${blendedScore >= 0 ? "bg-[#A78BFA]/60" : "bg-bearish/60"}`}
                  style={{ height: `${Math.min(Math.abs(blendedScore) * 100, 100)}%` }}
                />
              </div>
              <span className="text-[10px] text-text-muted mt-1.5">Blend</span>
            </div>
          </div>
        </div>

        {/* --- Feature Importance --- */}
        <div className="lg:col-span-1 space-y-3">
          <h4 className="text-[11px] text-text-muted uppercase tracking-wider font-semibold">
            Top Features
          </h4>
          {features.length === 0 ? (
            <p className="text-xs text-text-muted py-4">No feature data</p>
          ) : (
            <div className="space-y-1.5">
              {features.map((f, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted w-28 truncate font-mono" title={f.feature}>
                    {f.feature}
                  </span>
                  <div className="flex-1 h-1.5 rounded-full bg-surface-alt overflow-hidden">
                    <div
                      className="h-full rounded-full bg-accent-blue/70 transition-all"
                      style={{ width: `${(f.importance / maxImportance) * 100}%` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] text-text-secondary w-10 text-right">
                    {(f.importance * 100).toFixed(1)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* --- Model Info Footer --- */}
        <div className="lg:col-span-1 space-y-3">
          <h4 className="text-[11px] text-text-muted uppercase tracking-wider font-semibold">
            Model Info
          </h4>
          <div className="space-y-2 text-xs">
            {stats?.trained_at && (
              <div className="flex items-center justify-between">
                <span className="text-text-muted">Trained</span>
                <span className="font-mono text-text-secondary">
                  {new Date(stats.trained_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            )}
            {stats?.samples != null && (
              <div className="flex items-center justify-between">
                <span className="text-text-muted">Samples</span>
                <span className="font-mono text-text-secondary">{stats.samples.toLocaleString()}</span>
              </div>
            )}
            {stats?.val_accuracy_1d != null && (
              <div className="flex items-center justify-between">
                <span className="text-text-muted">Val Acc (1d)</span>
                <span className="font-mono text-text-secondary">
                  {(stats.val_accuracy_1d * 100).toFixed(1)}%
                </span>
              </div>
            )}
            {stats?.val_accuracy_5d != null && (
              <div className="flex items-center justify-between">
                <span className="text-text-muted">Val Acc (5d)</span>
                <span className="font-mono text-text-secondary">
                  {(stats.val_accuracy_5d * 100).toFixed(1)}%
                </span>
              </div>
            )}
            {ml.features_used != null && (
              <div className="flex items-center justify-between">
                <span className="text-text-muted">Features</span>
                <span className="font-mono text-text-secondary">{ml.features_used}</span>
              </div>
            )}
          </div>

          <div className="pt-3 border-t border-border space-y-2">
            <button
              onClick={handleRetrain}
              disabled={retraining}
              className="flex items-center justify-center gap-1.5 w-full px-3 py-1.5 rounded-md text-xs font-medium bg-accent-blue/20 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/30 transition-colors disabled:opacity-50"
            >
              {retraining ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <RefreshCw size={12} />
              )}
              {retraining ? "Retraining..." : "Retrain Model"}
            </button>
            {retrainResult && (
              <p className="text-[10px] text-text-muted text-center">{retrainResult}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="flex-1 p-module-gap-lg overflow-auto animate-pulse">
      <div className="bg-surface border border-border rounded-card p-card-padding-lg mb-module-gap-lg">
        <div className="h-6 bg-surface-alt rounded w-1/3 mb-2" />
        <div className="h-4 bg-surface-alt rounded w-1/4" />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-module-gap-lg">
        <div className="bg-surface border border-border rounded-card min-h-[400px]" />
        <div className="space-y-module-gap">
          <div className="bg-surface border border-border rounded-card h-40" />
          <div className="bg-surface border border-border rounded-card h-40" />
          <div className="bg-surface border border-border rounded-card h-40" />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function FinancialAlphaPage() {
  const params = useParams();
  const ticker = (params.ticker as string) || "TSLA";
  const { data, loading, error } = useAlphaData(ticker);
  const [showAnalysis, setShowAnalysis] = useState(false);

  // Prepare chart data from candles + technicals_series
  const chartData = useMemo(() => {
    if (!data) return [];
    return data.candles.map((c, i) => {
      const ts = data.technicals_series?.[i] || {};
      return {
        date: c.date.length > 5 ? c.date.slice(5) : c.date,
        close: c.close,
        volume: c.volume,
        sma20: ts.sma20 ?? ts.SMA_20 ?? null,
        sma50: ts.sma50 ?? ts.SMA_50 ?? null,
        sentiment: data.sentiment
          ? data.sentiment.score * 100
          : null,
      };
    });
  }, [data]);

  // Price domain for Y axis
  const priceDomain = useMemo(() => {
    if (!data || data.candles.length === 0) return [0, 100];
    const prices = data.candles.map((c) => c.close);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const pad = (max - min) * 0.08 || 5;
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [data]);

  // Max volume for bar scaling
  const maxVol = useMemo(() => {
    if (!data) return 1;
    return Math.max(...data.candles.map((c) => c.volume), 1);
  }, [data]);

  // Volume data scaled to show as fraction of price domain
  const chartDataWithScaledVol = useMemo(() => {
    const domainRange = priceDomain[1] - priceDomain[0];
    return chartData.map((d) => ({
      ...d,
      volScaled: d.volume ? priceDomain[0] + (d.volume / maxVol) * domainRange * 0.15 : 0,
    }));
  }, [chartData, priceDomain, maxVol]);

  if (loading) {
    return (
      <div className="flex flex-col h-screen bg-base text-text-primary">
        <Header title="Financial Alpha" />
        <SourceTicker />
        <LoadingSkeleton />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex flex-col h-screen bg-base text-text-primary">
        <Header title="Financial Alpha" />
        <SourceTicker />
        <div className="flex-1 flex flex-col items-center justify-center gap-4">
          <AlertTriangle size={40} className="text-text-muted" />
          <p className="text-text-muted text-sm">
            No data available for <span className="font-mono font-semibold text-text-primary">{ticker}</span>
          </p>
          <Link href="/" className="text-accent-blue text-sm hover:underline">
            Return to Global Pulse
          </Link>
        </div>
      </div>
    );
  }

  const changePct = data.change_pct ?? 0;
  const changePositive = changePct >= 0;
  const sentimentScore = data.sentiment?.score ?? 0.5;
  const sentimentLabel = data.sentiment?.label ?? "neutral";

  const sentBadgeColor =
    sentimentLabel === "bullish" || sentimentScore > 0.6
      ? "bg-bullish/10 text-bullish border-bullish/20"
      : sentimentLabel === "bearish" || sentimentScore < 0.4
        ? "bg-bearish/10 text-bearish border-bearish/20"
        : "bg-neutral/10 text-neutral border-neutral/20";

  const sentBadgeText =
    sentimentLabel === "bullish" || sentimentScore > 0.6
      ? "Bullish"
      : sentimentLabel === "bearish" || sentimentScore < 0.4
        ? "Bearish"
        : "Neutral";

  return (
    <div className="flex flex-col h-screen bg-base text-text-primary">
      <Header title="Financial Alpha" />
      <SourceTicker />

      <div className="flex-1 p-module-gap-lg overflow-auto">
        {/* ================================================================
            1. Ticker Header Bar
            ================================================================ */}
        <div className="bg-surface border border-border rounded-card p-card-padding-lg mb-module-gap-lg flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="text-text-muted hover:text-accent-blue transition-colors"
              title="Back to Global Pulse"
            >
              <ArrowLeft size={18} />
            </Link>
            <div>
              <h2 className="text-xl font-semibold flex items-center gap-2">
                <span className="font-mono text-2xl tracking-tight">{data.ticker}</span>
                <span className="text-text-secondary font-normal text-base">{data.company}</span>
              </h2>
              {data.range_52w && data.range_52w.low != null && data.range_52w.high != null && (
                <p className="text-[11px] text-text-muted font-mono mt-0.5">
                  52W: ${data.range_52w.low.toFixed(2)} — ${data.range_52w.high.toFixed(2)}
                </p>
              )}
            </div>
          </div>

          <div className="flex items-center gap-5">
            {/* Price & change */}
            <div className="text-right">
              <p className="font-mono text-2xl font-semibold">${data.price?.toFixed(2) ?? "--"}</p>
              <p className={`font-mono text-sm ${changePositive ? "text-bullish" : "text-bearish"}`}>
                {changePositive ? "+" : ""}
                {data.change_amt?.toFixed(2) ?? "0.00"} ({changePositive ? "+" : ""}
                {changePct?.toFixed(2) ?? "0.00"}%)
              </p>
            </div>

            {/* Sentiment badge */}
            <div className={`px-3 py-1.5 rounded-md text-sm font-mono font-semibold border ${sentBadgeColor}`}>
              {sentBadgeText} {Math.round(sentimentScore * 100)}%
            </div>

            {/* AI Analysis button */}
            <button
              onClick={() => setShowAnalysis(true)}
              className="flex items-center gap-2 px-3 py-1.5 text-sm font-medium bg-accent-blue/10 text-accent-blue border border-accent-blue/20 rounded-md hover:bg-accent-blue/20 transition-colors"
            >
              <Brain size={16} />
              AI Analysis
            </button>
          </div>
        </div>

        {/* ================================================================
            2 + 3. Main Chart (left) + Technical Indicators (right)
            ================================================================ */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-module-gap-lg mb-module-gap-lg">
          {/* Main Price Chart */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg min-h-[400px]">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Price + Sentiment Overlay</h3>
              <InfoTooltip title="Price + Sentiment Chart">
                <p>Shows the closing price overlaid with pipeline sentiment score.</p>
                <p>Blue dashed lines are SMA20 (short-term trend) and SMA50 (medium-term trend). When price crosses above SMA20, it signals short-term bullish momentum.</p>
                <p>When sentiment diverges from price (sentiment falling while price rising), it is a warning signal.</p>
                <p>Volume bars at the bottom show trading activity — spikes often precede big moves.</p>
              </InfoTooltip>
            </div>
            <ResponsiveContainer width="100%" height={420}>
              <ComposedChart
                data={chartDataWithScaledVol}
                margin={{ top: 10, right: 10, bottom: 0, left: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "#8B949E" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  yAxisId="price"
                  domain={priceDomain}
                  tick={{ fontSize: 11, fill: "#E6EDF3" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: number) => `$${v}`}
                />
                <YAxis
                  yAxisId="sentiment"
                  orientation="right"
                  domain={[0, 100]}
                  tick={{ fontSize: 11, fill: "#00FFC2" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: number) => `${v}%`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1C2128",
                    border: "1px solid #21262D",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  labelStyle={{ color: "#E6EDF3" }}
                />
                {/* Volume bars (scaled to price domain bottom 15%) */}
                <Bar
                  yAxisId="price"
                  dataKey="volScaled"
                  fill="#3B82F6"
                  fillOpacity={0.15}
                  radius={[1, 1, 0, 0]}
                  isAnimationActive={false}
                  name="Volume"
                />
                {/* Sentiment area */}
                <Area
                  yAxisId="sentiment"
                  type="monotone"
                  dataKey="sentiment"
                  fill="url(#sentGradient)"
                  stroke="#00FFC2"
                  strokeWidth={1}
                  name="Sentiment %"
                  isAnimationActive={false}
                />
                {/* SMA 20 */}
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="sma20"
                  stroke="#58A6FF"
                  strokeWidth={1}
                  strokeDasharray="4 3"
                  dot={false}
                  name="SMA 20"
                  connectNulls
                  isAnimationActive={false}
                />
                {/* SMA 50 */}
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="sma50"
                  stroke="#A78BFA"
                  strokeWidth={1}
                  strokeDasharray="4 3"
                  dot={false}
                  name="SMA 50"
                  connectNulls
                  isAnimationActive={false}
                />
                {/* Price line */}
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="close"
                  stroke="#E6EDF3"
                  strokeWidth={2}
                  dot={false}
                  name="Close"
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <defs>
                  <linearGradient id="sentGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00FFC2" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#00FFC2" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* Technical Indicators Sidebar */}
          <div className="space-y-module-gap">
            <RSIGauge value={data.technicals?.rsi} />
            <MACDCard macd={data.technicals?.macd ?? null} series={data.technicals_series} />
            <BollingerCard bb={data.technicals?.bb ?? null} />
          </div>
        </div>

        {/* ================================================================
            4. Signal Scorecard
            ================================================================ */}
        {data.score && <div className="mb-module-gap-lg"><SignalScorecard score={data.score} /></div>}

        {/* ================================================================
            4b. Volume Profile + Volume & OBV Analysis
            ================================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-module-gap-lg mb-module-gap-lg">
          <VolumeProfile candles={data.candles} />
          <VolumeAnalysis technicals_series={data.technicals_series} />
        </div>

        {/* ================================================================
            4c. ML Prediction Panel
            ================================================================ */}
        <div className="mb-module-gap-lg">
          <MLPredictionPanel data={data} />
        </div>

        {/* ================================================================
            5. News & Sources Feed
            ================================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-module-gap-lg mb-module-gap-lg">
          {/* Left: Pipeline Signals */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Pipeline Signals</h3>
              <InfoTooltip title="Pipeline Signals">
                <p>Headlines and documents from the OSINT pipeline that mention this ticker.</p>
                <p>Each card shows the title, source, timestamp, and a content preview. Click linked titles to open the original source.</p>
                <p>Source colors indicate the data origin (HackerNews, GitHub, Reddit, SEC, etc.).</p>
              </InfoTooltip>
            </div>
            {data.sentiment.sample_docs.length === 0 ? (
              <p className="text-sm text-text-muted text-center py-6">No documents available</p>
            ) : (
              <div className="space-y-3 max-h-80 overflow-y-auto pr-1">
                {data.sentiment.sample_docs.map((doc, i) => (
                  <div
                    key={i}
                    className="p-3 rounded-md bg-surface-alt border border-border/50"
                  >
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <p className="text-sm text-text-primary leading-snug line-clamp-2">
                        {doc.url ? (
                          <a
                            href={doc.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="hover:text-accent-blue transition-colors"
                          >
                            {doc.title}
                            <ExternalLink size={10} className="inline ml-1 opacity-50" />
                          </a>
                        ) : (
                          doc.title
                        )}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 mt-1.5">
                      <span
                        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                          SOURCE_COLORS[doc.source]
                            ? `${SOURCE_COLORS[doc.source]}/20 text-text-primary`
                            : "bg-surface text-text-muted"
                        }`}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${SOURCE_COLORS[doc.source] || "bg-text-muted"}`}
                        />
                        {doc.source}
                      </span>
                      <span className="text-[10px] text-text-muted font-mono">
                        {new Date(doc.created_at).toLocaleDateString("en-US", {
                          month: "short",
                          day: "numeric",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    </div>
                    {doc.content && (
                      <p className="text-[11px] text-text-muted mt-1.5 line-clamp-2 leading-relaxed">
                        {doc.content}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Right: Signal Context */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wider">Signal Context</h3>
              <InfoTooltip title="Signal Context">
                <p>Cross-correlated signal from the pipeline engine, combining multiple data sources.</p>
                <p>Confidence score reflects multi-source agreement strength. Higher confidence means more independent sources agree on the signal direction.</p>
                <p>Keywords show the most frequent terms driving sentiment. Volume by Source breaks down how many documents came from each data source.</p>
              </InfoTooltip>
            </div>

            {data.signal ? (
              <div className="space-y-4">
                {/* Signal headline */}
                <div className="p-4 rounded-md bg-surface-alt border border-border">
                  <p className="text-sm text-text-primary font-medium leading-snug mb-2">
                    {data.signal.headline}
                  </p>
                  <div className="flex items-center gap-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[11px] font-semibold border capitalize ${
                        data.signal.type === "bullish"
                          ? "text-bullish bg-bullish/10 border-bullish/20"
                          : data.signal.type === "bearish"
                            ? "text-bearish bg-bearish/10 border-bearish/20"
                            : "text-neutral bg-neutral/10 border-neutral/20"
                      }`}
                    >
                      {data.signal.type}
                    </span>
                    <span className="text-[11px] text-text-muted">
                      Confidence:{" "}
                      <span className="font-mono text-accent-blue">
                        {(data.signal.confidence * 100).toFixed(0)}%
                      </span>
                    </span>
                  </div>
                </div>

                {/* Signal sources */}
                <div>
                  <p className="text-[11px] text-text-muted uppercase tracking-wider mb-2">Sources</p>
                  <div className="flex flex-wrap gap-2">
                    {data.signal.sources.map((src, i) => (
                      <span
                        key={i}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] bg-surface-alt border border-border text-text-secondary"
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${SOURCE_COLORS[src] || "bg-text-muted"}`}
                        />
                        {src}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Sentiment keywords */}
                {data.sentiment.keywords.length > 0 && (
                  <div>
                    <p className="text-[11px] text-text-muted uppercase tracking-wider mb-2">Keywords</p>
                    <div className="flex flex-wrap gap-1.5">
                      {data.sentiment.keywords.map((kw, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 rounded text-[11px] bg-accent-blue/10 text-accent-blue border border-accent-blue/20"
                        >
                          {kw}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Source breakdown */}
                {Object.keys(data.sentiment.sources).length > 0 && (
                  <div>
                    <p className="text-[11px] text-text-muted uppercase tracking-wider mb-2">
                      Volume by Source
                    </p>
                    <div className="space-y-1.5">
                      {Object.entries(data.sentiment.sources)
                        .sort(([, a], [, b]) => b - a)
                        .map(([src, count]) => {
                          const maxCount = Math.max(
                            ...Object.values(data.sentiment.sources),
                            1,
                          );
                          const pct = (count / maxCount) * 100;
                          return (
                            <div key={src} className="flex items-center gap-2">
                              <span className="text-[11px] text-text-muted w-24 truncate">
                                {src}
                              </span>
                              <div className="flex-1 h-1.5 rounded-full bg-surface-alt overflow-hidden">
                                <div
                                  className="h-full rounded-full bg-accent-blue/60"
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                              <span className="font-mono text-[11px] text-text-secondary w-8 text-right">
                                {count}
                              </span>
                            </div>
                          );
                        })}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-text-muted">
                <Loader2 size={20} className="animate-spin mb-2 opacity-50" />
                <p className="text-sm">Awaiting correlated signal</p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ================================================================
          Analysis Modal
          ================================================================ */}
      {showAnalysis && (
        <AnalysisModal
          entity={{
            id: data.ticker,
            label: data.company || data.ticker,
            sentiment: sentimentScore,
            volume: data.sentiment?.volume ?? 0,
            sources: data.sentiment?.sources,
            keywords: data.sentiment?.keywords,
            sampleDocs: data.sentiment?.sample_docs,
            signal_type: data.signal?.type,
            confidence: data.signal?.confidence,
            headline: data.signal?.headline,
          }}
          contextType="entity"
          onClose={() => setShowAnalysis(false)}
        />
      )}
    </div>
  );
}
