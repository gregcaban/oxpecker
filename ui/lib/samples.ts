import fs from "node:fs";
import readline from "node:readline";
import path from "node:path";
import { runDir } from "./paths";
import type { Sample } from "./types";

/** Defensive coercion: samples.jsonl lines are trusted-ish JSON, but fields may be missing. */
function coerceSample(raw: unknown): Sample | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.id !== "string") return null;
  return {
    id: r.id,
    runId: typeof r.runId === "string" ? r.runId : "",
    kind: r.kind === "template" ? "template" : "generator",
    focus: typeof r.focus === "string" ? r.focus : "",
    source: typeof r.source === "string" ? r.source : "",
    sourcePlain: typeof r.sourcePlain === "string" ? r.sourcePlain : "",
    seed: typeof r.seed === "number" ? r.seed : 0,
    template: typeof r.template === "string" ? r.template : "",
    enabledGenerators: Array.isArray(r.enabledGenerators) ? (r.enabledGenerators as string[]) : [],
    generatorTrace: Array.isArray(r.generatorTrace)
      ? (r.generatorTrace as Sample["generatorTrace"])
      : [],
    stats: (r.stats as Sample["stats"]) ?? undefined,
    results: (r.results as Sample["results"]) ?? undefined,
    verdict: (r.verdict as Sample["verdict"]) ?? undefined,
  };
}

async function* iterSamples(runId: string): AsyncGenerator<Sample> {
  const file = path.join(runDir(runId), "samples.jsonl");
  let stream: fs.ReadStream;
  try {
    stream = fs.createReadStream(file, { encoding: "utf8" });
  } catch {
    return;
  }
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
  try {
    for await (const line of rl) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        const parsed = JSON.parse(trimmed);
        const sample = coerceSample(parsed);
        if (sample) yield sample;
      } catch {
        // skip malformed line
      }
    }
  } finally {
    rl.close();
    stream.close();
  }
}

export interface SampleFilters {
  focus?: string;
  template?: string;
  verdict?: string;
  statusInterpreted?: string;
  statusCompiled?: string;
  compiledActuallyFalse?: boolean;
  exception?: string;
  minBytes?: number;
  maxBytes?: number;
  q?: string;
}

function matches(s: Sample, f: SampleFilters): boolean {
  if (f.focus && s.focus !== f.focus) return false;
  if (f.template && s.template !== f.template) return false;
  if (f.verdict && s.verdict?.kind !== f.verdict) return false;
  if (f.statusInterpreted && s.results?.interpreted?.status !== f.statusInterpreted) return false;
  if (f.statusCompiled && s.results?.compiled?.status !== f.statusCompiled) return false;
  if (f.compiledActuallyFalse) {
    const c = s.results?.compiled;
    if (!c || c.compiledActually !== false) return false;
  }
  if (f.exception) {
    const names = [s.results?.interpreted?.thrown?.name, s.results?.compiled?.thrown?.name];
    if (!names.includes(f.exception)) return false;
  }
  const bytes = s.stats?.bytes;
  if (f.minBytes !== undefined && (bytes === undefined || bytes < f.minBytes)) return false;
  if (f.maxBytes !== undefined && (bytes === undefined || bytes > f.maxBytes)) return false;
  if (f.q) {
    const needle = f.q.toLowerCase();
    if (!s.source.toLowerCase().includes(needle)) return false;
  }
  return true;
}

export interface SamplesPage {
  items: Sample[];
  total: number;
  offset: number;
  limit: number;
}

export async function querySamples(
  runId: string,
  filters: SampleFilters,
  opts: { offset: number; limit: number },
): Promise<SamplesPage> {
  const items: Sample[] = [];
  let total = 0;
  for await (const s of iterSamples(runId)) {
    if (!matches(s, filters)) continue;
    if (total >= opts.offset && items.length < opts.limit) {
      items.push(s);
    }
    total++;
  }
  return { items, total, offset: opts.offset, limit: opts.limit };
}

export async function getFilteredIdsOrdered(
  runId: string,
  filters: SampleFilters,
): Promise<string[]> {
  const ids: string[] = [];
  for await (const s of iterSamples(runId)) {
    if (matches(s, filters)) ids.push(s.id);
  }
  return ids;
}

export async function getSampleById(runId: string, id: string): Promise<Sample | null> {
  for await (const s of iterSamples(runId)) {
    if (s.id === id) return s;
  }
  return null;
}

