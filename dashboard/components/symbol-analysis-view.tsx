"use client";

import { ReactNode, useEffect, useState } from "react";
import { HealthScoreBreakdownView } from "@/components/score-breakdown";
import { PositionChart } from "@/components/position/PositionChart";
import { VolumeProfilePanel } from "@/components/position/VolumeProfilePanel";
import { VolumeXrayPanel } from "@/components/position/VolumeXrayPanel";
import {
  DEFAULT_LAYER_STATE,
  focusedTaLayer,
  isTaLayer,
  loadLayerState,
  saveLayerState,
  toggleLayer,
  type ChartLayerId,
  type ChartLayerState,
  type TaFocusLayer
} from "@/lib/chartLayers";
import { confidenceLabel, DEFAULT_DENSITY, eventDisplayLimit, loadDensity, type Density } from "@/lib/density";
import { formatPrice, signedPercent } from "@/lib/format";
import { plainifyTaText, splitWyckoffEvents, taPlainTooltip, taShortLabel } from "@/lib/labels/taGlossary";
import {
  atrRiskLabel,
  bollingerLabel,
  criticalLevelTypeLabel,
  genericMarketStateLabel,
  macdLabel,
  phaseHintLabel,
  resistanceStatusLabel,
  rsiLabel,
  supportStatusLabel,
  trendLabel,
  volumeStateLabel,
  yesNoLabel
} from "@/lib/labels/marketStateLabels";
import type {
  LivePositionPayload,
  PositionActionPlan,
  PositionChartAnalysis,
  PositionEvent,
  PositionState
} from "@/lib/api";

export type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";

export type EvidenceModuleId = "wyckoff" | "harmonic" | "volume" | "indicators" | "risk" | "history";

const MODULE_LAYER: Record<EvidenceModuleId, TaFocusLayer | null> = {
  wyckoff: "wyckoff",
  harmonic: "harmonic",
  volume: "volume_profile",
  indicators: "indicators",
  risk: null,
  history: null
};

function moduleForLayer(layer: TaFocusLayer): EvidenceModuleId | null {
  const entry = (Object.entries(MODULE_LAYER) as Array<[EvidenceModuleId, TaFocusLayer | null]>).find(([, value]) => value === layer);
  return entry ? entry[0] : null;
}

export type AnalysisWorkspace = {
  layers: ChartLayerState;
  highlightPrice: number | null;
  setHighlightPrice: (price: number | null) => void;
  openModule: EvidenceModuleId | null;
  handleToggleLayer: (id: ChartLayerId, additive: boolean) => void;
  handleModuleToggle: (id: EvidenceModuleId) => void;
  density: Density;
};

