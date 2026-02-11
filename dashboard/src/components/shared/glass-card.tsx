"use client";

import { cn } from "@/lib/utils/format";

export function GlassCard({
  children,
  className,
  glow,
  ...props
}: {
  children: React.ReactNode;
  className?: string;
  glow?: "episodic" | "semantic" | "sml" | "lml";
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "glass p-4",
        glow === "episodic" && "glow-episodic",
        glow === "semantic" && "glow-semantic",
        glow === "sml" && "glow-sml",
        glow === "lml" && "glow-lml",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
