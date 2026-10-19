import fs from "node:fs";
import path from "node:path";
import { cache } from "react";
import type { RunInfo } from "./types";

export const DATA_DIR = path.join(process.cwd(), "data");

/** List run directories under ui/data, newest first (names are sortable timestamps). */
export const listRuns = cache((): RunInfo[] => {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(DATA_DIR, { withFileTypes: true });
  } catch {
    return [];
  }
  const runs = entries
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort((a, b) => (a < b ? 1 : a > b ? -1 : 0)) // lexicographic desc == newest first
    .map((id): RunInfo => {
      const dir = path.join(DATA_DIR, id);
      return {
        id,
        hasCatalogue: fs.existsSync(path.join(dir, "catalogue.json")),
        hasSamples: fs.existsSync(path.join(dir, "samples.jsonl")),
        hasMetrics: fs.existsSync(path.join(dir, "metrics.json")),
        hasFindings: fs.existsSync(path.join(dir, "findings.json")),
      };
    });
  return runs;
});

export function runDir(runId: string): string {
  return path.join(DATA_DIR, runId);
}

export function resolveRunId(requested: string | undefined | null): string | null {
  const runs = listRuns();
  if (runs.length === 0) return null;
  if (requested && runs.some((r) => r.id === requested)) return requested;
  return runs[0].id;
}
