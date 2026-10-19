import Link from "next/link";
import Shell, { EmptyState } from "@/components/Shell";
import CodeBlock from "@/components/CodeBlock";
import { Badge } from "@/components/Badge";
import { getCurrentRunId, type SearchParams } from "@/lib/run-context";
import { getFindings } from "@/lib/findings";
import { formatNum } from "@/lib/format";
import type { Finding } from "@/lib/types";

const KIND_ORDER: Finding["kind"][] = ["hard", "java-types", "soft"];
const KIND_LABEL: Record<Finding["kind"], string> = {
  hard: "Hard divergences",
  "java-types": "Java type differences",
  soft: "Soft findings",
};
const KIND_TONE: Record<Finding["kind"], "bad" | "warn"> = {
  hard: "bad",
  "java-types": "warn",
  soft: "warn",
};

function triangulationHint(f: Finding): string | null {
  const reasons = f.verdict?.reasons ?? [];
  if (reasons.length === 0) return null;
  if (reasons.some((r) => /java types? differ/i.test(r))) {
    return "compiled and interpreted modes agree on value but disagree on the underlying Java type";
  }
  if (reasons.some((r) => /trace/i.test(r))) {
    return "compiled differs from interpreted somewhere in the probe trace";
  }
  if (reasons.some((r) => /thrown|throw/i.test(r))) {
    return "one mode throws where the other doesn't (or throws something different)";
  }
  return reasons[0];
}

export default async function FindingsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const runId = await getCurrentRunId(sp);

  if (!runId) {
    return (
      <Shell currentRun={null}>
        <EmptyState title="No runs found" />
      </Shell>
    );
  }

  const findings = await getFindings(runId);

  if (findings.length === 0) {
    return (
      <Shell currentRun={runId}>
        <h1 className="text-xl font-semibold mb-4">Findings</h1>
        <EmptyState
          title="No findings.json for this run"
          hint="This run may not have produced any grouped findings, or the file is missing."
        />
      </Shell>
    );
  }

  const grouped = KIND_ORDER.map((kind) => ({
    kind,
    items: findings.filter((f) => f.kind === kind).sort((a, b) => b.count - a.count),
  })).filter((g) => g.items.length > 0);

  return (
    <Shell currentRun={runId}>
      <div className="flex items-baseline justify-between mb-6">
        <h1 className="text-xl font-semibold">Findings</h1>
        <span className="text-sm text-[var(--muted)]">{formatNum(findings.length)} distinct signatures</span>
      </div>

      <div className="flex flex-col gap-8">
        {grouped.map((g) => (
          <section key={g.kind}>
            <h2 className="text-sm font-semibold mb-3 flex items-center gap-2">
              <Badge tone={KIND_TONE[g.kind]}>{g.kind}</Badge>
              {KIND_LABEL[g.kind]}
              <span className="text-[var(--muted)] font-normal">({g.items.length})</span>
            </h2>
            <div className="flex flex-col gap-4">
              {g.items.map((f) => (
                <FindingCard key={f.signature} finding={f} runId={runId} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </Shell>
  );
}

function FindingCard({ finding, runId }: { finding: Finding; runId: string }) {
  const hint = triangulationHint(finding);
  const focusEntries = Object.entries(finding.focus ?? {}).sort((a, b) => b[1] - a[1]);

  return (
    <div className="surface p-4">
      <div className="flex items-start justify-between flex-wrap gap-2 mb-2">
        <div>
          <p className="font-mono text-sm">{finding.signature}</p>
          {hint && <p className="text-xs text-[var(--muted)] mt-0.5">{hint}</p>}
        </div>
        <Badge tone="neutral">{formatNum(finding.count)} occurrences</Badge>
      </div>

      <div className="flex flex-wrap gap-1.5 mb-3">
        {focusEntries.map(([name, count]) => (
          <Link
            key={name}
            href={`/generators/${encodeURIComponent(name)}?run=${encodeURIComponent(runId)}`}
            className="badge badge-neutral font-mono hover:border-[var(--accent)]"
            title={`${count} sample(s)`}
          >
            {name} ({count})
          </Link>
        ))}
      </div>

      {finding.verdict?.reasons?.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3 text-xs">
          {finding.verdict.reasons.map((r, i) => (
            <span key={i} className="badge badge-warn">
              {r}
            </span>
          ))}
          {finding.verdict.javaTypeDiff?.map((r, i) => (
            <span key={`jt-${i}`} className="badge badge-warn font-mono">
              {r}
            </span>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-[var(--muted)]">example</span>
        <Link
          href={`/programs/${encodeURIComponent(finding.example)}?run=${encodeURIComponent(runId)}`}
          className="text-xs text-[var(--accent)] hover:underline font-mono"
        >
          {finding.example} →
        </Link>
      </div>
      <CodeBlock code={finding.source} showLineNumbers={false} />
    </div>
  );
}
