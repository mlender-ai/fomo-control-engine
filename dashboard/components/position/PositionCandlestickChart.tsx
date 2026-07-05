"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type Time
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChartCandle, ChartPriceLevel, PositionActionPlan, PositionActionPlanItem, PositionChartAnalysis, PositionWatchTrigger, WyckoffMarker } from "@/lib/api";
import { CHART_LAYER_DEFS, layerActive, type ChartLayerId, type ChartLayerState } from "@/lib/chartLayers";
import { chartTheme, createChartPalette, type ResolvedChartPalette } from "@/lib/chartTheme";
import { formatPrice } from "@/lib/format";
import { localizeMarketCodes, phaseHintLabel, sourceLabel, timeframeLabel } from "@/lib/labels/marketStateLabels";
import { splitWyckoffEvents, taShortLabel } from "@/lib/labels/taGlossary";
import { priceLinesForAnalysis, type ChartPriceLine } from "./PriceLevelOverlay";
import type { PositionChartOverlay } from "./PositionChart";
import { VolumePanel } from "./VolumePanel";

const LABEL_MERGE_PX = 8;
const AXIS_GUTTER = 82;

export function PositionCandlestickChart({
  analysis,
  trendSummary,
  plan,
  layers,
  onToggleLayer,
  highlightPrice = null,
  positionOverlay = null
}: {
  analysis: PositionChartAnalysis;
  trendSummary: string;
  plan: PositionActionPlan | null;
  layers: ChartLayerState;
  onToggleLayer: (id: ChartLayerId, additive: boolean) => void;
  highlightPrice?: number | null;
  positionOverlay?: PositionChartOverlay | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [harmonicIndex, setHarmonicIndex] = useState(0);
  const validation = useMemo(() => validateCandles(analysis.candles), [analysis.candles]);
  const priceLines = useMemo(
    () => (validation.valid ? priceLinesForAnalysis(analysis, plan, layers) : []),
    [analysis, plan, layers, validation.valid]
  );
  const lastCandle = validation.candles.at(-1);
  const averageVolume = validation.valid ? average(validation.candles.map((candle) => candle.volume)) : 0;
  const harmonicFocused = layers.ta.includes("harmonic");
  const harmonicPatterns = useMemo(
    () => [...analysis.harmonic_patterns].sort((left, right) => right.confidence - left.confidence),
    [analysis.harmonic_patterns]
  );
  const activeHarmonic = harmonicPatterns.length ? harmonicPatterns[((harmonicIndex % harmonicPatterns.length) + harmonicPatterns.length) % harmonicPatterns.length] : null;

  useEffect(() => {
    setHarmonicIndex(0);
  }, [analysis.position_id, analysis.timeframe]);

  useEffect(() => {
    if (!validation.valid || !containerRef.current || !validation.candles.length) return;
    const container = containerRef.current;
    const overlay = overlayRef.current;
    const palette = createChartPalette(container);
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: palette.color("panel") },
        textColor: palette.color("muted"),
        fontFamily: "SF Mono, Monaco, Consolas, monospace",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: palette.color("neutral", 0.18) },
        horzLines: { color: palette.color("neutral", 0.2) }
      },
      localization: {
        locale: "ko-KR",
        priceFormatter: (price: number) => formatPrice(price),
        timeFormatter: (time: Time) => formatKoreanDateTime(Number(time))
      },
      rightPriceScale: {
        borderColor: palette.color("neutral", 0.44),
        scaleMargins: { top: 0.08, bottom: 0.26 }
      },
      timeScale: {
        borderColor: palette.color("neutral", 0.44),
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: false,
        rightOffset: 18,
        barSpacing: 13,
        minBarSpacing: 5
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true
      },
      crosshair: {
        mode: 1,
        horzLine: {
          color: palette.color("text", 0.26),
          labelBackgroundColor: palette.color("panel", 0.92)
        },
        vertLine: {
          color: palette.color("text", 0.18),
          labelBackgroundColor: palette.color("panel", 0.92)
        }
      }
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: palette.color("green"),
      downColor: palette.color("red"),
      wickUpColor: palette.color("green", 0.92),
      wickDownColor: palette.color("red", 0.92),
      borderUpColor: palette.color("green", 0.9),
      borderDownColor: palette.color("red", 0.9),
      borderVisible: true,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const candleData: CandlestickData[] = validation.candles.map((candle) => ({
      time: candle.time as Time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close
    }));
    candleSeries.setData(candleData);

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      priceLineVisible: false,
      lastValueVisible: false
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.76, bottom: 0 }
    });

    const volumeData: HistogramData[] = validation.candles.map((candle) => ({
      time: candle.time as Time,
      value: candle.volume,
      color: layers.flow ? volumeColorForCandle(analysis, candle, palette) : simpleVolumeColor(candle, palette)
    }));
    volumeSeries.setData(volumeData);

    if (layers.flow) {
      const averageVolumeSeries = chart.addSeries(LineSeries, {
        priceScaleId: "volume",
        color: palette.color("neutral", 0.64),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false
      });
      averageVolumeSeries.setData(
        validation.candles.map((candle) => ({
          time: candle.time as Time,
          value: averageVolume
        }))
      );
      if (analysis.trade_flow.cvd.length) {
        const cvdSeries = chart.addSeries(LineSeries, {
          priceScaleId: "cvd",
          color: palette.color("blue", 0.72),
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        cvdSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.86, bottom: 0 }
        });
        cvdSeries.setData(
          analysis.trade_flow.cvd.map((point) => ({
            time: point.time as Time,
            value: point.value
          }))
        );
      }
    }

    if (layers.ta.includes("indicators")) {
      const bands: Array<{ key: "upper" | "middle" | "lower"; style: LineStyle }> = [
        { key: "upper", style: LineStyle.Dashed },
        { key: "middle", style: LineStyle.Solid },
        { key: "lower", style: LineStyle.Dashed }
      ];
      for (const band of bands) {
        const points = analysis.indicators.bollinger[band.key];
        if (!points?.length) continue;
        const bandSeries = chart.addSeries(LineSeries, {
          color: band.key === "middle" ? palette.color("neutral", 0.6) : palette.color("blue", 0.45),
          lineWidth: 1,
          lineStyle: band.style,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        bandSeries.setData(points.map((point) => ({ time: point.time as Time, value: point.value })));
      }
    }

    const labeled = mergeLineLabels(priceLines, container.clientHeight, validation.candles);
    labeled.forEach((line) => {
      const highlighted = highlightPrice !== null && Math.abs(line.price - highlightPrice) <= Math.abs(highlightPrice) * 1e-9 + 1e-12;
      candleSeries.createPriceLine({
        price: line.price,
        color: chartLineColor(line.kind, palette, highlighted ? 1 : line.opacity),
        lineWidth: (highlighted ? Math.min(4, line.lineWidth * 2) : line.lineWidth) as 1 | 2 | 3 | 4,
        lineStyle: line.kind === "mark" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: highlighted,
        title: line.title
      });
    });

    const spikeMarkers = layers.flow
      ? validation.candles
          .filter((candle) => candle.volume >= averageVolume * 1.8)
          .slice(-3)
          .map((candle) => ({
            time: candle.time as Time,
            position: "belowBar" as const,
            color: palette.color("neutral", 0.82),
            shape: "circle" as const,
            text: ""
          }))
      : [];
    if (spikeMarkers.length) {
      createSeriesMarkers(candleSeries, spikeMarkers.sort((left, right) => Number(left.time) - Number(right.time)));
    }

    chart.subscribeCrosshairMove((param) => {
      const tooltip = tooltipRef.current;
      if (!tooltip || !param.time || !param.point) {
        if (tooltip) resetHoverReadout(tooltip);
        return;
      }
      const data = param.seriesData.get(candleSeries);
      if (!isCandlestickData(data)) {
        resetHoverReadout(tooltip);
        return;
      }
      tooltip.classList.add("is-active");
      tooltip.innerHTML = `
        <strong>${formatKoreanDateTime(Number(data.time))}</strong>
        <span>시가 ${formatPrice(data.open)}</span>
        <span>고가 ${formatPrice(data.high)}</span>
        <span>저가 ${formatPrice(data.low)}</span>
        <span>종가 ${formatPrice(data.close)}</span>
        <span>거래량 ${formatCompactNumber(volumeAtTime(validation.candles, Number(data.time)))}</span>
      `;
    });

    chart.timeScale().fitContent();
    chart.timeScale().applyOptions({ rightOffset: 18 });
    const drawOverlay = () => renderTaOverlay(overlay, container, candleSeries, chart, analysis, plan, layers, activeHarmonic, positionOverlay, highlightPrice, palette);
    window.setTimeout(drawOverlay, 0);
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawOverlay);
    const resizeObserver = new ResizeObserver(drawOverlay);
    resizeObserver.observe(container);
    return () => {
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawOverlay);
      chart.remove();
    };
  }, [analysis, averageVolume, priceLines, plan, layers, highlightPrice, activeHarmonic, positionOverlay, validation]);

  if (!validation.valid) {
    return (
      <div className="chartErrorState">
        <strong>캔들 데이터가 올바르지 않습니다.</strong>
        <p>Bitget OHLCV 매핑을 확인해주세요.</p>
        <small>{validation.reason}</small>
      </div>
    );
  }

  return (
    <>
      <div className="positionChartHeader">
        <div>
          <h2>{analysis.symbol} 차트</h2>
          <p>{timeframeLabel(analysis.timeframe)} · {sourceLabel(analysis.data_quality.source)} · 마지막 캔들: {lastCandle ? formatKoreanDateTime(lastCandle.time) : "-"}</p>
        </div>
        <span>{trendSummary}</span>
      </div>
      <div className="taLayerToggle" role="group" aria-label="차트 레이어 선택">
        {CHART_LAYER_DEFS.map((layer) => (
          <button
            aria-pressed={layerActive(layers, layer.id)}
            className={layerActive(layers, layer.id) ? "active" : ""}
            key={layer.id}
            onClick={(event) => onToggleLayer(layer.id, event.shiftKey)}
            title={`${layer.description} (shift-클릭: 비교 모드)`}
            type="button"
          >
            {layer.label}
          </button>
        ))}
        <small>shift-클릭으로 TA 레이어 비교</small>
      </div>
      {harmonicFocused ? (
        <div className="harmonicNav">
          {activeHarmonic ? (
            <>
              <button onClick={() => setHarmonicIndex((value) => value - 1)} type="button" aria-label="이전 패턴">◀</button>
              <span title={harmonicPatternTitle(activeHarmonic)}>
                {activeHarmonic.label} · {activeHarmonic.direction === "bearish" ? "하락 반전 후보 구간(PRZ)" : "상승 반전 후보 구간(PRZ)"} · 신뢰도 {activeHarmonic.confidence}
                {harmonicPatterns.length > 1 ? ` · ${(((harmonicIndex % harmonicPatterns.length) + harmonicPatterns.length) % harmonicPatterns.length) + 1}/${harmonicPatterns.length}` : ""}
              </span>
              <button onClick={() => setHarmonicIndex((value) => value + 1)} type="button" aria-label="다음 패턴">▶</button>
            </>
          ) : (
            <span className="muted">반전 패턴 조건을 충족한 구간이 없어 하모닉 판정을 보류 중입니다.</span>
          )}
        </div>
      ) : null}
      <div className="chartHoverReadout" ref={tooltipRef}>
        <strong>캔들 정보</strong>
        <span>시가·고가·저가·종가·거래량</span>
      </div>
      <div className="positionChartCanvasFrame">
        <div className="positionChartCanvas" ref={containerRef} />
        <svg className="volumeProfileOverlay" ref={overlayRef} aria-hidden="true" />
      </div>
      {layers.flow ? <VolumePanel analysis={analysis} averageVolume={averageVolume} /> : null}
    </>
  );
}

