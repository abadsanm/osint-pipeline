"use client";

import { useState, useEffect, useCallback, useMemo } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  ZAxis,
  Tooltip,
  ReferenceArea,
  Label,
  Cell,
} from "recharts";
import { ExternalLink, Brain, TrendingUp, TrendingDown, Minus, ArrowRight } from "lucide-react";
import AnalysisModal from "@/components/AnalysisModal";
import InfoTooltip from "@/components/InfoTooltip";

/* ─── Types ─── */

interface FeatureItem {
  feature: string;
  volume: number;
  intensity: number;
  sources: string[];
  sample_quote: string;
  trend?: "worsening" | "improving" | "stable";
}

interface GapPoint {
  feature: string;
  satisfaction: number;
  importance: number;
  volume: number;
  entity_id: string;
  opportunity_score?: number;
}

interface TrendingTopic {
  keyword: string;
  count: number;
}

interface RecentInsight {
  title: string;
  source: string;
  content: string;
  url: string | null;
  created_at: string;
}

interface CompetitiveLandscapeEntry {
  entity: string;
  competitors: string[];
  shared_mentions: number;
}

interface InnovationData {
  channels: Record<string, number>;
  criticisms: FeatureItem[];
  requests: FeatureItem[];
  gap_map: GapPoint[];
  trending_topics: TrendingTopic[];
  recent_insights: RecentInsight[];
  source_breakdown: {
    total_entities: number;
    total_mentions: number;
    avg_sentiment: number;
  };
  competitive_landscape?: CompetitiveLandscapeEntry[];
}

/* ─── Source color mapping ─── */

const SOURCE_COLORS: Record<string, { bg: string; text: string; dot: string }> = {
  hacker_news:   { bg: "bg-[#FF6600]/20", text: "text-[#FF6600]", dot: "#FF6600" },
  github:        { bg: "bg-[#8B5CF6]/20", text: "text-[#8B5CF6]", dot: "#8B5CF6" },
  reddit:        { bg: "bg-[#FF4500]/20", text: "text-[#FF4500]", dot: "#FF4500" },
  sec_edgar:     { bg: "bg-bullish/20",   text: "text-bullish",   dot: "#00FFC2" },
  news:          { bg: "bg-[#FBBF24]/20", text: "text-[#FBBF24]", dot: "#FBBF24" },
  trustpilot:    { bg: "bg-[#00B67A]/20", text: "text-[#00B67A]", dot: "#00B67A" },
  google_trends: { bg: "bg-[#4285F4]/20", text: "text-[#4285F4]", dot: "#4285F4" },
  amazon:        { bg: "bg-[#FF9900]/20", text: "text-[#FF9900]", dot: "#FF9900" },
  producthunt:   { bg: "bg-[#DA552F]/20", text: "text-[#DA552F]", dot: "#DA552F" },
};

const defaultSourceColor = { bg: "bg-text-muted/20", text: "text-text-muted", dot: "#7D8590" };

function getSourceColor(source: string) {
  return SOURCE_COLORS[source] ?? defaultSourceColor;
}

/* ─── Helpers ─── */

const API_BASE = "http://localhost:8000/api";

const EMPTY_DATA: InnovationData = {
  channels: {},
  criticisms: [],
  requests: [],
  gap_map: [],
  trending_topics: [],
  recent_insights: [],
  source_breakdown: { total_entities: 0, total_mentions: 0, avg_sentiment: 0 },
};

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function sentimentColor(val: number): string {
  if (val >= 0.6) return "text-bullish";
  if (val <= 0.4) return "text-bearish";
  return "text-text-muted";
}

/* ─── Custom tooltip styles ─── */

const tooltipStyle = {
  backgroundColor: "#1C2128",
  border: "1px solid #21262D",
  borderRadius: 8,
  fontSize: 12,
};

/* ─── Skeleton loaders ─── */

