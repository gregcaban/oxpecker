"use client";

import { useRef } from "react";
import { useRouter, usePathname, useSearchParams } from "next/navigation";

export interface SelectField {
  type: "select";
  name: string;
  label: string;
  options: { value: string; label: string }[];
}
export interface CheckboxField {
  type: "checkbox";
  name: string;
  label: string;
}
export interface TextField {
  type: "text";
  name: string;
  label: string;
  placeholder?: string;
}
export type FilterField = SelectField | CheckboxField | TextField;

export default function FilterBar({ fields }: { fields: FilterField[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function navigate(params: URLSearchParams) {
    params.delete("page");
    router.replace(`${pathname}?${params.toString()}`);
  }

  function update(name: string, value: string | null) {
    const params = new URLSearchParams(searchParams.toString());
    if (value === null || value === "") params.delete(name);
    else params.set(name, value);
    navigate(params);
  }

  function updateDebounced(name: string, value: string) {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => update(name, value), 300);
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      {fields.map((f) => {
        if (f.type === "select") {
          return (
            <label key={f.name} className="flex items-center gap-1.5 text-xs text-[var(--muted)]">
              {f.label}
              <select
                defaultValue={searchParams.get(f.name) ?? ""}
                onChange={(e) => update(f.name, e.target.value)}
                className="rounded-md px-2 py-1 text-sm border border-[var(--border)] bg-[var(--surface-2)] text-[var(--foreground)]"
              >
                <option value="">all</option>
                {f.options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
          );
        }
        if (f.type === "checkbox") {
          return (
            <label key={f.name} className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                defaultChecked={searchParams.get(f.name) === "1"}
                onChange={(e) => update(f.name, e.target.checked ? "1" : null)}
              />
              {f.label}
            </label>
          );
        }
        return (
          <input
            key={f.name}
            type="text"
            defaultValue={searchParams.get(f.name) ?? ""}
            placeholder={f.placeholder ?? f.label}
            onChange={(e) => updateDebounced(f.name, e.target.value)}
            className="rounded-md px-2 py-1 text-sm border border-[var(--border)] bg-[var(--surface-2)] text-[var(--foreground)] min-w-[200px]"
          />
        );
      })}
    </div>
  );
}