type LabeledPriceLine = ChartPriceLine & { title: string };

/** 같은 y좌표 ±8px 내 가격선 라벨을 병합한다 ("S1 · POC 260.4"). */
function mergeLineLabels(lines: ChartPriceLine[], containerHeight: number, candles: ChartCandle[]): LabeledPriceLine[] {
  if (!lines.length) return [];
  const prices = candles.flatMap((candle) => [candle.high, candle.low]).concat(lines.map((line) => line.price));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const priceRange = Math.max(max - min, 1e-12);
  // rightPriceScale margins(top 0.08 / bottom 0.26)을 뺀 실제 가격 영역 높이 근사
  const plotHeight = Math.max(containerHeight * (1 - 0.08 - 0.26), 1);
  const mergeThreshold = (LABEL_MERGE_PX / plotHeight) * priceRange;

  const sorted = [...lines].sort((left, right) => left.price - right.price);
  const result: LabeledPriceLine[] = [];
  let group: ChartPriceLine[] = [];
  const flush = () => {
    if (!group.length) return;
    const anchor = [...group].sort((left, right) => left.priority - right.priority)[0];
    for (const line of group) {
      result.push({
        ...line,
        title: line === anchor ? `${group.map((item) => item.label).join(" · ")} ${formatPrice(anchor.price)}` : ""
      });
    }
    group = [];
  };
  for (const line of sorted) {
    if (group.length && Math.abs(line.price - group[group.length - 1].price) > mergeThreshold) flush();
    group.push(line);
  }
  flush();
  return result;
}

