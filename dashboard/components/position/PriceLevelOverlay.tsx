import type { PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export type ChartPriceLine = {
  label: string;
  price: number;
  kind: "entry" | "mark" | "liquidation" | "support" | "resistance" | "invalidation";
  priority: number;
};

export function priceLinesForAnalysis(analysis: PositionChartAnalysis): ChartPriceLine[] {
  const range = chartDisplayRange(analysis);
  return allPriceLinesForAnalysis(analysis).filter((line) => {
    if (line.kind !== "liquidation") return true;
    return priceWithinRange(line.price, range);
  });
}

export function hiddenPriceLinesForAnalysis(analysis: PositionChartAnalysis): ChartPriceLine[] {
  const range = chartDisplayRange(analysis);
  return allPriceLinesForAnalysis(analysis).filter((line) => line.kind === "liquidation" && !priceWithinRange(line.price, range));
}

function allPriceLinesForAnalysis(analysis: PositionChartAnalysis): ChartPriceLine[] {
  const lines: ChartPriceLine[] = [
    { label: "진입가", price: analysis.price_levels.entry, kind: "entry", priority: 1 },
    { label: "현재가", price: analysis.price_levels.mark, kind: "mark", priority: 0 },
    ...numberLine("청산가", analysis.price_levels.liquidation, "liquidation", 2),
    ...analysis.price_levels.support.slice(0, 2).map((level, index) => ({ label: index === 0 ? "지지선" : "보조 지지선", price: level.price, kind: "support" as const, priority: 3 + index })),
    ...analysis.price_levels.resistance.slice(0, 2).map((level, index) => ({ label: index === 0 ? "저항선" : "보조 저항선", price: level.price, kind: "resistance" as const, priority: 4 + index })),
    ...analysis.price_levels.invalidation.slice(0, 1).map((level) => ({ label: level.label || "무효화 가격", price: level.price, kind: "invalidation" as const, priority: 5 }))
  ];
  return lines.filter((line) => Number.isFinite(line.price));
}

export function priceLineColor(kind: ChartPriceLine["kind"]): string {
  if (kind === "entry") return "#62cfe8";
  if (kind === "mark") return "#eef2f7";
  if (kind === "liquidation") return "#ee7b80";
  if (kind === "support") return "#6ed28f";
  if (kind === "resistance") return "#f0b840";
  return "#f28b54";
}

export function PriceLevelLegend({ lines }: { lines: ChartPriceLine[] }) {
  return (
    <div className="priceLevelLegend" aria-label="차트 가격 라인">
      {lines.map((line) => (
        <span className={`priceLevelChip level-${line.kind}`} key={`${line.kind}-${line.price}`}>
          {line.label} {formatPrice(line.price)}
        </span>
      ))}
    </div>
  );
}

function numberLine(label: string, price: number | null, kind: ChartPriceLine["kind"], priority: number): ChartPriceLine[] {
  return price === null ? [] : [{ label, price, kind, priority }];
}

function chartDisplayRange(analysis: PositionChartAnalysis): { min: number; max: number } {
  const candlePrices = analysis.candles.flatMap((candle) => [candle.high, candle.low]);
  const structuralPrices = [
    analysis.price_levels.entry,
    analysis.price_levels.mark,
    ...analysis.price_levels.support.map((level) => level.price),
    ...analysis.price_levels.resistance.map((level) => level.price),
    ...analysis.price_levels.invalidation.map((level) => level.price)
  ].filter((price) => Number.isFinite(price));
  const prices = [...candlePrices, ...structuralPrices];
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const padding = Math.max((max - min) * 0.16, Math.abs(analysis.price_levels.mark) * 0.015, 0.0001);
  return { min: min - padding, max: max + padding };
}

function priceWithinRange(price: number, range: { min: number; max: number }): boolean {
  return price >= range.min && price <= range.max;
}
