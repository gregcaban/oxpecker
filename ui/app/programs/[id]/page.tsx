import Link from "next/link";
import Shell, { EmptyState } from "@/components/Shell";
import CodeBlock from "@/components/CodeBlock";
import GenLegend from "@/components/GenLegend";
import ModeComparison from "@/components/ModeComparison";
import TraceDiff from "@/components/TraceDiff";
import ReproducerButton from "@/components/ReproducerButton";
import ProgramKeyNav from "@/components/ProgramKeyNav";
import { VerdictBadge } from "@/components/Badge";
import { getCurrentRunId, firstParam, buildQueryString, type SearchParams } from "@/lib/run-context";
import { getSampleById, getFilteredIdsOrdered, type SampleFilters } from "@/lib/samples";
import { buildLineGenMap } from "@/lib/genTrace";
import { firstDiffIndex } from "@/lib/diff";
import { probeLineForIndex } from "@/lib/probeLines";
import { formatBytes, formatNum } from "@/lib/format";

export default async function ProgramDetailPage({
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

  const sample = await getSampleById(runId, id);
  if (!sample) {
    return (
      <Shell currentRun={runId}>
        <EmptyState title={`Sample "${id}" not found in this run`} />
      </Shell>
    );
  }

  const filters: SampleFilters = {
    focus: firstParam(sp, "focus") || undefined,
    template: firstParam(sp, "template") || undefined,
    verdict: firstParam(sp, "verdict") || undefined,
    statusInterpreted: firstParam(sp, "statusInterpreted") || undefined,
    statusCompiled: firstParam(sp, "statusCompiled") || undefined,
    compiledActuallyFalse: firstParam(sp, "compiledActuallyFalse") === "1",
    exception: firstParam(sp, "exception") || undefined,
    minBytes: firstParam(sp, "minBytes") ? Number(firstParam(sp, "minBytes")) : undefined,
    maxBytes: firstParam(sp, "maxBytes") ? Number(firstParam(sp, "maxBytes")) : undefined,
    q: firstParam(sp, "q") || undefined,
  };
  const orderedIds = await getFilteredIdsOrdered(runId, filters);
  const idx = orderedIds.indexOf(sample.id);
  const prevId = idx > 0 ? orderedIds[idx - 1] : null;
  const nextId = idx >= 0 && idx < orderedIds.length - 1 ? orderedIds[idx + 1] : null;
  const query = buildQueryString(sp, { run: runId });
  const prevHref = prevId ? `/programs/${encodeURIComponent(prevId)}?${query}` : null;
  const nextHref = nextId ? `/programs/${encodeURIComponent(nextId)}?${query}` : null;

  const lineGenMap = buildLineGenMap(sample.generatorTrace);
  const gensUsed = [...new Set(sample.generatorTrace.map((s) => s.gen))];

  const interp = sample.results?.interpreted;
  const comp = sample.results?.compiled;

  const rawDiff = sample.verdict?.firstTraceDiff;
  const firstTraceDiffIndex =
    typeof rawDiff === "number"
      ? rawDiff
      : firstDiffIndex(interp?.trace, comp?.trace) ?? firstDiffIndex(interp?.traceJavaTypes, comp?.traceJavaTypes);
  const markedLines = new Set<number>();
  if (firstTraceDiffIndex !== null) {
    const line = probeLineForIndex(sample.source, firstTraceDiffIndex);
    if (line !== null) markedLines.add(line);
  }

  return (
    <Shell currentRun={runId}>
      <ProgramKeyNav prevHref={prevHref} nextHref={nextHref} />

      <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
        <div className="flex items-center gap-3">
          <Link href={`/programs?${query}`} className="text-xs text-[var(--accent)] hover:underline">
            ← programs
          </Link>
          <h1 className="text-lg font-semibold font-mono">{sample.id}</h1>
          <VerdictBadge kind={sample.verdict?.kind} />
        </div>
        <div className="flex items-center gap-2">
          <Link
            href={prevHref ?? "#"}
            aria-disabled={!prevHref}
            className={`text-xs px-2 py-1 rounded-md border border-[var(--border)] ${
              prevHref ? "hover:bg-[var(--surface-2)]" : "opacity-30 pointer-events-none"
            }`}
          >
            ‹ k
          </Link>
          <span className="text-xs text-[var(--muted)]">
            {idx >= 0 ? `${idx + 1} / ${orderedIds.length}` : ""}
          </span>
          <Link
            href={nextHref ?? "#"}
            aria-disabled={!nextHref}
            className={`text-xs px-2 py-1 rounded-md border border-[var(--border)] ${
              nextHref ? "hover:bg-[var(--surface-2)]" : "opacity-30 pointer-events-none"
            }`}
          >
            j ›
          </Link>
          <ReproducerButton sourcePlain={sample.sourcePlain} sampleId={sample.id} />
        </div>
      </div>

      {sample.verdict && sample.verdict.reasons.length > 0 && (
        <div className="surface p-3 mb-4 text-sm flex flex-wrap gap-2 items-center">
          <span className="text-[var(--muted)] text-xs">verdict reasons:</span>
          {sample.verdict.reasons.map((r, i) => (
            <span key={i} className="badge badge-warn">
              {r}
            </span>
          ))}
          {sample.verdict.javaTypeDiff.map((r, i) => (
            <span key={`jt-${i}`} className="badge badge-warn font-mono">
              {r}
            </span>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-4 mb-4">
        <div className="surface p-4">
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-medium text-[var(--muted)]">Source</h2>
            <GenLegend gens={gensUsed} />
          </div>
          <CodeBlock
            code={sample.source}
            lineGenerator={(i) => lineGenMap.get(i)}
            markedLines={markedLines}
          />
        </div>

        <div className="surface p-4">
          <h2 className="text-sm font-medium text-[var(--muted)] mb-2">Metadata</h2>
          <dl className="text-sm flex flex-col gap-1.5">
            <Meta label="Kind" value={sample.kind} />
            <Meta label="Focus" value={sample.focus} mono />
            <Meta label="Template" value={sample.template} mono />
            <Meta label="Seed" value={String(sample.seed)} />
            <Meta label="Bytes" value={formatBytes(sample.stats?.bytes)} />
            <Meta label="Statements" value={formatNum(sample.stats?.statements)} />
            <Meta label="Top-level stmts" value={formatNum(sample.stats?.topLevelStatements)} />
            <Meta label="Vars" value={formatNum(sample.stats?.vars)} />
            <Meta label="Max depth" value={formatNum(sample.stats?.maxDepth)} />
            <Meta label="Probes" value={formatNum(sample.stats?.probes)} />
          </dl>
          <h3 className="text-xs font-medium text-[var(--muted)] mt-3 mb-1">Enabled generators</h3>
          <div className="flex flex-wrap gap-1">
            {sample.enabledGenerators.map((g) => (
              <Link
                key={g}
                href={`/generators/${encodeURIComponent(g)}?run=${encodeURIComponent(runId)}`}
                className="badge badge-neutral font-mono hover:border-[var(--accent)]"
              >
                {g}
              </Link>
            ))}
          </div>
          {sample.verdict?.signature && (
            <>
              <h3 className="text-xs font-medium text-[var(--muted)] mt-3 mb-1">Verdict signature</h3>
              <p className="font-mono text-xs break-all text-[var(--muted)]">{sample.verdict.signature}</p>
            </>
          )}
        </div>
      </div>

      <h2 className="text-sm font-medium text-[var(--muted)] mb-2">Mode comparison</h2>
      <div className="mb-4">
        <ModeComparison interpreted={interp} compiled={comp} />
      </div>

      <div className="flex flex-col gap-4">
        <TraceDiff title="Probe trace" left={interp?.trace} right={comp?.trace} />
        <TraceDiff title="Probe trace — Java types" left={interp?.traceJavaTypes} right={comp?.traceJavaTypes} />
        <TraceDiff title="Java trace" left={interp?.javaTrace} right={comp?.javaTrace} />
        <TraceDiff title="Java trace — Java types" left={interp?.javaTraceTypes} right={comp?.javaTraceTypes} />
        <TraceDiff title="Continuations" left={interp?.continuations} right={comp?.continuations} />
      </div>
    </Shell>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-[var(--muted)]">{label}</dt>
      <dd className={mono ? "font-mono text-right" : "text-right"}>{value || "—"}</dd>
    </div>
  );
}