type CandleValidation =
  | { valid: true; candles: ChartCandle[]; reason: "" }
  | { valid: false; candles: ChartCandle[]; reason: string };

function validateCandles(candles: ChartCandle[]): CandleValidation {
  const validCandles: ChartCandle[] = [];
  let previousTime = 0;
  for (const candle of candles) {
    const validNumber = [candle.time, candle.open, candle.high, candle.low, candle.close, candle.volume].every(Number.isFinite);
    if (!validNumber) return { valid: false, candles: [], reason: "숫자가 아닌 값이 포함되어 있습니다." };
    if (candle.open <= 0 || candle.high <= 0 || candle.low <= 0 || candle.close <= 0) return { valid: false, candles: [], reason: "가격은 0보다 커야 합니다." };
    if (candle.high < candle.low || candle.high < candle.open || candle.high < candle.close || candle.low > candle.open || candle.low > candle.close) {
      return { valid: false, candles: [], reason: "OHLC 고가/저가 관계가 맞지 않습니다." };
    }
    if (candle.time <= previousTime) return { valid: false, candles: [], reason: "캔들 시간이 오름차순이 아닙니다." };
    previousTime = candle.time;
    validCandles.push(candle);
  }
  return { valid: true, candles: validCandles, reason: "" };
}

function isCandlestickData(value: unknown): value is CandlestickData {
  if (typeof value !== "object" || value === null) return false;
  return ["open", "high", "low", "close", "time"].every((key) => key in value);
}

function average(values: number[]): number {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function volumeAtTime(candles: ChartCandle[], time: number): number {
  return candles.find((candle) => candle.time === time)?.volume ?? 0;
}

function nearestCandleAtOrAfter(candles: ChartCandle[], time: number): ChartCandle | null {
  return candles.find((candle) => candle.time >= time) ?? candles.at(-1) ?? null;
}

function formatSignedNumber(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(Math.abs(value) >= 100 ? 1 : 2)}`;
}

function formatSignedPercent(value: number): string {
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatCompactQuantity(value: number): string {
  if (!Number.isFinite(value)) return "-";
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${trimFixed(value / 1_000_000, 2)}M`;
  if (abs >= 1_000) return new Intl.NumberFormat("en-US", { maximumFractionDigits: 4 }).format(value);
  if (abs >= 1) return trimFixed(value, 4);
  return trimFixed(value, 6);
}

function trimFixed(value: number, digits: number): string {
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

function simpleVolumeColor(candle: ChartCandle, palette: ResolvedChartPalette): string {
  return candle.close >= candle.open ? palette.color("green", 0.28) : palette.color("red", 0.3);
}

function volumeColorForCandle(analysis: PositionChartAnalysis, candle: ChartCandle, palette: ResolvedChartPalette): string {
  const bucket = analysis.trade_flow.buckets.find((item) => item.time === candle.time);
  if (bucket) {
    if (bucket.delta > 0) return palette.color("green", 0.42);
    if (bucket.delta < 0) return palette.color("red", 0.42);
    return palette.color("neutral", 0.28);
  }
  return simpleVolumeColor(candle, palette);
}

function chartLineColor(kind: ChartPriceLine["kind"], palette: ResolvedChartPalette, opacity = 1): string {
  if (kind === "entry") return palette.flag("entry", opacity);
  if (kind === "mark") return palette.flag("mark", opacity);
  if (kind === "liquidation") return palette.flag("liquidation", opacity);
  if (kind === "take_profit") return palette.flag("takeProfit", opacity);
  if (kind === "poc") return palette.flag("poc", opacity);
  if (kind === "value_area") return palette.flag("valueArea", opacity);
  if (kind === "support") return palette.flag("entry", opacity);
  if (kind === "resistance") return palette.flag("watch", opacity);
  return palette.flag("invalidation", opacity);
}


function renderTaOverlay(
  svg: SVGSVGElement | null,
  container: HTMLDivElement,
  series: { priceToCoordinate(price: number): number | null },
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } },
  analysis: PositionChartAnalysis,
  plan: PositionActionPlan | null,
  layers: ChartLayerState,
  activeHarmonic: PositionChartAnalysis["harmonic_patterns"][number] | null,
  positionOverlay: PositionChartOverlay | null,
  highlightPrice: number | null,
  palette: ResolvedChartPalette
) {
  if (!svg) return;
  const width = container.clientWidth;
  const height = container.clientHeight;
  if (!width || !height) return;
  const zones: string[] = [];
  const shapes: string[] = [];
  const badges: string[] = [];
  const right = overlayRight(width);
  const context: OverlayContext = { series, chart, analysis, plan, layers, positionOverlay, highlightPrice, palette, width, height, right };

  if (layers.ta.includes("levels")) {
    zones.push(...structureZoneNodes(context));
  }
  if (layers.ta.includes("wyckoff")) {
    const wyckoff = wyckoffOverlayNodes(context);
    zones.push(...wyckoff.zones);
    shapes.push(...wyckoff.shapes);
    badges.push(...wyckoff.badges);
  }
  if (layers.ta.includes("volume_profile")) {
    shapes.push(...volumeProfileNodes(context));
  }
  if (layers.ta.includes("harmonic") && activeHarmonic) {
    const harmonic = harmonicPatternNodes(context, activeHarmonic);
    zones.push(...harmonic.zones);
    shapes.push(...harmonic.shapes);
    badges.push(...harmonic.badges);
  }
  if (layers.plan) {
    zones.push(...riskRewardBoxNodes(context));
    badges.push(...priceFlagNodes(context));
  }
  if (layers.scenario) {
    shapes.push(...scenarioPathNodes(context));
  }
  if (positionOverlay) {
    badges.push(...positionOverlayNodes(context, positionOverlay));
  }
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = [...zones, ...shapes, ...badges].join("");
}

