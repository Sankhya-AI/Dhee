"use client";

import { cn } from "@/lib/utils/format";

export function PulseDot({
  color = "#7c3aed",
  size = "sm",
  className,
}: {
  color?: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}) {
  const sizeMap = { sm: "h-2 w-2", md: "h-3 w-3", lg: "h-4 w-4" };
  return (
    <span className={cn("relative inline-flex", className)}>
      <span
        className={cn("animate-ping absolute inline-flex rounded-full opacity-40", sizeMap[size])}
        style={{ backgroundColor: color }}
      />
      <span
        className={cn("relative inline-flex rounded-full", sizeMap[size])}
        style={{ backgroundColor: color }}
      />
    </span>
  );
}
