"use client";

import { useState, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import { usePredictions, usePredictionStats } from "@/hooks/useApiData";
import { CheckCircle, XCircle, Minus } from "lucide-react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
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

// Normalized shape used by table
interface TableRow {
  id: string;
  ticker: string;
  direction: "bullish" | "bearish" | "neutral";
  confidence: number;
  horizon: string;
  regime: string;
  agreement: number;
  score: number;
  timestamp: string;
  outcome: "correct" | "incorrect" | "pending";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeRow(p: MockPrediction): TableRow {
  const agreeing = p.contributing_signals.filter((s) => s.signal_type === p.direction).length;
  return {
    id: p.prediction_id,
    ticker: p.ticker,
    direction: p.direction,
    confidence: p.confidence,
    horizon: `${p.time_horizon_hours}h`,
    regime: p.regime.replace(/_/g, " "),
    agreement: agreeing,
    score: Math.round(Math.abs(p.raw_score) * 100),
    timestamp: p.created_at,
    outcome: p.outcome ?? "pending",
  };
}

function directionColor(d: string) {
  if (d === "bullish") return "bg-bullish/20 text-bullish";
  if (d === "bearish") return "bg-bearish/20 text-bearish";
  return "bg-neutral/20 text-neutral";
}

function accuracyColor(acc: number) {
  if (acc > 0.6) return "text-bullish";
  if (acc < 0.5) return "text-bearish";
  return "text-neutral";
}

function barFill(acc: number) {
  if (acc > 0.6) return "#00FFC2";
  if (acc < 0.5) return "#FF4B2B";
  return "#8B949E";
}

function formatTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PredictionsPage() {
  const [tickerFilter, setTickerFilter] = useState("");
  const apiPredictions = usePredictions(50, tickerFilter || undefined) as MockPrediction[];
  const stats = usePredictionStats();

  // Use API data if available, fall back to mock
  const rawPredictions: MockPrediction[] =
    Array.isArray(apiPredictions) && apiPredictions.length > 0
      ? apiPredictions
      : (mockPredictions as unknown as MockPrediction[]);

  const rows: TableRow[] = useMemo(() => rawPredictions.map(normalizeRow), [rawPredictions]);

  const filteredRows = tickerFilter
    ? rows.filter((r) => r.ticker.toLowerCase().includes(tickerFilter.toLowerCase()))
    : rows;

  // Calibration error
  const calibrationError = useMemo(() => {
    const curve = stats.calibration_curve;
    if (!curve || curve.length === 0) return null;
    const totalError = curve.reduce(
      (sum, pt) => sum + Math.abs(pt.avg_predicted_confidence - pt.actual_accuracy),
      0,
    );
    return (totalError / curve.length) * 100;
  }, [stats.calibration_curve]);

  // Rolling accuracy trend
  const rollingTrend =
    stats.rolling_accuracy_200 > stats.overall_accuracy
      ? "up"
      : stats.rolling_accuracy_200 < stats.overall_accuracy
        ? "down"
        : "flat";

  // By horizon chart data
  const horizonData = useMemo(() => {
    const bh = stats.by_horizon ?? {};
    return Object.entries(bh).map(([h, v]) => ({
      name: `${h}h`,
      accuracy: +(v.accuracy * 100).toFixed(1),
    }));
  }, [stats.by_horizon]);

  // By regime chart data
  const regimeData = useMemo(() => {
    const br = stats.by_regime ?? {};
    return Object.entries(br).map(([r, v]) => ({
      name: r.replace(/_/g, " "),
      accuracy: +(v.accuracy * 100).toFixed(1),
    }));
  }, [stats.by_regime]);

  // Model agreement data
  const agreementData = useMemo(() => {
    const ma = stats.model_agreement ?? {};
    return [
      { name: "4/4 agree", pct: ma["4_agree_pct"] ?? 0 },
      { name: "3/4 agree", pct: ma["3_agree_pct"] ?? 0 },
      { name: "2/4 agree", pct: ma["2_agree_pct"] ?? 0 },
    ];
  }, [stats.model_agreement]);

  // Calibration chart data
  const calibrationData = useMemo(() => {
    return (stats.calibration_curve ?? []).map((pt) => ({
      x: pt.avg_predicted_confidence * 100,
      y: pt.actual_accuracy * 100,
      count: pt.count,
    }));
  }, [stats.calibration_curve]);

  return (
    <div className="min-h-screen bg-base">
      <Header title="Predictions" />
      <div className="md:ml-12">
        <SourceTicker />
        <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">

          {/* ---- Stat Cards ---- */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Total Predictions</p>
              <p className="font-mono text-2xl text-text-primary">
                {stats.total_predictions || filteredRows.length}
              </p>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Overall Accuracy</p>
              <p className={`font-mono text-2xl ${accuracyColor(stats.overall_accuracy)}`}>
                {stats.overall_accuracy ? `${(stats.overall_accuracy * 100).toFixed(1)}%` : "--"}
              </p>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Rolling Accuracy (200)</p>
              <div className="flex items-baseline gap-2">
                <p className={`font-mono text-2xl ${accuracyColor(stats.rolling_accuracy_200)}`}>
                  {stats.rolling_accuracy_200 ? `${(stats.rolling_accuracy_200 * 100).toFixed(1)}%` : "--"}
                </p>
                {rollingTrend === "up" && <span className="text-bullish text-xs">&#9650;</span>}
                {rollingTrend === "down" && <span className="text-bearish text-xs">&#9660;</span>}
              </div>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Calibration Error</p>
              <p className="font-mono text-2xl text-text-primary">
                {calibrationError !== null ? `${calibrationError.toFixed(1)}%` : "--"}
              </p>
            </div>
          </div>

          {/* ---- Predictions Table ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-text-primary font-semibold text-sm">Predictions</h2>
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
                      className={`border-b border-border/50 ${i % 2 === 0 ? "bg-surface" : "bg-surface-alt"}`}
                    >
                      <td className="py-2 px-2 font-mono text-text-primary font-medium">{p.ticker}</td>
                      <td className="py-2 px-2">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-medium ${directionColor(p.direction)}`}>
                          {p.direction}
                        </span>
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-text-secondary">
                        {(p.confidence * 100).toFixed(0)}%
                      </td>
                      <td className="py-2 px-2 font-mono text-text-secondary">{p.horizon}</td>
                      <td className="py-2 px-2 text-text-secondary capitalize">{p.regime}</td>
                      <td className="py-2 px-2 text-center font-mono text-text-secondary">{p.agreement}/4</td>
                      <td className="py-2 px-2 text-right font-mono text-text-primary">{p.score}</td>
                      <td className="py-2 px-2 text-text-muted">{formatTime(p.timestamp)}</td>
                      <td className="py-2 px-2 text-center">
                        {p.outcome === "correct" && <CheckCircle size={14} className="inline text-bullish" />}
                        {p.outcome === "incorrect" && <XCircle size={14} className="inline text-bearish" />}
                        {p.outcome === "pending" && <Minus size={14} className="inline text-text-muted" />}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ---- Calibration Curve + Accuracy by Horizon ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <h2 className="text-text-primary font-semibold text-sm mb-3">Calibration Curve</h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis
                      type="number"
                      dataKey="x"
                      domain={[0, 100]}
                      tick={{ fill: "#8B949E", fontSize: 11 }}
                      label={{ value: "Predicted Confidence %", position: "bottom", fill: "#7D8590", fontSize: 11, offset: 0 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="y"
                      domain={[0, 100]}
                      tick={{ fill: "#8B949E", fontSize: 11 }}
                      label={{ value: "Actual Accuracy %", angle: -90, position: "insideLeft", fill: "#7D8590", fontSize: 11 }}
                    />
                    <ReferenceLine
                      segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
                      stroke="#7D8590"
                      strokeDasharray="4 4"
                      strokeOpacity={0.6}
                    />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 6, fontSize: 11 }}
                      formatter={(value: number, name: string) => {
                        if (name === "y") return [`${value.toFixed(1)}%`, "Actual Accuracy"];
                        return [`${value}`, name];
                      }}
                    />
                    <Scatter data={calibrationData} fill="#58A6FF">
                      {calibrationData.map((entry, idx) => (
                        <Cell key={idx} r={Math.max(4, Math.min(12, entry.count / 20))} />
                      ))}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <h2 className="text-text-primary font-semibold text-sm mb-3">Accuracy by Horizon</h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={horizonData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis dataKey="name" tick={{ fill: "#8B949E", fontSize: 11 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 6, fontSize: 11 }}
                      formatter={(value: number) => [`${value}%`, "Accuracy"]}
                    />
                    <Bar dataKey="accuracy" radius={[4, 4, 0, 0]}>
                      {horizonData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy / 100)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* ---- Accuracy by Regime + Model Agreement ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <h2 className="text-text-primary font-semibold text-sm mb-3">Accuracy by Regime</h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={regimeData} layout="vertical" margin={{ top: 10, right: 20, bottom: 10, left: 80 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis type="number" domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }} />
                    <YAxis type="category" dataKey="name" tick={{ fill: "#8B949E", fontSize: 11 }} width={70} />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 6, fontSize: 11 }}
                      formatter={(value: number) => [`${value}%`, "Accuracy"]}
                    />
                    <Bar dataKey="accuracy" radius={[0, 4, 4, 0]}>
                      {regimeData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy / 100)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <h2 className="text-text-primary font-semibold text-sm mb-3">Model Agreement</h2>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={agreementData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis dataKey="name" tick={{ fill: "#8B949E", fontSize: 11 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }} />
                    <Tooltip
                      contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 6, fontSize: 11 }}
                      formatter={(value: number) => [`${value}%`, "Percentage"]}
                    />
                    <Bar dataKey="pct" fill="#58A6FF" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
