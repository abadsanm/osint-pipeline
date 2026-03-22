"use client";

import { useStats } from "@/hooks/useApiData";

/** Color per source — matches connector identity */
const SOURCE_META: Record<string, { label: string; dot: string; text: string }> = {
  hacker_news:      { label: "Hacker News",      dot: "#FF6600", text: "text-[#FF6600]" },
  github:           { label: "GitHub",            dot: "#8B5CF6", text: "text-[#8B5CF6]" },
  reddit:           { label: "Reddit",            dot: "#FF4500", text: "text-[#FF4500]" },
  sec_edgar:        { label: "SEC EDGAR",         dot: "#00FFC2", text: "text-bullish" },
  openinsider:      { label: "OpenInsider",       dot: "#00FFC2", text: "text-bullish" },
  fred:             { label: "FRED",              dot: "#58A6FF", text: "text-accent-blue" },
  news:             { label: "Financial News",    dot: "#FBBF24", text: "text-[#FBBF24]" },
  producthunt:      { label: "ProductHunt",       dot: "#DA552F", text: "text-[#DA552F]" },
  trustpilot:       { label: "Trustpilot",        dot: "#00B67A", text: "text-[#00B67A]" },
  google_trends:    { label: "Google Trends",     dot: "#4285F4", text: "text-[#4285F4]" },
  amazon:           { label: "Amazon",            dot: "#FF9900", text: "text-[#FF9900]" },
  alpaca:           { label: "Alpaca",            dot: "#F5D547", text: "text-[#F5D547]" },
  binance:          { label: "Binance",           dot: "#F3BA2F", text: "text-[#F3BA2F]" },
  finviz:           { label: "Finviz",            dot: "#2196F3", text: "text-[#2196F3]" },
  techcrunch:       { label: "TechCrunch",        dot: "#0A9E1A", text: "text-[#0A9E1A]" },
  seeking_alpha:    { label: "Seeking Alpha",     dot: "#F57C00", text: "text-[#F57C00]" },
  techmeme:         { label: "Techmeme",          dot: "#CC0000", text: "text-[#CC0000]" },
  newsletter:       { label: "Newsletters",       dot: "#AB47BC", text: "text-[#AB47BC]" },
  sam_gov:          { label: "SAM.gov",           dot: "#1565C0", text: "text-[#1565C0]" },
  usaspending:      { label: "USAspending",       dot: "#1565C0", text: "text-[#1565C0]" },
  federal_register: { label: "Fed Register",      dot: "#5C6BC0", text: "text-[#5C6BC0]" },
  sbir:             { label: "SBIR",              dot: "#26A69A", text: "text-[#26A69A]" },
  bls:              { label: "BLS",               dot: "#78909C", text: "text-[#78909C]" },
  stocktwits:       { label: "StockTwits",        dot: "#1DA1F2", text: "text-[#1DA1F2]" },
  bluesky:          { label: "Bluesky",           dot: "#0085FF", text: "text-[#0085FF]" },
  yahoo_news:       { label: "Yahoo News",        dot: "#6001D2", text: "text-[#6001D2]" },
  yahoo_rss:        { label: "Yahoo RSS",         dot: "#6001D2", text: "text-[#6001D2]" },
};

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

export default function SourceTicker() {
  const stats = useStats();

  const entries = Object.entries(stats.sources || {}) as [string, number][];
  const sources = entries.sort(([, a], [, b]) => b - a);

  if (sources.length === 0) return null;

  const items = sources.map(([key, count]) => {
    const meta = SOURCE_META[key] || { label: key, dot: "#7D8590", text: "text-text-muted" };
    return { key, count, ...meta };
  });

  const total = sources.reduce((sum, [, c]) => sum + c, 0);

  const renderItem = (item: (typeof items)[0], idx: number) => (
    <span
      key={`${item.key}-${idx}`}
      className="inline-flex items-center gap-1.5 mx-4 flex-shrink-0"
    >
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ backgroundColor: item.dot }}
      />
      <span className="text-text-muted whitespace-nowrap">{item.label}</span>
      <span
        className={`font-mono font-medium whitespace-nowrap ${item.count > 0 ? item.text : "text-text-muted"}`}
      >
        {formatCount(item.count)}
      </span>
    </span>
  );

  return (
    <div className="hidden md:block relative overflow-hidden bg-base/80 border-b border-border/50">
      {/* Left: total badge with fade */}
      <div className="absolute left-0 top-0 bottom-0 z-10 flex items-center pl-3 pr-8 bg-gradient-to-r from-base via-base/95 to-transparent">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" />
          <span className="text-[10px] uppercase tracking-widest text-text-muted font-semibold">
            Sources
          </span>
          <span className="font-mono text-xs text-bullish ml-0.5">
            {formatCount(total)}
          </span>
        </div>
      </div>

      {/* Right fade */}
      <div className="absolute right-0 top-0 bottom-0 z-10 w-16 bg-gradient-to-l from-base to-transparent pointer-events-none" />

      {/* Scrolling track */}
      <div className="flex items-center h-7 text-[11px] pl-40 overflow-hidden">
        <div className="source-ticker-track flex items-center whitespace-nowrap">
          {items.map((item, i) => renderItem(item, i))}
          {items.map((item, i) => renderItem(item, items.length + i))}
        </div>
      </div>
    </div>
  );
}
