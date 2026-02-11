"use client";

import dynamic from "next/dynamic";
import { ClusterToolbar } from "@/components/clusters/cluster-toolbar";

const ClusterCanvas = dynamic(
  () => import("@/components/clusters/cluster-canvas").then(m => ({ default: m.ClusterCanvas })),
  {
    ssr: false,
    loading: () => (
      <div className="h-full flex items-center justify-center">
        <div className="animate-neural-pulse text-purple-400 text-sm">Mapping cortex...</div>
      </div>
    ),
  }
);

export default function CortexPage() {
  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-4 py-3">
        <div>
          <h1 className="text-lg font-semibold text-white">Cortex</h1>
          <p className="text-xs" style={{ color: '#64748b' }}>Cluster explorer — regroup memories by any dimension</p>
        </div>
        <ClusterToolbar />
      </div>

      {/* Canvas */}
      <div className="flex-1 relative">
        <ClusterCanvas />
      </div>
    </div>
  );
}
