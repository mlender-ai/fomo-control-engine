"use client";

import { ReactNode, useEffect, useState } from "react";
import { HealthScoreBreakdownView } from "@/components/score-breakdown";
import { PositionChart, type PositionChartOverlay } from "@/components/position/PositionChart";
import { VolumeProfilePanel } from "@/components/position/VolumeProfilePanel";
import { VolumeXrayPanel } from "@/components/position/VolumeXrayPanel";
import {
  activeFocusLayers,
  CHART_LAYER_DEFS,
  DEFAULT_LAYER_STATE,
  isTaLayer,
  loadLayerState,
  saveLayerState,
  setFocusedLayer,
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
  AnalystBriefing,
  JudgmentLedgerEntry,
  JudgmentScore,
  LivePositionDetail,
  LivePositionPayload,
  PositionActionPlan,
  PositionChartAnalysis,
  PositionEvent,
  PositionState
} from "@/lib/api";

export type MetricTone = "positive" | "negative" | "warning" | "neutral" | "info" | "agent";

export type EvidenceModuleId = "briefing" | "wyckoff" | "liquidity" | "harmonic" | "volume" | "indicators" | "risk" | "history";

const MODULE_LAYER: Record<EvidenceModuleId, TaFocusLayer | null> = {
  briefing: null,
  wyckoff: "wyckoff",
  liquidity: "liquidity",
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
  setLayers: (layers: ChartLayerState) => void;
  highlightPrice: number | null;
  setHighlightPrice: (price: number | null) => void;
  openModule: EvidenceModuleId | null;
  focusedLayer: ChartLayerId | null;
  handleToggleLayer: (id: ChartLayerId, additive: boolean) => void;
  handleModuleToggle: (id: EvidenceModuleId) => void;
  density: Density;
  focusEvidence: (layer: ChartLayerId, price?: number | null) => void;
};

