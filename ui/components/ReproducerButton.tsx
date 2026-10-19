"use client";

import { useState } from "react";

export default function ReproducerButton({ sourcePlain, sampleId }: { sourcePlain: string; sampleId: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    const filename = `${sampleId}.js`;
    const text = `// ${filename} — replay with: oxpecker diff ${filename}\n${sourcePlain}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // clipboard API unavailable; no-op
    }
  }

  return (
    <button
      onClick={copy}
      className="text-xs px-3 py-1.5 rounded-md border border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--border)] transition-colors"
    >
      {copied ? "copied ✓" : "copy reproducer"}
    </button>
  );
}
