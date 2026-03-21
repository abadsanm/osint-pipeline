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

export function useSignals(limit = 20) {
  return useApiData("/signals?limit=" + limit, [], 3000);
}

export function useSectors(limit = 30) {
  return useApiData("/sectors?limit=" + limit, [], 5000);
}

export function useTopics() {
  return useApiData("/topics", {} as Record<string, number>, 5000);
}
