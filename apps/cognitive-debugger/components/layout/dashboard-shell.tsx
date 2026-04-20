"use client";

import { useState } from "react";

import { Sidebar } from "@/components/layout/sidebar";

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex min-h-screen bg-transparent">
      <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((value) => !value)} />
      <div className="flex-1 px-4 pb-4 pt-0">
        <div className="main-pattern soft-scrollbar mt-4 flex h-[calc(100vh_-_1rem)] flex-col overflow-hidden rounded-tl-shell border border-[#e7e7e7] bg-white px-4 shadow-panel sm:px-6 lg:px-8">
          <div className="soft-scrollbar flex-1 overflow-y-auto">{children}</div>
        </div>
      </div>
    </div>
  );
}
