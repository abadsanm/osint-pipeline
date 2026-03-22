"use client";

import { useState, useEffect, useCallback } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import {
  X,
  Plus,
  Save,
  Check,
  Activity,
  Database,
  Clock,
  Server,
} from "lucide-react";

const API_BASE = "http://localhost:8000/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface AlertThresholds {
  high_confidence: number;
  volume_spike: number;
  convergence_sources: number;
}

interface RefreshIntervals {
  signals: number;
  alpha: number;
  innovation: number;
}

interface Connectors {
  [key: string]: boolean;
}

interface DisplayPrefs {
  theme: string;
  chart_style: string;
  show_source_ticker: boolean;
  default_timeframe: string;
}

interface Settings {
  watchlist: string[];
  alert_thresholds: AlertThresholds;
  refresh_intervals: RefreshIntervals;
  connectors: Connectors;
  display: DisplayPrefs;
}

interface PipelineStats {
  total_ingested: number;
  total_normalized: number;
  total_correlated: number;
  sources: Record<string, number>;
  started_at: string;
  entities_tracked: number;
  topic_counts: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Toast component
// ---------------------------------------------------------------------------

function Toast({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 3000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className="fixed top-4 right-4 z-50 flex items-center gap-2 bg-bullish/20 border border-bullish/40 text-bullish px-4 py-2.5 rounded-card text-sm font-medium animate-in slide-in-from-top">
      <Check size={14} />
      {message}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section card wrapper
// ---------------------------------------------------------------------------

function SectionCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="bg-surface border border-border rounded-card p-card-padding-lg">
      <h2 className="text-base font-semibold text-text-primary mb-4">
        {title}
      </h2>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [stats, setStats] = useState<PipelineStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<string | null>(null);

  // Watchlist local state
  const [newTicker, setNewTicker] = useState("");

  // Fetch settings + stats on mount
  useEffect(() => {
    Promise.all([
      fetch(`${API_BASE}/settings`)
        .then((r) => r.json())
        .catch(() => null),
      fetch(`${API_BASE}/stats`)
        .then((r) => r.json())
        .catch(() => null),
    ]).then(([s, st]) => {
      if (s) setSettings(s);
      if (st) setStats(st);
      setLoading(false);
    });
  }, []);

  const saveSection = useCallback(
    async (partial: Partial<Settings>) => {
      try {
        const res = await fetch(`${API_BASE}/settings`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(partial),
        });
        if (res.ok) {
          const updated = await res.json();
          setSettings(updated);
          setToast("Settings saved");
        }
      } catch {
        setToast("Failed to save settings");
      }
    },
    []
  );

  // Uptime calculation
  const uptime = stats?.started_at
    ? (() => {
        const diff =
          Date.now() - new Date(stats.started_at).getTime();
        const hours = Math.floor(diff / 3_600_000);
        const mins = Math.floor((diff % 3_600_000) / 60_000);
        if (hours > 24) {
          const days = Math.floor(hours / 24);
          return `${days}d ${hours % 24}h`;
        }
        return `${hours}h ${mins}m`;
      })()
    : "--";

  const connectorsRunning = settings
    ? Object.values(settings.connectors).filter(Boolean).length
    : 0;

  if (loading || !settings) {
    return (
      <div className="flex flex-col h-screen">
        <Header title="Settings" />
        <div className="flex-1 flex items-center justify-center">
          <div className="text-text-muted text-sm">Loading settings...</div>
        </div>
        <SourceTicker />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen">
      <Header title="Settings" />

      {toast && (
        <Toast message={toast} onDismiss={() => setToast(null)} />
      )}

      <div className="flex-1 overflow-y-auto p-module-gap-lg pb-20 md:pb-module-gap-lg">
        <div className="max-w-3xl mx-auto flex flex-col gap-module-gap-lg">
          {/* ---- Watchlist ---- */}
          <SectionCard title="Watchlist">
            <div className="flex flex-wrap gap-2 mb-3">
              {settings.watchlist.map((ticker) => (
                <span
                  key={ticker}
                  className="inline-flex items-center gap-1.5 bg-surface-alt border border-border rounded-full px-3 py-1 text-sm font-mono text-text-primary"
                >
                  {ticker}
                  <button
                    onClick={() =>
                      setSettings({
                        ...settings,
                        watchlist: settings.watchlist.filter(
                          (t) => t !== ticker
                        ),
                      })
                    }
                    className="text-text-muted hover:text-bearish transition-colors"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={newTicker}
                onChange={(e) =>
                  setNewTicker(e.target.value.toUpperCase())
                }
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newTicker.trim()) {
                    const t = newTicker.trim();
                    if (!settings.watchlist.includes(t)) {
                      setSettings({
                        ...settings,
                        watchlist: [...settings.watchlist, t],
                      });
                    }
                    setNewTicker("");
                  }
                }}
                placeholder="Add ticker..."
                className="bg-surface-alt border border-border rounded-card px-3 py-1.5 text-sm font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue w-32"
              />
              <button
                onClick={() => {
                  const t = newTicker.trim();
                  if (t && !settings.watchlist.includes(t)) {
                    setSettings({
                      ...settings,
                      watchlist: [...settings.watchlist, t],
                    });
                  }
                  setNewTicker("");
                }}
                className="flex items-center gap-1 bg-surface-alt border border-border rounded-card px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary hover:border-accent-blue transition-colors"
              >
                <Plus size={14} /> Add
              </button>
              <button
                onClick={() =>
                  saveSection({ watchlist: settings.watchlist })
                }
                className="flex items-center gap-1.5 bg-accent-blue/20 border border-accent-blue/40 text-accent-blue rounded-card px-3 py-1.5 text-sm font-medium hover:bg-accent-blue/30 transition-colors ml-auto"
              >
                <Save size={13} /> Save
              </button>
              <button
                onClick={async () => {
                  setToast("Training ML on watchlist...");
                  try {
                    const res = await fetch(`${API_BASE}/ml/train-watchlist`, {
                      method: "POST",
                      headers: { "Content-Type": "application/json" },
                      body: JSON.stringify({ tickers: settings.watchlist }),
                    });
                    if (res.ok) {
                      const data = await res.json();
                      if (data.status === "trained") {
                        setToast(`ML trained: ${data.accuracy_1d ? (data.accuracy_1d * 100).toFixed(0) : '?'}% 1D, ${data.accuracy_5d ? (data.accuracy_5d * 100).toFixed(0) : '?'}% 5D accuracy on ${data.labeled} samples`);
                      } else {
                        setToast(data.message || "Training incomplete");
                      }
                    }
                  } catch {
                    setToast("Training failed — check API");
                  }
                }}
                className="flex items-center gap-1.5 bg-bullish/20 border border-bullish/40 text-bullish rounded-card px-3 py-1.5 text-sm font-medium hover:bg-bullish/30 transition-colors"
              >
                <Activity size={13} /> Train ML
              </button>
            </div>
          </SectionCard>

          {/* ---- Alert Thresholds ---- */}
          <SectionCard title="Alert Thresholds">
            <div className="flex flex-col gap-4">
              {/* High confidence */}
              <div>
                <label className="flex items-center justify-between text-sm text-text-secondary mb-1.5">
                  <span>High confidence trigger</span>
                  <span className="font-mono text-text-primary">
                    {settings.alert_thresholds.high_confidence.toFixed(2)}
                  </span>
                </label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={settings.alert_thresholds.high_confidence}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      alert_thresholds: {
                        ...settings.alert_thresholds,
                        high_confidence: parseFloat(e.target.value),
                      },
                    })
                  }
                  className="w-full accent-accent-blue"
                />
              </div>
              {/* Volume spike */}
              <div>
                <label className="flex items-center justify-between text-sm text-text-secondary mb-1.5">
                  <span>Volume spike trigger</span>
                  <span className="font-mono text-text-primary">
                    {settings.alert_thresholds.volume_spike}
                  </span>
                </label>
                <input
                  type="range"
                  min={1}
                  max={100}
                  step={1}
                  value={settings.alert_thresholds.volume_spike}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      alert_thresholds: {
                        ...settings.alert_thresholds,
                        volume_spike: parseInt(e.target.value),
                      },
                    })
                  }
                  className="w-full accent-accent-blue"
                />
              </div>
              {/* Convergence sources */}
              <div>
                <label className="flex items-center justify-between text-sm text-text-secondary mb-1.5">
                  <span>Convergence sources minimum</span>
                  <span className="font-mono text-text-primary">
                    {settings.alert_thresholds.convergence_sources}
                  </span>
                </label>
                <input
                  type="range"
                  min={1}
                  max={10}
                  step={1}
                  value={
                    settings.alert_thresholds.convergence_sources
                  }
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      alert_thresholds: {
                        ...settings.alert_thresholds,
                        convergence_sources: parseInt(e.target.value),
                      },
                    })
                  }
                  className="w-full accent-accent-blue"
                />
              </div>
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() =>
                  saveSection({
                    alert_thresholds: settings.alert_thresholds,
                  })
                }
                className="flex items-center gap-1.5 bg-accent-blue/20 border border-accent-blue/40 text-accent-blue rounded-card px-3 py-1.5 text-sm font-medium hover:bg-accent-blue/30 transition-colors"
              >
                <Save size={13} /> Save
              </button>
            </div>
          </SectionCard>

          {/* ---- Refresh Intervals ---- */}
          <SectionCard title="Refresh Intervals">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {(
                [
                  ["signals", "Signals Feed"],
                  ["alpha", "Alpha Page"],
                  ["innovation", "Innovation Page"],
                ] as const
              ).map(([key, label]) => (
                <div key={key}>
                  <label className="block text-sm text-text-secondary mb-1.5">
                    {label}
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={5}
                      max={300}
                      value={settings.refresh_intervals[key]}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          refresh_intervals: {
                            ...settings.refresh_intervals,
                            [key]: parseInt(e.target.value) || 15,
                          },
                        })
                      }
                      className="bg-surface-alt border border-border rounded-card px-3 py-1.5 text-sm font-mono text-text-primary focus:outline-none focus:border-accent-blue w-20"
                    />
                    <span className="text-xs text-text-muted">sec</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() =>
                  saveSection({
                    refresh_intervals: settings.refresh_intervals,
                  })
                }
                className="flex items-center gap-1.5 bg-accent-blue/20 border border-accent-blue/40 text-accent-blue rounded-card px-3 py-1.5 text-sm font-medium hover:bg-accent-blue/30 transition-colors"
              >
                <Save size={13} /> Save
              </button>
            </div>
          </SectionCard>

          {/* ---- Connector Status ---- */}
          <SectionCard title="Connector Status">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {Object.entries(settings.connectors).map(
                ([name, enabled]) => (
                  <div
                    key={name}
                    className="flex items-center justify-between bg-surface-alt border border-border rounded-card px-3 py-2"
                  >
                    <div className="flex items-center gap-2">
                      <div
                        className={`w-2 h-2 rounded-full ${
                          enabled ? "bg-bullish" : "bg-bearish"
                        }`}
                      />
                      <span className="text-sm text-text-primary font-mono">
                        {name.replace(/_/g, " ")}
                      </span>
                    </div>
                    <button
                      className={`relative w-9 h-5 rounded-full transition-colors cursor-default ${
                        enabled
                          ? "bg-bullish/30 border border-bullish/40"
                          : "bg-surface border border-border"
                      }`}
                      title="Read-only: connectors are managed as separate processes"
                    >
                      <div
                        className={`absolute top-0.5 w-3.5 h-3.5 rounded-full transition-all ${
                          enabled
                            ? "left-[18px] bg-bullish"
                            : "left-0.5 bg-text-muted"
                        }`}
                      />
                    </button>
                  </div>
                )
              )}
            </div>
            <p className="text-xs text-text-muted mt-3">
              Connectors are managed as separate processes. Toggle
              state is read-only.
            </p>
          </SectionCard>

          {/* ---- Display Preferences ---- */}
          <SectionCard title="Display Preferences">
            <div className="flex flex-col gap-4">
              {/* Theme */}
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">
                  Theme
                </span>
                <div className="flex gap-1">
                  <button className="px-3 py-1 text-xs font-medium rounded-card bg-accent-blue/20 border border-accent-blue/40 text-accent-blue">
                    Dark
                  </button>
                  <button
                    className="px-3 py-1 text-xs font-medium rounded-card bg-surface-alt border border-border text-text-muted cursor-not-allowed opacity-50"
                    disabled
                    title="Light theme coming soon"
                  >
                    Light
                  </button>
                </div>
              </div>

              {/* Chart style */}
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">
                  Chart style
                </span>
                <div className="flex gap-1">
                  {["line", "candle"].map((style) => (
                    <button
                      key={style}
                      onClick={() =>
                        setSettings({
                          ...settings,
                          display: {
                            ...settings.display,
                            chart_style: style,
                          },
                        })
                      }
                      className={`px-3 py-1 text-xs font-medium rounded-card border transition-colors capitalize ${
                        settings.display.chart_style === style
                          ? "bg-accent-blue/20 border-accent-blue/40 text-accent-blue"
                          : "bg-surface-alt border-border text-text-muted hover:text-text-secondary"
                      }`}
                    >
                      {style}
                    </button>
                  ))}
                </div>
              </div>

              {/* Show source ticker */}
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">
                  Show source ticker
                </span>
                <button
                  onClick={() =>
                    setSettings({
                      ...settings,
                      display: {
                        ...settings.display,
                        show_source_ticker:
                          !settings.display.show_source_ticker,
                      },
                    })
                  }
                  className={`relative w-9 h-5 rounded-full transition-colors ${
                    settings.display.show_source_ticker
                      ? "bg-bullish/30 border border-bullish/40"
                      : "bg-surface border border-border"
                  }`}
                >
                  <div
                    className={`absolute top-0.5 w-3.5 h-3.5 rounded-full transition-all ${
                      settings.display.show_source_ticker
                        ? "left-[18px] bg-bullish"
                        : "left-0.5 bg-text-muted"
                    }`}
                  />
                </button>
              </div>

              {/* Default timeframe */}
              <div className="flex items-center justify-between">
                <span className="text-sm text-text-secondary">
                  Default timeframe
                </span>
                <div className="flex gap-1">
                  {["1D", "5D", "1M", "YTD", "1Y"].map((tf) => (
                    <button
                      key={tf}
                      onClick={() =>
                        setSettings({
                          ...settings,
                          display: {
                            ...settings.display,
                            default_timeframe: tf,
                          },
                        })
                      }
                      className={`px-2.5 py-1 text-xs font-mono font-medium rounded-card border transition-colors ${
                        settings.display.default_timeframe === tf
                          ? "bg-accent-blue/20 border-accent-blue/40 text-accent-blue"
                          : "bg-surface-alt border-border text-text-muted hover:text-text-secondary"
                      }`}
                    >
                      {tf}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-4 flex justify-end">
              <button
                onClick={() =>
                  saveSection({ display: settings.display })
                }
                className="flex items-center gap-1.5 bg-accent-blue/20 border border-accent-blue/40 text-accent-blue rounded-card px-3 py-1.5 text-sm font-medium hover:bg-accent-blue/30 transition-colors"
              >
                <Save size={13} /> Save
              </button>
            </div>
          </SectionCard>

          {/* ---- System Info ---- */}
          <SectionCard title="System Info">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-surface-alt border border-border rounded-card p-3 text-center">
                <Activity
                  size={16}
                  className="mx-auto mb-1.5 text-accent-blue"
                />
                <div className="text-lg font-mono font-semibold text-text-primary">
                  {stats?.total_ingested?.toLocaleString() ?? "--"}
                </div>
                <div className="text-xs text-text-muted">
                  Ingested
                </div>
              </div>
              <div className="bg-surface-alt border border-border rounded-card p-3 text-center">
                <Database
                  size={16}
                  className="mx-auto mb-1.5 text-accent-blue"
                />
                <div className="text-lg font-mono font-semibold text-text-primary">
                  {stats?.entities_tracked?.toLocaleString() ?? "--"}
                </div>
                <div className="text-xs text-text-muted">
                  Entities
                </div>
              </div>
              <div className="bg-surface-alt border border-border rounded-card p-3 text-center">
                <Clock
                  size={16}
                  className="mx-auto mb-1.5 text-accent-blue"
                />
                <div className="text-lg font-mono font-semibold text-text-primary">
                  {uptime}
                </div>
                <div className="text-xs text-text-muted">Uptime</div>
              </div>
              <div className="bg-surface-alt border border-border rounded-card p-3 text-center">
                <Server
                  size={16}
                  className="mx-auto mb-1.5 text-accent-blue"
                />
                <div className="text-lg font-mono font-semibold text-text-primary">
                  {connectorsRunning}
                </div>
                <div className="text-xs text-text-muted">
                  Connectors
                </div>
              </div>
            </div>
            {stats && (
              <div className="mt-3 text-xs text-text-muted">
                <span className="font-mono">
                  Normalized: {stats.total_normalized?.toLocaleString() ?? 0}
                </span>
                {" | "}
                <span className="font-mono">
                  Correlated: {stats.total_correlated?.toLocaleString() ?? 0}
                </span>
                {" | "}
                <span className="font-mono">
                  Sources: {Object.keys(stats.sources ?? {}).length}
                </span>
              </div>
            )}
          </SectionCard>
        </div>
      </div>

      <SourceTicker />
    </div>
  );
}
