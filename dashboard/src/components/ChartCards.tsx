"use client";

import { useState, useRef, useEffect } from "react";
import { MoreHorizontal, X } from "lucide-react";
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
  description?: string;
}

interface TimelineData {
  date: string;
  sentiment: number;
  sp500: number;
}

interface PopupState {
  visible: boolean;
  x: number;
  y: number;
  name: string;
  score: number;
  color: string;
  description: string;
}

const DESCRIPTIONS: Record<string, string> = {
  Technology: "Tech sector sentiment driven by AI adoption and earnings momentum.",
  Healthcare: "Healthcare sentiment reflecting pharma pipeline developments.",
  Energy: "Energy sector tracking commodity prices and renewable transition.",
  Finance: "Financial sector gauging rate sensitivity and credit conditions.",
  "Consumer Discretionary": "Consumer spending signals from retail and e-commerce data.",
  "Real Estate": "Real estate sentiment tracking mortgage rates and housing demand.",
  "Supply Chain": "Supply chain risk from logistics disruptions and inventory shifts.",
  "Geopolitical": "Geopolitical risk from trade tensions and regional instability.",
  "Credit Risk": "Credit risk indicators from spreads and default rate trends.",
  "Inflation": "Inflation expectations from consumer surveys and commodity prices.",
  "Regulatory": "Regulatory risk from upcoming policy and legislation changes.",
  "Interest Rates": "Interest rate sentiment from Fed guidance and bond markets.",
  "Employment": "Employment sentiment from jobs data and labor market indicators.",
  "Consumer Confidence": "Consumer confidence from surveys and spending patterns.",
  "Trade Balance": "Trade balance sentiment from import/export and tariff data.",
  "GDP Growth": "GDP growth outlook from leading economic indicators.",
};

function getDescription(name: string): string {
  return DESCRIPTIONS[name] || `Sentiment analysis for ${name} based on multi-source OSINT data.`;
}

function BarPopup({ popup, onClose }: { popup: PopupState; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  if (!popup.visible) return null;

  return (
    <div
      ref={ref}
      className="absolute z-50 w-56 bg-surface-alt border border-border rounded-lg p-3 shadow-lg"
      style={{ top: popup.y, left: popup.x }}
    >
      <div className="flex items-start justify-between mb-1.5">
        <h4 className="text-xs font-semibold text-text-primary">{popup.name}</h4>
        <button onClick={onClose} className="text-text-muted hover:text-text-primary p-0.5 -mt-0.5 -mr-0.5">
          <X size={11} />
        </button>
      </div>
      <div className="flex items-center gap-2 mb-1.5">
        <div
          className="w-3 h-3 rounded-sm"
          style={{ backgroundColor: popup.color }}
        />
        <span className="text-sm font-mono font-semibold text-text-primary">
          {popup.score.toFixed(1)}
        </span>
        <span className="text-[10px] text-text-muted">/ 100</span>
      </div>
      <p className="text-[10px] text-text-muted leading-relaxed">
        {popup.description}
      </p>
    </div>
  );
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
  const [popup, setPopup] = useState<PopupState>({
    visible: false, x: 0, y: 0, name: "", score: 0, color: "", description: "",
  });
  const containerRef = useRef<HTMLDivElement>(null);

  const handleBarClick = (entry: BarData, e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = Math.min(e.clientX - rect.left, rect.width - 230);
    const y = Math.min(e.clientY - rect.top + 10, rect.height - 120);
    setPopup({
      visible: true,
      x: Math.max(0, x),
      y: Math.max(0, y),
      name: entry.name,
      score: entry.score,
      color,
      description: getDescription(entry.name),
    });
  };

  return (
    <div ref={containerRef} className="card flex flex-col h-full relative">
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
            onClick={(state) => {
              if (state && state.activePayload && state.activePayload[0]) {
                const entry = state.activePayload[0].payload as BarData;
                // Use a synthetic event position from the chart area
                const rect = containerRef.current?.getBoundingClientRect();
                if (rect) {
                  setPopup({
                    visible: true,
                    x: Math.min(state.chartX || 60, rect.width - 230),
                    y: Math.min((state.chartY || 0) + 10, rect.height - 120),
                    name: entry.name,
                    score: entry.score,
                    color,
                    description: getDescription(entry.name),
                  });
                }
              }
            }}
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
            <Bar dataKey="score" fill={color} radius={[0, 3, 3, 0]} barSize={12} className="cursor-pointer" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <BarPopup popup={popup} onClose={() => setPopup((p) => ({ ...p, visible: false }))} />
    </div>
  );
}

function EconomicSentimentCard({ data }: { data: BarData[] }) {
  const [popup, setPopup] = useState<PopupState>({
    visible: false, x: 0, y: 0, name: "", score: 0, color: "", description: "",
  });
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div ref={containerRef} className="card flex flex-col h-full relative">
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
            onClick={(state) => {
              if (state && state.activePayload && state.activePayload[0]) {
                const entry = state.activePayload[0].payload as BarData;
                const barColor = entry.direction === "bullish" ? "#00FFC2" : "#FF4B2B";
                const rect = containerRef.current?.getBoundingClientRect();
                if (rect) {
                  setPopup({
                    visible: true,
                    x: Math.min(state.chartX || 80, rect.width - 230),
                    y: Math.min((state.chartY || 0) + 10, rect.height - 120),
                    name: entry.name,
                    score: entry.score,
                    color: barColor,
                    description: getDescription(entry.name),
                  });
                }
              }
            }}
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
              className="cursor-pointer"
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
                    className="cursor-pointer"
                  />
                );
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <BarPopup popup={popup} onClose={() => setPopup((p) => ({ ...p, visible: false }))} />
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
