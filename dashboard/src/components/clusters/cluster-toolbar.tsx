"use client";

import { useClusterStore, type ClusterDimension } from "@/lib/stores/cluster-store";
import { cn } from "@/lib/utils/format";
import {
  FolderTree,
  Brain,
  Layers,
  Film,
  Sparkles,
  Gauge,
  Clock,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const DIMENSIONS: { id: ClusterDimension; label: string; icon: LucideIcon }[] = [
  { id: "category", label: "Category", icon: FolderTree },
  { id: "memory_type", label: "Type", icon: Brain },
  { id: "layer", label: "Layer", icon: Layers },
  { id: "scene", label: "Scene", icon: Film },
  { id: "echo_depth", label: "Echo", icon: Sparkles },
  { id: "strength", label: "Strength", icon: Gauge },
  { id: "time", label: "Time", icon: Clock },
  { id: "profile", label: "Profile", icon: Users },
];

export function ClusterToolbar() {
  const { dimension, setDimension, transitioning } = useClusterStore();

  return (
    <div className="flex items-center gap-1 p-1 glass-subtle">
      {DIMENSIONS.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          onClick={() => setDimension(id)}
          disabled={transitioning}
          className={cn(
            "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all disabled:opacity-50",
            dimension === id
              ? "text-purple-300"
              : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.03]"
          )}
          style={dimension === id ? {
            background: 'rgba(124, 58, 237, 0.15)',
            boxShadow: '0 0 12px rgba(124, 58, 237, 0.1)',
          } : undefined}
        >
          <Icon className="h-3.5 w-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}
