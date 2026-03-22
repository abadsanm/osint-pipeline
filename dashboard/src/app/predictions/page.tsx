"use client";

import { useState, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import InfoTooltip from "@/components/InfoTooltip";
import { usePredictions, usePredictionStats } from "@/hooks/useApiData";
import { Check, X } from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  ScatterChart,
  Scatter,
  BarChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
  ZAxis,
} from "recharts";
import mockPredictions from "../../../data/predictions.json";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MockPrediction {
  prediction_id: string;
  ticker: string;
  direction: "bullish" | "bearish" | "neutral";
  magnitude: number;
  confidence: number;
  raw_score: number;
  time_horizon_hours: number;
  decay_rate: number;
  regime: string;
  contributing_signals: Array<{ source: string; signal_type: string; value: number; weight: number }>;
  model_version: string;
  created_at: string;
  expires_at: string;
  outcome: "correct" | "incorrect" | null;
}

interface TableRow {
  id: string;
  ticker: string;
  direction: "bullish" | "bearish" | "neutral";
  confidence: number;
  horizon: string;
  regime: string;
  agreement: number;
  totalModels: number;
  score: number;
  timestamp: string;
  outcome: "correct" | "incorrect" | "pending";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeRow(p: MockPrediction): TableRow {
  const totalModels = p.contributing_signals.length;
  const agreeing = p.contributing_signals.filter((s) => s.signal_type === p.direction).length;
  return {
    id: p.prediction_id,
    ticker: p.ticker,
    direction: p.direction,
    confidence: p.confidence,
    horizon: `${p.time_horizon_hours}h`,
    regime: p.regime.replace(/_/g, " "),
    agreement: agreeing,
    totalModels: Math.max(totalModels, 4),
    score: Math.round(Math.abs(p.raw_score) * 100),
    timestamp: p.created_at,
    outcome: p.outcome ?? "pending",
  };
}

function formatTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const tooltipStyle = {
  backgroundColor: "#1C2128",
  border: "1px solid #21262D",
  borderRadius: 8,
  fontSize: 12,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PredictionsPage() {
  const [tickerFilter, setTickerFilter] = useState("");
  const apiPredictions = usePredictions(50, tickerFilter || undefined) as MockPrediction[];
  const stats = usePredictionStats();

  const rawPredictions: MockPrediction[] =
    Array.isArray(apiPredictions) && apiPredictions.length > 0
      ? apiPredictions
      : (mockPredictions as unknown as MockPrediction[]);

  const rows: TableRow[] = useMemo(() => rawPredictions.map(normalizeRow), [rawPredictions]);

  const filteredRows = tickerFilter
    ? rows.filter((r) => r.ticker.toLowerCase().includes(tickerFilter.toLowerCase()))
    : rows;

  // ── Stat card values ──
  const overallAccuracy = stats.overall_accuracy;
  const rollingAccuracy = stats.rolling_accuracy_200;

  const calibrationError = useMemo(() => {
    const curve = stats.calibration_curve;
    if (!curve || curve.length === 0) return null;
    const totalError = curve.reduce(
      (sum, pt) => sum + Math.abs(pt.avg_predicted_confidence - pt.actual_accuracy),
      0,
    );
    return totalError / curve.length;
  }, [stats.calibration_curve]);

  const rollingDelta = rollingAccuracy - overallAccuracy;

  // ── Chart data ──
  const horizonData = useMemo(() => {
    const bh = stats.by_horizon ?? {};
    return Object.entries(bh).map(([h, v]) => ({
      name: `${h}h`,
      accuracy: +(v.accuracy * 100).toFixed(1),
    }));
  }, [stats.by_horizon]);

  const regimeData = useMemo(() => {
    const br = stats.by_regime ?? {};
    return Object.entries(br).map(([r, v]) => ({
      name: r.replace(/_/g, " "),
      accuracy: +(v.accuracy * 100).toFixed(1),
    }));
  }, [stats.by_regime]);

  const agreementData = useMemo(() => {
    const ma = stats.model_agreement ?? {};
    return {
      four: ma["4_agree_pct"] ?? 0,
      three: ma["3_agree_pct"] ?? 0,
      two: ma["2_agree_pct"] ?? 0,
    };
  }, [stats.model_agreement]);

  const calibrationData = useMemo(() => {
    return (stats.calibration_curve ?? []).map((pt) => ({
      x: +(pt.avg_predicted_confidence * 100).toFixed(1),
      y: +(pt.actual_accuracy * 100).toFixed(1),
      count: pt.count,
    }));
  }, [stats.calibration_curve]);

  // ── Color helpers ──
  function accuracyColor(acc: number) {
    if (acc > 0.6) return "text-bullish";
    if (acc < 0.5) return "text-bearish";
    return "text-text-secondary";
  }

  function calibrationErrorColor(err: number) {
    if (err < 0.05) return "text-bullish";
    if (err > 0.10) return "text-bearish";
    return "text-text-secondary";
  }

  function confidenceColor(conf: number) {
    if (conf > 0.70) return "text-bullish";
    if (conf >= 0.50) return "text-text-secondary";
    return "text-bearish";
  }

  function barFill(acc: number) {
    if (acc > 60) return "#00FFC2";
    if (acc >= 50) return "#FAAB35";
    return "#8B949E";
  }

  function deltaPrefix(val: number) {
    return val >= 0 ? "\u2191" : "\u2193";
  }

  function deltaColor(val: number) {
    if (val > 0) return "text-bullish";
    if (val < 0) return "text-bearish";
    return "text-text-secondary";
  }

  return (
    <div className="min-h-screen bg-base">
      <Header title="Predictions Monitor" />
      <div className="md:ml-12">
        <SourceTicker />
        <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">

          {/* ──── Stat Cards ──── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {/* Total Predictions */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Total Predictions</p>
                <InfoTooltip title="Total Predictions">
                  <p>Total number of directional predictions generated by the ensemble model across all tickers and time horizons. Each prediction combines rule-based scoring, XGBoost classification, and time-series forecasting.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-text-primary">
                {stats.total_predictions || filteredRows.length}
              </p>
            </div>

            {/* Overall Accuracy */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Overall Accuracy</p>
                <InfoTooltip title="Overall Accuracy">
                  <p>Percentage of predictions where the predicted direction (bullish/bearish/neutral) matched the actual price movement. Above 55% is statistically significant for financial markets. The system targets 60-65% accuracy with proper calibration.</p>
                </InfoTooltip>
              </div>
              <p className={`font-mono text-[28px] font-semibold ${accuracyColor(overallAccuracy)}`}>
                {overallAccuracy ? `${(overallAccuracy * 100).toFixed(1)}%` : "--"}
              </p>
            </div>

            {/* Rolling Accuracy (200) */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Rolling Accuracy (200)</p>
                <InfoTooltip title="Rolling Accuracy">
                  <p>Accuracy over the most recent 200 evaluated predictions. This rolling window catches model degradation faster than the overall average. If rolling drops below overall by more than 5%, the model may need retraining. The arrow shows the trend direction.</p>
                </InfoTooltip>
              </div>
              <p className={`font-mono text-[28px] font-semibold ${accuracyColor(rollingAccuracy)}`}>
                {rollingAccuracy ? `${(rollingAccuracy * 100).toFixed(1)}%` : "--"}
              </p>
              {rollingAccuracy > 0 && overallAccuracy > 0 && (
                <p className={`text-xs font-mono mt-0.5 ${deltaColor(rollingDelta)}`}>
                  {deltaPrefix(rollingDelta)}{Math.abs(rollingDelta * 100).toFixed(1)}%
                </p>
              )}
            </div>

            {/* Calibration Error */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Calibration Error</p>
                <InfoTooltip title="Calibration Error">
                  <p>Average absolute difference between stated confidence and actual accuracy. Below 0.05 means the model is well-calibrated — when it says 70% confident, it&apos;s right about 70% of the time. Above 0.10 suggests the model needs recalibration via isotonic regression.</p>
                </InfoTooltip>
              </div>
              <p className={`font-mono text-[28px] font-semibold ${calibrationError !== null ? calibrationErrorColor(calibrationError) : "text-text-secondary"}`}>
                {calibrationError !== null ? `${(calibrationError * 100).toFixed(1)}%` : "--"}
              </p>
            </div>
          </div>

          {/* ──── Predictions Table ──── */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <h2 className="text-text-primary font-semibold text-sm">Predictions</h2>
                <InfoTooltip title="Predictions Table">
                  <p>Live stream of predictions from the ensemble model. Direction shows the predicted price movement. Confidence is the calibrated probability. Agreement shows how many of the 4 models (rule-based, XGBoost, Prophet, LSTM) agreed on the direction — higher agreement generally means higher accuracy.</p>
                </InfoTooltip>
              </div>
              <input
                type="text"
                placeholder="Filter by ticker..."
                value={tickerFilter}
                onChange={(e) => setTickerFilter(e.target.value)}
                className="bg-surface-alt border border-border rounded px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue w-40"
              />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted uppercase tracking-wide border-b border-border">
                    <th className="text-left py-2 px-2">Ticker</th>
                    <th className="text-left py-2 px-2">Direction</th>
                    <th className="text-right py-2 px-2">Confidence</th>
                    <th className="text-left py-2 px-2">Horizon</th>
                    <th className="text-left py-2 px-2">Regime</th>
                    <th className="text-center py-2 px-2">Agreement</th>
                    <th className="text-right py-2 px-2">Score</th>
                    <th className="text-left py-2 px-2">Time</th>
                    <th className="text-center py-2 px-2">Outcome</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((p, i) => (
                    <tr
                      key={p.id}
                      className={i % 2 === 0 ? "bg-surface" : "bg-surface-alt"}
                    >
                      <td className="py-2 px-2 font-mono font-semibold text-text-primary">{p.ticker}</td>
                      <td className="py-2 px-2">
                        <span
                          className={`inline-block text-[10px] font-semibold font-mono px-2 py-0.5 rounded ${
                            p.direction === "bullish"
                              ? "bg-bullish-muted text-bullish"
                              : p.direction === "bearish"
                                ? "bg-bearish-muted text-bearish"
                                : "bg-neutral/15 text-neutral"
                          }`}
                        >
                          {p.direction}
                        </span>
                      </td>
                      <td className={`py-2 px-2 text-right font-mono ${confidenceColor(p.confidence)}`}>
                        {(p.confidence * 100).toFixed(0)}%
                      </td>
                      <td className="py-2 px-2 font-mono text-text-secondary">{p.horizon}</td>
                      <td className="py-2 px-2 text-text-secondary capitalize">{p.regime}</td>
                      <td className="py-2 px-2 text-center font-mono text-accent-blue">
                        {p.agreement}/{p.totalModels}
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-text-primary">{p.score}</td>
                      <td className="py-2 px-2 text-text-muted">{formatTime(p.timestamp)}</td>
                      <td className="py-2 px-2 text-center">
                        {p.outcome === "correct" && <Check size={14} className="inline text-bullish" />}
                        {p.outcome === "incorrect" && <X size={14} className="inline text-bearish" />}
                        {p.outcome === "pending" && <span className="text-text-muted">&mdash;</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ──── Calibration Curve + Accuracy by Horizon ──── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Calibration Curve */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Calibration Curve</h2>
                <InfoTooltip title="Calibration Curve">
                  <p>Compares predicted confidence (X) against actual accuracy (Y). Perfect calibration follows the diagonal line. Points above the line mean the model is under-confident (actual accuracy exceeds stated confidence). Points below mean over-confident. Well-calibrated models make better risk-adjusted decisions.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={calibrationData} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis
                      type="number"
                      dataKey="x"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                      label={{ value: "Predicted Confidence %", position: "bottom", fill: "#7D8590", fontSize: 11, offset: 0 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="y"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                      label={{ value: "Actual Accuracy %", angle: -90, position: "insideLeft", fill: "#7D8590", fontSize: 11 }}
                    />
                    <ZAxis type="number" dataKey="count" range={[40, 400]} />
                    <ReferenceLine
                      segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
                      stroke="#7D8590"
                      strokeDasharray="4 4"
                    />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Scatter dataKey="y" fill="#58A6FF" />
                    <Line type="monotone" dataKey="y" stroke="#58A6FF" strokeWidth={1.5} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Accuracy by Horizon */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy by Horizon</h2>
                <InfoTooltip title="Accuracy by Horizon">
                  <p>How accuracy varies by prediction time horizon. Shorter horizons (1h, 4h) capture momentum but are noisier. Longer horizons (72h, 168h) are more stable but less actionable. The sweet spot is typically 24h for daily trading decisions. Green bars (&gt;60%) indicate reliable horizons.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={horizonData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number) => [`${value}%`, "Accuracy"]}
                    />
                    <Bar dataKey="accuracy" barSize={24} radius={[3, 3, 0, 0]}>
                      {horizonData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* ──── Accuracy by Regime + Model Agreement ──── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Accuracy by Regime — horizontal bar chart */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy by Regime</h2>
                <InfoTooltip title="Accuracy by Regime">
                  <p>Prediction accuracy broken down by market regime. Trending markets are easier to predict than range-bound markets. High-volatility breakouts are hardest. This helps you know when to trust the model&apos;s predictions and when to be cautious.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={regimeData}
                    layout="vertical"
                    margin={{ top: 0, right: 20, bottom: 0, left: 90 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#8B949E"
                      strokeOpacity={0.1}
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 12, fill: "#E6EDF3" }}
                      axisLine={false}
                      tickLine={false}
                      width={85}
                    />
                    <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => [`${value}%`, "Accuracy"]} />
                    <Bar dataKey="accuracy" barSize={14} radius={[0, 3, 3, 0]} opacity={0.85}>
                      {regimeData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Model Agreement — three stat blocks with dividers */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Model Agreement</h2>
                <InfoTooltip title="Model Agreement">
                  <p>Shows how often the 4 prediction models agree. 4/4 agreement (unanimous) historically produces the highest accuracy. When only 2/4 agree, the signal is weak — the model outputs &apos;neutral&apos; with low confidence. Use agreement level as a meta-confidence indicator.</p>
                </InfoTooltip>
              </div>
              <div className="flex items-center justify-center h-56">
                {/* 4/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-bullish">
                    {agreementData.four.toFixed(1)}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">4/4 agree</p>
                  <p className="text-xs text-text-muted mt-0.5">Highest accuracy</p>
                </div>

                <div className="w-px h-20 bg-border" />

                {/* 3/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-accent-blue">
                    {agreementData.three.toFixed(1)}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">3/4 agree</p>
                  <p className="text-xs text-text-muted mt-0.5">Good confidence</p>
                </div>

                <div className="w-px h-20 bg-border" />

                {/* 2/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-neutral">
                    {agreementData.two.toFixed(1)}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">2/4 agree</p>
                  <p className="text-xs text-bearish mt-0.5">Low confidence</p>
                </div>
              </div>
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
