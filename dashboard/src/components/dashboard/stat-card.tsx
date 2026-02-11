"use client";

import type { LucideIcon } from "lucide-react";

export function StatCard({
  label,
  value,
  icon: Icon,
  color = "#7c3aed",
  badge,
}: {
  label: string;
  value: number | string;
  icon: LucideIcon;
  color?: string;
  badge?: string;
}) {
  return (
    <div className="glass p-4 hover:border-purple-500/20 transition-all">
      <div className="flex items-center justify-between">
        <div
          className="flex h-9 w-9 items-center justify-center rounded-lg"
          style={{ backgroundColor: color + "18" }}
        >
          <Icon className="h-4.5 w-4.5" style={{ color }} />
        </div>
        {badge && (
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold ring-1"
            style={{
              backgroundColor: `${color}15`,
              color,
              borderColor: `${color}30`,
            }}
          >
            {badge}
          </span>
        )}
      </div>
      <p className="mt-3 text-2xl font-semibold text-white tabular-nums">{value}</p>
      <p className="mt-0.5 text-xs" style={{ color: '#94a3b8' }}>{label}</p>
    </div>
  );
}
