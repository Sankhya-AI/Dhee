"use client";

import { InboxIcon } from "lucide-react";

export function EmptyState({
  title = "No data",
  description = "Nothing to show yet.",
  icon: Icon = InboxIcon,
}: {
  title?: string;
  description?: string;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Icon className="h-12 w-12 mb-4 text-slate-700" />
      <h3 className="text-sm font-medium text-slate-300">{title}</h3>
      <p className="mt-1 text-sm" style={{ color: '#64748b' }}>{description}</p>
    </div>
  );
}
