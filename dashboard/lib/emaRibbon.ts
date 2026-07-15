import type { ChartCandle } from "@/lib/api";

export const EMA_RIBBON_PERIODS = [20, 25, 30, 35, 40, 45, 50, 55] as const;
export const EMA_RIBBON_COMPRESSION_PCT = 0.65;

export type EmaRibbonState = "bullish" | "bearish" | "compressed" | "mixed";

export type EmaRibbonPoint = {
  time: number;
  value: number;
  state: EmaRibbonState;
};

export type EmaRibbonResult = {
  series: Array<{ period: number; points: EmaRibbonPoint[] }>;
  state: EmaRibbonState;
  label: string;
  spreadPct: number;
  fastValue: number;
  slowValue: number;
  priceRelation: "above" | "below" | "inside";
};

export function buildEmaRibbon(candles: ChartCandle[]): EmaRibbonResult | null {
  if (candles.length < EMA_RIBBON_PERIODS.at(-1)!) return null;
  const closes = candles.map((candle) => candle.close);
  const valuesByPeriod = EMA_RIBBON_PERIODS.map((period) => ema(closes, period));
  const startIndex = EMA_RIBBON_PERIODS.at(-1)! - 1;
  const states = candles.map((candle, index) => {
    const values = valuesByPeriod.map((valuesForPeriod) => valuesForPeriod[index]);
    return ribbonState(values, candle.close);
  });
  const series = EMA_RIBBON_PERIODS.map((period, periodIndex) => ({
    period,
    points: candles.slice(startIndex).map((candle, visibleIndex) => {
      const index = startIndex + visibleIndex;
      return {
        time: candle.time,
        value: valuesByPeriod[periodIndex][index],
        state: states[index]
      };
    })
  }));
  const latestValues = valuesByPeriod.map((values) => values.at(-1)!);
  const latestClose = closes.at(-1)!;
  const state = ribbonState(latestValues, latestClose);
  const low = Math.min(...latestValues);
  const high = Math.max(...latestValues);
  return {
    series,
    state,
    label: stateLabel(state),
    spreadPct: round(((high - low) / Math.abs(latestClose)) * 100, 2),
    fastValue: latestValues[0],
    slowValue: latestValues.at(-1)!,
    priceRelation: latestClose > high ? "above" : latestClose < low ? "below" : "inside"
  };
}

function ema(values: number[], period: number): number[] {
  const result = Array<number>(values.length).fill(Number.NaN);
  if (values.length < period) return result;
  const seed = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  result[period - 1] = seed;
  const multiplier = 2 / (period + 1);
  for (let index = period; index < values.length; index += 1) {
    result[index] = (values[index] - result[index - 1]) * multiplier + result[index - 1];
  }
  return result;
}

function ribbonState(values: number[], close: number): EmaRibbonState {
  if (values.some((value) => !Number.isFinite(value)) || !Number.isFinite(close) || close === 0) return "mixed";
  const spreadPct = ((Math.max(...values) - Math.min(...values)) / Math.abs(close)) * 100;
  if (spreadPct <= EMA_RIBBON_COMPRESSION_PCT) return "compressed";
  const bullish = values.slice(0, -1).every((value, index) => value > values[index + 1]);
  if (bullish) return "bullish";
  const bearish = values.slice(0, -1).every((value, index) => value < values[index + 1]);
  if (bearish) return "bearish";
  return "mixed";
}

function stateLabel(state: EmaRibbonState): string {
  if (state === "bullish") return "정배열";
  if (state === "bearish") return "역배열";
  if (state === "compressed") return "압축";
  return "혼조";
}

function round(value: number, digits: number): number {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}
