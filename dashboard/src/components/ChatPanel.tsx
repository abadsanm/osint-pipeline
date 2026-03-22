"use client";

import { useState, useRef, useEffect } from "react";
import { X, Send } from "lucide-react";

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  timestamp: string;
}

interface ChatPanelProps {
  open: boolean;
  onClose: () => void;
}

export default function ChatPanel({ open, onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Sentinel AI ready. Ask me about any ticker (e.g. 'What is RKLB sentiment?'), market trends, or pipeline data. I have access to real-time OSINT signals, price data, technicals, and ML forecasts.",
      timestamp: "",
    },
  ]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setMessages((prev) =>
      prev.map((m) => m.id === "welcome" ? { ...m, timestamp: new Date().toLocaleTimeString() } : m)
    );
  }, []);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    const trimmed = input.trim();
    if (!trimmed || loading) return;

    const userMsg: Message = {
      id: `user-${Date.now()}`,
      role: "user",
      text: trimmed,
      timestamp: new Date().toLocaleTimeString(),
    };

    // Show user message + loading indicator
    const loadingMsg: Message = {
      id: `loading-${Date.now()}`,
      role: "assistant",
      text: "Analyzing...",
      timestamp: "",
    };

    setMessages((prev) => [...prev, userMsg, loadingMsg]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("http://localhost:8000/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: trimmed }),
      });
      const data = await res.json();

      setMessages((prev) => {
        const filtered = prev.filter((m) => !m.id.startsWith("loading-"));
        return [
          ...filtered,
          {
            id: `asst-${Date.now()}`,
            role: "assistant",
            text: data.response || "No response received.",
            timestamp: new Date().toLocaleTimeString(),
          },
        ];
      });
    } catch {
      setMessages((prev) => {
        const filtered = prev.filter((m) => !m.id.startsWith("loading-"));
        return [
          ...filtered,
          {
            id: `asst-${Date.now()}`,
            role: "assistant",
            text: "Could not reach Sentinel AI. Make sure the API server is running.",
            timestamp: new Date().toLocaleTimeString(),
          },
        ];
      });
    }
    setLoading(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div
      className={`fixed top-0 right-0 h-full w-80 bg-surface border-l border-border z-50 flex flex-col transition-transform duration-200 ${
        open ? "translate-x-0" : "translate-x-full"
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-text-primary">Sentinel Chat</h2>
        <button
          onClick={onClose}
          className="p-1 text-text-muted hover:text-text-primary transition-colors"
        >
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3 min-h-0">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col ${msg.role === "user" ? "items-end" : "items-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-lg px-3 py-2 text-xs leading-relaxed ${
                msg.role === "user"
                  ? "bg-accent-blue/20 text-text-primary"
                  : "bg-surface-alt text-text-primary"
              }`}
            >
              {msg.text}
            </div>
            <span className="text-[10px] text-text-muted mt-0.5 px-1" suppressHydrationWarning>{msg.timestamp}</span>
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="p-3 border-t border-border">
        <div className="relative">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask Sentinel..."
            className="w-full bg-surface-alt border border-border rounded-lg px-3 py-2 pr-9 text-xs text-text-primary placeholder:text-text-muted focus:outline-none focus:border-bullish/50 transition-colors"
          />
          <button
            onClick={handleSend}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-bullish transition-colors"
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
