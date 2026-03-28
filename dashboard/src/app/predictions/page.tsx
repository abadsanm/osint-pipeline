"use client";

import { useState, useMemo, useCallback, useEffect } from "react";
import Header from "@/components/Header";
import SourceTicker from "@/components/SourceTicker";
import InfoTooltip from "@/components/InfoTooltip";
import { usePredictions, usePredictionStats } from "@/hooks/useApiData";
import { Check, X, Plus, Trash2, RefreshCw, Zap } from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  ScatterChart,
  Scatter,
  BarChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Cell,
  ZAxis,
} from "recharts";
import mockPredictions from "../../../data/predictions.json";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MockPrediction {
  prediction_id: string;
  ticker: string;
  direction: "bullish" | "bearish" | "neutral";
  magnitude: number;
  confidence: number;
  raw_score: number;
  time_horizon_hours: number;
  decay_rate: number;
  regime: string;
  contributing_signals: Array<{ source: string; signal_type: string; value: number; weight: number }>;
  model_version: string;
  created_at: string;
  expires_at: string;
  outcome: "correct" | "incorrect" | null;
}

interface TableRow {
  id: string;
  ticker: string;
  direction: "bullish" | "bearish" | "neutral";
  confidence: number;
  horizon: string;
  regime: string;
  agreement: number;
  totalModels: number;
  score: number;
  timestamp: string;
  outcome: "correct" | "incorrect" | "pending";
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function normalizeRow(p: MockPrediction): TableRow {
  const signals = p.contributing_signals ?? [];
  const totalModels = signals.length;
  const agreeing = signals.filter((s) => s.signal_type === p.direction).length;
  return {
    id: p.prediction_id,
    ticker: p.ticker,
    direction: p.direction,
    confidence: p.confidence ?? 0,
    horizon: `${p.time_horizon_hours ?? 0}h`,
    regime: (p.regime ?? "unknown").replace(/_/g, " "),
    agreement: agreeing,
    totalModels: Math.max(totalModels, 4),
    score: Math.round(Math.abs(p.raw_score ?? 0) * 100),
    timestamp: p.created_at,
    outcome: p.outcome ?? "pending",
  };
}

function formatTime(ts: string) {
  const d = new Date(ts);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

const tooltipStyle = {
  backgroundColor: "#1C2128",
  border: "1px solid #21262D",
  borderRadius: 8,
  fontSize: 12,
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PredictionsPage() {
  const [tickerFilter, setTickerFilter] = useState("");
  const [trackedTickers, setTrackedTickers] = useState<string[]>([]);
  const [newTicker, setNewTicker] = useState("");
  const [addingTicker, setAddingTicker] = useState(false);
  const hookPredictions = usePredictions(50, tickerFilter || undefined) as MockPrediction[];
  const stats = usePredictionStats();

  // Direct fetch fallback — ensures live data even if hook has timing issues
  const [directPredictions, setDirectPredictions] = useState<MockPrediction[]>([]);
  useEffect(() => {
    const fetchDirect = async () => {
      try {
        const res = await fetch("http://localhost:8000/api/predictions?limit=50");
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data) && data.length > 0) {
            setDirectPredictions(data);
          }
        }
      } catch { /* silent */ }
    };
    fetchDirect();
    const interval = setInterval(fetchDirect, 5000);
    return () => clearInterval(interval);
  }, []);

  const apiPredictions = hookPredictions.length > 0 ? hookPredictions : directPredictions;

  // Load tracked tickers from settings on mount
  useEffect(() => {
    fetch("http://localhost:8000/api/settings")
      .then((r) => r.json())
      .then((d) => {
        const wl = d.predict_tickers ?? d.watchlist ?? [];
        setTrackedTickers(wl);
      })
      .catch(() => {});
  }, []);

