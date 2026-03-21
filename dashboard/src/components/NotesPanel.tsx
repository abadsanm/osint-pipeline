"use client";

import { useState, useEffect, useCallback } from "react";
import { X } from "lucide-react";

const STORAGE_KEY = "sentinel-notes";

interface NotesPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function NotesPanel({ open, onClose }: NotesPanelProps) {
  const [notes, setNotes] = useState("");
  const [saved, setSaved] = useState(false);

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored !== null) {
        setNotes(stored);
      }
    } catch {
      // localStorage unavailable
    }
  }, []);

  // Auto-save with debounce
  const saveToStorage = useCallback((text: string) => {
    try {
      localStorage.setItem(STORAGE_KEY, text);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch {
      // localStorage unavailable
    }
  }, []);

  useEffect(() => {
    const timeout = setTimeout(() => {
      saveToStorage(notes);
    }, 500);
    return () => clearTimeout(timeout);
  }, [notes, saveToStorage]);

  return (
    <div
      className={`fixed top-0 right-0 h-full w-80 bg-surface border-l border-border z-50 flex flex-col transition-transform duration-200 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-text-primary">Analyst Notes</h2>
          {saved && (
            <span className="text-[10px] text-bullish animate-pulse">Saved</span>
          )}
        </div>
        <button
          onClick={onClose}
          className="p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Textarea */}
      <div className="flex-1 p-3 min-h-0">
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Write analyst notes here... Auto-saved locally."
          className="w-full h-full bg-surface-alt border border-border rounded-lg p-3 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors resize-none leading-relaxed"
        />
      </div>

      {/* Footer */}
      <div className="px-4 py-2 border-t border-border">
        <p className="text-[10px] text-text-muted">
          Notes are stored in your browser only.
        </p>
      </div>
    </div>
  );
}
