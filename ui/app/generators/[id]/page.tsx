import Link from "next/link";
import Shell, { EmptyState } from "@/components/Shell";
import CodeBlock from "@/components/CodeBlock";
import SampleCard from "@/components/SampleCard";
import { Badge } from "@/components/Badge";
import { getCurrentRunId, type SearchParams } from "@/lib/run-context";
import { getCatalogueEntry } from "@/lib/catalogue";
import { getPerFocus } from "@/lib/metrics";
import { getSamplesByFocus } from "@/lib/samples";
import { formatBytes, formatNum, formatPct } from "@/lib/format";

export default async function GeneratorDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { id } = await params;
  const sp = await searchParams;
  const runId = await getCurrentRunId(sp);

  if (!runId) {
    return (
      <Shell currentRun={null}>
        <EmptyState title="No runs found" />
      </Shell>
    );
  }

  const [entry, metrics, samples] = await Promise.all([
    getCatalogueEntry(runId, id),
    getPerFocus(runId, id),
    getSamplesByFocus(runId, id, 12),
  ]);

  if (!entry) {
    return (
      <Shell currentRun={runId}>
        <EmptyState title={`"${id}" not found in this run's catalogue`} />
      </Shell>
    );
  }

  const metricRows: { label: string; value: string; tone?: "bad" | "warn" }[] = [
    { label: "Samples", value: formatNum(metrics.samples) },
    { label: "Fired", value: formatNum(metrics.fired) },
    { label: "Usage total", value: formatNum(metrics.usageTotal) },
    { label: "Early-throw rate", value: formatPct(metrics.earlyThrowRate), tone: metrics.earlyThrowRate > 0.1 ? "warn" : undefined },
    { label: "Throw rate", value: formatPct(metrics.throwRate) },
    { label: "Syntax-both rate", value: formatPct(metrics.syntaxBothRate), tone: metrics.syntaxBothRate > 0 ? "bad" : undefined },
    { label: "Mean probes before throw", value: metrics.meanProbesBeforeThrow?.toFixed(2) ?? "—" },
    { label: "Timeouts", value: formatNum(metrics.timeouts), tone: metrics.timeouts > 0 ? "bad" : undefined },
    { label: "Resource", value: formatNum(metrics.resource), tone: metrics.resource > 0 ? "bad" : undefined },
    { label: "Fallback", value: formatNum(metrics.fallback), tone: metrics.fallback > 0 ? "warn" : undefined },
    { label: "Crash", value: formatNum(metrics.crash), tone: metrics.crash > 0 ? "bad" : undefined },
    { label: "Hard findings", value: formatNum(metrics.hard), tone: metrics.hard > 0 ? "bad" : undefined },
    { label: "Java-types findings", value: formatNum(metrics.javaTypes), tone: metrics.javaTypes > 0 ? "warn" : undefined },
    { label: "Soft findings", value: formatNum(metrics.soft), tone: metrics.soft > 0 ? "warn" : undefined },
    { label: "Equal", value: formatNum(metrics.equal) },
    { label: "Discard", value: formatNum(metrics.discard) },
    { label: "Mean bytes", value: formatBytes(Math.round(metrics.meanBytes)) },
    { label: "Mean statements", value: metrics.meanStatements?.toFixed(1) ?? "—" },
    { label: "Duplicate rate", value: formatPct(metrics.duplicateRate) },
  ];

  const exceptionEntries = Object.entries(metrics.exceptions ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <Shell currentRun={runId}>
      <div className="mb-1">
        <Link href={`/generators?run=${encodeURIComponent(runId)}`} className="text-xs text-[var(--accent)] hover:underline">
          ← generators
        </Link>
      </div>
      <div className="flex items-start justify-between flex-wrap gap-2 mb-1">
        <h1 className="text-xl font-semibold font-mono">{entry.id}</h1>
        <div className="flex gap-1.5">
          <Badge tone="neutral">{entry.kind}</Badge>
          <Badge tone="neutral">{entry.family}</Badge>
          {entry.toggle && <Badge tone="info">toggle: {entry.toggle}</Badge>}
          {entry.sloppyOnly && <Badge tone="warn">sloppy-only</Badge>}
          {metrics.samples === 0 && <Badge tone="bad">never fired</Badge>}
        </div>
      </div>
      <p className="text-sm text-[var(--muted)] mb-4 max-w-3xl">{entry.description || "No description."}</p>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <div className="surface p-4 lg:col-span-1">
          <h2 className="text-sm font-medium text-[var(--muted)] mb-2">Inputs / outputs</h2>
          <dl className="text-sm flex flex-col gap-1.5">
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--muted)]">Weight</dt>
              <dd className="tabular-nums">{entry.weight}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--muted)]">Output type</dt>
              <dd className="font-mono">{entry.outputType}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--muted)]">Input types</dt>
              <dd className="font-mono text-right">
                {entry.inputTypes.length ? entry.inputTypes.join(", ") : "—"}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-[var(--muted)]">Source</dt>
              <dd className="font-mono text-xs text-right break-all">{entry.sourcePath}</dd>
            </div>
          </dl>
          {exceptionEntries.length > 0 && (
            <>
              <h2 className="text-sm font-medium text-[var(--muted)] mt-4 mb-2">Exceptions</h2>
              <ul className="text-sm flex flex-col gap-1">
                {exceptionEntries.map(([name, count]) => (
                  <li key={name} className="flex justify-between gap-2 font-mono text-xs">
                    <span>{name}</span>
                    <span className="text-[var(--muted)]">{count}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="surface p-4 lg:col-span-2">
          <h2 className="text-sm font-medium text-[var(--muted)] mb-2">Python source</h2>
          <CodeBlock code={entry.sourceSnippet} lang="python" />
        </div>
      </div>

      <div className="surface p-4 mb-6">
        <h2 className="text-sm font-medium text-[var(--muted)] mb-3">Metrics</h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-6 gap-y-2 text-sm">
          {metricRows.map((m) => (
            <div key={m.label} className="flex flex-col">
              <span className="text-[var(--muted)] text-xs">{m.label}</span>
              <span
                className="tabular-nums font-medium"
                style={{ color: m.tone ? `var(--${m.tone})` : undefined }}
              >
                {m.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-sm font-medium text-[var(--muted)] mb-3">
          Sample programs {samples.length > 0 && <span>({samples.length})</span>}
        </h2>
        {samples.length === 0 ? (
          <EmptyState title="No sample programs focused on this generator in this run" />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {samples.map((s) => (
              <SampleCard
                key={s.id}
                sample={s}
                runId={runId}
                highlightGen={entry.id}
                query={`run=${encodeURIComponent(runId)}&focus=${encodeURIComponent(entry.id)}`}
              />
            ))}
          </div>
        )}
      </div>
    </Shell>
  );
}
