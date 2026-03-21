"use client";

import { useState, useCallback, useRef } from "react";
import { GripHorizontal } from "lucide-react";

interface ResizableCardProps {
  children: React.ReactNode;
  defaultHeight: number;
  minHeight?: number;
  maxHeight?: number;
  handlePosition?: "top" | "bottom";
}

export default function ResizableCard({
  children,
  defaultHeight,
  minHeight = 100,
  maxHeight = 900,
  handlePosition = "bottom",
}: ResizableCardProps) {
  const [height, setHeight] = useState(defaultHeight);
  const dragging = useRef(false);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      const startY = e.clientY;
      const startH = height;
      const direction = handlePosition === "top" ? -1 : 1;

      const onMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const newH = startH + (ev.clientY - startY) * direction;
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
    [height, minHeight, maxHeight, handlePosition]
  );

  const handle = (
    <div
      onMouseDown={onDragStart}
      className="h-3 flex items-center justify-center cursor-row-resize group hover:bg-accent-blue/10 rounded transition-colors"
    >
      <GripHorizontal size={14} className="text-border group-hover:text-neutral transition-colors" />
    </div>
  );

  return (
    <div className="relative">
      {handlePosition === "top" && handle}
      <div style={{ height }}>{children}</div>
      {handlePosition === "bottom" && handle}
    </div>
  );
}
