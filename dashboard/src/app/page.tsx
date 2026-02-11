"use client";

import dynamic from "next/dynamic";
import { useStats } from "@/lib/hooks/use-stats";
import { useConflicts } from "@/lib/hooks/use-conflicts";
import { useStaging } from "@/lib/hooks/use-staging";
import { useDecayLog } from "@/lib/hooks/use-decay-log";
import { StatCardsRow } from "@/components/dashboard/stat-cards-row";
import { LayerDonut } from "@/components/dashboard/layer-donut";
import { CategoriesBar } from "@/components/dashboard/categories-bar";
import { DecaySparkline } from "@/components/dashboard/decay-sparkline";
import { useScrollProgress } from "@/lib/hooks/use-scroll-progress";
import { NEURAL } from "@/lib/utils/neural-palette";

const BrainCanvas = dynamic(() => import("@/components/brain/brain-canvas").then(m => ({ default: m.BrainCanvas })), {
  ssr: false,
  loading: () => (
    <div className="h-screen flex items-center justify-center" style={{ background: NEURAL.void }}>
      <div className="animate-neural-pulse text-purple-400 text-sm">Loading neural mesh...</div>
    </div>
  ),
});

function ScrollSection({
  children,
  className,
  id,
}: {
  children: React.ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section id={id} className={`min-h-screen flex items-center justify-center px-6 py-20 ${className || ''}`}>
      {children}
    </section>
  );
}

export default function BrainHeroPage() {
  const scrollProgress = useScrollProgress();
  const { data: stats } = useStats();
  const { data: conflicts } = useConflicts("UNRESOLVED");
  const { data: staging } = useStaging("PENDING");
  const { data: decayLog } = useDecayLog();

  const totalMemories = stats?.total_memories ?? 0;
  const smlCount = stats?.sml_count ?? 0;
  const lmlCount = stats?.lml_count ?? 0;
  const categoryCount = stats ? Object.keys(stats.categories).length : 0;
  const conflictCount = conflicts?.conflicts?.length ?? 0;
  const pendingCount = staging?.commits?.length ?? 0;

  return (
    <div className="relative">
      {/* Fixed 3D brain background */}
      <div className="fixed inset-0 z-0" style={{ top: '3.5rem', left: '14rem' }}>
        <BrainCanvas scrollProgress={scrollProgress} />
      </div>

      {/* Scroll sections overlay */}
      <div className="relative z-10 pointer-events-none">
        {/* Section 0: Brain overview + floating stats */}
        <ScrollSection id="overview">
          <div className="w-full max-w-6xl pointer-events-auto">
            <div className="text-center mb-12">
              <h1 className="text-4xl font-bold text-white mb-3" style={{ textShadow: `0 0 40px ${NEURAL.neuralGlow}` }}>
                Neural Memory
              </h1>
              <p className="text-lg" style={{ color: NEURAL.shallow }}>
                {totalMemories} memories across {categoryCount} categories
              </p>
            </div>
            <div className="opacity-80">
              <StatCardsRow
                totalMemories={totalMemories}
                smlCount={smlCount}
                lmlCount={lmlCount}
                categoryCount={categoryCount}
                conflictCount={conflictCount}
                pendingCount={pendingCount}
              />
            </div>
          </div>
        </ScrollSection>

        {/* Section 1: Layer split — SML vs LML */}
        <ScrollSection id="layers">
          <div className="w-full max-w-4xl pointer-events-auto">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div className="glass p-8 glow-sml">
                <h2 className="text-xl font-semibold mb-2" style={{ color: NEURAL.sml }}>Short-Term Memory</h2>
                <p className="text-3xl font-bold text-white mb-1">{smlCount}</p>
                <p className="text-sm" style={{ color: NEURAL.shallow }}>Active in SML — fast recall, rapid decay</p>
                <div className="mt-4 h-1 rounded-full" style={{ backgroundColor: `${NEURAL.sml}30` }}>
                  <div
                    className="h-1 rounded-full transition-all duration-1000"
                    style={{
                      width: totalMemories > 0 ? `${(smlCount / totalMemories) * 100}%` : '0%',
                      backgroundColor: NEURAL.sml,
                      boxShadow: `0 0 8px ${NEURAL.sml}`,
                    }}
                  />
                </div>
              </div>
              <div className="glass p-8 glow-lml">
                <h2 className="text-xl font-semibold mb-2" style={{ color: NEURAL.lml }}>Long-Term Memory</h2>
                <p className="text-3xl font-bold text-white mb-1">{lmlCount}</p>
                <p className="text-sm" style={{ color: NEURAL.shallow }}>Consolidated in LML — durable, stable</p>
                <div className="mt-4 h-1 rounded-full" style={{ backgroundColor: `${NEURAL.lml}30` }}>
                  <div
                    className="h-1 rounded-full transition-all duration-1000"
                    style={{
                      width: totalMemories > 0 ? `${(lmlCount / totalMemories) * 100}%` : '0%',
                      backgroundColor: NEURAL.lml,
                      boxShadow: `0 0 8px ${NEURAL.lml}`,
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        </ScrollSection>

        {/* Section 2: Categories + Distribution */}
        <ScrollSection id="clusters">
          <div className="w-full max-w-5xl pointer-events-auto">
            <div className="text-center mb-8">
              <h2 className="text-2xl font-bold text-white mb-2">Category Clusters</h2>
              <p style={{ color: NEURAL.shallow }}>How memories organize into knowledge regions</p>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <LayerDonut smlCount={smlCount} lmlCount={lmlCount} />
              <CategoriesBar categories={stats?.categories ?? {}} />
            </div>
          </div>
        </ScrollSection>

        {/* Section 3: Decay landscape */}
        <ScrollSection id="decay">
          <div className="w-full max-w-5xl pointer-events-auto">
            <div className="text-center mb-8">
              <h2 className="text-2xl font-bold text-white mb-2">Decay Landscape</h2>
              <p style={{ color: NEURAL.shallow }}>Memory strength fading over time — the rhythm of forgetting</p>
            </div>
            <div className="max-w-3xl mx-auto">
              <DecaySparkline entries={decayLog?.entries ?? []} />
            </div>
          </div>
        </ScrollSection>

        {/* Section 4: CTA scroll end */}
        <ScrollSection id="explore">
          <div className="text-center pointer-events-auto">
            <h2 className="text-3xl font-bold text-white mb-4" style={{ textShadow: `0 0 40px ${NEURAL.neuralGlow}` }}>
              Explore the Cortex
            </h2>
            <p className="text-lg mb-8" style={{ color: NEURAL.shallow }}>
              Dive into the cluster explorer for full interactive visualization
            </p>
            <a
              href="/cortex"
              className="inline-flex items-center gap-2 rounded-xl px-6 py-3 text-sm font-medium text-white transition-all hover:scale-105"
              style={{
                background: `linear-gradient(135deg, ${NEURAL.episodic}, ${NEURAL.semantic})`,
                boxShadow: `0 0 30px ${NEURAL.neuralGlow}`,
              }}
            >
              Open Cortex
            </a>
          </div>
        </ScrollSection>
      </div>
    </div>
  );
}
