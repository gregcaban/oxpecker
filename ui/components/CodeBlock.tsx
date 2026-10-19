import { tokenize, type HlLang } from "@/lib/highlight";
import { hashHue } from "@/lib/format";

export interface CodeBlockProps {
  code: string;
  className?: string;
  lang?: HlLang;
  /** 0-based line index -> generator name, used for background tinting. */
  lineGenerator?: (lineIndex: number) => string | undefined;
  /** 0-based line index that should get a left-border "diff" marker. */
  markedLines?: Set<number>;
  showLineNumbers?: boolean;
}

export default async function CodeBlock({
  code,
  className,
  lang = "javascript",
  lineGenerator,
  markedLines,
  showLineNumbers = true,
}: CodeBlockProps) {
  const lines = await tokenize(code.replace(/\n$/, ""), lang);

  return (
    <pre className={`code-block ${className ?? ""}`}>
      <code>
        {lines.map((tokens, i) => {
          const gen = lineGenerator?.(i);
          const hue = gen ? hashHue(gen) : undefined;
          const marked = markedLines?.has(i);
          return (
            <div
              key={i}
              className={`code-line${marked ? " code-line-marked" : ""}`}
              style={hue !== undefined ? ({ "--gen-hue": hue } as React.CSSProperties) : undefined}
              data-gen={gen}
              title={gen ? `generator: ${gen}` : undefined}
            >
              {showLineNumbers && <span className="line-no">{i + 1}</span>}
              <span className={`line-tint${gen ? " line-tint-on" : ""}`} aria-hidden />
              <span className="line-content">
                {tokens.length === 0
                  ? " "
                  : tokens.map((t, j) => (
                      <span
                        key={j}
                        style={
                          {
                            color: t.color,
                            "--shiki-dark": t.darkColor,
                          } as React.CSSProperties
                        }
                      >
                        {t.content}
                      </span>
                    ))}
              </span>
            </div>
          );
        })}
      </code>
    </pre>
  );
}
