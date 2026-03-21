"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";

import stockData from "../../../../data/stockHistory.json";

export default function FinancialAlphaPage() {
  const params = useParams();
  const ticker = (params.ticker as string) || "TSLA";

  // Merge candles + sentiment + predictions into chart data
  const chartData = stockData.candles.map((c) => {
    const sent = stockData.sentimentTimeline.find((s) => s.date === c.date);
    return {
      date: c.date.slice(5), // MM-DD
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
      sentiment: sent ? sent.score * 100 : null,
    };
  });

  // Add predictions
  const predictionData = stockData.predictions.map((p) => ({
    date: p.date.slice(5),
    predicted: p.predicted,
    confLow: p.confidenceLow,
    confHigh: p.confidenceHigh,
    confRange: [p.confidenceLow, p.confidenceHigh],
  }));

  const allData = [
    ...chartData,
    ...predictionData.map((p) => ({
      ...p,
      close: undefined,
      sentiment: undefined,
    })),
  ];

  const change = stockData.change24h;
  const sentScore = Math.round(stockData.sentimentScore * 100);

  return (
    <div className="flex flex-col h-screen">
      <Header title="Financial Alpha" />
      <SourceTicker />

      <div className="flex-1 p-module-gap-lg overflow-auto">
        {/* Ticker header */}
        <div className="card-lg mb-module-gap-lg flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="text-text-muted hover:text-accent-blue text-sm"
            >
              &larr; Back
            </Link>
            <div>
              <h2 className="text-xl font-semibold">
                <span className="font-mono">{ticker}</span>
                <span className="text-text-secondary font-normal ml-2">
                  {stockData.company}
                </span>
              </h2>
            </div>
          </div>
          <div className="flex items-center gap-6">
            <div className="text-right">
              <p className="font-mono text-lg font-medium">
                ${stockData.currentPrice.toFixed(2)}
              </p>
              <p
                className={`font-mono text-sm ${change >= 0 ? "text-bullish" : "text-bearish"}`}
              >
                {change >= 0 ? "+" : ""}
                {change}%
              </p>
            </div>
            <div
              className={`px-3 py-1 rounded-md text-sm font-mono font-medium ${
                sentScore >= 50
                  ? "bg-bullish-muted text-bullish"
                  : "bg-bearish-muted text-bearish"
              }`}
            >
              Sent: {sentScore}%
            </div>
          </div>
        </div>

        {/* Main chart + News panel */}
        <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-module-gap-lg">
          {/* Dual-axis chart */}
          <div className="card-lg min-h-[500px]">
            <h3 className="text-sm font-semibold text-text-secondary mb-3">
              Price + Sentiment Overlay
            </h3>
            <ResponsiveContainer width="100%" height={450}>
              <ComposedChart
                data={allData as any}
                margin={{ top: 10, right: 10, bottom: 0, left: 0 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="#8B949E"
                  strokeOpacity={0.1}
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "#8B949E" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  yAxisId="price"
                  domain={[210, 300]}
                  tick={{ fontSize: 11, fill: "#E6EDF3" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  yAxisId="sentiment"
                  orientation="right"
                  domain={[50, 100]}
                  tick={{ fontSize: 11, fill: "#00FFC2" }}
                  axisLine={false}
                  tickLine={false}
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
                {/* Sentiment area fill */}
                <Area
                  yAxisId="sentiment"
                  type="monotone"
                  dataKey="sentiment"
                  fill="#00FFC233"
                  stroke="#00FFC2"
                  strokeWidth={1}
                  name="Sentiment %"
                />
                {/* Price as line (simplified from candlestick) */}
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="close"
                  stroke="#E6EDF3"
                  strokeWidth={2}
                  dot={{ r: 2, fill: "#E6EDF3" }}
                  name="Close Price"
                  connectNulls={false}
                />
                {/* Prediction ghost */}
                <Line
                  yAxisId="price"
                  type="monotone"
                  dataKey="predicted"
                  stroke="#58A6FF"
                  strokeWidth={2}
                  strokeDasharray="6 4"
                  dot={false}
                  name="Predicted"
                  connectNulls={false}
                />
                {/* Confidence band */}
                <Area
                  yAxisId="price"
                  type="monotone"
                  dataKey="confHigh"
                  fill="#58A6FF"
                  fillOpacity={0.08}
                  stroke="none"
                  name="Conf High"
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {/* News insights panel */}
          <div className="card-lg">
            <h3 className="text-sm font-semibold text-text-secondary mb-3">
              News Insights
            </h3>
            <div className="space-y-3">
              {stockData.newsInsights.map((n) => (
                <div
                  key={n.id}
                  className="p-3 rounded-md bg-surface-alt border border-border"
                >
                  <p className="text-sm text-text-primary leading-snug">
                    {n.headline}
                  </p>
                  <p className="text-xs text-text-muted mt-1.5">
                    {n.source} &middot;{" "}
                    {new Date(n.timestamp).toLocaleDateString("en-US", {
                      month: "short",
                      day: "numeric",
                    })}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
