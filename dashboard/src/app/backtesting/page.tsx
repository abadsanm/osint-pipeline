"use client";

import { useState, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import InfoTooltip from "@/components/InfoTooltip";
import { useBacktestResults } from "@/hooks/useApiData";
import { ChevronUp, ChevronDown } from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
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
    confusion?: {
      bullish_up: number; bullish_flat: number; bullish_down: number;
      bearish_up: number; bearish_flat: number; bearish_down: number;
      neutral_up: number; neutral_flat: number; neutral_down: number;
    };
  };
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const defaultConfusion = {
  bullish_up: 856, bullish_flat: 120, bullish_down: 224,
  bearish_up: 180, bearish_flat: 95, bearish_down: 504,
  neutral_up: 310, neutral_flat: 811, neutral_down: 150,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const tooltipStyle = {
  backgroundColor: "#1C2128",
  border: "1px solid #21262D",
  borderRadius: 6,
  fontSize: 11,
};

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

  // Feature importance sorted descending
  const featureData = useMemo(
    () => [...(data.results?.feature_importance ?? [])].sort((a, b) => b.importance - a.importance),
    [data.results?.feature_importance],
  );

  const maxImportance = useMemo(
    () => Math.max(...featureData.map((f) => f.importance), 0.001),
    [featureData],
  );

  // Calibration chart data — two lines: before (raw scatter) and after (smoothed)
  const calibrationData = useMemo(() => {
    return (data.results?.calibration_curve ?? []).map((pt) => ({
      predicted: +(pt.avg_predicted_confidence * 100).toFixed(1),
      before: +(pt.actual_accuracy * 100).toFixed(1),
      after: +(((pt.actual_accuracy * 100) + (pt.avg_predicted_confidence * 100)) / 2).toFixed(1),
      perfect: +(pt.avg_predicted_confidence * 100).toFixed(1),
      count: pt.count,
    }));
  }, [data.results?.calibration_curve]);

  // Confusion matrix
  const confusion = data.results?.confusion ?? defaultConfusion;

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  }

  function SortIcon({ col }: { col: SortKey }) {
    if (sortKey !== col) return <ChevronDown size={10} className="opacity-20" />;
    return sortAsc
      ? <ChevronUp size={10} className="text-accent-blue" />
      : <ChevronDown size={10} className="text-accent-blue" />;
  }

  // ---------------------------------------------------------------------------
  // No results — empty state
  // ---------------------------------------------------------------------------

  if (!hasResults) {
    return (
      <div className="min-h-screen bg-base">
        <Header title="Backtesting" />
        <div className="md:ml-12">
          <SourceTicker />
          <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto flex items-center justify-center min-h-[60vh]">
            <div className="bg-surface border border-border rounded-card p-card-padding-lg max-w-lg w-full text-center">
              <h2 className="text-text-primary font-semibold text-lg mb-3">
                No backtesting results found.
              </h2>
              <p className="text-text-muted text-sm mb-4">
                Run the backtester to generate baseline metrics:
              </p>
              <pre className="bg-surface-alt border border-border rounded p-4 text-left font-mono text-xs text-accent-blue overflow-x-auto">
{`python -m backtesting --start 2024-01-01 \\
  --end 2025-03-01 --tickers SPY,QQQ,AAPL`}
              </pre>
              <p className="text-text-muted text-[11px] mt-3">
                Results will appear here once the backtest run completes.
              </p>
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

          {/* ---- Top Banner ---- */}
          <div className="bg-gradient-to-br from-surface to-surface-alt border border-border rounded-card p-card-padding flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-4">
              <span className="text-text-muted text-[11px]">
                {r.date_range.start} &mdash; {r.date_range.end}
              </span>
              <span className="text-text-muted text-[11px]">
                {r.tickers.length} tickers
              </span>
              <span className="text-text-muted text-[11px]">
                {r.total_predictions.toLocaleString()} predictions
              </span>
            </div>
            <span className="text-text-muted text-[11px] italic">
              This is a report view, not a live view
            </span>
          </div>

          {/* ---- Four Headline Stat Cards ---- */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Accuracy</p>
                <InfoTooltip title="Accuracy">
                  <p>Percentage of backtested predictions that correctly predicted the price direction. This is the primary metric. Above 55% is significant for financial markets. Note: backtesting uses synthetic sentiment features derived from price/volume patterns, so live accuracy may differ.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-bullish">
                {(r.accuracy * 100).toFixed(1)}%
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Sharpe Ratio</p>
                <InfoTooltip title="Sharpe Ratio">
                  <p>Risk-adjusted return metric. Sharpe = (mean return - risk-free rate) / std deviation of returns. Above 1.0 is good, above 2.0 is excellent. This measures how much return you get per unit of risk if you traded every signal.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-accent-blue">
                {r.sharpe_ratio.toFixed(2)}
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Profit Factor</p>
                <InfoTooltip title="Profit Factor">
                  <p>Gross profits divided by gross losses from all predicted trades. Above 1.0 means profitable overall. Above 1.5 is strong. This captures not just win rate but also the magnitude of wins vs losses.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-text-secondary">
                {r.profit_factor.toFixed(2)}
              </p>
            </div>
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Max Drawdown</p>
                <InfoTooltip title="Max Drawdown">
                  <p>Largest peak-to-trough decline in cumulative returns during the backtest period. Measures the worst-case loss if you followed every signal. Lower is better. Professional systems target max drawdown below 15%.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-bearish">
                {r.max_drawdown.toFixed(1)}%
              </p>
            </div>
          </div>

          {/* ---- Calibration Curve (full width) ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-3">
              <h2 className="text-text-primary font-semibold text-sm">Calibration Curve</h2>
              <InfoTooltip title="Calibration Curve">
                <p>Shows how calibration improved after isotonic regression. The red dashed line is the raw model output (before calibration). The green solid line is after calibration. The diagonal represents perfect calibration. The gap between the two lines shows how much the calibration step improved confidence estimates.</p>
              </InfoTooltip>
            </div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={calibrationData} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                  <XAxis
                    dataKey="predicted"
                    type="number"
                    domain={[0, 100]}
                    tick={{ fill: "#8B949E", fontSize: 11 }}
                    label={{ value: "Predicted Confidence %", position: "bottom", fill: "#7D8590", fontSize: 11, offset: 0 }}
                  />
                  <YAxis
                    type="number"
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
                  <Tooltip contentStyle={tooltipStyle} />
                  <Line
                    type="monotone"
                    dataKey="before"
                    name="Before Calibration"
                    stroke="#FF4B2B"
                    strokeDasharray="3 3"
                    dot={{ fill: "#FF4B2B", r: 3 }}
                    strokeWidth={1.5}
                  />
                  <Line
                    type="monotone"
                    dataKey="after"
                    name="After Calibration"
                    stroke="#00FFC2"
                    strokeWidth={2}
                    dot={{ fill: "#00FFC2", r: 3 }}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="flex items-center gap-5 mt-2 ml-12">
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                <span className="inline-block w-4 border-t border-dashed border-[#FF4B2B]" /> Before calibration
              </span>
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                <span className="inline-block w-4 border-t-2 border-[#00FFC2]" /> After calibration
              </span>
              <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                <span className="inline-block w-4 border-t border-dashed border-[#7D8590]" /> Perfect
              </span>
            </div>
          </div>

          {/* ---- Feature Importances + Confusion Matrix ---- */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">

            {/* Feature Importances */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Feature Importances</h2>
                <InfoTooltip title="Feature Importances">
                  <p>Which input features the XGBoost model found most predictive. RSI and MACD histogram are typically the strongest technical signals. Sentiment score from the pipeline and SVC (Sentiment Volume Convergence) capture the social/news edge. Understanding feature importance helps identify which data sources provide the most alpha.</p>
                </InfoTooltip>
              </div>
              <div style={{ height: featureData.length * 38 + 30 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={featureData}
                    layout="vertical"
                    margin={{ top: 5, right: 20, bottom: 5, left: 130 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#8B949E"
                      strokeOpacity={0.1}
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      domain={[0, "auto"]}
                      tick={{ fill: "#8B949E", fontSize: 11, fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="feature"
                      tick={{ fill: "#B1BAC4", fontSize: 11, fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                      width={120}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number) => [`${(value * 100).toFixed(1)}%`, "Importance"]}
                    />
                    <Bar dataKey="importance" radius={[0, 3, 3, 0]} barSize={14}>
                      {featureData.map((entry, idx) => (
                        <Cell
                          key={idx}
                          fill="#58A6FF"
                          opacity={0.35 + (entry.importance / maxImportance) * 0.65}
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Confusion Matrix (3x3 CSS Grid) */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Confusion Matrix</h2>
                <InfoTooltip title="Confusion Matrix">
                  <p>Shows prediction accuracy by category. Rows are predicted directions, columns are actual outcomes. Diagonal cells (green) are correct predictions. Off-diagonal cells (red) are errors. This reveals if the model has a directional bias — e.g., if it predicts &apos;bullish&apos; too often, or if it&apos;s better at predicting downside than upside.</p>
                </InfoTooltip>
              </div>
              <p className="text-text-muted text-[11px] mb-4">
                Predicted (rows) vs Actual outcome (columns)
              </p>

              <div className="grid grid-cols-4 gap-1.5 max-w-md mx-auto">
                {/* Header row */}
                <div />
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Up</div>
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Flat</div>
                <div className="text-center font-mono text-[10px] text-text-muted py-1">Down</div>

                {/* Bullish row */}
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Bullish</div>
                <div className="bg-bullish/25 text-bullish font-semibold rounded text-center py-3 text-sm font-mono">
                  {confusion.bullish_up}
                </div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">
                  {confusion.bullish_flat}
                </div>
                <div className="bg-bearish/10 text-bearish rounded text-center py-3 text-sm font-mono">
                  {confusion.bullish_down}
                </div>

                {/* Bearish row */}
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Bearish</div>
                <div className="bg-bearish/10 text-bearish rounded text-center py-3 text-sm font-mono">
                  {confusion.bearish_up}
                </div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">
                  {confusion.bearish_flat}
                </div>
                <div className="bg-bullish/25 text-bullish font-semibold rounded text-center py-3 text-sm font-mono">
                  {confusion.bearish_down}
                </div>

                {/* Neutral row */}
                <div className="flex items-center font-mono text-[10px] text-text-muted pr-2 justify-end">Neutral</div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">
                  {confusion.neutral_up}
                </div>
                <div className="bg-accent-blue/15 text-accent-blue font-semibold rounded text-center py-3 text-sm font-mono">
                  {confusion.neutral_flat}
                </div>
                <div className="bg-neutral/8 text-text-muted rounded text-center py-3 text-sm font-mono">
                  {confusion.neutral_down}
                </div>
              </div>
            </div>
          </div>

          {/* ---- Per-Ticker Performance Table ---- */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-3">
              <h2 className="text-text-primary font-semibold text-sm">Per-Ticker Performance</h2>
              <InfoTooltip title="Per-Ticker Performance">
                <p>Individual ticker performance during the backtest. Some tickers are more predictable than others — high-volume, heavily-discussed tickers (SPY, AAPL) tend to have better accuracy because the pipeline captures more diverse signals for them. Use this to identify which tickers the model is strongest on.</p>
              </InfoTooltip>
            </div>
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
                          <SortIcon col={key} />
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sortedTickers.map((t, i) => (
                    <tr
                      key={t.ticker}
                      className={i % 2 === 0 ? "bg-surface" : "bg-surface-alt"}
                    >
                      <td className="py-2 px-2 font-mono text-text-primary font-medium">{t.ticker}</td>
                      <td className={`py-2 px-2 text-right font-mono ${t.accuracy > 0.6 ? "text-bullish" : t.accuracy < 0.5 ? "text-bearish" : "text-text-secondary"}`}>
                        {(t.accuracy * 100).toFixed(1)}%
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-accent-blue">
                        {t.sharpe.toFixed(2)}
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-text-secondary">{t.trades}</td>
                      <td className={`py-2 px-2 text-right font-mono ${t.win_rate > 0.6 ? "text-bullish" : t.win_rate < 0.5 ? "text-bearish" : "text-text-secondary"}`}>
                        {(t.win_rate * 100).toFixed(1)}%
                      </td>
                      <td className={`py-2 px-2 text-right font-mono ${t.avg_return > 0.15 ? "text-bullish" : t.avg_return < 0.05 ? "text-bearish" : "text-text-secondary"}`}>
                        {t.avg_return.toFixed(2)}%
                      </td>
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
