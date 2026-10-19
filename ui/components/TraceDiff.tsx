import { firstDiffIndex } from "@/lib/diff";

export default function TraceDiff({
  title,
  left,
  right,
  leftLabel = "interpreted",
  rightLabel = "compiled",
}: {
  title: string;
  left: string[] | undefined;
  right: string[] | undefined;
  leftLabel?: string;
  rightLabel?: string;
}) {
  const a = left ?? [];
  const b = right ?? [];
  const len = Math.max(a.length, b.length);
  if (len === 0) return null;
  const diffAt = firstDiffIndex(a, b);

  return (
    <div className="surface p-4">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-sm font-medium text-[var(--muted)]">{title}</h3>
        {diffAt !== null && (
          <span className="text-xs text-[var(--bad)]">first diff at index {diffAt}</span>
        )}
      </div>
      <div className="font-mono text-xs overflow-x-auto">
        <div className="grid grid-cols-[2.5rem_1fr_1fr] gap-x-3 text-[var(--muted)] pb-1 border-b border-[var(--border)] mb-1">
          <span>#</span>
          <span>{leftLabel}</span>
          <span>{rightLabel}</span>
        </div>
        {Array.from({ length: len }).map((_, i) => {
          const differs = a[i] !== b[i];
          return (
            <div
              key={i}
              className={`grid grid-cols-[2.5rem_1fr_1fr] gap-x-3 py-0.5 rounded ${
                differs ? "bg-[color-mix(in_srgb,var(--bad)_10%,transparent)]" : ""
              } ${i === diffAt ? "ring-1 ring-[var(--bad)]" : ""}`}
            >
              <span className="text-[var(--muted)]">{i}</span>
              <span className="whitespace-pre-wrap break-all">{a[i] ?? <span className="text-[var(--muted)]">—</span>}</span>
              <span className="whitespace-pre-wrap break-all">{b[i] ?? <span className="text-[var(--muted)]">—</span>}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
