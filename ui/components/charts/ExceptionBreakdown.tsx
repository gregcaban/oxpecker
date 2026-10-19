"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export default function ExceptionBreakdown({ data }: { data: { name: string; count: number }[] }) {
  if (data.length === 0) {
    return <p className="text-sm text-[var(--muted)] py-8 text-center">No exceptions recorded.</p>;
  }
  const height = Math.max(160, data.length * 26);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 16, left: 0, bottom: 4 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
        <XAxis type="number" tick={{ fontSize: 11, fill: "var(--muted)" }} axisLine={false} tickLine={false} allowDecimals={false} />
        <YAxis
          type="category"
          dataKey="name"
          tick={{ fontSize: 11, fill: "var(--foreground)" }}
          axisLine={false}
          tickLine={false}
          width={160}
        />
        <Tooltip
          cursor={{ fill: "var(--surface-2)" }}
          contentStyle={{
            background: "var(--surface)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
        />
        <Bar dataKey="count" fill="var(--bad)" radius={[0, 3, 3, 0]} barSize={14} />
      </BarChart>
    </ResponsiveContainer>
  );
}
