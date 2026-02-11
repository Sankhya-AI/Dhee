"use client";

import { formatDateTime } from "@/lib/utils/format";
import type { MemoryHistoryEntry } from "@/lib/types/memory";
import { NEURAL } from "@/lib/utils/neural-palette";

const EVENT_COLORS: Record<string, string> = {
  CREATE: NEURAL.success,
  DECAY: NEURAL.lml,
  PROMOTE: NEURAL.lml,
  DEMOTE: NEURAL.sml,
  ACCESS: "#60a5fa",
  UPDATE: NEURAL.episodic,
  DELETE: NEURAL.conflict,
};

export function HistoryTimeline({ entries }: { entries: MemoryHistoryEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm" style={{ color: NEURAL.shallow }}>No history available.</p>;
  }

  return (
    <div className="relative">
      <div className="absolute left-2 top-0 bottom-0 w-px" style={{ backgroundColor: 'rgba(124,58,237,0.15)' }} />
      <ul className="space-y-4">
        {entries.map((entry, i) => (
          <li key={i} className="relative pl-7">
            <div
              className="absolute left-0.5 top-1 h-3 w-3 rounded-full ring-2"
              style={{
                backgroundColor: EVENT_COLORS[entry.event] || '#475569',
                boxShadow: `0 0 0 2px ${NEURAL.cortex}`,
              }}
            />
            <p className="text-xs font-medium text-slate-200">{entry.event}</p>
            <p className="text-[11px]" style={{ color: NEURAL.forgotten }}>
              {formatDateTime(entry.timestamp)}
            </p>
            {entry.details && (
              <pre
                className="mt-1 text-[10px] rounded p-1.5 overflow-x-auto"
                style={{
                  color: NEURAL.shallow,
                  backgroundColor: 'rgba(124,58,237,0.06)',
                }}
              >
                {JSON.stringify(entry.details, null, 2)}
              </pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