export function useAnalysisWorkspace(): AnalysisWorkspace {
  const [layers, setLayers] = useState<ChartLayerState>(DEFAULT_LAYER_STATE);
  const [highlightPrice, setHighlightPrice] = useState<number | null>(null);
  const [openModule, setOpenModule] = useState<EvidenceModuleId | null>(null);
  const [focusedLayer, setFocusedLayerState] = useState<ChartLayerId | null>(null);
  const [density, setDensity] = useState<Density>(DEFAULT_DENSITY);

  useEffect(() => {
    const stored = loadLayerState();
    const params = new URLSearchParams(window.location.search);
    const focus = params.get("focus") as ChartLayerId | null;
    const known = CHART_LAYER_DEFS.some((item) => item.id === focus);
    setLayers(known && focus ? setFocusedLayer(stored, focus) : stored);
    setFocusedLayerState(known ? focus : activeFocusLayers(stored).at(-1) ?? null);
    const price = Number(params.get("price"));
    if (Number.isFinite(price) && price > 0) setHighlightPrice(price);
    setDensity(loadDensity());
  }, []);

  useEffect(() => {
    saveLayerState(layers);
  }, [layers]);

  function handleToggleLayer(id: ChartLayerId, additive: boolean) {
    const next = toggleLayer(layers, id, additive);
    setLayers(next);
    setFocusedLayerState(activeFocusLayers(next).includes(id) ? id : activeFocusLayers(next).at(-1) ?? null);
    if (isTaLayer(id)) {
      const moduleId = moduleForLayer(id);
      if (moduleId && next.ta.includes(id)) {
        setOpenModule(moduleId);
      } else if (moduleId && openModule === moduleId) {
        setOpenModule(null);
      }
    }
  }

  function handleModuleToggle(id: EvidenceModuleId) {
    const layer = MODULE_LAYER[id];
    if (openModule === id) {
      setOpenModule(null);
      if (layer) setLayers((current) => ({ ...current, ta: current.ta.filter((item) => item !== layer) }));
      if (layer) setFocusedLayerState((current) => current === layer ? null : current);
      return;
    }
    setOpenModule(id);
    if (layer) {
      setLayers((current) => toggleLayer(current, layer, false));
      setFocusedLayerState(layer);
    }
  }

  function focusEvidence(layer: ChartLayerId, price: number | null = null) {
    setLayers((current) => setFocusedLayer(current, layer));
    setFocusedLayerState(layer);
    setHighlightPrice(price);
    if (isTaLayer(layer)) setOpenModule(moduleForLayer(layer));
    const url = new URL(window.location.href);
    url.searchParams.set("mode", "pro");
    url.searchParams.set("focus", layer);
    if (price !== null && Number.isFinite(price)) url.searchParams.set("price", String(price));
    else url.searchParams.delete("price");
    window.history.replaceState(window.history.state, "", url);
  }

  return { layers, setLayers, highlightPrice, setHighlightPrice, openModule, focusedLayer, handleToggleLayer, handleModuleToggle, density, focusEvidence };
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
  analystBriefing,
  sidePanel,
  workspace,
  gridClassName = "positionDetailMain",
  historyExtras,
  intentZoneSelector
}: {
  chartAnalysis: PositionChartAnalysis | null;
  chartLoading: boolean;
  chartError: string;
  onRetryChart: () => void;
  trendSummary: string;
  plan: PositionActionPlan | null;
  payload?: LivePositionPayload;
  analystBriefing?: AnalystBriefing | null;
  sidePanel: ReactNode;
  workspace: AnalysisWorkspace;
  gridClassName?: string;
  historyExtras?: ReactNode;
  intentZoneSelector?: {
    enabled: boolean;
    draft: { lower: number | null; upper: number | null };
    onDraftChange: (lower: number, upper: number) => void;
    onComplete: (lower: number, upper: number) => void;
  };
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
          positionOverlay={chartOverlayFromPayload(payload)}
          density={workspace.density}
          intentZoneSelector={intentZoneSelector}
        />
        <aside className="evidenceRoomRail">
          {sidePanel}
          <EvidenceRoomPanel payload={payload} chartAnalysis={chartAnalysis} layers={workspace.layers} focusedLayer={workspace.focusedLayer} />
        </aside>
      </section>
      <EvidenceAccordion
        payload={payload}
        analystBriefing={analystBriefing ?? payload?.analyst_briefing ?? null}
        chartAnalysis={chartAnalysis}
        openModule={workspace.openModule}
        onModuleToggle={workspace.handleModuleToggle}
        density={workspace.density}
        historyExtras={historyExtras}
      />
    </>
  );
}

