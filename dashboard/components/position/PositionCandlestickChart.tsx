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
  type LogicalRange,
  type Time
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { api, type UnifiedLiquidationHeatmap, type
  ChartCandle,
  ChartPriceLevel,
  CompactChartGauges,
  LiquidityPool,
  LiquiditySweep,
  PositionActionPlan,
  PositionActionPlanItem,
  PositionChartAnalysis,
  PositionWatchTrigger,
  OnchainChartMarker,
  WyckoffMarker
} from "@/lib/api";
import {
  activeFocusLayers,
  CHART_LAYER_DEFS,
  MINIMAL_FIXED_LAYER_STATE,
  layerActive,
  type ChartLayerId,
  type ChartLayerState,
  type MinimalChartEvidence,
  type TaFocusLayer
} from "@/lib/chartLayers";
import { chartTheme, createChartPalette, type ResolvedChartPalette } from "@/lib/chartTheme";
import type { Density } from "@/lib/density";
import { buildEmaRibbon, type EmaRibbonResult, type EmaRibbonState } from "@/lib/emaRibbon";
import { formatPrice } from "@/lib/format";
import { localizeMarketCodes, phaseHintLabel, sourceLabel, timeframeLabel } from "@/lib/labels/marketStateLabels";
import { splitWyckoffEvents, taGlossaryEntry, taShortLabel } from "@/lib/labels/taGlossary";
import { useSecondaryTaRows, visibleTaRows } from "@/lib/taDisplayPreferences";
import { priceLinesForAnalysis, type ChartPriceLine } from "./PriceLevelOverlay";
import type { PositionChartOverlay } from "./PositionChart";
import { VolumePanel } from "./VolumePanel";

const LABEL_MERGE_PX = 8;
const AXIS_GUTTER = 82;
type HeatmapFilters = {
  side: "all" | "long" | "short";
  size: "all" | "q2_plus" | "q3_plus" | "q4" | "10x" | "25x" | "50x" | "100x";
  range: "12H" | "24H" | "3D" | "1W" | "1M";
  mode: "persist" | "event";
};

const DEFAULT_HEATMAP_FILTERS: HeatmapFilters = { side: "all", size: "all", range: "3D", mode: "persist" };
const HEATMAP_FILTERS_KEY = "fce.unifiedHeatmap.filters.v1";
const HEATMAP_OPACITY_KEY = "fce.unifiedHeatmap.opacity.v1";

