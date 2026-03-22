"use client";

import { useState, useRef, useEffect } from "react";
import { Info, X } from "lucide-react";

export default function InfoTooltip({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const popupRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  // Reposition popup if it overflows viewport
  useEffect(() => {
    if (!open || !popupRef.current || !btnRef.current) return;
    const popup = popupRef.current;
    const rect = popup.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Fix horizontal overflow
    if (rect.right > vw - 8) {
      popup.style.left = "auto";
      popup.style.right = "0px";
    }
    if (rect.left < 8) {
      popup.style.left = "0px";
      popup.style.right = "auto";
    }
    // Fix vertical overflow — show above if below viewport
    if (rect.bottom > vh - 8) {
      popup.style.top = "auto";
      popup.style.bottom = "24px";
    }
  }, [open]);

  return (
    <div className="relative inline-block">
      <button
        ref={btnRef}
        onClick={() => setOpen(!open)}
        className="text-text-muted hover:text-accent-blue transition-colors p-0.5"
        title={title}
      >
        <Info size={13} />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            ref={popupRef}
            className="absolute z-50 w-72 bg-surface-alt border border-border rounded-lg p-3 shadow-lg"
            style={{ right: 0, top: 24 }}
          >
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
