"use client";

import { NEURAL } from "@/lib/utils/neural-palette";
import { useDecayLog } from "@/lib/hooks/use-decay-log";
import { useMemo } from "react";

function GaugeMeter({
  label,
  description,
  value,
  color,
}: {
  label: string;
  description: string;
  value: number;
  color: string;
}) {
  const angle = value * 180;
  const circumference = Math.PI * 50;
  const dashOffset = circumference - (value * circumference);

  return (
    <div className="glass p-4 text-center">
      <div className="relative inline-flex items-center justify-center mb-2">
        <svg width="100" height="60" viewBox="0 0 100 60">
          {/* Background arc */}
          <path
            d="M10 55 A 45 45 0 0 1 90 55"
            fill="none"
            stroke="rgba(124,58,237,0.1)"
            strokeWidth="6"
            strokeLinecap="round"
          />
          {/* Value arc */}
          <path
            d="M10 55 A 45 45 0 0 1 90 55"
            fill="none"
            stroke={color}
            strokeWidth="6"
            strokeLinecap="round"
            strokeDasharray={`${value * 141.37} 141.37`}
            style={{ filter: `drop-shadow(0 0 4px ${color}50)` }}
          />
        </svg>
        <span
          className="absolute text-sm font-bold bottom-0"
          style={{ color }}
        >
          {(value * 100).toFixed(0)}%
        </span>
      </div>
      <p className="text-xs font-medium text-white">{label}</p>
      <p className="text-[10px] mt-0.5" style={{ color: NEURAL.shallow }}>{description}</p>
    </div>
  );
}

export function ForgettingDashboard() {
  const { data } = useDecayLog();
  const entries = data?.entries ?? [];

  const stats = useMemo(() => {
    if (entries.length === 0) return { interference: 0, redundancy: 0, homeostasis: 0 };
    const latest = entries[entries.length - 1];
    const total = (latest.decayed + latest.forgotten + latest.promoted) || 1;
    return {
      interference: latest.forgotten / total,
      redundancy: latest.decayed / total,
      homeostasis: latest.promoted / total,
    };
  }, [entries]);

  return (
    <div>
      <h3 className="text-sm font-medium text-white mb-3">Forgetting Mechanisms</h3>
      <div className="grid grid-cols-3 gap-3">
        <GaugeMeter
          label="Interference"
          description="Competing memories"
          value={stats.interference}
          color={NEURAL.conflict}
        />
        <GaugeMeter
          label="Redundancy"
          description="Duplicate decay"
          value={stats.redundancy}
          color={NEURAL.pending}
        />
        <GaugeMeter
          label="Homeostasis"
          description="Capacity balance"
          value={stats.homeostasis}
          color={NEURAL.success}
        />
      </div>
    </div>
  );
}
