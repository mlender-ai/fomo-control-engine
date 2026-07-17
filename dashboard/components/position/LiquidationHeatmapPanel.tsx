"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type ChartCandle, type LiquidationHeatmap } from "@/lib/api";
import { formatPrice } from "@/lib/format";

const WIDTH = 960;
const HEIGHT = 430;
const PLOT = { left: 12, right: 88, top: 22, bottom: 36 };

export function LiquidationHeatmapPanel({
  symbol,
  currentPrice,
  candles = [],
  timeframe = "4h"
}: {
  symbol: string;
  currentPrice?: number | null;
  candles?: ChartCandle[];
  timeframe?: string;
}) {
  const [windowHours, setWindowHours] = useState<24 | 72>(72);
  const [heatmap, setHeatmap] = useState<LiquidationHeatmap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (hours: 24 | 72, refresh = true) => {
    setLoading(true);
    setError("");
    try {
      const payload = refresh
        ? await api.refreshLiquidationHeatmap(symbol, hours)
        : await api.liquidationHeatmap(symbol, hours);
      setHeatmap(payload);
    } catch (reason) {
      try {
        setHeatmap(await api.liquidationHeatmap(symbol, hours));
      } catch {
        setError(reason instanceof Error ? reason.message : "실현 청산 이력을 불러오지 못했습니다.");
      }
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    void load(windowHours);
  }, [load, windowHours]);

  function changeWindow(hours: 24 | 72) {
    setWindowHours(hours);
  }

  const plottedPrice = currentPrice ?? heatmap?.current_price ?? null;
  const densitySummary = heatmap ? summarizeDensity(heatmap, plottedPrice) : null;
  return (
    <section className="liquidationHeatmapPanel" data-testid="realized-liquidation-heatmap">
      <header className="liquidationHeatmapHeader">
        <div>
          <span>실현 청산 밀집도</span>
          <strong>{symbol} · Bitget 공개 이력</strong>
          <small>실제 청산 가격 밀집 + {timeframe.toUpperCase()} 가격 흐름 · 미래 예상 청산대가 아님</small>
        </div>
        <div className="liquidationHeatmapActions">
          <div aria-label="청산 이력 범위" className="liquidationWindowToggle">
            <button className={windowHours === 24 ? "active" : ""} onClick={() => changeWindow(24)} type="button">24H</button>
            <button className={windowHours === 72 ? "active" : ""} onClick={() => changeWindow(72)} type="button">3D</button>
          </div>
          <button aria-label="실현 청산 새로고침" className="liquidationRefresh" disabled={loading} onClick={() => void load(windowHours)} type="button">
            <RefreshCw size={14} />
          </button>
        </div>
      </header>

      {error ? <div className="liquidationHeatmapEmpty error">{error}</div> : loading && !heatmap ? (
        <div className="liquidationHeatmapEmpty">Bitget 공개 청산 이력을 수집하는 중입니다…</div>
      ) : heatmap?.source_status === "locked" ? (
        <div className="liquidationHeatmapEmpty">{heatmap.notes[0] ?? "Bitget 공개 청산 수집이 비활성화되어 있습니다."}</div>
      ) : !heatmap?.cells.length ? (
        <div className="liquidationHeatmapEmpty">선택 범위에 실현 청산 표본이 없습니다. 새로고침 후에도 비어 있으면 거래소 이력 자체가 없는 상태입니다.</div>
      ) : (
        <>
          {densitySummary ? (
            <div className="liquidationDensitySummary" data-testid="realized-liquidation-density-summary">
              <div className="primary">
                <span>최대 실현 밀집</span>
                <strong>{formatPrice(densitySummary.strongest.price_mid)}</strong>
                <em>{densitySummary.strongest.share_pct.toFixed(1)}% · {densitySummary.distanceLabel}</em>
              </div>
              <div>
                <span>현재가 위</span>
                <strong>{compactUsd(densitySummary.aboveTotal)}</strong>
                <em>{densitySummary.aboveShare.toFixed(0)}% 관측</em>
              </div>
              <div>
                <span>현재가 아래</span>
                <strong>{compactUsd(densitySummary.belowTotal)}</strong>
                <em>{densitySummary.belowShare.toFixed(0)}% 관측</em>
              </div>
            </div>
          ) : null}
          <HeatmapSvg heatmap={heatmap} currentPrice={plottedPrice} candles={candles} timeframe={timeframe} />
          <div className="liquidationHeatmapStats">
            <div><span>표본</span><strong>N={heatmap.sample_size}</strong></div>
            <div><span>롱 청산 강도</span><strong className="negative">{compactUsd(heatmap.summary.long_usd_estimated)}</strong></div>
            <div><span>숏 청산 강도</span><strong className="positive">{compactUsd(heatmap.summary.short_usd_estimated)}</strong></div>
            <div><span>마지막 이벤트</span><strong>{heatmap.latest_event_at ? shortTime(heatmap.latest_event_at) : "-"}</strong></div>
          </div>
          <div className="liquidationHeatmapZones">
            <span>실현 집중 가격대</span>
            {heatmap.top_zones.slice(0, 3).map((zone) => (
              <div key={`${zone.price_low}-${zone.price_high}`}>
                <strong>{formatPrice(zone.price_mid)}</strong>
                <small>{zone.dominant_side === "long" ? "롱 청산" : "숏 청산"} · N={zone.events} · {zone.share_pct.toFixed(1)}%</small>
              </div>
            ))}
          </div>
        </>
      )}
      <footer>
        <span><i className="heatLegend low" />낮음<i className="heatLegend medium" /><i className="heatLegend high" />높음<i className="candleLegend" />{timeframe.toUpperCase()} 가격</span>
        <small>수평 밴드 = 선택 기간 누적 실현 밀집 · 셀 = 실제 발생 시점 · 관측 전용 · Entry Score 미사용</small>
      </footer>
    </section>
  );
}

