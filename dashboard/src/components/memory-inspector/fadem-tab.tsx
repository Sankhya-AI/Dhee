"use client";

import { LayerBadge } from "@/components/shared/layer-badge";
import { StrengthIndicator } from "@/components/shared/strength-indicator";
import {
  decayProjectionSeries,
  FORGET_THRESHOLD,
  PROMOTE_THRESHOLD,
  PROMOTE_ACCESS_THRESHOLD,
} from "@/lib/utils/decay-math";
import { timeAgo } from "@/lib/utils/format";
import { COLORS } from "@/lib/utils/colors";
import { NEURAL } from "@/lib/utils/neural-palette";
import type { Memory } from "@/lib/types/memory";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

function StrengthRing({ strength, layer }: { strength: number; layer: string }) {
  const pct = strength * 100;
  const circumference = 2 * Math.PI * 40;
  const offset = circumference - (pct / 100) * circumference;
  const color = layer === "sml" ? COLORS.sml : COLORS.lml;

  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width="96" height="96" viewBox="0 0 96 96">
        <circle cx="48" cy="48" r="40" fill="none" stroke="rgba(124,58,237,0.1)" strokeWidth="6" />
        <circle
          cx="48"
          cy="48"
          r="40"
          fill="none"
          stroke={color}
          strokeWidth="6"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 48 48)"
          className="transition-all duration-500"
          style={{ filter: `drop-shadow(0 0 6px ${color}50)` }}
        />
      </svg>
      <span className="absolute text-lg font-semibold text-white">
        {Math.round(pct)}%
      </span>
    </div>
  );
}

function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = Math.min((value / max) * 100, 100);
  const met = value >= max;
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span style={{ color: NEURAL.shallow }}>{label}</span>
        <span className={met ? "text-green-400 font-medium" : ""} style={!met ? { color: NEURAL.shallow } : undefined}>
          {typeof value === 'number' && value < 1 ? value.toFixed(2) : value} / {max}
        </span>
      </div>
      <div className="h-1.5 rounded-full" style={{ backgroundColor: 'rgba(124,58,237,0.1)' }}>
        <div
          className="h-1.5 rounded-full transition-all"
          style={{
            width: `${pct}%`,
            backgroundColor: met ? NEURAL.success : '#475569',
          }}
        />
      </div>
    </div>
  );
}

export function FadeMemTab({ memory }: { memory: Memory }) {
  const series = decayProjectionSeries(
    memory.strength,
    memory.access_count,
    memory.layer
  );

  return (
    <div className="space-y-6">
      {/* Strength ring + metadata */}
      <div className="flex items-center gap-5">
        <StrengthRing strength={memory.strength} layer={memory.layer} />
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <LayerBadge layer={memory.layer} />
          </div>
          <p className="text-xs" style={{ color: NEURAL.shallow }}>
            Accessed {memory.access_count} time{memory.access_count !== 1 ? "s" : ""}
          </p>
          {memory.last_accessed && (
            <p className="text-xs" style={{ color: NEURAL.shallow }}>
              Last accessed {timeAgo(memory.last_accessed)}
            </p>
          )}
        </div>
      </div>

      {/* 30-day decay projection */}
      <div>
        <h4 className="text-xs font-medium text-slate-300 mb-2">
          30-Day Decay Projection
        </h4>
        <div className="h-32">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="decayGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop
                    offset="5%"
                    stopColor={memory.layer === "sml" ? COLORS.sml : COLORS.lml}
                    stopOpacity={0.3}
                  />
                  <stop
                    offset="95%"
                    stopColor={memory.layer === "sml" ? COLORS.sml : COLORS.lml}
                    stopOpacity={0}
                  />
                </linearGradient>
              </defs>
              <XAxis dataKey="day" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} />
              <YAxis
                domain={[0, 1]}
                tick={{ fontSize: 10, fill: '#64748b' }}
                tickLine={false}
                axisLine={false}
                width={28}
              />
              <Tooltip
                contentStyle={{
                  fontSize: 11,
                  backgroundColor: 'rgba(26,26,58,0.9)',
                  border: '1px solid rgba(124,58,237,0.2)',
                  borderRadius: 8,
                  color: '#e2e8f0',
                }}
                formatter={(v) => [`${(Number(v) * 100).toFixed(1)}%`, "Strength"]}
                labelFormatter={(l) => `Day ${l}`}
              />
              <ReferenceLine y={FORGET_THRESHOLD} stroke={NEURAL.conflict} strokeDasharray="3 3" />
              <ReferenceLine y={PROMOTE_THRESHOLD} stroke={NEURAL.success} strokeDasharray="3 3" />
              <Area
                type="monotone"
                dataKey="strength"
                stroke={memory.layer === "sml" ? COLORS.sml : COLORS.lml}
                fill="url(#decayGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="flex gap-4 mt-1 text-[10px]" style={{ color: NEURAL.forgotten }}>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-px" style={{ backgroundColor: NEURAL.conflict }} /> Forget ({FORGET_THRESHOLD})
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-px" style={{ backgroundColor: NEURAL.success }} /> Promote ({PROMOTE_THRESHOLD})
          </span>
        </div>
      </div>

      {/* Promotion pathway */}
      {memory.layer === "sml" && (
        <div>
          <h4 className="text-xs font-medium text-slate-300 mb-3">
            Promotion Pathway (SML → LML)
          </h4>
          <div className="space-y-2.5">
            <ProgressBar
              value={memory.access_count}
              max={PROMOTE_ACCESS_THRESHOLD}
              label="Access count"
            />
            <ProgressBar
              value={memory.strength}
              max={PROMOTE_THRESHOLD}
              label="Strength"
            />
          </div>
        </div>
      )}

      {/* Current strength bar */}
      <div>
        <h4 className="text-xs font-medium text-slate-300 mb-2">Current Strength</h4>
        <StrengthIndicator strength={memory.strength} layer={memory.layer} size="md" />
      </div>
    </div>
  );
}
