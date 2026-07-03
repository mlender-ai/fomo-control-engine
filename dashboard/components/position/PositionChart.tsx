"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type Time
} from "lightweight-charts";
import { useEffect, useMemo, useRef } from "react";
import type { PositionChartAnalysis } from "@/lib/api";
import { PriceLevelLegend, priceLineColor, priceLinesForAnalysis } from "./PriceLevelOverlay";
import { VolumePanel } from "./VolumePanel";

export function PositionChart({
  analysis,
  loading,
  error,
  onRetry
}: {
  analysis: PositionChartAnalysis | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const priceLines = useMemo(() => (analysis ? priceLinesForAnalysis(analysis) : []), [analysis]);

  useEffect(() => {
    if (!analysis || !containerRef.current || !analysis.candles.length) return;
    const container = containerRef.current;
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#0b0f15" },
        textColor: "#99a4b4"
      },
      grid: {
        vertLines: { color: "rgba(255,255,255,0.045)" },
        horzLines: { color: "rgba(255,255,255,0.055)" }
      },
      rightPriceScale: {
        borderColor: "#2a3240",
        scaleMargins: { top: 0.08, bottom: 0.26 }
      },
      timeScale: {
        borderColor: "#2a3240",
        timeVisible: true,
        secondsVisible: false
      },
      crosshair: {
        horzLine: { color: "rgba(240,184,64,0.45)" },
        vertLine: { color: "rgba(240,184,64,0.32)" }
      }
    });
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#62cfe8",
      downColor: "#ee7b80",
      wickUpColor: "#62cfe8",
      wickDownColor: "#ee7b80",
      borderVisible: false,
      priceLineVisible: false
    });
    candleSeries.setData(
      analysis.candles.map((candle) => ({
        time: candle.time as Time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close
      }))
    );
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      priceLineVisible: false,
      lastValueVisible: false
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.76, bottom: 0 }
    });
    volumeSeries.setData(
      analysis.candles.map((candle) => ({
        time: candle.time as Time,
        value: candle.volume,
        color: candle.close >= candle.open ? "rgba(98,207,232,0.44)" : "rgba(238,123,128,0.42)"
      }))
    );
    priceLines.forEach((line) => {
      candleSeries.createPriceLine({
        price: line.price,
        color: priceLineColor(line.kind),
        lineWidth: line.kind === "mark" ? 2 : 1,
        lineStyle: line.kind === "mark" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${line.label} ${line.price.toLocaleString()}`
      });
    });
    if (analysis.wyckoff_markers.length) {
      createSeriesMarkers(
        candleSeries,
        analysis.wyckoff_markers.map((marker) => ({
          time: marker.time as Time,
          position: marker.type.includes("spring") || marker.type.includes("lps") ? "belowBar" : "aboveBar",
          color: marker.type.includes("distribution") ? "#ee7b80" : "#f0b840",
          shape: marker.type.includes("spring") ? "arrowUp" : "circle",
          text: `${marker.label} ${marker.confidence}`
        }))
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [analysis, priceLines]);

  if (loading) {
    return (
      <section className="positionChartPanel">
        <div className="chartLoadingState">
          <i />
          <span>Loading candles...</span>
        </div>
      </section>
    );
  }

  if (error || !analysis) {
    return (
      <section className="positionChartPanel">
        <div className="chartErrorState">
          <strong>차트 데이터를 불러올 수 없습니다.</strong>
          <p>가능한 원인: Bitget market data 오류, 캔들 데이터 부족, symbol mapping 오류</p>
          {error ? <small>{error}</small> : null}
          <button className="button" onClick={onRetry} type="button">Retry</button>
        </div>
      </section>
    );
  }

  if (analysis.candles.length < 100) {
    return (
      <section className="positionChartPanel">
        <div className="chartErrorState">
          <strong>차트 분석에 필요한 캔들 데이터가 부족합니다.</strong>
          <p>최소 100개 이상 candle이 필요합니다.</p>
          <button className="button" onClick={onRetry} type="button">Retry</button>
        </div>
      </section>
    );
  }

  return (
    <section className="positionChartPanel">
      <div className="positionChartHeader">
        <div>
          <h2>{analysis.symbol} Candlestick Chart</h2>
          <p>{analysis.timeframe} · candles {analysis.data_quality.candles} · {analysis.data_quality.source}</p>
        </div>
        <span>{analysis.direction.toUpperCase()} analysis</span>
      </div>
      <PriceLevelLegend lines={priceLines} />
      <div className="positionChartCanvas" ref={containerRef} />
      <VolumePanel analysis={analysis} />
      {analysis.wyckoff_markers.length ? (
        <div className="wyckoffMarkerRail">
          {analysis.wyckoff_markers.map((marker) => (
            <span key={`${marker.type}-${marker.time}`}>{marker.label} · {marker.confidence}</span>
          ))}
        </div>
      ) : (
        <div className="wyckoffMarkerRail muted">Wyckoff marker structure ready · no marker candidate</div>
      )}
    </section>
  );
}
