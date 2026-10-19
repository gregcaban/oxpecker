export function firstDiffIndex(a: string[] | undefined, b: string[] | undefined): number | null {
  const aa = a ?? [];
  const bb = b ?? [];
  const len = Math.max(aa.length, bb.length);
  for (let i = 0; i < len; i++) {
    if (aa[i] !== bb[i]) return i;
  }
  return null;
}
