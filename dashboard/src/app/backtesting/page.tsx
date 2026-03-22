"use client";

import { useState, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import { useBacktestResults } from "@/hooks/useApiData";
import { ArrowUpDown } from "lucide-react";
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
import mockResults from "../../../data/backtestResults.json";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface BacktestResponse {
  status: string;
  results?: {
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
      bin: string;
      bin_center: number;
      avg_predicted_confidence: number;
      actual_accuracy: number;
      count: number;
    }>;
    by_regime: Record<string, { total: number; correct: number; accuracy: number }>;
    by_horizon: Record<string, { total: number; correct: number; accuracy: number }>;
    feature_importance: Array<{ feature: string; importance: number }>;
    per_ticker: Array<{
      ticker: string;
      accuracy: number;
      sharpe: number;
      trades: number;
      win_rate: number;
      avg_return: number;
    }>;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function barFill(acc: number) {
  if (acc > 0.6) return "#00FFC2";
  if (acc < 0.5) return "#FF4B2B";
  return "#8B949E";
}

function valueColor(val: number, good: number, bad: number) {
  if (val >= good) return "text-bullish";
  if (val <= bad) return "text-bearish";
  return "text-neutral";
}

type SortKey = "ticker" | "accuracy" | "sharpe" | "trades" | "win_rate" | "avg_return";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BacktestingPage() {
  const apiData = useBacktestResults() as BacktestResponse;
  const [sortKey, setSortKey] = useState<SortKey>("accuracy");
  const [sortAsc, setSortAsc] = useState(false);

  // Use API data if it has results, otherwise fall back to mock
  const data: BacktestResponse =
    apiData.status === "ok" && apiData.results
      ? apiData
      : (mockResults as unknown as BacktestResponse);
  const hasResults = (data.status === "ok" || data.status === "complete") && data.results;

  // Sort per-ticker table
  const sortedTickers = useMemo(() => {
    const tickers = [...(data.results?.per_ticker ?? [])];
    tickers.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (typeof av === "string" && typeof bv === "string") {
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      }
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });
    return tickers;
  }, [data.results?.per_ticker, sortKey, sortAsc]);

  const bestAccuracy = useMemo(
    () => Math.max(...(data.results?.per_ticker ?? []).map((t) => t.accuracy), 0),
    [data.results?.per_ticker],
  );
  const worstAccuracy = useMemo(
    () => Math.min(...(data.results?.per_ticker ?? []).map((t) => t.accuracy), 1),
    [data.results?.per_ticker],
  );

  // Feature importance sorted
  const featureData = useMemo(
    () => [...(data.results?.feature_importance ?? [])].sort((a, b) => b.importance - a.importance),
    [data.results?.feature_importance],
  );

  // Regime chart data
  const regimeData = useMemo(() => {
    const br = data.results?.by_regime ?? {};
    return Object.entries(br).map(([r, v]) => ({
      name: r.replace(/_/g, " "),
      accuracy: +(v.accuracy * 100).toFixed(1),
    }));
  }, [data.results?.by_regime]);

  // Calibration chart data (values are 0-1 in mock, scale to 0-100)
  const calibrationData = useMemo(() => {
    return (data.results?.calibration_curve ?? []).map((pt) => ({
      x: pt.avg_predicted_confidence * 100,
      y: pt.actual_accuracy * 100,
      count: pt.count,
    }));
  }, [data.results?.calibration_curve]);

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  }

  // ---------------------------------------------------------------------------
  // No results state
  // ---------------------------------------------------------------------------

  if (!hasResults) {
    return (
      <div className="min-h-screen bg-base">
        <Header title="Backtesting" />
        <div className="md:ml-12">
          <SourceTicker />
          <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto flex items-center justify-center min-h-[60vh]">
            <div className="bg-surface border border-border rounded-card p-card-padding-lg max-w-lg w-full text-center">
              <h2 className="text-text-primary font-semibold text-lg mb-3">No backtesting results found.</h2>
              <p className="text-text-secondary text-sm mb-4">Run the backtester to generate baseline metrics:</p>
              <pre className="bg-surface-alt border border-border rounded p-4 text-left font-mono text-xs text-accent-blue overflow-x-auto">
{`python -m backtesting --start 2024-01-01 --end 2025-03-01 --tickers SPY,QQQ,AAPL`}
              </pre>
            </div>
          </main>
        </div>
      </div>
    );
  }

  const r = data.results!;

  // ---------------------------------------------------------------------------
  // Results
  // ---------------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-base">
      <Header title="Backtesting" />
      <div className="md:ml-12">
        <SourceTicker />
        <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">

          {/* ---- Summary Banner ---- */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Overall Accuracy</p>
              <p className={`font-mono text-2xl ${valueColor(r.accuracy, 0.6, 0.5)}`}>
                {(r.accuracy * 100).toFixed(1)}%
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Sharpe Ratio</p>
              <p className={`font-mono text-2xl ${valueColor(r.sharpe_ratio, 1.5, 0.5)}`}>
                {r.sharpe_ratio.toFixed(2)}
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Profit Factor</p>
              <p className={`font-mono text-2xl ${valueColor(r.profit_factor, 1.5, 1.0)}`}>
                {r.profit_factor.toFixed(2)}
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <p className="text-text-muted text-xs uppercase tracking-wide mb-1">Max Drawdown</p>
              <p className={`font-mono text-2xl ${valueColor(r.max_drawdown, -15, -5)}`}>
                {r.max_drawdown.toFixed(1)}%
              </p>
            </div>
          </div>

          {/* ---- Calibration Curve ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <h2 className="text-text-primary font-semibold text-sm mb-3">Calibration Curve</h2>
            <div className="h-72">
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
                      <Cell key={idx} r={Math.max(4, Math.min(12, entry.count / 40))} />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ---- Feature Importances ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <h2 className="text-text-primary font-semibold text-sm mb-3">Feature Importances</h2>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={featureData} layout="vertical" margin={{ top: 10, right: 20, bottom: 10, left: 120 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                  <XAxis type="number" domain={[0, "auto"]} tick={{ fill: "#8B949E", fontSize: 11 }} />
                  <YAxis type="category" dataKey="feature" tick={{ fill: "#8B949E", fontSize: 11 }} width={110} />
                  <Tooltip
                    contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 6, fontSize: 11 }}
                    formatter={(value: number) => [`${(value * 100).toFixed(1)}%`, "Importance"]}
                  />
                  <Bar dataKey="importance" fill="#58A6FF" radius={[0, 4, 4, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* ---- Accuracy by Regime ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <h2 className="text-text-primary font-semibold text-sm mb-3">Accuracy by Regime</h2>
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={regimeData} layout="vertical" margin={{ top: 10, right: 20, bottom: 10, left: 100 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                  <XAxis type="number" domain={[0, 100]} tick={{ fill: "#8B949E", fontSize: 11 }} />
                  <YAxis type="category" dataKey="name" tick={{ fill: "#8B949E", fontSize: 11 }} width={90} />
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

          {/* ---- Per-Ticker Performance Table ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <h2 className="text-text-primary font-semibold text-sm mb-3">Per-Ticker Performance</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted uppercase tracking-wide border-b border-border">
                    {(
                      [
                        ["ticker", "Ticker"],
                        ["accuracy", "Accuracy"],
                        ["sharpe", "Sharpe"],
                        ["trades", "Trades"],
                        ["win_rate", "Win Rate"],
                        ["avg_return", "Avg Return"],
                      ] as [SortKey, string][]
                    ).map(([key, label]) => (
                      <th
                        key={key}
                        className={`py-2 px-2 cursor-pointer select-none ${key === "ticker" ? "text-left" : "text-right"}`}
                        onClick={() => handleSort(key)}
                      >
                        <span className="inline-flex items-center gap-1">
                          {label}
                          <ArrowUpDown size={10} className={sortKey === key ? "text-accent-blue" : "opacity-30"} />
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedTickers.map((t, i) => {
                    const isBest = t.accuracy === bestAccuracy;
                    const isWorst = t.accuracy === worstAccuracy;
                    const rowHighlight = isBest
                      ? "border-l-2 border-l-bullish"
                      : isWorst
                        ? "border-l-2 border-l-bearish"
                        : "";
                    return (
                      <tr
                        key={t.ticker}
                        className={`border-b border-border/50 ${i % 2 === 0 ? "bg-surface" : "bg-surface-alt"} ${rowHighlight}`}
                      >
                        <td className="py-2 px-2 font-mono text-text-primary font-medium">{t.ticker}</td>
                        <td className={`py-2 px-2 text-right font-mono ${valueColor(t.accuracy, 0.65, 0.55)}`}>
                          {(t.accuracy * 100).toFixed(1)}%
                        </td>
                        <td className={`py-2 px-2 text-right font-mono ${valueColor(t.sharpe, 1.5, 1.0)}`}>
                          {t.sharpe.toFixed(2)}
                        </td>
                        <td className="py-2 px-2 text-right font-mono text-text-secondary">{t.trades}</td>
                        <td className={`py-2 px-2 text-right font-mono ${valueColor(t.win_rate, 0.62, 0.55)}`}>
                          {(t.win_rate * 100).toFixed(1)}%
                        </td>
                        <td className={`py-2 px-2 text-right font-mono ${valueColor(t.avg_return, 0.15, 0.05)}`}>
                          {t.avg_return.toFixed(2)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
