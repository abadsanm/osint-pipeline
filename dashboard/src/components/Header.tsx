"use client";

import { Search } from "lucide-react";
import { useEffect, useState } from "react";

interface HeaderProps {
  title: string;
}

export default function Header({ title }: HeaderProps) {
  const [time, setTime] = useState("");

  useEffect(() => {
    const update = () => {
      const now = new Date();
      setTime(
        now.toLocaleTimeString("en-US", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      );
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <header className="flex items-center justify-between px-6 py-3 border-b border-border bg-surface">
      <div className="flex items-center gap-4">
        <h1 className="text-lg font-semibold tracking-tight">
          <span className="text-accent-blue">SENTINEL</span>
          <span className="text-neutral mx-2">|</span>
          <span>{title}</span>
        </h1>
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-bullish animate-pulse" />
          <span className="text-xs font-mono text-text-muted">Live</span>
          <span className="text-xs font-mono text-neutral ml-1">{time}</span>
        </div>
      </div>

      <div className="relative">
        <Search
          size={14}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted"
        />
        <input
          type="text"
          placeholder="Search tickers & topics..."
          className="bg-surface-alt border border-border rounded-md pl-8 pr-4 py-1.5 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent-blue transition-colors w-64"
        />
      </div>
    </header>
  );
}
