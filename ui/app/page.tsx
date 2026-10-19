import Shell, { EmptyState } from "@/components/Shell";
import StatTile from "@/components/StatTile";
import { VerdictBadge } from "@/components/Badge";
import BytesHistogram from "@/components/charts/BytesHistogram";
import ExceptionBreakdown from "@/components/charts/ExceptionBreakdown";
import GeneratorUsageChart, { type UsageDatum } from "@/components/charts/GeneratorUsageChart";
import { getCurrentRunId, type SearchParams } from "@/lib/run-context";
import { listRuns } from "@/lib/paths";
import { getCatalogue } from "@/lib/catalogue";
import { getMetrics } from "@/lib/metrics";
import { computeOverviewAggregates } from "@/lib/samples";
import { formatNum, formatPct } from "@/lib/format";
import type { VerdictKind } from "@/lib/types";

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const runs = listRuns();
  const runId = await getCurrentRunId(sp);

  if (!runId) {
    return (
      <Shell currentRun={null}>
        <EmptyState
          title="No runs found"
          hint="Generate a run with `oxpecker export` and point it at ui/data/<run-id>/."
        />
      </Shell>
    );
  }

  const [catalogue, metrics, aggregates] = await Promise.all([
    getCatalogue(runId),
    getMetrics(runId),
    computeOverviewAggregates(runId),
  ]);

  const total = aggregates.total || metrics?.samples || 0;

  const verdictOrder: VerdictKind[] = ["equal", "hard", "java-types", "soft", "skip", "discard"];
  const verdictEntries = verdictOrder
    .map((k) => ({ kind: k, count: aggregates.verdictCounts[k] ?? 0 }))
    .filter((v) => v.count > 0 || v.kind === "equal" || v.kind === "hard");

  const generatorEntries = catalogue.filter((c) => c.kind === "generator");
  const usage: UsageDatum[] = generatorEntries
    .map((g) => {
      const count = metrics?.generatorUsage?.[g.id] ?? 0;
      return { name: g.id, count, rare: count === 0 || count < 3 };
    })
    .sort((a, b) => a.count - b.count);
  const neverFiredCount = usage.filter((u) => u.count === 0).length;

  return (
    <Shell currentRun={runId}>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-xl font-semibold">Run overview</h1>
        <span className="text-sm text-[var(--muted)] font-mono">{runId}</span>
      </div>

      {runs.length === 0 || total === 0 ? (
        <EmptyState title="This run has no samples yet" hint={`Looked in ui/data/${runId}/samples.jsonl`} />
      ) : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
            <StatTile label="Programs" value={formatNum(total)} />
            <StatTile
              label="Early-throw rate"
              value={formatPct(aggregates.earlyThrowCount / total)}
              sub={`${formatNum(aggregates.earlyThrowCount)} programs`}
              tone={aggregates.earlyThrowCount / total > 0.1 ? "warn" : "neutral"}
            />
            <StatTile
              label="Syntax in both"
              value={formatPct(aggregates.syntaxBothCount / total)}
              sub={`${formatNum(aggregates.syntaxBothCount)} programs`}
              tone={aggregates.syntaxBothCount > 0 ? "bad" : "neutral"}
            />
            <StatTile
              label="Fallback rate"
              value={formatPct(aggregates.fallbackCount / total)}
              sub={`${formatNum(aggregates.fallbackCount)} programs`}
              tone={aggregates.fallbackCount > 0 ? "warn" : "neutral"}
            />
            <StatTile
              label="Timeouts"
              value={formatNum(aggregates.timeoutCount)}
              tone={aggregates.timeoutCount > 0 ? "bad" : "neutral"}
            />
            <StatTile
              label="Crashes"
              value={formatNum(aggregates.crashCount)}
              tone={aggregates.crashCount > 0 ? "bad" : "neutral"}
            />
          </div>

          <div className="surface p-4 mb-6">
            <h2 className="text-sm font-medium text-[var(--muted)] mb-3">Verdict breakdown</h2>
            <div className="flex flex-wrap gap-4">
              {verdictEntries.map((v) => (
                <div key={v.kind} className="flex items-center gap-2">
                  <VerdictBadge kind={v.kind} />
                  <span className="text-sm tabular-nums">{formatNum(v.count)}</span>
                  <span className="text-xs text-[var(--muted)]">({formatPct(v.count / total)})</span>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
            <div className="surface p-4">
              <h2 className="text-sm font-medium text-[var(--muted)] mb-3">Program size (bytes)</h2>
              <BytesHistogram data={aggregates.bytesHistogram} />
            </div>
            <div className="surface p-4">
              <h2 className="text-sm font-medium text-[var(--muted)] mb-3">
                Exception breakdown <span className="text-[var(--muted)] font-normal">(top 15)</span>
              </h2>
              <ExceptionBreakdown data={aggregates.exceptionCounts} />
            </div>
          </div>

          <div className="surface p-4">
            <div className="flex items-baseline justify-between mb-3">
              <h2 className="text-sm font-medium text-[var(--muted)]">
                Generator usage{" "}
                <span className="font-normal">
                  ({generatorEntries.length} generators, {neverFiredCount} never fired)
                </span>
              </h2>
              <a href={`/generators?run=${encodeURIComponent(runId)}&problems=1`} className="text-xs text-[var(--accent)] hover:underline">
                view problem generators →
              </a>
            </div>
            <GeneratorUsageChart data={usage} />
          </div>
        </>
      )}
    </Shell>
  );
}