export async function getSamplesByFocus(
  runId: string,
  focus: string,
  limit: number,
): Promise<Sample[]> {
  const out: Sample[] = [];
  for await (const s of iterSamples(runId)) {
    if (s.focus === focus) {
      out.push(s);
      if (out.length >= limit) break;
    }
  }
  return out;
}

export interface SampleFacets {
  templates: string[];
  exceptions: string[];
}

export async function getSampleFacets(runId: string): Promise<SampleFacets> {
  const templates = new Set<string>();
  const exceptions = new Set<string>();
  for await (const s of iterSamples(runId)) {
    if (s.template) templates.add(s.template);
    const n1 = s.results?.interpreted?.thrown?.name;
    const n2 = s.results?.compiled?.thrown?.name;
    if (n1) exceptions.add(n1);
    if (n2) exceptions.add(n2);
  }
  return { templates: [...templates].sort(), exceptions: [...exceptions].sort() };
}

export async function countSamples(runId: string): Promise<number> {
  let n = 0;
  for await (const sample of iterSamples(runId)) {
    void sample;
    n++;
  }
  return n;
}

/** For the overview page: byte histogram + exception breakdown computed in one streaming pass. */
export interface OverviewAggregates {
  total: number;
  verdictCounts: Record<string, number>;
  earlyThrowCount: number;
  syntaxBothCount: number;
  fallbackCount: number;
  timeoutCount: number;
  crashCount: number;
  bytesHistogram: { bucket: string; count: number }[];
  exceptionCounts: { name: string; count: number }[];
}

export async function computeOverviewAggregates(runId: string): Promise<OverviewAggregates> {
  let total = 0;
  const verdictCounts: Record<string, number> = {};
  let earlyThrowCount = 0;
  let syntaxBothCount = 0;
  let fallbackCount = 0;
  let timeoutCount = 0;
  let crashCount = 0;
  const exceptionCounts = new Map<string, number>();
  // bytes buckets: 0-50,50-100,...,450-500,500+
  const bucketSize = 50;
  const bucketMax = 500;
  const histogram = new Map<string, number>();

  for await (const s of iterSamples(runId)) {
    total++;
    const vk = s.verdict?.kind ?? "unknown";
    verdictCounts[vk] = (verdictCounts[vk] ?? 0) + 1;

    const interp = s.results?.interpreted;
    const comp = s.results?.compiled;
    const bothSyntax = interp?.status === "syntax" && comp?.status === "syntax";
    if (bothSyntax) syntaxBothCount++;
    const anyThrowEarly = (o?: typeof interp) => o?.status === "throw" && (o.trace?.length ?? 0) <= 1;
    if (anyThrowEarly(interp) || anyThrowEarly(comp)) earlyThrowCount++;
    if (interp?.status === "fallback" || comp?.status === "fallback") fallbackCount++;
    if (interp?.status === "timeout" || comp?.status === "timeout") timeoutCount++;
    if (interp?.status === "crash" || comp?.status === "crash") crashCount++;

    for (const o of [interp, comp]) {
      const name = o?.thrown?.name;
      if (name) exceptionCounts.set(name, (exceptionCounts.get(name) ?? 0) + 1);
    }

    const bytes = s.stats?.bytes;
    if (typeof bytes === "number") {
      const bucketStart = Math.min(Math.floor(bytes / bucketSize) * bucketSize, bucketMax);
      const label = bucketStart >= bucketMax ? `${bucketMax}+` : `${bucketStart}-${bucketStart + bucketSize}`;
      histogram.set(label, (histogram.get(label) ?? 0) + 1);
    }
  }

  const bytesHistogram: { bucket: string; count: number }[] = [];
  for (let b = 0; b < bucketMax; b += bucketSize) {
    const label = `${b}-${b + bucketSize}`;
    bytesHistogram.push({ bucket: label, count: histogram.get(label) ?? 0 });
  }
  bytesHistogram.push({ bucket: `${bucketMax}+`, count: histogram.get(`${bucketMax}+`) ?? 0 });

  const exceptionCountsArr = [...exceptionCounts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 15);

  return {
    total,
    verdictCounts,
    earlyThrowCount,
    syntaxBothCount,
    fallbackCount,
    timeoutCount,
    crashCount,
    bytesHistogram,
    exceptionCounts: exceptionCountsArr,
  };
}