function SkeletonBar() {
  return (
    <div className="space-y-3">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="w-20 h-3 bg-surface-alt rounded animate-pulse" />
          <div
            className="h-3 bg-surface-alt rounded animate-pulse"
            style={{ width: `${80 - i * 12}%` }}
          />
        </div>
      ))}
    </div>
  );
}

function SkeletonScatter() {
  return (
    <div className="w-full h-[400px] flex items-center justify-center">
      <div className="flex gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="rounded-full bg-surface-alt animate-pulse"
            style={{
              width: 20 + i * 8,
              height: 20 + i * 8,
              opacity: 0.3 + i * 0.1,
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* ─── Custom scatter tooltip ─── */

function GapMapTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload as GapPoint | undefined;
  if (!d) return null;
  return (
    <div className="bg-surface-alt border border-border rounded-lg px-3 py-2 shadow-lg">
      <p className="text-xs font-semibold text-text-primary mb-1">{d.feature}</p>
      <p className="text-[11px] text-text-secondary">
        Satisfaction: <span className="font-mono text-accent-blue">{(d.satisfaction * 100).toFixed(0)}%</span>
      </p>
      <p className="text-[11px] text-text-secondary">
        Importance: <span className="font-mono text-accent-blue">{(d.importance * 100).toFixed(0)}%</span>
      </p>
      <p className="text-[11px] text-text-secondary">
        Volume: <span className="font-mono text-accent-blue">{d.volume.toLocaleString()}</span>
      </p>
      {d.opportunity_score != null && (
        <p className="text-[11px] text-text-secondary">
          Opportunity: <span className="font-mono text-bullish">{(d.opportunity_score * 100).toFixed(0)}</span>
        </p>
      )}
    </div>
  );
}

/* ─── Custom scatter label ─── */

function renderScatterLabel(props: any) {
  const { cx, cy, payload } = props;
  if (!payload?.feature) return <text />;
  return (
    <text
      x={cx}
      y={cy - 12}
      textAnchor="middle"
      fill="#B1BAC4"
      fontSize={10}
      fontFamily="Inter, system-ui, sans-serif"
    >
      {payload.feature.length > 16
        ? payload.feature.slice(0, 14) + "..."
        : payload.feature}
    </text>
  );
}

/* ─── Trend Indicator ─── */

function TrendIndicator({ trend }: { trend?: string }) {
  if (!trend) return null;
  if (trend === "worsening")
    return <span title="Worsening"><TrendingUp size={12} className="text-bearish inline-block ml-1" /></span>;
  if (trend === "improving")
    return <span title="Improving"><TrendingDown size={12} className="text-bullish inline-block ml-1" /></span>;
  return <span title="Stable"><Minus size={12} className="text-text-muted inline-block ml-1" /></span>;
}

/* ─── Opportunity Score Badge ─── */

function OpportunityBadge({ score }: { score: number }) {
  const color =
    score >= 0.6 ? "bg-bullish/20 text-bullish border-bullish/30" :
    score >= 0.3 ? "bg-accent-blue/20 text-accent-blue border-accent-blue/30" :
    "bg-text-muted/20 text-text-muted border-text-muted/30";
  return (
    <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border ${color}`}>
      {(score * 100).toFixed(0)}
    </span>
  );
}

/* ─── Main Page ─── */

export default function InnovationPage() {
  const [activeChannel, setActiveChannel] = useState<string>("all");
  const [data, setData] = useState<InnovationData>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [analysisTarget, setAnalysisTarget] = useState<{id: string; label: string; sentiment: number; volume: number} | null>(null);

  const fetchData = useCallback(async (channel: string) => {
    try {
      const params = new URLSearchParams({ limit: "20" });
      if (channel !== "all") params.set("source", channel);
      const res = await fetch(`${API_BASE}/innovation?${params}`);
      if (res.ok) {
        const json: InnovationData = await res.json();
        setData(json);
      }
    } catch {
      // API unavailable — keep existing data
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch + polling every 15s
  useEffect(() => {
    setLoading(true);
    fetchData(activeChannel);
    const interval = setInterval(() => fetchData(activeChannel), 15_000);
    return () => clearInterval(interval);
  }, [activeChannel, fetchData]);

  // Derived data
  const channelList = useMemo(() => {
    const entries = Object.entries(data.channels).sort(([, a], [, b]) => b - a);
    return entries;
  }, [data.channels]);

  const { total_entities, total_mentions, avg_sentiment } = data.source_breakdown;

  const maxTopicCount = useMemo(
    () => Math.max(...data.trending_topics.map((t) => t.count), 1),
    [data.trending_topics],
  );

  const recentItems = data.recent_insights.slice(0, 10);

  // Fetch AI analysis for recent insights on load
  const [insightAnalyses, setInsightAnalyses] = useState<Record<string, string>>({});
  const [analyzingInsight, setAnalyzingInsight] = useState<string | null>(null);

  const analyzeInsight = useCallback(async (insight: RecentInsight, idx: number) => {
    const key = `${insight.title}-${idx}`;
    if (insightAnalyses[key]) return; // already analyzed
    setAnalyzingInsight(key);
    try {
      const res = await fetch(`${API_BASE}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_id: insight.title,
          label: insight.title,
          context_type: "entity",
          sentiment: 0.5,
          volume: 1,
          sources: { [insight.source]: 1 },
          keywords: insight.content.split(/\s+/).slice(0, 10),
          sampleDocs: [{ title: insight.title, content: insight.content, source: insight.source }],
        }),
      });
      if (res.ok) {
        const result = await res.json();
        setInsightAnalyses((prev) => ({ ...prev, [key]: result.analysis || "No analysis available." }));
      }
    } catch {
      setInsightAnalyses((prev) => ({ ...prev, [key]: "Analysis unavailable — API error." }));
    } finally {
      setAnalyzingInsight(null);
    }
  }, [insightAnalyses]);

  const topOpportunities = useMemo(() => {
    return [...data.gap_map]
      .filter((g) => g.opportunity_score != null)
      .sort((a, b) => (b.opportunity_score ?? 0) - (a.opportunity_score ?? 0))
      .slice(0, 5);
  }, [data.gap_map]);

  return (
    <div className="flex flex-col h-screen bg-base">
      <Header title="Product Innovation" />
      <SourceTicker />

      <div className="flex-1 p-module-gap-lg overflow-auto space-y-module-gap-lg">
        {/* ── Channel Slicer ── */}
        <div className="bg-surface border border-border rounded-card p-card-padding flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => setActiveChannel("all")}
            className={`px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors duration-150 ${
              activeChannel === "all"
                ? "bg-accent-blue text-base"
                : "text-text-muted hover:text-text-primary hover:bg-surface-alt"
            }`}
          >
            All
          </button>
          {channelList.map(([key, count]) => (
            <button
              key={key}
              onClick={() => setActiveChannel(key)}
              className={`px-3.5 py-1.5 text-xs font-medium rounded-md transition-colors duration-150 flex items-center gap-1.5 ${
                activeChannel === key
                  ? "bg-accent-blue text-base"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-alt"
              }`}
            >
              <span
                className="w-1.5 h-1.5 rounded-full flex-shrink-0"
                style={{ backgroundColor: getSourceColor(key).dot }}
              />
              {key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              <span className="font-mono text-[10px] opacity-70">{formatCount(count)}</span>
            </button>
          ))}
          {channelList.length === 0 && !loading && (
            <span className="text-xs text-text-muted ml-2">No channels available</span>
          )}
        </div>

        {/* ── Stats Bar ── */}
        <div className="bg-surface border border-border rounded-card px-card-padding py-2 flex flex-wrap items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-widest text-text-muted">Entities</span>
            <span className="font-mono text-sm text-text-primary">{formatCount(total_entities)}</span>
          </div>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-widest text-text-muted">Mentions</span>
            <span className="font-mono text-sm text-text-primary">{formatCount(total_mentions)}</span>
          </div>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase tracking-widest text-text-muted">Avg Sentiment</span>
            <span className={`font-mono text-sm font-medium ${sentimentColor(avg_sentiment)}`}>
              {(avg_sentiment * 100).toFixed(1)}%
            </span>
          </div>
        </div>

        {/* ── Feature Friction Board ── */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-module-gap-lg">
          {/* Criticisms — The Pain */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-4">
              <h3 className="text-sm font-semibold">
                <span className="text-bearish">Top Criticisms</span>
                <span className="text-text-muted ml-2 font-normal text-xs">The Pain</span>
              </h3>
              <InfoTooltip title="Top Criticisms">
                <p>Entities from the pipeline with below-neutral sentiment, ranked by mention volume. These represent the most-discussed pain points detected across all monitored sources. High intensity (darker bars) indicates stronger negative sentiment. Click the brain icon to get AI analysis of any criticism.</p>
              </InfoTooltip>
            </div>
            {loading ? (
              <SkeletonBar />
            ) : data.criticisms.length === 0 ? (
              <div className="h-[220px] flex items-center justify-center text-text-muted text-xs">
                No data for this source
              </div>
            ) : (
              <div>
                <ResponsiveContainer width="100%" height={data.criticisms.length * 28 + 20}>
                  <BarChart
                    data={data.criticisms}
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
                      tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="feature"
                      tick={{ fontSize: 12, fill: "#E6EDF3" }}
                      axisLine={false}
                      tickLine={false}
                      width={85}
                    />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar
                      dataKey="volume" fill="#FF4B2B" radius={[0, 3, 3, 0]} barSize={10} opacity={0.85}
                      className="cursor-pointer"
                      onClick={(entry: any) => {
                        if (entry?.feature) setAnalysisTarget({ id: entry.feature, label: entry.feature, sentiment: 1 - (entry.intensity || 0.5), volume: entry.volume || 0 });
                      }}
                    />
                  </BarChart>
                </ResponsiveContainer>
                {/* Sample quotes */}
                <div className="mt-3 space-y-1.5">
                  {data.criticisms.map(
                    (c) =>
                      c.sample_quote && (
                        <div key={c.feature} className="flex items-center gap-1 text-[11px] text-text-muted pl-1 border-l-2 border-bearish/30">
                          <p className="truncate flex-1">
                            <span className="font-medium text-text-secondary">{c.feature}</span>
                            <TrendIndicator trend={c.trend} />
                            :{" "}
                            &ldquo;{c.sample_quote.slice(0, 120)}{c.sample_quote.length > 120 ? "..." : ""}&rdquo;
                          </p>
                          <button
                            onClick={() => setAnalysisTarget({ id: c.feature, label: c.feature, sentiment: 1 - c.intensity, volume: c.volume })}
                            className="flex-shrink-0 p-0.5 rounded hover:bg-surface-alt text-text-muted hover:text-accent-blue transition-colors"
                            title="Analyze with Sentinel AI"
                          >
                            <Brain size={12} />
                          </button>
                        </div>
                      ),
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Requests — The Opportunity */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-4">
              <h3 className="text-sm font-semibold">
                <span className="text-bullish">Top Requests</span>
                <span className="text-text-muted ml-2 font-normal text-xs">The Opportunity</span>
              </h3>
              <InfoTooltip title="Top Requests">
                <p>Entities matching feature-request language patterns (wish, want, need, should, missing) or tagged as PRODUCT/TECHNOLOGY entities. These represent consumer demand signals and unmet needs. Higher volume means more people are asking for this. Use these to validate product roadmap priorities.</p>
              </InfoTooltip>
            </div>
            {loading ? (
              <SkeletonBar />
            ) : data.requests.length === 0 ? (
              <div className="h-[220px] flex items-center justify-center text-text-muted text-xs">
                No data for this source
              </div>
            ) : (
              <div>
                <ResponsiveContainer width="100%" height={data.requests.length * 28 + 20}>
                  <BarChart
                    data={data.requests}
                    layout="vertical"
                    margin={{ top: 0, right: 20, bottom: 0, left: 100 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#8B949E"
                      strokeOpacity={0.1}
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="feature"
                      tick={{ fontSize: 12, fill: "#E6EDF3" }}
                      axisLine={false}
                      tickLine={false}
                      width={95}
                    />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Bar
                      dataKey="volume" fill="#00FFC2" radius={[0, 3, 3, 0]} barSize={10} opacity={0.85}
                      className="cursor-pointer"
                      onClick={(entry: any) => {
                        if (entry?.feature) setAnalysisTarget({ id: entry.feature, label: entry.feature, sentiment: 0.5, volume: entry.volume || 0 });
                      }}
                    />
                  </BarChart>
                </ResponsiveContainer>
                {/* Sample quotes */}
                <div className="mt-3 space-y-1.5">
                  {data.requests.map(
                    (r) =>
                      r.sample_quote && (
                        <div key={r.feature} className="flex items-center gap-1 text-[11px] text-text-muted pl-1 border-l-2 border-bullish/30">
                          <p className="truncate flex-1">
                            <span className="font-medium text-text-secondary">{r.feature}</span>
                            <TrendIndicator trend={r.trend} />
                            :{" "}
                            &ldquo;{r.sample_quote.slice(0, 120)}{r.sample_quote.length > 120 ? "..." : ""}&rdquo;
                          </p>
                          <button
                            onClick={() => setAnalysisTarget({ id: r.feature, label: r.feature, sentiment: 0.5, volume: r.volume })}
                            className="flex-shrink-0 p-0.5 rounded hover:bg-surface-alt text-text-muted hover:text-accent-blue transition-colors"
                            title="Analyze with Sentinel AI"
                          >
                            <Brain size={12} />
                          </button>
                        </div>
                      ),
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* ── Innovation Gap Map ── */}
        <div className="bg-surface border border-border rounded-card p-card-padding-lg">
          <div className="flex items-center gap-1.5 mb-4">
            <h3 className="text-sm font-semibold text-text-secondary">Innovation Gap Map</h3>
            <InfoTooltip title="Innovation Gap Map">
              <p>Scatter plot mapping satisfaction (X) against importance (Y). The top-left quadrant (Innovation Zone) contains features that are important to users but currently unsatisfying — these are the highest-value opportunities to build. Bubble size reflects mention volume. Green dots are in the Innovation Zone.</p>
            </InfoTooltip>
          </div>
          {loading ? (
            <SkeletonScatter />
          ) : data.gap_map.length === 0 ? (
            <div className="h-[400px] flex items-center justify-center text-text-muted text-xs">
              No gap map data available
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={400}>
              <ScatterChart margin={{ top: 20, right: 30, bottom: 30, left: 30 }}>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="#8B949E"
                  strokeOpacity={0.1}
                />
                <XAxis
                  type="number"
                  dataKey="satisfaction"
                  domain={[0, 1]}
                  tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                  name="Satisfaction"
                >
                  <Label
                    value="Current Satisfaction"
                    position="bottom"
                    offset={5}
                    style={{ fill: "#7D8590", fontSize: 12 }}
                  />
                </XAxis>
                <YAxis
                  type="number"
                  dataKey="importance"
                  domain={[0, 1]}
                  tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                  name="Importance"
                >
                  <Label
                    value="Importance to User"
                    position="insideLeft"
                    angle={-90}
                    offset={10}
                    style={{ fill: "#7D8590", fontSize: 12 }}
                  />
                </YAxis>
                <ZAxis
                  type="number"
                  dataKey="volume"
                  range={[80, 600]}
                  name="Volume"
                />
                {/* Innovation Zone — top-left quadrant */}
                <ReferenceArea
                  x1={0}
                  x2={0.5}
                  y1={0.5}
                  y2={1}
                  fill="#00FFC2"
                  fillOpacity={0.04}
                  stroke="#00FFC2"
                  strokeOpacity={0.25}
                  strokeDasharray="4 4"
                  label={{
                    value: "Innovation Zone",
                    position: "insideTopLeft",
                    fill: "#00FFC2",
                    fontSize: 11,
                    fontWeight: 600,
                  }}
                />
                <Tooltip content={<GapMapTooltip />} />
                <Scatter
                  data={data.gap_map}
                  fill="#58A6FF"
                  opacity={0.8}
                  name="Feature"
                  label={renderScatterLabel}
                >
                  {data.gap_map.map((entry, idx) => {
                    // Color points in Innovation Zone differently
                    const inZone = entry.satisfaction < 0.5 && entry.importance > 0.5;
                    return (
                      <Cell
                        key={`cell-${idx}`}
                        fill={inZone ? "#00FFC2" : "#58A6FF"}
                        opacity={inZone ? 0.9 : 0.7}
                      />
                    );
                  })}
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
          )}

          {/* Top Opportunities ranked list */}
          {topOpportunities.length > 0 && (
            <div className="mt-5 border-t border-border pt-4">
              <h4 className="text-xs font-semibold text-text-secondary mb-3">Top Opportunities</h4>
              <div className="space-y-2">
                {topOpportunities.map((g, idx) => (
                  <div
                    key={g.entity_id}
                    className="flex items-center gap-3 px-3 py-2 rounded-md bg-surface-alt/40 hover:bg-surface-alt transition-colors"
                  >
                    <span className="text-[10px] font-mono text-text-muted w-4">{idx + 1}.</span>
                    <span className="text-xs font-medium text-text-primary flex-1 truncate">{g.feature}</span>
                    <OpportunityBadge score={g.opportunity_score ?? 0} />
                    <span className="text-[10px] text-text-muted font-mono">
                      Sat {(g.satisfaction * 100).toFixed(0)}%
                    </span>
                    <span className="text-[10px] text-text-muted font-mono">
                      Imp {(g.importance * 100).toFixed(0)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* ── Competitive Landscape ── */}
        {data.competitive_landscape && data.competitive_landscape.length > 0 && (
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-4">
              <h3 className="text-sm font-semibold text-text-secondary">Competitive Landscape</h3>
              <InfoTooltip title="Competitive Landscape">
                <p>Entities that are frequently mentioned together across sources, indicating competitive relationships. Shared mentions count how often both entities appear in the same documents. Use this to identify market positioning, competitive threats, and partnership opportunities detected by cross-correlation.</p>
              </InfoTooltip>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {data.competitive_landscape.map((entry) => (
                <div
                  key={entry.entity}
                  className="flex items-center gap-2 px-3 py-2.5 rounded-md bg-surface-alt/50 border border-border/50"
                >
                  <span className="text-xs font-semibold text-text-primary whitespace-nowrap">
                    {entry.entity}
                  </span>
                  <ArrowRight size={12} className="text-text-muted flex-shrink-0" />
                  <div className="flex flex-wrap gap-1 flex-1">
                    {entry.competitors.map((comp) => (
                      <span
                        key={comp}
                        className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-accent-blue/10 text-accent-blue border border-accent-blue/20"
                      >
                        {comp}
                      </span>
                    ))}
                  </div>
                  <span className="text-[10px] font-mono text-text-muted flex-shrink-0" title="Shared mentions">
                    {entry.shared_mentions}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── Trending Topics ── */}
        {data.trending_topics.length > 0 && (
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center gap-1.5 mb-4">
              <h3 className="text-sm font-semibold text-text-secondary">Trending Topics</h3>
              <InfoTooltip title="Trending Topics">
                <p>Most frequently mentioned keywords across all monitored entities. Larger text indicates higher frequency. These reveal emerging themes and trends in real-time. Use these to spot new market narratives before they become mainstream.</p>
              </InfoTooltip>
            </div>
            <div className="flex flex-wrap gap-2">
              {data.trending_topics.map((topic) => {
                const ratio = topic.count / maxTopicCount;
                const fontSize = 11 + Math.round(ratio * 8); // 11px to 19px
                const opacity = 0.4 + ratio * 0.6; // 0.4 to 1.0
                return (
                  <span
                    key={topic.keyword}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-accent-blue/10 border border-accent-blue/20 text-accent-blue transition-colors hover:bg-accent-blue/20"
                    style={{ fontSize, opacity }}
                  >
                    {topic.keyword}
                    <span className="font-mono text-[10px] opacity-70">{formatCount(topic.count)}</span>
                  </span>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Recent Insights Feed ── */}
        <div className="bg-surface border border-border rounded-card p-card-padding-lg">
          <div className="flex items-center gap-1.5 mb-4">
            <h3 className="text-sm font-semibold text-text-secondary">Recent Insights</h3>
            <InfoTooltip title="Recent Insights">
              <p>Latest documents from the pipeline&apos;s product-related sources (Reddit, HN, Trustpilot, Amazon, ProductHunt). These are the raw signals feeding the analysis above. Click external links to read the source material.</p>
            </InfoTooltip>
          </div>
          {loading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="flex gap-3 animate-pulse">
                  <div className="w-16 h-5 rounded bg-surface-alt flex-shrink-0" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-3 bg-surface-alt rounded w-3/4" />
                    <div className="h-2.5 bg-surface-alt rounded w-full" />
                  </div>
                </div>
              ))}
            </div>
          ) : recentItems.length === 0 ? (
            <div className="py-8 text-center text-text-muted text-xs">
              No recent insights
            </div>
          ) : (
            <div className="max-h-[500px] overflow-y-auto space-y-1 pr-1">
              {recentItems.map((insight, idx) => {
                const sc = getSourceColor(insight.source);
                const analysisKey = `${insight.title}-${idx}`;
                const analysis = insightAnalyses[analysisKey];
                const isAnalyzing = analyzingInsight === analysisKey;
                return (
                  <div
                    key={analysisKey}
                    className="px-3 py-2.5 rounded-md hover:bg-surface-alt/50 transition-colors group"
                  >
                    <div className="flex items-start gap-3">
                      {/* Source badge */}
                      <span
                        className={`flex-shrink-0 px-2 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${sc.bg} ${sc.text}`}
                      >
                        {insight.source.replace(/_/g, " ")}
                      </span>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className="text-xs font-medium text-text-primary truncate">
                            {insight.title}
                          </p>
                          {insight.url && (
                            <a
                              href={insight.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex-shrink-0 text-text-muted hover:text-accent-blue transition-colors opacity-0 group-hover:opacity-100"
                            >
                              <ExternalLink size={12} />
                            </a>
                          )}
                        </div>
                        <p className="text-[11px] text-text-muted mt-0.5 line-clamp-2">
                          {insight.content}
                        </p>
                      </div>

                      {/* Analyze button + Timestamp */}
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <button
                          onClick={() => analyzeInsight(insight, idx)}
                          disabled={isAnalyzing || !!analysis}
                          className={`p-1 rounded transition-colors ${
                            analysis
                              ? "text-bullish"
                              : isAnalyzing
                              ? "text-accent-blue animate-pulse"
                              : "text-text-muted hover:text-accent-blue hover:bg-surface-alt"
                          }`}
                          title={analysis ? "Analysis complete" : "Analyze with Sentinel AI"}
                        >
                          <Brain size={13} />
                        </button>
                        <span className="text-[10px] font-mono text-text-muted">
                          {timeAgo(insight.created_at)}
                        </span>
                      </div>
                    </div>

                    {/* Expanded AI analysis */}
                    {analysis && (
                      <div className="mt-2 ml-[72px] p-2.5 rounded-md bg-surface-alt/60 border border-border/50 text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">
                        {analysis}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Bottom spacer */}
        <div className="h-4" />
      </div>

      {/* ── Analysis Modal ── */}
      {analysisTarget && (
        <AnalysisModal
          entity={analysisTarget}
          contextType="entity"
          onClose={() => setAnalysisTarget(null)}
        />
      )}
    </div>
  );
}
