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
import type { ChartCandle, PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { localizeMarketCodes, phaseHintLabel, sourceLabel, timeframeLabel } from "@/lib/labels/marketStateLabels";
import { hasHiddenStructureLevels, hiddenPriceLinesForAnalysis, priceLineColor, priceLinesForAnalysis, PriceLevelLegend } from "./PriceLevelOverlay";
import { VolumePanel } from "./VolumePanel";

export function PositionCandlestickChart({ analysis, trendSummary }: { analysis: PositionChartAnalysis; trendSummary: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const profileOverlayRef = useRef<SVGSVGElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const [showAllStructureLevels, setShowAllStructureLevels] = useState(false);
  const [showAllHarmonics, setShowAllHarmonics] = useState(false);
  const validation = useMemo(() => validateCandles(analysis.candles), [analysis.candles]);
  const priceLines = useMemo(() => (validation.valid ? priceLinesForAnalysis(analysis, showAllStructureLevels) : []), [analysis, showAllStructureLevels, validation.valid]);
  const hiddenPriceLines = useMemo(() => (validation.valid ? hiddenPriceLinesForAnalysis(analysis) : []), [analysis, validation.valid]);
  const hasAdditionalLevels = useMemo(() => hasHiddenStructureLevels(analysis), [analysis]);
  const lastCandle = validation.candles.at(-1);
  const averageVolume = validation.valid ? average(validation.candles.map((candle) => candle.volume)) : 0;

  useEffect(() => {
    if (!validation.valid || !containerRef.current || !validation.candles.length) return;
    const container = containerRef.current;
    const profileOverlay = profileOverlayRef.current;
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#000000" },
        textColor: "#8cab87",
        fontFamily: "SF Mono, Monaco, Consolas, monospace",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(72, 83, 70, 0.26)" },
        horzLines: { color: "rgba(72, 83, 70, 0.28)" }
      },
      localization: {
        locale: "ko-KR",
        priceFormatter: (price: number) => formatPrice(price),
        timeFormatter: (time: Time) => formatKoreanDateTime(Number(time))
      },
      rightPriceScale: {
        borderColor: "#485346",
        scaleMargins: { top: 0.08, bottom: 0.26 }
      },
      timeScale: {
        borderColor: "#485346",
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
          color: "rgba(221, 255, 220, 0.34)",
          labelBackgroundColor: "#181818"
        },
        vertLine: {
          color: "rgba(221, 255, 220, 0.24)",
          labelBackgroundColor: "#181818"
        }
      }
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "rgba(127, 238, 100, 0.74)",
      downColor: "rgba(238, 123, 128, 0.78)",
      wickUpColor: "#9cbf93",
      wickDownColor: "#ee7b80",
      borderUpColor: "#ddffdc",
      borderDownColor: "#ffadb1",
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
      color: volumeColorForCandle(analysis, candle)
    }));
    volumeSeries.setData(volumeData);

    const averageVolumeSeries = chart.addSeries(LineSeries, {
      priceScaleId: "volume",
      color: "rgba(174, 210, 164, 0.64)",
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
        color: "rgba(98, 207, 232, 0.72)",
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

    priceLines.forEach((line) => {
      candleSeries.createPriceLine({
        price: line.price,
        color: priceLineColor(line.kind, line.opacity),
        lineWidth: line.lineWidth,
        lineStyle: line.kind === "mark" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: false,
        title: ""
      });
    });

    const spikeMarkers = validation.candles
      .filter((candle) => candle.volume >= averageVolume * 1.8)
      .slice(-3)
      .map((candle) => ({
        time: candle.time as Time,
        position: "belowBar" as const,
        color: "#aed2a4",
        shape: "circle" as const,
        text: ""
      }));
    const wyckoffMarkers = analysis.wyckoff_markers.slice(-6).map((marker) => ({
      time: marker.time as Time,
      position: marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow") ? "aboveBar" as const : "belowBar" as const,
      color: marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow") ? "#ee7b80" : "#7fee64",
      shape: marker.side === "distribution" || marker.type.includes("utad") || marker.type.includes("sow") ? "arrowDown" as const : "arrowUp" as const,
      text: compactMarkerText(marker.label, marker.confidence)
    }));
    if (spikeMarkers.length || wyckoffMarkers.length) {
      createSeriesMarkers(candleSeries, [...wyckoffMarkers, ...spikeMarkers].sort((left, right) => Number(left.time) - Number(right.time)));
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
    const drawOverlay = () => renderVolumeProfileOverlay(profileOverlay, container, candleSeries, chart, analysis, showAllHarmonics);
    window.setTimeout(drawOverlay, 0);
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawOverlay);
    const resizeObserver = new ResizeObserver(drawOverlay);
    resizeObserver.observe(container);
    return () => {
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawOverlay);
      chart.remove();
    };
  }, [analysis, averageVolume, priceLines, showAllHarmonics, validation]);

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
      <PriceLevelLegend
        lines={priceLines}
        showAll={showAllStructureLevels}
        hasHiddenLevels={hasAdditionalLevels}
        onToggleAll={() => setShowAllStructureLevels((value) => !value)}
      />
      {hiddenPriceLines.length ? (
        <div className="chartOutOfRangeNotice">
          {hiddenPriceLines.map((line) => (
            <span key={`${line.kind}-${line.price}`}>{line.label}: {formatPrice(line.price)} · 현재 차트 범위 밖</span>
          ))}
        </div>
      ) : null}
      <div className="chartHoverReadout" ref={tooltipRef}>
        <strong>캔들 정보</strong>
        <span>시가·고가·저가·종가·거래량</span>
      </div>
      <div className="positionChartCanvasFrame">
        <div className="positionChartCanvas" ref={containerRef} />
        <svg className="volumeProfileOverlay" ref={profileOverlayRef} aria-hidden="true" />
      </div>
      <VolumePanel analysis={analysis} averageVolume={averageVolume} />
      {analysis.wyckoff_markers.length ? (
        <div className="wyckoffMarkerRail">
          {analysis.wyckoff_markers.map((marker) => (
            <span key={`${marker.type}-${marker.time}`} title={wyckoffMarkerTitle(marker)}>
              {localizeMarketCodes(marker.label)} · 신뢰도 {marker.confidence}
            </span>
          ))}
        </div>
      ) : (
        <div className="wyckoffMarkerRail muted">와이코프 마커 후보가 아직 없습니다.</div>
      )}
      {analysis.harmonic_patterns.length ? (
        <div className="harmonicPatternRail">
          {analysis.harmonic_patterns.slice(0, showAllHarmonics ? analysis.harmonic_patterns.length : 2).map((pattern) => (
            <span key={pattern.id} title={harmonicPatternTitle(pattern)}>
              {pattern.label} · {pattern.direction === "bearish" ? "하락 반전 PRZ" : "상승 반전 PRZ"} · 신뢰도 {pattern.confidence}
            </span>
          ))}
          {analysis.harmonic_patterns.length > 2 ? (
            <button className="priceLevelToggle" type="button" onClick={() => setShowAllHarmonics((value) => !value)}>
              {showAllHarmonics ? "핵심 패턴만" : "전체 패턴"}
            </button>
          ) : null}
        </div>
      ) : (
        <div className="harmonicPatternRail muted">하모닉 PRZ 후보가 아직 없습니다.</div>
      )}
    </>
  );
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

