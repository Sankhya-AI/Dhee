"use client";

import { useState } from "react";
import { resolveConflict } from "@/lib/api/conflicts";
import type { Conflict } from "@/lib/types/conflict";
import { timeAgo } from "@/lib/utils/format";

export function ConflictCard({
  conflict,
  onResolved,
}: {
  conflict: Conflict;
  onResolved: () => void;
}) {
  const [loading, setLoading] = useState(false);

  const handleResolve = async (resolution: string) => {
    setLoading(true);
    await resolveConflict(conflict.stash_id || conflict.id, resolution);
    setLoading(false);
    onResolved();
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-xs text-gray-400 font-mono">{conflict.id}</span>
        <span className="text-xs text-gray-500">{timeAgo(conflict.created_at)}</span>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-4">
        {/* Existing */}
        <div className="rounded-md border-2 border-cyan-200 bg-cyan-50/50 p-3">
          <p className="text-[10px] font-semibold text-cyan-600 uppercase tracking-wide mb-1.5">
            Existing
          </p>
          <p className="text-sm text-gray-700 leading-relaxed">
            {conflict.existing_content}
          </p>
        </div>
        {/* Proposed */}
        <div className="rounded-md border-2 border-amber-200 bg-amber-50/50 p-3">
          <p className="text-[10px] font-semibold text-amber-600 uppercase tracking-wide mb-1.5">
            Proposed
          </p>
          <p className="text-sm text-gray-700 leading-relaxed">
            {conflict.proposed_content}
          </p>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400 mr-auto">
          Similarity: {(conflict.similarity * 100).toFixed(0)}%
        </span>
        <button
          onClick={() => handleResolve("KEEP_EXISTING")}
          disabled={loading}
          className="rounded-md border border-cyan-200 bg-cyan-50 px-3 py-1.5 text-xs font-medium text-cyan-700 hover:bg-cyan-100 disabled:opacity-50"
        >
          Keep Existing
        </button>
        <button
          onClick={() => handleResolve("ACCEPT_PROPOSED")}
          disabled={loading}
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-50"
        >
          Accept Proposed
        </button>
        <button
          onClick={() => handleResolve("KEEP_BOTH")}
          disabled={loading}
          className="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-50"
        >
          Keep Both
        </button>
      </div>
    </div>
  );
}
