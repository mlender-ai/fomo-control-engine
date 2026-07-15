"use client";

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type LiquidationHeatmap } from "@/lib/api";
import { formatPrice } from "@/lib/format";

const WIDTH = 960;
const HEIGHT = 390;
const PLOT = { left: 12, right: 82, top: 16, bottom: 34 };

export function LiquidationHeatmapPanel({ symbol, currentPrice }: { symbol: string; currentPrice?: number | null }) {
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
  return (
    <section className="liquidationHeatmapPanel" data-testid="realized-liquidation-heatmap">
      <header className="liquidationHeatmapHeader">
        <div>
          <span>실현 청산 히트맵</span>
          <strong>{symbol} · Bitget 공개 이력</strong>
          <small>실제로 터진 청산의 가격×시각 분포 · 미래 예상 청산대가 아님</small>
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
          <HeatmapSvg heatmap={heatmap} currentPrice={plottedPrice} />
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
        <span><i className="heatLegend low" />낮음<i className="heatLegend medium" /><i className="heatLegend high" />높음</span>
        <small>강도는 price × amount 추정 명목액의 로그 스케일 · 관측 전용 · Entry Score 미사용</small>
      </footer>
    </section>
  );
}

function HeatmapSvg({ heatmap, currentPrice }: { heatmap: LiquidationHeatmap; currentPrice: number | null }) {
  const plotWidth = WIDTH - PLOT.left - PLOT.right;
  const plotHeight = HEIGHT - PLOT.top - PLOT.bottom;
  const priceLow = heatmap.price_low ?? 0;
  const priceHigh = heatmap.price_high ?? 1;
  const priceRange = Math.max(priceHigh - priceLow, 1e-9);
  const currentY = currentPrice !== null && currentPrice >= priceLow && currentPrice <= priceHigh
    ? PLOT.top + ((priceHigh - currentPrice) / priceRange) * plotHeight
    : null;
  const priceLabels = useMemo(() => Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    return { y: PLOT.top + ratio * plotHeight, price: priceHigh - ratio * priceRange };
  }), [plotHeight, priceHigh, priceRange]);
  const timeLabels = useMemo(() => Array.from({ length: 4 }, (_, index) => {
    const ratio = index / 3;
    const start = new Date(heatmap.window_start).getTime();
    const end = new Date(heatmap.window_end).getTime();
    return { x: PLOT.left + ratio * plotWidth, time: new Date(start + ratio * (end - start)) };
  }), [heatmap.window_end, heatmap.window_start, plotWidth]);

  return (
    <div className="liquidationHeatmapCanvas">
      <svg aria-label={`${heatmap.symbol} 실현 청산 히트맵`} role="img" viewBox={`0 0 ${WIDTH} ${HEIGHT}`}>
        <rect className="heatmapBackdrop" height={plotHeight} width={plotWidth} x={PLOT.left} y={PLOT.top} />
        {priceLabels.map((label) => <line className="heatmapGridLine" key={label.y} x1={PLOT.left} x2={PLOT.left + plotWidth} y1={label.y} y2={label.y} />)}
        {heatmap.cells.map((cell) => {
          const x = PLOT.left + (cell.time_index / heatmap.time_bins) * plotWidth;
          const y = PLOT.top + ((heatmap.price_bins - cell.price_index - 1) / heatmap.price_bins) * plotHeight;
          return (
            <rect
              fill={heatColor(cell.intensity)}
              height={plotHeight / heatmap.price_bins + 0.7}
              key={`${cell.time_index}-${cell.price_index}`}
              width={plotWidth / heatmap.time_bins + 0.7}
              x={x}
              y={y}
            >
              <title>{`${formatPrice((cell.price_low + cell.price_high) / 2)} · N=${cell.events} · ${compactUsd(cell.total_usd_estimated)}`}</title>
            </rect>
          );
        })}
        {currentY !== null ? (
          <g>
            <line className="heatmapCurrentLine" x1={PLOT.left} x2={PLOT.left + plotWidth} y1={currentY} y2={currentY} />
            <text className="heatmapCurrentLabel" x={PLOT.left + plotWidth + 6} y={currentY + 4}>{formatPrice(currentPrice!)}</text>
          </g>
        ) : null}
        {priceLabels.map((label) => <text className="heatmapAxisLabel" key={label.price} x={PLOT.left + plotWidth + 6} y={label.y + 4}>{formatAxisPrice(label.price)}</text>)}
        {timeLabels.map((label) => <text className="heatmapTimeLabel" key={label.x} textAnchor={label.x === PLOT.left ? "start" : label.x === PLOT.left + plotWidth ? "end" : "middle"} x={label.x} y={HEIGHT - 8}>{shortAxisTime(label.time, heatmap.window_hours)}</text>)}
      </svg>
    </div>
  );
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
