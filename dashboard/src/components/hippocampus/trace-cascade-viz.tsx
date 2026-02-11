"use client";

import { NEURAL } from "@/lib/utils/neural-palette";
import { useMemories } from "@/lib/hooks/use-memories";
import { useMemo } from "react";

export function TraceCascadeViz() {
  const { data } = useMemories({ limit: 200 });
  const memories = data?.memories ?? [];

  const traceStats = useMemo(() => {
    let fastSum = 0, midSum = 0, slowSum = 0, count = 0;
    for (const m of memories) {
      if (m.s_fast !== undefined || m.s_mid !== undefined || m.s_slow !== undefined) {
        fastSum += m.s_fast ?? 0;
        midSum += m.s_mid ?? 0;
        slowSum += m.s_slow ?? 0;
        count++;
      }
    }
    if (count === 0) return null;
    return {
      avgFast: fastSum / count,
      avgMid: midSum / count,
      avgSlow: slowSum / count,
      count,
    };
  }, [memories]);

  if (!traceStats) {
    return (
      <div className="glass p-6">
        <h3 className="text-sm font-medium text-white mb-3">Multi-Trace Cascade</h3>
        <p className="text-sm" style={{ color: NEURAL.shallow }}>
          No trace data available. Enable CLS distillation features.
        </p>
      </div>
    );
  }

  return (
    <div className="glass p-6">
      <h3 className="text-sm font-medium text-white mb-1">Multi-Trace Cascade</h3>
      <p className="text-[10px] mb-4" style={{ color: NEURAL.shallow }}>
        Average trace strengths across {traceStats.count} memories
      </p>

      {/* Cascade visualization */}
      <div className="flex items-end gap-8 justify-center mb-6">
        {[
          { label: "Fast", value: traceStats.avgFast, color: NEURAL.sFast, desc: "Immediate" },
          { label: "Mid", value: traceStats.avgMid, color: NEURAL.sMid, desc: "Session" },
          { label: "Slow", value: traceStats.avgSlow, color: NEURAL.sSlow, desc: "Consolidated" },
        ].map(({ label, value, color, desc }) => (
          <div key={label} className="flex flex-col items-center">
            <span className="text-xs font-medium mb-1" style={{ color }}>{(value * 100).toFixed(0)}%</span>
            <div className="w-16 rounded-t-lg relative" style={{
              height: `${Math.max(8, value * 120)}px`,
              backgroundColor: `${color}30`,
              boxShadow: `0 0 15px ${color}20`,
            }}>
              <div
                className="absolute bottom-0 w-full rounded-t-lg"
                style={{
                  height: `${value * 100}%`,
                  backgroundColor: color,
                  boxShadow: `0 0 10px ${color}40`,
                }}
              />
            </div>
            <p className="text-xs font-medium mt-2" style={{ color }}>{label}</p>
            <p className="text-[9px]" style={{ color: NEURAL.forgotten }}>{desc}</p>
          </div>
        ))}
      </div>

      {/* Flow arrows */}
      <div className="flex items-center justify-center gap-2">
        <span className="text-[10px] font-medium" style={{ color: NEURAL.sFast }}>s_fast</span>
        <div className="w-8 h-px" style={{ background: `linear-gradient(to right, ${NEURAL.sFast}, ${NEURAL.sMid})` }} />
        <svg width="6" height="6" viewBox="0 0 6 6"><path d="M0 0L6 3L0 6Z" fill={NEURAL.sMid} /></svg>
        <span className="text-[10px] font-medium" style={{ color: NEURAL.sMid }}>s_mid</span>
        <div className="w-8 h-px" style={{ background: `linear-gradient(to right, ${NEURAL.sMid}, ${NEURAL.sSlow})` }} />
        <svg width="6" height="6" viewBox="0 0 6 6"><path d="M0 0L6 3L0 6Z" fill={NEURAL.sSlow} /></svg>
        <span className="text-[10px] font-medium" style={{ color: NEURAL.sSlow }}>s_slow</span>
      </div>
    </div>
  );
}