function volumeColorForCandle(analysis: PositionChartAnalysis, candle: ChartCandle): string {
  const bucket = analysis.trade_flow.buckets.find((item) => item.time === candle.time);
  if (bucket) {
    if (bucket.delta > 0) return "rgba(98, 207, 232, 0.38)";
    if (bucket.delta < 0) return "rgba(238, 123, 128, 0.38)";
    return "rgba(174, 210, 164, 0.28)";
  }
  return candle.close >= candle.open ? "rgba(127, 238, 100, 0.22)" : "rgba(238, 123, 128, 0.24)";
}

function renderVolumeProfileOverlay(
  svg: SVGSVGElement | null,
  container: HTMLDivElement,
  series: { priceToCoordinate(price: number): number | null },
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } },
  analysis: PositionChartAnalysis,
  showAllHarmonics: boolean
) {
  if (!svg) return;
  const width = container.clientWidth;
  const height = container.clientHeight;
  if (!width || !height) return;
  const bins = analysis.volume_profile.bins.filter((bin) => bin.volume > 0);
  const maxVolume = Math.max(...bins.map((bin) => bin.volume), 1);
  const axisGutter = 78;
  const profileWidth = Math.min(180, Math.max(92, width * 0.18));
  const right = Math.max(24, width - axisGutter);
  const valueHigh = series.priceToCoordinate(analysis.volume_profile.value_area_high);
  const valueLow = series.priceToCoordinate(analysis.volume_profile.value_area_low);
  const poc = series.priceToCoordinate(analysis.volume_profile.poc_price);
  const nodes: string[] = [];
  const rangeNodes = wyckoffRangeNodes(series, chart, analysis);
  nodes.push(...rangeNodes);
  nodes.push(...harmonicPatternNodes(series, chart, analysis, showAllHarmonics, Math.max(24, right - profileWidth - 10)));
  if (valueHigh !== null && valueLow !== null) {
    const y = Math.min(valueHigh, valueLow);
    const bandHeight = Math.max(2, Math.abs(valueLow - valueHigh));
    nodes.push(`<rect x="${right - profileWidth}" y="${y}" width="${profileWidth}" height="${bandHeight}" fill="rgba(174,210,164,0.07)" />`);
  }
  for (const bin of bins) {
    const top = series.priceToCoordinate(bin.price_high);
    const bottom = series.priceToCoordinate(bin.price_low);
    if (top === null || bottom === null) continue;
    const y = Math.min(top, bottom);
    const rowHeight = Math.max(2, Math.abs(bottom - top) - 1);
    const barWidth = Math.max(2, (bin.volume / maxVolume) * profileWidth);
    const x = right - barWidth;
    if (bin.buy_volume !== undefined || bin.sell_volume !== undefined) {
      const buy = Math.max(0, bin.buy_volume ?? 0);
      const sell = Math.max(0, bin.sell_volume ?? 0);
      const total = Math.max(buy + sell, 1);
      const buyWidth = barWidth * (buy / total);
      const sellWidth = barWidth - buyWidth;
      nodes.push(`<rect x="${x}" y="${y}" width="${sellWidth}" height="${rowHeight}" fill="rgba(238,123,128,0.28)" />`);
      nodes.push(`<rect x="${x + sellWidth}" y="${y}" width="${buyWidth}" height="${rowHeight}" fill="rgba(98,207,232,0.34)" />`);
    } else {
      nodes.push(`<rect x="${x}" y="${y}" width="${barWidth}" height="${rowHeight}" fill="rgba(147,166,142,0.2)" />`);
    }
  }
  if (poc !== null) {
    nodes.push(`<line x1="${right - profileWidth}" x2="${right}" y1="${poc}" y2="${poc}" stroke="rgba(174,210,164,0.72)" stroke-width="2" />`);
  }
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.innerHTML = nodes.join("");
}

