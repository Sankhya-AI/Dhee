"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { COLORS } from "@/lib/utils/colors";

export function CategoriesBar({
  categories,
}: {
  categories: Record<string, number>;
}) {
  const data = Object.entries(categories)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 8)
    .map(([name, count]) => ({ name, count }));

  if (data.length === 0) {
    return (
      <div className="glass p-4">
        <h3 className="text-sm font-medium text-slate-300 mb-2">Top Categories</h3>
        <p className="text-sm py-8 text-center" style={{ color: '#64748b' }}>No categories yet</p>
      </div>
    );
  }

  return (
    <div className="glass p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-2">Top Categories</h3>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 0, right: 8 }}>
            <XAxis type="number" tick={{ fontSize: 10, fill: '#64748b' }} tickLine={false} axisLine={false} />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fontSize: 10, fill: '#94a3b8' }}
              tickLine={false}
              axisLine={false}
              width={80}
            />
            <Tooltip
              contentStyle={{
                fontSize: 12,
                backgroundColor: 'rgba(26,26,58,0.9)',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 8,
                color: '#e2e8f0',
              }}
            />
            <Bar dataKey="count" fill={COLORS.brand} radius={[0, 3, 3, 0]} barSize={14} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