  const addTicker = useCallback(async () => {
    const ticker = newTicker.trim().toUpperCase();
    if (!ticker || trackedTickers.includes(ticker)) {
      setNewTicker("");
      return;
    }
    setAddingTicker(true);
    const updated = [...trackedTickers, ticker];
    setTrackedTickers(updated);
    setNewTicker("");
    try {
      await fetch("http://localhost:8000/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ predict_tickers: updated }),
      });
      // Trigger enrichment for the new ticker
      await fetch(`http://localhost:8000/api/enrich/${ticker}`, { method: "POST" });
    } catch {}
    setAddingTicker(false);
  }, [newTicker, trackedTickers]);

  const removeTicker = useCallback(async (ticker: string) => {
    const updated = trackedTickers.filter((t) => t !== ticker);
    setTrackedTickers(updated);
    try {
      await fetch("http://localhost:8000/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ predict_tickers: updated }),
      });
    } catch {}
  }, [trackedTickers]);

  const [generating, setGenerating] = useState(false);
  const [generateResult, setGenerateResult] = useState<string | null>(null);

  const generatePredictions = useCallback(async () => {
    setGenerating(true);
    setGenerateResult(null);
    try {
      const res = await fetch("http://localhost:8000/api/predictions/generate", { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setGenerateResult(`Generated ${data.predicted}/${data.total} predictions`);
      } else {
        setGenerateResult("Failed to generate predictions");
      }
    } catch {
      setGenerateResult("API unavailable");
    }
    setGenerating(false);
  }, []);

  const generateSingle = useCallback(async (ticker: string) => {
    try {
      await fetch(`http://localhost:8000/api/predict/${ticker}`, { method: "POST" });
    } catch {}
  }, []);

  const isLiveData = Array.isArray(apiPredictions) && apiPredictions.length > 0;
  const rawPredictions: MockPrediction[] = isLiveData
    ? apiPredictions
    : (mockPredictions as unknown as MockPrediction[]);

  const rows: TableRow[] = useMemo(() => rawPredictions.map(normalizeRow), [rawPredictions]);

  const filteredRows = tickerFilter
    ? rows.filter((r) => r.ticker.toLowerCase().includes(tickerFilter.toLowerCase()))
    : rows;

  // ── Compute stats from mock predictions when API stats are empty ──
  const derivedStats = useMemo(() => {
    const hasLiveStats = stats.total_predictions > 0;
    if (hasLiveStats) return null;
    if (rawPredictions.length === 0) return null;

    const evaluated = rawPredictions.filter((p) => p.outcome !== null);
    const correct = evaluated.filter((p) => p.outcome === "correct");
    const total = evaluated.length;
    const accuracy = total > 0 ? correct.length / total : 0;

    // By horizon
    const byHorizon: Record<string, { total: number; correct: number }> = {};
    for (const p of evaluated) {
      const h = String(p.time_horizon_hours);
      if (!byHorizon[h]) byHorizon[h] = { total: 0, correct: 0 };
      byHorizon[h].total++;
      if (p.outcome === "correct") byHorizon[h].correct++;
    }

    // By regime
    const byRegime: Record<string, { total: number; correct: number }> = {};
    for (const p of evaluated) {
      const r = p.regime || "unknown";
      if (!byRegime[r]) byRegime[r] = { total: 0, correct: 0 };
      byRegime[r].total++;
      if (p.outcome === "correct") byRegime[r].correct++;
    }

    // Model agreement
    let agree4 = 0, agree3 = 0, agree2 = 0;
    for (const p of rawPredictions) {
      const signals = p.contributing_signals ?? [];
      if (signals.length === 0) { agree2++; continue; }
      const dirs = signals.map((s) => s.signal_type);
      const counts: Record<string, number> = {};
      for (const d of dirs) counts[d] = (counts[d] || 0) + 1;
      const max = Math.max(...Object.values(counts), 0);
      if (max >= 4) agree4++;
      else if (max >= 3) agree3++;
      else agree2++;
    }
    const agreeTotal = rawPredictions.length || 1; // avoid division by zero

    // Calibration from predictions
    const bins: Record<number, { predSum: number; correctCount: number; count: number }> = {};
    for (const p of evaluated) {
      const conf = p.confidence ?? 0;
      const bin = Math.floor(conf * 10);
      if (!bins[bin]) bins[bin] = { predSum: 0, correctCount: 0, count: 0 };
      bins[bin].predSum += conf;
      bins[bin].count++;
      if (p.outcome === "correct") bins[bin].correctCount++;
    }
    const calibration = Object.entries(bins)
      .map(([b, v]) => ({
        avg_predicted_confidence: v.predSum / v.count,
        actual_accuracy: v.correctCount / v.count,
        count: v.count,
      }))
      .sort((a, b) => a.avg_predicted_confidence - b.avg_predicted_confidence);

    return {
      total_predictions: rawPredictions.length,
      overall_accuracy: accuracy,
      rolling_accuracy_200: accuracy,
      by_horizon: Object.fromEntries(
        Object.entries(byHorizon).map(([h, v]) => [h, { accuracy: v.total > 0 ? v.correct / v.total : 0 }]),
      ),
      by_regime: Object.fromEntries(
        Object.entries(byRegime).map(([r, v]) => [r, { accuracy: v.total > 0 ? v.correct / v.total : 0 }]),
      ),
      model_agreement: {
        "4_agree_pct": agreeTotal > 0 ? (agree4 / agreeTotal) * 100 : 0,
        "3_agree_pct": agreeTotal > 0 ? (agree3 / agreeTotal) * 100 : 0,
        "2_agree_pct": agreeTotal > 0 ? (agree2 / agreeTotal) * 100 : 0,
      },
      calibration_curve: calibration,
    };
  }, [rawPredictions, stats.total_predictions]);

  // Use live stats if available, otherwise derived from mock data
  const effectiveStats = derivedStats ?? stats;

  // ── Safe number helper — prevents NaN from reaching DOM ──
  function safe(v: unknown, fallback = 0): number {
    const n = Number(v);
    return isFinite(n) ? n : fallback;
  }

  // ── Stat card values ──
  const overallAccuracy = safe(effectiveStats.overall_accuracy);
  const rollingAccuracy = safe(effectiveStats.rolling_accuracy_200);

  const calibrationError = useMemo(() => {
    const curve = effectiveStats.calibration_curve;
    if (!curve || curve.length === 0) return null;
    const totalError = curve.reduce(
      (sum: number, pt: any) => sum + Math.abs(pt.avg_predicted_confidence - pt.actual_accuracy),
      0,
    );
    return totalError / curve.length;
  }, [effectiveStats.calibration_curve]);

  const rollingDelta = rollingAccuracy - overallAccuracy;

  // ── Chart data ──
  const horizonData = useMemo(() => {
    const bh = effectiveStats.by_horizon ?? {};
    return Object.entries(bh).map(([h, v]: [string, any]) => ({
      name: `${h}h`,
      accuracy: +safe(safe(v?.accuracy) * 100).toFixed(1),
    }));
  }, [effectiveStats.by_horizon]);

  const regimeData = useMemo(() => {
    const br = effectiveStats.by_regime ?? {};
    return Object.entries(br).map(([r, v]: [string, any]) => ({
      name: r.replace(/_/g, " "),
      accuracy: +safe(safe(v?.accuracy) * 100).toFixed(1),
    }));
  }, [effectiveStats.by_regime]);

  const agreementData = useMemo(() => {
    const ma = effectiveStats.model_agreement ?? {};
    return {
      four: safe(ma["4_agree_pct"]),
      three: safe(ma["3_agree_pct"]),
      two: safe(ma["2_agree_pct"]),
    };
  }, [effectiveStats.model_agreement]);

  const calibrationData = useMemo(() => {
    return (effectiveStats.calibration_curve ?? []).map((pt: any) => ({
      x: +safe(safe(pt?.avg_predicted_confidence) * 100).toFixed(1),
      y: +safe(safe(pt?.actual_accuracy) * 100).toFixed(1),
      count: safe(pt?.count),
    }));
  }, [effectiveStats.calibration_curve]);

  // ── Per-ticker accuracy data (all tracked + model tickers) ──
  const perTickerData = useMemo(() => {
    // Gather all tickers: from tracked list + from predictions
    const predictionTickers = new Set(rawPredictions.map((p) => p.ticker));
    const allTickers = new Set([...trackedTickers, ...predictionTickers]);

    return Array.from(allTickers)
      .map((ticker) => {
        const tickerPreds = rawPredictions.filter((p) => p.ticker === ticker);
        const evaluated = tickerPreds.filter((p) => p.outcome !== null);
        const correct = evaluated.filter((p) => p.outcome === "correct").length;
        const total = evaluated.length;
        const accuracy = total > 0 ? (correct / total) * 100 : 0;
        const avgConf = tickerPreds.length > 0
          ? (tickerPreds.reduce((s, p) => s + (p.confidence ?? 0), 0) / tickerPreds.length) * 100
          : 0;
        const predictions = tickerPreds.length;
        const isTracked = trackedTickers.includes(ticker);
        const safeAcc = isFinite(accuracy) ? +accuracy.toFixed(1) : 0;
        const safeConf = isFinite(avgConf) ? +avgConf.toFixed(1) : 0;
        return { ticker, accuracy: safeAcc, avgConf: safeConf, predictions, evaluated: total, isTracked };
      })
      .sort((a, b) => b.predictions - a.predictions);
  }, [rawPredictions, trackedTickers]);

  // ── Color helpers ──
  function accuracyColor(acc: number) {
    if (acc > 0.6) return "text-bullish";
    if (acc < 0.5) return "text-bearish";
    return "text-text-secondary";
  }

  function calibrationErrorColor(err: number) {
    if (err < 0.05) return "text-bullish";
    if (err > 0.10) return "text-bearish";
    return "text-text-secondary";
  }

  function confidenceColor(conf: number) {
    if (conf > 0.70) return "text-bullish";
    if (conf >= 0.50) return "text-text-secondary";
    return "text-bearish";
  }

  function barFill(acc: number) {
    if (acc > 60) return "#00FFC2";
    if (acc >= 50) return "#FAAB35";
    return "#8B949E";
  }

  function deltaPrefix(val: number) {
    return val >= 0 ? "\u2191" : "\u2193";
  }

  function deltaColor(val: number) {
    if (val > 0) return "text-bullish";
    if (val < 0) return "text-bearish";
    return "text-text-secondary";
  }

  return (
    <div className="min-h-screen bg-base">
      <Header title="Predictions Monitor" />
      <div className="md:ml-12">
        <SourceTicker />
        <main className="px-4 pb-24 md:pb-8 pt-2 max-w-[1600px] mx-auto space-y-4">

          {/* ──── Data source indicator ──── */}
          <div className="flex items-center gap-2 text-[11px]">
            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full font-mono ${isLiveData ? "bg-bullish/15 text-bullish" : "bg-neutral/15 text-text-muted"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${isLiveData ? "bg-bullish animate-pulse" : "bg-neutral"}`} />
              {isLiveData ? `Live — ${rawPredictions.length} predictions` : "Demo data — API not connected"}
            </span>
            {isLiveData && filteredRows.filter(r => r.outcome === "pending").length === filteredRows.length && (
              <span className="text-text-muted">All predictions pending — accuracy stats populate after predictions expire and get evaluated</span>
            )}
            {!isLiveData && (
              <span className="text-text-muted">Start the API server and click &quot;Predict All&quot; to generate live predictions</span>
            )}
          </div>

          {/* ──── Stat Cards ──── */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {/* Total Predictions */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Total Predictions</p>
                <InfoTooltip title="Total Predictions">
                  <p>Total number of directional predictions generated by the ensemble model across all tickers and time horizons. Each prediction combines rule-based scoring, XGBoost classification, and time-series forecasting.</p>
                </InfoTooltip>
              </div>
              <p className="font-mono text-[28px] font-semibold text-text-primary">
                {(effectiveStats as any).total_predictions || filteredRows.length}
              </p>
            </div>

            {/* Overall Accuracy / Avg Confidence */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">
                  {overallAccuracy ? "Overall Accuracy" : "Avg Confidence"}
                </p>
                <InfoTooltip title={overallAccuracy ? "Overall Accuracy" : "Avg Confidence"}>
                  <p>{overallAccuracy ? "Percentage of evaluated predictions matching actual direction." : "Average model confidence across pending predictions. Accuracy will appear after predictions are evaluated."}</p>
                </InfoTooltip>
              </div>
              {overallAccuracy ? (
                <p className={`font-mono text-[28px] font-semibold ${accuracyColor(overallAccuracy)}`}>
                  {(overallAccuracy * 100).toFixed(1)}%
                </p>
              ) : (
                <p className="font-mono text-[28px] font-semibold text-accent-blue">
                  {filteredRows.length > 0
                    ? `${(filteredRows.reduce((s, r) => s + r.confidence, 0) / filteredRows.length * 100).toFixed(1)}%`
                    : "--"}
                </p>
              )}
            </div>

            {/* Rolling Accuracy / Direction Breakdown */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">
                  {rollingAccuracy ? "Rolling Accuracy (200)" : "Direction Split"}
                </p>
                <InfoTooltip title={rollingAccuracy ? "Rolling Accuracy" : "Direction Split"}>
                  <p>{rollingAccuracy ? "Accuracy over the most recent 200 evaluated predictions." : "Breakdown of bullish vs bearish vs neutral predictions."}</p>
                </InfoTooltip>
              </div>
              {rollingAccuracy ? (
                <p className={`font-mono text-[28px] font-semibold ${accuracyColor(rollingAccuracy)}`}>
                  {(rollingAccuracy * 100).toFixed(1)}%
                </p>
              ) : (
                <div className="flex items-center gap-3 mt-1">
                  <span className="font-mono text-sm text-bullish">{filteredRows.filter(r => r.direction === "bullish").length} bull</span>
                  <span className="font-mono text-sm text-bearish">{filteredRows.filter(r => r.direction === "bearish").length} bear</span>
                  <span className="font-mono text-sm text-text-muted">{filteredRows.filter(r => r.direction === "neutral").length} neutral</span>
                </div>
              )}
              {rollingAccuracy > 0 && overallAccuracy > 0 && isFinite(rollingDelta) && (
                <p className={`text-xs font-mono mt-0.5 ${deltaColor(rollingDelta)}`}>
                  {deltaPrefix(rollingDelta)}{Math.abs(rollingDelta * 100).toFixed(1)}%
                </p>
              )}
            </div>

            {/* Calibration Error */}
            <div className="bg-surface border border-border rounded-card p-card-padding">
              <div className="flex items-center justify-between mb-1">
                <p className="text-text-muted text-xs uppercase tracking-wide">Calibration Error</p>
                <InfoTooltip title="Calibration Error">
                  <p>Average absolute difference between stated confidence and actual accuracy. Below 0.05 means the model is well-calibrated — when it says 70% confident, it&apos;s right about 70% of the time. Above 0.10 suggests the model needs recalibration via isotonic regression.</p>
                </InfoTooltip>
              </div>
              {calibrationError !== null ? (
                <p className={`font-mono text-[28px] font-semibold ${calibrationErrorColor(calibrationError)}`}>
                  {(calibrationError * 100).toFixed(1)}%
                </p>
              ) : (
                <div>
                  <p className="font-mono text-[28px] font-semibold text-text-muted">{filteredRows.filter(r => r.outcome === "pending").length}</p>
                  <p className="text-[10px] text-text-muted">pending evaluation</p>
                </div>
              )}
            </div>
          </div>

          {/* ──── Tracked Tickers ──── */}
          <div className="bg-surface border border-border rounded-card p-card-padding">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <h2 className="text-text-primary font-semibold text-sm">Tracked Tickers</h2>
                <InfoTooltip title="Tracked Tickers">
                  <p>Add stock tickers to track predictions for. The prediction engine will generate directional forecasts for these tickers using sentiment, technicals, and cross-correlated OSINT signals. New tickers are automatically enriched with news data when added.</p>
                </InfoTooltip>
              </div>
              <button
                onClick={generatePredictions}
                disabled={generating || trackedTickers.length === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded bg-bullish/15 text-bullish border border-bullish/30 hover:bg-bullish/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                title="Generate predictions for all tracked tickers using technicals"
              >
                {generating ? <RefreshCw size={12} className="animate-spin" /> : <Zap size={12} />}
                Predict All
              </button>
              {generateResult && (
                <span className="text-[11px] text-text-muted">{generateResult}</span>
              )}
              <form
                onSubmit={(e) => { e.preventDefault(); addTicker(); }}
                className="flex items-center gap-2"
              >
                <input
                  type="text"
                  placeholder="Add ticker (e.g. AAPL)"
                  value={newTicker}
                  onChange={(e) => setNewTicker(e.target.value.toUpperCase())}
                  maxLength={5}
                  className="bg-surface-alt border border-border rounded px-3 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue w-36 uppercase"
                />
                <button
                  type="submit"
                  disabled={!newTicker.trim() || addingTicker}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded bg-accent-blue/15 text-accent-blue border border-accent-blue/30 hover:bg-accent-blue/25 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  {addingTicker ? <RefreshCw size={12} className="animate-spin" /> : <Plus size={12} />}
                  Add
                </button>
              </form>
            </div>
            {trackedTickers.length === 0 ? (
              <p className="text-text-muted text-xs">No tickers tracked yet. Add tickers above to generate predictions.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {trackedTickers.map((ticker) => (
                  <span
                    key={ticker}
                    className="inline-flex items-center gap-1.5 pl-2.5 pr-1 py-1 rounded-md bg-surface-alt border border-border text-xs font-mono font-medium text-text-primary group"
                  >
                    <a
                      href={`/alpha/${ticker}`}
                      className="hover:text-accent-blue transition-colors"
                    >
                      {ticker}
                    </a>
                    <button
                      onClick={() => generateSingle(ticker)}
                      className="p-0.5 rounded hover:bg-bullish/20 text-text-muted hover:text-bullish transition-colors opacity-0 group-hover:opacity-100"
                      title={`Predict ${ticker}`}
                    >
                      <Zap size={11} />
                    </button>
                    <button
                      onClick={() => removeTicker(ticker)}
                      className="p-0.5 rounded hover:bg-bearish/20 text-text-muted hover:text-bearish transition-colors opacity-0 group-hover:opacity-100"
                      title={`Remove ${ticker}`}
                    >
                      <Trash2 size={11} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* ──── Predictions Table ──── */}
          <div className="bg-surface border border-border rounded-card p-card-padding-lg">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <h2 className="text-text-primary font-semibold text-sm">Predictions</h2>
                <InfoTooltip title="Predictions Table">
                  <p>Live stream of predictions from the ensemble model. Direction shows the predicted price movement. Confidence is the calibrated probability. Agreement shows how many of the 4 models (rule-based, XGBoost, Prophet, LSTM) agreed on the direction — higher agreement generally means higher accuracy.</p>
                </InfoTooltip>
              </div>
              <input
                type="text"
                placeholder="Filter by ticker..."
                value={tickerFilter}
                onChange={(e) => setTickerFilter(e.target.value)}
                className="bg-surface-alt border border-border rounded px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue w-40"
              />
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-text-muted uppercase tracking-wide border-b border-border">
                    <th className="text-left py-2 px-2">Ticker</th>
                    <th className="text-left py-2 px-2">Direction</th>
                    <th className="text-right py-2 px-2">Confidence</th>
                    <th className="text-left py-2 px-2">Horizon</th>
                    <th className="text-left py-2 px-2">Regime</th>
                    <th className="text-center py-2 px-2">Agreement</th>
                    <th className="text-right py-2 px-2">Score</th>
                    <th className="text-left py-2 px-2">Time</th>
                    <th className="text-center py-2 px-2">Outcome</th>
                    {isLiveData && <th className="text-center py-2 px-2 w-8"></th>}
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((p, i) => (
                    <tr
                      key={p.id}
                      className={i % 2 === 0 ? "bg-surface" : "bg-surface-alt"}
                    >
                      <td className="py-2 px-2 font-mono font-semibold text-text-primary">{p.ticker}</td>
                      <td className="py-2 px-2">
                        <span
                          className={`inline-block text-[10px] font-semibold font-mono px-2 py-0.5 rounded ${
                            p.direction === "bullish"
                              ? "bg-bullish-muted text-bullish"
                              : p.direction === "bearish"
                                ? "bg-bearish-muted text-bearish"
                                : "bg-neutral/15 text-neutral"
                          }`}
                        >
                          {p.direction}
                        </span>
                      </td>
                      <td className={`py-2 px-2 text-right font-mono ${confidenceColor(p.confidence)}`}>
                        {isFinite(p.confidence) ? `${(p.confidence * 100).toFixed(0)}%` : "--"}
                      </td>
                      <td className="py-2 px-2 font-mono text-text-secondary">{p.horizon}</td>
                      <td className="py-2 px-2 text-text-secondary capitalize">{p.regime}</td>
                      <td className="py-2 px-2 text-center font-mono text-accent-blue">
                        {p.agreement}/{p.totalModels}
                      </td>
                      <td className="py-2 px-2 text-right font-mono text-text-primary">{p.score}</td>
                      <td className="py-2 px-2 text-text-muted">{formatTime(p.timestamp)}</td>
                      <td className="py-2 px-2 text-center">
                        {p.outcome === "correct" && <Check size={14} className="inline text-bullish" />}
                        {p.outcome === "incorrect" && <X size={14} className="inline text-bearish" />}
                        {p.outcome === "pending" && <span className="text-text-muted">&mdash;</span>}
                      </td>
                      {isLiveData && (
                        <td className="py-2 px-2 text-center">
                          <button
                            onClick={() => removeTicker(p.ticker)}
                            className="p-0.5 rounded hover:bg-bearish/20 text-text-muted hover:text-bearish transition-colors opacity-50 hover:opacity-100"
                            title={`Remove ${p.ticker}`}
                          >
                            <Trash2 size={12} />
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ──── Calibration Curve + Accuracy by Horizon ──── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Calibration Curve */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Calibration Curve</h2>
                <InfoTooltip title="Calibration Curve">
                  <p>Compares predicted confidence (X) against actual accuracy (Y). Perfect calibration follows the diagonal line. Points above the line mean the model is under-confident (actual accuracy exceeds stated confidence). Points below mean over-confident. Well-calibrated models make better risk-adjusted decisions.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={calibrationData} margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis
                      type="number"
                      dataKey="x"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                      label={{ value: "Predicted Confidence %", position: "bottom", fill: "#7D8590", fontSize: 11, offset: 0 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="y"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                      label={{ value: "Actual Accuracy %", angle: -90, position: "insideLeft", fill: "#7D8590", fontSize: 11 }}
                    />
                    <ZAxis type="number" dataKey="count" range={[40, 400]} />
                    <ReferenceLine
                      segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]}
                      stroke="#7D8590"
                      strokeDasharray="4 4"
                    />
                    <Tooltip contentStyle={tooltipStyle} />
                    <Scatter dataKey="y" fill="#58A6FF" />
                    <Line type="monotone" dataKey="y" stroke="#58A6FF" strokeWidth={1.5} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Accuracy by Horizon */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy by Horizon</h2>
                <InfoTooltip title="Accuracy by Horizon">
                  <p>How accuracy varies by prediction time horizon. Shorter horizons (1h, 4h) capture momentum but are noisier. Longer horizons (72h, 168h) are more stable but less actionable. The sweet spot is typically 24h for daily trading decisions. Green bars (&gt;60%) indicate reliable horizons.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={horizonData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#21262D" strokeOpacity={0.5} />
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number) => [`${value}%`, "Accuracy"]}
                    />
                    <Bar dataKey="accuracy" barSize={24} radius={[3, 3, 0, 0]}>
                      {horizonData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>

          {/* ──── Per-Ticker Accuracy ──── */}
          {perTickerData.length > 0 && (
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy by Ticker</h2>
                <InfoTooltip title="Accuracy by Ticker">
                  <p>Per-ticker prediction accuracy for all tracked and model tickers. Blue bars show accuracy for tickers with evaluated outcomes. Gray bars indicate tickers with predictions but no outcomes yet. Tickers from your tracked list are marked with a dot. Click a ticker to view its alpha page.</p>
                </InfoTooltip>
              </div>
              <div style={{ height: Math.max(perTickerData.length * 32 + 30, 120) }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={perTickerData}
                    layout="vertical"
                    margin={{ top: 5, right: 60, bottom: 5, left: 60 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#8B949E" strokeOpacity={0.1} horizontal={false} />
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                      tickFormatter={(v: number) => `${v}%`}
                    />
                    <YAxis
                      type="category"
                      dataKey="ticker"
                      tick={{ fontSize: 12, fill: "#E6EDF3", fontFamily: "Roboto Mono, monospace", fontWeight: 500 }}
                      axisLine={false}
                      tickLine={false}
                      width={55}
                    />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(value: number, name: string) => [
                        `${value}%`,
                        name === "accuracy" ? "Accuracy" : "Avg Confidence",
                      ]}
                      labelFormatter={(label: string) => {
                        const t = perTickerData.find((d) => d.ticker === label);
                        return t ? `${label} — ${t.predictions} predictions, ${t.evaluated} evaluated` : label;
                      }}
                    />
                    <Bar dataKey="accuracy" name="accuracy" barSize={12} radius={[0, 3, 3, 0]}>
                      {perTickerData.map((entry, idx) => (
                        <Cell
                          key={idx}
                          fill={entry.evaluated > 0 ? barFill(entry.accuracy) : "#3D444D"}
                          opacity={entry.isTracked ? 1 : 0.6}
                        />
                      ))}
                    </Bar>
                    <Bar dataKey="avgConf" name="avgConf" barSize={12} radius={[0, 3, 3, 0]} opacity={0.3}>
                      {perTickerData.map((entry, idx) => (
                        <Cell key={idx} fill="#58A6FF" />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center gap-5 mt-2 ml-4">
                <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                  <span className="inline-block w-3 h-2 rounded-sm bg-bullish" /> Accuracy (evaluated)
                </span>
                <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                  <span className="inline-block w-3 h-2 rounded-sm bg-accent-blue/30" /> Avg Confidence
                </span>
                <span className="flex items-center gap-1.5 text-[11px] text-text-muted">
                  <span className="inline-block w-3 h-2 rounded-sm bg-[#3D444D]" /> No outcomes yet
                </span>
              </div>
            </div>
          )}

          {/* ──── Accuracy by Regime + Model Agreement ──── */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Accuracy by Regime — horizontal bar chart */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Accuracy by Regime</h2>
                <InfoTooltip title="Accuracy by Regime">
                  <p>Prediction accuracy broken down by market regime. Trending markets are easier to predict than range-bound markets. High-volatility breakouts are hardest. This helps you know when to trust the model&apos;s predictions and when to be cautious.</p>
                </InfoTooltip>
              </div>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={regimeData}
                    layout="vertical"
                    margin={{ top: 0, right: 20, bottom: 0, left: 90 }}
                  >
                    <CartesianGrid
                      strokeDasharray="3 3"
                      stroke="#8B949E"
                      strokeOpacity={0.1}
                      horizontal={false}
                    />
                    <XAxis
                      type="number"
                      domain={[0, 100]}
                      tick={{ fontSize: 11, fill: "#8B949E", fontFamily: "Roboto Mono, monospace" }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      type="category"
                      dataKey="name"
                      tick={{ fontSize: 12, fill: "#E6EDF3" }}
                      axisLine={false}
                      tickLine={false}
                      width={85}
                    />
                    <Tooltip contentStyle={tooltipStyle} formatter={(value: number) => [`${value}%`, "Accuracy"]} />
                    <Bar dataKey="accuracy" barSize={14} radius={[0, 3, 3, 0]} opacity={0.85}>
                      {regimeData.map((entry, idx) => (
                        <Cell key={idx} fill={barFill(entry.accuracy)} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>

            {/* Model Agreement — three stat blocks with dividers */}
            <div className="bg-surface border border-border rounded-card p-card-padding-lg">
              <div className="flex items-center gap-1.5 mb-3">
                <h2 className="text-text-primary font-semibold text-sm">Model Agreement</h2>
                <InfoTooltip title="Model Agreement">
                  <p>Shows how often the 4 prediction models agree. 4/4 agreement (unanimous) historically produces the highest accuracy. When only 2/4 agree, the signal is weak — the model outputs &apos;neutral&apos; with low confidence. Use agreement level as a meta-confidence indicator.</p>
                </InfoTooltip>
              </div>
              <div className="flex items-center justify-center h-56">
                {/* 4/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-bullish">
                    {isFinite(agreementData.four) ? agreementData.four.toFixed(1) : "0.0"}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">4/4 agree</p>
                  <p className="text-xs text-text-muted mt-0.5">Highest accuracy</p>
                </div>

                <div className="w-px h-20 bg-border" />

                {/* 3/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-accent-blue">
                    {isFinite(agreementData.three) ? agreementData.three.toFixed(1) : "0.0"}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">3/4 agree</p>
                  <p className="text-xs text-text-muted mt-0.5">Good confidence</p>
                </div>

                <div className="w-px h-20 bg-border" />

                {/* 2/4 agree */}
                <div className="flex-1 flex flex-col items-center justify-center">
                  <p className="font-mono text-[28px] font-semibold text-neutral">
                    {isFinite(agreementData.two) ? agreementData.two.toFixed(1) : "0.0"}%
                  </p>
                  <p className="text-sm font-medium text-text-primary mt-1">2/4 agree</p>
                  <p className="text-xs text-bearish mt-0.5">Low confidence</p>
                </div>
              </div>
            </div>
          </div>

        </main>
      </div>
    </div>
  );
}
