"use client";

interface Stats {
  total_ingested: number;
  total_normalized: number;
  total_correlated: number;
  sources: Record<string, number>;
  entities_tracked: number;
}

interface LiveStatsProps {
  stats: Stats;
}

export default function LiveStats({ stats }: LiveStatsProps) {
  if (stats.total_ingested === 0) return null;

  const sourceEntries = Object.entries(stats.sources).sort(
    ([, a], [, b]) => b - a
  );

  return (
    <div className="bg-surface border border-border rounded-card px-3 py-1.5">
      <div className="flex items-center gap-6 text-xs">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" />
          <span className="text-text-muted">Pipeline Live</span>
        </div>
        <div>
          <span className="font-mono text-bullish">{stats.total_ingested.toLocaleString()}</span>
          <span className="text-text-muted ml-1">ingested</span>
        </div>
        <div>
          <span className="font-mono text-accent-blue">{stats.total_normalized.toLocaleString()}</span>
          <span className="text-text-muted ml-1">normalized</span>
        </div>
        <div>
          <span className="font-mono text-volume-spike">{stats.total_correlated.toLocaleString()}</span>
          <span className="text-text-muted ml-1">correlated</span>
        </div>
        <div>
          <span className="font-mono text-text-secondary">{stats.entities_tracked}</span>
          <span className="text-text-muted ml-1">entities</span>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-2">
          {sourceEntries.slice(0, 4).map(([source, count]) => (
            <span key={source} className="text-text-muted">
              {source}: <span className="font-mono text-text-secondary">{count}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
