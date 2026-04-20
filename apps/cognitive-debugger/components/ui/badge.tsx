import * as React from "react";

import { cn } from "@/lib/utils";

const toneClasses: Record<string, string> = {
  neutral: "border-stone bg-white text-ink",
  success: "border-[#bfd3bf] bg-[var(--success-bg)] text-success",
  warning: "border-[#ddcf9d] bg-[var(--warning-bg)] text-warning",
  danger: "border-[#e1c0bb] bg-[var(--danger-bg)] text-danger",
  info: "border-[#cad7d9] bg-[var(--info-bg)] text-[#49656a]",
};

export function Badge({
  children,
  className,
  tone = "neutral",
}: React.PropsWithChildren<{ className?: string; tone?: keyof typeof toneClasses }>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.08em]",
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
