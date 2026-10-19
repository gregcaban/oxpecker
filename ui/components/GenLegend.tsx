import { hashHue } from "@/lib/format";

export default function GenLegend({ gens }: { gens: string[] }) {
  if (gens.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-3 text-xs">
      {gens.map((g) => (
        <span key={g} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block w-2.5 h-2.5 rounded-sm"
            style={{ background: `hsl(${hashHue(g)} 75% 55%)` }}
          />
          <span className="font-mono">{g}</span>
        </span>
      ))}
    </div>
  );
}