type OverlayContext = {
  series: { priceToCoordinate(price: number): number | null };
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } };
  analysis: PositionChartAnalysis;
  plan: PositionActionPlan | null;
  layers: ChartLayerState;
  positionOverlay: PositionChartOverlay | null;
  highlightPrice: number | null;
  palette: ResolvedChartPalette;
  width: number;
  height: number;
  right: number;
};

type OverlayGroup = { zones: string[]; shapes: string[]; badges: string[] };

function overlayRight(width: number): number {
  return Math.max(24, width - AXIS_GUTTER);
}

type PriceFlag = {
  label: string;
  price: number;
  kind: "entry" | "mark" | "invalidation" | "takeProfit" | "watch" | "poc" | "valueArea";
  priority: number;
};

type NumericPlanItem = PositionActionPlanItem & { price: number };

function riskRewardBoxNodes(context: OverlayContext): string[] {
  const entry = context.positionOverlay?.entryPrice ?? context.analysis.entry_price;
  const invalidation = planInvalidation(context.plan, context.analysis);
  const target = firstTakeProfit(context.plan);
  if (!Number.isFinite(entry) || !invalidation || !target) return [];
  const entryY = context.series.priceToCoordinate(entry);
  const invalidationY = context.series.priceToCoordinate(invalidation.price);
  const targetY = context.series.priceToCoordinate(target.price);
  if (entryY === null || invalidationY === null || targetY === null) return [];
  const lastX = context.chart.timeScale().timeToCoordinate(context.analysis.candles.at(-1)?.time as Time);
  const x = Math.max(18, Math.min(context.right - 130, (lastX ?? context.right * 0.7) + 8));
  const width = Math.max(74, context.right - x);
  const riskY = Math.min(entryY, invalidationY);
  const riskHeight = Math.max(3, Math.abs(invalidationY - entryY));
  const profitY = Math.min(entryY, targetY);
  const profitHeight = Math.max(3, Math.abs(targetY - entryY));
  const riskPct = directionDistancePct(invalidation.price, entry, context.analysis.direction);
  const profitPct = directionDistancePct(target.price, entry, context.analysis.direction);
  const rr = riskPct < 0 && profitPct > 0 ? profitPct / Math.abs(riskPct) : null;
  const label = `${formatSignedPercent(profitPct)} / ${formatSignedPercent(riskPct)}${rr ? ` · R:R ${rr.toFixed(1)}` : ""}`;
  return [
    `<rect x="${x}" y="${profitY}" width="${width}" height="${profitHeight}" fill="${context.palette.zone("profit")}" />`,
    `<rect x="${x}" y="${riskY}" width="${width}" height="${riskHeight}" fill="${context.palette.zone("risk")}" />`,
    `<line x1="${x}" x2="${context.right}" y1="${entryY}" y2="${entryY}" stroke="${context.palette.flag("entry", 0.78)}" stroke-width="${chartTheme.stroke.major.width}" />`,
    labelBadge(context.right - 142, Math.max(18, profitY - 26), label, context.palette.color("panel", 0.82), context.palette.flag("takeProfit", 0.88), context.palette.color("text"), 136)
  ];
}

function priceFlagNodes(context: OverlayContext): string[] {
  const flags = stackFlags(actionPriceFlags(context), context.height, context.series);
  return flags.map((flag) => {
    const highlighted = context.highlightPrice !== null && Math.abs(flag.price - context.highlightPrice) <= Math.abs(flag.price) * 1e-9 + 1e-12;
    const width = Math.max(78, Math.min(158, flag.label.length * 8 + 18 + (highlighted ? 18 : 0)));
    const x = context.right + 5 - (highlighted ? 10 : 0);
    const fill = context.palette.flag(flag.kind, highlighted ? 1 : 0.92);
    const stroke = context.palette.color("text", highlighted ? 0.8 : 0.24);
    const text = flag.kind === "mark" ? context.palette.color("panel") : context.palette.color("panel");
    return [
      `<rect x="${x}" y="${flag.y - 12}" width="${width}" height="24" rx="5" fill="${fill}" stroke="${stroke}" stroke-width="${highlighted ? 2 : 1}" />`,
      `<text x="${x + 8}" y="${flag.y + 4}" fill="${text}" font-size="${highlighted ? 12 : 11}" font-weight="${highlighted ? 700 : 600}" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(flag.label)}</text>`
    ].join("");
  });
}

function structureZoneNodes(context: OverlayContext): string[] {
  const atr = averageTrueRange(context.analysis.candles);
  const support = context.analysis.price_levels.support.slice(0, 3).map((level, index) => levelZoneNode(context, level, index, "support", atr));
  const resistance = context.analysis.price_levels.resistance.slice(0, 3).map((level, index) => levelZoneNode(context, level, index, "resistance", atr));
  return [...support, ...resistance].flat();
}

