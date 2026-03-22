"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, TrendingUp, Lightbulb, Target, FlaskConical, Settings } from "lucide-react";

const navItems = [
  { href: "/", icon: Home, label: "Pulse", mobileLabel: "Pulse" },
  { href: "/alpha/TSLA", icon: TrendingUp, label: "Alpha", mobileLabel: "Alpha" },
  { href: "/innovation", icon: Lightbulb, label: "Innovation", mobileLabel: "Innovation" },
  { href: "/predictions", icon: Target, label: "Predictions", mobileLabel: "Predict" },
  { href: "/backtesting", icon: FlaskConical, label: "Backtesting", mobileLabel: "Backtest" },
  { href: "/settings", icon: Settings, label: "Settings", mobileLabel: "Settings" },
];

export default function Sidebar() {
  const pathname = usePathname() ?? "/";

  const isActiveItem = (href: string) =>
    href === "/"
      ? pathname === "/"
      : pathname.startsWith(href.split("/").slice(0, 2).join("/"));

  return (
    <>
      {/* Desktop sidebar */}
      <nav className="hidden md:flex fixed left-0 top-0 z-40 h-screen w-12 bg-surface border-r border-border flex-col items-center py-3 gap-1">
        {navItems.map((item) => {
          const isActive = isActiveItem(item.href);
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              className={`relative w-9 h-9 flex items-center justify-center rounded-lg transition-colors duration-150 ${
                isActive
                  ? "text-bullish bg-bullish/10"
                  : "text-text-muted hover:text-text-primary hover:bg-surface-alt"
              }`}
            >
              {isActive && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[2px] h-4 bg-bullish rounded-r" />
              )}
              <Icon size={18} />
            </Link>
          );
        })}
      </nav>

      {/* Mobile bottom tab bar */}
      <nav className="fixed bottom-0 left-0 right-0 z-40 flex md:hidden items-center justify-around bg-surface border-t border-border px-2 pt-1.5 pb-[calc(0.375rem+env(safe-area-inset-bottom))]">
        {navItems.map((item) => {
          const isActive = isActiveItem(item.href);
          const Icon = item.icon;

          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex flex-col items-center gap-0.5 px-3 py-1 rounded-lg transition-colors duration-150 ${
                isActive
                  ? "text-bullish"
                  : "text-text-muted"
              }`}
            >
              <Icon size={20} />
              <span className="text-[10px] leading-tight font-medium">{item.mobileLabel}</span>
            </Link>
          );
        })}
      </nav>
    </>
  );
}
