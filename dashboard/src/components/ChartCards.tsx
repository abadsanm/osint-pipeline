"use client";

import { useState, useRef, useEffect } from "react";
import { MoreHorizontal, X, Brain, Info } from "lucide-react";
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
  Legend,
} from "recharts";
import AnalysisModal from "./AnalysisModal";

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
  vix?: number;
  treasury10y?: number;
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
  Tech: "Tech sector sentiment driven by AI adoption, cloud earnings, and semiconductor demand.",
  TSLA: "Tesla sentiment from EV market dynamics, production numbers, and CEO activity.",
  NVDA: "NVIDIA sentiment from AI chip demand, data center growth, and GPU market.",
  Economy: "Broad economic sentiment from employment, inflation, and growth indicators.",
  Retail: "Retail sector reflecting consumer spending, e-commerce trends, and margin pressure.",
  Energy: "Energy sector tracking oil prices, OPEC decisions, and renewable transition.",
  AMZN: "Amazon sentiment from AWS growth, retail margins, and logistics expansion.",
  Regulation: "Regulatory risk from antitrust actions, tech legislation, and compliance changes.",
  Scandal: "Corporate scandal risk from fraud, executive misconduct, and governance failures.",
  "Interest Rates": "Fed policy and bond market expectations driving borrowing costs and valuations.",
  Employment: "Jobs data, wage growth, and labor participation signaling economic health.",
  Inflation: "CPI, PPI, and consumer expectations indicating purchasing power erosion.",
  "GDP Growth": "GDP trajectory from manufacturing, services, and consumer spending data.",
  Housing: "Housing market from mortgage rates, starts, and price indices.",
};

function getDescription(name: string): string {
  return DESCRIPTIONS[name] || `Sentiment analysis for ${name} based on multi-source OSINT data.`;
}

function BarPopup({
  popup,
  onClose,
  onAnalyze,
}: {
  popup: PopupState;
  onClose: () => void;
  onAnalyze: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  if (!popup.visible) return null;

  return (
    <div
      ref={ref}
      className="absolute z-50 w-60 bg-surface-alt border border-border rounded-lg p-3 shadow-lg"
      style={{ top: popup.y, left: popup.x }}
    >
      <div className="flex items-start justify-between mb-1.5">
        <h4 className="text-xs font-semibold text-text-primary">{popup.name}</h4>
        <button onClick={onClose} className="text-text-muted hover:text-text-primary p-0.5 -mt-0.5 -mr-0.5">
          <X size={11} />
        </button>
      </div>
      <div className="flex items-center gap-2 mb-1.5">
        <div className="w-3 h-3 rounded-sm" style={{ backgroundColor: popup.color }} />
        <span className="text-sm font-mono font-semibold text-text-primary">{popup.score.toFixed(1)}</span>
        <span className="text-[10px] text-text-muted">/ 100</span>
      </div>
      <p className="text-[10px] text-text-muted leading-relaxed mb-2">{popup.description}</p>
      <button
        onClick={onAnalyze}
        className="flex items-center gap-1 text-[10px] text-bullish hover:underline"
      >
        <Brain size={10} /> Deep Analysis
      </button>
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
  const [analysisTarget, setAnalysisTarget] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div ref={containerRef} className="card flex flex-col h-full relative">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">{title}</h3>
        <button className="text-text-muted hover:text-text-primary p-0.5"><MoreHorizontal size={14} /></button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 12, bottom: 0, left: 60 }}
            onClick={(state) => {
              if (state?.activePayload?.[0]) {
                const entry = state.activePayload[0].payload as BarData;
                const rect = containerRef.current?.getBoundingClientRect();
                if (rect) {
                  setPopup({
                    visible: true,
                    x: Math.min(state.chartX || 60, rect.width - 250),
                    y: Math.min((state.chartY || 0) + 10, rect.height - 130),
                    name: entry.name, score: entry.score, color,
                    description: getDescription(entry.name),
                  });
                }
              }
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} horizontal={false} />
            <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 11, fill: "#8B949E" }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: "#8B949E" }} axisLine={false} tickLine={false} width={55} />
            <Bar dataKey="score" fill={color} radius={[0, 3, 3, 0]} barSize={12} className="cursor-pointer" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <BarPopup
        popup={popup}
        onClose={() => setPopup((p) => ({ ...p, visible: false }))}
        onAnalyze={() => { setAnalysisTarget(popup.name); setPopup((p) => ({ ...p, visible: false })); }}
      />
      {analysisTarget && (
        <AnalysisModal
          entity={{
            id: analysisTarget,
            label: analysisTarget,
            sentiment: (data.find((d) => d.name === analysisTarget)?.score || 50) / 100,
            volume: 0,
            keywords: [analysisTarget, title],
          }}
          contextType="sector"
          onClose={() => setAnalysisTarget(null)}
        />
      )}
    </div>
  );
}

