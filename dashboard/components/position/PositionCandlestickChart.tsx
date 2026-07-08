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
import type {
  ChartCandle,
  ChartPriceLevel,
  LiquidityPool,
  LiquiditySweep,
  PositionActionPlan,
  PositionActionPlanItem,
  PositionChartAnalysis,
  PositionWatchTrigger,
  WyckoffMarker
} from "@/lib/api";
import {
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
import { formatPrice } from "@/lib/format";
import { localizeMarketCodes, phaseHintLabel, sourceLabel, timeframeLabel } from "@/lib/labels/marketStateLabels";
import { splitWyckoffEvents, taGlossaryEntry, taShortLabel } from "@/lib/labels/taGlossary";
import { priceLinesForAnalysis, type ChartPriceLine } from "./PriceLevelOverlay";
import type { PositionChartOverlay } from "./PositionChart";
import { VolumePanel } from "./VolumePanel";

const LABEL_MERGE_PX = 8;
const AXIS_GUTTER = 82;
const GUIDE_STORAGE_KEY = "fce.chartGuide.enabled";

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
  minimalEvidence = null
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
  intentZoneSelector?: {
    enabled: boolean;
    draft: { lower: number | null; upper: number | null };
    onDraftChange: (lower: number, upper: number) => void;
    onComplete: (lower: number, upper: number) => void;
  };
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const overlayRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const priceAtYRef = useRef<((y: number) => number | null) | null>(null);
  const viewportRef = useRef<{ key: string; locked: boolean; range: LogicalRange | null }>({ key: "", locked: false, range: null });
  const [harmonicIndex, setHarmonicIndex] = useState(0);
  const [guideOpen, setGuideOpen] = useState(false);
  const [dragStartPrice, setDragStartPrice] = useState<number | null>(null);
  const [dragStartY, setDragStartY] = useState<number | null>(null);
  const [zoneBand, setZoneBand] = useState<{ top: number; bottom: number } | null>(null);
  const validation = useMemo(() => validateCandles(analysis.candles), [analysis.candles]);
  const effectiveLayers = useMemo(
    () => (layerMode === "minimal" ? minimalLayersForEvidence(minimalEvidence) : layers),
    [layerMode, minimalEvidence, layers]
  );
  const priceLines = useMemo(
    () => (validation.valid ? priceLinesForAnalysis(analysis) : []),
    [analysis, validation.valid]
  );
  const lastCandle = validation.candles.at(-1);
  const averageVolume = validation.valid ? average(validation.candles.map((candle) => candle.volume)) : 0;
  const harmonicFocused = layerMode === "pro" && effectiveLayers.ta.includes("harmonic");
  const harmonicPatterns = useMemo(
    () => [...analysis.harmonic_patterns].sort((left, right) => right.confidence - left.confidence),
    [analysis.harmonic_patterns]
  );
  const activeHarmonic = harmonicPatterns.length ? harmonicPatterns[((harmonicIndex % harmonicPatterns.length) + harmonicPatterns.length) % harmonicPatterns.length] : null;
  const guideLayer = activeGuideLayer(effectiveLayers);
  const intentZoneDraft = intentZoneSelector?.draft ?? null;
  const viewportKey = `${analysis.position_id}:${analysis.timeframe}`;

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
    setGuideOpen(window.localStorage.getItem(GUIDE_STORAGE_KEY) === "true");
  }, []);

  function toggleGuide() {
    setGuideOpen((current) => {
      const next = !current;
      window.localStorage.setItem(GUIDE_STORAGE_KEY, String(next));
      return next;
    });
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

    const minimalCandles = layerMode === "minimal";
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: minimalCandles ? palette.color("amber", 0.98) : palette.color("green"),
      downColor: minimalCandles ? palette.color("red", 0.98) : palette.color("red"),
      wickUpColor: minimalCandles ? palette.color("amber", 0.96) : palette.color("green", 0.92),
      wickDownColor: minimalCandles ? palette.color("red", 0.96) : palette.color("red", 0.92),
      borderUpColor: minimalCandles ? palette.color("amber", 1) : palette.color("green", 0.9),
      borderDownColor: minimalCandles ? palette.color("red", 1) : palette.color("red", 0.9),
      borderVisible: true,
      priceLineVisible: false,
      lastValueVisible: false
    });
    priceAtYRef.current = (y: number) => {
      const price = candleSeries.coordinateToPrice(y);
      return typeof price === "number" && Number.isFinite(price) ? price : null;
    };

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
      color: effectiveLayers.flow ? volumeColorForCandle(analysis, candle, palette) : simpleVolumeColor(candle, palette)
    }));
    volumeSeries.setData(volumeData);

    if (effectiveLayers.flow) {
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

    if (effectiveLayers.ta.includes("indicators")) {
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

    const spikeMarkers = effectiveLayers.flow
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

    const storedViewport = viewportRef.current;
    if (storedViewport.key === viewportKey && storedViewport.locked && storedViewport.range) {
      chart.timeScale().setVisibleLogicalRange(storedViewport.range);
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

    const drawOverlay = () => renderTaOverlay(
      overlay,
      container,
      candleSeries,
      chart,
      analysis,
      plan,
      effectiveLayers,
      activeHarmonic,
      positionOverlay,
      highlightPrice,
      palette,
      density,
      layerMode === "minimal" ? minimalEvidence : null
    );
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
      chart.remove();
    };
  }, [analysis, averageVolume, priceLines, plan, effectiveLayers, highlightPrice, activeHarmonic, positionOverlay, validation, density, layerMode, minimalEvidence, viewportKey]);

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
          <span className="positionChartTrendPill">{trendSummary}</span>
          <button className={`chartGuideButton ${guideOpen ? "active" : ""}`} onClick={toggleGuide} type="button" aria-pressed={guideOpen} title="해설 오버레이 켜기/끄기">
            해설
          </button>
        </div>
      </div>
      {layerMode === "pro" ? (
        <ChartLayerControls
          layers={layers}
          onToggleLayer={onToggleLayer}
        />
      ) : null}
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
      <div className={`positionChartCanvasFrame ${guideOpen ? "showOverlayGuides" : ""}`} data-testid="chart-canvas-frame">
        <div className="positionChartCanvas" data-testid="chart-canvas" ref={containerRef} />
        <svg className="volumeProfileOverlay" data-testid="chart-overlay" ref={overlayRef} aria-hidden="true" />
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
  return (
    <div className="taLayerToggle" role="group" aria-label="차트 레이어 선택">
      {CHART_LAYER_DEFS.map((layer) => (
        <button
          aria-pressed={layerActive(layers, layer.id)}
          className={layerActive(layers, layer.id) ? "active" : ""}
          data-testid={`chart-layer-${layer.id}`}
          key={layer.id}
          onClick={() => onToggleLayer(layer.id, true)}
          title={layer.description}
          type="button"
        >
          {layer.label}
        </button>
      ))}
      <small>여러 레이어 동시 선택 가능 · 해설은 별도 토글</small>
    </div>
  );
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
    <div className={`chartGuideLayer guide-${layer}`} aria-label="차트 읽기 가이드">
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
  minimalEvidence: MinimalChartEvidence | null = null
) {
  if (!svg) return;
  const width = container.clientWidth;
  const height = container.clientHeight;
  if (!width || !height) return;
  const zones: string[] = [];
  const shapes: string[] = [];
  const badges: string[] = [];
  const right = overlayRight(width);
  const context: OverlayContext = { series, chart, analysis, plan, layers, positionOverlay, highlightPrice, palette, width, height, right, density, minimal: Boolean(minimalEvidence) };

  if (minimalEvidence) {
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
    if (layers.ta.includes("harmonic") && activeHarmonic) {
      const harmonic = harmonicPatternNodes(context, activeHarmonic);
      zones.push(...harmonic.zones);
      shapes.push(...harmonic.shapes);
      badges.push(...harmonic.badges);
    }
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
  density: Density;
  minimal: boolean;
};

type OverlayGroup = { zones: string[]; shapes: string[]; badges: string[] };

function overlayRight(width: number): number {
  return Math.max(24, width - AXIS_GUTTER);
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
    return minimalDashedPriceLine(context, marker.price, `${eventShortLabel(marker)} ${Math.round(marker.confidence)}`, marker.side === "distribution" ? "red" : "amber");
  }
  return [
    ...minimalDashedPriceLine(context, range.resistance.price, "레인지 상단", "red", "top"),
    ...minimalDashedPriceLine(context, range.support.price, "레인지 하단", "amber", "bottom")
  ];
}

