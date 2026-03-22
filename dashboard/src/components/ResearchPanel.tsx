"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { X, Plus, ChevronDown, ChevronRight, Trash2, RefreshCw, StickyNote, Loader2, Check } from "lucide-react";
import { useRouter } from "next/navigation";

const API_BASE = "http://localhost:8000/api";

interface ResearchItem {
  id: string;
  entity_id: string;
  entity_label: string;
  action: string;
  source_context: string;
  priority: "high" | "medium" | "low";
  status: "active" | "monitoring" | "resolved" | "dismissed";
  notes: string;
  created_at: string;
  updated_at: string;
  last_analysis: string | null;
  alert_count: number;
  sentiment_at_creation: number;
  current_sentiment: number;
  mention_count_at_creation: number;
  current_mention_count: number;
}

interface ResearchPanelProps {
  open: boolean;
  onClose: () => void;
}

const priorityColors: Record<string, string> = {
  high: "bg-bearish",
  medium: "bg-accent-blue",
  low: "bg-neutral",
};

const priorityBorderColors: Record<string, string> = {
  high: "border-bearish/30 text-bearish",
  medium: "border-accent-blue/30 text-accent-blue",
  low: "border-neutral/30 text-neutral",
};

const statusColors: Record<string, string> = {
  active: "bg-bullish/10 text-bullish border-bullish/20",
  monitoring: "bg-accent-blue/10 text-accent-blue border-accent-blue/20",
  resolved: "bg-neutral/10 text-neutral border-neutral/20",
  dismissed: "bg-surface-alt text-text-muted border-border",
};

const statusFlow: Record<string, string> = {
  active: "monitoring",
  monitoring: "resolved",
  resolved: "dismissed",
  dismissed: "active",
};

type FilterTab = "all" | "active" | "monitoring" | "resolved";

