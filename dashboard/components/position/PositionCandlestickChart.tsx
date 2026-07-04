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
        background: { type: ColorType.Solid, color: "#080c12" },
        textColor: "#b8c1cf",
        fontFamily: "SF Mono, Monaco, Consolas, monospace",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.045)" },
        horzLines: { color: "rgba(255,255,255,0.055)" }
      },
      localization: {
        locale: "ko-KR",
        priceFormatter: (price: number) => formatPrice(price),
        timeFormatter: (time: Time) => formatKoreanDateTime(Number(time))
      },
      rightPriceScale: {
        borderColor: "#2a3240",
        scaleMargins: { top: 0.08, bottom: 0.26 }
      },
      timeScale: {
        borderColor: "#2a3240",
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true
      },
      crosshair: {
        mode: 1,
        horzLine: {
          color: "rgba(238,242,247,0.34)",
          labelBackgroundColor: "#11151d"
        },
        vertLine: {
          color: "rgba(238,242,247,0.24)",
          labelBackgroundColor: "#11151d"
        }
      }
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#5fd0a5",
      downColor: "#f16672",
      wickUpColor: "#5fd0a5",
      wickDownColor: "#f16672",
      borderUpColor: "#8ee3c4",
      borderDownColor: "#ff9ca3",
      borderVisible: true,
      priceLineVisible: false,
      lastValueVisible: true
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
      color: candle.close >= candle.open ? "rgba(95,208,165,0.48)" : "rgba(241,102,114,0.46)"
    }));
    volumeSeries.setData(volumeData);

    const averageVolumeSeries = chart.addSeries(LineSeries, {
      priceScaleId: "volume",
      color: "rgba(240,184,64,0.72)",
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

    priceLines.forEach((line, index) => {
      const showAxisLabel = index < 3;
      candleSeries.createPriceLine({
        price: line.price,
        color: priceLineColor(line.kind),
        lineWidth: line.kind === "mark" ? 2 : 1,
        lineStyle: line.kind === "mark" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: showAxisLabel,
        title: showAxisLabel ? `${line.label} ${formatPrice(line.price)}` : ""
      });
    });

    const spikeMarkers = validation.candles
      .filter((candle) => candle.volume >= averageVolume * 1.8)
      .slice(-5)
      .map((candle) => ({
        time: candle.time as Time,
        position: "belowBar" as const,
        color: "#f0b840",
        shape: "circle" as const,
        text: "거래량 급증"
      }));
    const wyckoffMarkers = analysis.wyckoff_markers.map((marker) => ({
      time: marker.time as Time,
      position: marker.type.includes("spring") || marker.type.includes("lps") ? "belowBar" as const : "aboveBar" as const,
      color: marker.type.includes("distribution") ? "#f16672" : "#f0b840",
      shape: marker.type.includes("spring") ? "arrowUp" as const : "circle" as const,
      text: `${localizeMarketCodes(marker.label)} ${marker.confidence}`
    }));
    if (spikeMarkers.length || wyckoffMarkers.length) {
      createSeriesMarkers(candleSeries, [...wyckoffMarkers, ...spikeMarkers].sort((left, right) => Number(left.time) - Number(right.time)));
    }

    chart.subscribeCrosshairMove((param) => {
      const tooltip = tooltipRef.current;
      if (!tooltip || !param.time || !param.point) {
        if (tooltip) tooltip.style.opacity = "0";
        return;
      }
      const data = param.seriesData.get(candleSeries);
      if (!isCandlestickData(data)) {
        tooltip.style.opacity = "0";
        return;
      }
      tooltip.style.opacity = "1";
      tooltip.style.left = `${Math.min(param.point.x + 16, container.clientWidth - 190)}px`;
      tooltip.style.top = `${Math.max(12, param.point.y - 58)}px`;
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
      <div className="positionChartCanvas" ref={containerRef}>
        <div className="chartTooltip" ref={tooltipRef} />
      </div>
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
