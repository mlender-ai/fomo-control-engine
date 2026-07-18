"use client";

import type { CompactChartGauges, OnchainChartMarker, PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
import type { ChartLayerId, ChartLayerState, MinimalChartEvidence } from "@/lib/chartLayers";
import type { Density } from "@/lib/density";
import { formatPrice } from "@/lib/format";
import { PositionCandlestickChart } from "./PositionCandlestickChart";

export function PositionChart({
  analysis,
  loading,
  error,
  onRetry,
  trendSummary = "구조 확인 중",
  plan,
  layers,
  onToggleLayer,
  highlightPrice,
  positionOverlay,
  density = "simple",
  intentZoneSelector,
  layerMode = "pro",
  minimalEvidence = null,
  compressed = false,
  gauges = null
}: {
  analysis: PositionChartAnalysis | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  trendSummary?: string;
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
  if (loading && !analysis) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartLoadingState">
          <i />
          <span>캔들 데이터를 불러오는 중입니다.</span>
        </div>
      </section>
    );
  }

  if (error || !analysis) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartErrorState">
          <strong>차트 데이터를 불러올 수 없습니다.</strong>
          <p>가능한 원인: Bitget 시세 오류, 캔들 데이터 부족, 심볼 매핑 오류</p>
          {error ? <small>{error}</small> : null}
          <button className="button" onClick={onRetry} type="button">다시 불러오기</button>
        </div>
      </section>
    );
  }

  if (analysis.candles.length < 100) {
    return (
      <section className="positionChartPanel" data-testid="position-chart">
        <div className="chartErrorState">
          <strong>차트 분석에 필요한 캔들 데이터가 부족합니다.</strong>
          <p>최소 100개 이상의 캔들이 필요합니다.</p>
          <button className="button" onClick={onRetry} type="button">다시 불러오기</button>
        </div>
      </section>
    );
  }

  return (
    <section className="positionChartPanel" data-testid="position-chart">
      {loading ? <div className="chartRefreshingBadge">차트 갱신 중</div> : null}
      {analysis.underlying_join ? <UnderlyingJoinStrip analysis={analysis} /> : null}
      <PositionCandlestickChart
        analysis={analysis}
        trendSummary={trendSummary}
        plan={plan}
        layers={layers}
        onToggleLayer={onToggleLayer}
        highlightPrice={highlightPrice}
        positionOverlay={positionOverlay}
        density={density}
        intentZoneSelector={intentZoneSelector}
        layerMode={layerMode}
        minimalEvidence={minimalEvidence}
        compressed={compressed}
        gauges={gauges}
      />
      {shouldShowOnchainTimeline(analysis, layers, layerMode) ? (
        <OnchainFlowTimeline analysis={analysis} compact={compressed || layerMode === "minimal"} />
      ) : null}
    </section>
  );
}

function UnderlyingJoinStrip({ analysis }: { analysis: PositionChartAnalysis }) {
  const join = analysis.underlying_join;
  if (!join) return null;
  if (join.status !== "joined") {
    return (
      <section className="underlyingJoinStrip unavailable" data-testid="underlying-join-strip">
        <div><span>기초자산 조인 지연</span><strong>Bitget 차트 유지</strong></div>
        <p>{join.reason || "Toss 기초자산 데이터를 불러오지 못했습니다."}</p>
      </section>
    );
  }
  const basis = Number(join.basis_pct ?? 0);
  return (
    <section className={`underlyingJoinStrip ${join.stale ? "stale" : "live"}`} data-testid="underlying-join-strip">
      <div className="underlyingJoinTitle">
        <span>검증된 1:1 조인</span>
        <strong>{join.underlying_name}</strong>
        <small>{join.toss_exchange} · Toss 구조 {join.structure_timeframe?.toUpperCase()}</small>
      </div>
      <div className="underlyingJoinPrices">
        <span><i>Bitget 실행</i><b>{formatPrice(join.bitget_price)}</b></span>
        <span><i>Toss 원본</i><b>{formatPrice(join.toss_price)}</b></span>
        <span><i>베이시스</i><b className={basis > 0 ? "positive" : basis < 0 ? "negative" : ""}>{basis > 0 ? "+" : ""}{basis.toFixed(2)}%</b></span>
      </div>
      <div className="underlyingJoinState">
        <strong>{join.stale ? "기초자산 장 마감 · 구조 데이터 정지" : "기초자산 장중"}</strong>
        <span>기준 {join.toss_price_at ? new Date(join.toss_price_at).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "확인 중"}</span>
        {join.flow_note ? <span>{join.flow_note}</span> : null}
        {join.warning_gate_blocked ? <em>Toss 경고 게이트 차단 · {join.toss_warnings?.join(", ")}</em> : null}
        {!join.warning_gate_blocked && join.warning_badges?.length ? <span>Toss 경고: {join.warning_badges.join(", ")}</span> : null}
        {join.leverage_note ? <em>{join.leverage_note}</em> : null}
      </div>
    </section>
  );
}