export default function ResearchPanel({ open, onClose }: ResearchPanelProps) {
  const router = useRouter();
  const [items, setItems] = useState<ResearchItem[]>([]);
  const [filter, setFilter] = useState<FilterTab>("all");
  const [showAddForm, setShowAddForm] = useState(false);
  const [expandedNotes, setExpandedNotes] = useState<Set<string>>(new Set());
  const [expandedAnalysis, setExpandedAnalysis] = useState<Set<string>>(new Set());
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [reanalyzing, setReanalyzing] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  // Add form state
  const [newEntity, setNewEntity] = useState("");
  const [newAction, setNewAction] = useState("");
  const [newPriority, setNewPriority] = useState<"high" | "medium" | "low">("medium");
  const [submitting, setSubmitting] = useState(false);

  // Inline notes editing
  const [editingNotes, setEditingNotes] = useState<Record<string, string>>({});

  const fetchItems = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/research`);
      if (res.ok) {
        const raw = await res.json();
        const data: ResearchItem[] = Array.isArray(raw) ? raw : raw?.items || [];
        setItems(data);
      }
    } catch {
      // API unavailable
    }
  }, []);

  // Fetch on mount and every 30 seconds
  useEffect(() => {
    fetchItems();
    const interval = setInterval(fetchItems, 30000);
    return () => clearInterval(interval);
  }, [fetchItems]);

  // Refetch when panel opens
  useEffect(() => {
    if (open) fetchItems();
  }, [open, fetchItems]);

  const handleCreate = async () => {
    if (!newEntity.trim()) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entity_id: newEntity.trim().toUpperCase(),
          entity_label: newEntity.trim().toUpperCase(),
          action: newAction.trim() || "Manual tracking",
          priority: newPriority,
        }),
      });
      if (res.ok) {
        setNewEntity("");
        setNewAction("");
        setNewPriority("medium");
        setShowAddForm(false);
        fetchItems();
      }
    } catch {
      // API unavailable
    }
    setSubmitting(false);
  };

  const handleUpdateStatus = async (id: string, newStatus: string) => {
    try {
      await fetch(`${API_BASE}/research/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      fetchItems();
    } catch {
      // ignore
    }
  };

  const handleUpdateNotes = async (id: string, notes: string) => {
    try {
      await fetch(`${API_BASE}/research/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      fetchItems();
    } catch {
      // ignore
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await fetch(`${API_BASE}/research/${id}`, { method: "DELETE" });
      setDeleteConfirm(null);
      fetchItems();
    } catch {
      // ignore
    }
  };

  const handleReanalyze = async (id: string) => {
    setReanalyzing((prev) => new Set(prev).add(id));
    try {
      await fetch(`${API_BASE}/research/${id}/reanalyze`, { method: "POST" });
      fetchItems();
    } catch {
      // ignore
    }
    setReanalyzing((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  const toggleSet = (set: Set<string>, id: string): Set<string> => {
    const next = new Set(set);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  };

  const filteredItems = (items || []).filter((item) => {
    if (filter === "all") return true;
    return item.status === filter;
  });

  const filterTabs: { key: FilterTab; label: string }[] = [
    { key: "all", label: "All" },
    { key: "active", label: "Active" },
    { key: "monitoring", label: "Monitoring" },
    { key: "resolved", label: "Resolved" },
  ];

  const sentimentDelta = (at_creation: number, current: number) => {
    const diff = current - at_creation;
    if (Math.abs(diff) < 0.01) return { arrow: "~", color: "text-neutral", label: "flat" };
    if (diff > 0) return { arrow: "\u2191", color: "text-bullish", label: `+${(diff * 100).toFixed(0)}%` };
    return { arrow: "\u2193", color: "text-bearish", label: `${(diff * 100).toFixed(0)}%` };
  };

  const formatTime = (ts: string) => {
    if (!ts) return "";
    try {
      const d = new Date(ts);
      const now = new Date();
      const diffMs = now.getTime() - d.getTime();
      const diffMin = Math.floor(diffMs / 60000);
      if (diffMin < 1) return "just now";
      if (diffMin < 60) return `${diffMin}m ago`;
      const diffH = Math.floor(diffMin / 60);
      if (diffH < 24) return `${diffH}h ago`;
      const diffD = Math.floor(diffH / 24);
      return `${diffD}d ago`;
    } catch {
      return ts;
    }
  };

  return (
    <div
      className={`fixed top-0 right-0 h-full w-96 bg-surface border-l border-border z-50 flex flex-col transition-transform duration-200 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-primary">Research Queue</h2>
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-alt border border-border text-text-muted">
            {items.length}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setShowAddForm((v) => !v)}
            className={`p-1 transition-colors rounded ${
              showAddForm ? "text-bullish bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
            title="Add item"
          >
            <Plus size={16} />
          </button>
          <button
            onClick={onClose}
            className="p-1 text-text-muted hover:text-text-primary transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Add item form */}
      {showAddForm && (
        <div className="px-4 py-3 border-b border-border bg-surface-alt/50 space-y-2.5">
          <input
            type="text"
            value={newEntity}
            onChange={(e) => setNewEntity(e.target.value)}
            placeholder="Entity / Ticker (e.g. TSLA)"
            className="w-full bg-surface-alt border border-border rounded-md px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors"
          />
          <textarea
            value={newAction}
            onChange={(e) => setNewAction(e.target.value)}
            placeholder="Reason for tracking..."
            rows={2}
            className="w-full bg-surface-alt border border-border rounded-md px-3 py-1.5 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors resize-none"
          />
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-text-muted mr-1">Priority:</span>
            {(["high", "medium", "low"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setNewPriority(p)}
                className={`px-2 py-0.5 text-[10px] font-semibold rounded-full border transition-colors capitalize ${
                  newPriority === p
                    ? priorityBorderColors[p] + " bg-surface"
                    : "border-border text-text-muted hover:text-text-primary"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
          <button
            onClick={handleCreate}
            disabled={!newEntity.trim() || submitting}
            className="w-full py-1.5 text-xs font-semibold rounded-md bg-bullish/10 text-bullish border border-bullish/20 hover:bg-bullish/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? "Adding..." : "Track"}
          </button>
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex items-center gap-0.5 px-4 py-2 border-b border-border">
        {filterTabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setFilter(tab.key)}
            className={`px-2.5 py-1 text-[10px] font-semibold rounded transition-colors ${
              filter === tab.key
                ? "text-bullish bg-bullish/10"
                : "text-text-muted hover:text-text-primary"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Item list */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto min-h-0">
        {filteredItems.length === 0 && (
          <div className="px-4 py-8 text-center text-xs text-text-muted">
            {items.length === 0
              ? "No items in research queue. Add one with the + button above."
              : "No items match this filter."}
          </div>
        )}
        {filteredItems.map((item) => {
          const sd = sentimentDelta(item.sentiment_at_creation, item.current_sentiment);
          const isNotesOpen = expandedNotes.has(item.id);
          const isAnalysisOpen = expandedAnalysis.has(item.id);

          return (
            <div
              key={item.id}
              className="px-4 py-3 border-b border-border/50 hover:bg-surface-alt/30 transition-colors"
            >
              {/* Top row: priority dot + entity + status */}
              <div className="flex items-start gap-2">
                <span
                  className={`w-2 h-2 rounded-full flex-shrink-0 mt-1 ${priorityColors[item.priority]}`}
                  title={`${item.priority} priority`}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center justify-between gap-2 mb-0.5">
                    <button
                      onClick={() => {
                        const ticker = item.entity_id.replace(/[^a-zA-Z0-9]/g, "");
                        router.push(`/alpha/${ticker}`);
                        onClose();
                      }}
                      className="text-xs font-semibold font-mono text-accent-blue hover:text-bullish transition-colors truncate text-left"
                    >
                      {item.entity_label}
                    </button>
                    <span
                      className={`flex-shrink-0 text-[9px] font-semibold px-1.5 py-0.5 rounded border capitalize ${
                        statusColors[item.status]
                      }`}
                    >
                      {item.status}
                    </span>
                  </div>

                  {/* Action text */}
                  <p className="text-[11px] text-text-secondary leading-tight line-clamp-2 mb-1.5">
                    {item.action}
                  </p>

                  {/* Metrics row */}
                  <div className="flex items-center gap-3 text-[10px] mb-2">
                    <span className="text-text-muted">
                      Sent: <span className={`font-mono font-semibold ${sd.color}`}>{sd.arrow} {sd.label}</span>
                    </span>
                    <span className="text-text-muted">
                      Vol: <span className="font-mono text-text-primary">
                        {item.mention_count_at_creation} {"\u2192"} {item.current_mention_count}
                      </span>
                    </span>
                    {item.alert_count > 0 && (
                      <span className="text-text-muted">
                        Alerts: <span className="font-mono text-volume-spike">{item.alert_count}</span>
                      </span>
                    )}
                  </div>

                  {/* Updated timestamp */}
                  <p className="text-[10px] text-text-muted mb-2">
                    Updated {formatTime(item.updated_at)}
                  </p>

                  {/* Action buttons */}
                  <div className="flex items-center gap-1.5">
                    <button
                      onClick={() => handleReanalyze(item.id)}
                      disabled={reanalyzing.has(item.id)}
                      className="flex items-center gap-1 px-2 py-1 text-[10px] font-medium rounded border border-border text-text-muted hover:text-accent-blue hover:border-accent-blue/30 transition-colors disabled:opacity-40"
                      title="Re-analyze"
                    >
                      {reanalyzing.has(item.id) ? (
                        <Loader2 size={10} className="animate-spin" />
                      ) : (
                        <RefreshCw size={10} />
                      )}
                      Re-analyze
                    </button>
                    <button
                      onClick={() => handleUpdateStatus(item.id, statusFlow[item.status])}
                      className="px-2 py-1 text-[10px] font-medium rounded border border-border text-text-muted hover:text-bullish hover:border-bullish/30 transition-colors capitalize"
                      title={`Move to ${statusFlow[item.status]}`}
                    >
                      {"\u2192"} {statusFlow[item.status]}
                    </button>
                    <button
                      onClick={() => setExpandedNotes((s) => toggleSet(s, item.id))}
                      className={`p-1 rounded border border-border transition-colors ${
                        isNotesOpen
                          ? "text-bullish border-bullish/30"
                          : "text-text-muted hover:text-text-primary"
                      }`}
                      title="Notes"
                    >
                      <StickyNote size={10} />
                    </button>
                    {deleteConfirm === item.id ? (
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleDelete(item.id)}
                          className="px-1.5 py-1 text-[10px] font-medium rounded border border-bearish/30 text-bearish hover:bg-bearish/10 transition-colors"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setDeleteConfirm(null)}
                          className="px-1.5 py-1 text-[10px] font-medium rounded border border-border text-text-muted hover:text-text-primary transition-colors"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setDeleteConfirm(item.id)}
                        className="p-1 rounded border border-border text-text-muted hover:text-bearish hover:border-bearish/30 transition-colors"
                        title="Delete"
                      >
                        <Trash2 size={10} />
                      </button>
                    )}
                  </div>

                  {/* Inline notes editor */}
                  {isNotesOpen && (
                    <div className="mt-2">
                      <textarea
                        value={editingNotes[item.id] !== undefined ? editingNotes[item.id] : item.notes}
                        onChange={(e) =>
                          setEditingNotes((prev) => ({ ...prev, [item.id]: e.target.value }))
                        }
                        onBlur={() => {
                          if (editingNotes[item.id] !== undefined && editingNotes[item.id] !== item.notes) {
                            handleUpdateNotes(item.id, editingNotes[item.id]);
                          }
                        }}
                        placeholder="Add research notes..."
                        rows={3}
                        className="w-full bg-surface-alt border border-border rounded-md px-2.5 py-1.5 text-[11px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors resize-none"
                      />
                    </div>
                  )}

                  {/* Expandable analysis section */}
                  {item.last_analysis && (
                    <button
                      onClick={() => setExpandedAnalysis((s) => toggleSet(s, item.id))}
                      className="flex items-center gap-1 mt-2 text-[10px] text-accent-blue hover:text-bullish transition-colors"
                    >
                      {isAnalysisOpen ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
                      Last analysis
                    </button>
                  )}
                  {isAnalysisOpen && item.last_analysis && (
                    <div className="mt-1.5 p-2.5 bg-surface-alt rounded-md border border-border">
                      <p className="text-[11px] text-text-secondary leading-relaxed whitespace-pre-wrap">
                        {item.last_analysis}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-border">
        <p className="text-[10px] text-text-muted">
          Auto-refreshes every 30s. Click entity to open Financial Alpha.
        </p>
      </div>
    </div>
  );
}
