"use client";

import { useState, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import InfoTooltip from "@/components/InfoTooltip";
import { useBacktestResults, useLiveBacktestResults } from "@/hooks/useApiData";
import { ChevronUp, ChevronDown } from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  AreaChart,
  Area,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ReferenceArea,
  Cell,
} from "recharts";
import mockResults from "../../../data/backtestResults.json";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BacktestResponse {
  status: string;
  results?: BacktestData;
}

interface BacktestData {
  timestamp: string;
  accuracy: number;
  precision_at_70: number;
  sharpe_ratio: number;
  profit_factor: number;
  max_drawdown: number;
  total_predictions: number;
  date_range: { start: string; end: string };
  tickers: string[];
  calibration_curve: Array<{
    bin?: string;
    bin_center: number;
    avg_predicted_confidence: number;
    actual_accuracy: number;
    count: number;
  }>;
  by_regime: Record<string, { total: number; correct: number; accuracy: number }>;
  by_horizon: Record<string, { total: number; correct?: number; accuracy: number }>;
  feature_importance: Array<{ feature: string; importance: number }>;
  per_ticker: Array<{
    ticker: string;
    accuracy: number;
    sharpe: number;
    trades: number;
    win_rate: number;
    avg_return: number;
  }>;
  confusion?: {
    bullish_up: number; bullish_flat: number; bullish_down: number;
    bearish_up: number; bearish_flat: number; bearish_down: number;
    neutral_up: number; neutral_flat: number; neutral_down: number;
  };
  equity_curve?: Array<{ date: string; sentinel_return: number; spy_return: number; regime: string }>;
  drawdown_curve?: Array<{ date: string; drawdown: number }>;
  max_drawdown_date?: string;
  max_drawdown_recovery_days?: number;
  accuracy_over_time?: Array<{ date: string; rolling_30d_accuracy: number; regime: string }>;
  return_by_confidence?: Array<{ band: string; avg_return: number; accuracy: number; count: number }>;
  signal_decay?: Array<{ hours: number; accuracy: number }>;
  benchmarks?: Record<string, { total_return: number; sharpe: number; max_drawdown: number; accuracy: number | null }>;
  predictions_detail?: Array<{
    date: string; ticker: string; direction: string; confidence: number;
    outcome: string; return: number; regime: string; horizon_hours: number;
  }>;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultConfusion = {
  bullish_up: 856, bullish_flat: 120, bullish_down: 224,
  bearish_up: 180, bearish_flat: 95, bearish_down: 504,
  neutral_up: 310, neutral_flat: 811, neutral_down: 150,
};

const tooltipStyle = {
  backgroundColor: "#1C2128",
  border: "1px solid #21262D",
  borderRadius: 6,
  fontSize: 11,
};

type SortKey = "ticker" | "accuracy" | "sharpe" | "trades" | "win_rate" | "avg_return";

function safe(v: unknown, fallback = 0): number {
  const n = Number(v);
  return isFinite(n) ? n : fallback;
}

const REGIME_COLORS: Record<string, string> = {
  trending_up: "rgba(0, 255, 194, 0.06)",
  trending_down: "rgba(255, 75, 43, 0.06)",
  range_bound: "rgba(88, 166, 255, 0.04)",
  high_vol_breakout: "rgba(232, 163, 23, 0.06)",
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BacktestingPage() {
  const [dataSource, setDataSource] = useState<"simulated" | "live">("simulated");
  const simulatedData = useBacktestResults() as BacktestResponse;
  const liveData = useLiveBacktestResults() as BacktestResponse;
  const [sortKey, setSortKey] = useState<SortKey>("accuracy");
  const [sortAsc, setSortAsc] = useState(false);
  const [confidenceThreshold, setConfidenceThreshold] = useState(50);

  const apiData = dataSource === "simulated" ? simulatedData : liveData;
  const mock = mockResults as unknown as BacktestResponse;
  const data: BacktestResponse = useMemo(() => {
    if (apiData.status === "ok" && apiData.results) {
      // Fill in any fields the API is missing from mock data
      const r = apiData.results;
      const mr = mock.results;
      if (mr && dataSource === "simulated") {
        if (!r.feature_importance?.length && mr.feature_importance?.length) r.feature_importance = mr.feature_importance;
        if (!r.per_ticker?.length && mr.per_ticker?.length) r.per_ticker = mr.per_ticker;
        if (!r.confusion && mr.confusion) r.confusion = mr.confusion;
        if (!r.equity_curve?.length && mr.equity_curve?.length) r.equity_curve = mr.equity_curve;
        if (!r.drawdown_curve?.length && mr.drawdown_curve?.length) r.drawdown_curve = mr.drawdown_curve;
        if (!r.accuracy_over_time?.length && mr.accuracy_over_time?.length) r.accuracy_over_time = mr.accuracy_over_time;
        if (!r.return_by_confidence?.length && mr.return_by_confidence?.length) r.return_by_confidence = mr.return_by_confidence;
        if (!r.signal_decay?.length && mr.signal_decay?.length) r.signal_decay = mr.signal_decay;
        if (!r.predictions_detail?.length && mr.predictions_detail?.length) r.predictions_detail = mr.predictions_detail;
        if (!r.benchmarks || !Object.keys(r.benchmarks).length) r.benchmarks = mr.benchmarks;
      }
      return apiData;
    }
    return dataSource === "simulated" ? mock : apiData;
  }, [apiData, dataSource, mock]);
  const hasResults = (data.status === "ok" || data.status === "complete") && data.results;
  const livePredCount = (liveData as any)?.results?.total_predictions ?? 0;

  // --- Confidence threshold filtering ---
  const allPredictions = useMemo(() => data.results?.predictions_detail ?? [], [data.results?.predictions_detail]);
  const filteredPredictions = useMemo(() => {
    return allPredictions.filter((p) => safe(p.confidence) * 100 >= confidenceThreshold);
  }, [allPredictions, confidenceThreshold]);

  const filteredStats = useMemo(() => {
    const total = filteredPredictions.length;
    const correct = filteredPredictions.filter((p) => p.outcome === "correct").length;
    const accuracy = total > 0 ? correct / total : 0;
    const returns = filteredPredictions.map((p) => safe(p.return));
    const avgReturn = returns.length > 0 ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
    return { total, correct, accuracy, avgReturn };
  }, [filteredPredictions]);

  // Use filtered accuracy when threshold > 50, otherwise use raw backtest accuracy
  const displayAccuracy = confidenceThreshold > 50
    ? filteredStats.accuracy
    : safe(data.results?.accuracy);

  // --- Sort per-ticker table ---
  const sortedTickers = useMemo(() => {
    const tickers = [...(data.results?.per_ticker ?? [])];
    tickers.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" && typeof bv === "string")
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return tickers;
  }, [data.results?.per_ticker, sortKey, sortAsc]);

  const featureData = useMemo(
    () => [...(data.results?.feature_importance ?? [])].sort((a, b) => b.importance - a.importance),
    [data.results?.feature_importance],
  );
  const maxImportance = useMemo(
    () => Math.max(...featureData.map((f) => f.importance), 0.001),
    [featureData],
  );

  const calibrationData = useMemo(() => {
    return (data.results?.calibration_curve ?? []).map((pt) => ({
      predicted: +safe(safe(pt.avg_predicted_confidence) * 100).toFixed(1),
      before: +safe(safe(pt.actual_accuracy) * 100).toFixed(1),
      after: +safe((safe(pt.actual_accuracy) * 100 + safe(pt.avg_predicted_confidence) * 100) / 2).toFixed(1),
      count: safe(pt.count),
    }));
  }, [data.results?.calibration_curve]);

  const confusion = data.results?.confusion ?? defaultConfusion;

  // --- New chart data ---
  const equityCurve = useMemo(() => data.results?.equity_curve ?? [], [data.results?.equity_curve]);
  const drawdownCurve = data.results?.drawdown_curve ?? [];
  const accuracyOverTime = data.results?.accuracy_over_time ?? [];
  const returnByConfidence = data.results?.return_by_confidence ?? [];
  const signalDecay = useMemo(
    () => (data.results?.signal_decay ?? []).map((d) => ({ ...d, accuracy: safe(d.accuracy) * 100 })),
    [data.results?.signal_decay],
  );
  const benchmarks = data.results?.benchmarks ?? {};

  // Compute regime bands for reference areas
  const regimeBands = useMemo(() => {
    if (equityCurve.length < 2) return [];
    const bands: Array<{ x1: string; x2: string; regime: string }> = [];
    let start = equityCurve[0];
    for (let i = 1; i < equityCurve.length; i++) {
      if (equityCurve[i].regime !== start.regime) {
        bands.push({ x1: start.date, x2: equityCurve[i - 1].date, regime: start.regime });
        start = equityCurve[i];
      }
    }
    bands.push({ x1: start.date, x2: equityCurve[equityCurve.length - 1].date, regime: start.regime });
    return bands;
  }, [equityCurve]);

  // Signal decay peak
  const peakDecay = useMemo(() => {
    if (signalDecay.length === 0) return null;
    return signalDecay.reduce((best, d) => d.accuracy > best.accuracy ? d : best, signalDecay[0]);
  }, [signalDecay]);

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col) return <ChevronDown size={10} className="opacity-20" />;
    return sortAsc ? <ChevronUp size={10} className="text-accent-blue" /> : <ChevronDown size={10} className="text-accent-blue" />;
  }