function HeatmapSvg({
  heatmap,
  currentPrice,
  candles,
  timeframe
}: {
  heatmap: LiquidationHeatmap;
  currentPrice: number | null;
  candles: ChartCandle[];
  timeframe: string;
}) {
  const plotWidth = WIDTH - PLOT.left - PLOT.right;
  const plotHeight = HEIGHT - PLOT.top - PLOT.bottom;
  const windowStart = new Date(heatmap.window_start).getTime();
  const windowEnd = new Date(heatmap.window_end).getTime();
  const visibleCandles = useMemo(() => candles.filter((candle) => {
    const timestamp = candle.time * 1000;
    return timestamp >= windowStart && timestamp <= windowEnd;
  }), [candles, windowEnd, windowStart]);
  const domain = useMemo(
    () => priceDomain(heatmap, currentPrice, visibleCandles),
    [currentPrice, heatmap, visibleCandles]
  );
  const priceLow = domain.low;
  const priceHigh = domain.high;
  const priceRange = Math.max(priceHigh - priceLow, 1e-9);
  const xForTime = (timestampMs: number) => PLOT.left + ((timestampMs - windowStart) / Math.max(windowEnd - windowStart, 1)) * plotWidth;
  const yForPrice = (price: number) => PLOT.top + ((priceHigh - price) / priceRange) * plotHeight;
  const currentY = currentPrice !== null && currentPrice >= priceLow && currentPrice <= priceHigh
    ? yForPrice(currentPrice)
    : null;
  const priceLabels = useMemo(() => Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    return { y: PLOT.top + ratio * plotHeight, price: priceHigh - ratio * priceRange };
  }), [plotHeight, priceHigh, priceRange]);
  const timeLabels = useMemo(() => Array.from({ length: 4 }, (_, index) => {
    const ratio = index / 3;
    return { x: PLOT.left + ratio * plotWidth, time: new Date(windowStart + ratio * (windowEnd - windowStart)) };
  }), [plotWidth, windowEnd, windowStart]);
  const maxZoneTotal = Math.max(...heatmap.top_zones.map((zone) => zone.total_usd_estimated), 1);
  const candleWidth = Math.max(3, Math.min(13, (plotWidth / Math.max(visibleCandles.length, 1)) * 0.52));

  return (
    <div className="liquidationHeatmapCanvas">
      <svg aria-label={`${heatmap.symbol} 실현 청산 밀집도와 ${timeframe} 가격 차트`} role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        <rect className="heatmapBackdrop" height={plotHeight} width={plotWidth} x={PLOT.left} y={PLOT.top} />
        {priceLabels.map((label) => <line className="heatmapGridLine" key={label.y} x1={PLOT.left} x2={PLOT.left + plotWidth} y1={label.y} y2={label.y} />)}
        <g data-testid="realized-liquidation-density-bands">
          {heatmap.top_zones.map((zone, index) => {
            const intensity = zone.total_usd_estimated / maxZoneTotal;
            const yTop = yForPrice(zone.price_high);
            const yBottom = yForPrice(zone.price_low);
            const height = Math.max(7, yBottom - yTop);
            const midY = (yTop + yBottom) / 2;
            const color = heatColor(0.28 + intensity * 0.72);
            return (
              <g key={`${zone.price_low}-${zone.price_high}`}>
                <rect className="heatmapDensityBand" fill={color} height={height} opacity={0.12 + intensity * 0.24} width={plotWidth} x={PLOT.left} y={midY - height / 2}>
                  <title>{`기간 누적 실현 밀집 #${index + 1} · ${formatPrice(zone.price_mid)} · ${zone.share_pct.toFixed(1)}%`}</title>
                </rect>
                <line className="heatmapDensitySpine" opacity={0.36 + intensity * 0.42} stroke={color} strokeWidth={Math.max(2, Math.min(5, height * 0.45))} x1={PLOT.left} x2={PLOT.left + plotWidth * (0.25 + intensity * 0.75)} y1={midY} y2={midY} />
                {index === 0 ? <text className="heatmapDensityLabel" x={PLOT.left + 7} y={midY - height / 2 - 3}>최대 {formatAxisPrice(zone.price_mid)} · {zone.share_pct.toFixed(1)}%</text> : null}
              </g>
            );
          })}
        </g>
        <g data-testid="realized-liquidation-event-cells">
          {heatmap.cells.map((cell) => {
          const x = PLOT.left + (cell.time_index / heatmap.time_bins) * plotWidth;
          const y = yForPrice(cell.price_high);
          const height = Math.max(3, yForPrice(cell.price_low) - y);
          return (
            <rect
              className="heatmapEventCell"
              fill={heatColor(cell.intensity)}
              height={height}
              key={`${cell.time_index}-${cell.price_index}`}
              width={plotWidth / heatmap.time_bins + 0.7}
              x={x}
              y={y}
            >
              <title>{`${formatPrice((cell.price_low + cell.price_high) / 2)} · N=${cell.events} · ${compactUsd(cell.total_usd_estimated)}`}</title>
            </rect>
          );
          })}
        </g>
        <g data-testid="realized-liquidation-candles">
          {visibleCandles.map((candle) => {
            const x = xForTime(candle.time * 1000);
            const yOpen = yForPrice(candle.open);
            const yClose = yForPrice(candle.close);
            const rising = candle.close >= candle.open;
            const color = rising ? "#38e6b0" : "#ff5a72";
            const bodyTop = Math.min(yOpen, yClose);
            const bodyHeight = Math.max(2, Math.abs(yClose - yOpen));
            return (
              <g className={rising ? "heatmapCandle rising" : "heatmapCandle falling"} key={candle.time}>
                <line className="heatmapCandleWickShadow" x1={x} x2={x} y1={yForPrice(candle.high)} y2={yForPrice(candle.low)} />
                <line className="heatmapCandleWick" stroke={color} x1={x} x2={x} y1={yForPrice(candle.high)} y2={yForPrice(candle.low)} />
                <rect className="heatmapCandleBodyShadow" height={bodyHeight + 2} width={candleWidth + 2} x={x - candleWidth / 2 - 1} y={bodyTop - 1} />
                <rect className="heatmapCandleBody" fill={color} height={bodyHeight} width={candleWidth} x={x - candleWidth / 2} y={bodyTop}>
                  <title>{`${shortAxisTime(new Date(candle.time * 1000), heatmap.window_hours)} · O ${formatPrice(candle.open)} H ${formatPrice(candle.high)} L ${formatPrice(candle.low)} C ${formatPrice(candle.close)}`}</title>
                </rect>
              </g>
            );
          })}
        </g>
        {currentY !== null ? (
          <g data-testid="realized-liquidation-current-price">
            <line className="heatmapCurrentLine" x1={PLOT.left} x2={PLOT.left + plotWidth} y1={currentY} y2={currentY} />
            <rect className="heatmapCurrentPill" height={18} width={82} x={PLOT.left + plotWidth + 2} y={currentY - 9} />
            <text className="heatmapCurrentLabel" x={PLOT.left + plotWidth + 6} y={currentY + 4}>{formatPrice(currentPrice!)}</text>
          </g>
        ) : null}
        {priceLabels.map((label) => <text className="heatmapAxisLabel" key={label.price} x={PLOT.left + plotWidth + 6} y={label.y + 4}>{formatAxisPrice(label.price)}</text>)}
        {timeLabels.map((label) => <text className="heatmapTimeLabel" key={label.x} textAnchor={label.x === PLOT.left ? "start" : label.x === PLOT.left + plotWidth ? "end" : "middle"} x={label.x} y={HEIGHT - 8}>{shortAxisTime(label.time, heatmap.window_hours)}</text>)}
      </svg>
    </div>
  );
}

