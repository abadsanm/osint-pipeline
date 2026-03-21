"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, TrendingUp, Lightbulb, Settings } from "lucide-react";

const navItems = [
  { href: "/", icon: Home, label: "Global Pulse" },
  { href: "/alpha/TSLA", icon: TrendingUp, label: "Financial Alpha" },
  { href: "/innovation", icon: Lightbulb, label: "Product Innovation" },
  { href: "/settings", icon: Settings, label: "Settings" },
];

export default function Sidebar() {
  const pathname = usePathname() ?? "/";

  return (
    <nav className="fixed left-0 top-0 z-40 h-screen w-12 bg-surface border-r border-border flex flex-col items-center py-3 gap-1">
      {navItems.map((item) => {
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href.split("/").slice(0, 2).join("/"));
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
  );
}
