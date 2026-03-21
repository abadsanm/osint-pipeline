"use client";

import { MoreHorizontal } from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  ResponsiveContainer,
  LineChart,
  Line,
  Tooltip,
} from "recharts";

interface BarData {
  name: string;
  score: number;
  direction?: "bullish" | "bearish";
}

interface TimelineData {
  date: string;
  sentiment: number;
  sp500: number;
}

function HorizontalBarCard({
  title,
  data,
  color,
}: {
  title: string;
  data: BarData[];
  color: string;
}) {
  return (
    <div className="card flex flex-col h-full">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          {title}
        </h3>
        <button className="text-text-muted hover:text-text-primary p-0.5"><MoreHorizontal size={14} /></button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 12, bottom: 0, left: 60 }}
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
              tick={{ fontSize: 11, fill: "#8B949E" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fontSize: 11, fill: "#8B949E" }}
              axisLine={false}
              tickLine={false}
              width={55}
            />
            <Bar dataKey="score" fill={color} radius={[0, 3, 3, 0]} barSize={12} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function EconomicSentimentCard({ data }: { data: BarData[] }) {
  return (
    <div className="card flex flex-col h-full">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Economic Sentiments
        </h3>
        <button className="text-text-muted hover:text-text-primary p-0.5"><MoreHorizontal size={14} /></button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 12, bottom: 0, left: 80 }}
          >
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="#8B949E"
              strokeOpacity={0.1}
              horizontal={false}
            />
            <XAxis
              type="number"
              domain={[0, 80]}
              tick={{ fontSize: 11, fill: "#8B949E" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fontSize: 11, fill: "#8B949E" }}
              axisLine={false}
              tickLine={false}
              width={75}
            />
            <Bar
              dataKey="score"
              radius={[0, 3, 3, 0]}
              barSize={12}
              fill="#8B949E"
              shape={(props: any) => {
                const fill =
                  props.payload?.direction === "bullish"
                    ? "#00FFC2"
                    : "#FF4B2B";
                return (
                  <rect
                    x={props.x}
                    y={props.y + (props.height - 12) / 2}
                    width={props.width}
                    height={12}
                    fill={fill}
                    rx={3}
                    ry={3}
                  />
                );
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function MacroSentimentCard({ data }: { data: TimelineData[] }) {
  return (
    <div className="card flex flex-col h-full">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Macro Sentiment vs. S&P 500
        </h3>
        <button className="text-text-muted hover:text-text-primary p-0.5"><MoreHorizontal size={14} /></button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={data}
            margin={{ top: 5, right: 12, bottom: 0, left: 0 }}
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
              yAxisId="sentiment"
              domain={[40, 100]}
              tick={{ fontSize: 11, fill: "#00FFC2" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              yAxisId="sp500"
              orientation="right"
              domain={[280, 650]}
              tick={{ fontSize: 11, fill: "#58A6FF" }}
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
            <Line
              yAxisId="sentiment"
              type="monotone"
              dataKey="sentiment"
              stroke="#00FFC2"
              strokeWidth={2}
              dot={false}
              name="Sentiment"
            />
            <Line
              yAxisId="sp500"
              type="monotone"
              dataKey="sp500"
              stroke="#58A6FF"
              strokeWidth={2}
              dot={false}
              name="S&P 500"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// Named exports for individual use
const ChartCards = {
  Macro: MacroSentimentCard,
  HBar: HorizontalBarCard,
  Economic: EconomicSentimentCard,
};

export default ChartCards;
