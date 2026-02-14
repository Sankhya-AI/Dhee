import { ArrowUp, ArrowDown, Minus, AlertTriangle } from "lucide-react";
import type { Priority } from "@/types";

const config: Record<Priority, { icon: typeof ArrowUp; color: string; label: string }> = {
  urgent: { icon: AlertTriangle, color: "text-red-400", label: "Urgent" },
  high: { icon: ArrowUp, color: "text-orange-400", label: "High" },
  medium: { icon: Minus, color: "text-muted-foreground", label: "Medium" },
  low: { icon: ArrowDown, color: "text-green-400", label: "Low" },
};

export function PriorityIcon({ priority, className = "h-3.5 w-3.5" }: { priority: Priority; className?: string }) {
  const c = config[priority] || config.medium;
  const Icon = c.icon;
  return <Icon className={`${className} ${c.color}`} />;
}

export function PriorityLabel({ priority }: { priority: Priority }) {
  const c = config[priority] || config.medium;
  return <span className={`text-xs ${c.color}`}>{c.label}</span>;
}
