"use client";

import { useState, useEffect } from "react";
import { X, Loader2, TrendingUp, Lightbulb, ExternalLink, Brain, Crosshair, Check } from "lucide-react";
import Link from "next/link";

interface AnalysisModalProps {
  entity: {
    id: string;
    label: string;
    sentiment: number;
    volume: number;
    sources?: Record<string, number>;
    keywords?: string[];
    sampleDocs?: any[];
    signal_type?: string;
    confidence?: number;
    headline?: string;
  };
  contextType?: "entity" | "signal" | "sector";
  onClose: () => void;
}

const API_BASE = "http://localhost:8000/api";

export default function AnalysisModal({
  entity,
  contextType = "entity",
  onClose,
}: AnalysisModalProps) {
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [model, setModel] = useState("");
  const [error, setError] = useState("");
  const [tracked, setTracked] = useState(false);
  const [tracking, setTracking] = useState(false);

  useEffect(() => {
    const fetchAnalysis = async () => {
      setLoading(true);

      // If we're missing context (volume=0, no sources), try to enrich from search API
      let enriched = { ...entity };
      if (!entity.volume || entity.volume === 0 || !entity.sources || Object.keys(entity.sources).length === 0) {
        try {
          const searchRes = await fetch(`${API_BASE}/search?q=${encodeURIComponent(entity.id)}`);
          if (searchRes.ok) {
            const results = await searchRes.json();
            if (results.length > 0) {
              const match = results[0];
              enriched = {
                ...enriched,
                volume: match.volume || enriched.volume,
                sentiment: match.sentiment || enriched.sentiment,
                label: match.label || enriched.label,
              };
            }
          }
          // Also try to get full sector data
          const sectorRes = await fetch(`${API_BASE}/sectors?limit=100`);
          if (sectorRes.ok) {
            const sectors = await sectorRes.json();
            const found = sectors.find((s: any) =>
              s.id.toLowerCase() === entity.id.toLowerCase() ||
              s.label.toLowerCase().includes(entity.id.toLowerCase())
            );
            if (found) {
              enriched = {
                ...enriched,
                volume: found.volume || enriched.volume,
                sentiment: found.sentiment || enriched.sentiment,
                sources: found.sources || enriched.sources,
                keywords: found.keywords || enriched.keywords,
                sampleDocs: found.sampleDocs || enriched.sampleDocs,
              };
            }
          }
        } catch {
          // Continue with what we have
        }
      }

      try {
        const res = await fetch(`${API_BASE}/analyze`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            entity_id: enriched.id,
            label: enriched.label,
            sentiment: enriched.sentiment,
            volume: enriched.volume,
            sources: enriched.sources || {},
            keywords: enriched.keywords || [],
            sampleDocs: enriched.sampleDocs || [],
            signal_type: enriched.signal_type || "",
            confidence: enriched.confidence || 0,
            context_type: contextType,
          }),
        });
        if (res.ok) {
          const data = await res.json();
          setAnalysis(data.analysis);
          setModel(data.model);
          if (data.error) setError(data.error);
        } else {
          setAnalysis("Analysis unavailable. API returned an error.");
        }
      } catch {
        setAnalysis("Analysis unavailable. Could not reach the API.");
      }
      setLoading(false);
    };

    fetchAnalysis();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entity.id, contextType]);

  const handleTrack = async () => {
    setTracking(true);
    try {
      const sourceContext = analysis ? analysis.substring(0, 200) : "";
      const res = await fetch(`${API_BASE}/research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_id: entity.id,
          entity_label: entity.label,
          action: "Track for follow-up from AI analysis",
          source_context: sourceContext,
          priority: "medium",
          sentiment: entity.sentiment,
          mention_count: entity.volume,
        }),
      });
      if (res.ok) {
        setTracked(true);
        setTimeout(() => setTracked(false), 2500);
      }
    } catch {
      // API unavailable
    }
    setTracking(false);
  };

  const sentimentColor =
    entity.sentiment > 0.6
      ? "text-bullish"
      : entity.sentiment < 0.4
      ? "text-bearish"
      : "text-neutral";

  const sentimentLabel =
    entity.sentiment > 0.6
      ? "Bullish"
      : entity.sentiment < 0.4
      ? "Bearish"
      : "Neutral";

  return (
    <div
      className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface border border-border rounded-xl max-w-2xl w-full max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 bg-surface border-b border-border px-5 py-3 flex items-center justify-between rounded-t-xl">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-accent-blue/20 flex items-center justify-center">
              <Brain size={16} className="text-accent-blue" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-text-primary">
                AI Analysis: {entity.label}
              </h3>
              <p className="text-[11px] text-text-muted">
                {model === "rule-based-fallback" ? "Basic analysis" : "Powered by Sentinel AI"}
                {error && " (limited mode)"}
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary p-1"
          >
            <X size={16} />
          </button>
        </div>

        {/* Stats bar */}
        <div className="px-5 py-2.5 border-b border-border bg-surface-alt/50 flex items-center gap-4 text-xs">
          <div>
            <span className="text-text-muted">Sentiment: </span>
            <span className={`font-mono font-semibold ${sentimentColor}`}>
              {sentimentLabel} ({Math.round(entity.sentiment * 100)}%)
            </span>
          </div>
          <div>
            <span className="text-text-muted">Volume: </span>
            <span className="font-mono text-text-primary">{entity.volume?.toLocaleString()}</span>
          </div>
          {entity.confidence !== undefined && entity.confidence > 0 && (
            <div>
              <span className="text-text-muted">Confidence: </span>
              <span className="font-mono text-accent-blue">{Math.round(entity.confidence * 100)}%</span>
            </div>
          )}
          <div>
            <span className="text-text-muted">Sources: </span>
            <span className="text-text-primary">
              {Object.keys(entity.sources || {}).join(", ") || "unknown"}
            </span>
          </div>
        </div>

        {/* Analysis content */}
        <div className="px-5 py-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={24} className="animate-spin text-accent-blue" />
              <span className="text-sm text-text-muted ml-3">Generating analysis...</span>
            </div>
          ) : (
            <div className="prose-dark">
              {analysis?.split("\n").map((line, i) => {
                if (!line.trim()) return <br key={i} />;

                // Render markdown-like headers
                if (line.startsWith("## ")) {
                  return (
                    <h2 key={i} className="text-base font-semibold text-text-primary mt-4 mb-2">
                      {line.replace("## ", "")}
                    </h2>
                  );
                }
                if (line.startsWith("### ")) {
                  return (
                    <h3 key={i} className="text-sm font-semibold text-text-secondary mt-3 mb-1.5">
                      {line.replace("### ", "")}
                    </h3>
                  );
                }
                if (line.startsWith("- **")) {
                  const parts = line.replace("- **", "").split("**");
                  return (
                    <p key={i} className="text-sm text-text-primary ml-3 mb-1 leading-relaxed">
                      <span className="font-semibold text-accent-blue">{parts[0]}</span>
                      {parts.slice(1).join("")}
                    </p>
                  );
                }
                if (line.startsWith("- ")) {
                  return (
                    <p key={i} className="text-sm text-text-secondary ml-3 mb-1 leading-relaxed">
                      • {line.substring(2)}
                    </p>
                  );
                }
                if (line.startsWith("**") && line.endsWith("**")) {
                  return (
                    <p key={i} className="text-sm font-semibold text-text-primary mt-2 mb-1">
                      {line.replace(/\*\*/g, "")}
                    </p>
                  );
                }
                if (line.startsWith("*") && line.endsWith("*")) {
                  return (
                    <p key={i} className="text-[11px] text-text-muted italic mt-2">
                      {line.replace(/\*/g, "")}
                    </p>
                  );
                }

                // Bold inline markers
                const rendered = line.replace(
                  /\*\*(.+?)\*\*/g,
                  '<span class="font-semibold text-text-primary">$1</span>'
                );

                return (
                  <p
                    key={i}
                    className="text-sm text-text-secondary leading-relaxed mb-2"
                    dangerouslySetInnerHTML={{ __html: rendered }}
                  />
                );
              })}
            </div>
          )}
        </div>

        {/* Source links */}
        {entity.sampleDocs && entity.sampleDocs.length > 0 && (
          <div className="px-5 py-3 border-t border-border">
            <h4 className="text-[11px] uppercase tracking-widest text-text-muted font-semibold mb-2">
              Sources
            </h4>
            <div className="space-y-1.5">
              {entity.sampleDocs
                .filter((d: any) => d.url || d.title)
                .slice(0, 5)
                .map((doc: any, i: number) => (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="text-text-muted flex-shrink-0 mt-0.5 w-16 text-right font-mono">
                      {doc.source || "unknown"}
                    </span>
                    {doc.url ? (
                      <a
                        href={doc.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent-blue hover:underline truncate flex items-center gap-1"
                      >
                        {doc.title || doc.url}
                        <ExternalLink size={10} className="flex-shrink-0 opacity-50" />
                      </a>
                    ) : (
                      <span className="text-text-secondary truncate">
                        {doc.title || doc.headline || "Untitled"}
                      </span>
                    )}
                  </div>
                ))}
            </div>
          </div>
        )}

        {/* Actions footer */}
        <div className="sticky bottom-0 bg-surface border-t border-border px-5 py-3 flex items-center justify-between rounded-b-xl">
          <div className="flex items-center gap-2">
            <Link
              href={`/alpha/${encodeURIComponent(entity.id)}`}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-accent-blue/10 text-accent-blue border border-accent-blue/20 rounded-md hover:bg-accent-blue/20 transition-colors"
            >
              <TrendingUp size={12} />
              Financial Alpha
            </Link>
            <Link
              href="/innovation"
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium bg-bullish/10 text-bullish border border-bullish/20 rounded-md hover:bg-bullish/20 transition-colors"
            >
              <Lightbulb size={12} />
              Product Ideation
            </Link>
            <button
              onClick={handleTrack}
              disabled={tracking || tracked}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors ${
                tracked
                  ? "bg-bullish/10 text-bullish border-bullish/20"
                  : "bg-surface-alt text-text-muted border-border hover:text-text-primary hover:border-accent-blue/30"
              } disabled:cursor-not-allowed`}
            >
              {tracked ? (
                <>
                  <Check size={12} />
                  Tracked!
                </>
              ) : tracking ? (
                <>
                  <Loader2 size={12} className="animate-spin" />
                  Tracking...
                </>
              ) : (
                <>
                  <Crosshair size={12} />
                  Track This
                </>
              )}
            </button>
          </div>
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-text-muted hover:text-text-primary transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
