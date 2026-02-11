"use client";

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { COLORS } from "@/lib/utils/colors";
import type { DecayLogEntry } from "@/lib/api/decay";

export function DecaySparkline({ entries }: { entries: DecayLogEntry[] }) {
  const data = entries.map((e) => ({
    time: new Date(e.timestamp).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    }),
    decayed: e.decayed,
    forgotten: e.forgotten,
    promoted: e.promoted,
  }));

  if (data.length === 0) {
    return (
      <div className="glass p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-2">Decay History</h3>
        <p className="text-sm py-8 text-center" style={{ color: '#64748b' }}>No decay data yet</p>
      </div>
    );
  }

  return (
    <div className="glass p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-2">Decay History</h3>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="decayFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS.lml} stopOpacity={0.3} />
                <stop offset="95%" stopColor={COLORS.lml} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} />
            <YAxis tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} width={28} />
            <Tooltip
              contentStyle={{
                fontSize: 12,
                backgroundColor: 'rgba(26,26,58,0.9)',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 8,
                color: '#e2e8f0',
              }}
            />
            <Area
              type="monotone"
              dataKey="decayed"
              stroke={COLORS.lml}
              fill="url(#decayFill)"
              strokeWidth={2}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
