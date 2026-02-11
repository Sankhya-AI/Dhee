"use client";

import { useScenes } from "@/lib/hooks/use-scenes";
import { NEURAL } from "@/lib/utils/neural-palette";
import { formatDateTime, timeAgo } from "@/lib/utils/format";
import { Film } from "lucide-react";

export function SceneCorridor() {
  const { data } = useScenes();
  const scenes = data?.scenes ?? [];

  if (scenes.length === 0) {
    return (
      <div className="flex items-center justify-center py-16">
        <p className="text-sm" style={{ color: NEURAL.shallow }}>No scenes recorded yet.</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto pb-4">
      <div className="flex gap-4 min-w-max px-2">
        {scenes.map((scene, i) => (
          <div
            key={scene.id}
            className="glass w-64 p-4 shrink-0 transition-all hover:scale-[1.02]"
            style={{
              borderColor: `rgba(124, 58, 237, ${0.1 + (i % 3) * 0.05})`,
            }}
          >
            <div className="flex items-center gap-2 mb-3">
              <Film className="h-4 w-4" style={{ color: NEURAL.episodic }} />
              <span className="text-xs font-medium text-white truncate">
                {scene.title || `Scene ${scene.id.slice(0, 8)}`}
              </span>
            </div>

            {scene.topic && (
              <p className="text-xs mb-2" style={{ color: NEURAL.medium }}>
                {scene.topic}
              </p>
            )}

            {scene.summary && (
              <p className="text-xs mb-3 line-clamp-3" style={{ color: '#cbd5e1' }}>
                {scene.summary}
              </p>
            )}

            <div className="flex items-center justify-between text-[10px]" style={{ color: NEURAL.forgotten }}>
              <span>{scene.memory_count} memories</span>
              <span>{timeAgo(scene.start_time)}</span>
            </div>

            {/* Timeline bar */}
            <div className="mt-3 h-1 rounded-full" style={{ backgroundColor: 'rgba(124,58,237,0.1)' }}>
              <div
                className="h-1 rounded-full"
                style={{
                  width: `${Math.min(100, scene.memory_count * 10)}%`,
                  backgroundColor: NEURAL.episodic,
                  boxShadow: `0 0 6px ${NEURAL.neuralGlow}`,
                }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
