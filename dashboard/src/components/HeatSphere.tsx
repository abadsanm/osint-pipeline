"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as d3 from "d3";
import { MoreHorizontal } from "lucide-react";

interface Sector {
  id: string;
  label: string;
  sector: string;
  sentiment: number;
  volume: number;
  priceChange24h: number;
  keywords: string[];
}

interface HeatSphereProps {
  data: Sector[];
}

export default function HeatSphere({ data }: HeatSphereProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const router = useRouter();
  const [tooltip, setTooltip] = useState<{
    x: number;
    y: number;
    sector: Sector;
  } | null>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current || !data.length) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight - 32;
    const svg = d3.select(svgRef.current);

    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    // Color scale: bearish (0) → neutral (0.5) → bullish (1)
    const colorScale = d3
      .scaleLinear<string>()
      .domain([0, 0.35, 0.5, 0.65, 1])
      .range(["#FF4B2B", "#E88A5A", "#8B949E", "#5CB88A", "#00FFC2"]);

    const maxVol = d3.max(data, (d) => d.volume) || 1;
    const radiusScale = d3
      .scaleSqrt()
      .domain([0, maxVol])
      .range([12, Math.min(width, height) * 0.11]);

    const nodes = data.map((d) => ({
      ...d,
      r: radiusScale(d.volume),
      x: width / 2 + (Math.random() - 0.5) * width * 0.5,
      y: height / 2 + (Math.random() - 0.5) * height * 0.5,
    }));

    const simulation = d3
      .forceSimulation(nodes as any)
      .force("x", d3.forceX(width / 2).strength(0.04))
      .force("y", d3.forceY(height / 2).strength(0.04))
      .force(
        "collision",
        d3.forceCollide((d: any) => d.r + 2).strength(0.9)
      )
      .force("charge", d3.forceManyBody().strength(-3))
      .alphaDecay(0.015);

    const bubbleGroup = svg.append("g");

    const bubbles = bubbleGroup
      .selectAll("g")
      .data(nodes)
      .join("g")
      .style("cursor", "pointer");

    // Bubble circles
    bubbles
      .append("circle")
      .attr("r", (d: any) => d.r)
      .attr("fill", (d: any) => colorScale(d.sentiment))
      .attr("opacity", 0.75)
      .attr("stroke", (d: any) => colorScale(d.sentiment))
      .attr("stroke-width", 1.5)
      .attr("stroke-opacity", 0.4);

    // Label
    bubbles
      .append("text")
      .text((d: any) => d.label)
      .attr("text-anchor", "middle")
      .attr("dy", (d: any) => d.r > 25 ? "-0.2em" : "0.35em")
      .attr("font-family", "Roboto Mono, monospace")
      .attr("font-size", (d: any) => Math.max(8, Math.min(d.r / 2.8, 14)))
      .attr("font-weight", 600)
      .attr("fill", "#0A0E12")
      .attr("pointer-events", "none");

    // Trend arrow (only on larger bubbles)
    bubbles
      .filter((d: any) => d.r > 25)
      .append("text")
      .text((d: any) => d.priceChange24h >= 0 ? "↗" : "↘")
      .attr("text-anchor", "middle")
      .attr("dy", "1.1em")
      .attr("font-size", (d: any) => Math.max(8, d.r / 3.5))
      .attr("fill", (d: any) => d.priceChange24h >= 0 ? "#00FFC2" : "#FF4B2B")
      .attr("opacity", 0.9)
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
        router.push(`/alpha/${d.id}`);
      });

    simulation.on("tick", () => {
      bubbles.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
    });

    return () => {
      simulation.stop();
    };
  }, [data, router]);

  return (
    <div ref={containerRef} className="card relative w-full h-full min-h-[420px]">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-sm font-semibold text-text-secondary">
          Sentiment Heat-Sphere
        </h2>
        <button className="text-text-muted hover:text-text-primary p-1">
          <MoreHorizontal size={16} />
        </button>
      </div>
      <svg ref={svgRef} className="w-full h-[calc(100%-28px)]" />

      {tooltip && (
        <div
          className="absolute z-50 bg-surface-alt border border-bullish/30 rounded-lg px-3 py-2.5 pointer-events-none shadow-lg"
          style={{
            left: Math.min(tooltip.x + 12, (containerRef.current?.clientWidth || 600) - 220),
            top: tooltip.y - 10,
          }}
        >
          <p className="font-mono font-semibold text-sm text-text-primary">
            {tooltip.sector.label}
            <span className="text-neutral mx-1.5">|</span>
            <span className={tooltip.sector.sentiment > 0.5 ? "text-bullish" : "text-bearish"}>
              {Math.round(tooltip.sector.sentiment * 100)}% {tooltip.sector.sentiment > 0.5 ? "Pos" : "Neg"}
            </span>
          </p>
          <p className="text-xs text-text-muted mt-1">
            Keywords: {tooltip.sector.keywords.join(", ")}
          </p>
          <p className="text-xs text-text-muted mt-0.5">
            24h: <span className={tooltip.sector.priceChange24h >= 0 ? "text-bullish" : "text-bearish"}>
              {tooltip.sector.priceChange24h > 0 ? "+" : ""}{tooltip.sector.priceChange24h}%
            </span>
            <span className="ml-2">Vol: {(tooltip.sector.volume / 1000).toFixed(0)}K</span>
          </p>
        </div>
      )}
    </div>
  );
}
