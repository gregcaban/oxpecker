// Data model for oxpecker runs. The Python side writes these files; the UI only
// reads them. Everything is treated as possibly partial/missing at runtime —
// these types describe the "ideal" shape, and lib/samples.ts / lib/catalogue.ts
// etc. defend against missing fields rather than trusting this at the type level.

export type Kind = "generator" | "template";

export interface CatalogueEntry {
  id: string;
  kind: Kind;
  family: string;
  weight: number;
  toggle: string | null;
  sloppyOnly: boolean;
  inputTypes: string[];
  outputType: string;
  description: string;
  sourcePath: string;
  sourceSnippet: string;
}

export type OutcomeStatus =
  | "ok"
  | "throw"
  | "syntax"
  | "timeout"
  | "resource"
  | "fallback"
  | "crash"
  | "unsupported";

export interface ThrownInfo {
  kind: string;
  name: string;
  message: string;
  value: string;
  valueJavaTypes: string;
}

export interface EngineException {
  clazz: string;
  message: string;
  stack: string;
}

export interface Outcome {
  mode: string;
  status: OutcomeStatus;
  compiledActually: boolean;
  value: string;
  valueJavaTypes: string;
  thrown: ThrownInfo | null;
  trace: string[];
  traceJavaTypes: string[];
  javaTrace: string[];
  javaTraceTypes: string[];
  continuations: string[];
  soft: string[];
  output: string[];
  engineException: EngineException | null;
  reason: string | null;
  ms: number;
}

export interface Results {
  interpreted?: Outcome;
  compiled?: Outcome;
}

export type VerdictKind = "equal" | "hard" | "java-types" | "soft" | "skip" | "discard";

export interface Verdict {
  kind: VerdictKind;
  reasons: string[];
  javaTypeDiff: string[];
  firstTraceDiff: number | string | null;
  signature: string;
}

export interface GeneratorTraceSpan {
  gen: string;
  lines: [number, number];
}

export interface SampleStats {
  statements: number;
  topLevelStatements: number;
  vars: number;
  maxDepth: number;
  probes: number;
  bytes: number;
}

export interface Sample {
  id: string;
  runId: string;
  kind: Kind;
  focus: string;
  source: string;
  sourcePlain: string;
  seed: number;
  template: string;
  enabledGenerators: string[];
  generatorTrace: GeneratorTraceSpan[];
  stats?: Partial<SampleStats>;
  results?: Results;
  verdict?: Verdict;
}

export interface PerFocusMetrics {
  kind: Kind;
  samples: number;
  fired: number;
  usageTotal: number;
  syntaxBothRate: number;
  earlyThrowRate: number;
  throwRate: number;
  meanProbesBeforeThrow: number | null;
  timeouts: number;
  resource: number;
  fallback: number;
  crash: number;
  hard: number;
  javaTypes: number;
  soft: number;
  equal: number;
  discard: number;
  meanBytes: number;
  meanStatements: number;
  duplicateRate: number;
  exceptions: Record<string, number>;
}

export interface Metrics {
  runId: string;
  languageVersion: number;
  samples: number;
  generatorUsage: Record<string, number>;
  perFocus: Record<string, PerFocusMetrics>;
}

export interface Finding {
  signature: string;
  kind: "hard" | "java-types" | "soft";
  count: number;
  focus: Record<string, number>;
  example: string;
  verdict: Verdict;
  source: string;
}

export interface RunInfo {
  id: string;
  hasCatalogue: boolean;
  hasSamples: boolean;
  hasMetrics: boolean;
  hasFindings: boolean;
}
