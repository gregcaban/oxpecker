import fs from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { runDir } from "./paths";
import type { CatalogueEntry } from "./types";

function coerceEntry(raw: unknown): CatalogueEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string") return null;
  return {
    id: r.id,
    kind: r.kind === "template" ? "template" : "generator",
    family: typeof r.family === "string" ? r.family : "unknown",
    weight: typeof r.weight === "number" ? r.weight : 0,
    toggle: typeof r.toggle === "string" ? r.toggle : null,
    sloppyOnly: Boolean(r.sloppyOnly),
    inputTypes: Array.isArray(r.inputTypes) ? (r.inputTypes as string[]) : [],
    outputType: typeof r.outputType === "string" ? r.outputType : "unknown",
    description: typeof r.description === "string" ? r.description : "",
    sourcePath: typeof r.sourcePath === "string" ? r.sourcePath : "",
    sourceSnippet: typeof r.sourceSnippet === "string" ? r.sourceSnippet : "",
  };
}

export const getCatalogue = cache(async (runId: string): Promise<CatalogueEntry[]> => {
  try {
    const raw = await fs.readFile(path.join(runDir(runId), "catalogue.json"), "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.map(coerceEntry).filter((e): e is CatalogueEntry => e !== null);
  } catch {
    return [];
  }
});

export const getCatalogueEntry = cache(
  async (runId: string, id: string): Promise<CatalogueEntry | null> => {
    const all = await getCatalogue(runId);
    return all.find((e) => e.id === id) ?? null;
  },
);
