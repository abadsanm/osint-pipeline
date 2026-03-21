"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import * as d3 from "d3";
import { MoreHorizontal } from "lucide-react";
import AnalysisModal from "./AnalysisModal";

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
  entity_type?: string;
}

// Border ring color and style by entity type
function entityBorderColor(entityType?: string): string {
  switch (entityType) {
    case "TICKER": return "#58A6FF";
    case "COMPANY": return "#8B5CF6";
    case "PRODUCT":
    case "TECHNOLOGY": return "#00FFC2";
    case "PERSON": return "#FBBF24";
    default: return "#21262D";
  }
}

function entityBorderWidth(entityType?: string): number {
  switch (entityType) {
    case "TICKER":
    case "COMPANY":
    case "PRODUCT":
    case "TECHNOLOGY":
    case "PERSON":
      return 2;
    default:
      return 1;
  }
}

function entityBorderDash(entityType?: string): string | null {
  if (entityType === "PRODUCT" || entityType === "TECHNOLOGY") return "4 2";
  return null;
}

function entityBadgeLabel(entityType?: string): string | null {
  switch (entityType) {
    case "TICKER": return "stock";
    case "COMPANY": return "company";
    case "PRODUCT": return "product";
    case "TECHNOLOGY": return "tech";
    case "PERSON": return "person";
    case "LOCATION": return "location";
    case "GROUP": return "group";
    default: return null;
  }
}

// Legend config: maps entity_type to display label and color
const ENTITY_LEGEND: Record<string, { label: string; color: string }> = {
  TICKER: { label: "Ticker", color: "#58A6FF" },
  COMPANY: { label: "Company", color: "#8B5CF6" },
  PRODUCT: { label: "Product", color: "#00FFC2" },
  TECHNOLOGY: { label: "Tech", color: "#00FFC2" },
  PERSON: { label: "Person", color: "#FBBF24" },
};

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

  // Compute which entity types are present for the legend
  const presentEntityTypes = useMemo(() => {
    const types = new Set<string>();
    data.forEach((d) => {
      if (d.entity_type && ENTITY_LEGEND[d.entity_type]) {
        types.add(d.entity_type);
      }
    });
    return Array.from(types);
  }, [data]);

  // Only re-render D3 when the entity list actually changes (not every poll)
  const dataKey = useMemo(
    () => data.map((d) => d.id).sort().join(","),
    [data]
  );

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
      .alphaDecay(0.05); // Fast settle

    const bubbleGroup = svg.append("g");

    const bubbles = bubbleGroup
      .selectAll("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer");

    // Sentiment fill circle
    bubbles
      .append("circle")
      .attr("r", (d: any) => d.r)
      .attr("fill", (d: any) => colorScale(d.sentiment))
      .attr("opacity", 0.75)
      .attr("stroke", "none");

    // Entity type border ring
    bubbles
      .append("circle")
      .attr("class", "entity-border")
      .attr("r", (d: any) => d.r)
      .attr("fill", "none")
      .attr("stroke", (d: any) => entityBorderColor(d.entity_type))
      .attr("stroke-width", (d: any) => entityBorderWidth(d.entity_type))
      .attr("stroke-dasharray", (d: any) => entityBorderDash(d.entity_type))
      .attr("stroke-opacity", 0.9);

    // Entity name label — shift up if badge will be shown
    bubbles
      .append("text")
      .text((d: any) => {
        const raw = d.label || d.id;
        const prefix = d.entity_type === "TICKER" ? "$" : "";
        const label = prefix + raw;
        if (label.length <= 10 || raw.startsWith("r/")) return label;
        const words = raw.split(/\s+/).filter((w: string) => w.length > 2);
        return prefix + (words[0]?.substring(0, 12) || raw.substring(0, 10));
      })
      .attr("text-anchor", "middle")
      .attr("dy", (d: any) => (d.r > 25 && entityBadgeLabel(d.entity_type)) ? "-0.1em" : "0.35em")
      .attr("font-family", "Inter, system-ui, sans-serif")
      .attr("font-size", (d: any) => Math.max(8, Math.min(d.r / 2.5, 14)))
      .attr("font-weight", 600)
      .attr("fill", "#0A0E12")
      .attr("pointer-events", "none");

    // Type badge below label (only for larger bubbles)
    bubbles
      .filter((d: any) => d.r > 25 && !!entityBadgeLabel(d.entity_type))
      .append("text")
      .text((d: any) => entityBadgeLabel(d.entity_type)!)
      .attr("text-anchor", "middle")
      .attr("dy", "1.2em")
      .attr("font-family", "Inter, system-ui, sans-serif")
      .attr("font-size", 8)
      .attr("font-weight", 400)
      .attr("fill", "#7D8590")
      .attr("pointer-events", "none");

    bubbles
      .on("mouseenter", function (event: any, d: any) {
        simulation.stop(); // Freeze all bubbles
        d3.select(this).select("circle").attr("opacity", 1);
        d3.select(this).select(".entity-border").attr("stroke-width", (d: any) => entityBorderWidth(d.entity_type) + 1).attr("stroke-opacity", 1);
        const rect = container.getBoundingClientRect();
        setTooltip({
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
          sector: d,
        });
      })
      .on("mouseleave", function () {
        // Don't restart simulation — keep bubbles frozen
        d3.select(this).select("circle").attr("opacity", 0.75);
        d3.select(this).select(".entity-border").attr("stroke-width", (d: any) => entityBorderWidth(d.entity_type)).attr("stroke-opacity", 0.9);
        setTooltip(null);
      })
      .on("click", (_: any, d: any) => {
        setTooltip(null);
        setModal(d);
      });

    // Stop simulation after settling (bubbles stay in place)
    simulation.on("end", () => {
      simulation.stop();
    });

    simulation.on("tick", () => {
      bubbles.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataKey]);

  return (
    <div ref={containerRef} className="card relative w-full h-full overflow-hidden">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-text-secondary">
          Sentiment Heat-Sphere
        </h2>
        <button className="text-text-muted hover:text-text-primary p-1">
          <MoreHorizontal size={16} />
        </button>
      </div>
      <svg ref={svgRef} className="w-full h-[calc(100%-28px)]" />

      {/* Entity type legend */}
      {presentEntityTypes.length > 0 && (
        <div className="absolute bottom-2 right-2 flex items-center gap-3 px-2.5 py-1.5 rounded-md bg-surface/80 backdrop-blur-sm border border-[#21262D] z-10">
          {presentEntityTypes.map((type) => (
            <div key={type} className="flex items-center gap-1.5">
              <svg width="10" height="10">
                <circle
                  cx="5"
                  cy="5"
                  r="4"
                  fill="none"
                  stroke={ENTITY_LEGEND[type].color}
                  strokeWidth="1.5"
                  strokeDasharray={type === "PRODUCT" || type === "TECHNOLOGY" ? "2 1" : undefined}
                />
              </svg>
              <span className="text-[10px] text-text-muted font-medium">{ENTITY_LEGEND[type].label}</span>
            </div>
          ))}
        </div>
      )}

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

      {/* AI Analysis Modal */}
      {modal && (
        <AnalysisModal
          entity={modal}
          contextType="entity"
          onClose={() => setModal(null)}
        />
      )}
    </div>
  );
}
