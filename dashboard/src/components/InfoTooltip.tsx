"use client";

import { useState } from "react";
import { Info, X } from "lucide-react";

export default function InfoTooltip({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative inline-block">
      <button
        onClick={() => setOpen(!open)}
        className="text-text-muted hover:text-accent-blue transition-colors p-0.5"
        title={title}
      >
        <Info size={13} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-6 z-50 w-72 bg-surface-alt border border-border rounded-lg p-3 shadow-lg">
            <div className="flex items-center justify-between mb-1.5">
              <h4 className="text-xs font-semibold text-text-primary">{title}</h4>
              <button onClick={() => setOpen(false)} className="text-text-muted hover:text-text-primary">
                <X size={11} />
              </button>
            </div>
            <div className="text-[11px] text-text-muted leading-relaxed space-y-1.5">
              {children}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
