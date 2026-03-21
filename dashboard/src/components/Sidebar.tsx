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
  const pathname = usePathname();

  return (
    <nav className="fixed left-0 top-0 z-40 h-screen w-14 hover:w-[200px] transition-[width] duration-200 ease-in-out bg-surface border-r border-border flex flex-col py-4 overflow-hidden group">
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 mb-8 min-w-[200px]">
        <div className="w-6 h-6 rounded bg-accent-blue flex items-center justify-center flex-shrink-0">
          <span className="text-base font-bold text-xs">S</span>
        </div>
        <span className="text-sm font-semibold text-text-primary whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          SENTINEL
        </span>
      </div>

      {/* Nav items */}
      <div className="flex flex-col gap-1 flex-1">
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
              className={`flex items-center gap-3 px-4 py-2.5 min-w-[200px] transition-colors duration-150 relative ${
                isActive
                  ? "text-accent-blue"
                  : "text-text-muted hover:text-text-primary"
              }`}
            >
              {isActive && (
                <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 bg-accent-blue rounded-r" />
              )}
              <Icon size={18} className="flex-shrink-0" />
              <span className="text-sm whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                {item.label}
              </span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
