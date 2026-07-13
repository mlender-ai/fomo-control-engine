"use client";

import type { CompactChartGauges, DerivativesContext, PositionActionPlan, PositionChartAnalysis } from "@/lib/api";
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
  positionOverlay = null,
  onOpenEvidence
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
  onOpenEvidence?: () => void;
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
      <CompactGaugePanel
        analysis={analysis}
        gauges={gauges}
        nextPrice={marketNextPrice}
        loading={loading}
        hasPosition={positionOverlay !== null}
        onOpenEvidence={onOpenEvidence}
      />
    </section>
  );
}

export function CompactGaugePanel({
  analysis,
  gauges,
  nextPrice,
  loading = false,
  hasPosition = false,
  onOpenEvidence
}: {
  analysis: PositionChartAnalysis | null;
  gauges: CompactChartGauges | null;
  nextPrice: CompactNextPrice | null;
  loading?: boolean;
  hasPosition?: boolean;
  onOpenEvidence?: () => void;
}) {
  const provisional = Boolean(gauges?.bar_state.provisional);
  const minutes = gauges?.bar_state.minutes_to_close ?? null;
  const countdown = minutes === null
    ? "마감 시각 확인 중"
    : minutes >= 60
      ? `마감까지 ${Math.floor(minutes / 60)}시간 ${Math.round(minutes % 60)}분`
      : `마감까지 ${Math.max(1, Math.round(minutes))}분`;
  const pressure = clamp((gauges?.take_profit.pressure ?? 0) * 100, 0, 100);
  const pressurePending = hasPosition && !gauges;
  const pressureActive = Boolean(gauges?.take_profit.active);

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

      <MoneyFlowCard derivatives={analysis?.derivatives} />

      <section className={`compactGaugeCard ${pressureActive || pressurePending ? "" : "inactive"}`} data-testid="take-profit-gauge">
        <div className="compactGaugeTitle">
          <span>익절 압력</span>
          <strong>{pressureActive ? gauges?.take_profit.level || "계산 중" : pressurePending ? "계산 중" : "포지션 없음"}</strong>
        </div>
        <div className="pressureGaugeTrack" aria-label="익절 압력 낮음 중간 높음">
          <i><b style={{ left: `${pressure}%` }} /></i>
          <div><span>낮음</span><span>중간</span><span>높음</span></div>
        </div>
        <p>
          {gauges?.take_profit.reason || (pressurePending || loading
            ? "포지션 상세와 익절 압력을 계산하고 있습니다."
            : "스카우트에서는 익절 압력을 계산하지 않습니다.")}
        </p>
      </section>

      <section className="compactNextPrice" data-testid="compact-next-price">
        <span>다음 가격</span>
        <strong>{nextPrice?.price === null || nextPrice?.price === undefined ? "확인할 가격 없음" : formatPrice(nextPrice.price)}</strong>
        <p>{nextPrice ? `${nextPrice.label} · ${nextPrice.detail}` : "유효한 최근접 트리거가 없습니다."}</p>
      </section>
      {onOpenEvidence ? <button className="button secondary evidenceRoomLink" onClick={onOpenEvidence} type="button">프로에서 검증</button> : null}
    </aside>
  );
}

export function MoneyFlowCard({ derivatives }: { derivatives: DerivativesContext | null | undefined }) {
  const flow = derivatives?.signals?.money_flow;
  const coinglassRaw = derivatives?.coinglass?.raw_json;
  const options = coinglassRaw && typeof coinglassRaw.options_summary === "object"
    ? coinglassRaw.options_summary as Record<string, unknown>
    : null;
  const coinglassLocked = derivatives?.coinglass?.source_status === "locked";
  const tone = flow?.state === "spot_led" || flow?.state === "spot_absorb"
    ? "positive"
    : flow?.state === "futures_led"
      ? "negative"
      : "neutral";
  return (
    <section className={`moneyFlowCard ${tone} ${flow?.available ? "" : "inactive"}`} data-testid="money-flow-card">
      <header>
        <span>자금 흐름</span>
        <strong>{flow?.label || "판정 준비 중"}</strong>
      </header>
      <div className="moneyFlowSparks" aria-label="현물과 선물 CVD 흐름">
        <FlowSpark label="현물" values={flow?.spot_cvd ?? []} />
        <FlowSpark label="선물" values={flow?.futures_cvd ?? []} />
      </div>
      <p>{flow?.reason || "현물·선물 체결 시계열을 수집하고 있습니다."}</p>
      <footer>
        <span>{flow?.source_label || "출처 확인 중"}</span>
        <time>{flow?.as_of ? new Date(flow.as_of).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" }) : "-"}</time>
      </footer>
      {options?.available === true ? (
        <small>옵션 풋/콜 {formatCompactNumber(options.put_call_ratio)} · OI {formatCompactNumber(options.options_open_interest)}</small>
      ) : coinglassLocked ? (
        <small>Coinglass 집계·BTC/ETH 옵션: 연결 시 사용 가능</small>
      ) : options && options.available === false ? (
        <small>BTC·ETH 옵션: 현재 플랜에서 사용할 수 없음</small>
      ) : null}
    </section>
  );
}

function FlowSpark({ label, values }: { label: string; values: Array<{ value: number }> }) {
  const points = sparkPoints(values.map((item) => Number(item.value)).filter(Number.isFinite));
  return (
    <div><span>{label}</span><svg aria-hidden="true" viewBox="0 0 100 22" preserveAspectRatio="none"><polyline points={points} /></svg></div>
  );
}

function sparkPoints(values: number[]): string {
  if (values.length < 2) return "0,11 100,11";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  return values.map((value, index) => `${(index / (values.length - 1)) * 100},${20 - ((value - min) / range) * 18}`).join(" ");
}

function formatCompactNumber(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 2 }).format(number);
}


function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}
