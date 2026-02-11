"use client";

import {
  Brain,
  Zap,
  Shield,
  FolderTree,
  AlertTriangle,
  GitBranch,
} from "lucide-react";
import { StatCard } from "./stat-card";
import { COLORS } from "@/lib/utils/colors";

export function StatCardsRow({
  totalMemories,
  smlCount,
  lmlCount,
  categoryCount,
  conflictCount,
  pendingCount,
}: {
  totalMemories: number;
  smlCount: number;
  lmlCount: number;
  categoryCount: number;
  conflictCount: number;
  pendingCount: number;
}) {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      <StatCard
        label="Total Memories"
        value={totalMemories}
        icon={Brain}
        color={COLORS.brand}
      />
      <StatCard
        label="Short-term (SML)"
        value={smlCount}
        icon={Zap}
        color={COLORS.sml}
      />
      <StatCard
        label="Long-term (LML)"
        value={lmlCount}
        icon={Shield}
        color={COLORS.lml}
      />
      <StatCard
        label="Categories"
        value={categoryCount}
        icon={FolderTree}
        color={COLORS.brand}
      />
      <StatCard
        label="Conflicts"
        value={conflictCount}
        icon={AlertTriangle}
        color={COLORS.destructive}
        badge={conflictCount > 0 ? String(conflictCount) : undefined}
      />
      <StatCard
        label="Pending Proposals"
        value={pendingCount}
        icon={GitBranch}
        color={COLORS.scene}
      />
    </div>
  );
}
