import type { GeneratorTraceSpan } from "./types";

/** Map 0-based line index -> generator id, from (possibly overlapping) inclusive spans. */
export function buildLineGenMap(spans: GeneratorTraceSpan[] | undefined): Map<number, string> {
  const map = new Map<number, string>();
  for (const s of spans ?? []) {
    if (!s || !Array.isArray(s.lines)) continue;
    const [start, end] = s.lines;
    for (let i = start; i <= end; i++) map.set(i, s.gen);
  }
  return map;
}

export function spansForGenerator(
  spans: GeneratorTraceSpan[] | undefined,
  gen: string,
): GeneratorTraceSpan[] {
  return (spans ?? []).filter((s) => s.gen === gen);
}
