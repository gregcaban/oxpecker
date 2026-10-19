import Link from "next/link";
import CodeBlock from "./CodeBlock";
import { StatusBadge, VerdictBadge } from "./Badge";
import { buildLineGenMap } from "@/lib/genTrace";
import { formatBytes } from "@/lib/format";
import type { Sample } from "@/lib/types";

export default function SampleCard({
  sample,
  runId,
  highlightGen,
  maxLines = 10,
  query,
}: {
  sample: Sample;
  runId: string;
  highlightGen?: string;
  maxLines?: number;
  /** Extra query string (without leading "?") to carry filter context into the detail page for j/k nav. */
  query?: string;
}) {
  const allLines = sample.source.replace(/\n$/, "").split("\n");
  const truncated = allLines.length > maxLines;
  const preview = allLines.slice(0, maxLines).join("\n");

  const lineGenMap = buildLineGenMap(sample.generatorTrace);
  const lineGenerator = (i: number) => {
    const gen = lineGenMap.get(i);
    if (!gen) return undefined;
    if (highlightGen && gen !== highlightGen) return undefined;
    return gen;
  };

  return (
    <div className="surface flex flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--border)]">
        <Link
          href={`/programs/${encodeURIComponent(sample.id)}?${query ?? `run=${encodeURIComponent(runId)}`}`}
          className="font-mono text-xs text-[var(--accent)] hover:underline"
        >
          {sample.id}
        </Link>
        <div className="flex items-center gap-1.5">
          <VerdictBadge kind={sample.verdict?.kind} />
          <span className="text-xs text-[var(--muted)]">{formatBytes(sample.stats?.bytes)}</span>
        </div>
      </div>
      <div className="px-3 py-2 flex items-center gap-1.5 text-xs text-[var(--muted)] border-b border-[var(--border)]">
        <span>template: {sample.template || "—"}</span>
        <span className="mx-1">·</span>
        <span className="flex items-center gap-1">
          interpreted <StatusBadge status={sample.results?.interpreted?.status} />
        </span>
        <span className="flex items-center gap-1">
          compiled <StatusBadge status={sample.results?.compiled?.status} />
        </span>
      </div>
      <div className="flex-1 overflow-hidden">
        <CodeBlock
          code={preview}
          lineGenerator={lineGenerator}
          showLineNumbers={false}
          className="code-block-flush code-block-sm"
        />
      </div>
      {truncated && (
        <div className="px-3 py-1.5 text-[11px] text-[var(--muted)] border-t border-[var(--border)]">
          + {allLines.length - maxLines} more lines
        </div>
      )}
    </div>
  );
}
