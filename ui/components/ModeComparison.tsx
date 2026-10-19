import { StatusBadge } from "./Badge";
import { formatMs } from "@/lib/format";
import type { Outcome } from "@/lib/types";

function Cell({ children }: { children: React.ReactNode }) {
  return <div className="text-sm break-all whitespace-pre-wrap">{children}</div>;
}

function Row({
  label,
  interpreted,
  compiled,
  render,
  compareValue,
}: {
  label: string;
  interpreted: Outcome | undefined;
  compiled: Outcome | undefined;
  render: (o: Outcome | undefined) => React.ReactNode;
  compareValue?: (o: Outcome | undefined) => string;
}) {
  const a = render(interpreted);
  const b = render(compiled);
  const differs = compareValue ? compareValue(interpreted) !== compareValue(compiled) : String(a) !== String(b);
  return (
    <div
      className={`grid grid-cols-[8rem_1fr_1fr] gap-3 px-4 py-2 border-b border-[var(--border)] last:border-0 ${
        differs ? "bg-[color-mix(in_srgb,var(--warn)_8%,transparent)]" : ""
      }`}
    >
      <span className="text-xs text-[var(--muted)] pt-0.5">{label}</span>
      <Cell>{a}</Cell>
      <Cell>{b}</Cell>
    </div>
  );
}

export default function ModeComparison({
  interpreted,
  compiled,
}: {
  interpreted?: Outcome;
  compiled?: Outcome;
}) {
  if (!interpreted && !compiled) {
    return <p className="text-sm text-[var(--muted)]">No results recorded for either mode.</p>;
  }

  return (
    <div className="surface overflow-hidden">
      <div className="grid grid-cols-[8rem_1fr_1fr] gap-3 px-4 py-2 border-b border-[var(--border)] bg-[var(--surface-2)] text-xs font-medium text-[var(--muted)]">
        <span />
        <span>interpreted</span>
        <span>compiled</span>
      </div>
      <Row
        label="Status"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => <StatusBadge status={o?.status} />}
        compareValue={(o) => o?.status ?? ""}
      />
      <Row
        label="compiledActually"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => (o === undefined ? "—" : String(o.compiledActually))}
      />
      <Row label="Value" interpreted={interpreted} compiled={compiled} render={(o) => o?.value ?? "—"} />
      <Row
        label="Value Java type"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => o?.valueJavaTypes || "—"}
      />
      <Row
        label="Thrown"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) =>
          o?.thrown ? (
            <span>
              <span className="font-mono text-[var(--bad)]">{o.thrown.name}</span>: {o.thrown.message}
              {o.thrown.valueJavaTypes && (
                <span className="text-[var(--muted)]"> ({o.thrown.valueJavaTypes})</span>
              )}
            </span>
          ) : (
            "—"
          )
        }
        compareValue={(o) => (o?.thrown ? `${o.thrown.name}:${o.thrown.message}` : "")}
      />
      <Row
        label="Engine exception"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) =>
          o?.engineException ? (
            <span>
              <span className="font-mono text-[var(--bad)]">{o.engineException.clazz}</span>:{" "}
              {o.engineException.message}
            </span>
          ) : (
            "—"
          )
        }
        compareValue={(o) => (o?.engineException ? `${o.engineException.clazz}:${o.engineException.message}` : "")}
      />
      <Row
        label="Output"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => (o?.output?.length ? o.output.join("\n") : "—")}
      />
      <Row
        label="Soft findings"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => (o?.soft?.length ? o.soft.join(", ") : "—")}
      />
      <Row label="Reason" interpreted={interpreted} compiled={compiled} render={(o) => o?.reason ?? "—"} />
      <Row
        label="Timing"
        interpreted={interpreted}
        compiled={compiled}
        render={(o) => (o ? formatMs(o.ms) : "—")}
      />
    </div>
  );
}
