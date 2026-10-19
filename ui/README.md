# oxpecker inspector

A local web UI for eyeballing the quality of oxpecker's generated JavaScript
programs: browse the generator catalogue, see what each generator/template
produces, spot degenerate or broken output, and read grouped findings. It's a
development tool for whoever is tuning the generators — it has no write path,
it only reads the run data the Python side already wrote.

## Run it

```bash
npm install   # first time only
npm run dev
```

Open http://localhost:3000. The run picker in the top-right switches between
runs found under `ui/data/`; the selection is kept in a `run` query param
(shareable links) and a cookie (so it persists across navigation without the
param).

`npm run build && npm run start` runs the production build, which is
meaningfully faster per request than `next dev` (dev recompiles routes on
first hit).

## Where the data comes from

Everything under `ui/data/<run-id>/` is written by the Python side via
`oxpecker export` (see the top-level oxpecker `README.md`) and is treated as
**read-only** by this app — it is read from disk with `fs` on each request,
there is no database and no write path. A run directory holds:

- `catalogue.json` — every generator/template with its family, weight,
  toggle, in/out types, description and Python source snippet.
- `samples.jsonl` — one generated program per line (seed, template, enabled
  generators, generator→line-span trace, stats, both modes' outcomes, and
  the verdict). This file is read with a streaming line reader
  (`lib/samples.ts`), never loaded fully into memory, so it scales to many
  thousands of lines; list pages page through it 50 at a time.
- `metrics.json` — per-run and per-generator/template aggregate rates
  (early-throw, syntax-in-both, fallback, timeouts, duplicate rate, …).
- `findings.json` — deduplicated hard/java-types/soft findings with an
  example program each. Optional — a run without it still works, `/findings`
  just says so.

All of the above are treated as **possibly partial**: a run can be missing
`findings.json` entirely, a sample can be missing `results` or `verdict`, a
generator can be missing from `metrics.perFocus`. Every reader in `lib/`
defends against this (optional chaining, `?? []`/`?? null` fallbacks, a
coercion pass on the raw JSON) rather than trusting the shape — the goal is
that a half-finished or hand-edited run never 500s the UI, it just renders
with fewer numbers.

## Stack

- **Next.js 15** (App Router) + **React 19** + **TypeScript**, strict mode.
- **Tailwind CSS v4** for layout/utilities; a handful of hand-written CSS
  classes in `app/globals.css` for things Tailwind doesn't do well as literal
  utility strings (badges, the code-block gutter, generator line tinting via
  a `--gen-hue` custom property, dark-mode overrides for the syntax
  highlighter). Dark/light follows `prefers-color-scheme`, no toggle.
- **Shiki** for JS/Python syntax highlighting, via `codeToTokens` (not
  `codeToHtml`) so each line can be re-wrapped in our own markup — that's
  what makes per-line generator tinting and diff markers possible. Shiki's
  oniguruma WASM ships inside the npm package, so highlighting works with no
  network access at runtime.
- **Recharts** for the three charts on the overview page.
- No database, no client-side data fetching: every page is a server
  component that reads `ui/data` directly; the only client components are
  the run picker, filter bars, the keyboard-nav listener and the "copy
  reproducer" button — all small, all just URL/clipboard plumbing.

## Pages

| Route | What it shows |
|---|---|
| `/` | Run overview: headline stat tiles (programs, verdict breakdown, early-throw/syntax-both/fallback rates, timeouts, crashes), a byte-size histogram, an exception-name breakdown, and a generator-usage bar chart with never/rarely-fired generators highlighted in red. |
| `/generators` | Sortable catalogue table (id, kind, family, weight, samples, early-throw rate, syntax rate, a "problems" score). Defaults to problems-first. Filters: family, kind, toggle, "has problems". |
| `/generators/[id]` | Description, weight, in/out types, the highlighted Python source snippet, the full per-focus metrics row, and a grid of sample programs with the lines *this* generator emitted tinted (from `generatorTrace`), each linking to its program page. |
| `/programs` | Filterable, paginated (50/page) browser: focus, template, verdict kind, per-mode status, `compiledActually=false`, exception name, byte range, free-text source search. Compact cards with per-mode status badges and a verdict badge. |
| `/programs/[id]` | Full source, each line tinted by its originating generator with a legend; interpreted vs. compiled two-column comparison (status, `compiledActually`, value, thrown, output, Java types, timing); trace/java-trace/continuations diff views with the first differing index highlighted and auto-marked back onto the source line; seed/template/enabled-generators/stats metadata; a "copy reproducer" button (plain source + the `oxpecker diff <file>` command); prev/next within whatever filter you arrived from, with `j`/`k` shortcuts. |
| `/findings` | Deduplicated findings grouped hard → java-types → soft, each with its count, the focus generators (linked), the verdict reasons, a one-line triangulation hint, and the highlighted example source linking to its program page. |

## What's not done, and why

- **No automated tests for the UI itself.** The spec's quality bar
  (`npm run build` clean, every page renders against the fixture, graceful
  partial-data handling) was verified manually against the fixture run plus
  a hand-built partial-data fixture (missing `results`, missing `verdict`,
  missing `findings.json`, a sample with no `stats`) exercised against every
  route; the latter was deleted afterward so `ui/data/` only holds the real
  fixture. There's no Playwright/Vitest harness — adding one would be the
  first thing to do next if this UI grows.
- **Byte-size filter on `/programs` is free-typed min/max**, not a slider —
  there was no cheap way to get true min/max bounds without an extra full
  scan of `samples.jsonl` purely for slider ticks, and a dev tool doesn't
  need that polish.
- **Sorting is only on `/generators`.** The spec only asked for a sortable
  table there; `/programs` gets filters + pagination as specified, not
  column sorting.
- Everything else in the spec (all six pages, all the listed filters/charts/
  diff views, the j/k nav, the copy-reproducer button) is implemented.
