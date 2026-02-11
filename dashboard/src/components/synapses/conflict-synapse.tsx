"use client";

import { useState } from "react";
import { AlertTriangle, Check, X, Copy } from "lucide-react";
import { resolveConflict } from "@/lib/api/conflicts";
import type { Conflict } from "@/lib/types/conflict";
import { NEURAL } from "@/lib/utils/neural-palette";
import { timeAgo } from "@/lib/utils/format";
import type { KeyedMutator } from "swr";

export function ConflictSynapse({
  conflict,
  onMutate,
}: {
  conflict: Conflict;
  onMutate: KeyedMutator<{ conflicts: Conflict[] }>;
}) {
  const [loading, setLoading] = useState(false);

  const handleResolve = async (resolution: string) => {
    setLoading(true);
    try {
      await resolveConflict(conflict.stash_id, resolution);
      await onMutate();
    } finally {
      setLoading(false);
    }
  };

  const isResolved = !!conflict.resolution;

  return (
    <div
      className="glass p-4"
      style={!isResolved ? { borderColor: `${NEURAL.conflict}30`, boxShadow: `0 0 20px ${NEURAL.conflict}10` } : undefined}
    >
      <div className="flex items-start gap-3 mb-3">
        <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" style={{ color: isResolved ? NEURAL.success : NEURAL.conflict }} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium" style={{ color: isResolved ? NEURAL.success : NEURAL.conflict }}>
              {isResolved ? `Resolved: ${conflict.resolution}` : "Unresolved Conflict"}
            </span>
            <span className="text-[10px]" style={{ color: NEURAL.forgotten }}>
              {timeAgo(conflict.created_at)}
            </span>
          </div>
          <p className="text-xs mt-0.5" style={{ color: NEURAL.shallow }}>
            Similarity: {(conflict.similarity * 100).toFixed(0)}%
          </p>
        </div>
      </div>

      {/* Content comparison */}
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="glass-subtle p-3">
          <p className="text-[10px] font-medium mb-1" style={{ color: NEURAL.sml }}>Existing</p>
          <p className="text-xs text-slate-300 line-clamp-4">{conflict.existing_content}</p>
        </div>
        <div className="glass-subtle p-3">
          <p className="text-[10px] font-medium mb-1" style={{ color: NEURAL.pending }}>Proposed</p>
          <p className="text-xs text-slate-300 line-clamp-4">{conflict.proposed_content}</p>
        </div>
      </div>

      {/* Resolution buttons */}
      {!isResolved && (
        <div className="flex gap-2">
          <button
            onClick={() => handleResolve("KEEP_EXISTING")}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
            style={{ color: NEURAL.sml, border: `1px solid ${NEURAL.sml}30`, backgroundColor: `${NEURAL.sml}08` }}
          >
            <Check className="h-3 w-3" /> Keep Existing
          </button>
          <button
            onClick={() => handleResolve("ACCEPT_PROPOSED")}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
            style={{ color: NEURAL.pending, border: `1px solid ${NEURAL.pending}30`, backgroundColor: `${NEURAL.pending}08` }}
          >
            <Check className="h-3 w-3" /> Accept New
          </button>
          <button
            onClick={() => handleResolve("KEEP_BOTH")}
            disabled={loading}
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium disabled:opacity-50 transition-colors"
            style={{ color: NEURAL.shallow, border: `1px solid rgba(124,58,237,0.15)` }}
          >
            <Copy className="h-3 w-3" /> Keep Both
          </button>
        </div>
      )}
    </div>
  );
}
