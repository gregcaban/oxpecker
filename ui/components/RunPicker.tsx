"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";
import type { RunInfo } from "@/lib/types";

export default function RunPicker({ runs, current }: { runs: RunInfo[]; current: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  function onChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const value = e.target.value;
    try {
      document.cookie = `run=${encodeURIComponent(value)}; path=/; max-age=31536000`;
    } catch {
      // ignore (private browsing etc.)
    }
    const params = new URLSearchParams(searchParams.toString());
    params.set("run", value);
    params.delete("page");
    router.push(`${pathname}?${params.toString()}`);
  }

  if (runs.length === 0) {
    return <span className="text-sm text-[var(--muted)]">no runs found</span>;
  }

  return (
    <select
      value={current}
      onChange={onChange}
      className="surface-2 text-sm rounded-md px-2 py-1 border border-[var(--border)] bg-[var(--surface-2)] text-[var(--foreground)]"
      aria-label="Select run"
    >
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          {r.id}
        </option>
      ))}
    </select>
  );
}
