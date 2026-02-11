"use client";

import { RotateCcw } from "lucide-react";
import { useGraphStore } from "@/lib/stores/graph-store";

export function GraphControls() {
  const { showSml, showLml, toggleSml, toggleLml, resetView } = useGraphStore();

  return (
    <div className="absolute top-4 right-4 flex flex-col gap-2">
      <div className="glass-subtle flex flex-col gap-1 p-1">
        <button
          onClick={resetView}
          className="p-1.5 hover:bg-white/[0.05] rounded transition-colors"
          style={{ color: '#94a3b8' }}
          title="Reset view"
        >
          <RotateCcw className="h-4 w-4" />
        </button>
      </div>

      <div className="glass-subtle p-2 space-y-1.5">
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showSml}
            onChange={toggleSml}
            className="h-3 w-3 rounded accent-cyan-500"
          />
          <span className="text-xs" style={{ color: '#94a3b8' }}>SML</span>
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: '#22d3ee' }} />
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={showLml}
            onChange={toggleLml}
            className="h-3 w-3 rounded accent-amber-500"
          />
          <span className="text-xs" style={{ color: '#94a3b8' }}>LML</span>
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: '#fbbf24' }} />
        </label>
      </div>
    </div>
  );
}