function EvidenceRoomPanel({
  payload,
  chartAnalysis,
  layers,
  focusedLayer
}: {
  payload?: LivePositionPayload;
  chartAnalysis: PositionChartAnalysis | null;
  layers: ChartLayerState;
  focusedLayer: ChartLayerId | null;
}) {
  const active = activeFocusLayers(layers);
  const focused = focusedLayer && active.includes(focusedLayer) ? focusedLayer : active.at(-1) ?? null;
  if (!focused) {
    return (
      <section className="evidenceRoomPanel" data-testid="evidence-room-panel">
        <div className="evidenceRoomHeading"><span>근거 검증실</span><small>레이어를 선택하세요</small></div>
        <p className="evidenceRoomEmpty">차트 상단에서 근거 하나를 선택하면 상세 수치와 검증 기록을 함께 표시합니다.</p>
      </section>
    );
  }
  const definition = CHART_LAYER_DEFS.find((item) => item.id === focused);
  const stats = chartAnalysis?.historical_backtest?.stats ?? [];
  const relevantStats = stats
    .filter((stat) => signatureMatchesLayer(stat.signature, focused))
    .sort((left, right) => right.sample_size - left.sample_size)
    .slice(0, 2);
  const detail = payload as (LivePositionDetail | undefined);
  const scoreByJudgment = new Map((detail?.judgment_scores ?? []).map((score) => [score.judgment_id, score]));
  const judgmentHistory = (detail?.judgments ?? [])
    .filter((judgment) => judgmentMatchesLayer(judgment, focused))
    .map((judgment) => ({ judgment, score: scoreByJudgment.get(judgment.judgment_id) }))
    .slice(0, 3);
  const eventHistory = (payload?.recent_events ?? [])
    .filter((event) => eventMatchesLayer(event, focused))
    .slice(0, 3);
  return (
    <section className="evidenceRoomPanel" data-testid="evidence-room-panel" data-focus-layer={focused}>
      <div className="evidenceRoomHeading">
        <span>근거 검증실</span>
        <small>{definition?.label ?? focused} 포커스</small>
      </div>
      <div className="evidenceRoomSection">
        <strong>현재 주장</strong>
        <p>{focusedEvidenceClaim(chartAnalysis, focused)}</p>
      </div>
      <div className="evidenceRoomSection">
        <strong>백테스트 성적</strong>
        {relevantStats.length ? relevantStats.map((stat) => (
          <div className="evidenceRoomStat" key={stat.signature_key}>
            <span>{stat.label || definition?.label}</span>
            <b>{stat.win_1r_pct === null ? "판정 대기" : `1R ${stat.win_1r_pct.toFixed(1)}%`}</b>
            <small>N={stat.sample_size}{stat.win_1r_ci ? ` · CI ${stat.win_1r_ci[0].toFixed(1)}~${stat.win_1r_ci[1].toFixed(1)}%` : ""}</small>
          </div>
        )) : <p className="evidenceRoomEmpty">이 레이어의 검증 표본을 축적 중입니다.</p>}
      </div>
      <div className="evidenceRoomSection">
        <strong>최근 판단 이력</strong>
        {judgmentHistory.length ? judgmentHistory.map(({ judgment, score }) => (
          <div className="evidenceRoomHistory" key={judgment.id}>
            <span>{judgmentHistoryLabel(judgment, score)}</span>
            <small>{score ? judgmentOutcomeLabel(score.outcome) : "채점 대기"} · {new Date(judgment.as_of).toLocaleDateString("ko-KR")}</small>
          </div>
        )) : eventHistory.length ? eventHistory.map((event) => (
          <div className="evidenceRoomHistory" key={event.id}>
            <span>{plainifyTaText(event.title)}</span>
            <small>이벤트 기록 · {new Date(event.created_at).toLocaleDateString("ko-KR")}</small>
          </div>
        )) : <p className="evidenceRoomEmpty">이 심볼의 채점 가능한 판단 이력이 아직 없습니다.</p>}
      </div>
    </section>
  );
}

function signatureMatchesLayer(signature: Record<string, unknown> | undefined, layer: ChartLayerId): boolean {
  const source = `${signature?.engine ?? ""} ${signature?.event_type ?? ""}`.toLowerCase();
  const aliases: Record<ChartLayerId, string[]> = {
    plan: ["plan"], levels: ["level"], liquidity: ["liquidity", "sweep"], volume_profile: ["volume", "poc"],
    wyckoff: ["wyckoff", "spring", "utad"], harmonic: ["harmonic", "prz"], flow: ["flow", "funding", "oi"], indicators: ["indicator", "technical"]
  };
  return aliases[layer].some((alias) => source.includes(alias));
}

function eventMatchesLayer(event: PositionEvent, layer: ChartLayerId): boolean {
  return signatureMatchesLayer({ engine: event.event_type, event_type: `${event.title} ${event.description}` }, layer);
}

function judgmentMatchesLayer(judgment: JudgmentLedgerEntry, layer: ChartLayerId): boolean {
  const claim = JSON.stringify(judgment.claim ?? {});
  return signatureMatchesLayer({ engine: judgment.source_type, event_type: `${judgment.type} ${claim}` }, layer);
}