function wyckoffRangeBox(context: OverlayContext, range: NonNullable<PositionChartAnalysis["wyckoff_range"]>): { top: number; bottom: number; x: number; width: number } {
  const top = context.series.priceToCoordinate(range.resistance.price) ?? 0;
  const bottom = context.series.priceToCoordinate(range.support.price) ?? context.height;
  const rawX1 = context.chart.timeScale().timeToCoordinate(range.start_time as Time);
  const rawX2 = context.chart.timeScale().timeToCoordinate(range.end_time as Time);
  const x1 = rawX1 ?? 0;
  const x2 = rawX2 ?? context.width;
  return { top, bottom, x: Math.min(x1, x2), width: Math.max(12, Math.abs(x2 - x1)) };
}

function minimalHarmonicNodes(context: OverlayContext, pattern: PositionChartAnalysis["harmonic_patterns"][number]): string[] {
  const przTop = context.series.priceToCoordinate(pattern.prz.high);
  const przBottom = context.series.priceToCoordinate(pattern.prz.low);
  if (przTop === null || przBottom === null) return [];
  const price = (pattern.prz.high + pattern.prz.low) / 2;
  return minimalDashedPriceLine(context, price, `반전 후보 ${Math.round(pattern.confidence)}`, "amber");
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
  if (plain.includes("스윕") || plain.includes("청소")) return plain.includes("고점") || plain.includes("상위") ? "고점 청소" : "저점 청소";
  if (plain.includes("UTAD")) return "UTAD";
  if (plain.includes("Spring") || plain.includes("스프링")) return "스프링";
  if (plain.includes("반전")) return "반전 후보";
  if (plain.includes("지지")) return "지지 확인";
  if (plain.includes("저항")) return "저항 확인";
  if (fallback === "support") return "지지 확인";
  if (fallback === "resistance") return "저항 확인";
  return plain || "수급 근거";
}