function levelZoneNode(context: OverlayContext, level: ChartPriceLevel, index: number, kind: "support" | "resistance", atr: number): string[] {
  const band = Math.max(atr * 0.15, level.price * 0.001);
  const y1 = context.series.priceToCoordinate(level.price + band);
  const y2 = context.series.priceToCoordinate(level.price - band);
  if (y1 === null || y2 === null) return [];
  const touch = firstTouchCandle(context.analysis.candles, level.price, band, level.last_touch_at);
  const x1 = touch ? context.chart.timeScale().timeToCoordinate(touch.time as Time) : null;
  if (x1 === null) return [];
  const y = Math.min(y1, y2);
  const height = Math.max(4, Math.abs(y2 - y1));
  const label = `${kind === "support" ? "S" : "R"}${index + 1} · 터치${level.touches ?? "-"}`;
  const fill = context.palette.zone(kind, undefined, level.score);
  const stroke = context.palette.flag(kind === "support" ? "entry" : "invalidation", 0.46);
  return [
    `<rect x="${x1}" y="${y}" width="${Math.max(16, context.right - x1)}" height="${height}" rx="2" fill="${fill}" stroke="${stroke}" stroke-width="1" />`,
    labelBadge(x1 + 5, Math.max(16, y - 18), label, context.palette.color("panel", 0.72), stroke, context.palette.color("text"), 80)
  ];
}

function wyckoffOverlayNodes(context: OverlayContext): OverlayGroup {
  const range = context.analysis.wyckoff_range;
  if (!range) return { zones: [], shapes: [], badges: [] };
  const top = context.series.priceToCoordinate(range.resistance.price);
  const bottom = context.series.priceToCoordinate(range.support.price);
  const x1 = context.chart.timeScale().timeToCoordinate(range.start_time as Time);
  const x2 = context.chart.timeScale().timeToCoordinate(range.end_time as Time);
  if (top === null || bottom === null || x1 === null || x2 === null) return { zones: [], shapes: [], badges: [] };
  const x = Math.min(x1, x2);
  const width = Math.max(12, Math.abs(x2 - x1));
  const y = Math.min(top, bottom);
  const height = Math.max(8, Math.abs(bottom - top));
  const zones = [
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="3" fill="${context.palette.zone("range")}" stroke="${context.palette.color("neutral", 0.3)}" stroke-width="1" />`,
    `<line x1="${x}" x2="${x + width}" y1="${top}" y2="${top}" stroke="${context.palette.color("neutral", 0.58)}" stroke-width="${chartTheme.stroke.major.width}" />`,
    `<line x1="${x}" x2="${x + width}" y1="${bottom}" y2="${bottom}" stroke="${context.palette.color("neutral", 0.58)}" stroke-width="${chartTheme.stroke.major.width}" />`
  ];
  const phaseLabels = ["A", "B", "C", "D", "E"];
  const shapes = phaseLabels.flatMap((label, index) => {
    const phaseX = x + (width / phaseLabels.length) * index;
    const labelX = phaseX + width / phaseLabels.length / 2 - 5;
    return [
      index > 0 ? `<line x1="${phaseX}" x2="${phaseX}" y1="${y}" y2="${y + height}" stroke="${context.palette.color("neutral", 0.28)}" stroke-width="1" />` : "",
      `<text x="${labelX}" y="${Math.max(14, y - 8)}" fill="${context.palette.color("muted", 0.86)}" font-size="11" font-family="SF Mono, Monaco, Consolas, monospace">Phase ${label}</text>`
    ];
  });
  const events = splitWyckoffEvents(context.analysis.wyckoff_markers, context.analysis.wyckoff_markers_low_confidence).events;
  const badges = events.flatMap((marker, index) => wyckoffEventBadge(context, marker, { top, bottom, x, width }, index));
  badges.unshift(labelBadge(x + 8, Math.max(18, y + 10), phaseHintLabel(context.analysis.wyckoff_phase?.phase), context.palette.color("panel", 0.62), context.palette.color("neutral", 0.54), context.palette.color("text"), 132));
  return { zones, shapes, badges };
}

function wyckoffEventBadge(
  context: OverlayContext,
  marker: WyckoffMarker,
  range: { top: number; bottom: number; x: number; width: number },
  index: number
): string[] {
  const markerX = context.chart.timeScale().timeToCoordinate(marker.time as Time);
  const markerY = context.series.priceToCoordinate(marker.price);
  if (markerX === null || markerY === null) return [];
  const upper = marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow");
  const labelY = upper ? range.top - 28 - (index % 2) * 16 : range.bottom + 18 + (index % 2) * 16;
  const labelX = clamp(markerX - 34 + (index % 3) * 18, range.x, range.x + range.width - 92);
  const badgeText = `${upper ? "⤓" : "⤒"} ${eventShortLabel(marker)} · ${Math.round(marker.confidence)}`;
  const tone = upper ? "invalidation" : "entry";
  return [
    `<polyline points="${markerX},${markerY} ${markerX},${upper ? range.top : range.bottom} ${labelX + 10},${labelY}" fill="none" stroke="${context.palette.flag(tone, 0.58)}" stroke-width="1" />`,
    labelBadge(labelX, labelY - 12, badgeText, context.palette.color("panel", 0.82), context.palette.flag(tone, 0.86), context.palette.color("text"), 94)
  ];
}

function harmonicPatternNodes(context: OverlayContext, pattern: PositionChartAnalysis["harmonic_patterns"][number]): OverlayGroup {
  const coordinates = pattern.points
    .map((point) => {
      const x = context.chart.timeScale().timeToCoordinate(point.time as Time);
      const y = context.series.priceToCoordinate(point.price);
      return x === null || y === null ? null : { x, y, point };
    })
    .filter((item): item is { x: number; y: number; point: PositionChartAnalysis["harmonic_patterns"][number]["points"][number] } => item !== null);
  if (coordinates.length < 4) return { zones: [], shapes: [], badges: [] };
  const bearish = pattern.direction === "bearish";
  const stroke = context.palette.flag(bearish ? "invalidation" : "takeProfit", 0.86);
  const fill = context.palette.zone("prz", pattern.status === "forming" ? 0.06 : 0.12);
  const dash = pattern.status === "forming" ? `stroke-dasharray="6 6"` : "";
  const zones: string[] = [];
  const shapes: string[] = [];
  const badges: string[] = [];
  if (coordinates.length >= 3) zones.push(`<polygon points="${coordinates.slice(0, 3).map((item) => `${item.x},${item.y}`).join(" ")}" fill="${fill}" stroke="${stroke}" stroke-width="1" ${dash} />`);
  if (coordinates.length >= 5) zones.push(`<polygon points="${coordinates.slice(2, 5).map((item) => `${item.x},${item.y}`).join(" ")}" fill="${fill}" stroke="${stroke}" stroke-width="1" ${dash} />`);
  const przTop = context.series.priceToCoordinate(pattern.prz.high);
  const przBottom = context.series.priceToCoordinate(pattern.prz.low);
  if (przTop !== null && przBottom !== null) {
    const y = Math.min(przTop, przBottom);
    const height = Math.max(4, Math.abs(przBottom - przTop));
    const x = Math.min(coordinates.at(-2)?.x ?? coordinates[0].x, context.right);
    const width = Math.max(18, context.right - x);
    zones.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="3" fill="${context.palette.zone("prz", pattern.status === "forming" ? 0.06 : 0.18)}" stroke="${stroke}" stroke-width="1" ${dash} />`);
    badges.push(labelBadge(x + 6, Math.max(16, y - 24), `PRZ · 신뢰도 ${Math.round(pattern.confidence)}`, context.palette.color("panel", 0.82), stroke, context.palette.color("text"), 120));
  }
  shapes.push(`<polyline points="${coordinates.map((item) => `${item.x},${item.y}`).join(" ")}" fill="none" stroke="${stroke}" stroke-width="1.4" ${dash} />`);
  for (const item of coordinates) {
    shapes.push(`<circle cx="${item.x}" cy="${item.y}" r="3" fill="${stroke}" />`);
    badges.push(`<text x="${item.x + 5}" y="${item.y - 6}" fill="${context.palette.color("text", 0.82)}" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(item.point.label)}</text>`);
  }
  badges.push(...harmonicRatioLabels(context, pattern, coordinates));
  return { zones, shapes, badges };
}

