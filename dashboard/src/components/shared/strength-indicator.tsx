"use client";

import { layerColor } from "@/lib/utils/colors";

export function StrengthIndicator({
  strength,
  layer,
  size = "sm",
}: {
  strength: number;
  layer: string;
  size?: "sm" | "md";
}) {
  const pct = Math.round(strength * 100);
  const color = layerColor(layer);
  const h = size === "sm" ? "h-1.5" : "h-2.5";

  return (
    <div className="flex items-center gap-2">
      <div className={`flex-1 rounded-full ${h}`} style={{ backgroundColor: 'rgba(124, 58, 237, 0.1)' }}>
        <div
          className={`${h} rounded-full transition-all`}
          style={{
            width: `${pct}%`,
            backgroundColor: color,
            boxShadow: `0 0 8px ${color}40`,
          }}
        />
      </div>
      <span className="text-xs tabular-nums w-8 text-right" style={{ color: '#94a3b8' }}>
        {pct}%
      </span>
    </div>
  );
}