function EconomicSentimentCard({ data }: { data: BarData[] }) {
  const [popup, setPopup] = useState<PopupState>({
    visible: false, x: 0, y: 0, name: "", score: 0, color: "", description: "",
  });
  const [analysisTarget, setAnalysisTarget] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  return (
    <div ref={containerRef} className="card flex flex-col h-full relative">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">Economic Sentiments</h3>
        <button className="text-text-muted hover:text-text-primary p-0.5"><MoreHorizontal size={14} /></button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 0, right: 12, bottom: 0, left: 80 }}
            onClick={(state) => {
              if (state?.activePayload?.[0]) {
                const entry = state.activePayload[0].payload as BarData;
                const barColor = entry.direction === "bullish" ? "#00FFC2" : "#FF4B2B";
                const rect = containerRef.current?.getBoundingClientRect();
                if (rect) {
                  setPopup({
                    visible: true,
                    x: Math.min(state.chartX || 80, rect.width - 250),
                    y: Math.min((state.chartY || 0) + 10, rect.height - 130),
                    name: entry.name, score: entry.score, color: barColor,
                    description: getDescription(entry.name),
                  });
                }
              }
            }}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} horizontal={false} />
            <XAxis type="number" domain={[0, 80]} tick={{ fontSize: 11, fill: "#8B949E" }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" tick={{ fontSize: 11, fill: "#8B949E" }} axisLine={false} tickLine={false} width={75} />
            <Bar
              dataKey="score" radius={[0, 3, 3, 0]} barSize={12} fill="#8B949E" className="cursor-pointer"
              shape={(props: any) => {
                const fill = props.payload?.direction === "bullish" ? "#00FFC2" : "#FF4B2B";
                return (<rect x={props.x} y={props.y + (props.height - 12) / 2} width={props.width} height={12} fill={fill} rx={3} ry={3} className="cursor-pointer" />);
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <BarPopup
        popup={popup}
        onClose={() => setPopup((p) => ({ ...p, visible: false }))}
        onAnalyze={() => { setAnalysisTarget(popup.name); setPopup((p) => ({ ...p, visible: false })); }}
      />
      {analysisTarget && (
        <AnalysisModal
          entity={{
            id: analysisTarget,
            label: analysisTarget,
            sentiment: (data.find((d) => d.name === analysisTarget)?.score || 50) / 100,
            volume: 0,
            keywords: [analysisTarget, "Economic Sentiment"],
          }}
          contextType="sector"
          onClose={() => setAnalysisTarget(null)}
        />
      )}
    </div>
  );
}

function MacroSentimentCard({ data }: { data: TimelineData[] }) {
  const [showInfo, setShowInfo] = useState(false);

  return (
    <div className="card flex flex-col h-full relative">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Macro Sentiment vs. Markets
        </h3>
        <button
          onClick={() => setShowInfo(true)}
          className="text-text-muted hover:text-accent-blue p-0.5"
          title="What does this chart show?"
        >
          <Info size={14} />
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 5, right: 12, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} />
            <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8B949E" }} axisLine={false} tickLine={false} />
            <YAxis
              yAxisId="sentiment" domain={[40, 100]}
              tick={{ fontSize: 10, fill: "#00FFC2" }} axisLine={false} tickLine={false}
            />
            <YAxis
              yAxisId="price" orientation="right" domain={[280, 650]}
              tick={{ fontSize: 10, fill: "#58A6FF" }} axisLine={false} tickLine={false}
            />
            <Tooltip
              contentStyle={{ backgroundColor: "#1C2128", border: "1px solid #21262D", borderRadius: 8, fontSize: 11 }}
              labelStyle={{ color: "#E6EDF3" }}
            />
            <Legend wrapperStyle={{ fontSize: 10, paddingTop: 4 }} />
            <Line yAxisId="sentiment" type="monotone" dataKey="sentiment" stroke="#00FFC2" strokeWidth={2} dot={false} name="OSINT Sentiment" />
            <Line yAxisId="price" type="monotone" dataKey="sp500" stroke="#58A6FF" strokeWidth={2} dot={false} name="S&P 500" />
            {data[0]?.vix !== undefined && (
              <Line yAxisId="sentiment" type="monotone" dataKey="vix" stroke="#FF4B2B" strokeWidth={1.5} dot={false} name="VIX (Fear)" strokeDasharray="4 2" />
            )}
            {data[0]?.treasury10y !== undefined && (
              <Line yAxisId="sentiment" type="monotone" dataKey="treasury10y" stroke="#E88A5A" strokeWidth={1.5} dot={false} name="10Y Yield" strokeDasharray="4 2" />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Info modal */}
      {showInfo && (
        <div className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center p-4" onClick={() => setShowInfo(false)}>
          <div className="bg-surface border border-border rounded-xl max-w-lg w-full p-5" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-text-primary">Macro Sentiment vs. Markets</h3>
              <button onClick={() => setShowInfo(false)} className="text-text-muted hover:text-text-primary"><X size={16} /></button>
            </div>

            <div className="space-y-3 text-sm text-text-secondary leading-relaxed">
              <p>
                This chart overlays our <span className="text-bullish font-medium">OSINT sentiment score</span> (aggregated from HackerNews, Reddit, GitHub, SEC filings, and Google Trends) against the <span className="text-accent-blue font-medium">S&P 500 index</span>.
              </p>

              <p className="font-semibold text-text-primary">Why this matters:</p>
              <ul className="list-disc ml-4 space-y-1.5 text-text-muted">
                <li><span className="text-text-secondary">Leading indicator:</span> Social sentiment often shifts 24-72 hours before price moves — divergences between sentiment and price signal potential reversals.</li>
                <li><span className="text-text-secondary">Confirmation signal:</span> When sentiment and price move together, the trend has conviction. Divergence = caution.</li>
                <li><span className="text-text-secondary">Regime detection:</span> Persistent sentiment above 70 = euphoria risk. Below 40 = fear (potential buying opportunity).</li>
              </ul>

              <p className="font-semibold text-text-primary">Additional indicators to watch:</p>
              <ul className="list-disc ml-4 space-y-1.5 text-text-muted">
                <li><span className="text-bearish">VIX (Fear Index):</span> Spikes in VIX inversely correlate with sentiment — when VIX surges and sentiment drops, it signals market stress.</li>
                <li><span className="text-[#E88A5A]">10Y Treasury Yield:</span> Rising yields pressure growth stocks — if yield rises while sentiment is bullish, watch for a correction.</li>
                <li><span className="text-text-secondary">Dollar Index (DXY):</span> Strong dollar pressures emerging markets and multinationals — useful context for global sentiment.</li>
                <li><span className="text-text-secondary">Bitcoin:</span> Acts as a risk-on barometer — correlates with retail sentiment and risk appetite.</li>
              </ul>

              <p className="text-xs text-text-muted italic mt-2">
                These additional indicators can be added to this chart once live market data feeds are connected via the yfinance integration.
              </p>
            </div>
          </div>
        </div>
      )}
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
