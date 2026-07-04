import type { ChartPriceLevel, PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
import type { ChartLayerState } from "@/lib/chartLayers";

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
  plan: PositionActionPlan | null,
  layers: ChartLayerState
): ChartPriceLine[] {
  const lines: ChartPriceLine[] = [
    baseLine("진입", analysis.price_levels.entry, "entry", 1),
    baseLine("현재", analysis.price_levels.mark, "mark", 0, 2)
  ];

  if (layers.plan) {
    const invalidationPrice = plan?.invalidation?.price ?? analysis.price_levels.invalidation.find((level) => typeof level.price === "number")?.price ?? null;
    if (typeof invalidationPrice === "number") {
      lines.push(baseLine("무효화", invalidationPrice, "invalidation", 2, 2));
    }
    const targets = (plan?.take_profit ?? []).filter((item) => typeof item.price === "number").slice(0, 2);
    targets.forEach((target, index) => {
      lines.push(baseLine(targets.length > 1 ? `익절${index + 1}` : "익절", target.price as number, "take_profit", 3 + index));
    });
  }

  if (layers.ta.includes("levels")) {
    analysis.price_levels.support.slice(0, 3).forEach((level, index) => lines.push(structureLine(level, index, "support")));
    analysis.price_levels.resistance.slice(0, 3).forEach((level, index) => lines.push(structureLine(level, index, "resistance")));
  }

  if (layers.ta.includes("volume_profile")) {
    lines.push(
      baseLine("POC", analysis.volume_profile.poc_price, "poc", 11, 2),
      baseLine("VAH", analysis.volume_profile.value_area_high, "value_area", 12),
      baseLine("VAL", analysis.volume_profile.value_area_low, "value_area", 13)
    );
  }

  return lines.filter((line) => Number.isFinite(line.price));
}

export function priceLineColor(kind: ChartPriceLine["kind"], opacity = 1): string {
  const alpha = Math.max(0.28, Math.min(1, opacity));
  if (kind === "entry") return rgba(98, 207, 232, alpha);
  if (kind === "mark") return rgba(238, 242, 247, alpha);
  if (kind === "liquidation") return rgba(238, 123, 128, alpha);
  if (kind === "take_profit") return rgba(127, 238, 100, alpha);
  if (kind === "support") return rgba(110, 210, 143, alpha);
  if (kind === "resistance") return rgba(240, 184, 64, alpha);
  if (kind === "poc") return rgba(174, 210, 164, alpha);
  if (kind === "value_area") return rgba(147, 166, 142, alpha);
  return rgba(242, 139, 84, alpha);
}

function baseLine(label: string, price: number, kind: ChartPriceLine["kind"], priority: number, lineWidth: 1 | 2 | 3 | 4 = 1): ChartPriceLine {
  return { label, price, kind, priority, lineWidth, opacity: kind === "mark" ? 1 : 0.72 };
}

function structureLine(level: ChartPriceLevel, index: number, kind: "support" | "resistance"): ChartPriceLine {
  const score = Math.max(0, Math.min(100, level.score ?? 0));
  return {
    label: kind === "support" ? `S${index + 1}` : `R${index + 1}`,
    price: level.price,
    kind,
    priority: kind === "support" ? 3 + index : 6 + index,
    lineWidth: score >= 75 ? 3 : score >= 55 ? 2 : 1,
    opacity: 0.36 + score / 160
  };
}

function rgba(red: number, green: number, blue: number, alpha: number): string {
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}
