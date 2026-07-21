"use client";

import { ArrowDown, ArrowUp, Minus, ShieldCheck, TriangleAlert, Waves } from "lucide-react";
import type { CompactChartGauges, DerivativesContext, OccOptionsSummary, PositionActionPlan, PositionChartAnalysis, PositionDeepDive } from "@/lib/api";
import { MINIMAL_FIXED_LAYER_STATE } from "@/lib/chartLayers";
import { formatPrice } from "@/lib/format";
import { PositionChart, type PositionChartOverlay } from "./PositionChart";
import { PositionDeepDivePanel } from "./PositionDeepDivePanel";

export type CompactNextPrice = {
  label: string;
  price: number | null;
  detail: string;
};

const COMPACT_TIMEFRAMES = [
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
  { value: "12h", label: "12시간" },
  { value: "1d", label: "일봉" }
] as const;

export function CompactChartWorkspace({
  analysis,
  selectedTimeframe,
  onSelectTimeframe,
  loading,
  error,
  onRetry,
  trendSummary,
  plan,
  gauges,
  nextPrice,
  positionOverlay = null,
  deepDive = null,
  deepDiveLoading = false,
  deepDiveError = "",
  onRetryDeepDive,
  onSaveThesis,
  onOpenEvidence
}: {
  analysis: PositionChartAnalysis | null;
  selectedTimeframe?: string;
  onSelectTimeframe?: (timeframe: string) => void;
  loading: boolean;
  error: string;
  onRetry: () => void;
  trendSummary: string;
  plan: PositionActionPlan | null;
  gauges: CompactChartGauges | null;
  nextPrice: CompactNextPrice | null;
  positionOverlay?: PositionChartOverlay | null;
  deepDive?: PositionDeepDive | null;
  deepDiveLoading?: boolean;
  deepDiveError?: string;
  onRetryDeepDive?: () => void;
  onSaveThesis?: (value: string) => Promise<void>;
  onOpenEvidence?: () => void;
}) {
  const marketTrendSummary = gauges?.market_view?.stance_label || trendSummary;
  const marketNextPrice = gauges?.market_view?.next_price ?? nextPrice;
  return (
    <section className="compactChartWorkspace" data-testid="compact-chart-workspace">
      <div className="compactChartMain">
        {selectedTimeframe && onSelectTimeframe ? (
          <div className="compactChartTimeframeBar" data-testid="minimal-timeframe-selector">
            <div role="group" aria-label="미니멀 차트 시간봉 선택">
              {COMPACT_TIMEFRAMES.map((item) => (
                <button
                  aria-pressed={selectedTimeframe === item.value}
                  className={selectedTimeframe === item.value ? "active" : ""}
                  data-testid={`minimal-timeframe-${item.value}`}
                  key={item.value}
                  onClick={() => onSelectTimeframe(item.value)}
                  type="button"
                >
                  {item.label}
                </button>
              ))}
            </div>
            <p>
              <span>현재가</span>
              <strong>{analysis ? formatPrice(analysis.mark_price) : "-"}</strong>
              <small>{loading ? `${compactTimeframeLabel(selectedTimeframe)} 불러오는 중` : "확정 캔들만 표시"}</small>
            </p>
          </div>
        ) : null}
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
        <MoneyFlowCard derivatives={analysis?.derivatives} gauges={gauges} options={analysis?.options} />
      </div>
      {deepDive || deepDiveLoading || deepDiveError ? (
        <PositionDeepDivePanel
          deepDive={deepDive}
          loading={deepDiveLoading}
          error={deepDiveError}
          onRetry={onRetryDeepDive || onRetry}
          onSaveThesis={onSaveThesis || (async () => undefined)}
          onOpenEvidence={onOpenEvidence}
        />
      ) : (
        <CompactGaugePanel
          gauges={gauges}
          nextPrice={marketNextPrice}
          loading={loading}
          hasPosition={positionOverlay !== null}
          onOpenEvidence={onOpenEvidence}
        />
      )}
    </section>
  );
}

function compactTimeframeLabel(timeframe: string): string {
  return COMPACT_TIMEFRAMES.find((item) => item.value === timeframe)?.label ?? timeframe;
}

