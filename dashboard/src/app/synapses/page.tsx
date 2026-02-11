"use client";

import dynamic from "next/dynamic";
import { useConflicts } from "@/lib/hooks/use-conflicts";
import { useStaging } from "@/lib/hooks/use-staging";
import { ConflictSynapse } from "@/components/synapses/conflict-synapse";
import { StagingSynapse } from "@/components/synapses/staging-synapse";
import { EmptyState } from "@/components/shared/empty-state";
import { AlertTriangle, GitBranch, Cable } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils/format";
import { NEURAL } from "@/lib/utils/neural-palette";

const SynapseGraph = dynamic(
  () => import("@/components/synapses/synapse-graph").then(m => ({ default: m.SynapseGraph })),
  { ssr: false, loading: () => <div className="h-full flex items-center justify-center"><div className="animate-neural-pulse text-purple-400 text-sm">Mapping synapses...</div></div> }
);

type Tab = "graph" | "conflicts" | "staging";

export default function SynapsesPage() {
  const [tab, setTab] = useState<Tab>("graph");
  const { data: conflictsData, mutate: mutateConflicts } = useConflicts();
  const { data: stagingData, mutate: mutateStaging } = useStaging();

  const conflicts = conflictsData?.conflicts ?? [];
  const commits = stagingData?.commits ?? [];
  const unresolvedCount = conflicts.filter(c => !c.resolution).length;
  const pendingCount = commits.filter(c => c.status === "PENDING").length;

  return (
    <div className="flex h-[calc(100vh-3.5rem)] flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4">
        <div>
          <h1 className="text-lg font-semibold text-white">Synapses</h1>
          <p className="text-xs" style={{ color: '#64748b' }}>Connections, conflicts, and staging proposals</p>
        </div>
        <div className="flex items-center gap-1 glass-subtle p-1">
          {([
            { id: "graph" as Tab, label: "Graph", icon: Cable },
            { id: "conflicts" as Tab, label: `Conflicts${unresolvedCount > 0 ? ` (${unresolvedCount})` : ''}`, icon: AlertTriangle },
            { id: "staging" as Tab, label: `Staging${pendingCount > 0 ? ` (${pendingCount})` : ''}`, icon: GitBranch },
          ]).map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={cn(
                "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all",
                tab === id ? "text-purple-300" : "text-slate-500 hover:text-slate-300"
              )}
              style={tab === id ? { background: 'rgba(124, 58, 237, 0.15)' } : undefined}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {tab === "graph" && (
          <div className="h-full">
            <SynapseGraph />
          </div>
        )}

        {tab === "conflicts" && (
          <div className="h-full overflow-y-auto px-6 pb-6">
            {conflicts.length === 0 ? (
              <EmptyState title="No conflicts" description="No memory conflicts detected." icon={AlertTriangle} />
            ) : (
              <div className="space-y-3 max-w-3xl">
                {conflicts.map(c => (
                  <ConflictSynapse key={c.id} conflict={c} onMutate={mutateConflicts} />
                ))}
              </div>
            )}
          </div>
        )}

        {tab === "staging" && (
          <div className="h-full overflow-y-auto px-6 pb-6">
            {commits.length === 0 ? (
              <EmptyState title="No proposals" description="No staging proposals to review." icon={GitBranch} />
            ) : (
              <div className="space-y-3 max-w-3xl">
                {commits.map(c => (
                  <StagingSynapse key={c.id} commit={c} onMutate={mutateStaging} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
