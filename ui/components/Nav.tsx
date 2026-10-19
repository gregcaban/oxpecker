import Link from "next/link";
import { listRuns } from "@/lib/paths";
import RunPicker from "./RunPicker";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/generators", label: "Generators" },
  { href: "/programs", label: "Programs" },
  { href: "/findings", label: "Findings" },
];

export default function Nav({ currentRun }: { currentRun: string | null }) {
  const runs = listRuns();
  return (
    <header className="sticky top-0 z-20 border-b border-[var(--border)] bg-[var(--surface)]/90 backdrop-blur">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 flex items-center gap-6 h-14">
        <Link href="/" className="font-semibold tracking-tight text-[15px] shrink-0">
          oxpecker <span className="text-[var(--muted)] font-normal">inspector</span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          {LINKS.map((l) => (
            <Link
              key={l.href}
              href={currentRun ? `${l.href}?run=${encodeURIComponent(currentRun)}` : l.href}
              className="px-3 py-1.5 rounded-md text-[var(--muted)] hover:text-[var(--foreground)] hover:bg-[var(--surface-2)] transition-colors"
            >
              {l.label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {currentRun && <RunPicker runs={runs} current={currentRun} />}
        </div>
      </div>
    </header>
  );
}
