import type { PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
import type { ChartLayerState } from "@/lib/chartLayers";
import { flagColor } from "@/lib/chartTheme";

export type ChartPriceLine = {
  label: string;
  price: number;
  kind: "entry" | "mark" | "liquidation" | "support" | "resistance" | "invalidation" | "take_profit" | "poc" | "value_area";
  priority: number;
  lineWidth: 1 | 2 | 3 | 4;
  opacity: number;
};

export function priceLinesForAnalysis(
  analysis: PositionChartAnalysis,
  _plan: PositionActionPlan | null,
  _layers: ChartLayerState
): ChartPriceLine[] {
  const lines: ChartPriceLine[] = [baseLine("현재", analysis.price_levels.mark, "mark", 0, 2)];
  return lines.filter((line) => Number.isFinite(line.price));
}

export function priceLineColor(kind: ChartPriceLine["kind"], opacity = 1): string {
  const alpha = Math.max(0.28, Math.min(1, opacity));
  if (kind === "entry") return flagColor("entry", alpha);
  if (kind === "mark") return flagColor("mark", alpha);
  if (kind === "liquidation") return flagColor("liquidation", alpha);
  if (kind === "take_profit") return flagColor("takeProfit", alpha);
  if (kind === "support") return flagColor("watch", alpha);
  if (kind === "resistance") return flagColor("watch", alpha);
  if (kind === "poc") return flagColor("poc", alpha);
  if (kind === "value_area") return flagColor("valueArea", alpha);
  return flagColor("invalidation", alpha);
}

function baseLine(label: string, price: number, kind: ChartPriceLine["kind"], priority: number, lineWidth: 1 | 2 | 3 | 4 = 1): ChartPriceLine {
  return { label, price, kind, priority, lineWidth, opacity: kind === "mark" ? 1 : 0.72 };
}