function judgmentHistoryLabel(judgment: JudgmentLedgerEntry, score?: JudgmentScore): string {
  const claim = judgment.claim ?? {};
  const label = String(claim.label ?? claim.event_type ?? claim.condition ?? judgment.type).replaceAll("_", " ");
  const detail = score?.detail ? ` · ${score.detail}` : "";
  return plainifyTaText(`${label}${detail}`);
}

function judgmentOutcomeLabel(outcome: JudgmentScore["outcome"]): string {
  return ({ correct: "적중", wrong: "오판", whipsaw: "휩쏘", untested: "미검증" } as const)[outcome];
}

function focusedEvidenceClaim(analysis: PositionChartAnalysis | null, layer: ChartLayerId): string {
  if (!analysis) return "차트 데이터가 준비되면 현재 주장을 표시합니다.";
  if (layer === "levels") return `지지 ${analysis.price_levels.support.length}개 · 저항 ${analysis.price_levels.resistance.length}개를 구조 점수로 비교합니다.`;
  if (layer === "liquidity") return `미스윕 풀 ${analysis.liquidity?.pools.filter((pool) => !pool.swept).length ?? 0}개 · 확정 스윕 ${analysis.liquidity?.sweeps.filter((sweep) => sweep.confirmed).length ?? 0}개입니다.`;
  if (layer === "volume_profile") return `최다 거래 가격은 ${formatPrice(analysis.volume_profile.poc_price)}입니다.`;
  if (layer === "wyckoff") return `현재 국면은 ${phaseHintLabel(String(analysis.wyckoff_phase?.phase ?? analysis.wyckoff?.["phase"] ?? "undetermined"))}입니다.`;
  if (layer === "harmonic") return analysis.harmonic_patterns.length ? `하모닉 패턴 ${analysis.harmonic_patterns.length}개를 검증 중입니다.` : "확정 하모닉 패턴이 없습니다.";
  if (layer === "flow") return `거래량 상태는 ${volumeStateLabel(analysis.volume_xray.volume_state)}입니다.`;
  if (layer === "indicators") return "기술 지표의 방향 정합을 검증합니다.";
  return "액션 플랜의 가격과 조건을 검증합니다.";
}

export function chartOverlayFromPayload(payload: LivePositionPayload | undefined): PositionChartOverlay | null {
  if (!payload || payload.position.status !== "open") return null;
  return {
    direction: payload.position.direction,
    quantity: payload.position.quantity,
    leverage: payload.position.leverage,
    entryPrice: payload.position.entry_price,
    markPrice: payload.state.mark_price ?? payload.position.mark_price ?? payload.position.current_price,
    pnlPercent: payload.state.pnl_percent,
    pnlAmount: payload.state.pnl_amount ?? payload.position.unrealized_pl,
    openedAt: payload.position.opened_at
  };
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
  analystBriefing,
  chartAnalysis,
  openModule,
  onModuleToggle,
  density,
  historyExtras
}: {
  payload?: LivePositionPayload;
  analystBriefing?: AnalystBriefing | null;
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
  if (analystBriefing) {
    modules.push({
      id: "briefing",
      title: "브리핑",
      badge: analystBriefing.confluence.stance_label,
      content: <AnalystBriefingEvidence briefing={analystBriefing} />
    });
  }
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
    id: "liquidity",
    title: "유동성",
    badge: liquidityBadge(chartAnalysis, density),
    content: <LiquidityEvidence chartAnalysis={chartAnalysis} density={density} />
  });
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
        <span>펼치면 해당 분석 레이어를 차트에 함께 표시합니다.</span>
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