function harmonicRatioLabels(
  context: OverlayContext,
  pattern: PositionChartAnalysis["harmonic_patterns"][number],
  coordinates: Array<{ x: number; y: number; point: PositionChartAnalysis["harmonic_patterns"][number]["points"][number] }>
): string[] {
  return [
    { from: 0, to: 1, value: pattern.ratios.xa, name: "XA" },
    { from: 1, to: 2, value: pattern.ratios.b_xa, name: "B" },
    { from: 2, to: 3, value: pattern.ratios.c_ab, name: "C" },
    { from: 3, to: 4, value: pattern.ratios.d_xa ?? pattern.ratios.cd_ab, name: "D" }
  ].flatMap((item) => {
    const from = coordinates[item.from];
    const to = coordinates[item.to];
    if (!from || !to || typeof item.value !== "number") return [];
    const x = (from.x + to.x) / 2;
    const y = (from.y + to.y) / 2;
    return [labelBadge(x - 26, y - 10, `${item.name} ${item.value.toFixed(3)}`, context.palette.color("panel", 0.68), context.palette.color("purple", 0.72), context.palette.color("text"), 64)];
  });
}

function volumeProfileNodes(context: OverlayContext): string[] {
  const bins = context.analysis.volume_profile.bins.filter((bin) => bin.volume > 0);
  const maxVolume = Math.max(...bins.map((bin) => bin.volume), 1);
  const profileWidth = Math.min(180, Math.max(92, context.width * 0.18));
  const valueHigh = context.series.priceToCoordinate(context.analysis.volume_profile.value_area_high);
  const valueLow = context.series.priceToCoordinate(context.analysis.volume_profile.value_area_low);
  const poc = context.series.priceToCoordinate(context.analysis.volume_profile.poc_price);
  const nodes: string[] = [];
  if (valueHigh !== null && valueLow !== null) {
    const y = Math.min(valueHigh, valueLow);
    const bandHeight = Math.max(2, Math.abs(valueLow - valueHigh));
    nodes.push(`<rect x="${context.right - profileWidth}" y="${y}" width="${profileWidth}" height="${bandHeight}" fill="${context.palette.zone("neutral", 0.07)}" />`);
  }
  for (const bin of bins) {
    const top = context.series.priceToCoordinate(bin.price_high);
    const bottom = context.series.priceToCoordinate(bin.price_low);
    if (top === null || bottom === null) continue;
    const y = Math.min(top, bottom);
    const rowHeight = Math.max(2, Math.abs(bottom - top) - 1);
    const barWidth = Math.max(2, (bin.volume / maxVolume) * profileWidth);
    const x = context.right - barWidth;
    if (bin.buy_volume !== undefined || bin.sell_volume !== undefined) {
      const buy = Math.max(0, bin.buy_volume ?? 0);
      const sell = Math.max(0, bin.sell_volume ?? 0);
      const total = Math.max(buy + sell, 1);
      const buyWidth = barWidth * (buy / total);
      const sellWidth = barWidth - buyWidth;
      nodes.push(`<rect x="${x}" y="${y}" width="${sellWidth}" height="${rowHeight}" fill="${context.palette.flag("invalidation", 0.28)}" />`);
      nodes.push(`<rect x="${x + sellWidth}" y="${y}" width="${buyWidth}" height="${rowHeight}" fill="${context.palette.flag("entry", 0.34)}" />`);
    } else {
      nodes.push(`<rect x="${x}" y="${y}" width="${barWidth}" height="${rowHeight}" fill="${context.palette.color("neutral", 0.2)}" />`);
    }
  }
  if (poc !== null) {
    nodes.push(`<line x1="${context.right - profileWidth}" x2="${context.right}" y1="${poc}" y2="${poc}" stroke="${context.palette.flag("poc", 0.72)}" stroke-width="${chartTheme.stroke.major.width}" />`);
  }
  return nodes;
}

