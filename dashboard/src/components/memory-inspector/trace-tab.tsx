"use client";

import type { Memory } from "@/lib/types/memory";
import { NEURAL } from "@/lib/utils/neural-palette";

function TraceBar({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  const pct = Math.round(value * 100);
  return (
    <div>
      <div className="flex justify-between text-xs mb-1.5">
        <span style={{ color: NEURAL.shallow }}>{label}</span>
        <span className="tabular-nums font-medium" style={{ color }}>{pct}%</span>
      </div>
      <div className="h-2.5 rounded-full" style={{ backgroundColor: 'rgba(124, 58, 237, 0.1)' }}>
        <div
          className="h-2.5 rounded-full transition-all duration-500"
          style={{
            width: `${pct}%`,
            backgroundColor: color,
            boxShadow: `0 0 12px ${color}50`,
          }}
        />
      </div>
    </div>
  );
}

function TraceCascadePreview({ sFast, sMid, sSlow }: { sFast: number; sMid: number; sSlow: number }) {
  return (
    <div className="flex items-center gap-3 py-3">
      {/* Fast ring */}
      <div className="relative flex items-center justify-center">
        <svg width="56" height="56" viewBox="0 0 56 56">
          <circle cx="28" cy="28" r="24" fill="none" stroke="rgba(124,58,237,0.08)" strokeWidth="4" />
          <circle
            cx="28" cy="28" r="24" fill="none"
            stroke={NEURAL.sFast}
            strokeWidth="4"
            strokeDasharray={`${sFast * 150.8} ${150.8}`}
            strokeLinecap="round"
            transform="rotate(-90 28 28)"
            style={{ filter: `drop-shadow(0 0 4px ${NEURAL.sFast}50)` }}
          />
          <circle cx="28" cy="28" r="18" fill="none" stroke="rgba(124,58,237,0.08)" strokeWidth="4" />
          <circle
            cx="28" cy="28" r="18" fill="none"
            stroke={NEURAL.sMid}
            strokeWidth="4"
            strokeDasharray={`${sMid * 113.1} ${113.1}`}
            strokeLinecap="round"
            transform="rotate(-90 28 28)"
            style={{ filter: `drop-shadow(0 0 4px ${NEURAL.sMid}50)` }}
          />
          <circle cx="28" cy="28" r="12" fill="none" stroke="rgba(124,58,237,0.08)" strokeWidth="4" />
          <circle
            cx="28" cy="28" r="12" fill="none"
            stroke={NEURAL.sSlow}
            strokeWidth="4"
            strokeDasharray={`${sSlow * 75.4} ${75.4}`}
            strokeLinecap="round"
            transform="rotate(-90 28 28)"
            style={{ filter: `drop-shadow(0 0 4px ${NEURAL.sSlow}50)` }}
          />
        </svg>
      </div>
      {/* Legend */}
      <div className="space-y-1.5 text-xs">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NEURAL.sFast }} />
          <span style={{ color: NEURAL.shallow }}>Fast (volatile)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NEURAL.sMid }} />
          <span style={{ color: NEURAL.shallow }}>Mid (consolidating)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: NEURAL.sSlow }} />
          <span style={{ color: NEURAL.shallow }}>Slow (permanent)</span>
        </div>
      </div>
    </div>
  );
}

export function TraceTab({ memory }: { memory: Memory }) {
  const sFast = memory.s_fast ?? 0;
  const sMid = memory.s_mid ?? 0;
  const sSlow = memory.s_slow ?? 0;
  const hasTraces = sFast > 0 || sMid > 0 || sSlow > 0;

  if (!hasTraces) {
    return (
      <div className="space-y-4">
        <p className="text-sm" style={{ color: NEURAL.shallow }}>
          No multi-trace data available. CLS distillation features may not be enabled.
        </p>
        <div className="glass-subtle p-3">
          <p className="text-xs" style={{ color: NEURAL.forgotten }}>
            Multi-trace encoding records how strongly a memory is encoded across three time-scale systems:
            fast (immediate), mid (session), and slow (consolidated).
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Concentric rings preview */}
      <div>
        <h4 className="text-xs font-medium text-slate-300 mb-2">Trace Cascade</h4>
        <TraceCascadePreview sFast={sFast} sMid={sMid} sSlow={sSlow} />
      </div>

      {/* Individual bars */}
      <div className="space-y-3">
        <TraceBar label="s_fast" value={sFast} color={NEURAL.sFast} />
        <TraceBar label="s_mid" value={sMid} color={NEURAL.sMid} />
        <TraceBar label="s_slow" value={sSlow} color={NEURAL.sSlow} />
      </div>

      {/* Memory type */}
      {memory.memory_type && (
        <div>
          <h4 className="text-xs font-medium text-slate-300 mb-2">Memory Type</h4>
          <span
            className="inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 capitalize"
            style={{
              color: memory.memory_type === 'semantic' ? NEURAL.semantic : NEURAL.episodic,
              backgroundColor: memory.memory_type === 'semantic' ? `${NEURAL.semantic}15` : `${NEURAL.episodic}15`,
              borderColor: memory.memory_type === 'semantic' ? `${NEURAL.semantic}30` : `${NEURAL.episodic}30`,
            }}
          >
            {memory.memory_type}
          </span>
        </div>
      )}
    </div>
  );
}
