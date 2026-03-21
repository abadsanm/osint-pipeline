"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { MoreHorizontal, X, ExternalLink } from "lucide-react";

interface SampleDoc {
  title: string;
  source: string;
  url: string | null;
  created_at: string;
  score: number;
}

interface Sector {
  id: string;
  label: string;
  sector: string;
  sentiment: number;
  volume: number;
  priceChange24h: number;
  keywords: string[];
  sources?: Record<string, number>;
  sampleDocs?: SampleDoc[];
  uniqueSources?: number;
}

interface HeatSphereProps {
  data: Sector[];
}

export default function HeatSphere({ data }: HeatSphereProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    sector: Sector;
  } | null>(null);
  const [modal, setModal] = useState<Sector | null>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current || !data.length) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight - 32;
    const svg = d3.select(svgRef.current);

    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    // Color scale: bearish -> neutral -> bullish
    // Amplify differences: remap 0.3-0.7 range to full color spectrum
    const colorScale = d3
      .scaleLinear<string>()
      .domain([0.3, 0.42, 0.5, 0.58, 0.7])
      .range(["#FF4B2B", "#E88A5A", "#8B949E", "#5CB88A", "#00FFC2"])
      .clamp(true);

    const maxVol = d3.max(data, (d) => d.volume) || 1;
    const radiusScale = d3
      .scaleSqrt()
      .domain([0, maxVol])
      .range([14, Math.min(width, height) * 0.1]);

    const nodes = data.map((d) => ({
      ...d,
      r: radiusScale(d.volume),
      x: width / 2 + (Math.random() - 0.5) * width * 0.5,
      y: height / 2 + (Math.random() - 0.5) * height * 0.5,
    }));

    const simulation = d3
      .forceSimulation(nodes as any)
      .force("x", d3.forceX(width / 2).strength(0.05))
      .force("y", d3.forceY(height / 2).strength(0.05))
      .force("collision", d3.forceCollide((d: any) => d.r + 3).strength(1))
      .force("charge", d3.forceManyBody().strength(-8))
      .alphaDecay(0.012);

    const bubbleGroup = svg.append("g");

    const bubbles = bubbleGroup
      .selectAll("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer");

    bubbles
      .append("circle")
      .attr("r", (d: any) => d.r)
      .attr("fill", (d: any) => colorScale(d.sentiment))
      .attr("opacity", 0.75)
      .attr("stroke", (d: any) => colorScale(d.sentiment))
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.4);

    // Entity name label
    bubbles
      .append("text")
      .text((d: any) => {
        const label = d.label || d.id;
        if (label.length <= 10 || label.startsWith("r/")) return label;
        const words = label.split(/\s+/).filter((w: string) => w.length > 2);
        return words[0]?.substring(0, 12) || label.substring(0, 10);
      })
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-family", "Inter, system-ui, sans-serif")
      .attr("font-size", (d: any) => Math.max(8, Math.min(d.r / 2.5, 14)))
      .attr("font-weight", 600)
      .attr("fill", "#0A0E12")
      .attr("pointer-events", "none");

    bubbles
      .on("mouseenter", function (event: any, d: any) {
        simulation.alphaTarget(0);
        d3.select(this).select("circle").attr("opacity", 1).attr("stroke-width", 2.5);
        const rect = container.getBoundingClientRect();
        setTooltip({
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
          sector: d,
        });
      })
      .on("mouseleave", function () {
        simulation.alphaTarget(0.008);
        d3.select(this).select("circle").attr("opacity", 0.75).attr("stroke-width", 1.5);
        setTooltip(null);
      })
      .on("click", (_: any, d: any) => {
        setTooltip(null);
        setModal(d);
      });

    simulation.on("tick", () => {
      bubbles.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [data]);

  return (
    <div ref={containerRef} className="card relative w-full h-full min-h-0 overflow-hidden">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-text-secondary">
          Sentiment Heat-Sphere
        </h2>
        <button className="text-text-muted hover:text-text-primary p-1">
          <MoreHorizontal size={16} />
        </button>
      </div>
      <svg ref={svgRef} className="w-full h-[calc(100%-28px)]" />

      {/* Hover tooltip */}
      {tooltip && (
        <div
          className="absolute z-50 bg-surface-alt border border-bullish/30 rounded-lg px-3 py-2.5 pointer-events-none shadow-lg max-w-[260px]"
          style={{
            left: Math.min(tooltip.x + 12, (containerRef.current?.clientWidth || 600) - 270),
            top: tooltip.y - 10,
          }}
        >
          <p className="font-mono font-semibold text-sm text-text-primary truncate">
            {tooltip.sector.label}
          </p>
          <p className="text-xs mt-1">
            <span className={tooltip.sector.sentiment > 0.5 ? "text-bullish" : tooltip.sector.sentiment < 0.45 ? "text-bearish" : "text-neutral"}>
              Sentiment: {Math.round(tooltip.sector.sentiment * 100)}%
            </span>
            <span className="text-text-muted ml-2">Vol: {tooltip.sector.volume.toLocaleString()}</span>
          </p>
          {tooltip.sector.keywords.length > 0 && (
            <p className="text-xs text-text-muted mt-1 truncate">
              {tooltip.sector.keywords.join(", ")}
            </p>
          )}
          <p className="text-xs text-text-muted mt-1">
            Sources: {tooltip.sector.sector}
          </p>
          <p className="text-xs text-accent-blue mt-1">Click for details</p>
        </div>
      )}

      {/* Detail modal */}
      {modal && (
        <div
          className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center p-4"
          onClick={() => setModal(null)}
        >
          <div
            className="bg-surface border border-border rounded-xl max-w-lg w-full max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-start justify-between mb-4">
              <div>
                <h3 className="text-lg font-semibold text-text-primary">
                  {modal.label}
                </h3>
                <p className="text-sm text-text-muted mt-0.5">
                  Entity: <span className="font-mono text-text-secondary">{modal.id}</span>
                </p>
              </div>
              <button
                onClick={() => setModal(null)}
                className="text-text-muted hover:text-text-primary p-1"
              >
                <X size={18} />
              </button>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-3 mb-4">
              <div className="bg-surface-alt rounded-lg p-3 text-center">
                <p className="text-xs text-text-muted">Sentiment</p>
                <p className={`text-xl font-mono font-semibold ${
                  modal.sentiment > 0.6 ? "text-bullish" : modal.sentiment < 0.4 ? "text-bearish" : "text-neutral"
                }`}>
                  {Math.round(modal.sentiment * 100)}%
                </p>
              </div>
              <div className="bg-surface-alt rounded-lg p-3 text-center">
                <p className="text-xs text-text-muted">Mentions</p>
                <p className="text-xl font-mono font-semibold text-text-primary">
                  {modal.volume.toLocaleString()}
                </p>
              </div>
              <div className="bg-surface-alt rounded-lg p-3 text-center">
                <p className="text-xs text-text-muted">Sources</p>
                <p className="text-xl font-mono font-semibold text-accent-blue">
                  {modal.uniqueSources || Object.keys(modal.sources || {}).length}
                </p>
              </div>
            </div>

            {/* Why this bubble */}
            <div className="mb-4">
              <h4 className="text-sm font-semibold text-text-secondary mb-2">
                Why this entity is highlighted
              </h4>
              <p className="text-sm text-text-primary leading-relaxed">
                <span className="font-mono font-medium">{modal.id}</span> has been mentioned{" "}
                <span className="text-bullish font-semibold">{modal.volume.toLocaleString()}</span>{" "}
                times across{" "}
                <span className="text-accent-blue font-semibold">
                  {Object.keys(modal.sources || {}).length} platform{Object.keys(modal.sources || {}).length !== 1 ? "s" : ""}
                </span>{" "}
                ({Object.keys(modal.sources || {}).join(", ")}).
                {modal.sentiment > 0.6
                  ? " Overall sentiment is bullish."
                  : modal.sentiment < 0.4
                  ? " Overall sentiment is bearish — potential risk signal."
                  : " Sentiment is neutral."}
              </p>
            </div>

            {/* Source breakdown */}
            {modal.sources && Object.keys(modal.sources).length > 0 && (
              <div className="mb-4">
                <h4 className="text-sm font-semibold text-text-secondary mb-2">
                  Source Breakdown
                </h4>
                <div className="space-y-1.5">
                  {Object.entries(modal.sources)
                    .sort(([, a], [, b]) => (b as number) - (a as number))
                    .map(([source, count]) => (
                      <div key={source} className="flex items-center gap-2">
                        <span className="text-xs text-text-muted w-24">{source}</span>
                        <div className="flex-1 h-2 bg-surface-alt rounded-full overflow-hidden">
                          <div
                            className="h-full bg-accent-blue rounded-full"
                            style={{ width: `${Math.min(100, ((count as number) / modal.volume) * 100)}%` }}
                          />
                        </div>
                        <span className="text-xs font-mono text-text-secondary w-10 text-right">
                          {(count as number).toLocaleString()}
                        </span>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* Keywords */}
            {modal.keywords.length > 0 && (
              <div className="mb-4">
                <h4 className="text-sm font-semibold text-text-secondary mb-2">Keywords</h4>
                <div className="flex flex-wrap gap-1.5">
                  {modal.keywords.map((kw, i) => (
                    <span key={i} className="px-2 py-0.5 bg-surface-alt text-xs text-text-secondary rounded-md">
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Sample sources */}
            {modal.sampleDocs && modal.sampleDocs.length > 0 && (
              <div>
                <h4 className="text-sm font-semibold text-text-secondary mb-2">
                  Source Documents
                </h4>
                <div className="space-y-2">
                  {modal.sampleDocs.map((doc, i) => (
                    <div key={i} className="bg-surface-alt rounded-lg p-3 border border-border">
                      <p className="text-sm text-text-primary leading-snug">
                        {doc.title}
                      </p>
                      <div className="flex items-center gap-2 mt-1.5">
                        <span className="text-xs text-text-muted">{doc.source}</span>
                        {doc.score > 0 && (
                          <span className="text-xs font-mono text-text-muted">
                            score: {doc.score}
                          </span>
                        )}
                        {doc.url && (
                          <a
                            href={doc.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-accent-blue hover:underline flex items-center gap-0.5 ml-auto"
                          >
                            Open <ExternalLink size={10} />
                          </a>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