function priceDomain(heatmap: LiquidationHeatmap, currentPrice: number | null, candles: ChartCandle[]) {
  const prices = [
    heatmap.price_low,
    heatmap.price_high,
    currentPrice,
    ...candles.flatMap((candle) => [candle.low, candle.high])
  ].filter((value): value is number => typeof value === "number" && Number.isFinite(value) && value > 0);
  const low = Math.min(...prices);
  const high = Math.max(...prices);
  const spread = Math.max(high - low, Math.max(high, 1) * 0.004);
  const padding = spread * 0.045;
  return { low: low - padding, high: high + padding };
}

function summarizeDensity(heatmap: LiquidationHeatmap, currentPrice: number | null) {
  const strongest = heatmap.top_zones[0];
  if (!strongest) return null;
  const total = heatmap.cells.reduce((sum, cell) => sum + cell.total_usd_estimated, 0);
  const aboveTotal = currentPrice === null
    ? 0
    : heatmap.cells.reduce((sum, cell) => sum + (((cell.price_low + cell.price_high) / 2) > currentPrice ? cell.total_usd_estimated : 0), 0);
  const belowTotal = currentPrice === null ? total : Math.max(0, total - aboveTotal);
  const distancePct = currentPrice && currentPrice > 0 ? ((strongest.price_mid / currentPrice) - 1) * 100 : null;
  return {
    strongest,
    aboveTotal,
    belowTotal,
    aboveShare: total > 0 ? aboveTotal / total * 100 : 0,
    belowShare: total > 0 ? belowTotal / total * 100 : 0,
    distanceLabel: distancePct === null
      ? "현재가 비교 대기"
      : `현재가 대비 ${distancePct >= 0 ? "+" : ""}${distancePct.toFixed(2)}%`
  };
}

