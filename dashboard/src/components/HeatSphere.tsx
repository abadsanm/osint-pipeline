"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as d3 from "d3";

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
    const height = container.clientHeight;
    const svg = d3.select(svgRef.current);

    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    // Color scale: bearish (0) → neutral (0.5) → bullish (1)
    const colorScale = d3
      .scaleLinear<string>()
      .domain([0, 0.5, 1])
      .range(["#FF4B2B", "#8B949E", "#00FFC2"]);

    // Radius scale (sqrt for area perception)
    const radiusScale = d3
      .scaleSqrt()
      .domain([0, d3.max(data, (d) => d.volume) || 1])
      .range([20, 60]);

    // Y-position bias based on price change
    const yScale = d3
      .scaleLinear()
      .domain([
        d3.min(data, (d) => d.priceChange24h) || -5,
        d3.max(data, (d) => d.priceChange24h) || 5,
      ])
      .range([height * 0.75, height * 0.25]);

    const nodes = data.map((d) => ({
      ...d,
      r: radiusScale(d.volume),
      x: width / 2 + (Math.random() - 0.5) * width * 0.4,
      y: yScale(d.priceChange24h),
    }));

    const simulation = d3
      .forceSimulation(nodes as any)
      .force("x", d3.forceX(width / 2).strength(0.03))
      .force(
        "y",
        d3.forceY((d: any) => yScale(d.priceChange24h)).strength(0.05)
      )
      .force(
        "collision",
        d3.forceCollide((d: any) => d.r + 3).strength(0.8)
      )
      .force("charge", d3.forceManyBody().strength(-5))
      .alphaDecay(0.01);

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
      .attr("opacity", 0.8)
      .attr("stroke", (d: any) => colorScale(d.sentiment))
      .attr("stroke-width", 1)
      .attr("stroke-opacity", 0.3);

    bubbles
      .append("text")
      .text((d: any) => d.label)
      .attr("text-anchor", "middle")
      .attr("dy", "0.35em")
      .attr("font-family", "Roboto Mono, monospace")
      .attr("font-size", (d: any) => Math.max(10, d.r / 3))
      .attr("font-weight", 600)
      .attr("fill", "#0A0E12")
      .attr("pointer-events", "none");

    bubbles
      .on("mouseenter", function (event: any, d: any) {
        simulation.alphaTarget(0);
        d3.select(this).select("circle").attr("opacity", 1);
        const rect = container.getBoundingClientRect();
        setTooltip({
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
          sector: d,
        });
      })
      .on("mouseleave", function () {
        simulation.alphaTarget(0.01);
        d3.select(this).select("circle").attr("opacity", 0.8);
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
    <div ref={containerRef} className="card-lg relative w-full h-full min-h-[400px]">
      <h2 className="text-sm font-semibold mb-2 text-text-secondary">
        Sentiment Heat-Sphere
      </h2>
      <svg ref={svgRef} className="w-full h-[calc(100%-28px)]" />

      {tooltip && (
        <div
          className="absolute z-50 bg-surface-alt border border-border rounded-card px-3 py-2 pointer-events-none"
          style={{
            left: tooltip.x + 12,
            top: tooltip.y - 10,
          }}
        >
          <p className="font-mono font-medium text-sm text-text-primary">
            {tooltip.sector.label}{" "}
            <span className="text-text-muted">|</span>{" "}
            <span
              className={
                tooltip.sector.sentiment > 0.5
                  ? "text-bullish"
                  : "text-bearish"
              }
            >
              {Math.round(tooltip.sector.sentiment * 100)}%{" "}
              {tooltip.sector.sentiment > 0.5 ? "Pos" : "Neg"}
            </span>
          </p>
          <p className="text-xs text-text-muted mt-1">
            Keywords: {tooltip.sector.keywords.join(", ")}
          </p>
          <p className="text-xs text-text-muted">
            24h:{" "}
            <span
              className={
                tooltip.sector.priceChange24h >= 0
                  ? "text-bullish"
                  : "text-bearish"
              }
            >
              {tooltip.sector.priceChange24h > 0 ? "+" : ""}
              {tooltip.sector.priceChange24h}%
            </span>
          </p>
        </div>
      )}
    </div>
  );
}