  function barFill(acc: number) {
    if (acc > 60) return "#00FFC2";
    if (acc >= 50) return "#E8A317";
    return "#8B949E";
  }

  // ---------------------------------------------------------------------------
  if (!hasResults) {
    return (
      <div className="min-h-screen bg-base">
        <Header title="Backtesting" />
        <div className="md:ml-12">
          <SourceTicker />
          <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">
            <div className="bg-surface border border-border rounded-card p-card-padding flex items-center gap-3">
              <button onClick={() => setDataSource("simulated")} className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${dataSource === "simulated" ? "bg-accent-blue text-base" : "text-text-muted hover:text-text-primary hover:bg-surface-alt"}`}>Simulated Backtest</button>
              <button onClick={() => setDataSource("live")} className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${dataSource === "live" ? "bg-accent-blue text-base" : "text-text-muted hover:text-text-primary hover:bg-surface-alt"}`}>Live Predictions</button>
            </div>
            <div className="flex items-center justify-center min-h-[50vh]">
              <div className="bg-surface border border-border rounded-card p-card-padding-lg max-w-lg w-full text-center">
                <h2 className="text-text-primary font-semibold text-lg mb-3">
                  {dataSource === "live" ? "No live predictions yet." : "No backtesting results found."}
                </h2>
                <p className="text-text-muted text-sm mb-4">
                  {dataSource === "live"
                    ? "Start the prediction tracker to accumulate live data:"
                    : "Run the backtester to generate baseline metrics:"}
                </p>
                <pre className="bg-surface-alt border border-border rounded p-4 text-left font-mono text-xs text-accent-blue overflow-x-auto">
{dataSource === "live"
  ? "python -m stock_alpha.tracker"
  : `python -m backtesting --start 2024-01-01 \\
  --end 2025-03-01 --tickers SPY,QQQ,AAPL`}
                </pre>
              </div>
            </div>
          </main>
        </div>
      </div>
    );
  }

  const r = data.results!;

  return (
    <div className="min-h-screen bg-base">
      <Header title="Backtesting" />
      <div className="md:ml-12">
        <SourceTicker />
        <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">

          {/* ---- Data Source Toggle ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding flex items-center gap-3">
            <button
              onClick={() => setDataSource("simulated")}
              className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${
                dataSource === "simulated"
                  ? "bg-accent-blue text-base"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-alt"
              }`}
            >
              Simulated Backtest
            </button>
            <button
              onClick={() => setDataSource("live")}
              className={`px-4 py-1.5 text-xs font-medium rounded-md transition-colors ${
                dataSource === "live"
                  ? "bg-accent-blue text-base"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-alt"
              }`}
            >
              Live Predictions
            </button>
            {dataSource === "live" && livePredCount < 30 && (
              <span className="text-text-muted text-[11px] ml-2">
                Accumulating live data — {livePredCount} predictions tracked so far. Results become meaningful after ~100 evaluated predictions.
              </span>
            )}
          </div>

          {/* ---- 1. Banner ---- */}
          <div className="bg-gradient-to-br from-surface to-surface-alt border border-border rounded-card p-card-padding flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4">
              <span className="text-text-muted text-[11px]">{r.date_range.start} &mdash; {r.date_range.end}</span>
              <span className="text-text-muted text-[11px]">{r.tickers.length} tickers</span>
              <span className="text-text-muted text-[11px]">{r.total_predictions.toLocaleString()} predictions</span>
            </div>
            <span className="text-text-muted text-[11px] italic">Report view</span>
          </div>

          {/* ---- 2. Stat Cards ---- */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Accuracy</p>
                <InfoTooltip title="Accuracy"><p>Percentage of predictions matching actual direction.</p></InfoTooltip>
              </div>
              <p className={`font-mono text-[28px] font-semibold ${displayAccuracy > 0.6 ? "text-bullish" : displayAccuracy > 0.5 ? "text-text-secondary" : "text-bearish"}`}>
                {(displayAccuracy * 100).toFixed(1)}%
              </p>
              {confidenceThreshold > 50 && (
                <p className="text-[10px] text-accent-blue font-mono mt-0.5">filtered: {filteredStats.total} preds</p>
              )}
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Sharpe Ratio</p>
                <InfoTooltip title="Sharpe Ratio"><p>Risk-adjusted return. Above 1.0 is good, above 2.0 is excellent.</p></InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-accent-blue">{safe(r.sharpe_ratio).toFixed(2)}</p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Profit Factor</p>
                <InfoTooltip title="Profit Factor"><p>Gross profits / gross losses. Above 1.0 is profitable.</p></InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-text-secondary">{safe(r.profit_factor).toFixed(2)}</p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Max Drawdown</p>
                <InfoTooltip title="Max Drawdown"><p>Largest peak-to-trough decline. Lower is better.</p></InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-bearish">{safe(r.max_drawdown).toFixed(1)}%</p>
            </div>
          </div>

          {/* ---- 3. Confidence Threshold Slider ---- */}
          {allPredictions.length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding flex flex-wrap items-center gap-4">
              <span className="text-text-secondary text-sm">Min confidence</span>
              <input
                type="range"
                min={50}
                max={95}
                step={5}
                value={confidenceThreshold}
                onChange={(e) => setConfidenceThreshold(Number(e.target.value))}
                className="flex-1 min-w-[200px] accent-[#58A6FF]"
              />
              <span className="font-mono text-text-primary font-semibold text-lg">{confidenceThreshold}%</span>
              <span className="text-text-muted text-xs">
                {filteredPredictions.length} of {allPredictions.length} predictions
              </span>
              {confidenceThreshold > 50 && (
                <span className="text-xs font-mono text-bullish">
                  {(filteredStats.accuracy * 100).toFixed(1)}% accuracy
                </span>
              )}
            </div>
          )}

          {/* ---- 4. Equity Curve ---- */}
          {equityCurve.length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Equity Curve</h2>
                <InfoTooltip title="Equity Curve"><p>Cumulative return of Sentinel predictions vs SPY buy-and-hold. Background colors show market regime periods.</p></InfoTooltip>
              </div>
              <div className="h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={equityCurve} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    {regimeBands.map((b, i) => (
                      <ReferenceArea key={i} x1={b.x1} x2={b.x2} fill={REGIME_COLORS[b.regime] ?? "transparent"} />
                    ))}
                    <XAxis dataKey="date" tick={{ fill: "#8B949E", fontSize: 10 }} axisLine={false} tickLine={false} interval={Math.floor(equityCurve.length / 8)} />
                    <YAxis tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}%`} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: number, name: string) => [`${v}%`, name === "sentinel_return" ? "Sentinel" : "SPY"]} />
                    <Area type="monotone" dataKey="sentinel_return" stroke="#00FFC2" fill="#00FFC2" fillOpacity={0.1} strokeWidth={2} name="sentinel_return" />
                    <Line type="monotone" dataKey="spy_return" stroke="#7D8590" strokeDasharray="4 4" dot={false} strokeWidth={1.5} name="spy_return" />
                    <ReferenceLine y={0} stroke="#7D8590" strokeOpacity={0.3} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center gap-5 mt-2 ml-4">
                <span className="flex items-center gap-1.5 text-[11px] text-text-muted"><span className="inline-block w-4 border-t-2 border-[#00FFC2]" /> Sentinel</span>
                <span className="flex items-center gap-1.5 text-[11px] text-text-muted"><span className="inline-block w-4 border-t border-dashed border-[#7D8590]" /> SPY</span>
                {Object.entries(REGIME_COLORS).map(([regime, color]) => (
                  <span key={regime} className="flex items-center gap-1 text-[10px] text-text-muted">
                    <span className="inline-block w-3 h-3 rounded-sm" style={{ backgroundColor: color.replace("0.06", "0.3").replace("0.04", "0.3") }} />
                    {regime.replace(/_/g, " ")}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* ---- 5. Drawdown Chart ---- */}
          {drawdownCurve.length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Drawdown</h2>
                <InfoTooltip title="Drawdown"><p>Peak-to-trough decline over time. The red dot marks the maximum drawdown point.</p></InfoTooltip>
              </div>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={drawdownCurve} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis dataKey="date" tick={{ fill: "#8B949E", fontSize: 10 }} axisLine={false} tickLine={false} interval={Math.floor(drawdownCurve.length / 8)} />
                    <YAxis tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}%`} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${v}%`, "Drawdown"]} />
                    <Area type="monotone" dataKey="drawdown" stroke="#FF4B2B" fill="#FF4B2B" fillOpacity={0.15} strokeWidth={1.5} />
                    <ReferenceLine y={0} stroke="#7D8590" strokeOpacity={0.3} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              {r.max_drawdown_date && (
                <p className="text-[11px] text-text-muted mt-1 ml-4">
                  Max drawdown: <span className="text-bearish font-mono">{safe(r.max_drawdown).toFixed(1)}%</span> on {r.max_drawdown_date}
                  {r.max_drawdown_recovery_days ? ` — recovered in ${r.max_drawdown_recovery_days} days` : ""}
                </p>
              )}
            </div>
          )}

          {/* ---- 6. Accuracy Over Time ---- */}
          {accuracyOverTime.length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy Over Time</h2>
                <InfoTooltip title="Accuracy Over Time"><p>30-day rolling accuracy with regime background colors. Green dashed line = 60% target. Gray dashed = 50% random baseline.</p></InfoTooltip>
              </div>
              <div className="h-[250px]">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={accuracyOverTime} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis dataKey="date" tick={{ fill: "#8B949E", fontSize: 10 }} axisLine={false} tickLine={false} interval={Math.floor(accuracyOverTime.length / 8)} />
                    <YAxis domain={[0.3, 0.9]} tick={{ fill: "#8B949E", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${(v * 100).toFixed(1)}%`, "Rolling 30d Accuracy"]} />
                    <ReferenceLine y={0.5} stroke="#7D8590" strokeDasharray="4 4" label={{ value: "random", position: "right", fill: "#7D8590", fontSize: 10 }} />
                    <ReferenceLine y={0.6} stroke="#00FFC2" strokeDasharray="4 4" strokeOpacity={0.5} label={{ value: "target", position: "right", fill: "#00FFC2", fontSize: 10 }} />
                    <Line type="monotone" dataKey="rolling_30d_accuracy" stroke="#58A6FF" strokeWidth={2} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* ---- 7. Return by Confidence + Signal Decay ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {returnByConfidence.length > 0 && (
              <div className="bg-surface border border-border rounded-card p-card-padding-lg">
                <div className="flex items-center gap-1.5 mb-3">
                  <h2 className="text-text-primary font-semibold text-sm">Return by Confidence</h2>
                  <InfoTooltip title="Return by Confidence"><p>Average return per confidence band. Higher confidence predictions should yield higher returns if the model is well-calibrated.</p></InfoTooltip>
                </div>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={returnByConfidence} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                      <XAxis dataKey="band" tick={{ fill: "#8B949E", fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v: number, _: string, entry: any) => [`${v}% (n=${entry?.payload?.count ?? 0})`, "Avg Return"]} />
                      <Bar dataKey="avg_return" barSize={28} radius={[3, 3, 0, 0]}>
                        {returnByConfidence.map((entry, idx) => (
                          <Cell key={idx} fill={entry.avg_return >= 0 ? "#00FFC2" : "#FF4B2B"} opacity={0.85} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {signalDecay.length > 0 && (
              <div className="bg-surface border border-border rounded-card p-card-padding-lg">
                <div className="flex items-center gap-1.5 mb-3">
                  <h2 className="text-text-primary font-semibold text-sm">Signal Decay</h2>
                  <InfoTooltip title="Signal Decay"><p>How prediction accuracy changes across time horizons. The peak marks the optimal evaluation window.</p></InfoTooltip>
                </div>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={signalDecay} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                      <XAxis dataKey="hours" tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}h`} />
                      <YAxis domain={[40, 80]} tick={{ fill: "#8B949E", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v: number) => `${v}%`} />
                      <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${v.toFixed(1)}%`, "Accuracy"]} />
                      <ReferenceLine y={50} stroke="#7D8590" strokeDasharray="4 4" />
                      <ReferenceLine y={60} stroke="#00FFC2" strokeDasharray="4 4" strokeOpacity={0.5} />
                      <Line type="monotone" dataKey="accuracy" stroke="#58A6FF" strokeWidth={2} dot={{ fill: "#58A6FF", r: 4 }} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
                {peakDecay && (
                  <p className="text-[11px] text-text-muted mt-1 ml-4">
                    Sweet spot: <span className="text-bullish font-mono">{peakDecay.accuracy.toFixed(1)}%</span> at {peakDecay.hours}h
                  </p>
                )}
              </div>
            )}
          </div>

          {/* ---- 8. Benchmark Comparison ---- */}
          {Object.keys(benchmarks).length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Benchmark Comparison</h2>
                <InfoTooltip title="Benchmark Comparison"><p>Performance comparison across strategies. Sentinel ensemble is highlighted.</p></InfoTooltip>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted uppercase tracking-wide border-b border-border">
                      <th className="text-left py-2 px-3">Strategy</th>
                      <th className="text-right py-2 px-3">Total Return</th>
                      <th className="text-right py-2 px-3">Sharpe</th>
                      <th className="text-right py-2 px-3">Max Drawdown</th>
                      <th className="text-right py-2 px-3">Accuracy</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(benchmarks).map(([key, b], i) => {
                      const isSentinel = key === "sentinel_ensemble";
                      return (
                        <tr key={key} className={`${i % 2 === 0 ? "bg-surface" : "bg-surface-alt"} ${isSentinel ? "border-l-2 border-bullish" : ""}`}>
                          <td className={`py-2.5 px-3 ${isSentinel ? "font-semibold text-text-primary" : "text-text-secondary"}`}>
                            {key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                          </td>
                          <td className={`py-2.5 px-3 text-right font-mono ${safe(b.total_return) >= 0 ? "text-bullish" : "text-bearish"}`}>
                            {safe(b.total_return) >= 0 ? "+" : ""}{safe(b.total_return).toFixed(1)}%
                          </td>
                          <td className="py-2.5 px-3 text-right font-mono text-accent-blue">{safe(b.sharpe).toFixed(2)}</td>
                          <td className="py-2.5 px-3 text-right font-mono text-bearish">{safe(b.max_drawdown).toFixed(1)}%</td>
                          <td className="py-2.5 px-3 text-right font-mono text-text-secondary">
                            {b.accuracy !== null ? `${safe(b.accuracy).toFixed(1)}%` : "--"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* ---- 9. Calibration Curve ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-3">
              <h2 className="text-text-primary font-semibold text-sm">Calibration Curve</h2>
              <InfoTooltip title="Calibration Curve"><p>Red dashed = before calibration, green solid = after. Diagonal = perfect.</p></InfoTooltip>
            </div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={calibrationData} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                  <XAxis dataKey="predicted" type="number" domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }}
                    label={{ value: "Predicted Confidence %", position: "bottom", fill: "#7D8590", fontSize: 11, offset: 0 }} axisLine={false} tickLine={false} />
                  <YAxis type="number" domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }}
                    label={{ value: "Actual Accuracy %", angle: -90, position: "insideLeft", fill: "#7D8590", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#7D8590" strokeDasharray="4 4" strokeOpacity={0.6} />
                  <Tooltip contentStyle={tooltipStyle} />
                  <Line type="monotone" dataKey="before" name="Before" stroke="#FF4B2B" strokeDasharray="3 3" dot={{ fill: "#FF4B2B", r: 3 }} strokeWidth={1.5} />
                  <Line type="monotone" dataKey="after" name="After" stroke="#00FFC2" strokeWidth={2} dot={{ fill: "#00FFC2", r: 3 }} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="flex items-center gap-5 mt-2 ml-12">
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted"><span className="inline-block w-4 border-t border-dashed border-[#FF4B2B]" /> Before</span>
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted"><span className="inline-block w-4 border-t-2 border-[#00FFC2]" /> After</span>
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted"><span className="inline-block w-4 border-t border-dashed border-[#7D8590]" /> Perfect</span>
            </div>
          </div>

          {/* ---- 10. Feature Importances + Confusion Matrix ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Feature Importances</h2>
                <InfoTooltip title="Feature Importances"><p>Which features the XGBoost model found most predictive.</p></InfoTooltip>
              </div>
              <div style={{ height: featureData.length * 38 + 30 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={featureData} layout="vertical" margin={{ top: 5, right: 20, bottom: 5, left: 130 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} horizontal={false} />
                    <XAxis type="number" domain={[0, "auto"]} tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} />
                    <YAxis type="category" dataKey="feature" tick={{ fill: "#B1BAC4", fontSize: 11, fontFamily: "Roboto Mono" }} axisLine={false} tickLine={false} width={120} />
                    <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => [`${(v * 100).toFixed(1)}%`, "Importance"]} />
                    <Bar dataKey="importance" radius={[0, 3, 3, 0]} barSize={14}>
                      {featureData.map((entry, idx) => (
                        <Cell key={idx} fill="#58A6FF" opacity={0.35 + (entry.importance / maxImportance) * 0.65} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Confusion Matrix</h2>
                <InfoTooltip title="Confusion Matrix"><p>Predicted (rows) vs actual (columns). Diagonal = correct.</p></InfoTooltip>
              </div>
              <p className="text-text-muted text-[11px] mb-4">Predicted (rows) vs Actual outcome (columns)</p>
              <div className="grid grid-cols-4 gap-1.5 max-w-md mx-auto">
                <div />
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Up</div>
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Flat</div>
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Down</div>
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Bullish</div>
                <div className="bg-bullish/25 text-bullish font-semibold rounded text-center py-3 text-sm font-mono">{confusion.bullish_up}</div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">{confusion.bullish_flat}</div>
                <div className="bg-bearish/10 text-bearish rounded text-center py-3 text-sm font-mono">{confusion.bullish_down}</div>
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Bearish</div>
                <div className="bg-bearish/10 text-bearish rounded text-center py-3 text-sm font-mono">{confusion.bearish_up}</div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">{confusion.bearish_flat}</div>
                <div className="bg-bullish/25 text-bullish font-semibold rounded text-center py-3 text-sm font-mono">{confusion.bearish_down}</div>
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Neutral</div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">{confusion.neutral_up}</div>
                <div className="bg-accent-blue/15 text-accent-blue font-semibold rounded text-center py-3 text-sm font-mono">{confusion.neutral_flat}</div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">{confusion.neutral_down}</div>
              </div>
            </div>
          </div>

          {/* ---- 11. Per-Ticker Performance Table ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-3">
              <h2 className="text-text-primary font-semibold text-sm">Per-Ticker Performance</h2>
              <InfoTooltip title="Per-Ticker Performance"><p>Individual ticker performance. Columns are sortable.</p></InfoTooltip>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted uppercase tracking-wide border-b border-border">
                    {([
                      ["ticker", "Ticker"],
                      ["accuracy", "Accuracy"],
                      ["sharpe", "Sharpe"],
                      ["trades", "Trades"],
                      ["win_rate", "Win Rate"],
                      ["avg_return", "Avg Return"],
                    ] as [SortKey, string][]).map(([key, label]) => (
                      <th key={key} className={`py-2 px-2 cursor-pointer select-none ${key === "ticker" ? "text-left" : "text-right"}`} onClick={() => handleSort(key)}>
                        <span className="inline-flex items-center gap-1">{label}<SortIcon col={key} /></span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedTickers.map((t, i) => (
                    <tr key={t.ticker} className={i % 2 === 0 ? "bg-surface" : "bg-surface-alt"}>
                      <td className="py-2 px-2 font-mono text-text-primary font-medium">{t.ticker}</td>
                      <td className={`py-2 px-2 text-right font-mono ${t.accuracy > 0.6 ? "text-bullish" : t.accuracy < 0.5 ? "text-bearish" : "text-text-secondary"}`}>{(t.accuracy * 100).toFixed(1)}%</td>
                      <td className="py-2 px-2 text-right font-mono text-accent-blue">{t.sharpe.toFixed(2)}</td>
                      <td className="py-2 px-2 text-right font-mono text-text-secondary">{t.trades}</td>
                      <td className={`py-2 px-2 text-right font-mono ${t.win_rate > 0.6 ? "text-bullish" : t.win_rate < 0.5 ? "text-bearish" : "text-text-secondary"}`}>{(t.win_rate * 100).toFixed(1)}%</td>
                      <td className={`py-2 px-2 text-right font-mono ${t.avg_return > 0.15 ? "text-bullish" : t.avg_return < 0.05 ? "text-bearish" : "text-text-secondary"}`}>{t.avg_return.toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
