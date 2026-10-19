/** Maps a trace/probe index (order of __probe(...) calls) back to a 0-based source line. */
export function probeLineForIndex(source: string, index: number): number | null {
  if (index < 0) return null;
  const lines = source.split("\n");
  let count = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes("__probe(")) {
      count++;
      if (count === index) return i;
    }
  }
  return null;
}