function liquiditySweepMinimalLabel(sweep: LiquiditySweep): string {
  if (sweep.side === "buy_side" || sweep.pool_kind.includes("high")) return "고점 청소";
  return "저점 청소";
}

function liquidityPoolMinimalLabel(pool: LiquidityPool): string {
  if (pool.side === "buy_side" || pool.kind.includes("high") || pool.kind === "eqh") return "고점 유동성";
  return "저점 유동성";
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
  const rr = riskPct < 0 && profitPct > 0 ? profitPct / Math.abs(riskPct) : null;
  const label = `현재 기준 ${formatSignedPercent(profitPct)} / ${formatSignedPercent(riskPct)}${rr ? ` · R:R ${rr.toFixed(1)}` : ""}`;
  return [
    `<rect x="${x}" y="${profitY}" width="${width}" height="${profitHeight}" fill="${context.palette.zone("profit")}" />`,
    `<rect x="${x}" y="${riskY}" width="${width}" height="${riskHeight}" fill="${context.palette.zone("risk")}" />`,
    `<line x1="${x}" x2="${context.right}" y1="${entryY}" y2="${entryY}" stroke="${context.palette.flag("entry", 0.54)}" stroke-width="1" stroke-dasharray="${chartTheme.stroke.minor.dash}" />`,
    `<line x1="${x}" x2="${context.right}" y1="${markY}" y2="${markY}" stroke="${context.palette.flag("mark", 0.72)}" stroke-width="${chartTheme.stroke.major.width}" />`,
    labelBadge(context.right - 154, Math.max(18, Math.min(profitY, riskY) - 26), label, context.palette.color("panel", 0.82), context.palette.flag("takeProfit", 0.88), context.palette.color("text"), 148)
  ];
}

