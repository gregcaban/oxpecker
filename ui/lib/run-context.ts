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

export { listRuns };
