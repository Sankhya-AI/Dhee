"use client";

import { NEURAL } from "@/lib/utils/neural-palette";
import { useStats } from "@/lib/hooks/use-stats";

export function SleepVisualizer() {
  const { data: stats } = useStats();
  const sml = stats?.sml_count ?? 0;
  const lml = stats?.lml_count ?? 0;
  const total = sml + lml || 1;

  return (
    <div className="glass p-6">
      <h3 className="text-sm font-medium text-white mb-4">Sleep Cycle — Consolidation Flow</h3>

      <div className="flex items-center justify-between gap-4">
        {/* SML pool */}
        <div className="flex-1 text-center">
          <div
            className="relative mx-auto w-24 h-24 rounded-full flex items-center justify-center"
            style={{ border: `2px solid ${NEURAL.sml}30`, boxShadow: `0 0 20px ${NEURAL.sml}15` }}
          >
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ backgroundColor: `${NEURAL.sml}15` }}
            >
              <span className="text-lg font-bold" style={{ color: NEURAL.sml }}>{sml}</span>
            </div>
          </div>
          <p className="text-xs mt-2" style={{ color: NEURAL.sml }}>SML</p>
          <p className="text-[10px]" style={{ color: NEURAL.shallow }}>Short-term</p>
        </div>

        {/* Flow arrows */}
        <div className="flex flex-col items-center gap-1">
          <div className="flex items-center gap-1">
            <div className="w-12 h-px" style={{ background: `linear-gradient(to right, ${NEURAL.sml}, ${NEURAL.episodic})` }} />
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
              <path d="M0 0L8 4L0 8Z" fill={NEURAL.episodic} />
            </svg>
          </div>
          <p className="text-[9px] font-medium" style={{ color: NEURAL.episodic }}>CONSOLIDATE</p>
          <div className="flex items-center gap-1">
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none" style={{ transform: 'rotate(180deg)' }}>
              <path d="M0 0L8 4L0 8Z" fill={NEURAL.conflict} />
            </svg>
            <div className="w-12 h-px" style={{ background: `linear-gradient(to left, ${NEURAL.lml}, ${NEURAL.conflict})` }} />
          </div>
          <p className="text-[9px] font-medium" style={{ color: NEURAL.conflict }}>DEMOTE</p>
        </div>

        {/* Hippocampus */}
        <div className="flex-1 text-center">
          <div
            className="relative mx-auto w-28 h-28 rounded-full flex items-center justify-center animate-neural-pulse"
            style={{
              background: `radial-gradient(circle, ${NEURAL.episodic}15 0%, transparent 70%)`,
              border: `2px solid ${NEURAL.episodic}30`,
            }}
          >
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ backgroundColor: `${NEURAL.episodic}20` }}
            >
              <span className="text-[10px] font-medium" style={{ color: NEURAL.episodic }}>HPC</span>
            </div>
          </div>
          <p className="text-xs mt-2" style={{ color: NEURAL.episodic }}>Hippocampus</p>
          <p className="text-[10px]" style={{ color: NEURAL.shallow }}>Processing</p>
        </div>

        {/* Flow arrows */}
        <div className="flex flex-col items-center gap-1">
          <div className="flex items-center gap-1">
            <div className="w-12 h-px" style={{ background: `linear-gradient(to right, ${NEURAL.episodic}, ${NEURAL.lml})` }} />
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
              <path d="M0 0L8 4L0 8Z" fill={NEURAL.lml} />
            </svg>
          </div>
          <p className="text-[9px] font-medium" style={{ color: NEURAL.lml }}>PROMOTE</p>
        </div>

        {/* LML pool */}
        <div className="flex-1 text-center">
          <div
            className="relative mx-auto w-24 h-24 rounded-full flex items-center justify-center"
            style={{ border: `2px solid ${NEURAL.lml}30`, boxShadow: `0 0 20px ${NEURAL.lml}15` }}
          >
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center"
              style={{ backgroundColor: `${NEURAL.lml}15` }}
            >
              <span className="text-lg font-bold" style={{ color: NEURAL.lml }}>{lml}</span>
            </div>
          </div>
          <p className="text-xs mt-2" style={{ color: NEURAL.lml }}>LML</p>
          <p className="text-[10px]" style={{ color: NEURAL.shallow }}>Long-term</p>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mt-6">
        <div className="flex justify-between text-[10px] mb-1" style={{ color: NEURAL.shallow }}>
          <span>Memory Distribution</span>
          <span>{((lml / total) * 100).toFixed(0)}% consolidated</span>
        </div>
        <div className="h-2 rounded-full flex overflow-hidden" style={{ backgroundColor: 'rgba(124,58,237,0.1)' }}>
          <div style={{ width: `${(sml / total) * 100}%`, backgroundColor: NEURAL.sml }} />
          <div style={{ width: `${(lml / total) * 100}%`, backgroundColor: NEURAL.lml }} />
        </div>
      </div>
    </div>
  );
}
