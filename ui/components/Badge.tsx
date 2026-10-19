import type { OutcomeStatus, VerdictKind } from "@/lib/types";

type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

const STATUS_TONE: Record<OutcomeStatus, Tone> = {
  ok: "ok",
  throw: "warn",
  syntax: "bad",
  timeout: "bad",
  resource: "bad",
  fallback: "info",
  crash: "bad",
  unsupported: "neutral",
};

const VERDICT_TONE: Record<VerdictKind, Tone> = {
  equal: "ok",
  hard: "bad",
  "java-types": "warn",
  soft: "warn",
  skip: "neutral",
  discard: "neutral",
};

export function StatusBadge({ status }: { status?: OutcomeStatus | string }) {
  if (!status) return <span className="badge badge-neutral">no data</span>;
  const tone = STATUS_TONE[status as OutcomeStatus] ?? "neutral";
  return <span className={`badge badge-${tone}`}>{status}</span>;
}

export function VerdictBadge({ kind }: { kind?: VerdictKind | string }) {
  if (!kind) return <span className="badge badge-neutral">—</span>;
  const tone = VERDICT_TONE[kind as VerdictKind] ?? "neutral";
  return <span className={`badge badge-${tone}`}>{kind}</span>;
}

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: React.ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}