export function CompactGaugePanel({
  gauges,
  nextPrice,
  loading = false,
  hasPosition = false,
  onOpenEvidence
}: {
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

export function MoneyFlowCard({
  derivatives,
  gauges = null,
  options: occOptions = null
}: {
  derivatives: DerivativesContext | null | undefined;
  gauges?: CompactChartGauges | null;
  options?: OccOptionsSummary | null;
}) {
  const flow = derivatives?.signals?.money_flow;
  const coinglassRaw = derivatives?.coinglass?.raw_json;
  const cryptoOptions = coinglassRaw && typeof coinglassRaw.options_summary === "object"
    ? coinglassRaw.options_summary as Record<string, unknown>
    : null;
  const coinglassLocked = derivatives?.coinglass?.source_status === "locked";
  const spotAvailable = flow?.coverage?.spot_available === true;
  const futuresAvailable = flow?.coverage?.futures_available === true;
  const spotUnavailableReason = flow && !spotAvailable
    ? flow.coverage?.spot_mapping === "mapping_unavailable"
      ? "현물 마켓 미지원"
      : "현물 체결 없음"
    : null;
  const futuresUnavailableReason = flow && !futuresAvailable ? "선물 체결 없음" : null;
  const hasObservableFlow = Boolean(flow?.available || spotAvailable || futuresAvailable);
  const tone = flow?.state === "spot_led" || flow?.state === "spot_absorb"
    ? "positive"
    : flow?.state === "futures_led"
      ? "negative"
      : "neutral";
  const presentation = moneyFlowPresentation(flow);
  const spotRatio = finiteNumber(flow?.spot_cvd_delta_ratio);
  const futuresRatio = finiteNumber(flow?.futures_cvd_delta_ratio);
  const confidenceLabel = flow?.provisional
    ? `표본 ${flow.sample_size}/${flow.required_samples ?? 10}`
    : Number.isFinite(flow?.confidence)
      ? `판정 신뢰 ${Math.round(flow?.confidence ?? 0)}%`
      : flow?.available
        ? "확정봉 판정"
        : futuresAvailable
          ? "선물 체결 관측"
        : "데이터 대기";
  return (
    <section className={`moneyFlowIndicator ${tone} ${hasObservableFlow ? "" : "inactive"}`} data-testid="money-flow-card">
      <header className="moneyFlowIndicatorHeader">
        <div className="moneyFlowTitle">
          <FlowStateIcon state={flow?.state} />
          <span>Money Flow</span>
          <strong>{presentation.headline}</strong>
        </div>
        <div className="moneyFlowMeta">
          <i className={hasObservableFlow ? "live" : ""} />
          <span>{confidenceLabel}</span>
          <time>{flow?.as_of ? new Date(flow.as_of).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "관측 대기"}</time>
        </div>
      </header>

      {gauges?.stance_history?.length ? <StanceHistoryStrip gauges={gauges} /> : null}

      {occOptions?.available === true ? <OptionsPositioningSummary options={occOptions} /> : null}

      <div className="moneyFlowIndicatorGrid">
        <div className="moneyFlowPressure" aria-label="현물과 선물 매수 매도 압력">
          <FlowPressure label="현물 체결" ratio={spotRatio} unavailableReason={spotUnavailableReason} />
          <FlowPressure label="선물 체결" ratio={futuresRatio} unavailableReason={futuresUnavailableReason} />
        </div>

        <div className="moneyFlowMetrics" aria-label="가격과 미결제약정 판정">
        <FlowMetric
          label="가격"
          value={finiteNumber(flow?.price_change_pct)}
          direction={flowDirection(flow?.directions?.price, finiteNumber(flow?.price_change_pct), 0.05)}
          unit="%"
          upLabel="상승"
          downLabel="하락"
          flatLabel="보합"
        />
        <FlowMetric
          label="미결제약정"
          value={finiteNumber(flow?.oi_change_pct)}
          direction={flowDirection(flow?.directions?.oi, finiteNumber(flow?.oi_change_pct), 0.05)}
          unit="%"
          upLabel="포지션 증가"
          downLabel="포지션 감소"
          flatLabel="변화 작음"
        />
        </div>

        <div className="moneyFlowHistory" aria-label="최근 구간 CVD 변화">
          <FlowHistogram
            label="현물 CVD"
            values={flow?.spot_cvd ?? []}
            method={String(flow?.coverage?.spot_cvd_method ?? "")}
            unavailableReason={spotUnavailableReason}
          />
          <FlowHistogram
            label="선물 CVD"
            values={flow?.futures_cvd ?? []}
            method={String(flow?.coverage?.futures_cvd_method ?? "")}
            unavailableReason={futuresUnavailableReason}
          />
        </div>

        <div className="moneyFlowReadout">
          <strong>{presentation.driver}</strong>
          <p>{presentation.readout}</p>
        </div>
      </div>
      <p>{flow?.reason || "현물·선물 체결 시계열을 수집하고 있습니다."}</p>
      {flow?.state === "futures_led" && flow.predictive_warning ? <em>선물 단독 견인의 예측력 미검증</em> : null}
      <footer className="moneyFlowIndicatorFooter">
        <span>{flow?.source_label || "출처 확인 중"}</span>
        <small>CVD = 실제 입금액이 아닌 시장가 매수−매도 체결 우위</small>
        {cryptoOptions?.available === true ? (
          <small>옵션 풋/콜 {formatCompactNumber(cryptoOptions.put_call_ratio)} · OI {formatCompactNumber(cryptoOptions.options_open_interest)}</small>
        ) : coinglassLocked ? (
          <small>Coinglass 집계·BTC/ETH 옵션 연결 대기</small>
        ) : null}
      </footer>
    </section>
  );
}

function OptionsPositioningSummary({ options }: { options: OccOptionsSummary }) {
  return (
    <section className="optionsPositioningSummary" data-testid="options-put-call-summary">
      <header>
        <div><span>OCC {options.underlying}</span><strong>풋/콜 비율</strong></div>
        <em>전일 결제 · 관측 전용</em>
      </header>
      <div>
        <article>
          <span>미결제약정 P/C</span>
          <strong>{formatRatio(options.put_call_oi_ratio)}</strong>
          <small>풋 {formatCompactNumber(options.put_open_interest)} / 콜 {formatCompactNumber(options.call_open_interest)}</small>
        </article>
        <article>
          <span>{options.volume_date ?? "최근 완료일"} 계약량 P/C</span>
          <strong>{formatRatio(options.put_call_volume_ratio)}</strong>
          <small>풋 {formatCompactNumber(options.put_volume)} / 콜 {formatCompactNumber(options.call_volume)}</small>
        </article>
        <article className="maxPainMetric">
          <span>최근접 만기 맥스페인</span>
          <strong>{formatOptionPrice(options.max_pain_price)}</strong>
          <small>{formatExpiry(options.max_pain_expiry, options.days_to_expiry)}</small>
        </article>
      </div>
      <p>맥스페인은 해당 만기 OI의 이론상 결제비용 최소 가격입니다. 가격 목표·방향 점수에는 반영하지 않습니다.</p>
    </section>
  );
}

function formatOptionPrice(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 3 })}`;
}

function formatExpiry(expiry: string | null | undefined, days: number | null | undefined): string {
  if (!expiry) return "만기 데이터 없음";
  const dDay = days == null ? "" : days === 0 ? " · D-DAY" : ` · D-${days}`;
  return `${expiry}${dDay}`;
}

function StanceHistoryStrip({ gauges }: { gauges: CompactChartGauges }) {
  const history = (gauges.stance_history ?? []).slice(-40);
  const last = history.at(-1);
  const preview = last?.transitioning && last.preview_stance && last.preview_stance !== last.stance
    ? last.preview_stance
    : null;
  return (
    <div className="stanceIndicator" data-testid="stance-ribbon">
      <span>시장 방향</span>
      <div className="stanceIndicatorTrack" aria-label="확정 캔들 스탠스 이력">
        {history.map((item) => (
          <b
            className={stanceClass(item.stance)}
            data-stance-flip={item.flipped ? "true" : undefined}
            key={item.time}
            title={item.reason}
          />
        ))}
        {preview ? <b className={`${stanceClass(preview)} preview`} title="전환 관찰" /> : null}
      </div>
      <strong>{gauges.market_view?.stance_label || gauges.direction.stance_label}</strong>
      {preview ? <em>순간 {stanceLabel(preview)} 시도 · 전환 {Math.round((gauges.direction.flip_progress ?? 0) * 100)}%</em> : null}
    </div>
  );
}

type FlowDirection = "up" | "down" | "flat" | "unknown";

function stanceClass(stance: string | null | undefined): string {
  if (stance === "long_leaning" || stance === "long") return "long";
  if (stance === "short_leaning" || stance === "short") return "short";
  return "neutral";
}

function stanceLabel(stance: string | null | undefined): string {
  if (stance === "long_leaning" || stance === "long") return "상방";
  if (stance === "short_leaning" || stance === "short") return "하방";
  return "균형";
}

function FlowStateIcon({ state }: { state: string | undefined }) {
  if (state === "spot_led" || state === "spot_absorb") return <ShieldCheck aria-hidden="true" />;
  if (state === "futures_led" || state === "delever") return <TriangleAlert aria-hidden="true" />;
  return <Waves aria-hidden="true" />;
}

function FlowMetric({
  label,
  value,
  direction,
  unit,
  upLabel,
  downLabel,
  flatLabel
}: {
  label: string;
  value: number | null;
  direction: FlowDirection;
  unit: string;
  upLabel: string;
  downLabel: string;
  flatLabel: string;
}) {
  const Icon = direction === "up" ? ArrowUp : direction === "down" ? ArrowDown : Minus;
  const directionLabel = direction === "up" ? upLabel : direction === "down" ? downLabel : direction === "flat" ? flatLabel : "확인 중";
  return (
    <div className={`moneyFlowMetric ${direction}`}>
      <span>{label}</span>
      <strong><Icon aria-hidden="true" />{directionLabel}</strong>
      <em>{value === null ? "-" : `${signed(value)}${unit}`}</em>
    </div>
  );
}

function FlowPressure({ label, ratio, unavailableReason = null }: { label: string; ratio: number | null; unavailableReason?: string | null }) {
  const percent = ratio === null ? null : ratio * 100;
  const width = percent === null ? 0 : clamp(Math.abs(percent) * 2, 1.5, 50);
  const left = percent === null || percent >= 0 ? 50 : 50 - width;
  const direction = percent === null ? "unknown" : percent > 0.5 ? "buy" : percent < -0.5 ? "sell" : "flat";
  return (
    <div className={`flowPressureRow ${direction}`}>
      <div><span>{label}</span><strong>{unavailableReason || (percent === null ? "체결 표본 없음" : percent > 0.5 ? "매수 체결 우위" : percent < -0.5 ? "매도 체결 우위" : "매수·매도 균형")}</strong><em>{percent === null ? "-" : `${signed(percent)}%`}</em></div>
      <div className="flowPressureAxis"><i /><b style={{ left: `${left}%`, width: `${width}%` }} /></div>
      <footer><span>매도 우위</span><span>0</span><span>매수 우위</span></footer>
    </div>
  );
}

function FlowHistogram({
  label,
  values,
  method = "",
  unavailableReason = null
}: {
  label: string;
  values: Array<{ value: number }>;
  method?: string;
  unavailableReason?: string | null;
}) {
  const cumulative = values.map((item) => Number(item.value)).filter(Number.isFinite);
  const deltas = (cumulative.length === 1
    ? cumulative
    : cumulative.slice(1).map((value, index) => value - cumulative[index])).slice(-18);
  const max = Math.max(...deltas.map((value) => Math.abs(value)), 0);
  const methodLabel = method === "event_time_fills" ? "최근 실제 체결 24구간" : cumulative.length === 1 ? "현재 확정 구간" : "구간별 체결 델타";
  return (
    <div className="flowHistogram">
      <header><span>{label}</span><em>{methodLabel}</em></header>
      {unavailableReason ? (
        <div className="flowHistogramEmpty unavailable">{unavailableReason}</div>
      ) : deltas.length >= 1 && max > 0 ? (
        <div className="flowHistogramPlot">
          <i />
          {deltas.map((value, index) => {
            const height = Math.max(4, (Math.abs(value) / max) * 46);
            return (
              <b
                className={value >= 0 ? "buy" : "sell"}
                key={`${index}-${value}`}
                style={{ height: `${height}%`, transform: `translateY(${value >= 0 ? "-50%" : "50%"})` }}
              />
            );
          })}
        </div>
      ) : cumulative.length ? (
        <div className="flowHistogramEmpty">현재 구간 매수·매도 균형</div>
      ) : (
        <div className="flowHistogramEmpty">체결 표본 없음</div>
      )}
    </div>
  );
}

function moneyFlowPresentation(flow: DerivativesContext["signals"]["money_flow"] | undefined) {
  if (flow?.coverage?.spot_available !== true && flow?.coverage?.futures_available === true) return {
    headline: "선물 체결만 제공",
    driver: "현물 마켓 미지원 · 선물 CVD 관측 중",
    summary: flow.reason,
    readout: "SOXL 현물 CVD는 만들 수 없습니다. 선물 체결 우위만 관측하고 현물·선물 비교 판정은 보류합니다."
  };
  if (!flow?.available) return {
    headline: "판정 준비 중",
    driver: "현물·선물 체결 데이터 대기",
    summary: flow?.reason || "확정봉 기준 현물과 선물 체결 시계열을 수집하고 있습니다.",
    readout: "데이터가 채워지기 전에는 평평한 선을 신호로 해석하지 않습니다."
  };
  if (flow.provisional) return {
    headline: "표본 축적 중",
    driver: "아직 주도 주체를 확정할 수 없음",
    summary: `현물·선물 값은 관측됐지만 최근 30일 비교 표본이 부족합니다. ${flow.sample_size}/${flow.required_samples ?? 10}개`,
    readout: "현재 수치는 참고만 하고 확정봉과 비교 표본이 채워진 뒤 주도권을 판정합니다."
  };
  if (flow.state === "spot_led") return {
    headline: "현물 주도 상승",
    driver: "현물 매수 체결이 가격 상승을 지지",
    summary: "가격 상승과 현물 매수 우위가 함께 나타났습니다. 선물 레버리지 단독 상승보다 수급 기반이 단단한 상태입니다.",
    readout: "현물 CVD가 양수를 유지하는지 확인합니다. 현물이 꺾이고 선물만 강해지면 상승의 질이 약해집니다."
  };
  if (flow.state === "futures_led") return {
    headline: "선물 단독 견인",
    driver: "선물 매수가 가격을 끌지만 현물 확인 없음",
    summary: "가격과 선물 매수·OI는 증가했지만 현물 매수 체결이 받치지 않습니다. 레버리지성 상승이라 되돌림 위험이 큽니다.",
    readout: "다음 확정봉에서 현물 CVD가 따라붙는지 봅니다. 계속 선물만 양수면 추격보다 가짜 반등 가능성을 우선 경계합니다."
  };
  if (flow.state === "spot_absorb") return {
    headline: "현물 흡수 매수",
    driver: "가격 약세 속 현물 매수 체결 유입",
    summary: "가격은 약하거나 하락 중이지만 현물 매수 우위가 나타납니다. 매도 물량을 현물이 받아내는 초기 축적 가능성입니다.",
    readout: "가격 반전과 함께 현물 CVD 양수가 이어지는지 확인합니다. 가격 확인 전에는 바닥 확정이 아니라 흡수 관찰 단계입니다."
  };
  if (flow.state === "delever") return {
    headline: "레버리지 청산",
    driver: "가격 하락과 OI 감소가 동반",
    summary: "가격과 미결제약정이 같이 줄어 신규 숏 유입보다 기존 포지션 청산 성격이 강합니다.",
    readout: "OI 감소가 멈추고 현물 매수 우위가 생기는지 확인합니다. 그 전까지는 청산 압력이 진행 중인 상태입니다."
  };
  const spot = finiteNumber(flow.spot_cvd_delta_ratio);
  const futures = finiteNumber(flow.futures_cvd_delta_ratio);
  const conflict = spot !== null && futures !== null && Math.sign(spot) !== 0 && Math.sign(futures) !== 0 && Math.sign(spot) !== Math.sign(futures);
  return {
    headline: "주도권 혼조",
    driver: conflict ? "현물과 선물 체결 방향이 충돌" : "한쪽의 뚜렷한 수급 우위 없음",
    summary: conflict
      ? "현물과 선물이 반대 방향으로 체결돼 가격 움직임의 주체가 일치하지 않습니다."
      : "현물·선물·가격·OI 조합이 한 방향을 확인하지 못해 판정을 보류합니다.",
    readout: "다음 확정봉에서 현물과 선물이 같은 방향으로 정렬되는지 확인합니다. 현재는 방향 신호가 아니라 관망 신호입니다."
  };
}

function flowDirection(explicit: "up" | "down" | "flat" | undefined, value: number | null, flatThreshold: number): FlowDirection {
  if (explicit) return explicit;
  if (value === null) return "unknown";
  if (Math.abs(value) < flatThreshold) return "flat";
  return value > 0 ? "up" : "down";
}

function finiteNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(Math.abs(value) >= 10 ? 1 : 2)}`;
}

function formatCompactNumber(value: unknown): string {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return new Intl.NumberFormat("ko-KR", { notation: "compact", maximumFractionDigits: 2 }).format(number);
}

function formatRatio(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : "-";
}


function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Number.isFinite(value) ? value : min));
}