function harmonicPatternNodes(
  series: { priceToCoordinate(price: number): number | null },
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } },
  analysis: PositionChartAnalysis,
  showAllHarmonics: boolean,
  rightEdge: number
): string[] {
  const patterns = (showAllHarmonics ? analysis.harmonic_patterns : analysis.harmonic_patterns.slice(0, 2)).filter((pattern) => pattern.points.length >= 4);
  const nodes: string[] = [];
  for (const pattern of patterns) {
    const stroke = pattern.direction === "bearish" ? "rgba(238,123,128,0.78)" : "rgba(127,238,100,0.74)";
    const fill = pattern.direction === "bearish" ? "rgba(238,123,128,0.11)" : "rgba(127,238,100,0.1)";
    const dash = pattern.status === "forming" ? `stroke-dasharray="6 6"` : "";
    const coordinates = pattern.points
      .map((point) => {
        const x = chart.timeScale().timeToCoordinate(point.time as Time);
        const y = series.priceToCoordinate(point.price);
        return x === null || y === null ? null : { x, y, point };
      })
      .filter((item): item is { x: number; y: number; point: PositionChartAnalysis["harmonic_patterns"][number]["points"][number] } => item !== null);
    if (coordinates.length < 4) continue;
    const przTop = series.priceToCoordinate(pattern.prz.high);
    const przBottom = series.priceToCoordinate(pattern.prz.low);
    if (przTop !== null && przBottom !== null) {
      const y = Math.min(przTop, przBottom);
      const height = Math.max(4, Math.abs(przBottom - przTop));
      const x = Math.min(coordinates.at(-2)?.x ?? coordinates[0].x, rightEdge);
      const width = Math.max(18, rightEdge - x);
      nodes.push(`<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="${fill}" stroke="${stroke}" stroke-width="1" stroke-dasharray="3 4" />`);
      nodes.push(`<text x="${x + 6}" y="${Math.max(14, y - 5)}" fill="${stroke}" font-size="11" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(pattern.label)} PRZ ${pattern.confidence}</text>`);
    }
    nodes.push(`<polyline points="${coordinates.map((item) => `${item.x},${item.y}`).join(" ")}" fill="none" stroke="${stroke}" stroke-width="1.6" ${dash} />`);
    for (const item of coordinates) {
      nodes.push(`<circle cx="${item.x}" cy="${item.y}" r="3" fill="${stroke}" />`);
      nodes.push(`<text x="${item.x + 5}" y="${item.y - 6}" fill="rgba(238,242,247,0.82)" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(item.point.label)}</text>`);
    }
    nodes.push(...harmonicRatioLabels(pattern, coordinates));
  }
  return nodes;
}

