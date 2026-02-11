"use client";

import { SceneCorridor } from "@/components/hippocampus/scene-corridor";
import { SleepVisualizer } from "@/components/hippocampus/sleep-visualizer";
import { TraceCascadeViz } from "@/components/hippocampus/trace-cascade-viz";
import { ForgettingDashboard } from "@/components/hippocampus/forgetting-dashboard";
import { DecaySparkline } from "@/components/dashboard/decay-sparkline";
import { useDecayLog } from "@/lib/hooks/use-decay-log";

export default function HippocampusPage() {
  const { data: decayLog } = useDecayLog();

  return (
    <div className="space-y-8 p-6">
      <div>
        <h1 className="text-lg font-semibold text-white">Hippocampus</h1>
        <p className="text-xs" style={{ color: '#64748b' }}>Memory lifecycle — scenes, sleep cycles, distillation, and forgetting</p>
      </div>

      {/* Scene corridor */}
      <div>
        <h2 className="text-sm font-medium text-slate-300 mb-3">Memory Palace — Scene Timeline</h2>
        <SceneCorridor />
      </div>

      {/* Sleep cycle visualizer */}
      <SleepVisualizer />

      {/* Trace cascade + Decay */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <TraceCascadeViz />
        <DecaySparkline entries={decayLog?.entries ?? []} />
      </div>

      {/* Forgetting dashboard */}
      <ForgettingDashboard />
    </div>
  );
}
