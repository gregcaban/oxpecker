import Nav from "./Nav";

export default function Shell({
  currentRun,
  children,
}: {
  currentRun: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex flex-col">
      <Nav currentRun={currentRun} />
      <main className="mx-auto w-full max-w-7xl px-4 sm:px-6 py-6 flex-1">{children}</main>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="surface p-10 text-center mt-10">
      <p className="text-lg font-medium">{title}</p>
      {hint && <p className="text-sm text-[var(--muted)] mt-2">{hint}</p>}
    </div>
  );
}