export function PositionCandlestickChart({
  analysis,
  trendSummary,
  plan,
  layers,
  onToggleLayer,
  highlightPrice = null,
  positionOverlay = null,
  density = "simple",
  intentZoneSelector,
  layerMode = "pro",
  minimalEvidence = null,
  compressed = false,
  gauges = null
}: {
  analysis: PositionChartAnalysis;
  trendSummary: string;
  plan: PositionActionPlan | null;
  layers: ChartLayerState;
  onToggleLayer: (id: ChartLayerId, additive: boolean) => void;
  highlightPrice?: number | null;
  positionOverlay?: PositionChartOverlay | null;
  density?: Density;
  layerMode?: "minimal" | "pro";
  minimalEvidence?: MinimalChartEvidence | null;
  compressed?: boolean;
  gauges?: CompactChartGauges | null;
  intentZoneSelector?: {
    enabled: boolean;
    draft: { lower: number | null; upper: number | null };
    onDraftChange: (lower: number, upper: number) => void;
    onComplete: (lower: number, upper: number) => void;
  };
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const heatmapCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const priceAtYRef = useRef<((y: number) => number | null) | null>(null);
  const overlayDrawRef = useRef<(() => void) | null>(null);
  const heatmapRangeApplyRef = useRef<((range: HeatmapFilters["range"]) => void) | null>(null);
  const heatmapFingerprintRef = useRef("");
  const viewportRef = useRef<{ key: string; locked: boolean; range: LogicalRange | null }>({ key: "", locked: false, range: null });
  const [harmonicIndex, setHarmonicIndex] = useState(0);
  const [guideOpen, setGuideOpen] = useState(false);
  const [stanceStripOpen, setStanceStripOpen] = useState(false);
  const [dragStartPrice, setDragStartPrice] = useState<number | null>(null);
  const [dragStartY, setDragStartY] = useState<number | null>(null);
  const [zoneBand, setZoneBand] = useState<{ top: number; bottom: number } | null>(null);
  const [heatmap, setHeatmap] = useState<UnifiedLiquidationHeatmap | null>(null);
  const [heatmapError, setHeatmapError] = useState("");
  const [heatmapFilters, setHeatmapFilters] = useState<HeatmapFilters>(loadHeatmapFilters);
  const [heatmapOpacity, setHeatmapOpacity] = useState(loadHeatmapOpacity);
  const [heatmapHighlightPrice, setHeatmapHighlightPrice] = useState<number | null>(null);
  const showSecondaryTaRows = useSecondaryTaRows();
  const validation = useMemo(() => validateCandles(analysis.candles), [analysis.candles]);
  const visibleCandles = useMemo(
    () => (validation.valid && layerMode === "minimal" ? validation.candles.slice(-72) : validation.candles),
    [layerMode, validation]
  );
  const effectiveLayers = useMemo(
    () => (layerMode === "minimal" ? (compressed ? MINIMAL_FIXED_LAYER_STATE : minimalLayersForEvidence(minimalEvidence)) : layers),
    [compressed, layerMode, minimalEvidence, layers]
  );
  const realizedHeatmapActive = layerMode === "pro" && effectiveLayers.ta.includes("liquidation_realized");
  const seriesLayerState = useMemo(
    () => ({
      flow: effectiveLayers.flow,
      indicators: effectiveLayers.ta.includes("indicators"),
      ema: effectiveLayers.ta.includes("ema")
    }),
    [effectiveLayers.flow, effectiveLayers.ta]
  );
  const priceLines = useMemo(
    () => (validation.valid ? priceLinesForAnalysis(analysis) : []),
    [analysis, validation.valid]
  );
  const lastCandle = validation.candles.at(-1);
  const averageVolume = validation.valid ? average(visibleCandles.map((candle) => candle.volume)) : 0;
  const emaRibbon = useMemo(() => buildEmaRibbon(visibleCandles), [visibleCandles]);
  const wyckoffFocused = layerMode === "pro" && effectiveLayers.ta.includes("wyckoff");
  const harmonicFocused = layerMode === "pro" && effectiveLayers.ta.includes("harmonic");
  const harmonicPatterns = useMemo(
    () => [...analysis.harmonic_patterns].sort((left, right) => right.confidence - left.confidence),
    [analysis.harmonic_patterns]
  );
  const activeHarmonic = harmonicPatterns.length ? harmonicPatterns[((harmonicIndex % harmonicPatterns.length) + harmonicPatterns.length) % harmonicPatterns.length] : null;
  const guideLayer = activeGuideLayer(effectiveLayers);
  const intentZoneDraft = intentZoneSelector?.draft ?? null;
  const viewportKey = `${analysis.position_id}:${analysis.timeframe}`;
  const overlayInputsRef = useRef({
    analysis,
    plan,
    layers: effectiveLayers,
    activeHarmonic,
    positionOverlay,
    highlightPrice,
    density,
    layerMode,
    minimalEvidence,
    compressed,
    gauges,
    heatmap,
    heatmapOpacity,
    heatmapHighlightPrice
  });

  useEffect(() => {
    overlayInputsRef.current = {
      analysis,
      plan,
      layers: effectiveLayers,
      activeHarmonic,
      positionOverlay,
      highlightPrice,
      density,
      layerMode,
      minimalEvidence,
      compressed,
      gauges,
      heatmap,
      heatmapOpacity,
      heatmapHighlightPrice
    };
    overlayDrawRef.current?.();
  }, [activeHarmonic, analysis, compressed, density, effectiveLayers, gauges, heatmap, heatmapHighlightPrice, heatmapOpacity, highlightPrice, layerMode, minimalEvidence, plan, positionOverlay]);

  useEffect(() => {
    try {
      window.localStorage.setItem(HEATMAP_FILTERS_KEY, JSON.stringify(heatmapFilters));
      window.localStorage.setItem(HEATMAP_OPACITY_KEY, String(heatmapOpacity));
    } catch {
      // localStorage 비활성 환경에서는 세션 상태로만 동작
    }
  }, [heatmapFilters, heatmapOpacity]);

  useEffect(() => {
    if (!realizedHeatmapActive) return;
    let active = true;
    const load = async () => {
      try {
        const payload = await api.unifiedLiquidationHeatmap(analysis.symbol, analysis.timeframe, heatmapFilters);
        if (!active) return;
        const lastBucketTotal = payload.grid.at(-1)?.reduce((sum, value) => sum + value, 0) ?? 0;
        const fingerprint = `${payload.source}:${payload.last_event_ts}:${payload.n_events}:${payload.max_value_usd_estimated}:${lastBucketTotal}:${JSON.stringify(payload.filters)}`;
        if (fingerprint !== heatmapFingerprintRef.current) {
          heatmapFingerprintRef.current = fingerprint;
          setHeatmap(payload);
        }
        setHeatmapError("");
      } catch (error) {
        if (!active) return;
        setHeatmapError(error instanceof Error ? error.message : "실현 청산 그리드를 불러오지 못했습니다.");
      }
    };
    void load();
    const timer = window.setInterval(load, 5_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [analysis.symbol, analysis.timeframe, heatmapFilters, realizedHeatmapActive]);

  useEffect(() => {
    if (!realizedHeatmapActive) return;
    heatmapRangeApplyRef.current?.(heatmapFilters.range);
  }, [heatmapFilters.range, realizedHeatmapActive]);

  useEffect(() => {
    if (!heatmap?.filters.leverage_available || !["q2_plus", "q3_plus", "q4"].includes(heatmapFilters.size)) return;
    setHeatmapFilters((current) => ({ ...current, size: "all" }));
  }, [heatmap?.filters.leverage_available, heatmapFilters.size]);

  useEffect(() => {
    if (intentZoneDraft?.lower === null && intentZoneDraft?.upper === null) {
      setZoneBand(null);
    }
  }, [intentZoneDraft?.lower, intentZoneDraft?.upper]);

  useEffect(() => {
    setHarmonicIndex(0);
  }, [analysis.position_id, analysis.timeframe]);

  useEffect(() => {
    viewportRef.current = { key: viewportKey, locked: false, range: null };
  }, [viewportKey]);

  useEffect(() => {
    if (layerMode === "minimal") {
      setGuideOpen(false);
    }
  }, [layerMode]);

  function toggleGuide() {
    if (layerMode === "minimal") return;
    setGuideOpen((current) => !current);
  }

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
      wickUpColor: palette.color("green", 0.94),
      wickDownColor: palette.color("red", 0.94),
      borderUpColor: palette.color("green", 0.96),
      borderDownColor: palette.color("red", 0.96),
      borderVisible: true,
      priceLineVisible: false,
      lastValueVisible: false
    });
    priceAtYRef.current = (y: number) => {
      const price = candleSeries.coordinateToPrice(y);
      return typeof price === "number" && Number.isFinite(price) ? price : null;
    };

    const candleData: CandlestickData[] = visibleCandles.map((candle) => ({
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

    const volumeData: HistogramData[] = visibleCandles.map((candle) => ({
      time: candle.time as Time,
      value: candle.volume,
      color: seriesLayerState.flow ? volumeColorForCandle(analysis, candle, palette) : simpleVolumeColor(candle, palette)
    }));
    volumeSeries.setData(volumeData);

    if (seriesLayerState.flow) {
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
        visibleCandles.map((candle) => ({
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
      const oiPoints = derivativeSeriesPoints(analysis, "open_interest");
      if (oiPoints.length) {
        const oiSeries = chart.addSeries(LineSeries, {
          priceScaleId: "oi",
          color: palette.color("purple", 0.78),
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        oiSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.64, bottom: 0.22 }
        });
        oiSeries.setData(oiPoints);
      }
      const fundingPoints = derivativeFundingPoints(analysis, palette);
      if (fundingPoints.length) {
        const fundingSeries = chart.addSeries(HistogramSeries, {
          priceScaleId: "funding",
          priceFormat: { type: "price", precision: 4, minMove: 0.0001 },
          priceLineVisible: false,
          lastValueVisible: false
        });
        fundingSeries.priceScale().applyOptions({
          scaleMargins: { top: 0.88, bottom: 0.02 }
        });
        fundingSeries.setData(fundingPoints);
      }
    }

    if (seriesLayerState.indicators) {
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

    if (seriesLayerState.ema && emaRibbon) {
      for (const [index, ribbon] of emaRibbon.series.entries()) {
        const emaSeries = chart.addSeries(LineSeries, {
          color: emaRibbonColor(emaRibbon.state, index, palette),
          lineWidth: index === 0 ? 2 : 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false
        });
        emaSeries.setData(
          ribbon.points.map((point) => ({
            time: point.time as Time,
            value: point.value,
            color: emaRibbonColor(point.state, index, palette)
          }))
        );
      }
    }

    const labeled = mergeLineLabels(priceLines, container.clientHeight, visibleCandles);
    labeled.forEach((line) => {
      candleSeries.createPriceLine({
        price: line.price,
        color: chartLineColor(line.kind, palette, line.opacity),
        lineWidth: line.lineWidth,
        lineStyle: line.kind === "mark" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: false,
        title: line.title
      });
    });

    const spikeMarkers = seriesLayerState.flow
      ? visibleCandles
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
      const heatmapReadout = heatmapCellAt(
        overlayInputsRef.current.heatmap,
        Number(data.time),
        candleSeries.coordinateToPrice(param.point.y)
      );
      tooltip.innerHTML = `
        <strong>${formatKoreanDateTime(Number(data.time))}</strong>
        <span>시가 ${formatPrice(data.open)}</span>
        <span>고가 ${formatPrice(data.high)}</span>
        <span>저가 ${formatPrice(data.low)}</span>
        <span>종가 ${formatPrice(data.close)}</span>
        <span>거래량 ${formatCompactNumber(volumeAtTime(visibleCandles, Number(data.time)))}</span>
        ${heatmapReadout ? `<span class="heatmapRawValue">실현 청산 원본 버킷 ${formatUsd(heatmapReadout.value)} · ${heatmapReadout.events}건</span>` : ""}
      `;
    });

    const applyHeatmapRange = (range: HeatmapFilters["range"]) => {
      const hours = range === "12H" ? 12 : range === "24H" ? 24 : range === "3D" ? 72 : range === "1W" ? 168 : 720;
      const candleSeconds = visibleCandles.length > 1
        ? Math.max(60, visibleCandles.at(-1)!.time - visibleCandles.at(-2)!.time)
        : 14_400;
      const bars = Math.max(12, Math.ceil(hours * 3600 / candleSeconds));
      const last = visibleCandles.length - 1;
      const rangeValue = { from: Math.max(-0.5, last - bars + 0.5), to: last + 4.5 } as LogicalRange;
      chart.timeScale().setVisibleLogicalRange(rangeValue);
      viewportRef.current = { key: viewportKey, locked: false, range: rangeValue };
      window.setTimeout(() => overlayDrawRef.current?.(), 0);
    };
    heatmapRangeApplyRef.current = applyHeatmapRange;

    const storedViewport = viewportRef.current;
    if (storedViewport.key === viewportKey && storedViewport.locked && storedViewport.range) {
      chart.timeScale().setVisibleLogicalRange(storedViewport.range);
    } else if (overlayInputsRef.current.layers.ta.includes("liquidation_realized")) {
      applyHeatmapRange(heatmapFilters.range);
    } else {
      chart.timeScale().fitContent();
      chart.timeScale().applyOptions({ rightOffset: 18 });
    }

    const markViewportLocked = () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      viewportRef.current = {
        key: viewportKey,
        locked: true,
        range
      };
    };
    container.addEventListener("wheel", markViewportLocked, { passive: true });
    container.addEventListener("pointerdown", markViewportLocked);
    container.addEventListener("touchstart", markViewportLocked, { passive: true });

    const drawOverlay = () => {
      const current = overlayInputsRef.current;
      renderUnifiedHeatmap(
        heatmapCanvasRef.current,
        container,
        candleSeries,
        chart,
        current.heatmap,
        current.layers.ta.includes("liquidation_realized") ? current.heatmapOpacity : 0,
        current.heatmapHighlightPrice
      );
      renderTaOverlay(
        overlay,
        container,
        candleSeries,
        chart,
        current.analysis,
        current.plan,
        current.layers,
        current.activeHarmonic,
        current.positionOverlay,
        current.highlightPrice,
        palette,
        current.density,
        current.layerMode === "minimal" ? current.minimalEvidence : null,
        current.compressed,
        current.gauges
      );
    };
    overlayDrawRef.current = drawOverlay;
    const initialDrawTimer = window.setTimeout(drawOverlay, 0);
    const handleVisibleRangeChange = (range: LogicalRange | null) => {
      const current = viewportRef.current;
      if (current.key === viewportKey && current.locked) {
        viewportRef.current = { ...current, range };
      }
      drawOverlay();
    };
    chart.timeScale().subscribeVisibleLogicalRangeChange(handleVisibleRangeChange);
    const resizeObserver = new ResizeObserver(drawOverlay);
    resizeObserver.observe(container);
    return () => {
      window.clearTimeout(initialDrawTimer);
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(handleVisibleRangeChange);
      container.removeEventListener("wheel", markViewportLocked);
      container.removeEventListener("pointerdown", markViewportLocked);
      container.removeEventListener("touchstart", markViewportLocked);
      priceAtYRef.current = null;
      if (overlayDrawRef.current === drawOverlay) overlayDrawRef.current = null;
      if (heatmapRangeApplyRef.current === applyHeatmapRange) heatmapRangeApplyRef.current = null;
      chart.remove();
    };
  }, [analysis, averageVolume, emaRibbon, heatmapFilters.range, priceLines, seriesLayerState, validation, visibleCandles, layerMode, viewportKey]);

  function pointerY(event: PointerEvent<HTMLDivElement>): number {
    const rect = event.currentTarget.getBoundingClientRect();
    return event.clientY - rect.top;
  }

  function priceFromPointer(event: PointerEvent<HTMLDivElement>): number | null {
    const y = pointerY(event);
    return priceAtYRef.current?.(y) ?? null;
  }

  function handleZonePointerDown(event: PointerEvent<HTMLDivElement>) {
    if (!intentZoneSelector?.enabled) return;
    const price = priceFromPointer(event);
    if (price === null) return;
    const y = pointerY(event);
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragStartPrice(price);
    setDragStartY(y);
    setZoneBand({ top: y, bottom: y });
    intentZoneSelector.onDraftChange(price, price);
  }

  function handleZonePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!intentZoneSelector?.enabled || dragStartPrice === null || dragStartY === null) return;
    const price = priceFromPointer(event);
    if (price === null) return;
    const y = pointerY(event);
    setZoneBand({ top: Math.min(dragStartY, y), bottom: Math.max(dragStartY, y) });
    intentZoneSelector.onDraftChange(Math.min(dragStartPrice, price), Math.max(dragStartPrice, price));
  }

  function handleZonePointerUp(event: PointerEvent<HTMLDivElement>) {
    if (!intentZoneSelector?.enabled || dragStartPrice === null) return;
    const price = priceFromPointer(event);
    const y = pointerY(event);
    if (dragStartY !== null) {
      setZoneBand({ top: Math.min(dragStartY, y), bottom: Math.max(dragStartY, y) });
    }
    setDragStartPrice(null);
    setDragStartY(null);
    if (price === null) return;
    const lower = Math.min(dragStartPrice, price);
    const upper = Math.max(dragStartPrice, price);
    if (Math.abs(upper - lower) <= Math.max(Math.abs(upper) * 0.0001, 1e-12)) return;
    intentZoneSelector.onComplete(lower, upper);
  }

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
          <p>
            {timeframeLabel(analysis.timeframe)} · {sourceLabel(analysis.data_quality.source)}
            {layerMode === "pro" ? ` · ${assetClassLabel(analysis.asset_class)}` : ""}
            {layerMode === "pro" && analysis.session?.label ? ` · ${analysis.session.label}` : ""}
            {layerMode === "pro" && typeof analysis.data_quality.session_excluded_candles === "number" && analysis.data_quality.session_excluded_candles > 0
              ? ` · 휴장 제외 ${analysis.data_quality.session_excluded_candles}개`
              : ""}
            {" · "}마지막 캔들: {lastCandle ? formatKoreanDateTime(lastCandle.time) : "-"}
          </p>
        </div>
        <div className="positionChartHeaderActions">
          {compressed && gauges ? (
            <StanceHeaderHud
              analysis={analysis}
              gauges={gauges}
              open={stanceStripOpen}
              onToggle={() => setStanceStripOpen((value) => !value)}
              showSecondaryTaRows={showSecondaryTaRows}
            />
          ) : <span className="positionChartTrendPill">{trendSummary}</span>}
          {layerMode === "pro" ? (
            <button className={`chartGuideButton ${guideOpen ? "active" : ""}`} onClick={toggleGuide} type="button" aria-pressed={guideOpen} title="해설 오버레이 켜기/끄기">
              {guideOpen ? "해설 켜짐" : "해설"}
            </button>
          ) : null}
        </div>
      </div>
      {layerMode === "minimal" && !compressed ? (
        <div className="minimalChartVerdict" data-testid="minimal-chart-verdict">
          <span>현재 판정</span>
          <strong>{trendSummary}</strong>
          <small>{minimalEvidence?.label || "선택 근거를 차트에 표시합니다."}</small>
        </div>
      ) : null}
      {layerMode === "pro" ? (
        <ChartLayerControls
          layers={layers}
          onToggleLayer={onToggleLayer}
        />
      ) : null}
      {realizedHeatmapActive ? (
        <UnifiedHeatmapControls
          filters={heatmapFilters}
          heatmap={heatmap}
          error={heatmapError}
          opacity={heatmapOpacity}
          onFilterChange={(key, value) => setHeatmapFilters((current) => ({ ...current, [key]: value }))}
          onOpacityChange={setHeatmapOpacity}
          onZoneFocus={setHeatmapHighlightPrice}
        />
      ) : null}
      {wyckoffFocused ? <WyckoffLayerStatus analysis={analysis} /> : null}
      {harmonicFocused ? (
        <div className={`taLayerStatus ${activeHarmonic ? "confirmed" : "inactive"}`} data-testid="harmonic-layer-status">
          {activeHarmonic ? (
            <>
              <strong>하모닉 확인</strong>
              <span title={harmonicPatternTitle(activeHarmonic)}>{activeHarmonic.label} · {activeHarmonic.direction === "bearish" ? "하락 반전 PRZ" : "상승 반전 PRZ"}</span>
              <small>신뢰도 {Math.round(activeHarmonic.confidence)} · {activeHarmonic.status === "forming" ? "형성 중" : "완성"}</small>
              {harmonicPatterns.length > 1 ? (
                <div className="taLayerStatusPager">
                  <button onClick={() => setHarmonicIndex((value) => value - 1)} type="button" aria-label="이전 패턴">◀</button>
                  <b>{(((harmonicIndex % harmonicPatterns.length) + harmonicPatterns.length) % harmonicPatterns.length) + 1}/{harmonicPatterns.length}</b>
                  <button onClick={() => setHarmonicIndex((value) => value + 1)} type="button" aria-label="다음 패턴">▶</button>
                </div>
              ) : null}
            </>
          ) : (
            <>
              <strong>하모닉 미검출</strong>
              <span>유효 X-A-B-C-D 비율 없음</span>
              <small>PRZ 없음 · 방향 판정에 사용하지 않음</small>
            </>
          )}
        </div>
      ) : null}
      <div className="chartHoverReadout" ref={tooltipRef}>
        <strong>캔들 정보</strong>
        <span>시가·고가·저가·종가·거래량</span>
      </div>
      <div className={`positionChartCanvasFrame ${guideOpen ? "showOverlayGuides" : ""}`} data-testid="chart-canvas-frame">
        <div className="positionChartCanvas" data-testid="chart-canvas" ref={containerRef} />
        <canvas className="unifiedHeatmapCanvas" data-testid="unified-heatmap-canvas" ref={heatmapCanvasRef} />
        <svg className="volumeProfileOverlay" data-testid="chart-overlay" ref={overlayRef} />
        {realizedHeatmapActive && heatmap ? <HeatmapLegend heatmap={heatmap} /> : null}
        {seriesLayerState.ema && emaRibbon ? <EmaRibbonHud ribbon={emaRibbon} /> : null}
        {zoneBand ? (
          <div
            className="chartIntentZoneBand"
            style={{
              top: `${zoneBand.top}px`,
              height: `${Math.max(2, zoneBand.bottom - zoneBand.top)}px`
            }}
          />
        ) : null}
        {intentZoneSelector?.enabled ? (
          <div
            className="chartIntentZonePicker"
            data-testid="chart-intent-zone-picker"
            onPointerDown={handleZonePointerDown}
            onPointerMove={handleZonePointerMove}
            onPointerUp={handleZonePointerUp}
            role="presentation"
          >
            <span>드래그해서 의도 존 지정</span>
          </div>
        ) : null}
        {guideOpen ? <ChartGuideLayer layer={guideLayer} /> : null}
      </div>
      {effectiveLayers.flow ? <VolumePanel analysis={analysis} averageVolume={averageVolume} /> : null}
    </>
  );
}

function ChartLayerControls({
  layers,
  onToggleLayer
}: {
  layers: ChartLayerState;
  onToggleLayer: (id: ChartLayerId, additive: boolean) => void;
}) {
  const comparisonCount = activeFocusLayers(layers).length;
  return (
    <div className="taLayerToggle" role="group" aria-label="차트 레이어 선택">
      {CHART_LAYER_DEFS.map((layer) => (
        <button
          aria-pressed={layerActive(layers, layer.id)}
          className={layerActive(layers, layer.id) ? "active" : ""}
          data-testid={`chart-layer-${layer.id}`}
          disabled={layer.id === "liquidation_estimated"}
          key={layer.id}
          onClick={(event) => onToggleLayer(layer.id, event.shiftKey)}
          title={layer.id === "liquidation_estimated" ? "미연동 · Coinglass 추정 수집기는 이번 작업 범위 밖입니다." : layer.description}
          type="button"
        >
          {layer.label}{layer.id === "liquidation_estimated" ? <small>미연동</small> : null}
        </button>
      ))}
      {comparisonCount === 2 ? <em className="chartCompareBadge" data-testid="chart-compare-badge">비교 중</em> : null}
      <small>일반 클릭 다중 토글 · shift-클릭 비교 추가(최대 2)</small>
    </div>
  );
}

