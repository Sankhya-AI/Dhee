"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import { useInspectorStore } from "@/lib/stores/inspector-store";
import { useMemory, useMemoryHistory } from "@/lib/hooks/use-memory";
import { FadeMemTab } from "./fadem-tab";
import { EchoTab } from "./echo-tab";
import { CategoryTab } from "./category-tab";
import { TraceTab } from "./trace-tab";
import { HistoryTimeline } from "./history-timeline";
import { InspectorActions } from "./inspector-actions";
import { cn } from "@/lib/utils/format";
import { useState } from "react";
import { NEURAL } from "@/lib/utils/neural-palette";

const TABS = ["FadeMem", "EchoMem", "Traces", "CategoryMem", "History"] as const;
type Tab = (typeof TABS)[number];

function InspectorContent() {
  const { selectedMemoryId, close } = useInspectorStore();
  const { data: memory, mutate } = useMemory(selectedMemoryId);
  const { data: history } = useMemoryHistory(selectedMemoryId);
  const [activeTab, setActiveTab] = useState<Tab>("FadeMem");

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [close]);

  if (!memory) {
    return (
      <div className="flex h-full items-center justify-center text-sm" style={{ color: NEURAL.shallow }}>
        Loading...
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-start justify-between px-5 py-4 border-b" style={{ borderColor: 'rgba(124, 58, 237, 0.12)' }}>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-mono truncate" style={{ color: NEURAL.shallow }}>{memory.id}</p>
          <p className="mt-1 text-sm text-slate-200 line-clamp-3">{memory.content}</p>
        </div>
        <button onClick={close} className="ml-3 p-1 rounded hover:bg-white/[0.05] transition-colors">
          <X className="h-4 w-4" style={{ color: NEURAL.shallow }} />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b px-5 overflow-x-auto" style={{ borderColor: 'rgba(124, 58, 237, 0.12)' }}>
        {TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={cn(
              "px-3 py-2.5 text-xs font-medium transition-colors border-b-2 -mb-px whitespace-nowrap",
              activeTab === tab
                ? "border-purple-500 text-purple-300"
                : "border-transparent text-slate-500 hover:text-slate-300"
            )}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {activeTab === "FadeMem" && <FadeMemTab memory={memory} />}
        {activeTab === "EchoMem" && <EchoTab memory={memory} />}
        {activeTab === "Traces" && <TraceTab memory={memory} />}
        {activeTab === "CategoryMem" && <CategoryTab memory={memory} />}
        {activeTab === "History" && <HistoryTimeline entries={history || []} />}
      </div>

      {/* Actions */}
      <InspectorActions memory={memory} onMutate={mutate} />
    </div>
  );
}

export function InspectorWrapper() {
  const { isOpen } = useInspectorStore();

  return (
    <div
      className={cn(
        "h-screen border-l transition-all duration-200 overflow-hidden",
        isOpen ? "w-[480px]" : "w-0"
      )}
      style={{
        backgroundColor: NEURAL.cortex,
        borderColor: 'rgba(124, 58, 237, 0.12)',
      }}
    >
      {isOpen && <InspectorContent />}
    </div>
  );
}
