import type { PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";

export type ChartPriceLine = {
  label: string;
  price: number;
  kind: "entry" | "mark" | "liquidation" | "support" | "resistance" | "invalidation";
};

export function priceLinesForAnalysis(analysis: PositionChartAnalysis): ChartPriceLine[] {
  const lines: ChartPriceLine[] = [
    { label: "Entry", price: analysis.price_levels.entry, kind: "entry" },
    { label: "Mark", price: analysis.price_levels.mark, kind: "mark" },
    ...numberLine("Liq", analysis.price_levels.liquidation, "liquidation"),
    ...analysis.price_levels.support.slice(0, 2).map((level) => ({ label: "Support", price: level.price, kind: "support" as const })),
    ...analysis.price_levels.resistance.slice(0, 2).map((level) => ({ label: "Resistance", price: level.price, kind: "resistance" as const })),
    ...analysis.price_levels.invalidation.slice(0, 1).map((level) => ({ label: "Invalidation", price: level.price, kind: "invalidation" as const }))
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
    <div className="priceLevelLegend" aria-label="Chart price levels">
      {lines.map((line) => (
        <span className={`priceLevelChip level-${line.kind}`} key={`${line.kind}-${line.price}`}>
          {line.label} {formatPrice(line.price)}
        </span>
      ))}
    </div>
  );
}

function numberLine(label: string, price: number | null, kind: ChartPriceLine["kind"]): ChartPriceLine[] {
  return price === null ? [] : [{ label, price, kind }];
}