function AnalystBriefingEvidence({ briefing }: { briefing: AnalystBriefing }) {
  const confluence = briefing.confluence;
  const strongest = [...confluence.long_evidence, ...confluence.short_evidence]
    .sort((left, right) => (right.score ?? 0) - (left.score ?? 0))
    .slice(0, 3);
  const counter = confluence.counter_evidence.slice(0, 2);
  return (
    <div className="analystBriefingEvidence">
      <div className="tabMetricLayout compact">
        <PositionHeaderMetric label="스탠스" value={confluence.stance_label} tone={confluence.stance === "conflicted" ? "warning" : confluence.stance === "insufficient" ? "neutral" : "info"} />
        <PositionHeaderMetric label="종합" value={`${confluence.composite_score}/100`} tone="info" />
        <PositionHeaderMetric label="롱/숏" value={`${confluence.long_score} / ${confluence.short_score}`} />
        <PositionHeaderMetric label="증거" value={`${confluence.evidence_count}개`} />
      </div>

      <div className="briefingColumns">
        <div className="tabLevelsList">
          <strong>근거</strong>
          {strongest.length ? strongest.map((item, index) => (
            <div key={`evidence-${item.engine}-${index}`}>
              <span>{plainifyTaText(item.claim)}</span>
              <em>{item.direction === "long" ? "상방 근거" : item.direction === "short" ? "하방 근거" : "중립"} · {item.score}</em>
              <p>{item.engine} · 신뢰도 {confidenceLabel(item.confidence, "detailed")}</p>
            </div>
          )) : <p>유효 근거가 3개 미만이라 브리핑을 보류합니다.</p>}
        </div>
        <div className="tabLevelsList">
          <strong>반대 근거</strong>
          {counter.length ? counter.map((item, index) => (
            <div key={`counter-${item.engine}-${index}`}>
              <span>{plainifyTaText(item.claim)}</span>
              <em>{item.direction === "long" ? "상방 근거" : item.direction === "short" ? "하방 근거" : "중립"} · {item.score}</em>
              <p>{item.engine} · 신뢰도 {confidenceLabel(item.confidence, "detailed")}</p>
            </div>
          )) : <p>반대 근거가 없으면 방향 브리핑을 보류합니다.</p>}
        </div>
      </div>

      <div className="briefingScenarioList">
        <strong>조건부 시나리오</strong>
        {briefing.scenario.map((line, index) => <span key={`${index}-${line}`}>{plainifyTaText(line)}</span>)}
      </div>
      <p className="tabExplanation">
        {briefing.hit_rates.length ? briefing.hit_rates.join(" · ") : "근거별 실측 적중률은 표본이 충분할 때만 표시합니다."}
      </p>
    </div>
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
      {chartAnalysis?.wyckoff?.["liquidity_crosscheck"] ? (
        <div className="wyckoffEventList">
          {liquidityCrosscheckLabels(chartAnalysis.wyckoff["liquidity_crosscheck"]).map((label) => (
            <span key={label}>{label}</span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LiquidityEvidence({ chartAnalysis, density }: { chartAnalysis: PositionChartAnalysis | null; density: Density }) {
  const liquidity = chartAnalysis?.liquidity;
  if (!liquidity) {
    return <div className="terminalEmpty">유동성 구조 데이터가 준비되면 표시됩니다.</div>;
  }
  const pools = liquidity.pools
    .filter((pool) => !pool.swept)
    .sort((left, right) => (right.score - left.score) || (right.touch_count - left.touch_count))
    .slice(0, density === "simple" ? 2 : 8);
  const sweeps = [...liquidity.sweeps, ...liquidity.htf_range_sweeps]
    .filter((sweep) => sweep.confirmed && (density === "detailed" || sweep.grade === "Strong"))
    .slice(0, density === "simple" ? 4 : 8);
  const shift = liquidity.structure_shift;
  const range = liquidity.dealing_range;
  return (
    <div className="tabRiskGrid">
      <div className="tabLevelsList">
        <strong title={taPlainTooltip("LiquidityPool")}>{taShortLabel("LiquidityPool")}</strong>
        {pools.length ? pools.map((pool) => (
          <div key={pool.id}>
            <span>{liquidityPoolEvidenceLabel(pool)}</span>
            <em>{formatPrice(pool.price)}</em>
            <p>{pool.grade} · 터치 {pool.touch_count} · 점수 {pool.score}</p>
          </div>
        )) : <p>미스윕 유동성 풀이 충분하지 않습니다.</p>}
      </div>
      <div className="tabLevelsList">
        <strong title={taPlainTooltip("Sweep")}>{taShortLabel("Sweep")}</strong>
        {sweeps.length ? sweeps.map((sweep) => (
          <div key={sweep.id}>
            <span>{liquiditySweepEvidenceLabel(sweep)}</span>
            <em>{formatPrice(sweep.price)}</em>
            <p>{sweep.grade} · 신뢰도 {confidenceLabel(sweep.confidence, density)} · 거래량 {sweep.volume_ratio.toFixed(2)}배</p>
          </div>
        )) : <p>확정 Strong 스윕이 아직 없습니다.</p>}
      </div>
      <div className="tabMetricLayout compact">
        <PositionHeaderMetric
          label={shift.event === "CHoCH" ? taShortLabel("CHoCH") : taShortLabel("BOS")}
          title={taPlainTooltip(shift.event === "CHoCH" ? "CHoCH" : "BOS")}
          value={shift.event && typeof shift.level === "number" ? `${shift.label ?? shift.event} · ${formatPrice(shift.level)}` : "돌파 없음"}
          tone={shift.event === "CHoCH" ? "warning" : shift.event === "BOS" ? "info" : "neutral"}
        />
        <PositionHeaderMetric
          label={range?.zone?.includes("premium") ? taShortLabel("Premium") : range?.zone?.includes("discount") ? taShortLabel("Discount") : "균형 위치"}
          title={taPlainTooltip(range?.zone?.includes("premium") ? "Premium" : range?.zone?.includes("discount") ? "Discount" : "Range")}
          value={range ? `${range.label} · ${range.position_pct.toFixed(1)}%` : "범위 부족"}
        />
      </div>
    </div>
  );
}

function liquidityBadge(chartAnalysis: PositionChartAnalysis | null, density: Density): string {
  const liquidity = chartAnalysis?.liquidity;
  if (!liquidity) return "데이터 대기";
  const unswept = liquidity.pools.filter((pool) => !pool.swept).length;
  const strong = [...liquidity.sweeps, ...liquidity.htf_range_sweeps].filter((sweep) => sweep.confirmed && sweep.grade === "Strong").length;
  if (density === "simple") return strong ? `Strong 스윕 ${strong}개` : `미스윕 풀 ${Math.min(unswept, 2)}개`;
  return `풀 ${unswept}개 · 확정 스윕 ${liquidity.sweeps.length + liquidity.htf_range_sweeps.length}개`;
}

function liquidityPoolEvidenceLabel(pool: PositionChartAnalysis["liquidity"]["pools"][number]): string {
  if (pool.kind === "eqh") return `상단 풀(EQH ${pool.touch_count}터치)`;
  if (pool.kind === "eql") return `하단 풀(EQL ${pool.touch_count}터치)`;
  if (pool.kind === "old_high") return `상단 풀(전고 ${pool.touch_count}터치)`;
  if (pool.kind === "old_low") return `하단 풀(전저 ${pool.touch_count}터치)`;
  return pool.label;
}

function liquiditySweepEvidenceLabel(sweep: PositionChartAnalysis["liquidity"]["sweeps"][number]): string {
  return sweep.side === "buy_side" ? "고점 스윕" : "저점 스윕";
}

function liquidityCrosscheckLabels(value: unknown): string[] {
  const payload = value as { confirmations?: Array<Record<string, unknown>> } | null;
  if (!payload?.confirmations?.length) return [];
  return payload.confirmations
    .map((item) => {
      const bonus = typeof item.liquidity_confirmation === "number" ? item.liquidity_confirmation : null;
      const grade = item.sweep_grade ? String(item.sweep_grade) : "스윕";
      return bonus ? `${grade} 스윕 확인 +${bonus}` : `${grade} 스윕 확인`;
    })
    .slice(0, 3);
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
