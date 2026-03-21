"use client";

import { useState, useCallback, useRef } from "react";

interface ResizableCardProps {
  children: React.ReactNode;
  defaultHeight: number;
  minHeight?: number;
  maxHeight?: number;
}

export default function ResizableCard({
  children,
  defaultHeight,
  minHeight = 100,
  maxHeight = 900,
}: ResizableCardProps) {
  const [height, setHeight] = useState(defaultHeight);
  const dragging = useRef(false);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      const startY = e.clientY;
      const startH = height;

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const newH = startH + (ev.clientY - startY);
        setHeight(Math.max(minHeight, Math.min(maxHeight, newH)));
      };

      const onUp = () => {
        dragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [height, minHeight, maxHeight]
  );

  return (
    <div className="relative">
      <div style={{ height }}>
        {children}
      </div>
      {/* Resize handle at bottom */}
      <div
        onMouseDown={onDragStart}
        className="h-2 flex items-center justify-center cursor-row-resize group hover:bg-surface-alt/30 rounded-b transition-colors -mt-1"
      >
        <div className="w-10 h-0.5 bg-border group-hover:bg-neutral rounded-full transition-colors" />
      </div>
    </div>
  );
}
