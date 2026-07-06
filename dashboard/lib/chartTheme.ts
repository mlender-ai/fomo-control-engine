export type ChartTone =
  | "teal"
  | "green"
  | "red"
  | "amber"
  | "blue"
  | "purple"
  | "neutral"
  | "text"
  | "muted"
  | "panel";

export type ChartFlagKind = "entry" | "mark" | "invalidation" | "takeProfit" | "watch" | "liquidation" | "poc" | "valueArea";
export type ChartZoneKind = "support" | "resistance" | "profit" | "risk" | "prz" | "liquidity" | "liquidationCluster" | "range" | "neutral";
export type ChartStrokeKind = "major" | "minor" | "scenario";

const toneVar: Record<ChartTone, string> = {
  teal: "--chart-teal-rgb",
  green: "--chart-green-rgb",
  red: "--chart-red-rgb",
  amber: "--chart-amber-rgb",
  blue: "--chart-blue-rgb",
  purple: "--chart-purple-rgb",
  neutral: "--chart-neutral-rgb",
  text: "--chart-text-rgb",
  muted: "--chart-muted-rgb",
  panel: "--chart-panel-rgb"
};

export const chartTheme = {
  candle: {
    up: color("green"),
    down: color("red"),
    wickUp: color("green", 0.92),
    wickDown: color("red", 0.92),
    volumeUp: color("green", 0.28),
    volumeDown: color("red", 0.3),
    volumeNeutral: color("neutral", 0.28)
  },
  zone: {
    support: (score = 50) => color("teal", scoreOpacity(score)),
    resistance: (score = 50) => color("red", scoreOpacity(score)),
    profit: color("green", 0.1),
    risk: color("red", 0.1),
    prz: (forming = false) => color("purple", forming ? 0.06 : 0.18),
    liquidity: (score = 50) => color("amber", scoreOpacity(score)),
    liquidationCluster: color("purple", 0.12),
    range: color("neutral", 0.08),
    neutral: color("neutral", 0.08)
  },
  flag: {
    entry: color("blue", 0.94),
    mark: color("text", 0.9),
    invalidation: color("red", 0.94),
    takeProfit: color("green", 0.94),
    watch: color("amber", 0.94),
    liquidation: color("red", 0.84),
    poc: color("amber", 0.9),
    valueArea: color("neutral", 0.86)
  },
  stroke: {
    major: { width: 2, dash: "" },
    minor: { width: 1, dash: "5 5" },
    scenario: { width: 1, dash: "3 6" }
  },
  color
};

export type ResolvedChartPalette = {
  color: (tone: ChartTone, alpha?: number) => string;
  flag: (kind: ChartFlagKind, alpha?: number) => string;
  zone: (kind: ChartZoneKind, alpha?: number, score?: number) => string;
};

export function createChartPalette(root: Element): ResolvedChartPalette {
  const style = getComputedStyle(root);
  const rgb = (tone: ChartTone) => style.getPropertyValue(toneVar[tone]).trim() || fallbackRgb[tone];
  const colorFrom = (tone: ChartTone, alpha = 1) => `rgba(${rgb(tone)}, ${clampAlpha(alpha)})`;
  return {
    color: colorFrom,
    flag: (kind, alpha = 1) => colorFrom(flagTone[kind], alpha),
    zone: (kind, alpha, score) => {
      if (kind === "support") return colorFrom("teal", alpha ?? scoreOpacity(score ?? 50));
      if (kind === "resistance") return colorFrom("red", alpha ?? scoreOpacity(score ?? 50));
      if (kind === "profit") return colorFrom("green", alpha ?? 0.1);
      if (kind === "risk") return colorFrom("red", alpha ?? 0.1);
      if (kind === "prz") return colorFrom("purple", alpha ?? 0.18);
      if (kind === "liquidity") return colorFrom("amber", alpha ?? scoreOpacity(score ?? 50));
      if (kind === "liquidationCluster") return colorFrom("purple", alpha ?? 0.12);
      return colorFrom("neutral", alpha ?? 0.08);
    }
  };
}

export function color(tone: ChartTone, alpha = 1): string {
  return `rgba(var(${toneVar[tone]}), ${clampAlpha(alpha)})`;
}

export function scoreOpacity(score: number): number {
  const bounded = Math.max(0, Math.min(100, score));
  return 0.12 + (bounded / 100) * 0.16;
}

export function flagColor(kind: ChartFlagKind, alpha = 1): string {
  return color(flagTone[kind], alpha);
}

export function zoneColor(kind: ChartZoneKind, alpha?: number, score?: number): string {
  if (kind === "support") return color("teal", alpha ?? scoreOpacity(score ?? 50));
  if (kind === "resistance") return color("red", alpha ?? scoreOpacity(score ?? 50));
  if (kind === "profit") return color("green", alpha ?? 0.1);
  if (kind === "risk") return color("red", alpha ?? 0.1);
  if (kind === "prz") return color("purple", alpha ?? 0.18);
  if (kind === "liquidity") return color("amber", alpha ?? scoreOpacity(score ?? 50));
  if (kind === "liquidationCluster") return color("purple", alpha ?? 0.12);
  return color("neutral", alpha ?? 0.08);
}

function clampAlpha(alpha: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(alpha) ? alpha : 1));
}

const flagTone: Record<ChartFlagKind, ChartTone> = {
  entry: "blue",
  mark: "text",
  invalidation: "red",
  takeProfit: "green",
  watch: "amber",
  liquidation: "red",
  poc: "amber",
  valueArea: "neutral"
};

const fallbackRgb: Record<ChartTone, string> = {
  teal: "0, 209, 178",
  green: "0, 192, 135",
  red: "255, 91, 90",
  amber: "240, 184, 64",
  blue: "98, 207, 232",
  purple: "168, 118, 255",
  neutral: "147, 166, 142",
  text: "238, 242, 247",
  muted: "140, 171, 135",
  panel: "0, 0, 0"
};