function scenarioPathNodes(context: OverlayContext): string[] {
  const mark = context.analysis.mark_price;
  const target = firstTakeProfit(context.plan);
  const invalidation = planInvalidation(context.plan, context.analysis);
  if (!target || !invalidation) return [];
  const watchPrice = firstWatchPrice(context.plan?.watch_triggers) ?? midPrice(mark, target.price);
  const startY = context.series.priceToCoordinate(mark);
  const watchY = context.series.priceToCoordinate(watchPrice);
  const targetY = context.series.priceToCoordinate(target.price);
  const invalidationY = context.series.priceToCoordinate(invalidation.price);
  if (startY === null || watchY === null || targetY === null || invalidationY === null) return [];
  const x0 = Math.max(22, context.right - 280);
  const x1 = Math.max(22, context.right - 168);
  const x2 = Math.max(22, context.right - 48);
  const stroke = context.palette.color("neutral", 0.72);
  const dash = chartTheme.stroke.scenario.dash;
  return [
    labelBadge(x0, Math.max(18, Math.min(startY, watchY, targetY, invalidationY) - 30), "시나리오 · 예측 아님", context.palette.color("panel", 0.78), context.palette.color("neutral", 0.72), context.palette.color("text"), 132),
    `<polyline points="${x0},${startY} ${x1},${watchY} ${x2},${targetY}" fill="none" stroke="${context.palette.flag("takeProfit", 0.58)}" stroke-width="${chartTheme.stroke.scenario.width}" stroke-dasharray="${dash}" />`,
    `<polyline points="${x0},${startY} ${x1},${watchY} ${x2},${invalidationY}" fill="none" stroke="${stroke}" stroke-width="${chartTheme.stroke.scenario.width}" stroke-dasharray="${dash}" />`
  ];
}

function positionOverlayNodes(context: OverlayContext, position: PositionChartOverlay): string[] {
  const y = context.series.priceToCoordinate(position.entryPrice);
  if (y === null || y < 18 || y > context.height - 42) return [];
  const labelX = 118;
  const labelY = Math.max(28, Math.min(context.height - 58, y - 18));
  const sideLabel = position.direction === "long" ? "롱" : "숏";
  const pnlText = position.pnlAmount === null
    ? formatSignedPercent(position.pnlPercent)
    : `${formatSignedNumber(position.pnlAmount)} USDT (${formatSignedPercent(position.pnlPercent)})`;
  const pnlColor = context.palette.flag(position.pnlPercent >= 0 ? "takeProfit" : "invalidation");
  const quantityText = formatCompactQuantity(position.quantity);
  const tagWidth = Math.max(70, Math.min(128, quantityText.length * 11 + 30));
  const pnlWidth = Math.max(176, Math.min(310, pnlText.length * 9 + 34));
  const sideWidth = 82;
  const totalWidth = sideWidth + tagWidth + pnlWidth;
  const nodes = [
    `<rect x="${labelX}" y="${labelY}" width="${totalWidth}" height="34" rx="6" fill="${context.palette.color("panel", 0.72)}" stroke="${context.palette.flag("entry", 0.9)}" stroke-width="1.2" />`,
    `<rect x="${labelX}" y="${labelY}" width="${sideWidth}" height="34" rx="6" fill="${context.palette.flag("entry", 0.92)}" />`,
    `<text x="${labelX + 12}" y="${labelY + 22}" fill="${context.palette.color("panel")}" font-size="12" font-weight="700" font-family="SF Mono, Monaco, Consolas, monospace">${sideLabel} ${position.leverage}x</text>`,
    `<text x="${labelX + sideWidth + 14}" y="${labelY + 22}" fill="${context.palette.color("text", 0.92)}" font-size="13" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(quantityText)}</text>`,
    `<text x="${labelX + sideWidth + tagWidth + 14}" y="${labelY + 22}" fill="${pnlColor}" font-size="13" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(pnlText)}</text>`,
    `<text x="${Math.min(context.right - 88, labelX + totalWidth + 12)}" y="${labelY + 22}" fill="${context.palette.color("text", 0.76)}" font-size="11" font-family="SF Mono, Monaco, Consolas, monospace">진입 ${escapeSvgText(formatPrice(position.entryPrice))}</text>`
  ];
  const openedTime = position.openedAt ? Math.floor(new Date(position.openedAt).getTime() / 1000) : null;
  const entryCandle = openedTime ? nearestCandleAtOrAfter(context.analysis.candles, openedTime) : null;
  if (entryCandle) {
    const x = context.chart.timeScale().timeToCoordinate(entryCandle.time as Time);
    if (x !== null && x > 0 && x < context.right) {
      nodes.push(`<line x1="${x}" x2="${x}" y1="${Math.max(0, y - 52)}" y2="${Math.min(context.height, y + 52)}" stroke="${context.palette.flag("entry", 0.65)}" stroke-width="1" stroke-dasharray="${chartTheme.stroke.minor.dash}" />`);
      nodes.push(`<path d="M ${x - 7} ${y - 11} L ${x + 7} ${y - 11} L ${x} ${y - 1} Z" fill="${context.palette.flag("entry", 0.92)}" />`);
      nodes.push(`<text x="${x + 8}" y="${Math.max(16, y - 14)}" fill="${context.palette.flag("entry", 0.92)}" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">진입</text>`);
    }
  }
  return nodes;
}

function actionPriceFlags(context: OverlayContext): PriceFlag[] {
  const flags: PriceFlag[] = [
    { label: `진입 ${formatPrice(context.analysis.entry_price)}`, price: context.analysis.entry_price, kind: "entry", priority: 0 },
    { label: `현재 ${formatPrice(context.analysis.mark_price)}`, price: context.analysis.mark_price, kind: "mark", priority: 1 }
  ];
  const invalidation = planInvalidation(context.plan, context.analysis);
  if (invalidation) {
    flags.push({ label: `무효화 ${formatPrice(invalidation.price)}`, price: invalidation.price, kind: "invalidation", priority: 2 });
  }
  firstTwoTakeProfits(context.plan).forEach((target, index) => {
    flags.push({ label: `익절${index + 1} ${formatPrice(target.price)}`, price: target.price, kind: "takeProfit", priority: 3 + index });
  });
  const watchPrice = firstWatchPrice(context.plan?.watch_triggers);
  if (watchPrice !== null) {
    flags.push({ label: `감시 ${formatPrice(watchPrice)}`, price: watchPrice, kind: "watch", priority: 6 });
  }
  if (context.layers.ta.includes("volume_profile")) {
    flags.push({ label: `POC ${formatPrice(context.analysis.volume_profile.poc_price)}`, price: context.analysis.volume_profile.poc_price, kind: "poc", priority: 7 });
  }
  return flags.filter((flag) => Number.isFinite(flag.price));
}

