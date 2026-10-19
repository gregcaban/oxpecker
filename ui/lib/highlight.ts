import { createHighlighter, type Highlighter } from "shiki";

let highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = createHighlighter({
      themes: ["github-light", "github-dark"],
      langs: ["javascript", "python"],
    });
  }
  return highlighterPromise;
}

export interface HlToken {
  content: string;
  color?: string;
  darkColor?: string;
}

export type HlLang = "javascript" | "python";

/** Tokenize source into per-line arrays of styled tokens (light+dark colors). */
export async function tokenize(code: string, lang: HlLang = "javascript"): Promise<HlToken[][]> {
  try {
    const hl = await getHighlighter();
    const result = hl.codeToTokens(code, {
      lang,
      themes: { light: "github-light", dark: "github-dark" },
    });
    return result.tokens.map((line) =>
      line.map((t) => ({
        content: t.content,
        color: typeof t.htmlStyle === "object" ? t.htmlStyle.color : undefined,
        darkColor:
          typeof t.htmlStyle === "object" ? t.htmlStyle["--shiki-dark"] : undefined,
      })),
    );
  } catch {
    // Fall back to unhighlighted plain text, one "token" per line.
    return code.split("\n").map((line) => [{ content: line }]);
  }
}
