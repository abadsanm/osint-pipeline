"use client";

import { useState } from "react";
import Header from "@/components/Header";
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
} from "recharts";

import productData from "../../../data/productFeatures.json";

const channels = ["Amazon", "Twitter/X", "Reddit", "YouTube"];

export default function InnovationPage() {
  const [activeChannel, setActiveChannel] = useState("Amazon");

  return (
    <div className="flex flex-col h-screen">
      <Header title="Product Innovation" />

      <div className="flex-1 p-module-gap-lg overflow-auto">
        {/* Channel slicer */}
        <div className="card mb-module-gap-lg flex items-center gap-1">
          {channels.map((ch) => (
            <button
              key={ch}
              onClick={() => setActiveChannel(ch)}
              className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors duration-150 ${
                activeChannel === ch
                  ? "bg-accent-blue text-base"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              {ch}
            </button>
          ))}
        </div>

        {/* Feature Friction Board */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-module-gap-lg mb-module-gap-lg">
          {/* Criticisms */}
          <div className="card-lg">
            <h3 className="text-sm font-semibold mb-3">
              <span className="text-bearish">Top 5 Criticisms</span>
              <span className="text-text-muted ml-2 font-normal">
                The Pain
              </span>
            </h3>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={productData.criticisms}
                layout="vertical"
                margin={{ top: 0, right: 20, bottom: 0, left: 80 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="#8B949E"
                  strokeOpacity={0.1}
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tick={{ fontSize: 11, fill: "#8B949E" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="feature"
                  tick={{ fontSize: 12, fill: "#E6EDF3" }}
                  axisLine={false}
                  tickLine={false}
                  width={75}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1C2128",
                    border: "1px solid #21262D",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar
                  dataKey="volume"
                  fill="#FF4B2B"
                  radius={[0, 4, 4, 0]}
                  opacity={0.8}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Requested Features */}
          <div className="card-lg">
            <h3 className="text-sm font-semibold mb-3">
              <span className="text-bullish">Top 5 Requested Features</span>
              <span className="text-text-muted ml-2 font-normal">
                The Opportunity
              </span>
            </h3>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={productData.requests}
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
                  tick={{ fontSize: 11, fill: "#8B949E" }}
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
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1C2128",
                    border: "1px solid #21262D",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
                <Bar
                  dataKey="volume"
                  fill="#00FFC2"
                  radius={[0, 4, 4, 0]}
                  opacity={0.8}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Innovation Gap Map */}
        <div className="card-lg">
          <h3 className="text-sm font-semibold text-text-secondary mb-3">
            Innovation Gap Map
          </h3>
          <ResponsiveContainer width="100%" height={400}>
            <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="#8B949E"
                strokeOpacity={0.1}
              />
              <XAxis
                type="number"
                dataKey="satisfaction"
                domain={[0, 1]}
                tick={{ fontSize: 11, fill: "#8B949E" }}
                axisLine={false}
                tickLine={false}
                name="Satisfaction"
              >
                <Label
                  value="Current Satisfaction"
                  position="bottom"
                  offset={0}
                  style={{ fill: "#7D8590", fontSize: 12 }}
                />
              </XAxis>
              <YAxis
                type="number"
                dataKey="importance"
                domain={[0, 1]}
                tick={{ fontSize: 11, fill: "#8B949E" }}
                axisLine={false}
                tickLine={false}
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
                range={[100, 800]}
                name="Volume"
              />
              {/* Innovation Zone highlight */}
              <ReferenceArea
                x1={0}
                x2={0.5}
                y1={0.5}
                y2={1}
                fill="#00FFC2"
                fillOpacity={0.05}
                stroke="#00FFC2"
                strokeOpacity={0.3}
                strokeDasharray="4 4"
                label={{
                  value: "Innovation Zone",
                  position: "insideTopLeft",
                  fill: "#00FFC2",
                  fontSize: 11,
                  fontWeight: 600,
                }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1C2128",
                  border: "1px solid #21262D",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                formatter={(value: any, name: any) => {
                  const n = String(name ?? "");
                  if (n === "Satisfaction" || n === "Importance")
                    return [(value * 100).toFixed(0) + "%", n];
                  return [value?.toLocaleString?.() ?? value, n];
                }}
              />
              <Scatter
                data={productData.gapMap}
                fill="#58A6FF"
                opacity={0.8}
                name="Feature"
              />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
