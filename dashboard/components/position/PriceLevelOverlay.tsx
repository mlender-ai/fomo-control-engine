import type { ChartPriceLevel, PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export type ChartPriceLine = {
  label: string;
  price: number;
  kind: "entry" | "mark" | "liquidation" | "support" | "resistance" | "invalidation";
  priority: number;
  score?: number;
  touches?: number;
  sources?: string[];
  lineWidth: 1 | 2 | 3 | 4;
  opacity: number;
};

export function priceLinesForAnalysis(analysis: PositionChartAnalysis, showAllStructureLevels = false): ChartPriceLine[] {
  const range = chartDisplayRange(analysis);
  return allPriceLinesForAnalysis(analysis, showAllStructureLevels).filter((line) => {
    if (line.kind !== "liquidation") return true;
    return priceWithinRange(line.price, range);
  });
}

export function hiddenPriceLinesForAnalysis(analysis: PositionChartAnalysis): ChartPriceLine[] {
  const range = chartDisplayRange(analysis);
  return allPriceLinesForAnalysis(analysis, true).filter((line) => line.kind === "liquidation" && !priceWithinRange(line.price, range));
}

export function hasHiddenStructureLevels(analysis: PositionChartAnalysis): boolean {
  return analysis.price_levels.support.length > 3 || analysis.price_levels.resistance.length > 3;
}

function allPriceLinesForAnalysis(analysis: PositionChartAnalysis, showAllStructureLevels: boolean): ChartPriceLine[] {
  const support = showAllStructureLevels ? analysis.price_levels.support : analysis.price_levels.support.slice(0, 3);
  const resistance = showAllStructureLevels ? analysis.price_levels.resistance : analysis.price_levels.resistance.slice(0, 3);
  const lines: ChartPriceLine[] = [
    baseLine("진입가", analysis.price_levels.entry, "entry", 1),
    baseLine("현재가", analysis.price_levels.mark, "mark", 0, 2),
    ...numberLine("청산가", analysis.price_levels.liquidation, "liquidation", 2),
    ...support.map((level, index) => structureLine(level, index, "support")),
    ...resistance.map((level, index) => structureLine(level, index, "resistance")),
    ...analysis.price_levels.invalidation.slice(0, 1).flatMap((level) =>
      typeof level.price === "number" ? [baseLine(level.label || "무효화", level.price, "invalidation", 5, 2)] : []
    )
  ];
  return lines.filter((line) => Number.isFinite(line.price));
}

export function priceLineColor(kind: ChartPriceLine["kind"], opacity = 1): string {
  const alpha = Math.max(0.28, Math.min(1, opacity));
  if (kind === "entry") return rgba(98, 207, 232, alpha);
  if (kind === "mark") return rgba(238, 242, 247, alpha);
  if (kind === "liquidation") return rgba(238, 123, 128, alpha);
  if (kind === "support") return rgba(110, 210, 143, alpha);
  if (kind === "resistance") return rgba(240, 184, 64, alpha);
  return rgba(242, 139, 84, alpha);
}

export function PriceLevelLegend({
  lines,
  showAll,
  hasHiddenLevels,
  onToggleAll
}: {
  lines: ChartPriceLine[];
  showAll: boolean;
  hasHiddenLevels: boolean;
  onToggleAll: () => void;
}) {
  return (
    <div className="priceLevelLegend" aria-label="차트 가격 라인">
      {lines.map((line) => (
        <span className={`priceLevelChip level-${line.kind}`} key={`${line.kind}-${line.price}`}>
          {line.label} {formatPrice(line.price)}
        </span>
      ))}
      {hasHiddenLevels ? (
        <button className="priceLevelToggle" type="button" onClick={onToggleAll}>
          {showAll ? "핵심 레벨만" : "전체 레벨"}
        </button>
      ) : null}
    </div>
  );
}

function numberLine(label: string, price: number | null, kind: ChartPriceLine["kind"], priority: number): ChartPriceLine[] {
  return price === null ? [] : [baseLine(label, price, kind, priority)];
}

function baseLine(label: string, price: number, kind: ChartPriceLine["kind"], priority: number, lineWidth: 1 | 2 | 3 | 4 = 1): ChartPriceLine {
  return { label, price, kind, priority, lineWidth, opacity: kind === "mark" ? 1 : 0.72 };
}

function structureLine(level: ChartPriceLevel, index: number, kind: "support" | "resistance"): ChartPriceLine {
  const rankLabel = kind === "support" ? `지지 S${index + 1}` : `저항 R${index + 1}`;
  const score = Math.max(0, Math.min(100, level.score ?? 0));
  return {
    label: `${rankLabel} · 터치 ${level.touches ?? 0} · 점수 ${score}`,
    price: level.price,
    kind,
    priority: kind === "support" ? 3 + index : 6 + index,
    score,
    touches: level.touches,
    sources: level.sources,
    lineWidth: score >= 75 ? 3 : score >= 55 ? 2 : 1,
    opacity: 0.36 + score / 160
  };
}

function chartDisplayRange(analysis: PositionChartAnalysis): { min: number; max: number } {
  const candlePrices = analysis.candles.flatMap((candle) => [candle.high, candle.low]);
  const structuralPrices = [
    analysis.price_levels.entry,
    analysis.price_levels.mark,
    ...analysis.price_levels.support.map((level) => level.price),
    ...analysis.price_levels.resistance.map((level) => level.price),
    ...analysis.price_levels.invalidation.map((level) => level.price)
  ].filter(isFiniteNumber);
  const prices = [...candlePrices, ...structuralPrices];
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const padding = Math.max((max - min) * 0.16, Math.abs(analysis.price_levels.mark) * 0.015, 0.0001);
  return { min: min - padding, max: max + padding };
}

function priceWithinRange(price: number, range: { min: number; max: number }): boolean {
  return price >= range.min && price <= range.max;
}

function rgba(red: number, green: number, blue: number, alpha: number): string {
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function isFiniteNumber(value: number | null): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
