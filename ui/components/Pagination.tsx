import Link from "next/link";

export default function Pagination({
  basePath,
  searchParams,
  page,
  pageSize,
  total,
}: {
  basePath: string;
  searchParams: Record<string, string | string[] | undefined>;
  page: number;
  pageSize: number;
  total: number;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const clamped = Math.min(Math.max(1, page), pageCount);

  function hrefFor(p: number) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(searchParams)) {
      if (v === undefined || k === "page") continue;
      params.set(k, Array.isArray(v) ? v[0] : v);
    }
    params.set("page", String(p));
    return `${basePath}?${params.toString()}`;
  }

  const from = total === 0 ? 0 : (clamped - 1) * pageSize + 1;
  const to = Math.min(total, clamped * pageSize);

  return (
    <div className="flex items-center justify-between gap-3 text-sm py-3">
      <span className="text-[var(--muted)]">
        {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1">
        <PageLink href={hrefFor(1)} disabled={clamped <= 1}>
          «
        </PageLink>
        <PageLink href={hrefFor(clamped - 1)} disabled={clamped <= 1}>
          ‹ prev
        </PageLink>
        <span className="px-2 text-[var(--muted)]">
          page {clamped} / {pageCount}
        </span>
        <PageLink href={hrefFor(clamped + 1)} disabled={clamped >= pageCount}>
          next ›
        </PageLink>
        <PageLink href={hrefFor(pageCount)} disabled={clamped >= pageCount}>
          »
        </PageLink>
      </div>
    </div>
  );
}

function PageLink({
  href,
  disabled,
  children,
}: {
  href: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  if (disabled) {
    return <span className="px-2 py-1 rounded-md text-[var(--muted)] opacity-40">{children}</span>;
  }
  return (
    <Link href={href} className="px-2 py-1 rounded-md hover:bg-[var(--surface-2)]">
      {children}
    </Link>
  );
}