function UnifiedHeatmapControls({
  filters,
  heatmap,
  error,
  opacity,
  onFilterChange,
  onOpacityChange,
  onZoneFocus
}: {
  filters: HeatmapFilters;
  heatmap: UnifiedLiquidationHeatmap | null;
  error: string;
  opacity: number;
  onFilterChange: <Key extends keyof HeatmapFilters>(key: Key, value: HeatmapFilters[Key]) => void;
  onOpacityChange: (value: number) => void;
  onZoneFocus: (price: number) => void;
}) {
  return (
    <section className="unifiedHeatmapControls" data-testid="unified-heatmap-controls">
      <header>
        <div>
          <strong>실현 청산 밀집</strong>
          <span>{heatmap?.truth_label ?? "실제 청산 · 예상 아님"}</span>
          <b>N={heatmap?.n_events ?? 0}</b>
          {heatmap?.sample_low !== false ? <em>표본 부족</em> : null}
        </div>
        <label title="히트맵 전역 투명도">
          농도
          <input
            aria-label="청산 히트맵 투명도"
            max="0.9"
            min="0.2"
            onChange={(event) => onOpacityChange(Number(event.target.value))}
            step="0.05"
            type="range"
            value={opacity}
          />
          <span>{Math.round(opacity * 100)}%</span>
        </label>
      </header>
      <div className="unifiedHeatmapFilterBar">
        <FilterGroup
          label="방향"
          options={[["all", "전체"], ["long", "롱만"], ["short", "숏만"]]}
          value={filters.side}
          onChange={(value) => onFilterChange("side", value as HeatmapFilters["side"])}
        />
        <FilterGroup
          label={heatmap?.filters.leverage_available ? "레버리지" : "규모"}
          options={heatmap?.filters.leverage_available
            ? [["all", "전체"], ["10x", "10x+"], ["25x", "25x+"], ["50x", "50x+"], ["100x", "100x+"]]
            : [["all", "전체 규모"], ["q2_plus", "Q2+"], ["q3_plus", "Q3+"], ["q4", "Q4"]]}
          value={filters.size}
          onChange={(value) => onFilterChange("size", value as HeatmapFilters["size"])}
        />
        <FilterGroup
          label="기간"
          options={[["12H", "12H"], ["24H", "24H"], ["3D", "3D"], ["1W", "1W"], ["1M", "1M"]]}
          value={filters.range}
          onChange={(value) => onFilterChange("range", value as HeatmapFilters["range"])}
        />
        <FilterGroup
          label="표현"
          options={[["persist", "persist"], ["event", "event"]]}
          value={filters.mode}
          onChange={(value) => onFilterChange("mode", value as HeatmapFilters["mode"])}
        />
      </div>
      {heatmap ? (
        <div className="unifiedHeatmapSummary" data-testid="unified-heatmap-summary">
          <SummaryMetric label="최대 밀집" value={heatmap.top_zones[0] ? `${formatPrice(heatmap.top_zones[0].price_mid)} · ${formatUsd(heatmap.top_zones[0].total_usd_estimated)}` : "-"} onClick={heatmap.top_zones[0] ? () => onZoneFocus(heatmap.top_zones[0].price_mid) : undefined} />
          <SummaryMetric label="현재가 위 / 아래" value={`${formatUsd(heatmap.position_split.above_usd_estimated)} / ${formatUsd(heatmap.position_split.below_usd_estimated)}`} />
          <SummaryMetric label="롱 / 숏 청산" value={`${formatUsd(heatmap.side_split.long_usd_estimated)} / ${formatUsd(heatmap.side_split.short_usd_estimated)}`} />
          <SummaryMetric label="마지막 이벤트" value={heatmap.last_event_ts ? formatKoreanDateTime(Date.parse(heatmap.last_event_ts) / 1000) : "-"} />
          <span className="heatmapBasis">{heatmap.filters.leverage_available ? "레버리지 원천값" : "레버리지 없음 · 규모 분위 폴백"}</span>
        </div>
      ) : null}
      {error ? <p className="unifiedHeatmapError">{error}</p> : null}
    </section>
  );
}

function FilterGroup({ label, options, value, onChange }: { label: string; options: string[][]; value: string; onChange: (value: string) => void }) {
  return (
    <div role="group" aria-label={label}>
      <span>{label}</span>
      {options.map(([option, caption]) => (
        <button aria-pressed={value === option} className={value === option ? "active" : ""} key={option} onClick={() => onChange(option)} type="button">{caption}</button>
      ))}
    </div>
  );
}

function SummaryMetric({ label, value, onClick }: { label: string; value: string; onClick?: () => void }) {
  const content = <><span>{label}</span><strong>{value}</strong></>;
  return onClick ? <button onClick={onClick} type="button">{content}</button> : <div>{content}</div>;
}

function WyckoffLayerStatus({ analysis }: { analysis: PositionChartAnalysis }) {
  const range = analysis.wyckoff_range;
  const trendValue = analysis.wyckoff.trend;
  const trend = isRecordValue(trendValue) && typeof trendValue.direction === "string"
    ? trendValue.direction
    : "neutral";
  const trendLabel = trend === "bearish" ? "하락 추세" : trend === "bullish" ? "상승 추세" : "추세 중립";
  const events = splitWyckoffEvents(analysis.wyckoff_markers, analysis.wyckoff_markers_low_confidence).events;
  if (!range) {
    return (
      <div className="taLayerStatus inactive" data-testid="wyckoff-layer-status">
        <strong>와이코프 미검출</strong>
        <span>거래 레인지 없음 · {trendLabel}</span>
        <small>Spring·UTAD·Phase 판정 대상 아님</small>
      </div>
    );
  }
  return (
    <div className="taLayerStatus confirmed" data-testid="wyckoff-layer-status">
      <strong>{phaseHintLabel(analysis.wyckoff_phase?.phase)}</strong>
      <span>레인지 {formatPrice(range.support.price)}–{formatPrice(range.resistance.price)}</span>
      <small>실제 확인 이벤트 {events.length}건 · {trendLabel}</small>
    </div>
  );
}

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function EmaRibbonHud({ ribbon }: { ribbon: EmaRibbonResult }) {
  const relation = ribbon.priceRelation === "above" ? "가격 리본 위" : ribbon.priceRelation === "below" ? "가격 리본 아래" : "가격 리본 내부";
  return (
    <div className={`emaRibbonHud ${ribbon.state}`} data-state={ribbon.state} data-testid="ema-ribbon-hud">
      <span>EMA 20–55</span>
      <strong>{ribbon.label}</strong>
      <em>폭 {ribbon.spreadPct.toFixed(2)}%</em>
      <small>{relation}</small>
    </div>
  );
}

function emaRibbonColor(state: EmaRibbonState, index: number, palette: ResolvedChartPalette): string {
  const alpha = Math.max(0.42, 0.9 - index * 0.065);
  if (state === "bullish") return palette.color("green", alpha);
  if (state === "bearish") return palette.color("red", alpha);
  if (state === "compressed") return palette.color("amber", alpha * 0.88);
  return palette.color("neutral", alpha * 0.8);
}

type LabeledPriceLine = ChartPriceLine & { title: string };

type GuideLayer = "plan" | "wyckoff" | "liquidity" | "harmonic" | "flow" | "levels";

function activeGuideLayer(layers: ChartLayerState): GuideLayer {
  if (layers.ta.includes("wyckoff")) return "wyckoff";
  if (layers.ta.includes("liquidity")) return "liquidity";
  if (layers.ta.includes("harmonic")) return "harmonic";
  if (layers.flow || layers.ta.includes("volume_profile")) return "flow";
  if (layers.ta.includes("levels")) return "levels";
  return "plan";
}

function minimalLayersForEvidence(evidence: MinimalChartEvidence | null): ChartLayerState {
  if (!evidence) return MINIMAL_FIXED_LAYER_STATE;
  if (evidence.layer === "flow") return { ...MINIMAL_FIXED_LAYER_STATE, flow: true };
  if (evidence.layer === "plan") return MINIMAL_FIXED_LAYER_STATE;
  return { ...MINIMAL_FIXED_LAYER_STATE, ta: [evidence.layer as TaFocusLayer] };
}

function assetClassLabel(assetClass?: string | null): string {
  if (assetClass === "stock") return "주식 퍼프";
  if (assetClass === "index") return "지수 퍼프";
  if (assetClass === "crypto") return "크립토";
  return "자산 미분류";
}

function ChartGuideLayer({ layer }: { layer: GuideLayer }) {
  const terms = guideTermsForLayer(layer);
  return (
    <div className={`chartGuideLayer guide-${layer}`} aria-label="차트 읽기 가이드" data-testid="chart-guide-layer">
      {terms.map((term, index) => {
        const entry = taGlossaryEntry(term);
        if (!entry) return null;
        return (
          <div className={`chartGuideCallout callout-${index + 1}`} key={term}>
            <strong>{entry.short}</strong>
            <span>{entry.plain}</span>
          </div>
        );
      })}
    </div>
  );
}

function guideTermsForLayer(layer: GuideLayer): string[] {
  if (layer === "wyckoff") return ["Range", "Spring", "UTAD"];
  if (layer === "liquidity") return ["LiquidityPool", "Sweep", "CHoCH"];
  if (layer === "harmonic") return ["PRZ", "ActionFlag"];
  if (layer === "flow") return ["POC", "CVD"];
  if (layer === "levels") return ["strong", "POC"];
  return ["RR", "ActionFlag"];
}

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


function renderUnifiedHeatmap(
  canvas: HTMLCanvasElement | null,
  container: HTMLDivElement,
  series: { priceToCoordinate(price: number): number | null },
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } },
  heatmap: UnifiedLiquidationHeatmap | null,
  opacity: number,
  highlightPrice: number | null
) {
  if (!canvas) return;
  const width = container.clientWidth;
  const height = container.clientHeight;
  const ratio = Math.max(1, window.devicePixelRatio || 1);
  const pixelWidth = Math.round(width * ratio);
  const pixelHeight = Math.round(height * ratio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const destination = canvas.getContext("2d");
  if (!destination) return;
  destination.clearRect(0, 0, pixelWidth, pixelHeight);
  if (!heatmap || opacity <= 0 || !heatmap.max_value_usd_estimated) return;

  const offscreen = typeof OffscreenCanvas !== "undefined"
    ? new OffscreenCanvas(pixelWidth, pixelHeight)
    : document.createElement("canvas");
  if (offscreen instanceof HTMLCanvasElement) {
    offscreen.width = pixelWidth;
    offscreen.height = pixelHeight;
  }
  const context = offscreen.getContext("2d");
  if (!context) return;
  context.scale(ratio, ratio);
  const maxLog = Math.log1p(heatmap.max_value_usd_estimated);
  const stepSeconds = heatmap.timeframe_seconds ?? 14_400;
  heatmap.grid.forEach((row, timeIndex) => {
    const bucketSeconds = Math.floor(Date.parse(heatmap.time_buckets[timeIndex]) / 1000);
    const x1 = chart.timeScale().timeToCoordinate(bucketSeconds as Time);
    const x2 = chart.timeScale().timeToCoordinate((bucketSeconds + stepSeconds) as Time);
    if (x1 === null || x2 === null) return;
    const left = Math.min(x1, x2);
    const cellWidth = Math.max(1, Math.abs(x2 - x1) + 0.6);
    row.forEach((value, priceIndex) => {
      if (value <= 0) return;
      const low = heatmap.price_bins.min + priceIndex * heatmap.price_bins.step;
      const high = low + heatmap.price_bins.step;
      const y1 = series.priceToCoordinate(high);
      const y2 = series.priceToCoordinate(low);
      if (y1 === null || y2 === null) return;
      const normalized = maxLog > 0 ? Math.log1p(value) / maxLog : 0;
      context.fillStyle = heatmapColor(normalized, opacity);
      context.fillRect(left, Math.min(y1, y2), cellWidth, Math.max(1.2, Math.abs(y2 - y1) + 0.5));
    });
  });
  for (const zone of heatmap.top_zones.slice(0, 3)) {
    const y1 = series.priceToCoordinate(zone.price_high);
    const y2 = series.priceToCoordinate(zone.price_low);
    if (y1 === null || y2 === null) continue;
    context.strokeStyle = "rgba(255, 220, 76, 0.72)";
    context.lineWidth = 1;
    context.setLineDash([5, 4]);
    context.strokeRect(0.5, Math.min(y1, y2), Math.max(1, width - AXIS_GUTTER), Math.max(2, Math.abs(y2 - y1)));
  }
  if (highlightPrice !== null) {
    const y = series.priceToCoordinate(highlightPrice);
    if (y !== null) {
      context.setLineDash([]);
      context.strokeStyle = "rgba(255, 236, 98, 0.96)";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(0, y);
      context.lineTo(Math.max(1, width - AXIS_GUTTER), y);
      context.stroke();
    }
  }
  destination.drawImage(offscreen, 0, 0);
}

function heatmapColor(value: number, opacity: number): string {
  const stops = [
    { at: 0, color: [31, 18, 95] },
    { at: 0.38, color: [0, 127, 255] },
    { at: 0.68, color: [32, 227, 178] },
    { at: 1, color: [255, 216, 74] }
  ];
  const bounded = clamp(value, 0, 1);
  const upper = stops.findIndex((stop) => stop.at >= bounded);
  const right = stops[Math.max(1, upper === -1 ? stops.length - 1 : upper)];
  const left = stops[Math.max(0, stops.indexOf(right) - 1)];
  const progress = (bounded - left.at) / Math.max(right.at - left.at, 1e-9);
  const rgb = left.color.map((channel, index) => Math.round(channel + (right.color[index] - channel) * progress));
  return `rgba(${rgb.join(",")},${clamp(opacity * (0.48 + bounded * 0.52), 0.1, 0.9)})`;
}

function HeatmapLegend({ heatmap }: { heatmap: UnifiedLiquidationHeatmap }) {
  return (
    <div className="unifiedHeatmapLegend" data-testid="unified-heatmap-legend">
      <span>$0</span><i /><span>{formatUsd(heatmap.max_value_usd_estimated)}</span>
    </div>
  );
}

function heatmapCellAt(heatmap: UnifiedLiquidationHeatmap | null, time: number, price: number | null): { value: number; events: number } | null {
  if (!heatmap || price === null || !heatmap.time_buckets.length || !heatmap.timeframe_seconds) return null;
  const start = Date.parse(heatmap.time_buckets[0]) / 1000;
  const timeIndex = Math.floor((time - start) / heatmap.timeframe_seconds);
  const priceIndex = Math.floor((price - heatmap.price_bins.min) / heatmap.price_bins.step);
  const value = heatmap.grid[timeIndex]?.[priceIndex];
  if (!value) return null;
  const bucketStart = start + timeIndex * heatmap.timeframe_seconds;
  const priceLow = heatmap.price_bins.min + priceIndex * heatmap.price_bins.step;
  const priceHigh = priceLow + heatmap.price_bins.step;
  const events = heatmap.events.filter((event) => {
    if (event.price < priceLow || event.price >= priceHigh) return false;
    const eventTime = Date.parse(event.timestamp) / 1000;
    if (heatmap.filters.mode === "event") return eventTime >= bucketStart && eventTime < bucketStart + heatmap.timeframe_seconds!;
    const end = event.persisted_until ? Date.parse(event.persisted_until) / 1000 : Number.POSITIVE_INFINITY;
    return eventTime < bucketStart + heatmap.timeframe_seconds! && end > bucketStart;
  }).length;
  return { value, events };
}

function loadHeatmapFilters(): HeatmapFilters {
  if (typeof window === "undefined") return DEFAULT_HEATMAP_FILTERS;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(HEATMAP_FILTERS_KEY) || "{}") as Partial<HeatmapFilters>;
    return {
      side: ["all", "long", "short"].includes(parsed.side || "") ? parsed.side! : "all",
      size: ["all", "q2_plus", "q3_plus", "q4", "10x", "25x", "50x", "100x"].includes(parsed.size || "") ? parsed.size! : "all",
      range: ["12H", "24H", "3D", "1W", "1M"].includes(parsed.range || "") ? parsed.range! : "3D",
      mode: parsed.mode === "event" ? "event" : "persist"
    };
  } catch {
    return DEFAULT_HEATMAP_FILTERS;
  }
}

