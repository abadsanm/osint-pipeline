"use client";

import { Search, LayoutGrid, Bell, X, Crosshair } from "lucide-react";
import { useEffect, useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import ChatPanel from "./ChatPanel";
import NotesPanel from "./NotesPanel";
import ResearchPanel from "./ResearchPanel";
import AnalysisModal from "./AnalysisModal";
import WatchlistTicker from "./WatchlistTicker";

interface HeaderProps {
  title: string;
}

interface SearchResult {
  id: string;
  label: string;
  volume: number;
  sentiment: number;
  entity_type?: string;
  sources?: Record<string, number>;
  keywords?: string[];
  sampleDocs?: any[];
}

interface Alert {
  id: string;
  type: "high_confidence" | "volume_spike" | "convergence" | "anomaly" | "insider";
  priority: "high" | "medium" | "low";
  ticker: string;
  headline: string;
  message: string;
  timestamp: string;
  read: boolean;
}

const API_BASE = "http://localhost:8000/api";

const priorityDotColor: Record<string, string> = {
  high: "bg-bearish",
  medium: "bg-volume-spike",
  low: "bg-neutral",
};

const alertTypeLabel: Record<string, string> = {
  high_confidence: "HIGH CONF",
  volume_spike: "VOLUME",
  convergence: "CONVERGENCE",
  anomaly: "ANOMALY",
  insider: "INSIDER",
};

export default function Header({ title }: HeaderProps) {
  const router = useRouter();
  const [time, setTime] = useState("");

  // Search state
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [showSearch, setShowSearch] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Notifications state
  const [notifications, setNotifications] = useState<Alert[]>([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const notifRef = useRef<HTMLDivElement>(null);

  // Panel state
  const [chatOpen, setChatOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);
  const [researchOpen, setResearchOpen] = useState(false);
  const [analysisEntity, setAnalysisEntity] = useState<SearchResult | null>(null);

  // Timer
  useEffect(() => {
    const start = Date.now();
    const update = () => {
      const secs = Math.floor((Date.now() - start) / 1000);
      const mm = String(Math.floor(secs / 60)).padStart(3, "0");
      const ss = String(secs % 60).padStart(2, "0");
      setTime(`${mm}:${ss}`);
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);

  // Debounced search
  useEffect(() => {
    if (query.trim().length < 2) {
      setSearchResults([]);
      setShowSearch(false);
      return;
    }

    setSearchLoading(true);
    const timeout = setTimeout(async () => {
      try {
        const res = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query.trim())}`);
        if (res.ok) {
          const data = await res.json();
          setSearchResults(data);
          setShowSearch(true);
        }
      } catch {
        setSearchResults([]);
      } finally {
        setSearchLoading(false);
      }
    }, 300);

    return () => clearTimeout(timeout);
  }, [query]);

  // Fetch notifications
  const fetchNotifications = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/alerts?limit=20&unread_only=false`);
      if (res.ok) {
        const data: Alert[] = await res.json();
        setNotifications(data);
        setNotifCount(data.filter((a) => !a.read).length);
      }
    } catch {
      // API unavailable
    }
  }, []);

  useEffect(() => {
    fetchNotifications();
    const interval = setInterval(fetchNotifications, 15000);
    return () => clearInterval(interval);
  }, [fetchNotifications]);

  // Click outside handlers
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSearch(false);
      }
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
        setShowNotifications(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleSearchSelect = (result: SearchResult) => {
    setQuery("");
    setShowSearch(false);

    const id = result.id;
    const isTickerLike = /^[A-Z]{1,5}$/.test(id) || /^\$[A-Z]{1,5}$/.test(id);
    const entityType = result.entity_type?.toUpperCase() || "";
    const isFinancial = isTickerLike || entityType === "TICKER" || entityType === "COMPANY";

    if (isFinancial) {
      // Route to Financial Alpha page
      const ticker = id.replace(/[^a-zA-Z0-9]/g, "");
      router.push(`/alpha/${ticker}`);
    } else {
      // Open AI analysis modal for non-financial entities (people, tech, etc.)
      setAnalysisEntity(result);
    }
  };

  const sentimentLabel = (s: number) => {
    if (s >= 0.6) return { text: "Bullish", cls: "text-bullish" };
    if (s <= 0.4) return { text: "Bearish", cls: "text-bearish" };
    return { text: "Neutral", cls: "text-text-muted" };
  };

  return (
    <>
      {/* Top brand bar */}
      <div className="flex items-center justify-between px-5 py-2 bg-base border-b border-border">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 md:w-7 md:h-7 rounded-lg bg-bullish/20 border border-bullish/30 flex items-center justify-center">
            <span className="text-bullish font-bold text-xs md:text-sm">S</span>
          </div>
          <span className="text-sm font-semibold tracking-wide">
            <span className="text-bullish">SENTINEL</span>
            <span className="hidden md:inline text-neutral mx-1.5">|</span>
            <span className="hidden md:inline text-text-secondary">Social Alpha</span>
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button className="p-2 text-text-muted hover:text-text-primary transition-colors" title="Grid view">
            <LayoutGrid size={16} />
          </button>

          {/* Notifications */}
          <div ref={notifRef} className="relative">
            <button
              onClick={() => {
                setShowNotifications((v) => !v);
                fetchNotifications();
              }}
              className="p-2 text-text-muted hover:text-text-primary transition-colors relative"
              title="Notifications"
            >
              <Bell size={16} />
              {notifCount > 0 && (
                <span className="absolute top-0.5 right-0.5 min-w-[16px] h-4 rounded-full bg-bearish flex items-center justify-center px-1">
                  <span className="text-[9px] font-semibold text-white leading-none">
                    {notifCount > 99 ? "99+" : notifCount}
                  </span>
                </span>
              )}
            </button>

            {showNotifications && (
              <div className="absolute right-0 top-full mt-1 w-[calc(100vw-1rem)] md:w-80 max-w-[320px] md:max-w-none bg-surface-alt border border-border rounded-lg shadow-lg z-50 overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-border">
                  <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                    Recent Alerts
                  </span>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={async () => {
                        try {
                          await fetch(`${API_BASE}/alerts/read-all`, { method: "POST" });
                          fetchNotifications();
                        } catch { /* ignore */ }
                      }}
                      className="text-[10px] text-accent-blue hover:text-bullish transition-colors"
                    >
                      Mark all read
                    </button>
                    <button
                      onClick={() => setShowNotifications(false)}
                      className="text-text-muted hover:text-text-primary p-0.5"
                    >
                      <X size={12} />
                    </button>
                  </div>
                </div>
                <div className="max-h-80 overflow-y-auto">
                  {notifications.length === 0 && (
                    <div className="px-3 py-4 text-xs text-text-muted text-center">
                      No recent alerts
                    </div>
                  )}
                  {notifications.map((notif, i) => (
                    <button
                      key={`${notif.id}-${i}`}
                      onClick={async () => {
                        if (!notif.read) {
                          try {
                            await fetch(`${API_BASE}/alerts/${notif.id}/read`, { method: "POST" });
                            fetchNotifications();
                          } catch { /* ignore */ }
                        }
                        setShowNotifications(false);
                        const ticker = notif.ticker.replace(/[^a-zA-Z0-9]/g, "");
                        if (ticker) router.push(`/alpha/${ticker}`);
                      }}
                      className={`w-full flex items-start gap-2 px-3 py-2 hover:bg-surface/50 transition-colors text-left border-b border-border/30 last:border-0 ${
                        notif.read ? "opacity-60" : ""
                      }`}
                    >
                      <span
                        className={`w-2 h-2 rounded-full flex-shrink-0 mt-1.5 ${
                          priorityDotColor[notif.priority] || "bg-text-muted"
                        }`}
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 mb-0.5">
                          <span className="font-mono font-semibold text-[11px] text-text-primary">
                            {notif.ticker}
                          </span>
                          <span className="text-[9px] font-semibold px-1 py-0.5 rounded bg-surface border border-border/50 text-text-muted uppercase tracking-wider">
                            {alertTypeLabel[notif.type] || notif.type}
                          </span>
                          {!notif.read && (
                            <span className="w-1.5 h-1.5 rounded-full bg-accent-blue flex-shrink-0" />
                          )}
                        </div>
                        <p className="text-[11px] text-text-muted leading-tight truncate">
                          {notif.message || notif.headline}
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="w-px h-5 bg-border mx-1" />
          <button
            onClick={() => { setChatOpen(true); setNotesOpen(false); setResearchOpen(false); }}
            className={`px-3 py-1.5 text-xs border border-border rounded-md transition-colors ${
              chatOpen ? "text-bullish border-bullish/30 bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
          >
            Chat
          </button>
          <button
            onClick={() => { setNotesOpen(true); setChatOpen(false); setResearchOpen(false); }}
            className={`px-3 py-1.5 text-xs border border-border rounded-md transition-colors ${
              notesOpen ? "text-bullish border-bullish/30 bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
          >
            Note
          </button>
          <button
            onClick={() => { setResearchOpen(true); setChatOpen(false); setNotesOpen(false); }}
            className={`flex items-center gap-1 px-3 py-1.5 text-xs border border-border rounded-md transition-colors ${
              researchOpen ? "text-bullish border-bullish/30 bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
          >
            <Crosshair size={12} />
            Research
          </button>
          <div className="w-px h-5 bg-border mx-1" />
          <div className="w-7 h-7 rounded-full bg-accent-blue/20 border border-accent-blue/30 flex items-center justify-center">
            <span className="text-accent-blue text-xs font-semibold">S</span>
          </div>
        </div>
      </div>

      {/* Page header */}
      <header className="flex items-center justify-between px-5 py-2.5 border-b border-border bg-surface">
        <div className="flex items-center gap-3 min-w-0">
          <h1 className="text-base font-semibold tracking-tight flex-shrink-0">
            <span className="text-bullish">SENTINEL</span>
            <span className="text-neutral mx-1.5">|</span>
            <span className="text-text-primary">{title}</span>
          </h1>
          <div className="flex items-center gap-1.5 bg-surface-alt px-2.5 py-1 rounded-md flex-shrink-0">
            <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" />
            <span className="text-xs text-text-muted">Live</span>
            <span className="text-xs font-mono text-bullish ml-0.5" suppressHydrationWarning>{time}</span>
          </div>

          {/* Watchlist ticker tape — hidden on small screens, capped width */}
          <WatchlistTicker />
        </div>

        {/* Search with dropdown */}
        <div ref={searchRef} className="relative">
          {/* Mobile: icon-only toggle */}
          <button
            onClick={() => setShowSearch((v) => !v)}
            className="md:hidden p-2 text-text-muted hover:text-text-primary transition-colors"
            title="Search"
          >
            <Search size={16} />
          </button>
          {/* Desktop: always-visible input */}
          <div className="hidden md:block">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
            />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => { if (searchResults.length > 0) setShowSearch(true); }}
              placeholder="Search Tickers, Topics..."
              className="bg-surface-alt border border-border rounded-lg pl-8 pr-4 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors w-60"
            />
          </div>
          {/* Mobile: expandable search input + results */}
          {showSearch && (
            <div className="absolute right-0 top-full mt-1 w-[calc(100vw-2rem)] md:hidden bg-surface-alt border border-border rounded-lg shadow-lg z-50 overflow-hidden">
              <div className="relative p-2">
                <Search size={14} className="absolute left-5 top-1/2 -translate-y-1/2 text-text-muted" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  autoFocus
                  placeholder="Search Tickers, Topics..."
                  className="bg-surface border border-border rounded-lg pl-8 pr-4 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors w-full"
                />
              </div>
              {(searchLoading || searchResults.length > 0) && (
                <div className="border-t border-border/50 max-h-60 overflow-y-auto">
                  {searchLoading && (
                    <div className="px-3 py-2 text-xs text-text-muted">Searching...</div>
                  )}
                  {searchResults.map((r, i) => {
                    const sl = sentimentLabel(r.sentiment);
                    return (
                      <button
                        key={`${r.id}-${i}`}
                        onClick={() => handleSearchSelect(r)}
                        className="w-full flex items-center justify-between px-3 py-2 hover:bg-surface/50 transition-colors text-left border-b border-border/30 last:border-0"
                      >
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-text-primary font-semibold truncate">{r.label}</p>
                          <p className="text-[10px] text-text-muted truncate">{r.id}</p>
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0 ml-2">
                          <span className="text-[10px] text-text-muted font-mono">
                            Vol: {r.volume}
                          </span>
                          <span className={`text-[10px] font-semibold ${sl.cls}`}>
                            {sl.text}
                          </span>
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Desktop: search results dropdown */}
          {showSearch && searchResults.length > 0 && (
            <div className="hidden md:block absolute right-0 top-full mt-1 w-72 bg-surface-alt border border-border rounded-lg shadow-lg z-50 overflow-hidden">
              {searchLoading && (
                <div className="px-3 py-2 text-xs text-text-muted">Searching...</div>
              )}
              {!searchLoading && searchResults.length === 0 && query.trim().length >= 2 && (
                <div className="px-3 py-3 text-center">
                  <p className="text-xs text-text-muted mb-2">No results in pipeline</p>
                  <a
                    href={`https://www.google.com/search?q=${encodeURIComponent(query.trim())}+stock+market`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-accent-blue bg-accent-blue/10 border border-accent-blue/20 rounded-md hover:bg-accent-blue/20 transition-colors"
                  >
                    Search Google →
                  </a>
                </div>
              )}
              {searchResults.map((r, i) => {
                const sl = sentimentLabel(r.sentiment);
                return (
                  <button
                    key={`${r.id}-${i}`}
                    onClick={() => handleSearchSelect(r)}
                    className="w-full flex items-center justify-between px-3 py-2 hover:bg-surface/50 transition-colors text-left border-b border-border/30 last:border-0"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <p className="text-xs text-text-primary font-semibold truncate">{r.label}</p>
                        {r.entity_type && (
                          <span className="text-[9px] px-1 py-0.5 rounded bg-surface border border-border/50 text-text-muted uppercase flex-shrink-0">
                            {r.entity_type === "TICKER" ? "Stock" : r.entity_type === "COMPANY" ? "Company" : r.entity_type === "PERSON" ? "Person" : r.entity_type?.toLowerCase()}
                          </span>
                        )}
                      </div>
                      <p className="text-[10px] text-text-muted truncate">{r.id}</p>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                      <span className="text-[10px] text-text-muted font-mono">
                        {r.volume}
                      </span>
                      <span className={`text-[10px] font-semibold ${sl.cls}`}>
                        {sl.text}
                      </span>
                    </div>
                  </button>
                );
              })}
              {/* Always show Google search option */}
              {query.trim().length >= 2 && (
                <a
                  href={`https://www.google.com/search?q=${encodeURIComponent(query.trim())}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-1.5 px-3 py-2 text-[11px] text-accent-blue hover:bg-surface/50 transition-colors border-t border-border/30"
                >
                  Search &quot;{query.trim()}&quot; on Google →
                </a>
              )}
            </div>
          )}
        </div>
      </header>

      {/* Slide-out panels */}
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      <NotesPanel open={notesOpen} onClose={() => setNotesOpen(false)} />
      <ResearchPanel open={researchOpen} onClose={() => setResearchOpen(false)} />

      {/* Backdrop for panels */}
      {(chatOpen || notesOpen || researchOpen) && (
        <div
          className="fixed inset-0 bg-black/30 z-40"
          onClick={() => { setChatOpen(false); setNotesOpen(false); setResearchOpen(false); }}
        />
      )}

      {/* Analysis modal for non-financial search results */}
      {analysisEntity && (
        <AnalysisModal
          entity={{
            id: analysisEntity.id,
            label: analysisEntity.label,
            sentiment: analysisEntity.sentiment,
            volume: analysisEntity.volume,
            sources: analysisEntity.sources,
            keywords: analysisEntity.keywords,
            sampleDocs: analysisEntity.sampleDocs,
          }}
          contextType="entity"
          onClose={() => setAnalysisEntity(null)}
        />
      )}
    </>
  );
}
