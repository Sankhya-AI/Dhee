"use client";

import { COLORS } from "@/lib/utils/colors";

const LEGEND_ITEMS = [
  { label: "SML Memory", color: COLORS.sml, shape: "circle" as const },
  { label: "LML Memory", color: COLORS.lml, shape: "circle" as const },
  { label: "Scene Link", color: COLORS.scene, shape: "line" as const },
  { label: "Category Link", color: COLORS.category, shape: "line" as const },
];

export function GraphLegend() {
  return (
    <div className="absolute bottom-4 left-4 glass-subtle px-3 py-2">
      <div className="flex gap-4">
        {LEGEND_ITEMS.map(({ label, color, shape }) => (
          <div key={label} className="flex items-center gap-1.5">
            {shape === "circle" ? (
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ backgroundColor: color }}
              />
            ) : (
              <span
                className="h-px w-4"
                style={{ backgroundColor: color }}
              />
            )}
            <span className="text-[10px]" style={{ color: '#64748b' }}>{label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