function priceFlagNodes(context: OverlayContext): string[] {
  const flags = stackFlags(actionPriceFlags(context), context.height, context.series);
  return flags.map((flag) => {
    const highlighted = context.highlightPrice !== null && Math.abs(flag.price - context.highlightPrice) <= Math.abs(flag.price) * 1e-9 + 1e-12;
    const displayLabel = truncateSvgLabel(flag.label, highlighted ? 18 : 15);
    const width = Math.max(62, Math.min(124, displayLabel.length * 7 + 16 + (highlighted ? 10 : 0)));
    const x = context.right + 4 - (highlighted ? 6 : 0);
    const fill = context.palette.flag(flag.kind, highlighted ? 1 : 0.92);
    const stroke = context.palette.color("text", highlighted ? 0.8 : 0.24);
    const text = flag.kind === "mark" ? context.palette.color("panel") : context.palette.color("panel");
    return [
      `<rect x="${x}" y="${flag.y - 10}" width="${width}" height="20" rx="4" fill="${fill}" stroke="${stroke}" stroke-width="${highlighted ? 1.6 : 1}" />`,
      `<text x="${x + 7}" y="${flag.y + 3.5}" fill="${text}" font-size="${highlighted ? 10.5 : 9.5}" font-weight="${highlighted ? 750 : 650}" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(displayLabel)}</text>`
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
  const badges = sweeps.flatMap((sweep, index) => liquiditySweepBadgeNode(context, sweep, index));
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

function liquiditySweepBadgeNode(context: OverlayContext, sweep: LiquiditySweep, index: number): string[] {
  const x = context.chart.timeScale().timeToCoordinate(sweep.timestamp as Time);
  const y = context.series.priceToCoordinate(sweep.pool_price ?? sweep.price);
  if (x === null || y === null) return [];
  const buySide = sweep.side === "buy_side";
  const tone = buySide ? "invalidation" : "takeProfit";
  const label = liquiditySweepLabel(sweep);
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
  if (!range) return { zones: [], shapes: [], badges: [] };
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
  const phaseLabels = ["A", "B", "C", "D", "E"];
  const shapes = phaseLabels.flatMap((label, index) => {
    const phaseX = x + (width / phaseLabels.length) * index;
    const labelX = phaseX + width / phaseLabels.length / 2 - 8;
    return [
      index > 0 ? `<line x1="${phaseX}" x2="${phaseX}" y1="${y}" y2="${y + height}" stroke="${context.palette.color("blue", 0.32)}" stroke-width="1" stroke-dasharray="4 4" />` : "",
      `<text x="${labelX}" y="${Math.max(12, y - 6)}" fill="${context.palette.color("blue", 0.82)}" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">Phase ${label}</text>`
    ];
  });
  const events = splitWyckoffEvents(context.analysis.wyckoff_markers, context.analysis.wyckoff_markers_low_confidence).events;
  shapes.push(...events.flatMap((marker) => wyckoffEventMarker(context, marker)));
  const badges = events.flatMap((marker, index) => wyckoffEventBadge(context, marker, { top, bottom, x, width }, index));
  // 국면 배지: 레인지 좌상단에 시안 톤으로 또렷하게 (기본 노출 — 가이드 토글과 무관하게 항상 보여야 하는 핵심 정보)
  badges.unshift(labelBadge(x + 6, Math.max(20, y + 6), phaseHintLabel(context.analysis.wyckoff_phase?.phase), context.palette.color("blue", 0.22), rangeStroke, context.palette.color("text"), 128));
  return { zones, shapes, badges };
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
  index: number
): string[] {
  const markerX = context.chart.timeScale().timeToCoordinate(marker.time as Time);
  const markerY = context.series.priceToCoordinate(marker.price);
  if (markerX === null || markerY === null) return [];
  const upper = marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow");
  const labelY = upper ? range.top - 28 - (index % 2) * 16 : range.bottom + 18 + (index % 2) * 16;
  const labelX = clamp(markerX - 34 + (index % 3) * 18, range.x, range.x + range.width - 92);
  const badgeText = `${upper ? "⤓" : "⤒"} ${eventShortLabel(marker)} · ${Math.round(marker.confidence)}`;
  const tone = upper ? "invalidation" : "takeProfit";
  // 배지는 항상 보여야 하는 핵심 콘텐츠(가이드 토글과 무관) — 배경을 톤 컬러로 살짝 채워 검은 차트 배경과 확실히 대비되게 한다.
  return [
    `<g><polyline points="${markerX},${markerY} ${markerX},${upper ? range.top : range.bottom} ${labelX + 10},${labelY}" fill="none" stroke="${context.palette.flag(tone, 0.7)}" stroke-width="1.2" />${labelBadge(labelX, labelY - 11, badgeText, context.palette.flag(tone, 0.24), context.palette.flag(tone, 0.92), context.palette.color("text"), 96)}</g>`
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

function scenarioPathNodes(context: OverlayContext): string[] {
  const mark = context.analysis.mark_price;
  const target = firstTakeProfit(context.plan);
  const invalidation = planInvalidation(context.plan, context.analysis);
  if (!target || !invalidation || !isFinitePrice(mark) || !isFinitePrice(target.price) || !isFinitePrice(invalidation.price)) return [];
  const watchPrice = firstWatchPrice(context.plan?.watch_triggers, mark);
  const x = Math.max(18, context.right - 248);
  const y = 24;
  const width = Math.min(226, Math.max(172, context.right - x - 8));
  const rows = [
    { label: "익절 후보", value: target.price, tone: "takeProfit" as const, pct: directionDistancePct(target.price, mark, context.analysis.direction) },
    { label: "무효화", value: invalidation.price, tone: "invalidation" as const, pct: directionDistancePct(invalidation.price, mark, context.analysis.direction) },
    ...(watchPrice ? [{ label: "감시 가격", value: watchPrice, tone: "watch" as const, pct: directionDistancePct(watchPrice, mark, context.analysis.direction) }] : [])
  ];
  const height = 38 + rows.length * 18;
  return [
    `<g><rect x="${x}" y="${y}" width="${width}" height="${height}" rx="6" fill="${context.palette.color("panel", 0.74)}" stroke="${context.palette.color("neutral", 0.54)}" stroke-width="1" />`,
    `<text x="${x + 10}" y="${y + 17}" fill="${context.palette.color("text", 0.9)}" font-size="10.5" font-weight="750" font-family="SF Mono, Monaco, Consolas, monospace">조건 경로 · 예측 아님</text>`,
    `<text x="${x + 10}" y="${y + 33}" fill="${context.palette.color("muted", 0.86)}" font-size="9.5" font-family="SF Mono, Monaco, Consolas, monospace">현재 ${escapeSvgText(formatPrice(mark))} 기준</text>`,
    ...rows.map((row, index) => {
      const rowY = y + 52 + index * 18;
      const text = `${row.label} ${formatPrice(row.value)} ${formatSignedPercent(row.pct)}`;
      return [
        `<circle cx="${x + 12}" cy="${rowY - 3}" r="3" fill="${context.palette.flag(row.tone, 0.88)}" />`,
        `<text x="${x + 22}" y="${rowY}" fill="${context.palette.color("text", 0.9)}" font-size="9.5" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(truncateSvgLabel(text, 24))}</text>`
      ].join("");
    }),
    "</g>"
  ];
}

function positionOverlayNodes(context: OverlayContext, position: PositionChartOverlay): string[] {
  const y = context.series.priceToCoordinate(position.entryPrice);
  if (y === null || y < 18 || y > context.height - 42) return [];
  const labelX = 110;
  const labelY = Math.max(24, Math.min(context.height - 42, y - 12));
  const sideLabel = position.direction === "long" ? "롱" : "숏";
  const pnlText = formatSignedPercent(position.pnlPercent);
  const pnlColor = context.palette.flag(position.pnlPercent >= 0 ? "takeProfit" : "invalidation");
  const sideWidth = 56;
  const pnlWidth = Math.max(58, Math.min(86, pnlText.length * 7 + 18));
  const totalWidth = sideWidth + pnlWidth;
  const amountText = position.pnlAmount === null ? "손익 금액 미수신" : `${formatSignedNumber(position.pnlAmount)} USDT`;
  const detailText = `${formatCompactQuantity(position.quantity)} · ${amountText} · 진입 ${formatPrice(position.entryPrice)}`;
  const nodes = [
    `<g><rect x="${labelX}" y="${labelY}" width="${totalWidth}" height="24" rx="5" fill="${context.palette.color("panel", 0.68)}" stroke="${context.palette.flag("entry", 0.84)}" stroke-width="1" />`,
    `<rect x="${labelX}" y="${labelY}" width="${sideWidth}" height="24" rx="5" fill="${context.palette.flag("entry", 0.92)}" />`,
    `<text x="${labelX + 8}" y="${labelY + 16}" fill="${context.palette.color("panel")}" font-size="10.5" font-weight="750" font-family="SF Mono, Monaco, Consolas, monospace">${sideLabel} ${position.leverage}x</text>`,
    `<text x="${labelX + sideWidth + 9}" y="${labelY + 16}" fill="${pnlColor}" font-size="10.5" font-weight="750" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(pnlText)}</text>`,
    `<title>${escapeSvgText(detailText)}</title></g>`
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
  const flags: PriceFlag[] = [];
  if (isFinitePrice(context.analysis.entry_price)) {
    flags.push({ label: `진입 ${formatPrice(context.analysis.entry_price)}`, price: context.analysis.entry_price, kind: "entry", priority: 0 });
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