export function useAnalysisWorkspace(): AnalysisWorkspace {
  const [layers, setLayers] = useState<ChartLayerState>(DEFAULT_LAYER_STATE);
  const [highlightPrice, setHighlightPrice] = useState<number | null>(null);
  const [openModule, setOpenModule] = useState<EvidenceModuleId | null>(null);
  const [density, setDensity] = useState<Density>(DEFAULT_DENSITY);

  useEffect(() => {
    setLayers(loadLayerState());
    setDensity(loadDensity());
  }, []);

  useEffect(() => {
    saveLayerState(layers);
  }, [layers]);

  function handleToggleLayer(id: ChartLayerId, additive: boolean) {
    const next = toggleLayer(layers, id, additive);
    setLayers(next);
    if (isTaLayer(id) && next.ta.includes(id) && focusedTaLayer(next) === id) {
      const moduleId = moduleForLayer(id);
      if (moduleId) {
        setOpenModule(moduleId);
        window.setTimeout(() => {
          document.getElementById(`evidence-${moduleId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }, 0);
      }
    }
  }

  function handleModuleToggle(id: EvidenceModuleId) {
    const layer = MODULE_LAYER[id];
    if (openModule === id) {
      setOpenModule(null);
      if (layer) setLayers((current) => ({ ...current, ta: current.ta.filter((item) => item !== layer) }));
      return;
    }
    setOpenModule(id);
    if (layer) setLayers((current) => ({ ...current, ta: [layer] }));
  }

  return { layers, highlightPrice, setHighlightPrice, openModule, handleToggleLayer, handleModuleToggle, density };
}

/** 포지션 상세와 스카우트가 공유하는 분석 화면: 차트(레이어) + 사이드 패널 + 근거 아코디언. */
export function SymbolAnalysisView({
  chartAnalysis,
  chartLoading,
  chartError,
  onRetryChart,
  trendSummary,
  plan,
  payload,
  sidePanel,
  workspace,
  gridClassName = "positionDetailMain",
  historyExtras
}: {
  chartAnalysis: PositionChartAnalysis | null;
  chartLoading: boolean;
  chartError: string;
  onRetryChart: () => void;
  trendSummary: string;
  plan: PositionActionPlan | null;
  payload?: LivePositionPayload;
  sidePanel: ReactNode;
  workspace: AnalysisWorkspace;
  gridClassName?: string;
  historyExtras?: ReactNode;
}) {
  return (
    <>
      <section className={gridClassName}>
        <PositionChart
          analysis={chartAnalysis}
          loading={chartLoading}
          error={chartError}
          onRetry={onRetryChart}
          trendSummary={trendSummary}
          plan={plan}
          layers={workspace.layers}
          onToggleLayer={workspace.handleToggleLayer}
          highlightPrice={workspace.highlightPrice}
        />
        {sidePanel}
      </section>
      <EvidenceAccordion
        payload={payload}
        chartAnalysis={chartAnalysis}
        openModule={workspace.openModule}
        onModuleToggle={workspace.handleModuleToggle}
        density={workspace.density}
        historyExtras={historyExtras}
      />
    </>
  );
}

type WyckoffSummary = {
  accumulation_score: number;
  distribution_score: number;
  phase?: string | null;
  phase_hint?: string | null;
  mtf?: { alignment?: string | null } | null;
  spring_candidate?: boolean;
  sos_candidate?: boolean;
  lps_candidate?: boolean;
  utad_candidate?: boolean;
  sow_candidate?: boolean;
  lpsy_candidate?: boolean;
  structure_comment?: string;
};

function wyckoffSummaryFrom(payload: LivePositionPayload | undefined, chartAnalysis: PositionChartAnalysis | null): WyckoffSummary | null {
  if (payload) return payload.state.analysis.wyckoff as unknown as WyckoffSummary;
  const raw = chartAnalysis?.wyckoff as Record<string, unknown> | undefined;
  if (!raw) return null;
  return {
    accumulation_score: Number(raw.accumulation_score ?? 0),
    distribution_score: Number(raw.distribution_score ?? 0),
    phase: (raw.phase as string) ?? null,
    phase_hint: (raw.phase_hint as string) ?? null,
    mtf: (raw.mtf as { alignment?: string | null } | null) ?? null,
    spring_candidate: Boolean(raw.spring_candidate),
    sos_candidate: Boolean(raw.sos_confirmed ?? raw.sos_candidate),
    lps_candidate: Boolean(raw.lps_candidate),
    utad_candidate: Boolean(raw.utad_candidate),
    sow_candidate: Boolean(raw.sow_confirmed ?? raw.sow_candidate),
    lpsy_candidate: Boolean(raw.lpsy_candidate),
    structure_comment: (raw.structure_comment as string) ?? ""
  };
}

export function EvidenceAccordion({
  payload,
  chartAnalysis,
  openModule,
  onModuleToggle,
  density,
  historyExtras
}: {
  payload?: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  openModule: EvidenceModuleId | null;
  onModuleToggle: (id: EvidenceModuleId) => void;
  density: Density;
  historyExtras?: ReactNode;
}) {
  const wyckoff = wyckoffSummaryFrom(payload, chartAnalysis);
  const direction = payload?.position.direction ?? "long";
  const wyckoffEvents = splitWyckoffEvents(chartAnalysis?.wyckoff_markers ?? [], chartAnalysis?.wyckoff_markers_low_confidence);
  const modules: Array<{ id: EvidenceModuleId; title: string; badge: string; content: ReactNode }> = [];
  if (wyckoff) {
    modules.push({
      id: "wyckoff",
      title: "와이코프",
      badge: density === "simple"
        ? phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)
        : `${phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)} · 유효 이벤트 ${wyckoffEvents.events.length}개`,
      content: <WyckoffEvidence wyckoff={wyckoff} chartAnalysis={chartAnalysis} direction={direction} density={density} />
    });
  }
  modules.push({
    id: "harmonic",
    title: "하모닉",
    badge: chartAnalysis?.harmonic_patterns.length
      ? density === "simple"
        ? `패턴 ${chartAnalysis.harmonic_patterns.length}개`
        : `패턴 ${chartAnalysis.harmonic_patterns.length}개 · 최고 ${confidenceLabel(Math.max(...chartAnalysis.harmonic_patterns.map((item) => item.confidence)), density)}`
      : "판정 보류",
    content: <HarmonicEvidence chartAnalysis={chartAnalysis} density={density} />
  });
  modules.push({
    id: "volume",
    title: "볼륨",
    badge: chartAnalysis ? volumeStateLabel(chartAnalysis.volume_xray.volume_state) : "차트 데이터 대기",
    content: chartAnalysis ? (
      <div className="evidenceVolumeGrid">
        <VolumeProfilePanel analysis={chartAnalysis} />
        <VolumeXrayPanel analysis={chartAnalysis} />
      </div>
    ) : (
      <div className="terminalEmpty">차트 데이터가 준비되면 표시됩니다.</div>
    )
  });
  if (payload) {
    modules.push({
      id: "indicators",
      title: "지표",
      badge: trendLabel(payload.state.analysis.technical.trend),
      content: <TechnicalEvidence state={payload.state} />
    });
    modules.push({
      id: "risk",
      title: "리스크",
      badge: `리스크 ${payload.state.risk_score}/100`,
      content: <RiskEvidence payload={payload} />
    });
    modules.push({
      id: "history",
      title: "기록",
      badge: payload.recent_events.length ? `이벤트 ${payload.recent_events.length}건` : "이벤트 없음",
      content: <TimelineEvidence payload={payload} extras={historyExtras} />
    });
  }
  return (
    <section className="evidenceAccordion" aria-label="판단 근거">
      <div className="evidenceAccordionHeader">
        <strong>판단 근거</strong>
        <span>펼치면 차트가 해당 분석 레이어로 전환됩니다.</span>
      </div>
      {modules.map((module) => (
        <details className="evidenceSection" id={`evidence-${module.id}`} key={module.id} open={openModule === module.id}>
          <summary
            onClick={(event) => {
              event.preventDefault();
              onModuleToggle(module.id);
            }}
          >
            <strong>{module.title}</strong>
            <small>{module.badge}</small>
          </summary>
          <div className="evidenceBody">{openModule === module.id ? module.content : null}</div>
        </details>
      ))}
    </section>
  );
}

function WyckoffEvidence({
  wyckoff,
  chartAnalysis,
  direction,
  density
}: {
  wyckoff: WyckoffSummary;
  chartAnalysis: PositionChartAnalysis | null;
  direction: "long" | "short";
  density: Density;
}) {
  const mtf = wyckoff.mtf;
  const { events, lowConfidence } = splitWyckoffEvents(chartAnalysis?.wyckoff_markers ?? [], chartAnalysis?.wyckoff_markers_low_confidence);
  const visibleEvents = events.slice(-eventDisplayLimit(density));
  const phase = chartAnalysis?.wyckoff_phase?.phase ?? wyckoff.phase ?? "undetermined";
  const emptyMessage = phase === "trending"
    ? "추세 구간이라 레인지 기반 와이코프 판정을 보류 중입니다."
    : "레인지가 형성되지 않아 와이코프 판정을 보류 중입니다.";
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="매집 점수" value={wyckoff.accumulation_score} tone="info" />
      <PositionHeaderMetric label="분산 점수" value={wyckoff.distribution_score} tone="warning" />
      <PositionHeaderMetric label="국면" value={phaseHintLabel(wyckoff.phase ?? wyckoff.phase_hint)} />
      <PositionHeaderMetric label="상위 정합" value={phaseHintLabel(mtf?.alignment)} tone={mtf?.alignment === "conflicting" ? "negative" : mtf?.alignment === "aligned" ? "positive" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("Spring")} title={taPlainTooltip("Spring", direction)} value={yesNoLabel(Boolean(wyckoff.spring_candidate))} />
      <PositionHeaderMetric label={taShortLabel("SOS")} title={taPlainTooltip("SOS", direction)} value={yesNoLabel(Boolean(wyckoff.sos_candidate))} />
      <PositionHeaderMetric label={taShortLabel("LPS")} title={taPlainTooltip("LPS", direction)} value={yesNoLabel(Boolean(wyckoff.lps_candidate))} />
      <PositionHeaderMetric label={taShortLabel("UTAD")} title={taPlainTooltip("UTAD", direction)} value={yesNoLabel(Boolean(wyckoff.utad_candidate))} tone={wyckoff.utad_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("SOW")} title={taPlainTooltip("SOW", direction)} value={yesNoLabel(Boolean(wyckoff.sow_candidate))} tone={wyckoff.sow_candidate ? "warning" : "neutral"} />
      <PositionHeaderMetric label={taShortLabel("LPSY")} title={taPlainTooltip("LPSY", direction)} value={yesNoLabel(Boolean(wyckoff.lpsy_candidate))} tone={wyckoff.lpsy_candidate ? "warning" : "neutral"} />
      <p className="tabExplanation">{plainifyTaText(wyckoff.structure_comment)}</p>
      {visibleEvents.length ? (
        <div className="wyckoffEventList">
          {visibleEvents.map((marker) => (
            <span key={`${marker.type}-${marker.time}`} title={taPlainTooltip(marker.label, direction)}>
              {taShortLabel(marker.label)} · {confidenceLabel(marker.confidence, density)}
            </span>
          ))}
        </div>
      ) : (
        <p className="tabExplanation">{emptyMessage}</p>
      )}
      {lowConfidence.length ? (
        <details className="lowConfidenceEvents">
          <summary>저신뢰 이벤트 {lowConfidence.length}개 보기</summary>
          <div className="wyckoffEventList">
            {lowConfidence.map((marker) => (
              <span key={`low-${marker.type}-${marker.time}`} title={taPlainTooltip(marker.label, direction)}>
                {taShortLabel(marker.label)} · {confidenceLabel(marker.confidence, density)}
              </span>
            ))}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function HarmonicEvidence({ chartAnalysis, density }: { chartAnalysis: PositionChartAnalysis | null; density: Density }) {
  const patterns = chartAnalysis?.harmonic_patterns ?? [];
  if (!patterns.length) {
    return <div className="terminalEmpty">반전 패턴 조건을 충족한 구간이 없어 하모닉 판정을 보류 중입니다.</div>;
  }
  return (
    <div className="harmonicEvidenceList">
      {patterns.map((pattern) => (
        <div className="harmonicEvidenceItem" key={pattern.id}>
          <div>
            <strong title={taPlainTooltip(pattern.name ?? pattern.label)}>{taShortLabel(pattern.name ?? pattern.label)}</strong>
            <span>{pattern.direction === "bearish" ? "하락 반전 후보" : "상승 반전 후보"} · {pattern.status === "forming" ? "형성 중" : "완성"} · {confidenceLabel(pattern.confidence, density)}</span>
          </div>
          <em title={taPlainTooltip("PRZ")}>반전 후보 구간(PRZ) {formatPrice(pattern.prz.low)} ~ {formatPrice(pattern.prz.high)}</em>
          <p>{plainifyTaText(pattern.basis)}</p>
        </div>
      ))}
    </div>
  );
}

function TechnicalEvidence({ state }: { state: PositionState }) {
  const technical = state.analysis.technical;
  return (
    <div className="tabMetricLayout">
      <PositionHeaderMetric label="추세" value={trendLabel(technical.trend)} tone={technical.trend_alignment.includes("against") ? "negative" : "positive"} />
      <PositionHeaderMetric label="RSI" value={rsiLabel(technical.rsi_state)} />
      <PositionHeaderMetric label="MACD" value={macdLabel(technical.macd_state)} tone={technical.macd_state.includes("bearish") ? "negative" : "positive"} />
      <PositionHeaderMetric label="볼린저" value={bollingerLabel(technical.bollinger_state)} />
      <PositionHeaderMetric label="거래량" value={volumeStateLabel(technical.volume_state)} tone={technical.volume_state.includes("declining") ? "warning" : "positive"} />
      <PositionHeaderMetric
        label="지지"
        value={supportStatusLabel(technical.support_status)}
        tone={technical.support_status === "broken" || technical.support_status === "at_risk" ? "negative" : technical.support_status === "near" ? "warning" : "positive"}
      />
      <PositionHeaderMetric
        label="저항"
        value={resistanceStatusLabel(technical.resistance_status)}
        tone={technical.resistance_status === "broken" ? "negative" : technical.resistance_status === "testing" ? "warning" : "neutral"}
      />
    </div>
  );
}

function RiskEvidence({ payload }: { payload: LivePositionPayload }) {
  const { position, state } = payload;
  return (
    <div className="tabRiskGrid">
      <div className="tabRiskStack">
        <HealthScoreBreakdownView components={state.score_json.health_components} />
        <div className="tabMetricLayout compact">
          <PositionHeaderMetric label="리스크 점수" value={`${state.risk_score}/100`} tone={state.risk_score >= 70 ? "negative" : state.risk_score >= 55 ? "warning" : "neutral"} />
          <PositionHeaderMetric label="청산가 거리" value={formatDistance(state.liquidation_distance_pct)} tone={state.liquidation_distance_pct !== null && state.liquidation_distance_pct < 5 ? "negative" : state.liquidation_distance_pct !== null && state.liquidation_distance_pct < 10 ? "warning" : "neutral"} />
          <PositionHeaderMetric label="방향 논리" value={`${state.thesis_delta > 0 ? "+" : ""}${state.thesis_delta}`} tone={state.thesis_delta < -20 ? "negative" : state.thesis_delta < -10 ? "warning" : "neutral"} />
          <PositionHeaderMetric label="수익 반납" value={formatDistance(state.analysis.risk.profit_giveback_pct)} />
          <PositionHeaderMetric label="손절 기준" value={position.planned_stop_price === null ? "-" : formatPrice(position.planned_stop_price)} tone="warning" />
          <PositionHeaderMetric label="ATR 리스크" value={atrRiskLabel(state.analysis.risk.atr_risk)} />
          <PositionHeaderMetric label="심각도" value={state.severity_rank} tone={state.severity_rank >= 3 ? "negative" : state.severity_rank >= 2 ? "warning" : "neutral"} />
        </div>
      </div>
      <div className="tabLevelsList">
        <strong>주의할 가격</strong>
        {state.analysis.risk.critical_levels.length ? (
          state.analysis.risk.critical_levels.map((level) => (
            <div key={`${level.type}-${level.price}`}>
              <span>{criticalLevelTypeLabel(level.type)}</span>
              <em>{formatPrice(level.price)}</em>
              <p>{level.meaning}</p>
            </div>
          ))
        ) : (
          <p>중요 가격대 데이터가 아직 충분하지 않습니다.</p>
        )}
      </div>
    </div>
  );
}

function TimelineEvidence({ payload, extras }: { payload: LivePositionPayload; extras?: ReactNode }) {
  return (
    <div className="timelineTab">
      <div className="snapshotSummary">
        <PositionHeaderMetric label="마지막 스냅샷" value={new Date(payload.latest_snapshot.created_at).toLocaleString()} />
      </div>
      <EventList events={payload.recent_events} />
      {extras}
    </div>
  );
}

export function PositionHeaderMetric({
  label,
  value,
  tone = "neutral",
  title
}: {
  label: string;
  value: string | number;
  tone?: MetricTone;
  title?: string;
}) {
  return (
    <div className={`positionHeaderMetric tone-${tone}`} title={title}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EventList({ events }: { events: PositionEvent[] }) {
  if (!events.length) {
    return <div className="terminalEmpty">아직 포지션 이벤트가 없습니다.</div>;
  }
  return (
    <div className="eventTimeline">
      {events.map((event) => (
        <div className={`eventItem severity-${event.severity}`} key={event.id}>
          <div>
            <strong>{event.title}</strong>
            <span>{new Date(event.created_at).toLocaleString()} · {genericMarketStateLabel(event.event_type)}</span>
          </div>
          <p>{event.description}</p>
        </div>
      ))}
    </div>
  );
}

function formatDistance(value: number | null): string {
  return value === null ? "-" : signedPercent(value);
}
