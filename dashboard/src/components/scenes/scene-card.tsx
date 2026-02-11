"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Film } from "lucide-react";
import { formatDateTime } from "@/lib/utils/format";
import type { Scene } from "@/lib/types/scene";

export function SceneCard({ scene }: { scene: Scene }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-3 p-4 text-left hover:bg-gray-50 transition-colors"
      >
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gray-100">
          <Film className="h-4 w-4 text-gray-500" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900">
            {scene.title || scene.topic || "Untitled Scene"}
          </p>
          <div className="flex items-center gap-2 text-xs text-gray-500 mt-0.5">
            <span>{formatDateTime(scene.start_time)}</span>
            {scene.end_time && (
              <>
                <span>—</span>
                <span>{formatDateTime(scene.end_time)}</span>
              </>
            )}
            <span className="inline-flex rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium">
              {scene.memory_count} memories
            </span>
          </div>
        </div>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-400" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="border-t border-gray-100 px-4 py-3 space-y-2">
          {scene.summary && (
            <p className="text-sm text-gray-600">{scene.summary}</p>
          )}
          {scene.participants && scene.participants.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {scene.participants.map((p) => (
                <span
                  key={p}
                  className="inline-flex rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-medium text-blue-700"
                >
                  {p}
                </span>
              ))}
            </div>
          )}
          {scene.memory_ids && scene.memory_ids.length > 0 && (
            <p className="text-xs text-gray-400">
              Memory IDs: {scene.memory_ids.slice(0, 5).join(", ")}
              {scene.memory_ids.length > 5 && ` +${scene.memory_ids.length - 5} more`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