function heatColor(intensity: number): string {
  const stops = [
    [4, 9, 38],
    [20, 30, 218],
    [0, 190, 242],
    [48, 229, 173],
    [255, 207, 45]
  ];
  const scaled = Math.max(0, Math.min(0.9999, intensity)) * (stops.length - 1);
  const index = Math.floor(scaled);
  const fraction = scaled - index;
  const start = stops[index];
  const end = stops[Math.min(index + 1, stops.length - 1)];
  const channel = (position: number) => Math.round(start[position] + (end[position] - start[position]) * fraction);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

function compactUsd(value: number): string {
  const absolute = Math.abs(value);
  if (absolute >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B*`;
  if (absolute >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M*`;
  if (absolute >= 1_000) return `$${(value / 1_000).toFixed(1)}K*`;
  return `$${value.toFixed(0)}*`;
}

function formatAxisPrice(value: number): string {
  if (value >= 1_000) return Math.round(value).toLocaleString("ko-KR");
  if (value >= 1) return value.toFixed(2);
  return value.toPrecision(4);
}

function shortTime(value: string): string {
  return new Date(value).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function shortAxisTime(value: Date, hours: number): string {
  return value.toLocaleString("ko-KR", hours > 24
    ? { month: "numeric", day: "numeric", hour: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" });
}
