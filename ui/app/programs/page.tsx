import Shell, { EmptyState } from "@/components/Shell";
import FilterBar, { type FilterField } from "@/components/FilterBar";
import SampleCard from "@/components/SampleCard";
import Pagination from "@/components/Pagination";
import { getCurrentRunId, firstParam, buildQueryString, type SearchParams } from "@/lib/run-context";
import { getCatalogue } from "@/lib/catalogue";
import { querySamples, getSampleFacets, type SampleFilters } from "@/lib/samples";

const PAGE_SIZE = 50;
const STATUSES = ["ok", "throw", "syntax", "timeout", "resource", "fallback", "crash", "unsupported"];
const VERDICTS = ["equal", "hard", "java-types", "soft", "skip", "discard"];

export default async function ProgramsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const runId = await getCurrentRunId(sp);

  if (!runId) {
    return (
      <Shell currentRun={null}>
        <EmptyState title="No runs found" />
      </Shell>
    );
  }

  const [catalogue, facets] = await Promise.all([getCatalogue(runId), getSampleFacets(runId)]);

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

  const page = Math.max(1, Number(firstParam(sp, "page") ?? "1") || 1);
  const offset = (page - 1) * PAGE_SIZE;

  const result = await querySamples(runId, filters, { offset, limit: PAGE_SIZE });
  const listQuery = buildQueryString(sp, { run: runId });

  const fields: FilterField[] = [
    {
      type: "select",
      name: "focus",
      label: "Focus",
      options: catalogue.map((c) => ({ value: c.id, label: `${c.id} (${c.kind})` })),
    },
    {
      type: "select",
      name: "template",
      label: "Template",
      options: facets.templates.map((t) => ({ value: t, label: t })),
    },
    {
      type: "select",
      name: "verdict",
      label: "Verdict",
      options: VERDICTS.map((v) => ({ value: v, label: v })),
    },
    {
      type: "select",
      name: "statusInterpreted",
      label: "Interpreted status",
      options: STATUSES.map((s) => ({ value: s, label: s })),
    },
    {
      type: "select",
      name: "statusCompiled",
      label: "Compiled status",
      options: STATUSES.map((s) => ({ value: s, label: s })),
    },
    { type: "checkbox", name: "compiledActuallyFalse", label: "compiledActually=false" },
    {
      type: "select",
      name: "exception",
      label: "Exception",
      options: facets.exceptions.map((e) => ({ value: e, label: e })),
    },
    { type: "number", name: "minBytes", label: "Min bytes" },
    { type: "number", name: "maxBytes", label: "Max bytes" },
    { type: "text", name: "q", label: "Search source", placeholder: "search source…" },
  ];

  return (
    <Shell currentRun={runId}>
      <div className="flex items-baseline justify-between mb-4 flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Programs</h1>
        <span className="text-sm text-[var(--muted)]">{result.total} matching</span>
      </div>

      <div className="surface p-3 mb-4">
        <FilterBar fields={fields} />
      </div>

      {result.items.length === 0 ? (
        <EmptyState title="No programs match these filters" />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {result.items.map((s) => (
            <SampleCard key={s.id} sample={s} runId={runId} maxLines={8} query={listQuery} />
          ))}
        </div>
      )}

      <Pagination basePath="/programs" searchParams={sp} page={page} pageSize={PAGE_SIZE} total={result.total} />
    </Shell>
  );
}
