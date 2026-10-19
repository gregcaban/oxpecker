export function formatBytes(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

export function formatPct(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}%`;
}

export function formatMs(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  if (n < 1000) return `${n} ms`;
  return `${(n / 1000).toFixed(2)} s`;
}

export function formatNum(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US");
}

/** Deterministic hash -> hue in [0, 360). Used to color-code generators consistently. */
export function hashHue(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}