function shouldShowOnchainTimeline(
  analysis: PositionChartAnalysis,
  layers: ChartLayerState,
  layerMode: "minimal" | "pro"
): boolean {
  return Boolean(
    analysis.onchain?.supported
    && analysis.onchain.markers.length
    && (layerMode === "minimal" || layers.ta.includes("onchain"))
  );
}

function OnchainFlowTimeline({
  analysis,
  compact
}: {
  analysis: PositionChartAnalysis;
  compact: boolean;
}) {
  const candles = compact ? analysis.candles.slice(-72) : analysis.candles;
  const start = candles.at(0)?.time ?? analysis.candles.at(0)?.time ?? 0;
  const end = candles.at(-1)?.time ?? analysis.candles.at(-1)?.time ?? start + 1;
  const rawMarkers = (analysis.onchain?.markers ?? [])
    .filter((marker) => marker.time >= start && marker.time <= end)
    .slice(-12);
  const markers = groupTimelineMarkers(rawMarkers, start, end);
  const visibleEvents = markers.reduce((sum, marker) => sum + marker.count, 0);
  const latest = markers.at(-1);

  return (
    <section className={`onchainFlowTimeline ${compact ? "compact" : ""}`} data-testid="onchain-flow-timeline" aria-label="상위 고래 확정 체결 흐름">
      <header>
        <div>
          <span>WHALE FLOW</span>
          <strong>상위 고래 확정 체결</strong>
        </div>
        <p>
          {visibleEvents ? `${visibleEvents}건 · ` : ""}
          {latest ? `${markerActionText(latest)} ${formatCompactUsd(latest.size_usd)}` : "최근 체결 없음"}
        </p>
      </header>
      <div className="onchainFlowLanes">
        {(["long", "short"] as const).map((side) => (
          <div className={`onchainFlowLane ${side}`} key={side}>
            <strong>{side === "long" ? "LONG" : "SHORT"}</strong>
            <div className="onchainFlowTrack">
              <i className="onchainFlowAxis" />
              {markers.filter((marker) => marker.side === side).map((marker, index) => (
                <TimelineMarker
                  key={`${marker.time}-${marker.side}-${marker.kind}-${index}`}
                  marker={marker}
                  position={timelinePosition(marker.time, start, end)}
                  slot={index % 2}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      <footer>
        <span>{formatTimelineTime(start)}</span>
        <span>진입·증액은 채움 / 감액·청산은 빈 원</span>
        <span>{formatTimelineTime(end)}</span>
      </footer>
    </section>
  );
}

function TimelineMarker({ marker, position, slot }: { marker: OnchainChartMarker; position: number; slot: number }) {
  const action = marker.kind === "entry" ? "+" : "−";
  const title = [
    markerActionText(marker),
    `체결 ${marker.count}건 · ${formatCompactUsd(marker.size_usd)}`,
    ...marker.items.slice(0, 3).map((item) => `${item.wallet_label} · ${formatCompactUsd(item.size_usd)}`)
  ].join("\n");
  return (
    <span
      className={`onchainFlowEvent slot${slot} ${marker.kind} tier${marker.size_tier} ${marker.emphasized ? "validated" : "candidate"}`}
      style={{ left: `${position}%` }}
      title={title}
      aria-label={title.replaceAll("\n", ", ")}
    >
      <i />
      <em>{action}{marker.count > 1 ? `×${marker.count}` : ""}</em>
    </span>
  );
}

function timelinePosition(time: number, start: number, end: number): number {
  if (end <= start) return 50;
  return Math.max(2, Math.min(96, ((time - start) / (end - start)) * 100));
}

function groupTimelineMarkers(markers: OnchainChartMarker[], start: number, end: number): OnchainChartMarker[] {
  const groups: OnchainChartMarker[] = [];
  for (const marker of markers) {
    const position = timelinePosition(marker.time, start, end);
    const existing = groups.find((item) => (
      item.side === marker.side
      && item.kind === marker.kind
      && Math.abs(timelinePosition(item.time, start, end) - position) <= 2
    ));
    if (!existing) {
      groups.push({ ...marker, items: [...marker.items] });
      continue;
    }
    existing.time = Math.max(existing.time, marker.time);
    existing.count += marker.count;
    existing.size_usd += marker.size_usd;
    existing.size_tier = Math.max(existing.size_tier, marker.size_tier) as 1 | 2 | 3;
    existing.emphasized = existing.emphasized || marker.emphasized;
    existing.items.push(...marker.items);
  }
  return groups.sort((left, right) => left.time - right.time);
}

function markerActionText(marker: OnchainChartMarker): string {
  const side = marker.side === "long" ? "롱" : "숏";
  if (marker.event === "flip") return `${side} 전환`;
  return `${side} ${marker.kind === "entry" ? "진입" : "정리"}`;
}

function formatCompactUsd(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

function formatTimelineTime(time: number): string {
  if (!time) return "-";
  return new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric" }).format(new Date(time * 1000));
}

export type PositionChartOverlay = {
  direction: "long" | "short";
  quantity: number;
  leverage: number;
  entryPrice: number;
  markPrice: number | null;
  pnlPercent: number;
  pnlAmount: number | null;
  openedAt: string | null;
};
