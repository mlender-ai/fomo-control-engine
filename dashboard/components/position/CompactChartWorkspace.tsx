"use client";

import type { CompactChartGauges, PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
import { MINIMAL_FIXED_LAYER_STATE } from "@/lib/chartLayers";
import { formatPrice } from "@/lib/format";
import { PositionChart, type PositionChartOverlay } from "./PositionChart";

export type CompactNextPrice = {
  label: string;
  price: number | null;
  detail: string;
};

export function CompactChartWorkspace({
  analysis,
  loading,
  error,
  onRetry,
  trendSummary,
  plan,
  gauges,
  nextPrice,
  positionOverlay = null
}: {
  analysis: PositionChartAnalysis | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  trendSummary: string;
  plan: PositionActionPlan | null;
  gauges: CompactChartGauges | null;
  nextPrice: CompactNextPrice | null;
  positionOverlay?: PositionChartOverlay | null;
}) {
  const marketTrendSummary = gauges?.market_view?.stance_label || trendSummary;
  const marketNextPrice = gauges?.market_view?.next_price ?? nextPrice;
  return (
    <section className="compactChartWorkspace" data-testid="compact-chart-workspace">
      <div className="compactChartMain">
        <PositionChart
          analysis={analysis}
          loading={loading}
          error={error}
          onRetry={onRetry}
          trendSummary={marketTrendSummary}
          plan={plan}
          layers={MINIMAL_FIXED_LAYER_STATE}
          onToggleLayer={() => undefined}
          positionOverlay={positionOverlay}
          density="simple"
          layerMode="minimal"
          compressed
          gauges={gauges}
        />
      </div>
      <CompactGaugePanel gauges={gauges} nextPrice={marketNextPrice} loading={loading} />
    </section>
  );
}

export function CompactGaugePanel({
  gauges,
  nextPrice
}: {
  gauges: CompactChartGauges | null;
  nextPrice: CompactNextPrice | null;
  loading?: boolean;
}) {
  const provisional = Boolean(gauges?.bar_state.provisional);
  const minutes = gauges?.bar_state.minutes_to_close ?? null;
  const countdown = minutes === null
    ? "마감 시각 확인 중"
    : minutes >= 60
      ? `마감까지 ${Math.floor(minutes / 60)}시간 ${Math.round(minutes % 60)}분`
      : `마감까지 ${Math.max(1, Math.round(minutes))}분`;
  const pressure = clamp((gauges?.take_profit.pressure ?? 0) * 100, 0, 100);

  return (
    <aside className={`compactGaugePanel ${provisional ? "provisional" : ""}`} data-testid="compact-gauge-panel">
      <header>
        <div>
          <span>판정 계기판</span>
          <strong>{provisional ? "잠정 판정" : "확정 캔들 기준"}</strong>
        </div>
        {provisional ? <em>{countdown}</em> : null}
      </header>

      {gauges?.position_context?.active ? (
        <section className={`compactPositionContext ${gauges.position_context.alignment ?? "neutral"}`} data-testid="position-market-context">
          <span>내 포지션 대비</span>
          <strong>{gauges.position_context.headline}</strong>
          <p>{gauges.position_context.detail}</p>
        </section>
      ) : null}

      <section className={`compactGaugeCard ${gauges?.take_profit.active ? "" : "inactive"}`} data-testid="take-profit-gauge">
        <div className="compactGaugeTitle">
          <span>익절 압력</span>
          <strong>{gauges?.take_profit.active ? gauges.take_profit.level || "계산 중" : "포지션 없음"}</strong>
        </div>
        <div className="pressureGaugeTrack" aria-label="익절 압력 낮음 중간 높음">
          <i><b style={{ left: `${pressure}%` }} /></i>
          <div><span>낮음</span><span>중간</span><span>높음</span></div>
        </div>
        <p>{gauges?.take_profit.reason || "스카우트에서는 익절 압력을 계산하지 않습니다."}</p>
      </section>

      <section className="compactNextPrice" data-testid="compact-next-price">
        <span>다음 가격</span>
        <strong>{nextPrice?.price === null || nextPrice?.price === undefined ? "확인할 가격 없음" : formatPrice(nextPrice.price)}</strong>
        <p>{nextPrice ? `${nextPrice.label} · ${nextPrice.detail}` : "유효한 최근접 트리거가 없습니다."}</p>
      </section>
    </aside>
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}
