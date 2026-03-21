"use client";

import { Search, LayoutGrid, Bell, X } from "lucide-react";
import { useEffect, useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import ChatPanel from "./ChatPanel";
import NotesPanel from "./NotesPanel";

interface HeaderProps {
  title: string;
}

interface SearchResult {
  id: string;
  label: string;
  volume: number;
  sentiment: number;
}

interface Notification {
  id: string;
  type: "bullish" | "bearish" | "volume";
  ticker: string;
  headline: string;
  timestamp: string;
  confidence: number;
}

const API_BASE = "http://localhost:8000/api";

const dotColor: Record<string, string> = {
  bullish: "bg-bullish",
  bearish: "bg-bearish",
  volume: "bg-volume-spike",
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
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [showNotifications, setShowNotifications] = useState(false);
  const [notifCount, setNotifCount] = useState(0);
  const notifRef = useRef<HTMLDivElement>(null);

  // Panel state
  const [chatOpen, setChatOpen] = useState(false);
  const [notesOpen, setNotesOpen] = useState(false);

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
      const res = await fetch(`${API_BASE}/signals?limit=10`);
      if (res.ok) {
        const data = await res.json();
        setNotifications(data);
        setNotifCount(data.length);
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
    const ticker = result.id.replace(/[^a-zA-Z0-9]/g, "");
    router.push(`/alpha/${ticker}`);
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
          <div className="w-7 h-7 rounded-lg bg-bullish/20 border border-bullish/30 flex items-center justify-center">
            <span className="text-bullish font-bold text-sm">S</span>
          </div>
          <span className="text-sm font-semibold tracking-wide">
            <span className="text-bullish">SENTINEL</span>
            <span className="text-neutral mx-1.5">|</span>
            <span className="text-text-secondary">Social Alpha</span>
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
              <div className="absolute right-0 top-full mt-1 w-80 bg-surface-alt border border-border rounded-lg shadow-lg z-50 overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-border">
                  <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                    Recent Alerts
                  </span>
                  <button
                    onClick={() => setShowNotifications(false)}
                    className="text-text-muted hover:text-text-primary p-0.5"
                  >
                    <X size={12} />
                  </button>
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
                      onClick={() => {
                        setShowNotifications(false);
                        const ticker = notif.ticker.replace(/[^a-zA-Z0-9]/g, "");
                        if (ticker) router.push(`/alpha/${ticker}`);
                      }}
                      className="w-full flex items-start gap-2 px-3 py-2 hover:bg-surface/50 transition-colors text-left border-b border-border/30 last:border-0"
                    >
                      <span
                        className={`w-2 h-2 rounded-full flex-shrink-0 mt-1 ${dotColor[notif.type] || "bg-text-muted"}`}
                      />
                      <div className="flex-1 min-w-0">
                        <p className="text-[11px] text-text-primary leading-tight">
                          <span className="font-mono font-semibold">{notif.ticker}</span>
                          <span className="text-text-muted"> {notif.headline}</span>
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
            onClick={() => { setChatOpen(true); setNotesOpen(false); }}
            className={`px-3 py-1.5 text-xs border border-border rounded-md transition-colors ${
              chatOpen ? "text-bullish border-bullish/30 bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
          >
            Chat
          </button>
          <button
            onClick={() => { setNotesOpen(true); setChatOpen(false); }}
            className={`px-3 py-1.5 text-xs border border-border rounded-md transition-colors ${
              notesOpen ? "text-bullish border-bullish/30 bg-bullish/10" : "text-text-muted hover:text-text-primary"
            }`}
          >
            Note
          </button>
          <div className="w-px h-5 bg-border mx-1" />
          <div className="w-7 h-7 rounded-full bg-accent-blue/20 border border-accent-blue/30 flex items-center justify-center">
            <span className="text-accent-blue text-xs font-semibold">S</span>
          </div>
        </div>
      </div>

      {/* Page header */}
      <header className="flex items-center justify-between px-5 py-2.5 border-b border-border bg-surface">
        <div className="flex items-center gap-4">
          <h1 className="text-base font-semibold tracking-tight">
            <span className="text-bullish">SENTINEL</span>
            <span className="text-neutral mx-1.5">|</span>
            <span className="text-text-primary">{title}</span>
          </h1>
          <div className="flex items-center gap-1.5 bg-surface-alt px-2.5 py-1 rounded-md">
            <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" />
            <span className="text-xs text-text-muted">Live</span>
            <span className="text-xs font-mono text-bullish ml-0.5" suppressHydrationWarning>{time}</span>
          </div>
        </div>

        {/* Search with dropdown */}
        <div ref={searchRef} className="relative">
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

          {showSearch && (
            <div className="absolute right-0 top-full mt-1 w-72 bg-surface-alt border border-border rounded-lg shadow-lg z-50 overflow-hidden">
              {searchLoading && (
                <div className="px-3 py-2 text-xs text-text-muted">Searching...</div>
              )}
              {!searchLoading && searchResults.length === 0 && (
                <div className="px-3 py-2 text-xs text-text-muted">No results found</div>
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
      </header>

      {/* Slide-out panels */}
      <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
      <NotesPanel open={notesOpen} onClose={() => setNotesOpen(false)} />

      {/* Backdrop for panels */}
      {(chatOpen || notesOpen) && (
        <div
          className="fixed inset-0 bg-black/30 z-40"
          onClick={() => { setChatOpen(false); setNotesOpen(false); }}
        />
      )}
    </>
  );
}
