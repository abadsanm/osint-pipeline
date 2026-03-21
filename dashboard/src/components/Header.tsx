"use client";

import { Search, LayoutGrid, Bell } from "lucide-react";
import { useEffect, useState } from "react";

interface HeaderProps {
  title: string;
}

export default function Header({ title }: HeaderProps) {
  const [time, setTime] = useState("");

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
          <button className="p-2 text-text-muted hover:text-text-primary transition-colors relative" title="Notifications">
            <Bell size={16} />
            <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-bearish" />
          </button>
          <div className="w-px h-5 bg-border mx-1" />
          <button className="px-3 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border rounded-md transition-colors">Chat</button>
          <button className="px-3 py-1.5 text-xs text-text-muted hover:text-text-primary border border-border rounded-md transition-colors">Note</button>
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

        <div className="relative">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
          />
          <input
            type="text"
            placeholder="Search Tickers, Topics..."
            className="bg-surface-alt border border-border rounded-lg pl-8 pr-4 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors w-60"
          />
        </div>
      </header>
    </>
  );
}
