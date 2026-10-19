import { cookies } from "next/headers";
import { listRuns, resolveRunId } from "./paths";

export type SearchParams = Record<string, string | string[] | undefined>;

export function firstParam(sp: SearchParams | undefined, key: string): string | undefined {
  const v = sp?.[key];
  if (Array.isArray(v)) return v[0];
  return v;
}

/** Resolve the active run: ?run= query param wins, then the `run` cookie, then newest. */
export async function getCurrentRunId(sp: SearchParams | undefined): Promise<string | null> {
  const fromQuery = firstParam(sp, "run");
  if (fromQuery) {
    const resolved = resolveRunId(fromQuery);
    if (resolved) return resolved;
  }
  const jar = await cookies();
  const fromCookie = jar.get("run")?.value;
  return resolveRunId(fromCookie);
}

/** Serialize searchParams (minus `page`) back into a query string, e.g. for carrying filter context. */
export function buildQueryString(sp: SearchParams | undefined, overrides?: SearchParams): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(sp ?? {})) {
    if (v === undefined || k === "page") continue;
    params.set(k, Array.isArray(v) ? v[0] : v);
  }
  for (const [k, v] of Object.entries(overrides ?? {})) {
    if (v === undefined) continue;
    params.set(k, Array.isArray(v) ? v[0] : v);
  }
  return params.toString();
}

export { listRuns };
