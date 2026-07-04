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
import { useEffect, useMemo, useRef } from "react";
import type { ChartCandle, PositionChartAnalysis } from "@/lib/api";
import { formatPrice } from "@/lib/format";
import { localizeMarketCodes, sourceLabel, timeframeLabel } from "@/lib/labels/marketStateLabels";
import { hiddenPriceLinesForAnalysis, priceLineColor, priceLinesForAnalysis, PriceLevelLegend } from "./PriceLevelOverlay";
import { VolumePanel } from "./VolumePanel";

export function PositionCandlestickChart({ analysis, trendSummary }: { analysis: PositionChartAnalysis; trendSummary: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const validation = useMemo(() => validateCandles(analysis.candles), [analysis.candles]);
  const priceLines = useMemo(() => (validation.valid ? priceLinesForAnalysis(analysis) : []), [analysis, validation.valid]);
  const hiddenPriceLines = useMemo(() => (validation.valid ? hiddenPriceLinesForAnalysis(analysis) : []), [analysis, validation.valid]);
  const lastCandle = validation.candles.at(-1);
  const averageVolume = validation.valid ? average(validation.candles.map((candle) => candle.volume)) : 0;

  useEffect(() => {
    if (!validation.valid || !containerRef.current || !validation.candles.length) return;
    const container = containerRef.current;
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
      color: candle.close >= candle.open ? "rgba(127, 238, 100, 0.32)" : "rgba(238, 123, 128, 0.34)"
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

    priceLines.forEach((line) => {
      candleSeries.createPriceLine({
        price: line.price,
        color: priceLineColor(line.kind),
        lineWidth: line.kind === "mark" ? 2 : 1,
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
    const wyckoffMarkers = analysis.wyckoff_markers.slice(-4).map((marker) => ({
      time: marker.time as Time,
      position: marker.type.includes("spring") || marker.type.includes("lps") ? "belowBar" as const : "aboveBar" as const,
      color: marker.type.includes("distribution") ? "#ee7b80" : "#7fee64",
      shape: marker.type.includes("spring") ? "arrowUp" as const : "circle" as const,
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
    return () => chart.remove();
  }, [analysis, averageVolume, priceLines, validation]);

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
      <PriceLevelLegend lines={priceLines} />
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
      <div className="positionChartCanvas" ref={containerRef} />
      <VolumePanel analysis={analysis} averageVolume={averageVolume} />
      {analysis.wyckoff_markers.length ? (
        <div className="wyckoffMarkerRail">
          {analysis.wyckoff_markers.map((marker) => (
            <span key={`${marker.type}-${marker.time}`}>{localizeMarketCodes(marker.label)} · 신뢰도 {marker.confidence}</span>
          ))}
        </div>
      ) : (
        <div className="wyckoffMarkerRail muted">와이코프 마커 후보가 아직 없습니다.</div>
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
