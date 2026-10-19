type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

export default function StatTile({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: Tone;
}) {
  const color = tone === "neutral" ? "var(--foreground)" : `var(--${tone})`;
  return (
    <div className="surface p-4 flex flex-col gap-1 min-w-0">
      <span className="text-xs uppercase tracking-wide text-[var(--muted)]">{label}</span>
      <span className="text-2xl font-semibold tabular-nums" style={{ color }}>
        {value}
      </span>
      {sub && <span className="text-xs text-[var(--muted)]">{sub}</span>}
    </div>
  );
}
