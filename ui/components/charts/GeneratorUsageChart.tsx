"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export interface UsageDatum {
  name: string;
  count: number;
  rare: boolean;
}

export default function GeneratorUsageChart({ data }: { data: UsageDatum[] }) {
  const height = Math.max(200, data.length * 16);
  return (
    <div style={{ height: Math.min(height, 520), overflowY: "auto" }} className="pr-1">
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fontSize: 10, fill: "var(--muted)" }}
            axisLine={false}
            tickLine={false}
            width={140}
            interval={0}
          />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            contentStyle={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value, _n, p) => [
              String(value),
              (p.payload as UsageDatum).rare ? "uses (rarely fires)" : "uses",
            ]}
          />
          <Bar dataKey="count" radius={[0, 3, 3, 0]} barSize={10}>
            {data.map((d) => (
              <Cell key={d.name} fill={d.rare ? "var(--bad)" : "var(--accent)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
