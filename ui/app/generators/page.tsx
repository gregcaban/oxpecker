import Link from "next/link";
import Shell, { EmptyState } from "@/components/Shell";
import FilterBar, { type FilterField } from "@/components/FilterBar";
import { Badge } from "@/components/Badge";
import { getCurrentRunId, firstParam, type SearchParams } from "@/lib/run-context";
import { getGeneratorRows, sortRows, SORT_KEYS, type SortKey } from "@/lib/generatorsView";
import { formatNum, formatPct } from "@/lib/format";

function sortHref(sp: SearchParams, runId: string, key: SortKey, currentSort: SortKey, currentDir: string) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(sp)) {
    if (v === undefined) continue;
    params.set(k, Array.isArray(v) ? v[0] : v);
  }
  params.set("run", runId);
  params.set("sort", key);
  params.set("dir", currentSort === key && currentDir === "desc" ? "asc" : "desc");
  return `/generators?${params.toString()}`;
}

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "id", label: "ID" },
  { key: "kind", label: "Kind" },
  { key: "family", label: "Family" },
  { key: "weight", label: "Weight" },
  { key: "samples", label: "Samples" },
  { key: "earlyThrowRate", label: "Early-throw" },
  { key: "syntaxBothRate", label: "Syntax rate" },
  { key: "problems", label: "Problems" },
];

export default async function GeneratorsPage({
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

  const rows = await getGeneratorRows(runId);

  const family = firstParam(sp, "family") ?? "";
  const kind = firstParam(sp, "kind") ?? "";
  const toggle = firstParam(sp, "toggle") ?? "";
  const hasProblems = firstParam(sp, "problems") === "1";
  const sort = (SORT_KEYS.includes(firstParam(sp, "sort") as SortKey) ? firstParam(sp, "sort") : "problems") as SortKey;
  const dir = (firstParam(sp, "dir") === "asc" ? "asc" : "desc") as "asc" | "desc";

  const families = [...new Set(rows.map((r) => r.entry.family))].sort();
  const toggles = [...new Set(rows.map((r) => r.entry.toggle).filter((t): t is string => !!t))].sort();

  let filtered = rows;
  if (family) filtered = filtered.filter((r) => r.entry.family === family);
  if (kind) filtered = filtered.filter((r) => r.entry.kind === kind);
  if (toggle) filtered = filtered.filter((r) => r.entry.toggle === toggle);
  if (hasProblems) filtered = filtered.filter((r) => r.problems > 0);

  const sorted = sortRows(filtered, sort, dir);

  const fields: FilterField[] = [
    {
      type: "select",
      name: "family",
      label: "Family",
      options: families.map((f) => ({ value: f, label: f })),
    },
    {
      type: "select",
      name: "kind",
      label: "Kind",
      options: [
        { value: "generator", label: "generator" },
        { value: "template", label: "template" },
      ],
    },
    {
      type: "select",
      name: "toggle",
      label: "Toggle",
      options: toggles.map((t) => ({ value: t, label: t })),
    },
    { type: "checkbox", name: "problems", label: "Has problems" },
  ];

  return (
    <Shell currentRun={runId}>
      <div className="flex items-baseline justify-between mb-4 flex-wrap gap-2">
        <h1 className="text-xl font-semibold">Generator catalogue</h1>
        <span className="text-sm text-[var(--muted)]">
          {formatNum(sorted.length)} of {formatNum(rows.length)}
        </span>
      </div>

      <div className="surface p-3 mb-4">
        <FilterBar fields={fields} />
      </div>

      <div className="surface overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key}>
                  <Link href={sortHref(sp, runId, c.key, sort, dir)} className="inline-flex items-center gap-1">
                    {c.label}
                    {sort === c.key && <span>{dir === "desc" ? "↓" : "↑"}</span>}
                  </Link>
                </th>
              ))}
              <th>Toggle</th>
              <th>Findings</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const m = r.metrics;
              return (
                <tr key={r.entry.id}>
                  <td>
                    <Link
                      href={`/generators/${encodeURIComponent(r.entry.id)}?run=${encodeURIComponent(runId)}`}
                      className="font-mono text-[var(--accent)] hover:underline"
                    >
                      {r.entry.id}
                    </Link>
                  </td>
                  <td>{r.entry.kind}</td>
                  <td>{r.entry.family}</td>
                  <td className="tabular-nums">{r.entry.weight}</td>
                  <td className="tabular-nums">{m ? formatNum(m.samples) : "—"}</td>
                  <td className="tabular-nums">
                    {m && m.samples > 0 ? formatPct(m.earlyThrowRate) : "—"}
                  </td>
                  <td className="tabular-nums">
                    {m && m.samples > 0 ? formatPct(m.syntaxBothRate) : "—"}
                  </td>
                  <td>
                    {r.problems === 0 ? (
                      <span className="text-[var(--muted)] text-xs">—</span>
                    ) : (
                      <Badge tone="bad">{r.problems}</Badge>
                    )}
                  </td>
                  <td>{r.entry.toggle ?? <span className="text-[var(--muted)]">—</span>}</td>
                  <td className="flex gap-1.5">
                    {m && m.hard > 0 && <Badge tone="bad">hard {m.hard}</Badge>}
                    {m && m.javaTypes > 0 && <Badge tone="warn">java-types {m.javaTypes}</Badge>}
                    {m && m.soft > 0 && <Badge tone="warn">soft {m.soft}</Badge>}
                    {m && !m.fired && m.samples === 0 && <Badge tone="neutral">never fired</Badge>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}