function loadHeatmapOpacity(): number {
  if (typeof window === "undefined") return 0.55;
  const raw = window.localStorage.getItem(HEATMAP_OPACITY_KEY);
  if (raw === null) return 0.55;
  const value = Number(raw);
  return Number.isFinite(value) ? clamp(value, 0.2, 0.9) : 0.55;
}

function formatUsd(value: number): string {
  return `$${formatCompactNumber(value)}`;
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
  palette: ResolvedChartPalette,
  density: Density,
  minimalEvidence: MinimalChartEvidence | null = null,
  compressed = false,
  gauges: CompactChartGauges | null = null
) {
  if (!svg) return;
  const width = container.clientWidth;
  const height = container.clientHeight;
  if (!width || !height) return;
  const zones: string[] = [];
  const shapes: string[] = [];
  const badges: string[] = [];
  const right = overlayRight(width);
  const context: OverlayContext = { series, chart, analysis, plan, layers, positionOverlay, highlightPrice, palette, width, height, right, density, minimal: Boolean(minimalEvidence) || compressed };

  if (compressed) {
    const nextTier2Key = (gauges?.tier2_overlays ?? [])
      .slice(0, 2)
      .map((overlay) => `${overlay.engine}:${overlay.direction}:${overlay.price ?? overlay.claim}`)
      .join("|");
    const previousTier2Key = svg.dataset.tier2Key ?? "";
    const animateTier2 = Boolean(previousTier2Key && nextTier2Key && previousTier2Key !== nextTier2Key);
    svg.dataset.tier2Key = nextTier2Key;
    const compact = compressedOverlayNodes(context, gauges, animateTier2);
    zones.push(...compact.zones);
    shapes.push(...compact.shapes);
    badges.push(...compact.badges);
    if (gauges) {
      badges.push(...validatedEventPillNodes(context, gauges));
    }
  } else if (minimalEvidence) {
    const minimal = minimalEvidenceOverlayNodes(context, minimalEvidence, activeHarmonic);
    zones.push(...minimal.zones);
    shapes.push(...minimal.shapes);
    badges.push(...minimal.badges);
  } else {
    if (layers.ta.includes("levels")) {
      zones.push(...structureZoneNodes(context));
    }
    if (layers.ta.includes("liquidity")) {
      const liquidity = liquidityOverlayNodes(context);
      zones.push(...liquidity.zones);
      shapes.push(...liquidity.shapes);
      badges.push(...liquidity.badges);
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
    if (layers.flow) {
      zones.push(...liquidationClusterNodes(context));
    }
    if (layers.ta.includes("harmonic")) {
      const harmonic = activeHarmonic
        ? harmonicPatternNodes(context, activeHarmonic)
        : harmonicCandidateNodes();
      zones.push(...harmonic.zones);
      shapes.push(...harmonic.shapes);
      badges.push(...harmonic.badges);
    }
    if (layers.ta.includes("onchain")) {
      const onchain = onchainOverlayNodes(context);
      shapes.push(...onchain.shapes);
      badges.push(...onchain.badges);
    }
  }
  if (!compressed && layers.plan) {
    zones.push(...riskRewardBoxNodes(context));
    badges.push(...priceFlagNodes(context));
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
  density: Density;
  minimal: boolean;
};

type OverlayGroup = { zones: string[]; shapes: string[]; badges: string[] };

function overlayRight(width: number): number {
  return Math.max(24, width - AXIS_GUTTER);
}

function compressedOverlayNodes(context: OverlayContext, gauges: CompactChartGauges | null, animateTier2 = false): OverlayGroup {
  const group: OverlayGroup = { zones: [], shapes: [], badges: [] };
  const mark = context.analysis.mark_price;
  const levelRows: Array<{ price: number; label: string; tone: "amber" | "red" | "text"; priority: number }> = [];
  const support = [...context.analysis.price_levels.support]
    .filter((level) => level.price <= mark)
    .sort((left, right) => right.score - left.score)[0];
  const resistance = [...context.analysis.price_levels.resistance]
    .filter((level) => level.price >= mark)
    .sort((left, right) => right.score - left.score)[0];
  const poc = context.analysis.volume_profile?.poc_price;
  if (support) levelRows.push({ price: support.price, label: "상위 지지", tone: "amber", priority: 40 });
  if (resistance) levelRows.push({ price: resistance.price, label: "상위 저항", tone: "red", priority: 40 });
  if (isFinitePrice(poc)) levelRows.push({ price: poc, label: "최다 거래 가격", tone: "text", priority: 30 });

  const confirmedSweep = [...(context.analysis.liquidity?.sweeps ?? []), ...(context.analysis.liquidity?.htf_range_sweeps ?? [])]
    .filter((sweep) => sweep.confirmed)
    .sort((left, right) => right.timestamp - left.timestamp)[0];
  if (confirmedSweep) {
    levelRows.push({
      price: confirmedSweep.price || confirmedSweep.pool_price,
      label: confirmedSweep.side === "buy_side" ? "고점 청소" : "저점 청소",
      tone: confirmedSweep.side === "buy_side" ? "red" : "amber",
      priority: 60
    });
  } else {
    const pool = nearestByPrice((context.analysis.liquidity?.pools ?? []).filter((item) => !item.swept), mark);
    if (pool) levelRows.push({ price: pool.price, label: "유동성 풀", tone: pool.side === "buy_side" ? "red" : "amber", priority: 50 });
  }

  const tier2Badges: string[] = [];
  const tier2Rows = (gauges?.tier2_overlays ?? []).slice(0, 2);
  for (const [index, overlay] of tier2Rows.entries()) {
    if (isFinitePrice(overlay.price)) {
      levelRows.push({
        price: overlay.price,
        label: overlay.engine_label || overlay.claim,
        tone: overlay.direction === "short" ? "red" : "amber",
        priority: 20 - index
      });
    } else {
      tier2Badges.push(minimalFloatingLabel(
        context,
        `${overlay.engine_label || overlay.engine} · ${overlay.claim}`,
        18,
        30 + index * 30,
        overlay.direction === "short" ? "red" : "amber"
      ));
    }
  }
  if (tier2Rows.length) {
    const className = animateTier2 ? "tier2OverlayGroup is-switching" : "tier2OverlayGroup";
    const key = tier2Rows.map((overlay) => `${overlay.engine}:${overlay.direction}`).join("|");
    if (tier2Badges.length) group.badges.push(`<g class="${className}" data-testid="tier2-overlay-label" data-tier2-key="${escapeSvgText(key)}">${tier2Badges.join("")}</g>`);
  }
  const selectedLevels = levelRows
    .filter((row, index, rows) => rows.findIndex((candidate) => Math.abs(candidate.price - row.price) <= Math.max(Math.abs(row.price) * 0.0002, 1e-9)) === index)
    .sort((left, right) => right.priority - left.priority)
    .slice(0, 3);
  group.shapes.push(...stackCompactLevels(context, selectedLevels).flatMap(({ row, pillY }) => compactPriceLine(context, row.price, row.label, row.tone, pillY)));
  return group;
}

function stanceKorean(stance: string | null | undefined): string {
  if (stance === "long_leaning" || stance === "long") return "상방";
  if (stance === "short_leaning" || stance === "short") return "하방";
  if (stance === "conflicted") return "균형";
  return "판단 보류";
}

function validatedEventPillNodes(context: OverlayContext, gauges: CompactChartGauges): string[] {
  return (gauges.event_pills ?? [])
    .filter((pill) => pill.qualification === "validated" && pill.confirmed)
    .slice(0, 6)
    .flatMap((pill, index) => {
      const x = context.chart.timeScale().timeToCoordinate(pill.time as Time);
      const y = context.series.priceToCoordinate(pill.price);
      if (x === null || y === null) return [];
      const label = truncateSvgLabel(pill.label, 12);
      const width = Math.max(72, label.length * 11 + 26);
      const left = clamp(x - width / 2, 8, context.right - width - 6);
      const above = pill.direction === "short";
      const top = clamp(y + (above ? -34 : 14) + (index % 2) * (above ? -3 : 3), 8, context.height - 28);
      const color = pill.direction === "long" ? context.palette.color("green", 0.9) : context.palette.color("red", 0.9);
      const result = pill.sample_size >= 30 && pill.win_1r_pct !== null && pill.win_1r_pct !== undefined
        ? `실측 ${pill.win_1r_pct}% (N=${pill.sample_size})`
        : "표본 축적 중";
      return [
        `<g class="validatedEventPill" data-event-pill="${escapeSvgText(pill.id)}" tabindex="0">` +
        `<title>${escapeSvgText(`${pill.label} · 신뢰도 ${pill.confidence} · ${result}`)}</title>` +
        `<rect x="${left}" y="${top}" width="${width}" height="24" rx="12" fill="${color}" stroke="${context.palette.color("panel", 0.92)}" stroke-width="1.5" />` +
        `<text x="${left + 12}" y="${top + 16}" fill="${context.palette.color("panel")}" font-size="11" font-weight="800">${pill.direction === "long" ? "↑" : "↓"} ${escapeSvgText(label)}</text></g>`
      ];
    });
}

function parseChartTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : null;
}

function nearestTimePoint<T extends { time: number }>(points: T[], time: number): T | null {
  return points.reduce<T | null>((best, point) => !best || Math.abs(point.time - time) < Math.abs(best.time - time) ? point : best, null);
}

function stanceTone(
  stance: string,
  previousStance?: string | null,
  longEvidence = 0,
  shortEvidence = 0
): "long" | "short" | "neutral" | "transition" | "conflict-long" | "conflict-short" {
  if (stance === "long_leaning") return "long";
  if (stance === "short_leaning") return "short";
  if (stance === "conflicted") {
    if (previousStance === "long_leaning" || longEvidence > shortEvidence) return "conflict-long";
    if (previousStance === "short_leaning" || shortEvidence > longEvidence) return "conflict-short";
  }
  return "neutral";
}

function formatHudCountdown(minutes: number | null | undefined): string {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return "마감 확인 중";
  if (minutes >= 60) return `마감 ${Math.floor(minutes / 60)}h ${Math.max(0, Math.round(minutes % 60))}m`;
  return `마감 ${Math.max(1, Math.round(minutes))}m`;
}

function StanceHeaderHud({
  analysis,
  gauges,
  open,
  onToggle,
  showSecondaryTaRows
}: {
  analysis: PositionChartAnalysis;
  gauges: CompactChartGauges;
  open: boolean;
  onToggle: () => void;
  showSecondaryTaRows: boolean;
}) {
  const direction = gauges.direction;
  const previewStance = direction.preview_stance || direction.target;
  const previewText = direction.transitioning && previewStance && previewStance !== direction.stance
    ? ` · 순간 ${stanceKorean(previewStance)} 시도 — 전환 문턱 ${Math.round((direction.flip_progress ?? 0) * 100)}%`
    : "";
  const headline = direction.transitioning
    ? `${gauges.market_view?.stance_label || direction.stance_label || "판단 대기"} 유지`
    : `${gauges.market_view?.stance_label || direction.stance_label || "판단 대기"}${direction.stance === "conflicted" ? "" : " 진행 중"}`;
  return (
    <div className={`stanceHud ${gauges.bar_state.provisional ? "provisional" : ""}`} data-testid="stance-hud">
      <button type="button" onClick={onToggle} aria-expanded={open}>
        <span className="stanceHudClock">{analysis.timeframe}</span>
        <span className={`stanceHudDot ${stanceTone(direction.stance, direction.previous_stance, direction.long_evidence_count, direction.short_evidence_count)}`} />
        <span className="stanceHudCopy">
          <strong>{headline}{direction.stance !== "conflicted" ? ` (${direction.candles_in_state ?? 0}캔들째)` : ""}{previewText}</strong>
          <span>상방 {direction.long_evidence_count ?? 0} · 하방 {direction.short_evidence_count ?? 0}</span>
          {gauges.bar_state.provisional ? <em>잠정 · {formatHudCountdown(gauges.bar_state.minutes_to_close)}</em> : null}
        </span>
      </button>
      {open ? (
        <div className="stanceHudStrip" data-testid="stance-hud-strip">
          {visibleTaRows(analysis.one_liners?.lines ?? [], showSecondaryTaRows).map((line) => (
            <span key={line.module}><b>{line.module_label}</b> {line.phrase}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function compactPriceLine(
  context: OverlayContext,
  price: number,
  label: string,
  tone: "amber" | "red" | "text",
  pillY?: number
): string[] {
  const y = context.series.priceToCoordinate(price);
  if (y === null || y < 18 || y > context.height - 36) return [];
  const color = tone === "red"
    ? context.palette.color("red", 0.78)
    : tone === "amber"
      ? context.palette.color("amber", 0.78)
      : context.palette.color("text", 0.56);
  const text = truncateSvgLabel(label, 18);
  const width = Math.max(56, text.length * 10 + 16);
  const pillX = Math.max(8, context.right - width - 4);
  const resolvedPillY = pillY ?? clamp(y - 11, 6, context.height - 28);
  const leaderEndX = pillX - 4;
  return [
    `<line data-compact-overlay="true" x1="18" x2="${Math.max(36, context.right - 18)}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="1.25" stroke-dasharray="6 7" />`,
    Math.abs(resolvedPillY + 11 - y) > 2
      ? `<path d="M ${Math.max(36, context.right - 18)} ${y} L ${leaderEndX} ${resolvedPillY + 11}" fill="none" stroke="${color}" stroke-opacity="0.72" stroke-width="1" />`
      : "",
    `<g class="compactLevelPill" data-compact-level-label="${escapeSvgText(text)}">` +
    `<rect x="${pillX}" y="${resolvedPillY}" width="${width}" height="22" rx="4" fill="${context.palette.color("panel", 0.94)}" stroke="${color}" stroke-width="1" />` +
    `<text x="${pillX + width - 8}" y="${resolvedPillY + 15}" text-anchor="end" fill="${color}" font-size="11" font-weight="760" font-family="Pretendard, Inter, system-ui, sans-serif">${escapeSvgText(text)}</text></g>`
  ];
}

function stackCompactLevels(
  context: OverlayContext,
  rows: Array<{ price: number; label: string; tone: "amber" | "red" | "text"; priority: number }>
): Array<{ row: (typeof rows)[number]; pillY: number }> {
  const visible = rows
    .map((row) => ({ row, y: context.series.priceToCoordinate(row.price) }))
    .filter((item): item is { row: (typeof rows)[number]; y: number } => item.y !== null && item.y >= 18 && item.y <= context.height - 36)
    .sort((left, right) => left.y - right.y);
  const gap = 26;
  const top = 6;
  const bottom = context.height - 28;
  const result: Array<{ row: (typeof rows)[number]; pillY: number }> = [];
  let cursor = top - gap;
  for (const item of visible) {
    const pillY = Math.max(clamp(item.y - 11, top, bottom), cursor + gap);
    result.push({ row: item.row, pillY });
    cursor = pillY;
  }
  const overflow = result.length ? result.at(-1)!.pillY - bottom : 0;
  if (overflow > 0) {
    for (const item of result) item.pillY -= overflow;
  }
  return result;
}

function minimalEvidenceOverlayNodes(
  context: OverlayContext,
  evidence: MinimalChartEvidence,
  activeHarmonic: PositionChartAnalysis["harmonic_patterns"][number] | null
): OverlayGroup {
  const group: OverlayGroup = { zones: [], shapes: [], badges: [] };
  if (evidence.layer === "levels") {
    group.shapes.push(...minimalLevelNodes(context, evidence));
  } else if (evidence.layer === "liquidity") {
    group.shapes.push(...minimalLiquidityNodes(context, evidence));
  } else if (evidence.layer === "wyckoff") {
    group.shapes.push(...minimalWyckoffNodes(context, evidence));
  } else if (evidence.layer === "harmonic" && activeHarmonic) {
    group.shapes.push(...minimalHarmonicNodes(context, activeHarmonic));
  } else if (evidence.layer === "flow") {
    group.badges.push(minimalFlowBadge(context, minimalEvidenceLabel(evidence.label, "flow")));
  }
  if (group.zones.length || group.shapes.length || group.badges.length) {
    group.shapes.unshift(`<g data-testid="minimal-evidence-overlay">`);
    group.badges.push("</g>");
  }
  return group;
}

function minimalLevelNodes(context: OverlayContext, evidence: MinimalChartEvidence): string[] {
  const levels = [...context.analysis.price_levels.support, ...context.analysis.price_levels.resistance];
  const target = nearestByPrice(levels, evidence.price ?? context.analysis.mark_price);
  if (!target) return [];
  const label = minimalEvidenceLabel(evidence.label, target.kind === "resistance" ? "resistance" : "support");
  return minimalDashedPriceLine(context, target.price, label, target.kind === "resistance" ? "red" : "amber");
}

function minimalLiquidityNodes(context: OverlayContext, evidence: MinimalChartEvidence): string[] {
  const liquidity = context.analysis.liquidity;
  if (!liquidity) return [];
  const sweeps = [...(liquidity.sweeps ?? []), ...(liquidity.htf_range_sweeps ?? [])]
    .filter((sweep) => sweep.confirmed)
    .sort((left, right) => {
      if (typeof evidence.time === "number") return Math.abs(left.timestamp - evidence.time) - Math.abs(right.timestamp - evidence.time);
      return (right.timestamp - left.timestamp) || (right.confidence - left.confidence);
    });
  const sweep = sweeps[0];
  if (sweep) {
    return minimalDashedPriceLine(context, sweep.price || sweep.pool_price, liquiditySweepMinimalLabel(sweep), sweep.side === "buy_side" ? "red" : "amber");
  }
  const pool = nearestByPrice(liquidity.pools.filter((item) => !item.swept), evidence.price ?? context.analysis.mark_price);
  return pool ? minimalDashedPriceLine(context, pool.price, liquidityPoolMinimalLabel(pool), pool.side === "buy_side" ? "red" : "amber") : [];
}

function minimalWyckoffNodes(context: OverlayContext, evidence: MinimalChartEvidence): string[] {
  const range = context.analysis.wyckoff_range;
  if (!range) return [];
  const events = splitWyckoffEvents(context.analysis.wyckoff_markers, context.analysis.wyckoff_markers_low_confidence).events
    .sort((left, right) => {
      if (typeof evidence.time === "number") return Math.abs(left.time - evidence.time) - Math.abs(right.time - evidence.time);
      return (right.confidence - left.confidence) || (right.time - left.time);
    });
  const marker = events[0];
  if (marker) {
    const label = marker.side === "distribution"
      ? `숏 근거 · ${eventShortLabel(marker)}`
      : `롱 근거 · ${eventShortLabel(marker)}`;
    return minimalDashedPriceLine(context, marker.price, label, marker.side === "distribution" ? "red" : "amber");
  }
  return [
    ...minimalDashedPriceLine(context, range.resistance.price, "하방 경계 · 상단", "red", "top"),
    ...minimalDashedPriceLine(context, range.support.price, "상방 경계 · 하단", "amber", "bottom")
  ];
}

function minimalHarmonicNodes(context: OverlayContext, pattern: PositionChartAnalysis["harmonic_patterns"][number]): string[] {
  const przTop = context.series.priceToCoordinate(pattern.prz.high);
  const przBottom = context.series.priceToCoordinate(pattern.prz.low);
  if (przTop === null || przBottom === null) return [];
  const price = (pattern.prz.high + pattern.prz.low) / 2;
  const direction = pattern.direction === "bearish" ? "하방 후보" : "상방 후보";
  return minimalDashedPriceLine(context, price, `${direction} · 반전`, pattern.direction === "bearish" ? "red" : "amber");
}

function minimalFlowBadge(context: OverlayContext, label: string): string {
  return minimalFloatingLabel(context, truncateSvgLabel(label || "수급 근거", 18), 22, 32, "amber");
}

function minimalDashedPriceLine(
  context: OverlayContext,
  price: number,
  label: string,
  tone: "amber" | "red" | "text" = "text",
  placement: "auto" | "top" | "bottom" = "auto"
): string[] {
  const y = context.series.priceToCoordinate(price);
  if (y === null) return [];
  const lineY = clamp(y, 20, context.height - 46);
  const x1 = 18;
  const x2 = Math.max(28, context.right - 16);
  const labelX = clamp(x1 + (x2 - x1) * 0.5, 96, context.right - 96);
  const offset = placement === "top" ? -18 : placement === "bottom" ? 28 : lineY > context.height * 0.72 ? -18 : 28;
  const labelY = clamp(lineY + offset, 24, context.height - 24);
  const stroke = tone === "red" ? context.palette.color("red", 0.9) : tone === "amber" ? context.palette.color("amber", 0.92) : context.palette.color("text", 0.88);
  return [
    `<line x1="${x1}" x2="${x2}" y1="${lineY}" y2="${lineY}" stroke="${context.palette.color("text", 0.2)}" stroke-width="7" stroke-linecap="round" stroke-dasharray="10 12" />`,
    `<line x1="${x1}" x2="${x2}" y1="${lineY}" y2="${lineY}" stroke="${stroke}" stroke-width="2.5" stroke-linecap="round" stroke-dasharray="10 12" />`,
    minimalTextLabel(context, label, labelX, labelY, stroke)
  ];
}

function minimalTextLabel(context: OverlayContext, label: string, x: number, y: number, fill: string): string {
  const text = truncateSvgLabel(label, 16);
  return `<text x="${x}" y="${y}" text-anchor="middle" fill="${fill}" stroke="${context.palette.color("panel", 0.9)}" stroke-width="5" paint-order="stroke" font-size="19" font-weight="780" font-family="Pretendard, Inter, system-ui, sans-serif">${escapeSvgText(text)}</text>`;
}

function minimalFloatingLabel(context: OverlayContext, label: string, x: number, y: number, tone: "amber" | "red" | "text" = "text"): string {
  const fill = tone === "red" ? context.palette.color("red", 0.92) : tone === "amber" ? context.palette.color("amber", 0.92) : context.palette.color("text", 0.9);
  return minimalTextLabel(context, label, x + 86, y, fill);
}

function minimalEvidenceLabel(label: string, fallback: "support" | "resistance" | "flow"): string {
  const plain = label.replace(/\s+/g, " ").trim();
  if (plain.includes("스윕") || plain.includes("청소")) return plain.includes("고점") || plain.includes("상위") ? "하방 근거 · 고점 청소" : "상방 근거 · 저점 청소";
  if (plain.includes("UTAD")) return "하방 근거 · UTAD";
  if (plain.includes("Spring") || plain.includes("스프링")) return "상방 근거 · 스프링";
  if (plain.includes("반전")) return plain.includes("하락") || plain.includes("숏") ? "하방 후보 · 반전" : "상방 후보 · 반전";
  if (plain.includes("지지")) return "상방 근거 · 지지";
  if (plain.includes("저항")) return "하방 근거 · 저항";
  if (fallback === "support") return "상방 근거 · 지지";
  if (fallback === "resistance") return "하방 근거 · 저항";
  return plain || "수급 근거";
}

function liquiditySweepMinimalLabel(sweep: LiquiditySweep): string {
  if (sweep.side === "buy_side" || sweep.pool_kind.includes("high")) return "하방 근거 · 고점 청소";
  return "상방 근거 · 저점 청소";
}

function liquidityPoolMinimalLabel(pool: LiquidityPool): string {
  if (pool.side === "buy_side" || pool.kind.includes("high") || pool.kind === "eqh") return "상방 목표 · 고점 풀";
  return "하방 목표 · 저점 풀";
}

function nearestByPrice<T extends { price: number }>(items: T[], price: number): T | null {
  if (!Number.isFinite(price) || !items.length) return items[0] ?? null;
  return [...items].sort((left, right) => Math.abs(left.price - price) - Math.abs(right.price - price))[0] ?? null;
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
  const mark = context.positionOverlay?.markPrice ?? context.analysis.mark_price;
  const invalidation = planInvalidation(context.plan, context.analysis);
  const target = firstTakeProfit(context.plan);
  if (!Number.isFinite(entry) || !Number.isFinite(mark) || !invalidation || !target) return [];
  const entryY = context.series.priceToCoordinate(entry);
  const markY = context.series.priceToCoordinate(mark);
  const invalidationY = context.series.priceToCoordinate(invalidation.price);
  const targetY = context.series.priceToCoordinate(target.price);
  if (entryY === null || markY === null || invalidationY === null || targetY === null) return [];
  const lastX = context.chart.timeScale().timeToCoordinate(context.analysis.candles.at(-1)?.time as Time);
  const x = Math.max(18, Math.min(context.right - 130, (lastX ?? context.right * 0.7) + 8));
  const width = Math.max(74, context.right - x);
  const riskY = Math.min(markY, invalidationY);
  const riskHeight = Math.max(3, Math.abs(invalidationY - markY));
  const profitY = Math.min(markY, targetY);
  const profitHeight = Math.max(3, Math.abs(targetY - markY));
  const riskPct = directionDistancePct(invalidation.price, mark, context.analysis.direction);
  const profitPct = directionDistancePct(target.price, mark, context.analysis.direction);
  const rr = riskPct <= -0.8 && profitPct > 0 ? profitPct / Math.abs(riskPct) : null;
  const rrLabel = rr ? ` · R:R ${rr > 10 ? "10+" : rr.toFixed(1)}` : riskPct < 0 && Math.abs(riskPct) < 0.8 ? " · R:R 산출 불가" : "";
  const label = `현재 기준 ${formatSignedPercent(profitPct)} / ${formatSignedPercent(riskPct)}${rrLabel}`;
  const profitFill = context.minimal ? context.palette.zone("profit", 0.045) : context.palette.zone("profit");
  const riskFill = context.minimal ? context.palette.zone("risk", 0.045) : context.palette.zone("risk");
  return [
    `<rect x="${x}" y="${profitY}" width="${width}" height="${profitHeight}" fill="${profitFill}" />`,
    `<rect x="${x}" y="${riskY}" width="${width}" height="${riskHeight}" fill="${riskFill}" />`,
    `<line x1="${x}" x2="${context.right}" y1="${entryY}" y2="${entryY}" stroke="${context.palette.flag("entry", 0.54)}" stroke-width="1" stroke-dasharray="${chartTheme.stroke.minor.dash}" />`,
    `<line x1="${x}" x2="${context.right}" y1="${markY}" y2="${markY}" stroke="${context.palette.flag("mark", 0.72)}" stroke-width="${chartTheme.stroke.major.width}" />`,
    labelBadge(context.right - 154, Math.max(18, Math.min(profitY, riskY) - 26), label, context.palette.color("panel", 0.82), context.palette.flag("takeProfit", 0.88), context.palette.color("text"), 148)
  ];
}

function priceFlagNodes(context: OverlayContext): string[] {
  const flags = stackFlags(actionPriceFlags(context), context.height, context.series);
  return flags.map((flag) => {
    if (flag.label.startsWith("더보기 ")) {
      return `<g data-testid="price-flag-overflow"><circle cx="${context.right + 10}" cy="${flag.y}" r="4" fill="${context.palette.color("muted", 0.84)}"><title>${escapeSvgText(flag.label)}</title></circle></g>`;
    }
    const highlighted = context.highlightPrice !== null && Math.abs(flag.price - context.highlightPrice) <= Math.abs(flag.price) * 1e-9 + 1e-12;
    const displayLabel = truncateSvgLabel(flag.label, highlighted ? 18 : 15);
    const width = Math.max(62, Math.min(124, displayLabel.length * 7 + 16 + (highlighted ? 10 : 0)));
    const x = context.right + 4 - (highlighted ? 6 : 0);
    const fill = context.palette.flag(flag.kind, highlighted ? 1 : 0.92);
    const stroke = context.palette.color("text", highlighted ? 0.8 : 0.24);
    const text = flag.kind === "mark" ? context.palette.color("panel") : context.palette.color("panel");
    return [
      `<g data-price-flag-kind="${flag.kind}">`,
      `<rect x="${x}" y="${flag.y - 10}" width="${width}" height="20" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="${highlighted ? 1.6 : 1}" />`,
      `<text x="${x + 7}" y="${flag.y + 3.5}" fill="${text}" font-size="${highlighted ? 10.5 : 9.5}" font-weight="${highlighted ? 750 : 650}" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(displayLabel)}</text>`,
      "</g>"
    ].join("");
  });
}

function structureZoneNodes(context: OverlayContext): string[] {
  const atr = averageTrueRange(context.analysis.candles);
  const support = context.analysis.price_levels.support.slice(0, 3).map((level, index) => levelZoneNode(context, level, index, "support", atr));
  const resistance = context.analysis.price_levels.resistance.slice(0, 3).map((level, index) => levelZoneNode(context, level, index, "resistance", atr));
  return [...support, ...resistance].flat();
}

function liquidityOverlayNodes(context: OverlayContext): OverlayGroup {
  const liquidity = context.analysis.liquidity;
  if (!liquidity) return { zones: [], shapes: [], badges: [] };
  const zones = [
    ...dealingRangeNodes(context),
    ...visibleLiquidityPools(liquidity.pools, context.density).flatMap((pool) => liquidityPoolZoneNode(context, pool))
  ];
  const sweeps = visibleLiquiditySweeps([...(liquidity.sweeps ?? []), ...(liquidity.htf_range_sweeps ?? [])], context.density);
  const badges = groupLiquiditySweepBadges(context, sweeps).flatMap(({ sweep, count }, index) => liquiditySweepBadgeNode(context, sweep, index, count));
  const shapes = [
    ...sweeps.flatMap((sweep) => liquiditySweepLeaderNode(context, sweep)),
    ...structureShiftNodes(context)
  ];
  if (!zones.length && !badges.length && !shapes.length) return { zones: [], shapes: [], badges: [] };
  return {
    zones: [`<g data-testid="liquidity-layer">`, ...zones, "</g>"],
    shapes,
    badges
  };
}

function visibleLiquidityPools(pools: LiquidityPool[], density: Density): LiquidityPool[] {
  const unswept = pools
    .filter((pool) => !pool.swept)
    .sort((left, right) => (right.score - left.score) || (right.touch_count - left.touch_count));
  if (density === "simple") return unswept.slice(0, 2);
  return unswept.slice(0, 12);
}

function visibleLiquiditySweeps(sweeps: LiquiditySweep[], density: Density): LiquiditySweep[] {
  const confirmed = sweeps
    .filter((sweep) => sweep.confirmed)
    .sort((left, right) => (right.timestamp - left.timestamp) || (right.confidence - left.confidence));
  if (density === "simple") return confirmed.filter((sweep) => sweep.grade === "Strong").slice(0, 4);
  return confirmed.filter((sweep) => sweep.grade !== "Weak").slice(0, 8);
}

function liquidityPoolZoneNode(context: OverlayContext, pool: LiquidityPool): string[] {
  const atr = averageTrueRange(context.analysis.candles);
  const band = Math.max(atr * 0.12, pool.price * 0.0008);
  const y1 = context.series.priceToCoordinate(pool.price + band);
  const y2 = context.series.priceToCoordinate(pool.price - band);
  if (y1 === null || y2 === null) return [];
  const touch = firstTouchCandle(context.analysis.candles, pool.price, band, pool.first_seen);
  const x1 = touch ? context.chart.timeScale().timeToCoordinate(touch.time as Time) : null;
  if (x1 === null) return [];
  const y = Math.min(y1, y2);
  const height = Math.max(4, Math.abs(y2 - y1));
  const width = Math.max(18, context.right - x1);
  const buySide = pool.side === "buy_side";
  const fill = context.palette.color(buySide ? "amber" : "teal", Math.min(0.28, 0.1 + pool.score / 520));
  const stroke = context.palette.color(buySide ? "amber" : "teal", Math.min(0.78, 0.32 + pool.score / 210));
  const label = liquidityPoolLabel(pool);
  return [
    `<rect data-testid="liquidity-pool-zone" x="${x1}" y="${y}" width="${width}" height="${height}" rx="3" fill="${fill}" stroke="${stroke}" stroke-width="1" />`,
    labelBadge(x1 + 5, Math.max(16, y - 19), label, context.palette.color("panel", 0.78), stroke, context.palette.color("text"), 108)
  ];
}

function dealingRangeNodes(context: OverlayContext): string[] {
  const range = context.analysis.liquidity.dealing_range;
  if (!range) return [];
  const top = context.series.priceToCoordinate(range.high);
  const mid = context.series.priceToCoordinate(range.midpoint);
  const bottom = context.series.priceToCoordinate(range.low);
  if (top === null || mid === null || bottom === null) return [];
  const yTop = Math.min(top, bottom);
  const yBottom = Math.max(top, bottom);
  const x = 0;
  const width = Math.max(18, context.right);
  const premiumY = Math.min(top, mid);
  const discountY = Math.min(mid, bottom);
  return [
    `<rect x="${x}" y="${premiumY}" width="${width}" height="${Math.max(3, Math.abs(mid - top))}" fill="${context.palette.color("amber", 0.045)}" />`,
    `<rect x="${x}" y="${discountY}" width="${width}" height="${Math.max(3, Math.abs(bottom - mid))}" fill="${context.palette.color("teal", 0.045)}" />`,
    `<line x1="${x}" x2="${width}" y1="${mid}" y2="${mid}" stroke="${context.palette.color("neutral", 0.42)}" stroke-width="1" stroke-dasharray="${chartTheme.stroke.minor.dash}" />`,
    labelBadge(10, Math.max(16, yTop + 8), "프리미엄", context.palette.color("panel", 0.52), context.palette.color("amber", 0.4), context.palette.color("muted"), 76),
    labelBadge(10, Math.min(context.height - 28, yBottom - 28), "디스카운트", context.palette.color("panel", 0.52), context.palette.color("teal", 0.4), context.palette.color("muted"), 82)
  ];
}

function liquiditySweepLeaderNode(context: OverlayContext, sweep: LiquiditySweep): string[] {
  const x = context.chart.timeScale().timeToCoordinate(sweep.timestamp as Time);
  const poolY = context.series.priceToCoordinate(sweep.pool_price ?? sweep.price);
  const wickY = context.series.priceToCoordinate(sweep.wick_extreme ?? sweep.price);
  if (x === null || poolY === null || wickY === null) return [];
  const tone = sweep.side === "buy_side" ? "invalidation" : "takeProfit";
  return [
    `<line data-testid="liquidity-sweep-line" x1="${x}" x2="${x}" y1="${wickY}" y2="${poolY}" stroke="${context.palette.flag(tone, 0.76)}" stroke-width="1.4" />`,
    `<circle cx="${x}" cy="${wickY}" r="3.5" fill="${context.palette.flag(tone, 0.92)}" stroke="${context.palette.color("text", 0.84)}" stroke-width="1" />`
  ];
}

function groupLiquiditySweepBadges(context: OverlayContext, sweeps: LiquiditySweep[]): Array<{ sweep: LiquiditySweep; count: number }> {
  const groups: Array<{ sweep: LiquiditySweep; count: number; x: number; y: number }> = [];
  for (const sweep of sweeps) {
    const x = context.chart.timeScale().timeToCoordinate(sweep.timestamp as Time);
    const y = context.series.priceToCoordinate(sweep.pool_price ?? sweep.price);
    if (x === null || y === null) continue;
    const previous = groups.at(-1);
    if (previous && previous.sweep.side === sweep.side && Math.abs(previous.x - x) <= 42 && Math.abs(previous.y - y) <= 10) {
      previous.count += 1;
      continue;
    }
    groups.push({ sweep, count: 1, x, y });
  }
  return groups.map(({ sweep, count }) => ({ sweep, count }));
}

function liquiditySweepBadgeNode(context: OverlayContext, sweep: LiquiditySweep, index: number, count = 1): string[] {
  const x = context.chart.timeScale().timeToCoordinate(sweep.timestamp as Time);
  const y = context.series.priceToCoordinate(sweep.pool_price ?? sweep.price);
  if (x === null || y === null) return [];
  const buySide = sweep.side === "buy_side";
  const tone = buySide ? "invalidation" : "takeProfit";
  const label = `${liquiditySweepLabel(sweep)}${count > 1 ? ` ×${count}` : ""}`;
  const offsetX = buySide ? -94 : 8;
  const labelX = clamp(x + offsetX + (index % 2) * 12, 4, context.right - 112);
  const labelY = clamp(y + (buySide ? -30 : 12) + (index % 2) * 14, 16, context.height - 28);
  return [
    `<g data-testid="liquidity-sweep-badge"><polyline points="${x},${y} ${labelX + 8},${labelY + 10}" fill="none" stroke="${context.palette.flag(tone, 0.5)}" stroke-width="1" stroke-dasharray="3 3" />${labelBadge(labelX, labelY, label, context.palette.color("panel", 0.84), context.palette.flag(tone, 0.86), context.palette.color("text"), 112)}</g>`
  ];
}

function structureShiftNodes(context: OverlayContext): string[] {
  const shift = context.analysis.liquidity.structure_shift;
  if (!shift?.event || typeof shift.level !== "number") return [];
  const y = context.series.priceToCoordinate(shift.level);
  if (y === null) return [];
  const recentX = context.chart.timeScale().timeToCoordinate(context.analysis.candles.at(-1)?.time as Time) ?? context.right - 120;
  const x1 = clamp(recentX - 116, 8, context.right - 136);
  const x2 = clamp(recentX + 12, x1 + 42, context.right - 12);
  const isChoCh = shift.event === "CHoCH";
  const stroke = context.palette.flag(isChoCh ? "watch" : "entry", isChoCh ? 0.92 : 0.72);
  const label = isChoCh ? "구조 전환 후보(CHoCH)" : "구조 지속 돌파(BOS)";
  return [
    `<line data-testid="liquidity-structure-line" x1="${x1}" x2="${x2}" y1="${y}" y2="${y}" stroke="${stroke}" stroke-width="${isChoCh ? 2 : 1.4}" />`,
    labelBadge(x1, clamp(y - 26, 16, context.height - 28), label, context.palette.color("panel", 0.78), stroke, context.palette.color("text"), isChoCh ? 126 : 118)
  ];
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
  if (!range) return unconfirmedWyckoffNodes();
  // 레인지 경계가 현재 보이는 범위 밖이면 price/timeToCoordinate가 null을 준다.
  // 통째로 숨기지 말고 차트 가장자리로 clamp해 — 스크롤/줌 위치와 무관하게 와이코프 구조가 항상 보이도록.
  const markCoord = context.series.priceToCoordinate(context.analysis.mark_price);
  const top = context.series.priceToCoordinate(range.resistance.price)
    ?? (markCoord !== null && range.resistance.price >= context.analysis.mark_price ? 0 : context.height);
  const bottom = context.series.priceToCoordinate(range.support.price)
    ?? (markCoord !== null && range.support.price <= context.analysis.mark_price ? context.height : 0);
  const rawX1 = context.chart.timeScale().timeToCoordinate(range.start_time as Time);
  const rawX2 = context.chart.timeScale().timeToCoordinate(range.end_time as Time);
  if (rawX1 === null && rawX2 === null) return { zones: [], shapes: [], badges: [] };
  const x1 = rawX1 ?? 0;
  const x2 = rawX2 ?? context.width;
  const x = Math.min(x1, x2);
  const width = Math.max(12, Math.abs(x2 - x1));
  const y = Math.min(top, bottom);
  const height = Math.max(8, Math.abs(bottom - top));
  // 와이코프 구조는 시안(blue) 톤으로 통일 — 하모닉(보라)·플랜(적/녹)과 구분되고 배경에서 또렷하게.
  const rangeStroke = context.palette.color("blue", 0.85);
  const zones = [
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="3" fill="${context.palette.color("blue", 0.13)}" stroke="${context.palette.color("blue", 0.7)}" stroke-width="1.5" />`,
    `<line x1="${x}" x2="${x + width}" y1="${top}" y2="${top}" stroke="${rangeStroke}" stroke-width="2.5" />`,
    `<line x1="${x}" x2="${x + width}" y1="${bottom}" y2="${bottom}" stroke="${rangeStroke}" stroke-width="2.5" />`
  ];
  const shapes: string[] = [];
  const events = splitWyckoffEvents(context.analysis.wyckoff_markers, context.analysis.wyckoff_markers_low_confidence).events;
  shapes.push(...events.flatMap((marker) => wyckoffEventMarker(context, marker)));
  const groupedEvents = groupWyckoffBadges(context, events);
  const badges = groupedEvents.flatMap(({ marker, count }, index) => wyckoffEventBadge(context, marker, { top, bottom, x, width }, index, count));
  // 국면 배지: 레인지 좌상단에 시안 톤으로 또렷하게 (기본 노출 — 가이드 토글과 무관하게 항상 보여야 하는 핵심 정보)
  badges.unshift(labelBadge(x + 6, Math.max(20, y + 6), phaseHintLabel(context.analysis.wyckoff_phase?.phase), context.palette.color("blue", 0.22), rangeStroke, context.palette.color("text"), 128));
  return { zones, shapes, badges };
}

function onchainOverlayNodes(context: OverlayContext): OverlayGroup {
  const group: OverlayGroup = { zones: [], shapes: [], badges: [] };
  const markers = context.analysis.onchain?.markers ?? [];
  if (!context.analysis.onchain?.supported || !markers.length) {
    group.badges.push(minimalFloatingLabel(context, "온체인 관측 없음", 18, 28, "text"));
    return group;
  }
  for (const marker of markers.slice(0, 8)) {
    const nodes = onchainMarkerNodes(context, marker);
    group.shapes.push(...nodes.shapes);
    group.badges.push(...nodes.badges);
  }
  return group;
}

function onchainMarkerNodes(context: OverlayContext, marker: OnchainChartMarker): { shapes: string[]; badges: string[] } {
  const candle = context.analysis.candles.find((item) => item.time === marker.time);
  const x = context.chart.timeScale().timeToCoordinate(marker.time as Time);
  if (!candle || x === null) return { shapes: [], badges: [] };
  const above = marker.kind === "entry" ? marker.side === "short" : marker.side === "long";
  const anchorPrice = above ? candle.high : candle.low;
  const priceY = context.series.priceToCoordinate(anchorPrice);
  if (priceY === null) return { shapes: [], badges: [] };
  const radius = marker.size_tier === 3 ? 6 : marker.size_tier === 2 ? 5 : 4;
  const stem = marker.size_tier === 3 ? 18 : 14;
  const y = clamp(priceY + (above ? -stem : stem), 16, context.height - 18);
  const green = context.palette.color("green", marker.emphasized ? 0.98 : 0.54);
  const red = context.palette.color("red", marker.emphasized ? 0.98 : 0.54);
  const strokeWidth = marker.emphasized ? 2 : 1.2;
  const title = onchainMarkerTitle(marker);
  const shapes: string[] = [];
  shapes.push(`<line x1="${x}" x2="${x}" y1="${priceY}" y2="${y}" stroke="${marker.side === "long" ? green : red}" stroke-width="1" stroke-dasharray="2 3" opacity="0.72" />`);
  if (marker.event === "flip") {
    shapes.push(
      `<g class="onchainMarker ${marker.emphasized ? "validated" : "candidate"}" data-testid="onchain-marker"><path d="M ${x - radius} ${y} L ${x} ${y - radius} L ${x + radius} ${y} L ${x} ${y + radius} Z" fill="${green}" stroke="${red}" stroke-width="${strokeWidth}" /><title>${escapeSvgText(title)}</title></g>`
    );
  } else {
    const color = marker.side === "long" ? green : red;
    const fill = marker.kind === "exit" ? context.palette.color("panel", 0.88) : color;
    shapes.push(
      `<g class="onchainMarker ${marker.emphasized ? "validated" : "candidate"}" data-testid="onchain-marker"><circle cx="${x}" cy="${y}" r="${radius}" fill="${fill}" stroke="${color}" stroke-width="${strokeWidth}" /><title>${escapeSvgText(title)}</title></g>`
    );
  }
  const label = onchainOperationLabel(marker);
  const labelX = clamp(x + radius + 4, 8, context.right - 54);
  const labelY = clamp(y + 3, 10, context.height - 10);
  const badges = [
    `<g class="onchainMarkerLabel ${marker.emphasized ? "validated" : "candidate"}"><title>${escapeSvgText(title)}</title><text x="${labelX}" y="${labelY}" fill="${marker.side === "long" ? green : red}" font-size="9" font-weight="700" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(label)}</text></g>`
  ];
  return { shapes, badges };
}

function onchainMarkerTitle(marker: OnchainChartMarker): string {
  return marker.items.map((item) => (
    `${item.wallet_label} (${item.wallet_address.slice(0, 6)}…${item.wallet_address.slice(-4)}) · ${item.side === "long" ? "롱" : "숏"} ${formatCompactNumber(item.size_usd)} · ${item.event === "open" ? "진입" : item.event === "increase" ? "증액" : item.event === "reduce" ? "감액" : item.event === "close" ? "청산" : "전환"} · 가격 ${item.entry_px ? formatPrice(item.entry_px) : "-"} · 미실현 ${item.unrealized_pnl === null ? "-" : formatCompactNumber(item.unrealized_pnl)} · ${item.accuracy_label} · 별칭은 추정`
  )).join("\n");
}

function onchainOperationLabel(marker: OnchainChartMarker): string {
  const side = marker.side === "long" ? "L" : "S";
  const operation = marker.kind === "entry" ? "+" : "−";
  return `${side}${operation}${marker.count > 1 ? `×${marker.count}` : ""}`;
}


function unconfirmedWyckoffNodes(): OverlayGroup {
  return { zones: [], shapes: [], badges: [] };
}

function wyckoffEventMarker(context: OverlayContext, marker: WyckoffMarker): string[] {
  const markerX = context.chart.timeScale().timeToCoordinate(marker.time as Time);
  const markerY = context.series.priceToCoordinate(marker.price);
  if (markerX === null || markerY === null) return [];
  const upper = marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow");
  const tone = upper ? "invalidation" : "takeProfit";
  const label = `${eventShortLabel(marker)} · ${Math.round(marker.confidence)}`;
  return [
    `<g><circle cx="${markerX}" cy="${markerY}" r="5" fill="${context.palette.flag(tone, 0.95)}" stroke="${context.palette.color("text", 0.92)}" stroke-width="1.4" /><title>${escapeSvgText(label)}</title></g>`
  ];
}

function wyckoffEventBadge(
  context: OverlayContext,
  marker: WyckoffMarker,
  range: { top: number; bottom: number; x: number; width: number },
  index: number,
  count = 1
): string[] {
  const markerX = context.chart.timeScale().timeToCoordinate(marker.time as Time);
  const markerY = context.series.priceToCoordinate(marker.price);
  if (markerX === null || markerY === null) return [];
  const upper = marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow");
  const labelY = upper ? range.top - 28 - (index % 2) * 16 : range.bottom + 18 + (index % 2) * 16;
  const labelX = clamp(markerX - 34 + (index % 3) * 18, range.x, range.x + range.width - 92);
  const badgeText = `${upper ? "⤓" : "⤒"} ${eventShortLabel(marker)}${count > 1 ? ` ×${count}` : ""} · ${Math.round(marker.confidence)}`;
  const tone = upper ? "invalidation" : "takeProfit";
  // 배지는 항상 보여야 하는 핵심 콘텐츠(가이드 토글과 무관) — 배경을 톤 컬러로 살짝 채워 검은 차트 배경과 확실히 대비되게 한다.
  return [
    `<g><polyline points="${markerX},${markerY} ${markerX},${upper ? range.top : range.bottom} ${labelX + 10},${labelY}" fill="none" stroke="${context.palette.flag(tone, 0.7)}" stroke-width="1.2" />${labelBadge(labelX, labelY - 11, badgeText, context.palette.flag(tone, 0.24), context.palette.flag(tone, 0.92), context.palette.color("text"), 96)}</g>`
  ];
}

function groupWyckoffBadges(context: OverlayContext, events: WyckoffMarker[]): Array<{ marker: WyckoffMarker; count: number }> {
  const groups: Array<{ marker: WyckoffMarker; count: number; x: number; y: number; label: string }> = [];
  for (const marker of events) {
    const x = context.chart.timeScale().timeToCoordinate(marker.time as Time);
    const y = context.series.priceToCoordinate(marker.price);
    if (x === null || y === null) continue;
    const label = eventShortLabel(marker);
    const previous = groups.at(-1);
    if (previous && previous.label === label && Math.abs(previous.x - x) <= 42 && Math.abs(previous.y - y) <= 10) {
      previous.count += 1;
      continue;
    }
    groups.push({ marker, count: 1, x, y, label });
  }
  return groups.map(({ marker, count }) => ({ marker, count }));
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

function harmonicCandidateNodes(): OverlayGroup {
  return { zones: [], shapes: [], badges: [] };
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

function liquidationClusterNodes(context: OverlayContext): string[] {
  const derivatives = context.analysis.derivatives;
  const coinglass = derivatives?.coinglass;
  if (coinglass?.source_status !== "ok") return [];
  const clusters = derivatives?.signals?.liquidation_clusters ?? [];
  const nodes: string[] = [];
  for (const cluster of clusters) {
    const price = numericClusterPrice(cluster);
    if (price === null) continue;
    const band = Math.max(averageTrueRange(context.analysis.candles) * 0.2, price * 0.0015);
    const y1 = context.series.priceToCoordinate(price + band);
    const y2 = context.series.priceToCoordinate(price - band);
    if (y1 === null || y2 === null) continue;
    const y = Math.min(y1, y2);
    const height = Math.max(4, Math.abs(y2 - y1));
    const x = Math.max(18, context.right - 190);
    nodes.push(`<rect x="${x}" y="${y}" width="${Math.max(20, context.right - x)}" height="${height}" rx="3" fill="${context.palette.zone("liquidationCluster", 0.14)}" stroke="${context.palette.color("purple", 0.52)}" stroke-width="1" />`);
    nodes.push(labelBadge(x + 6, Math.max(16, y - 20), `청산 밀집대 추정 ${formatPrice(price)}`, context.palette.color("panel", 0.78), context.palette.color("purple", 0.78), context.palette.color("text"), 138));
  }
  return nodes;
}

function positionOverlayNodes(context: OverlayContext, position: PositionChartOverlay): string[] {
  const rawY = context.series.priceToCoordinate(position.entryPrice);
  if (rawY === null) return [];
  const offscreen = rawY < 18 || rawY > context.height - 42;
  const y = Math.max(18, Math.min(context.height - 42, rawY));
  const labelX = 18;
  const labelY = Math.max(24, Math.min(context.height - 42, y - 30));
  const sideLabel = position.direction === "long" ? "롱" : "숏";
  const pnlText = formatSignedPercent(position.pnlPercent);
  const pnlColor = context.palette.flag(position.pnlPercent >= 0 ? "takeProfit" : "invalidation");
  const sideWidth = 106;
  const pnlWidth = Math.max(58, Math.min(86, pnlText.length * 7 + 18));
  const totalWidth = sideWidth + pnlWidth;
  const amountText = position.pnlAmount === null ? "손익 금액 미수신" : `${formatSignedNumber(position.pnlAmount)} USDT`;
  const detailText = `${formatCompactQuantity(position.quantity)} · ${amountText} · 진입 ${formatPrice(position.entryPrice)}`;
  const rangeMarker = offscreen ? (rawY < 18 ? " ↑ 범위 위" : " ↓ 범위 아래") : "";
  const nodes = [
    `<line data-testid="position-entry-line" data-offscreen="${offscreen}" x1="${labelX}" x2="${context.right}" y1="${y}" y2="${y}" stroke="${context.palette.flag("entry", 0.92)}" stroke-width="1.5" stroke-dasharray="6 4" />`,
    `<g><rect x="${labelX}" y="${labelY}" width="${totalWidth}" height="24" rx="5" fill="${context.palette.color("panel", 0.68)}" stroke="${context.palette.flag("entry", 0.84)}" stroke-width="1" />`,
    `<rect x="${labelX}" y="${labelY}" width="${sideWidth}" height="24" rx="5" fill="${context.palette.flag("entry", 0.92)}" />`,
    `<text x="${labelX + 8}" y="${labelY + 16}" fill="${context.palette.color("panel")}" font-size="10.5" font-weight="750" font-family="SF Mono, Monaco, Consolas, monospace">내 진입 ${formatPrice(position.entryPrice)}${rangeMarker}</text>`,
    `<text x="${labelX + sideWidth + 9}" y="${labelY + 16}" fill="${pnlColor}" font-size="10.5" font-weight="750" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(pnlText)}</text>`,
    `<title>${escapeSvgText(detailText)}</title></g>`
  ];
  const openedTime = position.openedAt ? Math.floor(new Date(position.openedAt).getTime() / 1000) : null;
  const entryCandle = openedTime ? nearestCandleAtOrAfter(context.analysis.candles, openedTime) : null;
  if (entryCandle && !offscreen) {
    const x = context.chart.timeScale().timeToCoordinate(entryCandle.time as Time);
    if (x !== null && x > 0 && x < context.right) {
      nodes.push(`<line x1="${x}" x2="${x}" y1="${Math.max(0, y - 52)}" y2="${Math.min(context.height, y + 52)}" stroke="${context.palette.flag("entry", 0.65)}" stroke-width="1" stroke-dasharray="${chartTheme.stroke.minor.dash}" />`);
      nodes.push(`<path d="M ${x - 7} ${y - 11} L ${x + 7} ${y - 11} L ${x} ${y - 1} Z" fill="${context.palette.flag("entry", 0.92)}" />`);
      nodes.push(`<text x="${x + 8}" y="${Math.max(16, y - 14)}" fill="${context.palette.flag("entry", 0.92)}" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">진입</text>`);
    }
  } else if (position.openedAt) {
    nodes.push(`<path d="M ${labelX} ${y} L ${labelX + 9} ${y - 5} L ${labelX + 9} ${y + 5} Z" fill="${context.palette.flag("entry", 0.92)}" />`);
    nodes.push(`<text x="${labelX + 12}" y="${Math.min(context.height - 8, y + 15)}" fill="${context.palette.flag("entry", 0.86)}" font-size="9.5" font-family="SF Mono, Monaco, Consolas, monospace">진입 시점은 표시 범위 이전 · ${sideLabel} ${position.leverage}x</text>`);
  }
  return nodes;
}

function actionPriceFlags(context: OverlayContext): PriceFlag[] {
  const flags: PriceFlag[] = [];
  const entryPrice = context.positionOverlay?.entryPrice ?? context.analysis.entry_price;
  if (isFinitePrice(entryPrice)) {
    flags.push({ label: `${context.positionOverlay ? "내 진입" : "진입"} ${formatPrice(entryPrice)}`, price: entryPrice, kind: "entry", priority: 0 });
  }
  if (isFinitePrice(context.analysis.mark_price)) {
    flags.push({ label: `현재 ${formatPrice(context.analysis.mark_price)}`, price: context.analysis.mark_price, kind: "mark", priority: 1 });
  }
  const invalidation = planInvalidation(context.plan, context.analysis);
  if (invalidation && isFinitePrice(invalidation.price)) {
    flags.push({ label: `무효화 ${formatPrice(invalidation.price)}`, price: invalidation.price, kind: "invalidation", priority: 2 });
  }
  firstTwoTakeProfits(context.plan).slice(0, context.minimal ? 1 : 2).forEach((target, index) => {
    if (isFinitePrice(target.price)) {
      flags.push({ label: `익절${index + 1} ${formatPrice(target.price)}`, price: target.price, kind: "takeProfit", priority: 3 + index });
    }
  });
  if (context.minimal) return flags.filter((flag) => Number.isFinite(flag.price)).slice(0, 4);
  const watchPrice = firstWatchPrice(context.plan?.watch_triggers, isFinitePrice(context.analysis.mark_price) ? context.analysis.mark_price : null);
  if (isFinitePrice(watchPrice)) {
    flags.push({ label: `감시 ${formatPrice(watchPrice)}`, price: watchPrice, kind: "watch", priority: 6 });
  }
  if (context.layers.ta.includes("volume_profile") && isFinitePrice(context.analysis.volume_profile.poc_price)) {
    flags.push({ label: `POC ${formatPrice(context.analysis.volume_profile.poc_price)}`, price: context.analysis.volume_profile.poc_price, kind: "poc", priority: 7 });
  }
  return flags.filter((flag) => Number.isFinite(flag.price));
}

function isFinitePrice(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function stackFlags(flags: PriceFlag[], height: number, series?: OverlayContext["series"]): Array<PriceFlag & { y: number }> {
  const positioned = flags
    .map((flag) => ({ ...flag, y: series?.priceToCoordinate(flag.price) ?? null }))
    .filter((flag): flag is PriceFlag & { y: number } => flag.y !== null)
    .sort((left, right) => left.y - right.y || left.priority - right.priority);
  const merged: Array<PriceFlag & { y: number }> = [];
  for (const flag of positioned) {
    const collision = merged.find((item) => Math.abs(item.y - flag.y) <= 12);
    if (!collision) {
      merged.push({ ...flag });
      continue;
    }
    const names = [collision.label.split(" ")[0], flag.label.split(" ")[0]].filter((name, index, items) => items.indexOf(name) === index);
    collision.label = names.join("·");
    collision.priority = Math.min(collision.priority, flag.priority);
    collision.y = (collision.y + flag.y) / 2;
  }
  const visible = merged.slice(0, 6);
  if (merged.length > 6) {
    const hidden = merged.slice(6);
    visible.push({ label: `더보기 ${hidden.length}개`, price: hidden[0].price, kind: "watch", priority: 99, y: hidden[0].y });
  }
  const gap = 26;
  for (let index = 1; index < visible.length; index += 1) {
    if (visible[index].y - visible[index - 1].y < gap) {
      visible[index].y = visible[index - 1].y + gap;
    }
  }
  for (let index = visible.length - 1; index >= 0; index -= 1) {
    visible[index].y = clamp(visible[index].y, 14, height - 16);
    if (index < visible.length - 1 && visible[index + 1].y - visible[index].y < gap) {
      visible[index].y = Math.max(14, visible[index + 1].y - gap);
    }
  }
  return visible.sort((left, right) => left.priority - right.priority);
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

function firstWatchPrice(triggers: PositionWatchTrigger[] | undefined, referencePrice: number | null): number | null {
  // 조건 문구에는 가격이 아닌 숫자(OI %, 펀딩률, 밀집대 % 등)가 섞인다.
  // 현재가 대비 그럴듯한 범위(0.2x~5x)의 숫자만 가격으로 인정한다.
  if (!Number.isFinite(referencePrice) || referencePrice === null || referencePrice <= 0) return null;
  for (const trigger of triggers ?? []) {
    const matches = trigger.condition.match(/-?\d+(?:\.\d+)?/g) ?? [];
    for (const raw of matches) {
      const value = Number(raw);
      if (Number.isFinite(value) && value >= referencePrice * 0.2 && value <= referencePrice * 5) return value;
    }
  }
  return null;
}

function directionDistancePct(price: number, entry: number, direction: "long" | "short"): number {
  if (!Number.isFinite(price) || !Number.isFinite(entry) || entry === 0) return 0;
  const raw = ((price - entry) / entry) * 100;
  return direction === "long" ? raw : -raw;
}

function derivativeSeriesPoints(analysis: PositionChartAnalysis, field: "open_interest"): Array<{ time: Time; value: number }> {
  return (analysis.derivatives?.metrics ?? [])
    .filter((metric) => metric.source === "bitget" && typeof metric[field] === "number")
    .map((metric) => ({ time: Math.floor(new Date(metric.as_of).getTime() / 1000) as Time, value: metric[field] as number }))
    .sort((left, right) => Number(left.time) - Number(right.time));
}

function derivativeFundingPoints(analysis: PositionChartAnalysis, palette: ResolvedChartPalette): HistogramData[] {
  return (analysis.derivatives?.metrics ?? [])
    .filter((metric) => metric.source === "bitget" && typeof metric.funding === "number")
    .map((metric) => {
      const value = (metric.funding as number) * 100;
      return {
        time: Math.floor(new Date(metric.as_of).getTime() / 1000) as Time,
        value,
        color: value >= 0 ? palette.color("green", 0.36) : palette.color("red", 0.36)
      };
    })
    .sort((left, right) => Number(left.time) - Number(right.time));
}

function numericClusterPrice(cluster: Record<string, unknown>): number | null {
  for (const key of ["price", "mid", "level"]) {
    const value = cluster[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string" && Number.isFinite(Number(value))) return Number(value);
  }
  return null;
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

function labelBadge(x: number, y: number, text: string, fill: string, stroke: string, textColor: string, width?: number, className?: string): string {
  const displayText = truncateSvgLabel(text, width && width <= 96 ? 14 : 22);
  const safeText = escapeSvgText(displayText);
  const rectWidth = width ?? Math.max(48, Math.min(150, displayText.length * 6.8 + 14));
  const openGroup = className ? `<g class="${className}">` : "<g>";
  return [
    openGroup,
    `<rect x="${x}" y="${y}" width="${rectWidth}" height="20" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="1" />`,
    `<text x="${x + 7}" y="${y + 13.5}" fill="${textColor}" font-size="9.5" font-family="SF Mono, Monaco, Consolas, monospace">${safeText}</text>`,
    "</g>"
  ].join("");
}

function truncateSvgLabel(text: string, maxLength: number): string {
  return text.length > maxLength ? `${text.slice(0, Math.max(1, maxLength - 1))}…` : text;
}

function eventShortLabel(marker: WyckoffMarker): string {
  const normalized = localizeMarketCodes(marker.label || marker.type);
  const glossary = taShortLabel(marker.label || marker.type);
  return (glossary || normalized)
    .replace("스프링 후보", "스프링")
    .replace("거래량 급증", "급증")
    .replace("클라이맥스 후보", "클라이맥스");
}

function liquidityPoolLabel(pool: LiquidityPool): string {
  const side = pool.side === "buy_side" ? "상단 풀" : "하단 풀";
  const kind = pool.kind === "eqh"
    ? "EQH"
    : pool.kind === "eql"
      ? "EQL"
      : pool.kind === "old_high"
        ? "전고"
        : pool.kind === "old_low"
          ? "전저"
          : String(pool.kind).toUpperCase();
  const touches = pool.touch_count ?? pool.touches ?? 1;
  return `${side}(${kind} ${touches}터치)`;
}

function liquiditySweepLabel(sweep: LiquiditySweep): string {
  const side = sweep.side === "buy_side" ? "고점 스윕" : "저점 스윕";
  const arrow = sweep.side === "buy_side" ? "⇧" : "⇩";
  return `${arrow} ${side} · ${sweep.grade}`;
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
