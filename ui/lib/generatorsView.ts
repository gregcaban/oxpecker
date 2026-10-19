import { getCatalogue } from "./catalogue";
import { getMetrics } from "./metrics";
import type { CatalogueEntry, PerFocusMetrics } from "./types";

export interface GeneratorRow {
  entry: CatalogueEntry;
  metrics: PerFocusMetrics | null;
  problems: number;
}

export async function getGeneratorRows(runId: string): Promise<GeneratorRow[]> {
  const [catalogue, metrics] = await Promise.all([getCatalogue(runId), getMetrics(runId)]);
  return catalogue.map((entry) => {
    const m = metrics?.perFocus?.[entry.id] ?? null;
    const problems = m ? m.hard * 3 + m.javaTypes * 2 + m.soft + m.timeouts * 2 + m.resource + m.crash * 3 : 0;
    return { entry, metrics: m, problems };
  });
}

export const SORT_KEYS = [
  "id",
  "kind",
  "family",
  "weight",
  "samples",
  "earlyThrowRate",
  "syntaxBothRate",
  "problems",
] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export function sortRows(rows: GeneratorRow[], sort: SortKey, dir: "asc" | "desc"): GeneratorRow[] {
  const mul = dir === "asc" ? 1 : -1;
  const sorted = [...rows].sort((a, b) => {
    switch (sort) {
      case "id":
        return a.entry.id.localeCompare(b.entry.id) * mul;
      case "kind":
        return a.entry.kind.localeCompare(b.entry.kind) * mul;
      case "family":
        return a.entry.family.localeCompare(b.entry.family) * mul;
      case "weight":
        return (a.entry.weight - b.entry.weight) * mul;
      case "samples":
        return ((a.metrics?.samples ?? 0) - (b.metrics?.samples ?? 0)) * mul;
      case "earlyThrowRate":
        return ((a.metrics?.earlyThrowRate ?? -1) - (b.metrics?.earlyThrowRate ?? -1)) * mul;
      case "syntaxBothRate":
        return ((a.metrics?.syntaxBothRate ?? -1) - (b.metrics?.syntaxBothRate ?? -1)) * mul;
      case "problems":
        return (a.problems - b.problems) * mul;
      default:
        return 0;
    }
  });
  return sorted;
}