function stackFlags(flags: PriceFlag[], height: number, series?: OverlayContext["series"]): Array<PriceFlag & { y: number }> {
  const positioned = flags
    .map((flag) => ({ ...flag, y: series?.priceToCoordinate(flag.price) ?? null }))
    .filter((flag): flag is PriceFlag & { y: number } => flag.y !== null)
    .sort((left, right) => left.y - right.y || left.priority - right.priority);
  const gap = 26;
  for (let index = 1; index < positioned.length; index += 1) {
    if (positioned[index].y - positioned[index - 1].y < gap) {
      positioned[index].y = positioned[index - 1].y + gap;
    }
  }
  for (let index = positioned.length - 1; index >= 0; index -= 1) {
    positioned[index].y = clamp(positioned[index].y, 14, height - 16);
    if (index < positioned.length - 1 && positioned[index + 1].y - positioned[index].y < gap) {
      positioned[index].y = Math.max(14, positioned[index + 1].y - gap);
    }
  }
  return positioned.sort((left, right) => left.priority - right.priority);
}

function planInvalidation(plan: PositionActionPlan | null, analysis: PositionChartAnalysis): NumericPlanItem | null {
  const fromPlan = numericPlanItem(plan?.invalidation) ?? numericPlanItem(plan?.engine_invalidation);
  if (fromPlan) return fromPlan;
  const fromLevel = analysis.price_levels.invalidation.find((level) => typeof level.price === "number");
  return fromLevel?.price ? { price: fromLevel.price, basis: fromLevel.label, distance_pct: null, action: "이탈 시 손절 검토" } : null;
}

function firstTakeProfit(plan: PositionActionPlan | null): NumericPlanItem | null {
  return firstTwoTakeProfits(plan)[0] ?? null;
}

function firstTwoTakeProfits(plan: PositionActionPlan | null): NumericPlanItem[] {
  return (plan?.take_profit ?? []).map(numericPlanItem).filter((item): item is NumericPlanItem => item !== null).slice(0, 2);
}

function numericPlanItem(item: PositionActionPlanItem | null | undefined): NumericPlanItem | null {
  return typeof item?.price === "number" && Number.isFinite(item.price) ? item as NumericPlanItem : null;
}

function firstWatchPrice(triggers: PositionWatchTrigger[] | undefined): number | null {
  for (const trigger of triggers ?? []) {
    const match = trigger.condition.match(/-?\d+(?:\.\d+)?/);
    if (!match) continue;
    const value = Number(match[0]);
    if (Number.isFinite(value) && value > 0) return value;
  }
  return null;
}

function directionDistancePct(price: number, entry: number, direction: "long" | "short"): number {
  if (!Number.isFinite(price) || !Number.isFinite(entry) || entry === 0) return 0;
  const raw = ((price - entry) / entry) * 100;
  return direction === "long" ? raw : -raw;
}

function midPrice(left: number, right: number): number {
  return (left + right) / 2;
}

function averageTrueRange(candles: ChartCandle[], period = 14): number {
  const sample = candles.slice(Math.max(1, candles.length - period));
  if (!sample.length) return Math.max(candles.at(-1)?.close ?? 0, 1) * 0.01;
  const ranges = sample.map((candle, index) => {
    const previous = candles[candles.length - sample.length + index - 1]?.close ?? candle.open;
    return Math.max(candle.high - candle.low, Math.abs(candle.high - previous), Math.abs(candle.low - previous));
  });
  return average(ranges);
}

function firstTouchCandle(candles: ChartCandle[], price: number, band: number, fallbackIso?: string | null): ChartCandle | null {
  const direct = candles.find((candle) => candle.low <= price + band && candle.high >= price - band);
  if (direct) return direct;
  if (!fallbackIso) return candles.at(0) ?? null;
  const fallbackTime = Math.floor(new Date(fallbackIso).getTime() / 1000);
  return nearestCandleAtOrAfter(candles, fallbackTime);
}

function labelBadge(x: number, y: number, text: string, fill: string, stroke: string, textColor: string, width?: number): string {
  const safeText = escapeSvgText(text);
  const rectWidth = width ?? Math.max(54, Math.min(190, safeText.length * 7.2 + 16));
  return [
    `<rect x="${x}" y="${y}" width="${rectWidth}" height="22" rx="5" fill="${fill}" stroke="${stroke}" stroke-width="1" />`,
    `<text x="${x + 8}" y="${y + 15}" fill="${textColor}" font-size="10.5" font-family="SF Mono, Monaco, Consolas, monospace">${safeText}</text>`
  ].join("");
}

function eventShortLabel(marker: WyckoffMarker): string {
  const normalized = localizeMarketCodes(marker.label || marker.type);
  const glossary = taShortLabel(marker.label || marker.type);
  return (glossary || normalized)
    .replace("스프링 후보", "스프링")
    .replace("거래량 급증", "급증")
    .replace("클라이맥스 후보", "클라이맥스");
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function formatKoreanDateTime(time: number): string {
  const date = new Date(time * 1000);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${year}.${month}.${day} ${hour}:${minute}`;
}

function formatCompactNumber(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(2)}K`;
  return value.toFixed(2);
}

function resetHoverReadout(element: HTMLDivElement): void {
  element.classList.remove("is-active");
  element.innerHTML = `
    <strong>캔들 정보</strong>
    <span>시가·고가·저가·종가·거래량</span>
  `;
}

function harmonicPatternTitle(pattern: PositionChartAnalysis["harmonic_patterns"][number]): string {
  return [
    `${pattern.label} · ${pattern.status === "forming" ? "형성 중" : "완성"} · 신뢰도 ${pattern.confidence}`,
    `비율 적합 ${pattern.components.ratio_fit}`,
    `합류 ${pattern.components.confluence}`,
    `ATR 유의성 ${pattern.components.atr_significance}`,
    `PRZ ${formatPrice(pattern.prz.low)} - ${formatPrice(pattern.prz.high)}`,
    pattern.basis
  ].join("\n");
}

function escapeSvgText(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
