"use client";

import { useState, useEffect, useCallback } from "react";

const API_BASE = "http://localhost:8000/api";

export function useApiData<T>(endpoint: string, fallback: T, refreshMs = 5000): T {
  const [data, setData] = useState<T>(fallback);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (res.ok) {
        const json = await res.json();
        setData(json);
      }
    } catch {
      // API not available, keep fallback
    }
  }, [endpoint]);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, refreshMs);
    return () => clearInterval(interval);
  }, [fetchData, refreshMs]);

  return data;
}

export function useStats() {
  return useApiData(
    "/stats",
    {
      total_ingested: 0,
      total_normalized: 0,
      total_correlated: 0,
      sources: {} as Record<string, number>,
      topic_counts: {} as Record<string, number>,
      entities_tracked: 0,
    },
    3000,
  );
}

export function useSignals(limit = 20, timeframe?: string) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (timeframe) params.set("since", timeframe);
  return useApiData(`/signals?${params}`, [], 3000);
}

export function useSectors(limit = 30, timeframe?: string) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (timeframe) params.set("since", timeframe);
  return useApiData(`/sectors?${params}`, [], 5000);
}

export function useTopics() {
  return useApiData("/topics", {} as Record<string, number>, 5000);
}

export interface PulseChartBarData {
  name: string;
  score: number;
  direction?: "bullish" | "bearish";
  volume?: number;
  sources?: Record<string, number>;
}

export interface PulseChartTimeline {
  date: string;
  sentiment: number;
  sp500: number;
  vix?: number;
  treasury10y?: number;
}

export interface PulseChartsData {
  topSectors: PulseChartBarData[];
  emergingRisks: PulseChartBarData[];
  economicSentiments: PulseChartBarData[];
  macroTimeline: PulseChartTimeline[];
}

const EMPTY_PULSE_CHARTS: PulseChartsData = {
  topSectors: [],
  emergingRisks: [],
  economicSentiments: [],
  macroTimeline: [],
};

export function usePulseCharts(timeframe?: string) {
  const params = new URLSearchParams();
  if (timeframe) params.set("since", timeframe);
  const qs = params.toString();
  const endpoint = qs ? `/pulse/charts?${qs}` : "/pulse/charts";
  return useApiData<PulseChartsData>(endpoint, EMPTY_PULSE_CHARTS, 10000);
}

export function usePredictions(limit = 50, ticker?: string) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (ticker) params.set("ticker", ticker);
  return useApiData(`/predictions?${params}`, [], 3000);
}

export function usePredictionStats() {
  return useApiData("/predictions/stats", {
    total_predictions: 0, total_evaluated: 0, overall_accuracy: 0,
    rolling_accuracy_200: 0,
    by_regime: {} as Record<string, {total: number; correct: number; accuracy: number}>,
    by_horizon: {} as Record<string, {total: number; correct: number; accuracy: number}>,
    by_direction: {} as Record<string, {total: number; correct: number; accuracy: number}>,
    calibration_curve: [] as Array<{bin: string; bin_center: number; avg_predicted_confidence: number; actual_accuracy: number; count: number}>,
    model_agreement: {"4_agree_pct": 0, "3_agree_pct": 0, "2_agree_pct": 0},
  }, 5000);
}

export function useBacktestResults() {
  return useApiData("/backtesting/results", {status: "loading"}, 30000);
}

export function usePredictionOutcomes(limit = 50) {
  return useApiData(`/predictions/outcomes?limit=${limit}`, [], 5000);
}

export function useLiveBacktestResults(minConfidence = 50) {
  return useApiData(`/backtesting/live?min_confidence=${minConfidence / 100}`, { status: "loading" }, 30000);
}
