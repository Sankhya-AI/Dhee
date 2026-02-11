"use client";

import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip, Legend } from "recharts";
import { COLORS } from "@/lib/utils/colors";

export function LayerDonut({
  smlCount,
  lmlCount,
}: {
  smlCount: number;
  lmlCount: number;
}) {
  const data = [
    { name: "SML", value: smlCount },
    { name: "LML", value: lmlCount },
  ];
  const colors = [COLORS.sml, COLORS.lml];

  return (
    <div className="glass p-4">
      <h3 className="text-sm font-medium text-slate-300 mb-2">Layer Distribution</h3>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={50}
              outerRadius={75}
              paddingAngle={2}
              dataKey="value"
            >
              {data.map((_, i) => (
                <Cell key={i} fill={colors[i]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                fontSize: 12,
                backgroundColor: 'rgba(26,26,58,0.9)',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 8,
                color: '#e2e8f0',
              }}
            />
            <Legend
              verticalAlign="bottom"
              height={24}
              iconType="circle"
              iconSize={8}
              formatter={(value) => (
                <span className="text-xs" style={{ color: '#94a3b8' }}>{value}</span>
              )}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
