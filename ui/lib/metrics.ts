import fs from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { runDir } from "./paths";
import type { Metrics, PerFocusMetrics } from "./types";

const EMPTY_PER_FOCUS: PerFocusMetrics = {
  kind: "generator",
  samples: 0,
  fired: 0,
  usageTotal: 0,
  syntaxBothRate: 0,
  earlyThrowRate: 0,
  throwRate: 0,
  meanProbesBeforeThrow: null,
  timeouts: 0,
  resource: 0,
  fallback: 0,
  crash: 0,
  hard: 0,
  javaTypes: 0,
  soft: 0,
  equal: 0,
  discard: 0,
  meanBytes: 0,
  meanStatements: 0,
  duplicateRate: 0,
  exceptions: {},
};

export const getMetrics = cache(async (runId: string): Promise<Metrics | null> => {
  try {
    const raw = await fs.readFile(path.join(runDir(runId), "metrics.json"), "utf8");
    const parsed = JSON.parse(raw) as Partial<Metrics>;
    return {
      runId: parsed.runId ?? runId,
      languageVersion: parsed.languageVersion ?? 0,
      samples: parsed.samples ?? 0,
      generatorUsage: parsed.generatorUsage ?? {},
      perFocus: parsed.perFocus ?? {},
    };
  } catch {
    return null;
  }
});

export async function getPerFocus(runId: string, name: string): Promise<PerFocusMetrics> {
  const metrics = await getMetrics(runId);
  return metrics?.perFocus?.[name] ?? EMPTY_PER_FOCUS;
}
