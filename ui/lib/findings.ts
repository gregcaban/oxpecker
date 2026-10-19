import fs from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import { runDir } from "./paths";
import type { Finding } from "./types";

export const getFindings = cache(async (runId: string): Promise<Finding[]> => {
  try {
    const raw = await fs.readFile(path.join(runDir(runId), "findings.json"), "utf8");
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (f): f is Finding => f && typeof f === "object" && typeof f.signature === "string",
    );
  } catch {
    return [];
  }
});
