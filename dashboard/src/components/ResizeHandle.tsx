"use client";

import { useCallback, useRef } from "react";

interface ResizeHandleProps {
  onResize: (deltaY: number) => void;
}

export default function ResizeHandle({ onResize }: ResizeHandleProps) {
  const dragging = useRef(false);
  const lastY = useRef(0);

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      dragging.current = true;
      lastY.current = e.clientY;
      document.body.style.cursor = "row-resize";
      document.body.style.userSelect = "none";

      const onMouseMove = (ev: MouseEvent) => {
        if (!dragging.current) return;
        const delta = ev.clientY - lastY.current;
        lastY.current = ev.clientY;
        onResize(delta);
      };

      const onMouseUp = () => {
        dragging.current = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMouseMove);
        window.removeEventListener("mouseup", onMouseUp);
      };

      window.addEventListener("mousemove", onMouseMove);
      window.addEventListener("mouseup", onMouseUp);
    },
    [onResize]
  );

  return (
    <div
      onMouseDown={onMouseDown}
      className="h-2 flex items-center justify-center cursor-row-resize group hover:bg-surface-alt/30 rounded transition-colors"
    >
      <div className="w-12 h-0.5 bg-border group-hover:bg-neutral rounded-full transition-colors" />
    </div>
  );
}
