"use client";

import { cn } from "@/lib/utils/format";

export function LayerBadge({ layer }: { layer: string }) {
  const isSml = layer === "sml";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold uppercase tracking-wide",
        isSml
          ? "bg-cyan-500/10 text-cyan-400 ring-1 ring-cyan-500/20"
          : "bg-amber-500/10 text-amber-400 ring-1 ring-amber-500/20"
      )}
    >
      {layer}
    </span>
  );
}