function harmonicRatioLabels(
  pattern: PositionChartAnalysis["harmonic_patterns"][number],
  coordinates: Array<{ x: number; y: number; point: PositionChartAnalysis["harmonic_patterns"][number]["points"][number] }>
): string[] {
  const labels = [
    { index: 2, value: pattern.ratios.b_xa, name: "B" },
    { index: 3, value: pattern.ratios.c_ab, name: "C" },
    { index: 4, value: pattern.ratios.d_xa ?? pattern.ratios.cd_ab, name: "D" }
  ];
  return labels.flatMap((label) => {
    const point = coordinates[label.index];
    if (!point || typeof label.value !== "number") return [];
    return [`<text x="${point.x + 8}" y="${point.y + 12}" fill="rgba(174,210,164,0.72)" font-size="10" font-family="SF Mono, Monaco, Consolas, monospace">${label.name} ${label.value.toFixed(3)}</text>`];
  });
}

function wyckoffRangeNodes(
  series: { priceToCoordinate(price: number): number | null },
  chart: { timeScale(): { timeToCoordinate(time: Time): number | null } },
  analysis: PositionChartAnalysis
): string[] {
  const range = analysis.wyckoff_range;
  if (!range) return [];
  const top = series.priceToCoordinate(range.resistance.price);
  const bottom = series.priceToCoordinate(range.support.price);
  if (top === null || bottom === null) return [];
  const x1 = chart.timeScale().timeToCoordinate(range.start_time as Time);
  const x2 = chart.timeScale().timeToCoordinate(range.end_time as Time);
  if (x1 === null || x2 === null) return [];
  const y = Math.min(top, bottom);
  const height = Math.max(8, Math.abs(bottom - top));
  const x = Math.min(x1, x2);
  const width = Math.max(12, Math.abs(x2 - x1));
  const phase = phaseHintLabel(analysis.wyckoff_phase?.phase);
  return [
    `<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="rgba(98,207,232,0.045)" stroke="rgba(98,207,232,0.22)" stroke-width="1" stroke-dasharray="4 5" />`,
    `<text x="${x + 8}" y="${Math.max(16, y + 18)}" fill="rgba(221,255,220,0.72)" font-size="12" font-family="SF Mono, Monaco, Consolas, monospace">${escapeSvgText(phase)}</text>`
  ];
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

function compactMarkerText(label: string, confidence: number): string {
  const localized = localizeMarketCodes(label)
    .replace("거래량 급증", "급증")
    .replace("클라이맥스 후보", "클라이맥스")
    .replace("스프링 후보", "스프링");
  return `${localized} ${confidence}`;
}

function wyckoffMarkerTitle(marker: PositionChartAnalysis["wyckoff_markers"][number]): string {
  const components = marker.components;
  if (!components) return `${localizeMarketCodes(marker.label)} 신뢰도 ${marker.confidence}`;
  return [
    `${localizeMarketCodes(marker.label)} 신뢰도 ${marker.confidence}`,
    `이탈/돌파 깊이 ${components.depth_significance}`,
    `복귀 속도 ${components.return_speed}`,
    `거래량 확인 ${components.volume_confirmation}`,
    `레벨 강도 ${components.level_strength}`
  ].join("\n");
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
